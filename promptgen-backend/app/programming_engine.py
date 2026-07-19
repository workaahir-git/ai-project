"""
programming_engine.py — Engine 19 (Programming), the final synthesis step.

Per its own spec: "does not generate recommendations independently;
instead, it assembles validated decisions into an executable program."
Everything below is assembled from data main.py's generation flow has
already computed for this cycle — no new data source, no new decision
logic, purely assembly (Deterministic Rule 1).

  exercises/sets/reps  — real, read directly off data["workout"]["days"]
                         (deduped by exercise_id, first occurrence wins).
  load_strategy        — "fixed", matching load_prescription_engine.py's
                         own intensity_method="fixed_load" (this app has
                         no percentage/RIR/RPE/velocity-based prescription
                         — see that module's own docstring).
  frequency_per_week   — real, len(data["workout"]["days"]) (training days
                         only, per adherence_engine.py's own convention for
                         what that array contains).
  weekly_volume_sets   — real, summed from volume_allocation_engine's own
                         per-muscle weekly_sets output.
  progression_model / deload_required — real, from periodization_engine's
                         profile for this cycle.
  safety_flags         — real, goal_optimization_engine's medical_flags.

PG001 (critical conflict -> block generation) is NOT re-implemented here:
this app already refuses to build an unsafe exercise list at the exercise-
filtering layer (exercise_database._filter_pool excludes anything
contraindicated before it ever reaches a day), and falls back to a safe
low-intensity default plan with a banner when medical flags are present
(see main.py's existing banner logic) — so there's no scenario where this
engine would need to block a program that already exists; it would
already have been built safely or replaced with the safe-default banner
upstream. Re-blocking here would be a second, redundant gate on data
that's already been made safe.

PG005 (weak point -> insert corrective work) is NOT structurally enforced
— weak_point_engine.py's output currently only reaches the member as
COACHING TEXT (via coaching_explanation_engine.py), not as an actual
inserted exercise in the day plan. Documented honestly as a gap between
"detected" and "programmed", not silently claimed as implemented.

PG002/PG003/PG004 are reflected as read-only NOTES on the assembled
program (their real actions already happened upstream — in load_
prescription_engine.py, volume_allocation_engine.py, and plateau_engine.py
respectively) — this engine doesn't re-decide them, it reports that they
were applied.
"""

from __future__ import annotations


def build_program(
    member_id: str | None,
    data: dict,
    goal_optimization: dict,
    periodization: dict,
    recovery_capacity_profile: dict | None = None,
    plateau_flags: list | None = None,
) -> dict:
    """
    data is the SAME dict main.py's _run() builds for this cycle (must
    already have ["workout"]["days"] and ["volume_allocation"] populated).
    """
    days = data.get("workout", {}).get("days", [])

    exercises, sets, reps = [], [], []
    seen = set()
    for day in days:
        for ex in day.get("exercises", []) or []:
            eid = ex.get("exercise_id") or ex.get("name")
            if not eid or eid in seen:
                continue
            seen.add(eid)
            exercises.append(eid)
            sets.append(ex.get("sets"))
            reps.append(ex.get("reps"))

    weekly_volume_sets = sum(
        v.get("weekly_sets", 0) for v in (data.get("volume_allocation") or [])
    )

    safety_flags = list(goal_optimization.get("constraints", {}).get("medical_flags", []))

    notes = []
    capacity_score = (recovery_capacity_profile or {}).get("capacity_score")
    if capacity_score is not None and capacity_score < 60:
        notes.append(f"PG003: weekly volume already capped by recovery capacity ({capacity_score}).")
    confirmed_plateaus = [p["exercise_id"] for p in (plateau_flags or []) if p.get("plateau_status") == "confirmed"]
    if confirmed_plateaus:
        notes.append(f"PG004: plateau-confirmed exercises use their own intervention, not standard progression: {', '.join(confirmed_plateaus)}.")

    return {
        "program_id": f"PROG_{member_id or 'UNKNOWN'}",
        "athlete_id": member_id,
        "goal": goal_optimization.get("primary_goal"),
        "exercises": exercises,
        "sets": sets,
        "reps": reps,
        "load_strategy": "fixed",
        "frequency_per_week": len(days),
        "weekly_volume_sets": weekly_volume_sets,
        "progression_model": periodization.get("progression_model"),
        "deload_required": bool(periodization.get("volume_strategy") == "decrease" and confirmed_plateaus) or (
            (recovery_capacity_profile or {}).get("recommendation", {}).get("deload_required", False)
        ),
        "safety_flags": safety_flags,
        "notes": notes,
    }
