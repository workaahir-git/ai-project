"""
feedback_engine.py — Engine 43 (Feedback / Explanation Engine — Rebuilt).

Full spec (KB engines["43"].spec_text): converts a post-workout rating
(1-5) + free-text notes into an actionable classification (FB001-FB005),
with pain-keyword detection that ALWAYS overrides the numeric rating.

WHAT THIS MODULE IS VS. WHAT ALREADY EXISTS
    The spec's title bundles "Feedback" and "Explanation" together, but
    this app already has a dedicated, working Explanation engine —
    coaching_explanation_engine.py (Engine 18) — which turns decisions
    other engines already made into plain-language, cited explanations.
    Re-implementing explanation generation here would either duplicate
    #18 or drift from it. So this module does ONLY the half of #43 that
    doesn't exist anywhere yet: the FB001-FB005 CLASSIFICATION of a single
    piece of feedback. Once classified, downstream callers use the
    EXISTING #18 functions to explain what happens next (e.g. a
    possible_pain_flag classification feeds the same
    explain_injury_exclusion()/explain_progression_adjustment() path #18
    already has, rather than a second explanation system).

PAIN KEYWORD LIST — reconciling two existing lists
    progression_engine.py already has its own PAIN_KEYWORDS tuple ("hurt",
    "pain", "sharp", "pinch", "sore joint", "injury", "tweak"), used by
    _contains_pain_language() to gate progression decisions. The KB spec
    for THIS engine lists a different, non-identical set for FB001
    ("sharp", "twinge", "popping", "numbness", "shooting"). Rather than
    picking one and silently dropping terms the other already catches,
    this module takes the UNION of both lists — this is a safety-adjacent
    signal (possible pain / injury), so under-detecting is the worse
    failure mode. See PAIN_KEYWORDS below; if you tune this, tune
    progression_engine.py's list too or the two will drift.

WHY CLASSIFICATION HAPPENS PER-EXERCISE, NOT PER-SESSION
    The spec schema is scoped to a single (athlete_id, exercise_id,
    session_date) feedback record, matching how this app already stores
    workout_exercise_feedback rows (one per exercise per session) — this
    module classifies one row at a time; callers loop over a session's
    rows if they need a full-session view.
"""

from __future__ import annotations

from app.load_adjustment_engine import (
    PAIN_KEYWORDS as _PROGRESSION_PAIN_KEYWORDS,
    _text_has_unnegated_keyword,
)

_SPEC_PAIN_KEYWORDS = ("sharp", "twinge", "popping", "numbness", "shooting")

# Union, de-duplicated, order-stable. See module docstring — deliberately
# broader than either single source list.
PAIN_KEYWORDS = tuple(dict.fromkeys((*_PROGRESSION_PAIN_KEYWORDS, *_SPEC_PAIN_KEYWORDS)))

TOO_EASY_RATINGS = (1, 2)     # FB002
TOO_HARD_RATINGS = (4, 5)     # FB003
APPROPRIATE_RATING = 3        # FB004


def _contains_pain_language(notes: str | None) -> bool:
    if not notes:
        return False
    # Reuses progression_engine's negation-aware check (see that module for
    # the false-positive bug this fixes, e.g. "no pain" no longer matches)
    # rather than a second, plain-substring implementation that would drift.
    return _text_has_unnegated_keyword(notes.lower(), PAIN_KEYWORDS)


def classify_feedback(
    difficulty_rating: int | None,
    notes: str | None,
    exercise_id: str | None = None,
) -> dict:
    """
    FB001-FB005. Returns a dict matching the KB schema's per-record fields
    (classification, pain_keyword_detected, routed_to) — feedback_record_id/
    athlete_id/session_date are caller-supplied metadata, not computed here.

    Priority order (per spec, FB001 always checked first regardless of
    whether a rating was even given):
      1. Pain keyword present -> possible_pain_flag, routed_to constraints_engine
      2. No rating but notes present -> insufficient_data (pain check above still applies)
      3. No rating and no notes -> insufficient_data
      4. Rating present -> too_easy / appropriate / too_hard by band
    """
    pain_detected = _contains_pain_language(notes)

    if pain_detected:
        # FB001 + deterministic rule 1: route to Constraints Engine
        # automatically, not wait for manual review — this app's
        # constraints/safety logic lives in safety_engine.py.
        return {
            "exercise_id": exercise_id,
            "difficulty_rating": difficulty_rating,
            "classification": "possible_pain_flag",
            "pain_keyword_detected": True,
            "routed_to": "safety_engine",
        }

    if difficulty_rating is None:
        # FB005
        return {
            "exercise_id": exercise_id,
            "difficulty_rating": None,
            "classification": "insufficient_data",
            "pain_keyword_detected": False,
            "routed_to": None,
        }

    if difficulty_rating in TOO_EASY_RATINGS:
        classification = "too_easy"
    elif difficulty_rating in TOO_HARD_RATINGS:
        classification = "too_hard"
    elif difficulty_rating == APPROPRIATE_RATING:
        classification = "appropriate"
    else:
        # Out-of-range rating (shouldn't happen given a 1-5 UI control, but
        # fail to insufficient_data rather than guess a bucket).
        classification = "insufficient_data"

    return {
        "exercise_id": exercise_id,
        "difficulty_rating": difficulty_rating,
        "classification": classification,
        "pain_keyword_detected": False,
        "routed_to": None,
    }


def check_consecutive_pattern(recent_classifications: list[str]) -> str | None:
    """
    Deterministic rules 2/3: three consecutive too_easy -> suggest
    progression; three consecutive too_hard -> suggest regression.
    `recent_classifications` should be the exercise's last N classification
    strings, most-recent-last (caller supplies real history — this
    function does not fetch or fabricate any).

    Returns "suggest_progression", "suggest_regression", or None.
    """
    if len(recent_classifications) < 3:
        return None
    last_three = recent_classifications[-3:]
    if all(c == "too_easy" for c in last_three):
        return "suggest_progression"
    if all(c == "too_hard" for c in last_three):
        return "suggest_regression"
    return None
