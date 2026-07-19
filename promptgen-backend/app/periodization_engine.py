"""
periodization_engine.py — Engine 17 (Periodization).

check_deload_recommended() below is the ORIGINAL scope: PD004 only, via a
performance-based trigger (see its own docstring — unchanged).

build_periodization_profile() is the Phase 3 addition, now that Engines 9
(Readiness), 10 (Recovery Capacity), 15 (Adherence), and 11 (Plateau) all
exist. Full spec (KB engines["17"].spec_text) wants macrocycle_weeks /
mesocycle_weeks tracked in real calendar weeks. This app doesn't track
calendar weeks directly, but main.py's plan save DOES hardcode
`valid_until = now + 14 days` for every generated plan — so 1 cycle_number
increment IS a real, fixed 2-week block. All week-based fields below are
computed as (cycles * 2), not invented.

progression_model / volume_strategy / intensity_strategy defaults per goal
are standard periodization conventions (linear for strength/power,
double_progression for hypertrophy/general_fitness, undulating for
endurance/fat_loss) — same "sourced convention, not this app's invention"
labeling volume_allocation_engine.py already uses for its landmarks, not a
per-athlete measurement.

Rules implemented for real:
  PD001 (Recovery Capacity low)  -> reduce volume_strategy
  PD002 (Plateau confirmed)      -> change mesocycle strategy (flip to "wave")
  PD005 (Adherence poor)         -> force progression_model to "linear"
                                     (simplest model to actually follow)
PD003 (readiness consistently high -> permit progression) is NOT
implemented — this app has no stored HISTORY of readiness across cycles
(readiness_checkins is per-session, not aggregated over time anywhere),
so "consistently high" can't be verified without guessing. Left unset
rather than approximated from one session's reading.

Never raises. Missing inputs fall back to the goal's default profile with
no rule adjustments applied.
"""

from __future__ import annotations

# Trigger thresholds — deliberately conservative (require a clear signal,
# not a borderline one) since acting on this means telling someone to
# scale back a week of training.
MIN_MUSCLES_ABOVE_MRV = 2
MIN_DELOAD_RATIO = 0.40  # fraction of feedback-having exercises reading "deload_or_hold"


def _count_deload_signals(days: list) -> tuple:
    """Returns (deload_count, total_with_feedback) across all exercises in
    the week that actually had a progression signal (baseline excluded —
    no data means no opinion, not "counts as fine")."""
    deload_count = 0
    total_with_signal = 0
    for day in days:
        if day.get("is_rest"):
            continue
        for ex in day.get("exercises", []):
            action = ex.get("_progression_action", "baseline")
            if action == "baseline":
                continue
            total_with_signal += 1
            if action == "deload_or_hold":
                deload_count += 1
    return deload_count, total_with_signal


def check_deload_recommended(days: list, volume_allocation: list) -> dict:
    """
    Returns:
      {"deload_recommended": bool, "reasons": [str, ...],
       "muscles_above_mrv": [str, ...], "deload_signal_ratio": float | None}

    Never raises. Both inputs are things fitness_generator.py has already
    computed for this same plan — no extra Supabase calls, pure function.
    """
    reasons = []

    muscles_above_mrv = [
        v["muscle_group"] for v in (volume_allocation or [])
        if v.get("allocation_status") == "above_mrv"
    ]
    if len(muscles_above_mrv) >= MIN_MUSCLES_ABOVE_MRV:
        reasons.append(
            f"{len(muscles_above_mrv)} muscle groups ({', '.join(muscles_above_mrv)}) are "
            f"programmed above their recoverable weekly volume this week."
        )

    deload_count, total_with_signal = _count_deload_signals(days)
    ratio = (deload_count / total_with_signal) if total_with_signal else None
    if ratio is not None and ratio >= MIN_DELOAD_RATIO and total_with_signal >= 3:
        # Require at least 3 exercises with real feedback so one bad day
        # doesn't trigger this off a tiny sample.
        reasons.append(
            f"{deload_count} of {total_with_signal} exercises with logged feedback ({ratio:.0%}) "
            f"came back rated hard last cycle."
        )

    return {
        "deload_recommended": bool(reasons),
        "reasons": reasons,
        "muscles_above_mrv": muscles_above_mrv,
        "deload_signal_ratio": ratio,
    }


# Standard periodization conventions per goal — sourced practice, not a
# per-athlete measurement (same disclaimer style as volume_allocation_
# engine.py's landmarks). Values are in CYCLES; converted to weeks via
# CYCLE_WEEKS below since that's the only real duration this app tracks.
CYCLE_WEEKS = 2  # from main.py's valid_until = now + 14 days, hardcoded per plan

GOAL_PERIODIZATION_DEFAULTS = {
    "strength":       {"macrocycle_cycles": 12, "mesocycle_cycles": 3, "progression_model": "linear",           "volume_strategy": "maintain", "intensity_strategy": "increase"},
    "power":          {"macrocycle_cycles": 12, "mesocycle_cycles": 3, "progression_model": "linear",           "volume_strategy": "maintain", "intensity_strategy": "increase"},
    "hypertrophy":    {"macrocycle_cycles": 12, "mesocycle_cycles": 3, "progression_model": "double_progression","volume_strategy": "increase", "intensity_strategy": "maintain"},
    "general_fitness":{"macrocycle_cycles": 12, "mesocycle_cycles": 3, "progression_model": "double_progression","volume_strategy": "maintain", "intensity_strategy": "maintain"},
    "endurance":      {"macrocycle_cycles": 12, "mesocycle_cycles": 3, "progression_model": "undulating",       "volume_strategy": "increase", "intensity_strategy": "wave"},
    "fat_loss":       {"macrocycle_cycles": 12, "mesocycle_cycles": 3, "progression_model": "undulating",       "volume_strategy": "increase", "intensity_strategy": "wave"},
}
DELOAD_FREQUENCY_CYCLES = 3  # every mesocycle, before rolling into the next one


def build_periodization_profile(
    member_id: str | None,
    primary_goal: str,
    recovery_capacity_profile: dict | None = None,
    adherence_profile: dict | None = None,
    plateau_confirmed: bool = False,
) -> dict:
    """
    primary_goal should be goal_optimization_engine's canonical enum
    (strength|hypertrophy|power|endurance|fat_loss|general_fitness) — pass
    the SAME value already computed for this member/cycle so the two
    engines never disagree on what the goal is.
    """
    defaults = GOAL_PERIODIZATION_DEFAULTS.get(primary_goal, GOAL_PERIODIZATION_DEFAULTS["general_fitness"])
    progression_model = defaults["progression_model"]
    volume_strategy = defaults["volume_strategy"]
    intensity_strategy = defaults["intensity_strategy"]
    reasons = []

    # PD001 — recovery capacity low reduces planned volume.
    capacity_score = (recovery_capacity_profile or {}).get("capacity_score")
    if capacity_score is not None and capacity_score < 60:
        volume_strategy = "decrease"
        reasons.append(f"Recovery capacity is low ({capacity_score}) — volume strategy reduced.")

    # PD002 — confirmed plateau changes mesocycle strategy.
    if plateau_confirmed:
        intensity_strategy = "wave"
        reasons.append("Plateau confirmed — switching to a wave intensity strategy to break through it.")

    # PD005 — poor adherence simplifies the progression model.
    adherence_score = (adherence_profile or {}).get("adherence_score")
    if adherence_score is not None and adherence_score < 60:
        progression_model = "linear"
        reasons.append(f"Adherence is low ({adherence_score}) — simplified to a linear progression model.")

    macrocycle_weeks = defaults["macrocycle_cycles"] * CYCLE_WEEKS
    mesocycle_weeks = defaults["mesocycle_cycles"] * CYCLE_WEEKS
    deload_frequency_weeks = DELOAD_FREQUENCY_CYCLES * CYCLE_WEEKS

    return {
        "periodization_profile_id": f"PER_{member_id or 'UNKNOWN'}",
        "goal": primary_goal,
        "macrocycle_weeks": macrocycle_weeks,
        "mesocycle_weeks": mesocycle_weeks,
        "microcycle_days": 7,  # this app's weekly training-day structure — real, not a guess
        "progression_model": progression_model,
        "deload_frequency_weeks": deload_frequency_weeks,
        "volume_strategy": volume_strategy,
        "intensity_strategy": intensity_strategy,
        "reasons": reasons,
    }
