"""
progression_engine.py
──────────────────────────────────────────────────────────────────────────────
Reads the member's last logged feedback for an exercise and returns a
deterministic coaching adjustment for the NEXT plan generation. No LLM calls.

Reads from workout_set_feedback / workout_exercise_feedback — the tables the
FRONTEND actually writes to (see result.html's client-side Supabase calls).
NOT plan_feedback (that table + main.py's /api/submit-feedback endpoint are
dead code — the frontend never calls that endpoint).

Both feedback tables now carry a `cycle_number` column (see
sql/add_cycle_tracking.sql) instead of being pure upsert-only on
member_id/day_index/exercise. When callers pass `cycle_number` (the cycle
number of the plan currently being generated), this module reads the
member's PREVIOUS cycle specifically (`cycle_number - 1`) rather than
whatever happens to be the latest row for that exercise — a real,
unambiguous "last cycle" read instead of one that only worked by upsert-
timing coincidence.

`cycle_number=None` (the default) preserves the old unscoped-read behavior
for any caller that hasn't been updated yet — it reads with no cycle
filter at all, same as before this change. Every caller inside this app
(fitness_generator.build_deterministic_workout_days) has been updated to
pass cycle_number; None is a compatibility fallback, not the intended
steady-state.

Fails conservative everywhere: any missing data, unmatched exercise, or
Supabase error returns {"action": "baseline", "note": None} rather than
raising — a wrong workout note is a UX ding, but this must never be able to
crash plan generation.
"""

from __future__ import annotations

from app.db import supabase
from app.exercise_database import get_substitutes_for_exercise

# Same substring-match style as exercise_database._parse_injury_keywords /
# safety_engine's keyword scans — deliberately simple, no NLP layer.
PAIN_KEYWORDS = ("hurt", "pain", "sharp", "pinch", "sore joint", "injury", "tweak")

# BUGFIX (found during Engine 40/43 integration smoke-testing): plain
# substring matching means "no pain", "not sore", "without any pinch" all
# incorrectly matched as a pain report — the exact opposite of what the
# member said. This is a safety-adjacent false positive (wrongly restricts/
# substitutes an exercise, or wrongly flags a session for review), so it's
# worth a real fix rather than leaving it. The actual negation-aware check
# now lives in text_matching.py (dependency-free, to avoid a circular
# import with exercise_database.py, which also needed this same fix) —
# re-exported here as _text_has_unnegated_keyword so existing callers
# (feedback_engine.py) don't need to change their import.
from app.text_matching import text_has_unnegated_keyword as _text_has_unnegated_keyword


def _contains_pain_language(notes: str | None) -> bool:
    if not notes:
        return False
    return _text_has_unnegated_keyword(notes.lower(), PAIN_KEYWORDS)


def _fetch_feedback(member_id: str, exercise_name: str, read_cycle: int | None) -> dict:
    """
    One read from workout_exercise_feedback (difficulty, notes), one from
    workout_set_feedback (weight_kg, reps_used), both filtered to this
    member + this exercise name. If read_cycle is given, both reads are
    additionally filtered to that exact cycle_number — the previous
    cycle's rows, not "whatever's currently in the table." Never raises.
    """
    result = {"difficulty": None, "notes": None, "had_any_log": False, "last_weight_kg": None}
    try:
        q = (
            supabase.table("workout_exercise_feedback")
            .select("difficulty, notes")
            .eq("member_id", member_id)
            .eq("exercise", exercise_name)
        )
        if read_cycle is not None:
            q = q.eq("cycle_number", read_cycle)
        ex_res = q.execute()
        if ex_res.data:
            row = ex_res.data[0]
            result["difficulty"] = row.get("difficulty")
            result["notes"] = row.get("notes")
    except Exception:
        pass

    try:
        q = (
            supabase.table("workout_set_feedback")
            .select("weight_kg, reps_used")
            .eq("member_id", member_id)
            .eq("exercise", exercise_name)
        )
        if read_cycle is not None:
            q = q.eq("cycle_number", read_cycle)
        set_res = q.execute()
        if set_res.data:
            result["had_any_log"] = any(
                row.get("weight_kg") is not None or row.get("reps_used")
                for row in set_res.data
            )
            # Top working-set weight across logged sets — most representative
            # of what the member can actually handle on this exercise, used
            # as the baseline load_prescription_engine.py scales from.
            weights = [row.get("weight_kg") for row in set_res.data if row.get("weight_kg") is not None]
            if weights:
                result["last_weight_kg"] = max(weights)
    except Exception:
        pass

    return result


def get_adjustment(member_id: str | None, exercise_name: str, exercise_id: str | None,
                    cycle_number: int | None = None) -> dict:
    """
    Returns {"action": str, "note": str | None, "last_weight_kg": float | None}.

    action is one of: "baseline", "progress", "hold", "deload_or_hold",
    "flag_pain". last_weight_kg is the member's top logged working-set
    weight for this exercise (None if nothing's been logged yet) — used by
    load_prescription_engine.py to turn "add weight" into an actual number
    rather than just a text note.

    cycle_number is the cycle number of the plan currently being generated.
    When given, this reads the member's PREVIOUS cycle (cycle_number - 1)
    specifically. If cycle_number is 1 (or less), there is no previous
    cycle by definition — returns baseline immediately, no Supabase call.
    cycle_number=None preserves the old unscoped-read behavior (reads
    whatever's in the table, no cycle filter) for backward compatibility.
    """
    if not member_id:
        return {"action": "baseline", "note": None, "last_weight_kg": None}

    read_cycle = None
    if cycle_number is not None:
        read_cycle = cycle_number - 1
        if read_cycle < 1:
            # First-ever cycle for this member — no prior cycle exists to
            # read feedback from, by definition. Don't even hit Supabase.
            return {"action": "baseline", "note": None, "last_weight_kg": None}

    try:
        fb = _fetch_feedback(member_id, exercise_name, read_cycle)
    except Exception:
        return {"action": "baseline", "note": None, "last_weight_kg": None}

    difficulty = fb["difficulty"]
    notes = fb["notes"]
    had_any_log = fb["had_any_log"]
    last_weight_kg = fb.get("last_weight_kg")

    # Pain check first, unconditionally, before any difficulty branch.
    if _contains_pain_language(notes):
        note = (
            "You flagged some pain/discomfort on this last cycle — keep the "
            "weight conservative or skip it today."
        )
        if exercise_id:
            try:
                subs = get_substitutes_for_exercise(exercise_id)
            except Exception:
                subs = []
            good_subs = [s for s in subs if s.get("equivalence_score", 0) >= 60]
            if good_subs:
                best = good_subs[0]
                note += (
                    f" This may be worth swapping — {best['exercise_id'].replace('_', ' ').title()} "
                    "targets the same muscles with a different joint pattern. "
                    "Check with a coach if pain persists."
                )
        return {"action": "flag_pain", "note": note, "last_weight_kg": last_weight_kg}

    if difficulty is None and not had_any_log:
        return {"action": "baseline", "note": None, "last_weight_kg": last_weight_kg}

    if difficulty in (1, 2):
        return {
            "action": "progress",
            "note": "Felt easy last cycle — add weight or a rep or two this time.",
            "last_weight_kg": last_weight_kg,
        }

    if difficulty == 3:
        return {
            "action": "hold",
            "note": "Same effort as last cycle — match your weight, focus on clean reps.",
            "last_weight_kg": last_weight_kg,
        }

    if difficulty in (4, 5):
        return {
            "action": "deload_or_hold",
            "note": "Felt tough last cycle — keep the same weight this week, don't add load yet.",
            "last_weight_kg": last_weight_kg,
        }

    # difficulty missing but a set was logged (weak signal) — treat like hold.
    return {
        "action": "hold",
        "note": "Same effort as last cycle — match your weight, focus on clean reps.",
        "last_weight_kg": last_weight_kg,
    }
