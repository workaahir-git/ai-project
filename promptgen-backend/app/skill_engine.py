"""
skill_engine.py — Engine 7 (Skill), separates technical complexity from
strength/fatigue per the spec's stated purpose.

Full coverage verified: all 13 movement_ids this app tags are in the KB's
14 skill profiles (1 extra: conditioning — not currently tagged on
anything here).

Real data shape: skill_level (novice/beginner/intermediate/advanced/
expert — 5 levels), coordination/balance/timing/motor_control (1-10 each),
learning_sessions_estimate, coaching_priority (ordered list of cue focus
areas, e.g. ["setup", "bracing", "depth", "bar_path"]).

This app's client-facing experience field (fitness_generator.py's
Beginner/Intermediate/Advanced — matches the dashboard.html <select>
values, 3 levels) is a DIFFERENT, coarser scale than the KB's 5-level
skill_level. This module maps between them explicitly rather than
silently assuming they line up 1:1 — they don't (the client scale has no
separate "novice" or "expert" tier).
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

# KB's 5-level skill scale, ordered low -> high.
_SKILL_ORDER = ("novice", "beginner", "intermediate", "advanced", "expert")

# This app's 3-level client experience scale, mapped to the highest KB
# skill_level a client at that experience is considered ready for. A
# Beginner client can handle up to "beginner" skill exercises; anything
# "intermediate" or above is flagged. This mapping is new engineering
# judgment (the KB doesn't define this correspondence, because it never
# claims a relationship between its 5-level scale and any particular
# consuming app's client-facing scale) — documented as such, not KB-stated.
_CLIENT_EXPERIENCE_CEILING = {
    "Beginner": "beginner",
    "Intermediate": "intermediate",
    "Advanced": "expert",
}


def get_profile(exercise_id: str) -> dict | None:
    movement_id = _MOVEMENT_ID_BY_EXERCISE.get(exercise_id)
    if not movement_id:
        return None
    return kb.get_skill(movement_id)


def exceeds_client_skill(exercise_id: str, client_experience: str) -> bool | None:
    """True if this exercise's skill_level is above what client_experience
    (Beginner/Intermediate/Advanced) is mapped to handle. None if either
    the exercise has no skill profile or client_experience isn't one of
    the 3 recognized values — never guesses in either case."""
    profile = get_profile(exercise_id)
    if profile is None:
        return None
    ceiling = _CLIENT_EXPERIENCE_CEILING.get(client_experience)
    if ceiling is None:
        return None

    exercise_level = profile.get("skill_level")
    if exercise_level not in _SKILL_ORDER:
        return None

    return _SKILL_ORDER.index(exercise_level) > _SKILL_ORDER.index(ceiling)


def top_coaching_cue(exercise_id: str) -> str | None:
    """First (highest-priority) entry in coaching_priority, for a coaching
    copy generator that only has room for one cue. None if no profile or
    the list is empty."""
    profile = get_profile(exercise_id)
    if not profile:
        return None
    cues = profile.get("coaching_priority") or []
    return cues[0] if cues else None
