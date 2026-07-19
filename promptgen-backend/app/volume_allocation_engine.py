"""
volume_allocation_engine.py — Engine 22 (Volume Allocation), scoped to what
this app can support without Engine 10 (Recovery Capacity).

Full spec (KB engines["22"].spec_text) has Recovery Capacity cap weekly
allocation (VA005) — Engine 10 doesn't exist (see progression_engine.py /
load_prescription_engine.py's own notes on this same gap). So this module
implements VA001-VA004 (classify current volume against MEV/MAV/MRV) as a
READ-ONLY reporting layer — it tells you where a muscle's weekly volume
sits, it does not (yet) feed a correction back into exercise selection.
Actually adjusting selection based on this is Periodization/Programming
Engine territory (17/19/20), not built.

Landmark source: Renaissance Periodization's published volume landmarks
(Israetel, Case & Davis, "Scientific Principles of Hypertrophy Training",
2019 — cross-checked against current RP/FitnessVolt/FitnessRec published
figures this session, standard/stable numbers in the field, not this app's
own invention). Landmarks are for an INTERMEDIATE trainee; this module
scales them for beginner (lower recoverable volume) and advanced (higher)
per a simple, commonly-used practical heuristic (~70% / ~115% of
intermediate) — that scaling factor is a simplification, not itself a
sourced research figure, and is labeled as such in the output.

This app's exercise buckets don't separate quads/hamstrings/glutes into
different training days (all fall under one "legs" bucket) — so "legs"
uses a single blended landmark set leaning toward quad-dominant work
(since squat-pattern compounds anchor most leg days here), not three
separate landmarks. Documented as a known simplification, not a bug.
"""

from __future__ import annotations

# All figures = weekly sets, INTERMEDIATE trainee baseline.
LANDMARKS_INTERMEDIATE = {
    "chest":     {"mv": 8,  "mev": 10, "mav_low": 12, "mav_high": 20, "mrv": 22},
    "back":      {"mv": 6,  "mev": 10, "mav_low": 14, "mav_high": 22, "mrv": 25},
    "shoulders": {"mv": 6,  "mev": 8,  "mav_low": 16, "mav_high": 22, "mrv": 26},
    "biceps":    {"mv": 5,  "mev": 8,  "mav_low": 14, "mav_high": 20, "mrv": 26},
    "triceps":   {"mv": 4,  "mev": 6,  "mav_low": 10, "mav_high": 14, "mrv": 18},
    "legs":      {"mv": 8,  "mev": 10, "mav_low": 14, "mav_high": 20, "mrv": 22},  # blended, see module docstring
    "calves":    {"mv": 6,  "mev": 8,  "mav_low": 12, "mav_high": 16, "mrv": 20},
    "core":      {"mv": 0,  "mev": 6,  "mav_low": 16, "mav_high": 20, "mrv": 25},
    "traps":     {"mv": 0,  "mev": 4,  "mav_low": 12, "mav_high": 16, "mrv": 20},
}

# Practical scaling heuristic, not itself a sourced figure — see module docstring.
EXPERIENCE_SCALE = {"beginner": 0.70, "intermediate": 1.00, "advanced": 1.15}


def _scaled_landmarks(muscle: str, exp_key: str) -> dict | None:
    base = LANDMARKS_INTERMEDIATE.get(muscle)
    if not base:
        return None
    scale = EXPERIENCE_SCALE.get(exp_key, 1.0)
    return {k: (round(v * scale) if v > 0 else 0) for k, v in base.items()}


def _classify(weekly_sets: int, landmarks: dict) -> str:
    if weekly_sets < landmarks["mev"]:
        return "below_mev"
    if weekly_sets <= landmarks["mav_high"]:
        return "optimal"
    if weekly_sets <= landmarks["mrv"]:
        return "above_mav"
    return "above_mrv"


ACTION_BY_STATUS = {
    "below_mev": "Increase volume — currently below what's needed to make progress on this muscle.",
    "optimal": "Maintain — currently in the productive range for this muscle.",
    "above_mav": "Monitor recovery — currently in the higher end of adaptive volume; watch for excess fatigue.",
    "above_mrv": "Reduce volume — currently above what can likely be recovered from; risks stalling or regressing.",
}


def compute_weekly_volume(days: list) -> dict:
    """
    Sums prescribed sets per muscle bucket across all non-rest days in a
    generated week. `days` is exactly what build_deterministic_workout_days()
    returns — each exercise already carries `muscle` and `sets`.
    """
    totals: dict = {}
    for day in days:
        if day.get("is_rest"):
            continue
        for ex in day.get("exercises", []):
            muscle = (ex.get("muscle") or "").lower()
            sets = ex.get("sets")
            if not muscle or not isinstance(sets, int):
                continue
            totals[muscle] = totals.get(muscle, 0) + sets
    return totals


def build_volume_allocation(days: list, experience_raw: str) -> list:
    """
    Returns a list of Volume Allocation profile dicts, one per muscle bucket
    that appears anywhere in this week's plan. Read-only classification —
    does not modify `days`.
    """
    exp_key = (experience_raw or "intermediate").lower().strip()
    if exp_key not in EXPERIENCE_SCALE:
        exp_key = "intermediate"

    weekly_sets_by_muscle = compute_weekly_volume(days)
    profiles = []
    for muscle, weekly_sets in weekly_sets_by_muscle.items():
        landmarks = _scaled_landmarks(muscle, exp_key)
        if landmarks is None:
            continue
        status = _classify(weekly_sets, landmarks)
        profiles.append({
            "volume_profile_id": f"VOL_{muscle.upper()}_{exp_key.upper()}",
            "muscle_group": muscle,
            "weekly_sets": weekly_sets,
            "mev": landmarks["mev"],
            "mav_low": landmarks["mav_low"],
            "mav_high": landmarks["mav_high"],
            "mrv": landmarks["mrv"],
            "allocation_status": status,
            "note": ACTION_BY_STATUS[status],
            "_experience_scale_applied": exp_key,
        })
    return profiles
