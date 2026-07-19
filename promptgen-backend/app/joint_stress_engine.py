"""
joint_stress_engine.py — Engine 4 (Joint Stress), deliberately scoped.

Real data shape differs from the spec's markdown schema table — that table
describes a per-joint dict (shoulder/elbow/wrist/spine/hip/knee/ankle, each
minimal..very_high) but the actual KB profiles are simpler: one
primary_joint, one secondary_joint, and a single 1-10 stress_rating. Built
against the real data, not the aspirational schema.

Full coverage verified: all 13 movement_ids this app tags are in the KB's
15 joint-stress profiles (2 extra: rotation, conditioning — not currently
tagged on anything here).

conflict_engine.py (Engine 14, wired in session 6) already calls
`kb.get_joint_stress()` directly for reorder-distance scoring. This module
is a thin, testable wrapper around the same data for other callers —
`get_profile()`, `stress_rating()`.

**Deliberately NOT implemented**: cross-referencing stress_rating/
primary_joint against this app's own `contraindicated_for` exercise tags
or against validator.py's condition->avoid-tag mapping. validator.py's own
comments (see `_AVOID_TAG_TO_PATTERNS`) explicitly flag that bridging two
independently-built vocabularies is "new engineering judgment... not a
KB-stated equivalence" and is kept deliberately small and explicit rather
than guessed broadly. A third guessed bridge (KB joint names <-> this
app's contraindicated_for joint-name strings, which use different casing
and terms — "lower back" vs KB's "spine") would repeat exactly the mistake
that comment warns against. If this cross-reference is wanted, it should
be a deliberate, reviewed addition to validator.py alongside its existing
two bridges — not a third one guessed independently here.

Never raises. Unknown exercise_id -> None, not an exception.
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


def get_profile(exercise_id: str) -> dict | None:
    """Joint stress profile for this exercise, via its movement_id. None if
    unknown — never guesses a stress rating for an exercise the KB has no
    profile for."""
    movement_id = _MOVEMENT_ID_BY_EXERCISE.get(exercise_id)
    if not movement_id:
        return None
    return kb.get_joint_stress(movement_id)


def stress_rating(exercise_id: str) -> int | None:
    """Just the 1-10 stress_rating, for callers that only need a sortable
    number (e.g. ranking a candidate list by mechanical stress)."""
    profile = get_profile(exercise_id)
    return profile.get("stress_rating") if profile else None
