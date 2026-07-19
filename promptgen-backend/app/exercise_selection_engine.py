"""
exercise_selection_engine.py — Engine 20 (Exercise Selection), scoped as a
READ-ONLY reporting layer, same pattern volume_allocation_engine.py already
uses for its own engine.

The actual selection LOGIC already exists and already runs at generation
time in exercise_database.py's pick_exercises_for_day() — equipment
filtering, injury-based exclusion, movement-pattern slotting, and
priority ordering are all real and already deterministic (ES001/ES003/
ES004 are effectively already enforced there). Rebuilding that logic a
second time here under a new name would duplicate it, not implement it.

What this module adds: a per-exercise SELECTION PROFILE record, assembled
from data pick_exercises_for_day() and exercise_database.get_full_exercise_
profile() already expose, in the spec's schema shape:

  candidate_exercises  — real, from exercise_database.get_substitutes_for_
                         exercise() (the same substitute pool progression_
                         regression_engine.py already reuses for its own
                         swap suggestions).
  selection_score      — the picked exercise's own equivalence_score
                         relative to its top substitute (100 if it has no
                         substitutes on file — nothing to be equivalent to).
  exclusion_reasons    — real, but coarse: this app's pick loop returns
                         `used_fallback`/`injury_keywords` at the DAY level
                         (which slot needed equipment relaxed, which injury
                         keywords excluded something), not a per-candidate
                         audit trail of exactly which exercise_id was
                         excluded for which reason. So exclusion_reasons
                         here is a day-level note when `used_fallback` was
                         true for that day, not a structured per-exercise
                         reason list — documented as coarser than the
                         spec's sample (which shows one specific excluded
                         exercise_id + reason).
  equipment_required   — real, from the exercise's own metadata.
  prerequisites.minimum_skill — real, from the movement's skill profile
                         (Engine 5 — get_skill()).
  prerequisites.readiness_score — real IF a readiness_profile is passed in
                         (Phase 2 convention, same as every other engine
                         here); otherwise None.

Never raises. Missing KB data for an exercise_id returns a minimal profile
with selection_score=None rather than guessing.
"""

from __future__ import annotations

from app.exercise_database import get_full_exercise_profile, get_substitutes_for_exercise


def build_selection_profile(
    exercise_id: str,
    goal: str,
    day_used_fallback: bool = False,
    day_injury_keywords: list | None = None,
    readiness_profile: dict | None = None,
) -> dict:
    profile = get_full_exercise_profile(exercise_id)
    if not profile:
        return {
            "selection_profile_id": f"SEL_{exercise_id}",
            "goal": goal,
            "movement_id": None,
            "selected_exercise_id": exercise_id,
            "candidate_exercises": [],
            "selection_score": None,
            "exclusion_reasons": [],
            "equipment_required": [],
            "prerequisites": {"minimum_skill": None, "readiness_score": None},
            "note": "No KB profile found for this exercise_id.",
        }

    meta = profile["metadata"]
    substitutes = get_substitutes_for_exercise(exercise_id) or []
    candidate_ids = [s["exercise_id"] for s in substitutes]

    selection_score = 100
    if substitutes:
        top = max(substitutes, key=lambda s: s.get("equivalence_score", 0))
        selection_score = top.get("equivalence_score", 100)

    exclusion_reasons = []
    if day_used_fallback:
        exclusion_reasons.append(
            "Equipment for the ideal pick wasn't available, or an injury excluded an option "
            "this day — see day_injury_keywords for which."
        )
    if day_injury_keywords:
        exclusion_reasons.append(f"Injury keywords in play this day: {', '.join(day_injury_keywords)}")

    skill = profile.get("skill") or {}

    return {
        "selection_profile_id": f"SEL_{exercise_id}",
        "goal": goal,
        "movement_id": meta.get("movement_id"),
        "selected_exercise_id": exercise_id,
        "candidate_exercises": candidate_ids,
        "selection_score": selection_score,
        "exclusion_reasons": exclusion_reasons,
        "equipment_required": meta.get("equipment") or [],
        "prerequisites": {
            "minimum_skill": skill.get("skill_level"),
            "readiness_score": (readiness_profile or {}).get("readiness_score"),
        },
        "note": None,
    }
