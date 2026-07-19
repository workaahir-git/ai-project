"""
recovery_engine.py — Engine 6 (Recovery), NOT the same thing as
recovery_capacity_engine.py (Engine 10).

Easy to conflate these two, so being explicit: Engine 10
(`recovery_capacity_engine.py`, already wired) answers "how ready is this
athlete for training in general, today" from checkin/readiness data.
Engine 6 (this file) answers a narrower, different question: "how long
should this specific movement pattern rest before being trained again" —
per-movement-pattern recovery spacing, not overall daily capacity. A caller
could plausibly want both for the same day and get different answers; that
is intended, not a bug to reconcile.

Full coverage verified: all 13 movement_ids this app tags are in the KB's
14 recovery profiles (1 extra: conditioning — not currently tagged on
anything here).

Real data shape: minimum_recovery_hours, recommended_recovery_hours,
fatigue_source (local/systemic/neural, 1-10 each), readiness_threshold.
No `recovery_modifiers` or `recovery_status` enum field in the actual
profiles (the spec's markdown table describes those, the JSON data
doesn't carry them) — this module computes recovery_status itself from
hours-elapsed against the two thresholds, rather than pretending the KB
supplies a status directly.
"""

from __future__ import annotations

from app import knowledge_base as kb
from app.exercise_database import EXERCISE_DB

_MOVEMENT_ID_BY_EXERCISE: dict[str, str] = {
    ex["_exercise_id"]: ex["_movement_id"]
    for _mg in EXERCISE_DB.values()
    for _cat in _mg.values()
    for ex in _cat
    if ex.get("_exercise_id") and ex.get("_movement_id")
}


def get_profile_for_exercise(exercise_id: str) -> dict | None:
    movement_id = _MOVEMENT_ID_BY_EXERCISE.get(exercise_id)
    if not movement_id:
        return None
    return kb.get_recovery(movement_id)


def get_recovery_status(movement_id: str, hours_since_last_trained: float) -> dict | None:
    """hours_since_last_trained: how long since this movement pattern was
    last trained (caller's responsibility to compute from workout history —
    this module has no access to that on its own).

    Returns None if the movement_id has no KB profile. Otherwise:
    {'status': 'recovered'|'partial'|'insufficient',
     'minimum_recovery_hours': int, 'recommended_recovery_hours': int,
     'hours_since_last_trained': float}

    status is 'insufficient' if under minimum_recovery_hours, 'partial' if
    at/past minimum but under recommended, 'recovered' at/past recommended.
    This is a simple hours-elapsed model — it does NOT factor in sleep/
    nutrition/soreness/stress modifiers the spec's markdown table
    mentions, because the actual KB data for this engine doesn't carry
    those fields (see module docstring). A caller wanting that nuance
    would need to combine this with recovery_capacity_engine.py's
    readiness-checkin data separately, not expect it folded in here."""
    profile = kb.get_recovery(movement_id)
    if profile is None:
        return None

    minimum = profile["minimum_recovery_hours"]
    recommended = profile["recommended_recovery_hours"]

    if hours_since_last_trained < minimum:
        status = "insufficient"
    elif hours_since_last_trained < recommended:
        status = "partial"
    else:
        status = "recovered"

    return {
        "status": status,
        "minimum_recovery_hours": minimum,
        "recommended_recovery_hours": recommended,
        "hours_since_last_trained": hours_since_last_trained,
    }
