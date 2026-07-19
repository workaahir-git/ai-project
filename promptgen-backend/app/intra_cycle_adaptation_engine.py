"""
Intra-cycle adaptation — NOT one of the 43 KB engine numbers. Purpose-built
for one specific product decision: within the SAME 14-day cycle, when a
training day-type (e.g. "push") repeats later that same week, the second
occurrence should react to feedback from the first occurrence, not wait
for the next full 2-week plan generation.

Deliberately narrow, real scope:
  - possible_pain_flag  -> substitute using the exercise's own KB-authored
    `regressions` list first (an easier/safer variant a human already
    picked for THIS exercise), falling back to substitution.py's ranked
    candidate_substitutes (equivalence_score) only if no regression is on
    file for it.
  - too_easy            -> same idea, using `progressions` (a harder
    variant) first, ranked substitutes as fallback. If NEITHER exists,
    this does NOT invent a "harder" exercise — exercise_database.py has
    no validated difficulty-ranking field to fabricate one from. It holds
    the exercise and lets load_prescription_engine's normal progressive-
    overload (more weight/reps) be the "harder" lever instead, which is
    the scientifically defensible response to "felt too easy" anyway.
  - appropriate / too_hard / insufficient_data -> hold. Same exercise,
    carry the just-logged weight forward as this week's own last_weight_kg
    for load_prescription_engine, so the SECOND occurrence still gets a
    real progression decision instead of repeating the exact same number.

Callers are expected to already have `classify_feedback()`'s output
(feedback_engine.py) — this module makes no DB reads and does no pain-
language parsing of its own; it is a pure decision + KB-lookup function
given a classification that's already been computed.
"""

from __future__ import annotations

from app.exercise_database import get_full_exercise_profile, get_substitutes_for_exercise


def decide_exercise_adaptation(exercise_id: str | None, classification: str) -> dict:
    """
    Returns:
      {"action": "hold", "reason": str, "requires_attention": bool}
      {"action": "substitute", "new_exercise_id": str, "reason": str, "requires_attention": False}

    `requires_attention` is True in exactly one case: pain was flagged AND
    the KB has no safe substitute or regression on file for this exercise
    at all. That combination deserves a visibly different signal than
    every other hold — "no swap was made, and that's specifically true
    for the exercise that just caused discomfort" is not the same message
    as "no swap was made because it was already easy enough." Callers
    (the feedback endpoint / eventual frontend) should surface this one
    distinctly — e.g. "no in-app substitute for this movement; consider
    reducing load or checking with a trainer" — rather than silently
    repeating the exercise with no comment.
    """
    if not exercise_id or classification in ("appropriate", "too_hard", "insufficient_data"):
        return {"action": "hold", "reason": classification or "no_classification", "requires_attention": False}

    profile = get_full_exercise_profile(exercise_id) or {}
    meta = profile.get("metadata") or {}

    if classification == "possible_pain_flag":
        pool = list(meta.get("regressions") or [])
        reason = "pain_flagged_last_time_regressed_to_safer_variant"
    elif classification == "too_easy":
        pool = list(meta.get("progressions") or [])
        reason = "rated_too_easy_last_time_progressed_to_harder_variant"
    else:
        return {"action": "hold", "reason": f"unhandled_classification:{classification}", "requires_attention": False}

    if not pool:
        # Fall back to substitution.py's ranked candidates — same pool
        # get_substitutes_for_exercise() already provides, best match first.
        ranked = get_substitutes_for_exercise(exercise_id)
        pool = [c["exercise_id"] for c in ranked]

    if pool:
        return {"action": "substitute", "new_exercise_id": pool[0], "reason": reason, "requires_attention": False}

    # Nothing on file either way (KB has no progression/regression/
    # substitute rule for this exercise, INCLUDING exercises where session
    # 8's original curation explicitly set `no_safe_substitute: true` —
    # a deliberate human safety call, e.g. grip/band/conditioning
    # exercises with no obvious in-KB downgrade. Never overridden with a
    # fabricated candidate just to fill the field; holding is correct.
    if classification == "possible_pain_flag":
        return {
            "action": "hold",
            "reason": "pain_flagged_no_safe_substitute_on_file_reduce_load_or_consult_trainer",
            "requires_attention": True,
        }
    return {"action": "hold", "reason": "no_safe_option_on_file", "requires_attention": False}
