"""
readiness_engine.py — Engine 9 (Readiness), built on the Phase 2 foundation
decision: a 1-5 pre-session self-report (see sql/add_readiness_checkins.sql
for why that option was chosen over re-deriving from difficulty trend or
building a full sleep/soreness/stress intake).

Full spec (KB engines["9"].spec_text) wants five separate sub-scores
(sleep_score, recovery_score, soreness_score, stress_score) plus the
composite readiness_score. This app collects exactly ONE real signal —
the self-report rating — so:

  readiness_score = rating * 20  (1-5 star scale -> 0-100, same mapping
                                   philosophy as difficulty stars elsewhere)
  sleep_score / recovery_score / soreness_score / stress_score = None,
    NOT invented. A single self-report number cannot honestly be split
    into four independent sub-scores; reporting fabricated sub-scores
    would look more precise than the data actually is.

  pain_flag   — reused from progression_engine._contains_pain_language
                against the check-in's own notes field (same keyword list
                used everywhere else in this app for pain detection).
  illness_flag — same substring-match philosophy, a small dedicated
                keyword list (fever, flu, sick, cold, covid, unwell) since
                pain and illness are different signals and conflating them
                would blur RD001 (pain->rest) with RD002 (illness->rest),
                even though both currently resolve to "rest" anyway.

RD003 (recovery_score < 40 -> deload) is NOT applied — there is no
recovery_score in this app (that's Engine 6, not built). RD004 (sleep_score
< 50 for 2 days) is NOT applied for the same reason. Only RD001, RD002,
RD005, and the zone table (which only needs readiness_score) are real here.

Never raises. No check-in for the requested day returns readiness_score=None
and recommendation="train" (the safe default: absence of a bad signal is
not evidence of a bad signal) rather than blocking a session on missing data.
"""

from __future__ import annotations

from app.db import supabase
from app.load_adjustment_engine import _contains_pain_language
from app.text_matching import text_has_unnegated_keyword as _text_has_unnegated_keyword

ILLNESS_KEYWORDS = ("fever", "flu", "sick", "cold", "covid", "unwell", "nausea", "vomit")


def _contains_illness_language(notes: str | None) -> bool:
    """
    BUGFIX (found during Engine 40/43 integration audit): plain substring
    matching meant "no fever, feeling great" matched "fever" and forced a
    "rest" recommendation (RD001/RD002) even though the member explicitly
    said they were fine. Same fix as progression_engine.py's pain check —
    see text_matching.py.
    """
    if not notes:
        return False
    return _text_has_unnegated_keyword(notes.lower(), ILLNESS_KEYWORDS)


def _default_profile(member_id: str | None, note: str) -> dict:
    return {
        "readiness_profile_id": f"READY_{member_id or 'UNKNOWN'}",
        "readiness_score": None,
        "sleep_score": None,
        "recovery_score": None,
        "soreness_score": None,
        "stress_score": None,
        "pain_flag": False,
        "illness_flag": False,
        "recommendation": "train",
        "note": note,
    }


def _fetch_checkin(member_id: str, cycle_number: int, day_index: int) -> dict | None:
    try:
        res = (
            supabase.table("readiness_checkins")
            .select("rating, notes")
            .eq("member_id", member_id)
            .eq("cycle_number", cycle_number)
            .eq("day_index", day_index)
            .execute()
        )
        return res.data[0] if res.data else None
    except Exception:
        return None


def get_readiness(
    member_id: str | None,
    cycle_number: int | None,
    day_index: int | None,
) -> dict:
    """
    Returns a dict matching the spec schema (module docstring covers which
    fields are real vs. always None). cycle_number/day_index identify the
    specific session's check-in — this is a per-session signal, not a
    per-member running average.
    """
    if not member_id or cycle_number is None or day_index is None:
        return _default_profile(member_id, "No member/cycle/day specified.")

    row = _fetch_checkin(member_id, cycle_number, day_index)
    if not row:
        return _default_profile(member_id, "No readiness check-in submitted for this session.")

    rating = row.get("rating")
    notes = row.get("notes")
    if rating is None:
        return _default_profile(member_id, "Check-in found but missing a rating.")

    readiness_score = rating * 20
    pain_flag = _contains_pain_language(notes)
    illness_flag = _contains_illness_language(notes)

    # RD001 / RD002 — pain or illness override the score entirely.
    if pain_flag or illness_flag:
        recommendation = "rest"
    elif readiness_score >= 75:  # RD005
        recommendation = "train"
    elif readiness_score >= 60:
        recommendation = "modified_train"
    elif readiness_score >= 40:
        recommendation = "modified_train"
    else:
        recommendation = "rest"

    return {
        "readiness_profile_id": f"READY_{member_id}",
        "readiness_score": readiness_score,
        "sleep_score": None,
        "recovery_score": None,
        "soreness_score": None,
        "stress_score": None,
        "pain_flag": pain_flag,
        "illness_flag": illness_flag,
        "recommendation": recommendation,
        "note": None,
    }
