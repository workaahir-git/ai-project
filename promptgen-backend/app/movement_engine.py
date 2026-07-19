"""
movement_engine.py — Engine 1 (Movement Intelligence).

Real finding, documented rather than silently worked around: KB engine 1's
markdown spec_text describes an 11-category idealized taxonomy (squat,
hip_hinge, horizontal_push, vertical_push, horizontal_pull, vertical_pull,
lunge, carry, rotation, anti_rotation, gait). That table is STALE — it does
not match this engine's own structured `data.canonical_movement_ids` (14
values: carry, conditioning, core, hinge, horizontal_pull, horizontal_push,
isolation_arm, isolation_calf, isolation_leg, isolation_shoulder, lunge,
squat, vertical_pull, vertical_push), which IS what every other DATA_COMPLETE
engine (4 joint stress, 6 recovery, 7 skill, 8 biomechanics, 16 tempo) was
actually built against, and which matches this app's real _movement_id
tagging in exercise_database.py exactly (13/13, verified by direct set
comparison). This module treats `data.canonical_movement_ids` as ground
truth and the markdown table as aspirational/superseded documentation —
flagging the divergence here rather than have it resurface as a confusing
mismatch in a future session.

What this implements, from the spec's own deterministic rules:

  Rule 1 (One primary movement_id per exercise) + Rule 4 (Exercise Metadata
  SHALL reference movement_id) -> `is_canonical(movement_id)` and
  `audit_app_movement_ids()`, a real consistency check comparing every
  distinct _movement_id actually used across exercise_database.py's
  EXERCISE_DB against the KB's canonical list. This is genuinely useful:
  it would catch a future exercise being tagged with a typo'd or
  newly-invented movement_id that no other engine (joint stress, recovery,
  skill, biomechanics, tempo) has a profile for — exactly the kind of
  silent gap the earlier biomechanics_engine.py coverage check had to be
  done by hand for. Run this instead, next time.

  Rule 3 (Movement taxonomy cannot be redefined downstream) — this module
  is intentionally read-only. No function here can add to or modify the
  canonical list; that would violate the rule as written.

Never raises. Unknown movement_id -> False / reported, not an exception.
"""

from __future__ import annotations

from app import knowledge_base as kb
from app.exercise_database import EXERCISE_DB

CANONICAL_MOVEMENT_IDS: tuple[str, ...] = kb.get_canonical_movement_ids()


def is_canonical(movement_id: str) -> bool:
    return movement_id in CANONICAL_MOVEMENT_IDS


def audit_app_movement_ids() -> dict:
    """Compares every _movement_id actually used in EXERCISE_DB against the
    KB's canonical list. Returns {'used': [...], 'canonical': [...],
    'uncovered': [...], 'unused_canonical': [...]}. 'uncovered' is the one
    that matters operationally — any movement_id tagged on a real exercise
    that the KB doesn't recognize, meaning joint_stress/recovery/skill/
    biomechanics/tempo profiles will all silently miss it too."""
    used: set[str] = {
        ex["_movement_id"]
        for muscle_group in EXERCISE_DB.values()
        for category_list in muscle_group.values()
        for ex in category_list
        if ex.get("_movement_id")
    }
    canonical = set(CANONICAL_MOVEMENT_IDS)
    return {
        "used": sorted(used),
        "canonical": sorted(canonical),
        "uncovered": sorted(used - canonical),
        "unused_canonical": sorted(canonical - used),
    }
