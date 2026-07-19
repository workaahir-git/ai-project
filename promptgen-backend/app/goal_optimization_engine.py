"""
goal_optimization_engine.py — Engine 25 (Goal Optimization), scoped to what
real profile data supports.

Full spec (KB engines["25"].spec_text) wants a canonical primary_goal enum
(strength|hypertrophy|power|endurance|fat_loss|general_fitness) plus
recovery_capacity/readiness constraints. This app only ever collects goal
as free text (profile["goal"], e.g. "muscle gain", "fat loss") — the same
field fitness_generator.py already keyword-matches to build goal_label. This
module reuses that exact mapping so the two stay consistent rather than
diverging into two different "what does this goal mean" interpretations.

recovery_capacity / readiness constraints are real now (Phase 2 — Engines
10/9 exist). Pass already-computed `recovery_capacity_profile` /
`readiness_profile` dicts in (same convention as plateau_engine.py — this
module doesn't fetch them itself, since readiness is per-session/day and
this module has no day_index). GO002/GO005 apply for real when given.
medical_flags IS real: it reuses
exercise_database._parse_injury_keywords against profile["medical_notes"],
the same parser safety_engine/exercise_database already use to exclude
exercises, so "medical flag present" here means the same thing it means
everywhere else in this codebase.

plateau_confirmed is an optional caller-supplied bool (from plateau_engine
.detect_plateau, if the caller has it) — GO003 only fires when the caller
actually knows a plateau is confirmed; this module never calls plateau_engine
itself; it does not have an exercise_id/exercise_name to check.

Never raises. Missing/unrecognized goal text defaults to general_fitness
(the safest, most balanced target) rather than guessing a specific one.
"""

from __future__ import annotations

from app.exercise_database import _parse_injury_keywords

_GOAL_KEYWORDS = (
    ("strength", "strength"),
    ("power", "power"),
    ("endurance", "endurance"),
    ("fat loss", "fat_loss"),
    ("weight loss", "fat_loss"),
    ("cut", "fat_loss"),
    ("muscle", "hypertrophy"),
    ("bulk", "hypertrophy"),
    ("gain", "hypertrophy"),
    ("mass", "hypertrophy"),
    ("recovery", "general_fitness"),
    ("maintenance", "general_fitness"),
)

_OPTIMIZATION_TARGETS = {
    "strength":         {"volume": "maintain", "intensity": "increase", "frequency": "maintain", "exercise_bias": "compound"},
    "hypertrophy":       {"volume": "increase", "intensity": "maintain", "frequency": "maintain", "exercise_bias": "balanced"},
    "power":             {"volume": "maintain", "intensity": "increase", "frequency": "maintain", "exercise_bias": "compound"},
    "endurance":         {"volume": "increase", "intensity": "decrease", "frequency": "increase", "exercise_bias": "balanced"},
    "fat_loss":          {"volume": "increase", "intensity": "maintain", "frequency": "increase", "exercise_bias": "balanced"},
    "general_fitness":   {"volume": "maintain", "intensity": "maintain", "frequency": "maintain", "exercise_bias": "balanced"},
}


def _canonical_goal(goal_raw: str) -> str:
    text = (goal_raw or "").lower()
    for keyword, canonical in _GOAL_KEYWORDS:
        if keyword in text:
            return canonical
    return "general_fitness"


def build_goal_profile(
    member_id: str | None,
    profile: dict | None = None,
    plateau_confirmed: bool = False,
    goal_text: str | None = None,
    medical_flags: list | None = None,
    recovery_capacity_profile: dict | None = None,
    readiness_profile: dict | None = None,
) -> dict:
    """
    Two calling conventions, since goal/medical_notes live in different
    places depending on the caller:

    - At generation time, `profile` is the raw intake-form dict
      (profile.get("goal"), profile.get("medical_notes")) — the form is
      never persisted as-is, so this only works while it's still in hand.
    - At read time (e.g. an API endpoint reading a saved plan), the raw
      form is gone; pass `goal_text` (the plan's own goal_label, e.g.
      "Muscle Gain Plan") and `medical_flags` (aggregated from the plan's
      per-day `_injury_keywords`, already computed by
      exercise_database._parse_injury_keywords at generation time —
      re-parsing free text isn't needed since the flags are already there).

    profile takes priority over goal_text/medical_flags when both given.
    """
    if profile:
        goal_raw = profile.get("goal", "")
        medical_notes = profile.get("medical_notes") or profile.get("notes") or ""
        resolved_flags = sorted(_parse_injury_keywords(medical_notes))
    else:
        goal_raw = goal_text or ""
        resolved_flags = sorted(set(medical_flags or []))

    primary_goal = _canonical_goal(goal_raw)
    targets = dict(_OPTIMIZATION_TARGETS[primary_goal])
    medical_flags = resolved_flags

    recovery_capacity_score = (recovery_capacity_profile or {}).get("capacity_score")
    readiness_score = (readiness_profile or {}).get("readiness_score")

    # GO001 — safety conflict present overrides optimization entirely.
    if medical_flags:
        targets = {"volume": "maintain", "intensity": "maintain", "frequency": "maintain", "exercise_bias": "balanced"}
        note = (
            f"Medical/injury flags on file ({', '.join(medical_flags)}) — optimization targets "
            "held at maintain across the board until cleared."
        )
    # GO002 — low recovery capacity reduces optimization intensity.
    elif recovery_capacity_score is not None and recovery_capacity_score < 60:
        targets["intensity"] = "decrease" if targets["intensity"] != "decrease" else targets["intensity"]
        targets["volume"] = "decrease"
        note = f"Recovery capacity is low ({recovery_capacity_score}) — optimization scaled back."
    # GO005 — low readiness delays progression.
    elif readiness_score is not None and readiness_score < 60:
        targets["intensity"] = "maintain"
        note = f"Readiness is low this session ({readiness_score}) — holding intensity, delaying progression."
    # GO003 — confirmed plateau modifies strategy (only when caller tells us).
    elif plateau_confirmed:
        targets["volume"] = "increase" if targets["volume"] != "decrease" else targets["volume"]
        targets["intensity"] = "maintain"
        note = "Plateau confirmed on at least one exercise — strategy nudged toward volume, not intensity."
    else:
        note = None

    return {
        "goal_profile_id": f"GOAL_{member_id or 'UNKNOWN'}",
        "athlete_id": member_id,
        "primary_goal": primary_goal,
        "secondary_goal": None,  # no secondary-goal field collected anywhere in this app
        "goal_priority_score": 100,  # single goal on file -> no competing priority to weigh
        "optimization_targets": targets,
        "constraints": {
            "recovery_capacity": recovery_capacity_score,
            "readiness": readiness_score,
            "medical_flags": medical_flags,
        },
        "note": note,
    }
