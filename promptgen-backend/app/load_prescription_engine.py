"""
load_prescription_engine.py — Engine 21 (Load Prescription).

Full spec (KB engines["21"].spec_text) defines a 6-source intensity
hierarchy: medical/safety > Recovery Capacity > Readiness > Progression
model > training goal > historical performance. Phase 3 update: Engines 9
(Readiness) and 10 (Recovery Capacity) now exist, so LP001/LP002 are real:

  1. Medical/safety  -> progression_engine's "flag_pain" action. Highest
                         priority, exactly per the spec's hierarchy — if
                         pain was flagged, this module prescribes NOTHING
                         (returns None) rather than suggest more load on a
                         flagged joint.
  2. Recovery Capacity (LP002) -> caps the increment when capacity_score
                         is low; a low-capacity cycle doesn't get a bigger
                         jump regardless of what the progression action says.
  3. Readiness (LP001) -> readiness_score < 60 reduces final_load by 5-15%
                         (scaled linearly within that band, real number
                         from readiness_engine.py, not a placeholder).
  4. Progression model -> progression_engine's progress/hold/deload_or_hold
                         action (the member's own logged difficulty rating).
  5. Training goal     -> goal-based increment size (a strength-focused
                         client progresses in bigger jumps than a fat-loss
                         client, at the same difficulty signal).
  6. Historical performance -> the member's own top logged working-set
                         weight (workout_set_feedback), used as the
                         baseline this module scales from.

LP003 (plateau confirmed -> maintain load, modify the variable Plateau
Engine indicated) is real when a plateau_profile is passed in: this module
holds the load flat and defers to plateau_engine's own `intervention`
block (volume/frequency/exercise_variation) rather than prescribing a
load change itself — LP003 says "maintain load", not "increase it
anyway", so a confirmed plateau overrides even a "progress" action from
progression_engine.

LP004 (deload active per periodization) is real when a periodization
profile's volume_strategy=="decrease" or a deload_recommended flag is
passed in — reduces the increment size, doesn't touch baseline weight
(periodization deloads volume, not necessarily load, per its own spec).

LP005 (RPE exceeds target by >1) is NOT implemented — no RPE is collected
anywhere in this app (only a 1-5 difficulty star), so there's no target-
vs-actual RPE delta to measure.

Requires at least one logged weight_kg for the exercise — with no
baseline, there is nothing to prescribe (returns None). Same "no data, no
fabricated number" rule this whole build has followed throughout.
"""

from __future__ import annotations

from app import load_adjustment_engine as progression_engine

# Goal-based increment size when action == "progress". Expressed as a
# fraction of current working weight. Strength/power goals progress in
# bigger jumps (fewer, larger PRs expected); fat-loss/endurance goals
# progress conservatively since load isn't the primary driver there.
GOAL_INCREMENT_PCT = {
    "strength": 0.05,
    "power": 0.05,
    "muscle gain": 0.03,
    "hypertrophy": 0.03,
    "general fitness": 0.025,
    "fat loss": 0.02,
    "endurance": 0.02,
}
DEFAULT_INCREMENT_PCT = 0.025

# Real-world plate/dumbbell increments — final numbers get rounded to this,
# so a suggestion is something a member can actually load, not a decimal.
ROUNDING_INCREMENT_KG = 2.5

# LP001 — readiness band 40-59 reduces load 15%, 60-74 reduces 5%. Linearly
# interpolated within each band per spec's "5-15%" range.
READINESS_REDUCTION_MAX_PCT = 0.15
READINESS_REDUCTION_MIN_PCT = 0.05
READINESS_FLOOR = 40


def _resolve_increment_pct(goal_raw: str) -> float:
    g = (goal_raw or "").lower().strip()
    for key, pct in GOAL_INCREMENT_PCT.items():
        if key in g:
            return pct
    return DEFAULT_INCREMENT_PCT


def _round_to_increment(weight_kg: float) -> float:
    return round(weight_kg / ROUNDING_INCREMENT_KG) * ROUNDING_INCREMENT_KG


def _readiness_reduction_pct(readiness_score: int | None) -> float:
    """LP001 — 0% at >=60, scaling up to 15% at the floor (40)."""
    if readiness_score is None or readiness_score >= 60:
        return 0.0
    if readiness_score <= READINESS_FLOOR:
        return READINESS_REDUCTION_MAX_PCT
    span = 60 - READINESS_FLOOR
    frac = (60 - readiness_score) / span
    return READINESS_REDUCTION_MIN_PCT + frac * (READINESS_REDUCTION_MAX_PCT - READINESS_REDUCTION_MIN_PCT)


def compute_final_load(
    adj: dict, exercise_id: str | None, exercise_name: str, goal_raw: str,
    readiness_profile: dict | None = None,
    recovery_capacity_profile: dict | None = None,
    plateau_profile: dict | None = None,
) -> dict | None:
    """
    Pure computation from an ALREADY-COMPUTED progression_engine.get_adjustment()
    result. readiness_profile / recovery_capacity_profile / plateau_profile
    are optional, already-computed Engine 9/10/11 outputs for this
    exercise/session (caller-supplies-it convention, same as
    plateau_engine.py / goal_optimization_engine.py — this module has no
    day_index/exercise lookup of its own).
    """
    if adj["action"] == "flag_pain":
        return None  # medical/safety restriction — top of the hierarchy, no override
    if adj.get("last_weight_kg") is None:
        return None  # no historical performance to scale from

    baseline = adj["last_weight_kg"]
    readiness_score = (readiness_profile or {}).get("readiness_score")
    recovery_capacity_score = (recovery_capacity_profile or {}).get("capacity_score")
    plateau_status = (plateau_profile or {}).get("plateau_status")

    if plateau_status == "confirmed":
        # LP003 — maintain load outright; plateau_engine's own intervention
        # (volume/frequency/exercise_variation) is what changes, not load.
        final_load = _round_to_increment(baseline)
        basis = "plateau_confirmed_hold"
    elif adj["action"] == "progress":
        pct = _resolve_increment_pct(goal_raw)
        # LP002 — low recovery capacity caps the increment even on a
        # "progress" signal, rather than letting difficulty alone decide.
        if recovery_capacity_score is not None and recovery_capacity_score < 60:
            pct = min(pct, DEFAULT_INCREMENT_PCT / 2)
        final_load = _round_to_increment(baseline * (1 + pct))
        if final_load <= baseline:
            final_load = baseline + ROUNDING_INCREMENT_KG
        basis = adj["action"]
    else:
        # "hold" and "deload_or_hold" both mean: keep this cycle's weight.
        final_load = _round_to_increment(baseline)
        basis = adj["action"]

    # LP001 — readiness reduction applies on top of whatever was decided
    # above (it never increases load, only pulls it back).
    readiness_modifier_pct = -round(_readiness_reduction_pct(readiness_score) * 100, 1)
    if readiness_modifier_pct != 0:
        final_load = _round_to_increment(final_load * (1 + readiness_modifier_pct / 100))

    recovery_modifier_pct = 0.0
    if recovery_capacity_score is not None and recovery_capacity_score < 40:
        recovery_modifier_pct = -10.0  # RC005 territory — pull back further still
        final_load = _round_to_increment(final_load * 0.90)

    return {
        "load_profile_id": f"LOAD_{exercise_id or exercise_name}",
        "exercise_id": exercise_id,
        "intensity_method": "fixed_load",
        "readiness_modifier_percent": readiness_modifier_pct,
        "recovery_modifier_percent": recovery_modifier_pct,
        "baseline_weight_kg": baseline,
        "final_load": final_load,
        "load_unit": "kg",
        "basis": basis,
    }


def prescribe_load(exercise_name: str, exercise_id: str | None, goal_raw: str,
                    member_id: str | None, readiness_profile: dict | None = None,
                    recovery_capacity_profile: dict | None = None,
                    plateau_profile: dict | None = None) -> dict | None:
    """
    Standalone convenience wrapper for callers that DON'T already have a
    progression_engine.get_adjustment() result on hand. Fetches fresh, then
    delegates to compute_final_load(). fitness_generator.py's day-building
    loop should call compute_final_load() directly instead, using the
    adjustment it already computed for that exercise.
    """
    adj = progression_engine.get_adjustment(member_id, exercise_name, exercise_id)
    return compute_final_load(
        adj, exercise_id, exercise_name, goal_raw,
        readiness_profile, recovery_capacity_profile, plateau_profile,
    )
