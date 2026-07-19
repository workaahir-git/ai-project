"""
fatigue_management_engine.py — Engine 23 (Fatigue Management), scoped to
what real data supports.

Full spec (KB engines["23"].spec_text) wants local_fatigue, systemic_fatigue,
and neural_fatigue as three SEPARATE signals. This app has one real proxy —
average logged difficulty rating, 1-5 scaled to 0-100 (same proxy
recovery_capacity_engine.py already uses for its own fatigue_score) — so:

  systemic_fatigue — reused directly from recovery_capacity_profile's
                      recovery_inputs.fatigue_score (NOT recomputed a
                      second way, so this engine and Engine 10 never
                      disagree on what "fatigue" means here).
  local_fatigue     — the SAME proxy, but computed per muscle group (using
                      this cycle's own workout days to map exercise name ->
                      muscle, then averaging recent difficulty for
                      exercises training that muscle) and taking the
                      single most-fatigued muscle's score. This is real
                      granularity systemic_fatigue doesn't have, not a
                      second invented number.
  neural_fatigue    — NOT implemented. No bar-speed, RPE, or technique-
                      breakdown signal exists anywhere in this app to
                      measure central/neural fatigue distinctly from
                      muscular fatigue. Always None.
  accumulated_fatigue — average of whichever of {local_fatigue,
                      systemic_fatigue} are available (not a 3-way blend
                      including a None neural_fatigue).

FM004 (high neural fatigue -> reduce intensity before volume) is NOT
applied — no neural_fatigue signal exists. FM001/FM002/FM003/FM005 apply
directly to accumulated_fatigue / systemic_fatigue.

Never raises. No logged difficulty at all returns fatigue_zone="low" with
a note, rather than assuming fatigue is fine with zero evidence either way
— the safe default here happens to double as "no data yet."
"""

from __future__ import annotations

from app.db import supabase

FATIGUE_LOOKBACK_CYCLES = 3


def _muscle_difficulty_averages(member_id: str, days: list, up_to_cycle: int) -> dict:
    """{muscle: avg_difficulty_0_100} using THIS cycle's exercise->muscle
    map, applied to recent logged difficulty for those exercise names."""
    exercise_to_muscle = {}
    for day in days:
        for ex in day.get("exercises", []) or []:
            name, muscle = ex.get("name"), ex.get("muscle")
            if name and muscle:
                exercise_to_muscle[name] = muscle

    if not exercise_to_muscle:
        return {}

    lowest = max(1, up_to_cycle - FATIGUE_LOOKBACK_CYCLES + 1)
    try:
        res = (
            supabase.table("workout_exercise_feedback")
            .select("exercise, difficulty, cycle_number")
            .eq("member_id", member_id)
            .gte("cycle_number", lowest)
            .lte("cycle_number", up_to_cycle)
            .execute()
        )
        rows = res.data or []
    except Exception:
        return {}

    by_muscle: dict[str, list[int]] = {}
    for r in rows:
        name, diff = r.get("exercise"), r.get("difficulty")
        muscle = exercise_to_muscle.get(name)
        if not muscle or diff is None:
            continue
        by_muscle.setdefault(muscle, []).append(diff)

    return {
        muscle: round((sum(vals) / len(vals)) * 20)
        for muscle, vals in by_muscle.items()
    }


def build_fatigue_profile(
    member_id: str | None,
    days: list | None = None,
    cycle_number: int | None = None,
    recovery_capacity_profile: dict | None = None,
) -> dict:
    """
    days = data["workout"]["days"] for the cycle being generated/queried
    (used only for its exercise->muscle map). cycle_number is the cycle
    currently being generated; muscle-difficulty history reads up to
    cycle_number - 1, same convention as every other engine here.
    """
    systemic_fatigue = (recovery_capacity_profile or {}).get("recovery_inputs", {}).get("fatigue_score")

    local_fatigue = None
    most_fatigued_muscle = None
    if member_id and days and cycle_number is not None and cycle_number - 1 >= 1:
        muscle_scores = _muscle_difficulty_averages(member_id, days, cycle_number - 1)
        if muscle_scores:
            most_fatigued_muscle = max(muscle_scores, key=muscle_scores.get)
            local_fatigue = muscle_scores[most_fatigued_muscle]

    components = [s for s in (local_fatigue, systemic_fatigue) if s is not None]
    accumulated_fatigue = round(sum(components) / len(components)) if components else None

    if accumulated_fatigue is None:
        return {
            "fatigue_profile_id": f"FAT_{member_id or 'UNKNOWN'}",
            "athlete_id": member_id,
            "local_fatigue": None,
            "systemic_fatigue": None,
            "neural_fatigue": None,
            "accumulated_fatigue": None,
            "fatigue_zone": "low",
            "most_fatigued_muscle": None,
            "intervention": {"volume": "maintain", "intensity": "maintain", "recovery_days": 0},
            "note": "No logged difficulty data yet — defaulting to low/no intervention.",
        }

    if accumulated_fatigue >= 75:
        zone, intervention = "critical", {"volume": "decrease", "intensity": "decrease", "recovery_days": 3}
    elif accumulated_fatigue >= 50:
        zone, intervention = "high", {"volume": "decrease", "intensity": "maintain", "recovery_days": 2}
    elif accumulated_fatigue >= 25:
        zone, intervention = "moderate", {"volume": "maintain", "intensity": "maintain", "recovery_days": 1}
    else:
        zone, intervention = "low", {"volume": "increase", "intensity": "maintain", "recovery_days": 0}

    # FM005 — high systemic fatigue schedules additional recovery regardless of zone math above.
    if systemic_fatigue is not None and systemic_fatigue >= 50:
        intervention["recovery_days"] = max(intervention["recovery_days"], 2)

    return {
        "fatigue_profile_id": f"FAT_{member_id}",
        "athlete_id": member_id,
        "local_fatigue": local_fatigue,
        "systemic_fatigue": systemic_fatigue,
        "neural_fatigue": None,
        "accumulated_fatigue": accumulated_fatigue,
        "fatigue_zone": zone,
        "most_fatigued_muscle": most_fatigued_muscle,
        "intervention": intervention,
        "note": None,
    }
