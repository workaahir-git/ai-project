"""
split_engine.py
──────────────────────────────────────────────────────────────────────────────
Replaces the old (experience → template) lookup with a profile-aware
recommend_split() that considers:

    1. experience
    2. days_per_week
    3. session_duration (minutes)
    4. primary goal
    5. BMI (derived from height/weight)
    6. activity level (modifier only — never overrides 1-4)

Public API
──────────
    recommend_split(profile: dict) -> dict
        {
            "split_name": "Push Pull Legs",
            "sequence":   ["push", "pull", "legs"],
            "reason":     "Intermediate, 3 days/week, 75 min sessions, muscle gain"
        }

    SPLIT_LIBRARY[split_key] -> {
            "display_name": "Push Pull Legs",
            "sequence":     ["push", "pull", "legs"],
        }

This module has ZERO dependency on the LLM prompt / schema code — it is pure
decision logic so it can be unit tested in isolation.
"""

from __future__ import annotations
import re


# ── SPLIT LIBRARY ─────────────────────────────────────────────────────────────
# sequence[] uses the same tokens as WARMUP_LIBRARY in fitness_generator.py
# (push / pull / legs / upper / lower / full / cardio) so day-type + warmup
# selection can key off the same vocabulary.
SPLIT_LIBRARY = {
    "full_body": {
        "display_name": "Full Body",
        "sequence": ["full", "full", "full"],
    },
    "upper_lower": {
        "display_name": "Upper Lower",
        "sequence": ["upper", "lower", "upper", "lower"],
    },
    "push_pull": {
        "display_name": "Push Pull",
        "sequence": ["push", "pull"],
    },
    "ppl": {
        "display_name": "Push Pull Legs",
        "sequence": ["push", "pull", "legs"],
    },
    "push_legs_pull": {
        "display_name": "Push / Legs / Pull",
        "sequence": ["push", "legs", "pull"],
    },
    "legs_push_pull": {
        "display_name": "Legs / Push / Pull",
        "sequence": ["legs", "push", "pull"],
    },
    "ppl_upper_lower": {
        "display_name": "Push Pull / Upper / Lower",
        "sequence": ["push", "pull", "upper", "lower", "legs"],
    },
    "torso_limbs": {
        "display_name": "Torso / Limbs",
        "sequence": ["upper", "lower", "upper", "lower"],  # torso=upper, limbs=lower token reuse
    },
    "bro_split": {
        "display_name": "Bro Split",
        "sequence": ["push", "pull", "legs", "upper", "full"],
    },
    "modified_bro_split": {
        "display_name": "Modified Bro Split",
        "sequence": ["push", "pull", "legs", "upper", "cardio"],
    },
    "ppl_x2": {
        "display_name": "PPL x2",
        "sequence": ["push", "pull", "legs", "push", "pull", "legs"],
    },
    "arnold": {
        "display_name": "Arnold Split",
        "sequence": ["push", "pull", "legs", "push", "pull"],
    },
    "phat": {
        "display_name": "PHAT",
        "sequence": ["lower", "upper", "full", "lower", "upper"],
    },
    "powerbuilding": {
        "display_name": "Powerbuilding",
        "sequence": ["lower", "upper", "lower", "upper"],
    },
    "priority": {
        "display_name": "Priority Split",
        "sequence": ["full", "upper", "lower", "full"],
    },
}


def _split(key: str) -> dict:
    lib = SPLIT_LIBRARY[key]
    return {
        "split_name": lib["display_name"],
        "sequence": lib["sequence"],
        "_key": key,
    }


# ── HELPERS ───────────────────────────────────────────────────────────────────
def _experience_tier(raw: str) -> str:
    raw = (raw or "intermediate").lower()
    if raw.startswith("beg"):
        return "beginner"
    if raw.startswith("adv"):
        return "advanced"
    return "intermediate"


def _goal_flags(raw_goal: str) -> dict:
    g = (raw_goal or "").lower()
    return {
        "fat_loss": any(t in g for t in ("fat loss", "weight loss", "cut", "lean")),
        "muscle_gain": any(t in g for t in ("muscle", "bulk", "gain", "mass", "hypertrophy")),
        "strength": any(t in g for t in ("strength", "powerlift", "power lift")),
        "bodybuilding": any(
            t in g for t in ("bodybuilding", "aesthetic", "stage prep", "physique")
        ),
        "maintain": "maintain" in g,
        "recovery": any(
            t in g for t in ("recovery", "recover", "deload", "injury", "rehab", "rehabilitation")
        ),
    }


def _session_minutes(raw_duration) -> int:
    """
    Accepts an int/float number of minutes, or a string like '45-60 min',
    '75 min', '30min'. Returns the midpoint (or the single value) as an int.
    Defaults to 60 if unparsable.
    """
    if isinstance(raw_duration, (int, float)):
        return int(raw_duration)
    if not raw_duration:
        return 60
    nums = [int(n) for n in re.findall(r"\d+", str(raw_duration))]
    if not nums:
        return 60
    return round(sum(nums) / len(nums))


def _bmi(height_cm: float, weight_kg: float) -> float:
    try:
        h_m = float(height_cm) / 100
        if h_m <= 0:
            return 22.0
        return round(float(weight_kg) / (h_m ** 2), 1)
    except (TypeError, ValueError, ZeroDivisionError):
        return 22.0


def _activity_modifier(activity_key: str) -> int:
    return {
        "sedentary": -1,
        "light": 0,
        "moderate": 0,
        "very_active": 1,
        "extreme": 1,
    }.get((activity_key or "moderate").lower(), 0)


# ── CORE DECISION TREE ────────────────────────────────────────────────────────
# Every branch below is keyed off the (experience x days/week) cells that
# actually appear in message.txt. "full_body" is intentionally NOT used as a
# catch-all any more -- a client training 2+ days/week always gets a split
# that's divided across sessions, not the same full-body day repeated.
# full_body is only ever selected for a literal 1-day/week schedule, where no
# split (by definition) is possible.
def _decide(
    tier: str,
    days: int,
    minutes: int,
    goals: dict,
    bmi: float,
) -> dict:
    """
    Pure decision tree per the agreed rules. Returns a _split(key) dict.
    `days` here is the ACTIVITY-ADJUSTED day count (see recommend_split()),
    already clamped to a sane 1-6 range by the caller.
    """

    # -- RECOVERY GOAL -- hard override --------------------------------
    # A recovery/deload/rehab goal changes what's appropriate regardless of
    # tier or day count: minimise per-session fatigue and joint/CNS load by
    # spreading work thin (full body at low frequency, upper/lower once
    # frequency climbs) rather than the high-fatigue splits (Bro Split,
    # Arnold, PHAT, PPL x2) that tier/day count alone would otherwise pick.
    if goals["recovery"]:
        if days <= 3:
            return _split("full_body")
        return _split("upper_lower")

    # -- BEGINNER --------------------------------------------------------
    if tier == "beginner":
        if days <= 1:
            # No split is possible on a single day/week -- full body is the
            # only sensible option, and it's a single day, not "every day".
            return _split("full_body")
        if days in (2, 3):
            # message.txt: Push / Pull -- Beginner-Intermediate, 2-4 days.
            # Cycles as Push, Pull, Push across a 3rd day.
            return _split("push_pull")
        if days == 4:
            return _split("upper_lower")
        # 5+ days -- beginners never get Arnold / Bro Split
        return _split("ppl_upper_lower")

    # -- INTERMEDIATE ------------------------------------------------------
    if tier == "intermediate":
        if days == 2:
            return _split("push_pull")

        if days == 3:
            # message.txt 3-day/Intermediate cells: PPL, Push/Legs/Pull,
            # Legs/Push/Pull. Goal decides ORDER (and therefore which muscle
            # group gets trained fresh first each week).
            if goals["fat_loss"]:
                # Legs first = biggest muscle group = highest calorie cost,
                # trained on fresh CNS/energy.
                return _split("legs_push_pull")
            if goals["muscle_gain"] or goals["bodybuilding"]:
                return _split("push_legs_pull") if minutes >= 60 else _split("push_pull")
            return _split("ppl")

        if days == 4:
            # message.txt 4-day/Intermediate cells: Torso/Limbs, Powerbuilding,
            # PHUL.
            if goals["strength"]:
                return _split("powerbuilding")
            return _split("torso_limbs")

        if days == 5:
            # message.txt 5-day/Intermediate cells: Bro Split, Modified Bro
            # Split (PHAT / Body Part Priority are Advanced-only per table).
            if goals["fat_loss"]:
                # Modified Bro Split carries a built-in cardio day.
                return _split("modified_bro_split")
            if goals["muscle_gain"] or goals["bodybuilding"]:
                return _split("bro_split")
            return _split("modified_bro_split")

        # 6 days -- Intermediate cells: Bro Split, Modified Bro Split,
        # Powerbuilding (PPL x2 / Arnold / Priority are Advanced-only).
        if goals["strength"]:
            return _split("powerbuilding")
        if goals["fat_loss"]:
            return _split("modified_bro_split")
        return _split("bro_split")

    # -- ADVANCED ------------------------------------------------------------
    if tier == "advanced":
        if days == 4:
            # message.txt 4-day/Advanced cells: Torso/Limbs, Powerbuilding.
            return _split("powerbuilding") if goals["strength"] else _split("torso_limbs")

        if days == 5:
            # message.txt 5-day/Advanced cells: Bro Split, Modified Bro
            # Split, PHAT, Body Part Priority, Powerbuilding.
            if goals["fat_loss"]:
                return _split("modified_bro_split")
            # strength + size -> PHAT or Powerbuilding; favour PHAT (hybrid) as
            # the more specific match, Powerbuilding if pure-strength worded.
            return _split("powerbuilding") if goals["strength"] and not goals["muscle_gain"] else _split("phat")

        if days == 6:
            # message.txt 6-day/Advanced cells: PPL x2, Arnold, Bro Split,
            # Modified Bro Split, Powerbuilding, Body Part Priority.
            if goals["fat_loss"]:
                return _split("modified_bro_split")
            if goals["strength"] and not goals["muscle_gain"]:
                return _split("powerbuilding")
            if goals["bodybuilding"] or goals["muscle_gain"]:
                return _split("arnold")
            return _split("ppl_x2")

        # <4 or >6 days for advanced clients -- fall back sensibly, but never
        # collapse to full_body for a multi-day schedule.
        if days <= 1:
            return _split("full_body")
        if days <= 3:
            return _split("ppl")
        return _split("ppl_x2")

    # Should never hit -- safety net
    return _split("full_body")


# ── BMI OVERRIDE LAYER ────────────────────────────────────────────────────────
def _apply_bmi_bias(base: dict, tier: str, goals: dict, bmi: float) -> tuple[dict, str]:
    """
    Returns (possibly-overridden split dict, bmi note string for the reason).
    High BMI (>30) + fat loss  -> bias toward Full Body / Upper Lower (lower
      recovery cost, higher calorie expenditure per session).
    Low BMI (<20) + muscle gain -> bias toward PPL / Bro Split (recovery is
      generally better, can support higher per-muscle frequency/volume).
    This bias only fires for intermediate+ tiers; beginners are already on
    the lowest-fatigue splits by design.
    """
    # Recovery goal already picked the lowest-fatigue split available in
    # _decide() on purpose — don't let the BMI layer override that choice.
    if goals["recovery"]:
        return base, f"BMI {bmi} — no bias applied (recovery goal takes priority)"

    if bmi > 30 and goals["fat_loss"] and tier != "beginner":
        if base["_key"] not in ("full_body", "upper_lower"):
            return _split("upper_lower"), f"BMI {bmi} (>30) biased toward a lower-fatigue split"
        return base, f"BMI {bmi} (>30) — already on a lower-fatigue split"

    if bmi < 20 and goals["muscle_gain"] and tier != "beginner":
        if base["_key"] not in ("ppl", "push_legs_pull", "legs_push_pull", "bro_split", "ppl_x2", "modified_bro_split"):
            return _split("ppl"), f"BMI {bmi} (<20) biased toward a higher-frequency split"
        return base, f"BMI {bmi} (<20) — already on a higher-frequency split"

    return base, f"BMI {bmi} — no bias applied"


# ── PUBLIC ENTRY POINT ────────────────────────────────────────────────────────
def recommend_split(profile: dict) -> dict:
    """
    profile keys used (all optional except experience/days_per_week/goal):
        experience         : "beginner" | "intermediate" | "advanced"
        days_per_week       : int
        session_duration     : int minutes, or string like "45-60 min"
        goal                : free-text, e.g. "muscle gain", "fat loss"
        height_cm, current_weight_kg : for BMI
        activity_key        : "sedentary"|"light"|"moderate"|"very_active"|"extreme"

    Returns:
        {
            "split_name": str,
            "sequence":   [str, ...],
            "reason":     str,
        }
    """
    tier = _experience_tier(profile.get("experience"))
    raw_days = int(profile.get("days_per_week", 4))
    minutes = _session_minutes(profile.get("session_duration"))
    goals = _goal_flags(profile.get("goal", ""))
    bmi = _bmi(profile.get("height_cm", 170), profile.get("current_weight_kg", 70))

    # Activity level is a MODIFIER ONLY. It nudges the effective day-count
    # used for split *shape* selection by ±1, but never overrides experience,
    # goal, or the originally-requested day count for scheduling purposes.
    modifier = _activity_modifier(profile.get("activity_key", "moderate"))
    effective_days = max(1, min(6, raw_days + modifier))

    base = _decide(tier, effective_days, minutes, goals, bmi)
    final, bmi_note = _apply_bmi_bias(base, tier, goals, bmi)

    goal_desc = profile.get("goal", "general fitness")
    recovery_note = " Recovery goal detected — lowest-fatigue split forced regardless of tier/days." if goals["recovery"] else ""
    reason = (
        f"{tier.capitalize()}, {raw_days} days/week"
        f"{f' (activity-adjusted to {effective_days})' if modifier else ''}, "
        f"{minutes} min sessions, {goal_desc}. {bmi_note}.{recovery_note}"
    )

    return {
        "split_name": final["split_name"],
        "sequence": final["sequence"],
        "reason": reason,
    }
