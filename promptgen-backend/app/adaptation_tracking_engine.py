"""
adaptation_tracking_engine.py — Engine 26 (Adaptation Tracking), scoped to
ONE domain: strength.

Full spec (KB engines["26"].spec_text) defines six domains (strength,
hypertrophy, power, endurance, body_composition, skill). This app only
has a real logged proxy for ONE of them:

  strength — max logged weight_kg per cycle, per exercise (same source
             plateau_engine.py already reads from workout_set_feedback).
             observed_change_percent = average, across all exercises with
             enough history, of (latest cycle's max weight vs the
             earliest cycle's max weight in the window).

  hypertrophy/power/endurance/body_composition/skill — NOT implemented.
  This app has no muscle-size proxy, no velocity/power data, no work-
  capacity metric, no body-composition log, and no technical-consistency
  score. Calling build_adaptation_profile() for any domain but "strength"
  raises NotImplementedError rather than silently returning a fabricated
  number for a domain this app cannot measure.

Window: 6 cycles (~12 weeks at this app's real 2-weeks/cycle length —
see periodization_engine.py's CYCLE_WEEKS) — approximates the spec's "2
mesocycles" using periodization_engine's own mesocycle_cycles=3 default.
This is a SINGLE window check, not a true multi-mesocycle trend-of-trends
(that would require storing this engine's own output across calls, which
nothing in this app currently persists) — documented limitation, not
silently overclaimed as the full AT001 rule.

expected_change_percent reuses load_prescription_engine's own goal-based
increment percentages, compounded across the window's cycle count — same
"don't invent a second number for the same concept" rule this build
follows throughout.

AT004/AT005 (recovery/adherence gate conclusions) are real: a low
recovery_capacity_score or adherence_score suspends the conclusion rather
than reporting a status built on unreliable conditions.

Never raises for missing DATA (returns confidence_score=0, status
"insufficient_data"); only raises NotImplementedError for an unsupported
domain, since that's a caller programming error, not a missing-data case.
"""

from __future__ import annotations

from app.db import supabase
from app.load_prescription_engine import GOAL_INCREMENT_PCT, DEFAULT_INCREMENT_PCT

ADAPTATION_WINDOW_CYCLES = 6  # ~2 mesocycles at periodization_engine's default mesocycle_cycles=3
SUPPORTED_DOMAINS = ("strength",)


def _resolve_expected_pct_per_cycle(goal_raw: str) -> float:
    g = (goal_raw or "").lower().strip()
    for key, pct in GOAL_INCREMENT_PCT.items():
        if key in g:
            return pct
    return DEFAULT_INCREMENT_PCT


def _strength_observed_change(member_id: str, up_to_cycle: int) -> tuple[float | None, int]:
    """
    Returns (observed_change_percent, exercises_counted). Averages each
    exercise's (latest - earliest) / earliest %, across exercises that
    have weight logged in at least 2 distinct cycles within the window.
    """
    lowest = max(1, up_to_cycle - ADAPTATION_WINDOW_CYCLES + 1)
    try:
        res = (
            supabase.table("workout_set_feedback")
            .select("exercise, cycle_number, weight_kg")
            .eq("member_id", member_id)
            .gte("cycle_number", lowest)
            .lte("cycle_number", up_to_cycle)
            .execute()
        )
        rows = res.data or []
    except Exception:
        return None, 0

    by_exercise: dict[str, dict[int, float]] = {}
    for r in rows:
        ex, cn, w = r.get("exercise"), r.get("cycle_number"), r.get("weight_kg")
        if not ex or cn is None or w is None:
            continue
        by_exercise.setdefault(ex, {})[cn] = max(w, by_exercise.get(ex, {}).get(cn, 0.0))

    pct_changes = []
    for cycles in by_exercise.values():
        if len(cycles) < 2:
            continue
        ordered = sorted(cycles.items())
        earliest_w, latest_w = ordered[0][1], ordered[-1][1]
        if earliest_w > 0:
            pct_changes.append(((latest_w - earliest_w) / earliest_w) * 100)

    if not pct_changes:
        return None, 0
    return round(sum(pct_changes) / len(pct_changes), 1), len(pct_changes)


def build_adaptation_profile(
    member_id: str | None,
    domain: str,
    goal_raw: str,
    cycle_number: int | None = None,
    recovery_capacity_profile: dict | None = None,
    adherence_profile: dict | None = None,
    plateau_confirmed: bool = False,
) -> dict:
    if domain not in SUPPORTED_DOMAINS:
        raise NotImplementedError(
            f"adaptation_domain={domain!r} has no real logged proxy in this app — only "
            f"{SUPPORTED_DOMAINS} are implemented."
        )

    base = {
        "adaptation_profile_id": f"ADAPT_{member_id or 'UNKNOWN'}",
        "athlete_id": member_id,
        "adaptation_domain": domain,
        "baseline_score": None,
        "current_score": None,
        "expected_change_percent": None,
        "observed_change_percent": None,
        "adaptation_status": "insufficient_data",
        "confidence_score": 0,
        "escalate_to_plateau": False,
        "note": None,
    }

    if not member_id or cycle_number is None or cycle_number - 1 < 1:
        base["note"] = "No completed cycle yet to assess adaptation."
        return base

    # AT005 — poor adherence suspends conclusions outright.
    adherence_score = (adherence_profile or {}).get("adherence_score")
    if adherence_score is not None and adherence_score < 60:
        base["note"] = f"Adherence is low ({adherence_score}) — suspending adaptation conclusions."
        return base

    # AT004 — inadequate recovery delays assessment.
    capacity_score = (recovery_capacity_profile or {}).get("capacity_score")
    if capacity_score is not None and capacity_score < 40:
        base["note"] = f"Recovery capacity is low ({capacity_score}) — delaying adaptation assessment."
        return base

    observed_pct, n_exercises = _strength_observed_change(member_id, cycle_number - 1)
    if observed_pct is None:
        base["note"] = "Not enough multi-cycle weight history yet to assess strength adaptation."
        return base

    cycles_in_window = min(ADAPTATION_WINDOW_CYCLES, cycle_number - 1)
    expected_pct = round(_resolve_expected_pct_per_cycle(goal_raw) * 100 * cycles_in_window, 1)

    if observed_pct >= expected_pct:
        status = "above_expected" if observed_pct > expected_pct * 1.1 else "on_track"
    else:
        status = "below_expected"

    escalate = status == "below_expected" and plateau_confirmed  # AT003

    # Confidence scales with how many exercises actually had a real trend to average.
    confidence = min(95, 40 + n_exercises * 12)

    return {
        "adaptation_profile_id": f"ADAPT_{member_id}",
        "athlete_id": member_id,
        "adaptation_domain": domain,
        "baseline_score": 100.0,
        "current_score": round(100.0 * (1 + observed_pct / 100), 1),
        "expected_change_percent": expected_pct,
        "observed_change_percent": observed_pct,
        "adaptation_status": status,
        "confidence_score": confidence,
        "escalate_to_plateau": escalate,
        "note": None,
    }
