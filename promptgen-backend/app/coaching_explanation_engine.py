"""
coaching_explanation_engine.py — Engine 18 (Coaching Explanation)

Converts decisions ALREADY MADE by other engines (progression_engine,
weak_point_engine, conflict_engine, exercise_database's contraindication
filter, the substitution endpoint) into the traceable, structured
explanation schema from the KB spec. This module makes ZERO new decisions
and computes ZERO new signal — per spec rule "Explanations SHALL never
invent evidence," every field here is a translation of something an
upstream engine already computed, with a citation back to which engine
produced it.

Rule coverage (CE001-CE005 from KB engines["18"].spec_text):
  CE001 (safety conflict)      -> explain_injury_exclusion / explain_progression_adjustment
                                   (flag_pain) / explain_conflict_note
  CE002 (plateau detected)     -> explain_weak_point
  CE003 (deload prescribed)    -> explain_progression_adjustment (deload_or_hold)
  CE004 (exercise substituted) -> explain_substitution
  CE005 (progression blocked)  -> explain_progression_adjustment (hold)

confidence_score is fixed per rule type, reflecting how directly-observed
vs inferred that signal is (a member's own pain-language report scores
higher than an aggregated multi-exercise plateau inference) — not computed
by any model, per spec rule "confidence scores SHALL reflect supporting
evidence only."
"""

from __future__ import annotations

CONFIDENCE_BY_RULE = {
    "pain": 95,               # directly stated by the member, not inferred
    "contraindication": 100,  # exact tag match, zero inference
    "conflict": 80,           # inferred from joint-stress data overlap
    "plateau": 75,            # inferred from aggregated difficulty ratings
    "deload_or_progress": 90, # directly stated single-cycle difficulty rating
    "substitution": 85,       # equivalence-score based, not a direct report
    "hold": 70,               # weakest signal — single-cycle "same effort" read
}


def _make_explanation(explanation_id, recommendation, rationale, engine, reference_id,
                       confidence_score, user_message, safety_notice=None):
    return {
        "explanation_id": explanation_id,
        "recommendation": recommendation,
        "rationale": rationale,
        "evidence": {"engine": engine, "reference_id": reference_id},
        "confidence_score": confidence_score,
        "safety_notice": safety_notice,
        "user_message": user_message,
    }


def explain_progression_adjustment(exercise_name: str, exercise_id: str | None, action: str,
                                    note: str | None) -> dict | None:
    """
    Wraps a progression_engine.get_adjustment() result into the formal
    explanation schema. Returns None for "baseline" (no decision was
    actually made — nothing to explain).
    """
    if action == "baseline" or not note:
        return None
    ref = exercise_id or exercise_name

    if action == "flag_pain":
        return _make_explanation(
            explanation_id=f"EXP_{ref}_PAIN", recommendation=f"Caution flagged on {exercise_name}",
            rationale=["Member reported pain/discomfort language in their last logged feedback for this exercise"],
            engine="progression_engine", reference_id=ref,
            confidence_score=CONFIDENCE_BY_RULE["pain"], safety_notice=note, user_message=note,
        )
    if action == "deload_or_hold":
        return _make_explanation(
            explanation_id=f"EXP_{ref}_DELOAD", recommendation=f"Hold weight steady on {exercise_name}",
            rationale=["Member rated this exercise 4-5/5 difficulty last cycle"],
            engine="progression_engine", reference_id=ref,
            confidence_score=CONFIDENCE_BY_RULE["deload_or_progress"], user_message=note,
        )
    if action == "progress":
        return _make_explanation(
            explanation_id=f"EXP_{ref}_PROGRESS", recommendation=f"Add weight or reps on {exercise_name}",
            rationale=["Member rated this exercise 1-2/5 difficulty last cycle"],
            engine="progression_engine", reference_id=ref,
            confidence_score=CONFIDENCE_BY_RULE["deload_or_progress"], user_message=note,
        )
    if action == "hold":
        return _make_explanation(
            explanation_id=f"EXP_{ref}_HOLD", recommendation=f"Match last cycle's weight on {exercise_name}",
            rationale=["Member rated this exercise 3/5 difficulty last cycle, or logged a set without a difficulty rating"],
            engine="progression_engine", reference_id=ref,
            confidence_score=CONFIDENCE_BY_RULE["hold"], user_message=note,
        )
    return None


def explain_weak_point(weak_point: dict) -> dict:
    region = weak_point["affected_region"]
    return _make_explanation(
        explanation_id=f"EXP_WP_{region}", recommendation=f"Extra accessory focus on {region}",
        rationale=[f"{region.title()} exercises averaged higher difficulty than other muscle groups across logged feedback"],
        engine="weak_point_engine", reference_id=region,
        confidence_score=CONFIDENCE_BY_RULE["plateau"], user_message=weak_point["note"],
    )


def explain_substitution(source_exercise_id: str, source_name: str, chosen_substitute: dict) -> dict:
    return _make_explanation(
        explanation_id=f"EXP_SUB_{source_exercise_id}",
        recommendation=f"{chosen_substitute['display_name']} suggested in place of {source_name}",
        rationale=[
            f"Equivalence score {chosen_substitute['equivalence_score']}/100 "
            "(shared movement pattern, exercise type, and primary muscles)",
        ],
        engine="substitution_engine", reference_id=source_exercise_id,
        confidence_score=CONFIDENCE_BY_RULE["substitution"],
        user_message=(
            f"{chosen_substitute['display_name']} targets the same muscles as {source_name} "
            f"with a {chosen_substitute['equivalence_score']}/100 match."
        ),
    )


def explain_injury_exclusion(injury_keywords: set, excluded_note: str) -> dict:
    keys = ",".join(sorted(injury_keywords))
    return _make_explanation(
        explanation_id="EXP_INJURY_EXCLUSION", recommendation="Some exercises excluded or reduced today",
        rationale=[f"Member's intake/notes flagged: {', '.join(sorted(injury_keywords))}"],
        engine="exercise_database_contraindication_filter", reference_id=keys,
        confidence_score=CONFIDENCE_BY_RULE["contraindication"],
        safety_notice=excluded_note, user_message=excluded_note,
    )


def explain_conflict_note(note: str) -> dict:
    return _make_explanation(
        explanation_id=f"EXP_CONFLICT_{abs(hash(note)) % 100000}",
        recommendation="Exercise order adjusted / flagged",
        rationale=["Two exercises in this day both showed high joint-stress ratings on the same joint"],
        engine="conflict_engine", reference_id="CF003",
        confidence_score=CONFIDENCE_BY_RULE["conflict"], user_message=note,
    )


def build_plan_explanations(days: list, weak_points: list) -> list:
    """
    Walks a generated plan's `days` (as produced by
    build_deterministic_workout_days — needs each exercise's
    _progression_action / _progression_note, and each day's
    _injury_safety_note / _conflict_notes, all already attached there) plus
    the separately-computed weak_points list, and returns a flat list of
    explanation objects. Safe to call with partial/missing fields — skips
    anything it can't explain rather than raising.
    """
    explanations = []
    for day in days:
        if day.get("is_rest"):
            continue
        for ex in day.get("exercises", []):
            exp = explain_progression_adjustment(
                ex.get("name", ""), ex.get("exercise_id"),
                ex.get("_progression_action", "baseline"), ex.get("_progression_note"),
            )
            if exp:
                explanations.append(exp)
        if day.get("_injury_safety_note"):
            explanations.append(
                explain_injury_exclusion(set(day.get("_injury_keywords", [])), day["_injury_safety_note"])
            )
        for note in day.get("_conflict_notes", []):
            explanations.append(explain_conflict_note(note))

    for wp in weak_points:
        explanations.append(explain_weak_point(wp))

    return explanations
