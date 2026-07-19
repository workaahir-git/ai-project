"""
progression_regression_engine.py — Engine 3 (Progression & Regression),
scoped to what real data supports.

NOT the same job as app/progression_engine.py: that module adjusts LOAD
(add weight / hold / deload) for the same exercise from a difficulty
rating. This module decides whether the EXERCISE ITSELF should change —
advance to a harder variation, regress to an easier one, or hold — per KB
engine 3's spec.

Full spec wants technique_score and RIR as primary inputs. Neither is
collected anywhere in this app (intake/feedback only ever capture a 1-5
difficulty star, free-text notes, and weight_kg/reps_used). Building
P001/R001 (technique-score thresholds) would mean inventing a technique
score, so this module does NOT implement them. It also cannot pick a
DIRECTION (harder vs. easier) among substitutes, because
exercise_database's substitution data only carries equivalence_score
(how similar two exercises are), never a difficulty tier — so "advance
to variation X" is not something this KB can currently answer. What IS
implemented, using only real logged fields:

  R002 (pain during execution)          -> regress: pull from real
                                            substitute pool, immediate.
  R003 (unable to reach minimum reps)   -> regress signal: reps_used below
                                            the plan's prescribed minimum
                                            for 2+ consecutive logged cycles.
  P002 (top of rep range achieved twice)-> progress signal: reps_used at or
                                            above the plan's prescribed
                                            maximum for 2+ consecutive cycles.

  P001/R001 (technique score)           -> NOT implemented (no data).
  P003/R004 (variation direction)       -> NOT implemented (no difficulty
                                            tier in substitution data); this
                                            module only flags "swap
                                            available" via the same
                                            substitute pool, without
                                            claiming a direction.

Never raises. Missing member_id, missing plan/rep-range data, or fewer
than 2 logged cycles returns decision="hold" with an explanatory note.
"""

from __future__ import annotations

import re

from app.db import supabase
from app.exercise_database import get_substitutes_for_exercise
from app.load_adjustment_engine import _contains_pain_language

LOOKBACK_CYCLES = 4


def _default_result(exercise_id: str, note: str) -> dict:
    return {
        "progression_rule_id": f"PR_{exercise_id}",
        "exercise_id": exercise_id,
        "decision": "hold",
        "target_exercise_id": None,
        "reason": note,
    }


def _parse_rep_range(reps_str: str | None) -> tuple[int | None, int | None]:
    """'8-10 reps' -> (8, 10). '12' -> (12, 12). Anything unparsable -> (None, None)."""
    if not reps_str:
        return None, None
    nums = re.findall(r"\d+", reps_str)
    if not nums:
        return None, None
    if len(nums) == 1:
        n = int(nums[0])
        return n, n
    return int(nums[0]), int(nums[1])


def _prescribed_rep_range(member_id: str, exercise_name: str) -> tuple[int | None, int | None]:
    # plans stores the plan under plan_json, not a `workout` column — see
    # adherence_engine.py's _get_active_plan docstring for why.
    try:
        res = (
            supabase.table("plans")
            .select("plan_json, cycle_number")
            .eq("member_id", member_id)
            .order("cycle_number", desc=True)
            .limit(1)
            .execute()
        )
        if not res.data:
            return None, None
        days = (res.data[0].get("plan_json") or {}).get("workout", {}).get("days", [])
        for day in days:
            for ex in day.get("exercises", []) or []:
                if ex.get("name") == exercise_name:
                    return _parse_rep_range(ex.get("reps"))
    except Exception:
        pass
    return None, None


def _recent_reps_used(member_id: str, exercise_name: str, up_to_cycle: int) -> list[tuple[int, int]]:
    lowest = max(1, up_to_cycle - LOOKBACK_CYCLES + 1)
    try:
        res = (
            supabase.table("workout_set_feedback")
            .select("cycle_number, reps_used")
            .eq("member_id", member_id)
            .eq("exercise", exercise_name)
            .gte("cycle_number", lowest)
            .lte("cycle_number", up_to_cycle)
            .execute()
        )
        rows = res.data or []
    except Exception:
        return []

    by_cycle: dict[int, int] = {}
    for r in rows:
        cn, reps = r.get("cycle_number"), r.get("reps_used")
        if cn is None or reps is None:
            continue
        by_cycle[cn] = max(reps, by_cycle.get(cn, 0))
    return sorted(by_cycle.items())


def _latest_notes(member_id: str, exercise_name: str, cycle_number: int) -> str | None:
    try:
        res = (
            supabase.table("workout_exercise_feedback")
            .select("notes")
            .eq("member_id", member_id)
            .eq("exercise", exercise_name)
            .eq("cycle_number", cycle_number)
            .execute()
        )
        if res.data:
            return res.data[0].get("notes")
    except Exception:
        pass
    return None


def evaluate(
    member_id: str | None,
    exercise_id: str,
    exercise_name: str,
    cycle_number: int | None = None,
) -> dict:
    """
    cycle_number is the cycle currently being generated. History is read up
    to cycle_number - 1 (last completed cycle). cycle_number=None or <2
    returns hold immediately.
    """
    if not member_id:
        return _default_result(exercise_id, "No member_id provided.")
    if cycle_number is None or cycle_number - 1 < 1:
        return _default_result(exercise_id, "No completed cycle yet to assess.")

    up_to = cycle_number - 1

    # R002 — pain overrides everything else, immediate regression signal.
    notes = _latest_notes(member_id, exercise_name, up_to)
    if _contains_pain_language(notes):
        subs = get_substitutes_for_exercise(exercise_id)
        good = [s for s in subs if s.get("equivalence_score", 0) >= 60]
        target = good[0]["exercise_id"] if good else None
        return {
            "progression_rule_id": f"PR_{exercise_id}",
            "exercise_id": exercise_id,
            "decision": "regress_variation",
            "target_exercise_id": target,
            "reason": "Pain flagged during execution — swap to a substitute with a different joint pattern.",
        }

    reps_history = _recent_reps_used(member_id, exercise_name, up_to)
    min_reps, max_reps = _prescribed_rep_range(member_id, exercise_name)

    if len(reps_history) < 2 or min_reps is None:
        return _default_result(
            exercise_id,
            f"Only {len(reps_history)} logged cycle(s) with rep data, or no prescribed rep "
            "range on file — not enough to judge rep-range progression.",
        )

    last_two = [reps for _, reps in reps_history[-2:]]

    if max_reps is not None and all(r >= max_reps for r in last_two):
        # P002 — top of rep range achieved twice.
        return {
            "progression_rule_id": f"PR_{exercise_id}",
            "exercise_id": exercise_id,
            "decision": "increase_resistance",
            "target_exercise_id": None,
            "reason": f"Hit the top of the {min_reps}-{max_reps} rep range for 2 straight cycles — add load.",
        }

    if all(r < min_reps for r in last_two):
        # R003 — unable to reach minimum reps.
        subs = get_substitutes_for_exercise(exercise_id)
        good = [s for s in subs if s.get("equivalence_score", 0) >= 60]
        target = good[0]["exercise_id"] if good else None
        return {
            "progression_rule_id": f"PR_{exercise_id}",
            "exercise_id": exercise_id,
            "decision": "regress_variation" if target else "reduce_load",
            "target_exercise_id": target,
            "reason": f"Falling short of the {min_reps} rep floor for 2 straight cycles — "
                      + ("swap to an easier substitute." if target else "reduce load."),
        }

    return _default_result(exercise_id, "Rep performance within expected range — no change.")
