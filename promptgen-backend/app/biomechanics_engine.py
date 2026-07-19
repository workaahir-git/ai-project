"""
biomechanics_engine.py — Engine 8 (Biomechanics), scoped to what the KB
actually covers.

Full spec (KB engines["8"].spec_text) defines a per-exercise biomechanics
profile: primary_force_vector, resistance_curve, lever_class,
stability_requirement, balance_requirement, center_of_mass_shift,
external_moment_arm, internal_moment_arm_dependency. The KB data (14
profiles) is keyed by movement_id, not exercise_id — i.e. every exercise
that shares a movement_id (e.g. every squat variation) gets the same
profile. This matches the coarse _movement_id granularity already used
everywhere else in this app (exercise_database.py, exercise_selector.py),
so it is NOT a gap: all 13 movement_id values actually used in
exercise_database.py have a matching biomechanics profile (verified by
direct set comparison against the KB — 13/13, plus a 14th profile
["conditioning"] not currently tagged on any exercise here).

What this implements, from the spec's "Deterministic Rules":

  Rule 3 (Exercise Selection MAY compare exercises using biomechanical
  similarity) -> similarity_score(exercise_id_a, exercise_id_b). Simple
  matching-field count over the 8 profile fields, 0.0-1.0. Used for
  substitution/pairing decisions that want "mechanically similar," not
  just "same movement_id."

  Rule 5 (Coaching Explanation SHALL expose biomechanical rationale where
  relevant) -> get_rationale(exercise_id). Plain-English sentence built
  from the profile fields (e.g. stability/balance demands, moment arm).
  coaching_explanation_engine.py doesn't call this yet — wiring that is a
  separate step, this only makes the data available in engine-ready form.

  Rule 4 (Joint Stress SHALL consume moment-arm information) — NOT done
  here. That's joint_stress logic living inside exercise_database.py /
  knowledge_base.get_joint_stress(); this module only exposes the
  moment-arm fields for that engine to read, doesn't modify it. Wiring
  joint_stress to actually read external_moment_arm is a separate,
  deliberate change against already-shipped code — not bundled in here.

Never raises. Returns None / [] on an unknown exercise/movement_id rather
than guessing a profile.
"""

from __future__ import annotations

from app import knowledge_base as kb
from app.exercise_database import EXERCISE_DB

# 8 profile fields the spec defines, in the order similarity is scored over.
_PROFILE_FIELDS = (
    "primary_force_vector",
    "resistance_curve",
    "lever_class",
    "stability_requirement",
    "balance_requirement",
    "center_of_mass_shift",
    "external_moment_arm",
    "internal_moment_arm_dependency",
)

# exercise_id -> movement_id, built once from the same EXERCISE_DB entries
# fitness_generator.py already reads _exercise_id / _movement_id from.
# EXERCISE_DB shape: {muscle_group: {"compound"|"isolation": [exercise_dict, ...]}}
_MOVEMENT_ID_BY_EXERCISE: dict[str, str] = {
    ex["_exercise_id"]: ex["_movement_id"]
    for _muscle_group in EXERCISE_DB.values()
    for _category_list in _muscle_group.values()
    for ex in _category_list
    if ex.get("_exercise_id") and ex.get("_movement_id")
}


def get_profile(exercise_id: str) -> dict | None:
    """Biomechanics profile for this exercise_id, via its movement_id.
    None if the exercise_id is unknown or its movement_id has no profile
    (should not happen for anything currently in EXERCISE_DB — see module
    docstring — but a future exercise with a new movement_id would hit
    this honestly rather than guess)."""
    movement_id = _MOVEMENT_ID_BY_EXERCISE.get(exercise_id)
    if not movement_id:
        return None
    return kb.get_biomechanics(movement_id)


def similarity_score(exercise_id_a: str, exercise_id_b: str) -> float | None:
    """0.0-1.0 fraction of the 8 profile fields that match exactly between
    two exercises. None if either exercise has no profile. Same movement_id
    on both sides trivially scores 1.0 (identical profile) — this is most
    useful comparing exercises across different movement_ids that still
    share mechanical demands (e.g. a landmine press vs a dumbbell shoulder
    press: different movement_id, similar stability/moment-arm profile)."""
    a = get_profile(exercise_id_a)
    b = get_profile(exercise_id_b)
    if a is None or b is None:
        return None
    matches = sum(1 for f in _PROFILE_FIELDS if a.get(f) == b.get(f))
    return round(matches / len(_PROFILE_FIELDS), 2)


def get_rationale(exercise_id: str) -> str | None:
    """Plain-English biomechanical note for this exercise, or None if no
    profile exists. Deliberately short (one or two clauses) — this is meant
    to be a supporting line under an exercise name, not a technique essay."""
    p = get_profile(exercise_id)
    if p is None:
        return None

    parts: list[str] = []

    stability = p.get("stability_requirement")
    balance = p.get("balance_requirement")
    if stability == "high" or balance == "high":
        parts.append("demands high stability/balance control")
    elif stability == "moderate" or balance == "moderate":
        parts.append("requires moderate stability control")

    moment_arm = p.get("external_moment_arm")
    if moment_arm == "long":
        parts.append("long external moment arm — technique breakdown costs more here")
    elif moment_arm == "short":
        parts.append("short external moment arm — more forgiving on form")

    curve = p.get("resistance_curve")
    if curve == "ascending":
        parts.append("resistance builds through the range, hardest at lockout/top")
    elif curve == "descending":
        parts.append("resistance is hardest at the start of the rep")
    elif curve == "bell":
        parts.append("resistance peaks mid-range")

    if not parts:
        return None
    return "; ".join(parts).capitalize() + "."
