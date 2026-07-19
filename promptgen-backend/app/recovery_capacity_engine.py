"""
recovery_capacity_engine.py — Engine 10 (Recovery Capacity), scoped to what
real data supports now that Engine 9 (Readiness) exists.

Full spec (KB engines["10"].spec_text) wants sleep_score, nutrition_score,
stress_score, recovery_score, and fatigue_score as separate recovery_inputs,
plus MEV/MAV/MRV. This app has:

  readiness_score  — real, from readiness_engine.py (Engine 9), when a
                     check-in exists for the session being evaluated.
  fatigue_score    — a real proxy: average difficulty rating (1-5, scaled
                     to 0-100) across the member's logged exercises in the
                     last few cycles. High recent difficulty is treated as
                     high accumulated fatigue. This is a proxy, not a true
                     physiological fatigue measure — documented as such.
  sleep_score / nutrition_score / stress_score / recovery_score — NOT
                     collected anywhere in this app. Always None.

  training_age     — real, from profile["experience"] (beginner/
                     intermediate/advanced), same field volume_allocation_
                     engine.py already scales landmarks by.
  age_group        — derived from profile["age"] (real field): <18 youth,
                     >=50 masters, else adult. Simple threshold, not a
                     clinical definition — documented as a practical
                     heuristic, not a sourced figure.

capacity score = weighted blend of whichever of {readiness_score,
100 - fatigue_score} are actually available. If NEITHER is available,
capacity_score is None and the recommendation defaults to "maintain"
across the board (RC003 territory) rather than guessing a number.

MEV/MAV/MRV reuse volume_allocation_engine._scaled_landmarks (already
built, sourced RP landmarks scaled by experience) averaged across this
app's muscle buckets into one aggregate figure, per spec's single-number
schema (not per-muscle) — then further scaled per RC001-005 once a real
capacity_score exists. Modifiers (illness/injury) come straight from
Engine 9's pain_flag/illness_flag when a same-session readiness profile is
passed in; caloric_deficit/surplus are NOT tracked (no macro-adherence log
exists), always False.

Never raises. Missing everything returns a conservative "maintain,
maintain, no deload" recommendation with capacity_score=None.
"""

from __future__ import annotations

from app.db import supabase
from app.volume_allocation_engine import _scaled_landmarks, LANDMARKS_INTERMEDIATE

FATIGUE_LOOKBACK_CYCLES = 3


def _age_group(age_raw) -> str:
    try:
        age = int(age_raw)
    except (TypeError, ValueError):
        return "adult"
    if age < 18:
        return "youth"
    if age >= 50:
        return "masters"
    return "adult"


def _fatigue_score(member_id: str, up_to_cycle: int) -> int | None:
    """Average logged difficulty (1-5) across recent cycles, scaled to 0-100."""
    lowest = max(1, up_to_cycle - FATIGUE_LOOKBACK_CYCLES + 1)
    try:
        res = (
            supabase.table("workout_exercise_feedback")
            .select("difficulty, cycle_number")
            .eq("member_id", member_id)
            .gte("cycle_number", lowest)
            .lte("cycle_number", up_to_cycle)
            .execute()
        )
        rows = res.data or []
    except Exception:
        return None

    ratings = [r["difficulty"] for r in rows if r.get("difficulty") is not None]
    if not ratings:
        return None
    return round((sum(ratings) / len(ratings)) * 20)


def _aggregate_landmarks(exp_key: str) -> dict:
    """Average MEV/MAV/MRV across all muscle buckets into one figure."""
    keys = ("mev", "mav_low", "mav_high", "mrv")
    totals = {k: 0.0 for k in keys}
    count = 0
    for muscle in LANDMARKS_INTERMEDIATE:
        scaled = _scaled_landmarks(muscle, exp_key)
        if not scaled:
            continue
        for k in keys:
            totals[k] += scaled[k]
        count += 1
    if not count:
        return {"MEV": 10, "MAV_low": 14, "MAV_high": 20, "MRV": 24}  # KB sample-profile fallback
    return {
        "MEV": round(totals["mev"] / count),
        "MAV_low": round(totals["mav_low"] / count),
        "MAV_high": round(totals["mav_high"] / count),
        "MRV": round(totals["mrv"] / count),
    }


def build_recovery_capacity(
    member_id: str | None,
    profile: dict | None = None,
    cycle_number: int | None = None,
    readiness_profile: dict | None = None,
) -> dict:
    """
    profile supplies experience/age (real intake fields, same as
    goal_optimization_engine's generation-time convention). readiness_profile
    is an already-computed Engine 9 output for this session (pass it in
    rather than re-fetching, since the caller usually has one already).
    cycle_number is the cycle currently being generated/queried — fatigue
    proxy reads up to cycle_number - 1 (last completed cycles).
    """
    exp_key = str((profile or {}).get("experience", "intermediate")).lower()
    if exp_key not in ("beginner", "intermediate", "advanced"):
        exp_key = "intermediate"
    age_group = _age_group((profile or {}).get("age"))

    readiness_score = (readiness_profile or {}).get("readiness_score")
    illness = bool((readiness_profile or {}).get("illness_flag", False))
    injury = bool((readiness_profile or {}).get("pain_flag", False))

    fatigue_score = None
    if member_id and cycle_number is not None and cycle_number - 1 >= 1:
        fatigue_score = _fatigue_score(member_id, cycle_number - 1)

    components = []
    if readiness_score is not None:
        components.append(readiness_score)
    if fatigue_score is not None:
        components.append(100 - fatigue_score)

    capacity_score = round(sum(components) / len(components)) if components else None

    landmarks = _aggregate_landmarks(exp_key)

    recommendation = {"volume_adjustment": "maintain", "frequency_adjustment": "maintain", "deload_required": False}
    if capacity_score is not None:
        if capacity_score >= 90:       # RC001
            recommendation["volume_adjustment"] = "increase"
            recommendation["frequency_adjustment"] = "increase"
        elif capacity_score >= 75:     # RC002
            recommendation["volume_adjustment"] = "increase"
        elif capacity_score >= 60:     # RC003
            pass  # maintain, already default
        elif capacity_score >= 40:     # RC004
            recommendation["volume_adjustment"] = "decrease"
            landmarks = {k: round(v * 0.85) for k, v in landmarks.items()}
        else:                          # RC005
            recommendation["volume_adjustment"] = "decrease"
            recommendation["frequency_adjustment"] = "decrease"
            recommendation["deload_required"] = True
            landmarks = {k: round(v * 0.6) for k, v in landmarks.items()}

    # Illness/injury reduce capacity regardless of the numeric score (rules 6/9).
    if illness or injury:
        recommendation["deload_required"] = True
        recommendation["volume_adjustment"] = "decrease"

    return {
        "recovery_capacity_profile_id": f"RC_{member_id or 'UNKNOWN'}",
        "athlete_profile": {"training_age": exp_key, "age_group": age_group},
        "recovery_inputs": {
            "sleep_score": None,
            "nutrition_score": None,
            "stress_score": None,
            "readiness_score": readiness_score,
            "recovery_score": None,
            "fatigue_score": fatigue_score,
        },
        "capacity": landmarks,
        "modifiers": {
            "illness": illness,
            "injury": injury,
            "caloric_deficit": False,
            "caloric_surplus": False,
        },
        "recommendation": recommendation,
        "capacity_score": capacity_score,
    }
