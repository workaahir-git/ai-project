"""
plateau_engine.py — Engine 11 (Plateau), scoped to what real multi-cycle
data actually supports.

PL003/PL004 gate on recovery_capacity_score and readiness_score — Engines
10 and 9. Both now exist (readiness_engine.py, recovery_capacity_engine.py,
Phase 2). Callers that HAVE a same-session readiness/recovery-capacity
profile already computed (main.py does, at generation time) should pass
them in via `readiness_profile` / `recovery_capacity_profile`; this module
does not compute them itself, since readiness is a per-session, per-day
signal (needs day_index) while plateau is evaluated per-exercise — mixing
those lookups here would mean guessing which day_index applies. Callers
without a same-session profile in hand (e.g. a bare exercise_id check) get
recovery_capacity_score/readiness_score = None, same conservative fallback
as before Phase 2.

Everything else the spec asks for IS real here:

  Progress signal: max logged weight_kg per cycle for the exercise, read
  from workout_set_feedback (now cycle-scoped — see sql/add_cycle_tracking
  .sql). "No measurable progress" = weight did not increase cycle-over-cycle.
  consecutive_sessions = current streak of non-increasing cycles, counted
  back from the most recent cycle with data.

  PL001/PL002 thresholds (3 / 6 sessions) applied directly to that streak.

  Deterministic rule 1 (adherence before plateau) is enforced by actually
  calling adherence_engine.get_adherence_profile() first — a low score
  blocks plateau diagnosis outright, matching AD005.

  PL005 (pain) reuses progression_engine._contains_pain_language on the
  most recent cycle's notes and defers rather than declaring a plateau.

Never raises. Any missing data (member_id, exercise history <3 cycles,
Supabase error) returns plateau_status="none" with a note explaining why,
rather than guessing.
"""

from __future__ import annotations

from app.adherence_engine import get_adherence_profile
from app.db import supabase
from app.load_adjustment_engine import _contains_pain_language
from app.readiness_engine import get_readiness
from app.recovery_capacity_engine import build_recovery_capacity

MIN_HISTORY_CYCLES = 3
LOOKBACK_CYCLES = 6


def _default_result(
    exercise_id: str, movement_id: str | None, note: str,
    recovery_capacity_score: int | None = None, readiness_score: int | None = None,
) -> dict:
    return {
        "plateau_profile_id": f"PL_{exercise_id}",
        "exercise_id": exercise_id,
        "movement_id": movement_id,
        "plateau_status": "none",
        "consecutive_sessions": 0,
        "performance_delta_percent": None,
        "bodyweight_delta_percent": None,
        "recovery_capacity_score": recovery_capacity_score,
        "readiness_score": readiness_score,
        "intervention": {
            "load": "maintain",
            "volume": "maintain",
            "frequency": "maintain",
            "deload": False,
            "exercise_variation": False,
        },
        "note": note,
    }


def _weight_history(member_id: str, exercise_name: str, up_to_cycle: int) -> list[tuple[int, float]]:
    """
    Returns [(cycle_number, max_weight_kg), ...] ascending by cycle, for the
    LOOKBACK_CYCLES cycles ending at up_to_cycle. Only cycles with at least
    one logged weight are included — missing cycles are gaps, not zeros.
    """
    lowest = max(1, up_to_cycle - LOOKBACK_CYCLES + 1)
    try:
        res = (
            supabase.table("workout_set_feedback")
            .select("cycle_number, weight_kg")
            .eq("member_id", member_id)
            .eq("exercise", exercise_name)
            .gte("cycle_number", lowest)
            .lte("cycle_number", up_to_cycle)
            .execute()
        )
        rows = res.data or []
    except Exception:
        return []

    by_cycle: dict[int, float] = {}
    for r in rows:
        cn = r.get("cycle_number")
        w = r.get("weight_kg")
        if cn is None or w is None:
            continue
        by_cycle[cn] = max(w, by_cycle.get(cn, 0.0))

    return sorted(by_cycle.items())


def _latest_notes(member_id: str, exercise_name: str, cycle_number: int) -> str | None:
    try:
        res = (
            supabase.table("workout_exercise_feedback")
            .select("notes")
            .eq("member_id", member_id)
            .eq("exercise", exercise_name)
            .eq("cycle_number", cycle_number)
            .execute()
        )
        if res.data:
            return res.data[0].get("notes")
    except Exception:
        pass
    return None


def detect_plateau(
    member_id: str | None,
    exercise_id: str,
    exercise_name: str,
    movement_id: str | None = None,
    cycle_number: int | None = None,
    readiness_profile: dict | None = None,
    recovery_capacity_profile: dict | None = None,
) -> dict:
    """
    cycle_number is the cycle currently being generated/queried. History is
    read up to cycle_number - 1 (the last completed cycle), same "previous
    cycle" convention as progression_engine.py. cycle_number=None or <2
    returns "none" immediately — no completed cycle exists yet to judge.

    readiness_profile / recovery_capacity_profile are optional, already-
    computed Engine 9 / Engine 10 outputs for this session (caller's
    responsibility — see module docstring for why this module doesn't
    fetch them itself). When given, PL003/PL004 are applied for real.
    """
    if not member_id:
        return _default_result(exercise_id, movement_id, "No member_id provided.")

    readiness_score = (readiness_profile or {}).get("readiness_score")
    recovery_capacity_score = (recovery_capacity_profile or {}).get("capacity_score")

    if cycle_number is None or cycle_number - 1 < 1:
        return _default_result(
            exercise_id, movement_id, "No completed cycle yet to assess.",
            recovery_capacity_score, readiness_score,
        )

    up_to = cycle_number - 1

    # Deterministic rule 1 / AD005 — adherence must be verified first.
    adherence = get_adherence_profile(member_id, cycle_number=cycle_number)
    if adherence.get("adherence_score") is not None and adherence["adherence_score"] < 60:
        return _default_result(
            exercise_id, movement_id,
            "Adherence is low this cycle — verify consistency before diagnosing a plateau.",
            recovery_capacity_score, readiness_score,
        )

    # PL003 — recovery limitation, not a real plateau.
    if recovery_capacity_score is not None and recovery_capacity_score < 40:
        return _default_result(
            exercise_id, movement_id,
            "Recovery capacity is low right now — this looks like a recovery limitation, not a plateau.",
            recovery_capacity_score, readiness_score,
        )

    # PL004 — low readiness delays diagnosis rather than confirming one.
    if readiness_score is not None and readiness_score < 60:
        return _default_result(
            exercise_id, movement_id,
            "Readiness is low this session — delaying plateau diagnosis until readiness recovers.",
            recovery_capacity_score, readiness_score,
        )

    # PL005 — pain present defers to injury handling, not a plateau call.
    notes = _latest_notes(member_id, exercise_name, up_to)
    if _contains_pain_language(notes):
        return _default_result(
            exercise_id, movement_id,
            "Pain/discomfort flagged on this exercise — addressing that comes before a plateau call.",
            recovery_capacity_score, readiness_score,
        )

    history = _weight_history(member_id, exercise_name, up_to)
    if len(history) < MIN_HISTORY_CYCLES:
        return _default_result(
            exercise_id, movement_id,
            f"Only {len(history)} cycle(s) of logged weight for this exercise — need at least "
            f"{MIN_HISTORY_CYCLES} to assess a trend.",
            recovery_capacity_score, readiness_score,
        )

    # Walk backward from the most recent cycle counting the non-increasing streak.
    streak = 1
    for i in range(len(history) - 1, 0, -1):
        _, w_now = history[i]
        _, w_prev = history[i - 1]
        if w_now <= w_prev:
            streak += 1
        else:
            break

    earliest_cycle, earliest_w = history[max(0, len(history) - streak)]
    latest_cycle, latest_w = history[-1]
    delta_pct = round(((latest_w - earliest_w) / earliest_w) * 100, 1) if earliest_w else 0.0

    if streak >= 6:
        status = "confirmed"
        intervention = {
            "load": "decrease", "volume": "decrease", "frequency": "maintain",
            "deload": True, "exercise_variation": True,
        }
        note = (
            f"No weight increase for {streak} straight cycles on this exercise — confirmed "
            "plateau. Recommend a deload and consider a variation swap."
        )
    elif streak >= 3:
        status = "suspected"
        intervention = {
            "load": "maintain", "volume": "increase", "frequency": "maintain",
            "deload": False, "exercise_variation": False,
        }
        note = (
            f"No weight increase for {streak} cycles on this exercise — suspected plateau. "
            "Try adding volume before changing load or exercise."
        )
    else:
        status = "none"
        intervention = {
            "load": "maintain", "volume": "maintain", "frequency": "maintain",
            "deload": False, "exercise_variation": False,
        }
        note = None

    return {
        "plateau_profile_id": f"PL_{exercise_id}",
        "exercise_id": exercise_id,
        "movement_id": movement_id,
        "plateau_status": status,
        "consecutive_sessions": streak if status != "none" else 0,
        "performance_delta_percent": delta_pct,
        "bodyweight_delta_percent": None,  # no bodyweight log exists in this app
        "recovery_capacity_score": recovery_capacity_score,
        "readiness_score": readiness_score,
        "intervention": intervention,
        "note": note,
    }
