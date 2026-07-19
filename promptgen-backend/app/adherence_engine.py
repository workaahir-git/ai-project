"""
adherence_engine.py — Engine 15 (Adherence), scoped to what real plan/feedback
data actually supports.

Full spec (KB engines["15"].spec_text) wants exercise_substitution_rate and
preferred_training_days derived from explicit substitution logs. This app
has no substitution-log table — substitutions only ever happen as a coaching
NOTE from progression_engine.py (flag_pain), never recorded as a discrete
event. Building a substitution rate off that would be fabricating a number,
so this module implements:

  attendance_rate          — distinct day_index with ANY logged feedback this
                              cycle, divided by days_per_week (the real
                              session count prescribed that cycle).
  workout_completion_rate  — distinct exercises logged this cycle, divided by
                              distinct exercises prescribed in that cycle's
                              plan (read from plans.workout->days).
  exercise_substitution_rate — NOT implemented (no data source). Always None.
  preferred_training_days  — day_name values that appear in logged feedback,
                              ranked by frequency. [] if nothing logged.
  missed_sessions          — days_per_week - distinct day_index logged.
  schedule_consistency     — derived from attendance_rate (>=0.8 high,
                              >=0.5 moderate, else low).

adherence_score = round(100 * (0.5*attendance_rate + 0.5*completion_rate)).
Deliberately excludes substitution_rate from the score since it's untracked.

Deterministic rules honored (per spec):
  1. Adherence SHALL be evaluated before diagnosing a plateau — this module
     has no dependency on plateau_engine.py, so plateau_engine.py calls this
     one, not the reverse.
  2/3. AD001/AD002/AD003 drive the `recommendation` block below.

Never raises. Missing member_id, no active plan, or any Supabase error
returns a conservative default profile (score=None, recommendation=maintain)
rather than crashing plan generation or the plateau gate.
"""

from __future__ import annotations

from app.db import supabase

ATTENDANCE_HIGH = 0.80
ATTENDANCE_MODERATE = 0.50


def _default_profile(member_id: str | None) -> dict:
    return {
        "adherence_profile_id": f"AD_{member_id or 'UNKNOWN'}",
        "adherence_score": None,
        "attendance_rate": None,
        "workout_completion_rate": None,
        "exercise_substitution_rate": None,
        "schedule_consistency": "unknown",
        "missed_sessions": None,
        "preferred_training_days": [],
        "recommendation": {
            "volume_adjustment": "maintain",
            "session_duration": "maintain",
            "exercise_complexity": "maintain",
        },
        "note": "Not enough logged data yet to assess adherence.",
    }


def _get_active_plan(member_id: str) -> dict | None:
    """
    plans stores the full generated plan under plan_json (see main.py's
    insert into "plans") — there is no separate `workout` or
    `days_per_week` column. workout.days lives inside plan_json, and its
    length IS the training-days-per-week count: day_index (used by the
    feedback tables) is defined as this array's position (see
    sql/add_cycle_tracking.sql's header comment), so workout.days never
    includes rest days.
    """
    try:
        res = (
            supabase.table("plans")
            .select("plan_json, cycle_number")
            .eq("member_id", member_id)
            .order("cycle_number", desc=True)
            .limit(1)
            .execute()
        )
        return res.data[0] if res.data else None
    except Exception:
        return None


def _days_per_week(plan: dict) -> int:
    try:
        return len((plan.get("plan_json") or {}).get("workout", {}).get("days", []))
    except Exception:
        return 0


def _prescribed_exercise_count(plan: dict) -> int:
    try:
        days = (plan.get("plan_json") or {}).get("workout", {}).get("days", [])
        names = set()
        for day in days:
            for ex in day.get("exercises", []) or []:
                name = ex.get("name")
                if name:
                    names.add(name)
        return len(names)
    except Exception:
        return 0


def get_adherence_profile(member_id: str | None, cycle_number: int | None = None) -> dict:
    """
    Returns a dict matching the spec schema (fields listed in module
    docstring). cycle_number is the cycle currently being generated/queried;
    reads feedback for cycle_number - 1 (the completed cycle whose adherence
    we're actually able to judge), same convention as progression_engine.py
    and weak_point_engine.py. cycle_number=None reads unscoped (all rows).
    """
    if not member_id:
        return _default_profile(member_id)

    read_cycle = None
    if cycle_number is not None:
        read_cycle = cycle_number - 1
        if read_cycle < 1:
            return _default_profile(member_id)

    plan = _get_active_plan(member_id)
    days_per_week = _days_per_week(plan) if plan else 0
    prescribed_exercises = _prescribed_exercise_count(plan) if plan else 0

    try:
        q = (
            supabase.table("workout_exercise_feedback")
            .select("day_index, day_name, exercise")
            .eq("member_id", member_id)
        )
        if read_cycle is not None:
            q = q.eq("cycle_number", read_cycle)
        rows = q.execute().data or []
    except Exception:
        rows = []

    if not rows or days_per_week <= 0:
        return _default_profile(member_id)

    logged_day_indexes = {r["day_index"] for r in rows if r.get("day_index") is not None}
    logged_exercises = {r["exercise"] for r in rows if r.get("exercise")}
    day_name_counts: dict[str, int] = {}
    for r in rows:
        dn = r.get("day_name")
        if dn:
            day_name_counts[dn] = day_name_counts.get(dn, 0) + 1

    attendance_rate = min(1.0, len(logged_day_indexes) / days_per_week)
    completion_rate = (
        min(1.0, len(logged_exercises) / prescribed_exercises)
        if prescribed_exercises > 0
        else None
    )
    missed_sessions = max(0, days_per_week - len(logged_day_indexes))
    preferred_days = sorted(day_name_counts, key=day_name_counts.get, reverse=True)

    score_components = [attendance_rate]
    if completion_rate is not None:
        score_components.append(completion_rate)
    adherence_score = round(100 * sum(score_components) / len(score_components))

    if attendance_rate >= ATTENDANCE_HIGH:
        consistency = "high"
    elif attendance_rate >= ATTENDANCE_MODERATE:
        consistency = "moderate"
    else:
        consistency = "low"

    # AD001 / AD002 / AD003 — deterministic recommendation block.
    recommendation = {
        "volume_adjustment": "maintain",
        "session_duration": "maintain",
        "exercise_complexity": "maintain",
    }
    if adherence_score < 60:  # AD001
        recommendation["exercise_complexity"] = "decrease"
        recommendation["session_duration"] = "shorten"
    if missed_sessions >= 2:  # AD002
        recommendation["volume_adjustment"] = "decrease"
    if adherence_score is not None and adherence_score >= 90 and completion_rate is not None and completion_rate >= 0.9:
        # AD003 requires 4 weeks of >90% — this module only ever sees one
        # cycle's window, so this is a single-cycle proxy, not the full
        # 4-week rule. Documented rather than silently over-claiming.
        recommendation["volume_adjustment"] = "increase" if recommendation["volume_adjustment"] == "maintain" else recommendation["volume_adjustment"]

    return {
        "adherence_profile_id": f"AD_{member_id}",
        "adherence_score": adherence_score,
        "attendance_rate": round(attendance_rate, 2),
        "workout_completion_rate": round(completion_rate, 2) if completion_rate is not None else None,
        "exercise_substitution_rate": None,
        "schedule_consistency": consistency,
        "missed_sessions": missed_sessions,
        "preferred_training_days": preferred_days,
        "recommendation": recommendation,
        "note": None,
    }
