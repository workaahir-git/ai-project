"""
split_engine.py
──────────────────────────────────────────────────────────────────────────────
STRICT rule-engine built directly from message.txt (the workout-split
reference table). Every branch in _decide() cites the exact row(s) of
message.txt that justify it. No split is invented that isn't in the table,
and no split is offered outside the Experience / Days-per-week cell it
belongs to in the table.

message.txt table (source of truth) — 17 rows:
    1.  Push / Pull / Legs (PPL)                     Int–Adv   3–6
    2.  PPL x2                                        Adv       6
    3.  Bro Split                                      Int–Adv  5–6
    4.  Arnold Split                                    Adv      6
    5.  Push / Pull                                     Beg–Int  2–4
    6.  Push / Legs / Pull                              Beg–Int  3
    7.  Legs / Push / Pull                              Beg–Int  3
    8.  Beginner Bodybuilding Split                     Beg      4
    9.  Foundation Strength Split                       Beg      3–4
    10. Machine-Based Split                             Beg      2–4
    11. Torso / Limbs                                   Int–Adv  4
    12. Body Part Priority Split                        Adv      5–6
    13. PHAT                                            Adv      5
    14. Powerbuilding Split                             Int–Adv  4–6
    15. PHUL                                            Int      4
    16. Modified Bro Split                              Int–Adv  5–6
    17. Advanced Arnold + Power Hybrid                   Adv      6

Public API (unchanged so fitness_generator.py needs no changes)
──────────
    recommend_split(profile: dict) -> dict
        {
            "split_name": "Push Pull Legs",
            "sequence":   ["push", "pull", "legs"],
            "reason":     "Intermediate, 3 days/week, 75 min sessions, muscle gain"
        }

    SPLIT_LIBRARY[split_key] -> {
            "display_name": "Push Pull Legs",
            "pattern":      ["push", "pull", "legs"],   # repeating cycle
        }

This module has ZERO dependency on the LLM prompt / schema code — it is pure
decision logic so it can be unit tested in isolation.
"""

from __future__ import annotations
import re


# ── SPLIT LIBRARY ─────────────────────────────────────────────────────────────
# Each entry stores a "pattern" — the repeating day-type cycle for that split —
# rather than a fixed-length sequence. The pattern is cycled out to the actual
# days/week requested (see _cycle_split()) so a single library entry can
# correctly serve every day-count listed for it in message.txt (e.g. Push/Pull
# covers 2, 3, AND 4 days/week — all valid per row 5).
#
# Tokens used ("push"/"pull"/"legs"/"upper"/"lower"/"full"/"cardio") match
# WARMUP_LIBRARY in fitness_generator.py so day-type + warmup selection can
# key off the same vocabulary.
SPLIT_LIBRARY = {
    # -- row 1 --------------------------------------------------------------
    "ppl": {
        "display_name": "Push Pull Legs",
        "pattern": ["push", "pull", "legs"],
    },
    # -- row 2 (PPL cycled to a fixed 6 days — "PPL x2") ---------------------
    "ppl_x2": {
        "display_name": "PPL x2",
        "pattern": ["push", "pull", "legs"],
    },
    # -- row 3 ----------------------------------------------------------------
    "bro_split": {
        "display_name": "Bro Split",
        "pattern": ["push", "pull", "legs", "upper", "full"],
    },
    # -- row 4 ------------------------------------------------------------------
    "arnold": {
        "display_name": "Arnold Split",
        "pattern": ["push", "pull", "legs", "upper", "lower"],
    },
    # -- row 5 --------------------------------------------------------------------
    "push_pull": {
        "display_name": "Push Pull",
        "pattern": ["push", "pull"],
    },
    # -- row 6 ----------------------------------------------------------------------
    "push_legs_pull": {
        "display_name": "Push / Legs / Pull",
        "pattern": ["push", "legs", "pull"],
    },
    # -- row 7 ------------------------------------------------------------------------
    "legs_push_pull": {
        "display_name": "Legs / Push / Pull",
        "pattern": ["legs", "push", "pull"],
    },
    # -- row 8 --------------------------------------------------------------------------
    "beginner_bodybuilding": {
        "display_name": "Beginner Bodybuilding Split",
        "pattern": ["upper", "lower", "push", "pull"],
    },
    # -- row 9 ----------------------------------------------------------------------------
    "foundation_strength": {
        "display_name": "Foundation Strength Split",
        "pattern": ["full", "full", "full"],
    },
    # -- row 10 -----------------------------------------------------------------------------
    "machine_based": {
        "display_name": "Machine-Based Split",
        "pattern": ["full", "full"],
    },
    # -- row 11 -------------------------------------------------------------------------------
    "torso_limbs": {
        "display_name": "Torso / Limbs",
        "pattern": ["upper", "lower"],
    },
    # -- row 12 ---------------------------------------------------------------------------------
    "body_part_priority": {
        "display_name": "Body Part Priority Split",
        "pattern": ["push", "pull", "legs", "upper", "lower"],
    },
    # -- row 13 -----------------------------------------------------------------------------------
    "phat": {
        "display_name": "PHAT",
        "pattern": ["lower", "upper", "full", "lower", "upper"],
    },
    # -- row 14 -------------------------------------------------------------------------------------
    "powerbuilding": {
        "display_name": "Powerbuilding Split",
        "pattern": ["lower", "upper"],
    },
    # -- row 15 ---------------------------------------------------------------------------------------
    "phul": {
        "display_name": "PHUL (Power Hypertrophy Upper Lower)",
        "pattern": ["upper", "lower", "upper", "lower"],
    },
    # -- row 16 -----------------------------------------------------------------------------------------
    "modified_bro_split": {
        "display_name": "Modified Bro Split",
        "pattern": ["push", "pull", "legs", "upper", "cardio"],
    },
    # -- row 17 -------------------------------------------------------------------------------------------
    "advanced_arnold_power_hybrid": {
        "display_name": "Advanced Arnold + Power Hybrid",
        "pattern": ["push", "pull", "legs", "upper", "lower", "full"],
    },
    # -- NOT in message.txt — used ONLY as the mechanical fallback for a
    # literal 1-day/week schedule, where no "split" is definitionally
    # possible (message.txt's lowest day-count is 2). Kept out of the
    # decision tree for every day-count that message.txt actually covers.
    "full_body": {
        "display_name": "Full Body",
        "pattern": ["full"],
    },
}


def _cycle_split(key: str, days: int) -> dict:
    """Cycle a split's pattern out to `days` entries and return the
    standard split dict."""
    lib = SPLIT_LIBRARY[key]
    pattern = lib["pattern"]
    sequence = [pattern[i % len(pattern)] for i in range(days)]
    return {
        "split_name": lib["display_name"],
        "sequence": sequence,
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
        "strength": any(t in g for t in ("strength", "powerlift", "power lift", "power")),
        "bodybuilding": any(
            t in g for t in ("bodybuilding", "aesthetic", "stage prep", "physique")
        ),
        "maintain": "maintain" in g,
        "recovery": any(
            t in g for t in ("recovery", "recover", "deload", "injury", "rehab", "rehabilitation")
        ),
        # Row 12 (Body Part Priority Split) is explicitly for "bringing up
        # lagging muscle groups" — only route there on an explicit signal,
        # never as a generic advanced default.
        "priority": any(
            t in g for t in ("lagging", "priority", "weak point", "weak-point", "bring up")
        ),
        "confidence_building": any(
            t in g for t in ("confidence", "new to the gym", "new gym", "beginner friendly")
        ),
        # Explicit opt-in for Upper/Lower-style rows (Torso/Limbs, etc.) —
        # these are no longer picked as a silent default; a client must ask.
        "upper_lower_preference": any(
            t in g for t in ("upper lower", "upper/lower", "upper-lower", "torso limbs", "torso/limbs")
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
# Every branch below is keyed strictly off the (experience x days/week) cells
# that actually appear in message.txt. Where a cell contains more than one
# eligible split, the client's stated `goal` (never anything outside
# message.txt) is used as the tie-breaker, using each row's own "Best For"
# description as the justification (cited inline).
def _decide(
    tier: str,
    days: int,
    minutes: int,
    goals: dict,
) -> dict:
    """
    Pure decision tree per message.txt. Returns a _cycle_split(key, days) dict.
    `days` here is the ACTIVITY-ADJUSTED day count (see recommend_split()),
    already clamped to a sane 1-6 range by the caller.
    """

    # -- RECOVERY GOAL -- hard override --------------------------------
    # message.txt has no dedicated "recovery/deload/rehab" row. In the
    # ABSENCE of a matching row, the lowest-fatigue option actually present
    # in the table is used: Machine-Based Split (row 10 — "new gym members,
    # confidence building, fat loss", i.e. the lowest-intensity entry) for
    # low frequency, or Torso/Limbs (row 11) once frequency climbs, since
    # both spread load thinner than the bodypart-isolation splits.
    if goals["recovery"]:
        if days <= 4:
            return _cycle_split("machine_based", days)
        return _cycle_split("torso_limbs", days)

    # -- BEGINNER ---------------------------------------------------------
    # message.txt Beginner rows only go up to 4 days/week (rows 5, 8, 9, 10).
    # There is no beginner cell for 5-6 days — per the table, a beginner
    # profile is capped at the 4-day decision, not extrapolated.
    if tier == "beginner":
        if days <= 1:
            # No row in message.txt goes below 2 days/week -- a single-day
            # schedule can't be "split" by definition, so this is the one
            # mechanical fallback outside the table.
            return _cycle_split("full_body", days)

        capped_days = min(days, 4)

        if capped_days == 2:
            # Cell: rows 5 (Push/Pull) and 10 (Machine-Based) both cover 2.
            if goals["fat_loss"] or goals["confidence_building"]:
                # Row 10: "New gym members, confidence building, fat loss"
                return _cycle_split("machine_based", capped_days)
            return _cycle_split("push_pull", capped_days)  # Row 5

        if capped_days == 3:
            # Cell: rows 5, 6, 7, 9, 10 all cover 3 for Beginner.
            # NOTE: fat_loss no longer routes to machine_based (fullbody) here.
            # Fullbody-every-day is reserved for the <=2-days-available case
            # (capped_days == 2, above). At 3+ days, fat-loss beginners get a
            # real split with movement variety instead.
            if goals["confidence_building"]:
                return _cycle_split("machine_based", capped_days)      # Row 10
            if goals["fat_loss"]:
                return _cycle_split("legs_push_pull", capped_days)      # Row 7
            if goals["strength"]:
                return _cycle_split("foundation_strength", capped_days)  # Row 9
            if goals["muscle_gain"] or goals["bodybuilding"]:
                return _cycle_split("push_legs_pull", capped_days)      # Row 6
            return _cycle_split("push_pull", capped_days)               # Row 5 default

        # capped_days == 4
        # Cell: rows 5, 8, 9, 10 all cover 4 for Beginner.
        # Same reasoning as capped_days==3: fat_loss should get a varied
        # split at 4 days/week, not fullbody. Only confidence_building (not
        # tied to a day-count) still gets the gentler machine_based split.
        if goals["confidence_building"]:
            return _cycle_split("machine_based", capped_days)          # Row 10
        if goals["fat_loss"]:
            return _cycle_split("beginner_bodybuilding", capped_days)   # Row 8
        if goals["strength"]:
            return _cycle_split("foundation_strength", capped_days)     # Row 9
        if goals["muscle_gain"] or goals["bodybuilding"]:
            return _cycle_split("beginner_bodybuilding", capped_days)   # Row 8
        return _cycle_split("push_pull", capped_days)                   # Row 5 default

    # -- INTERMEDIATE --------------------------------------------------------
    if tier == "intermediate":
        if days <= 1:
            return _cycle_split("full_body", days)  # below table range

        if days == 2:
            # Cell: only row 5 (Push/Pull, 2-4) covers 2 for Intermediate.
            return _cycle_split("push_pull", days)

        if days == 3:
            # Cell: rows 1 (PPL, 3-6), 5, 6, 7 all cover 3 for Intermediate.
            if goals["fat_loss"]:
                return _cycle_split("legs_push_pull", days)   # Row 7
            if goals["muscle_gain"] or goals["bodybuilding"]:
                return _cycle_split("push_legs_pull", days) if minutes >= 60 else _cycle_split("push_pull", days)  # Row 6 / Row 5
            return _cycle_split("ppl", days)                   # Row 1 default

        if days == 4:
            # Cell: rows 1 (PPL, 3-6), 11 (Torso/Limbs, 4), 14 (Powerbuilding,
            # 4-6), 15 (PHUL — Intermediate only, 4) all cover 4.
            # Rows 11/15 are Upper/Lower-style splits — only pick them on an
            # explicit signal (strength / an explicit upper-lower preference).
            # Row 14 (Powerbuilding) is likewise upper/lower in structure but
            # is the table's dedicated "strength and size" row, so a plain
            # strength goal still routes there deliberately, not by default.
            if goals["strength"] and goals["muscle_gain"]:
                return _cycle_split("phul", days)          # Row 15: strength AND hypertrophy
            if goals["strength"]:
                return _cycle_split("powerbuilding", days)  # Row 14
            if goals["upper_lower_preference"]:
                return _cycle_split("torso_limbs", days)     # Row 11 — explicit request only
            return _cycle_split("ppl", days)                  # Row 1 default (non upper/lower)

        if days == 5:
            # Cell: rows 3 (Bro Split, 5-6) and 16 (Modified Bro Split, 5-6) cover 5 for Intermediate.
            # (Rows 12/13 — Body Part Priority / PHAT — are Advanced-only.)
            if goals["fat_loss"]:
                return _cycle_split("modified_bro_split", days)  # Row 16 (built-in cardio day)
            return _cycle_split("bro_split", days)                # Row 3 default

        # days >= 6
        # Cell: rows 1 (PPL, up to 6), 3 (Bro Split), 14 (Powerbuilding), 16
        # (Modified Bro Split) all cover 6 for Intermediate.
        # (Rows 2/4/12/17 — PPL x2 / Arnold / Priority / Adv-Arnold-Hybrid — are Advanced-only.)
        if goals["strength"]:
            return _cycle_split("powerbuilding", 6)         # Row 14
        if goals["fat_loss"]:
            return _cycle_split("modified_bro_split", 6)     # Row 16
        if goals["muscle_gain"] or goals["bodybuilding"]:
            return _cycle_split("bro_split", 6)               # Row 3
        return _cycle_split("ppl", 6)                          # Row 1 default

    # -- ADVANCED --------------------------------------------------------------
    if tier == "advanced":
        if days <= 2:
            return _cycle_split("full_body", days)  # below table range

        if days == 3:
            # Cell: only row 1 (PPL, 3-6) covers 3 for Advanced.
            return _cycle_split("ppl", days)

        if days == 4:
            # Cell: rows 1 (PPL, 3-6), 11 (Torso/Limbs, 4), 14 (Powerbuilding,
            # 4-6) cover 4. Row 11 is Upper/Lower-style — explicit-request only.
            if goals["strength"]:
                return _cycle_split("powerbuilding", days)      # Row 14
            if goals["upper_lower_preference"]:
                return _cycle_split("torso_limbs", days)         # Row 11 — explicit request only
            return _cycle_split("ppl", days)                      # Row 1 default (non upper/lower)

        if days == 5:
            # Cell: rows 3 (Bro Split), 12 (Body Part Priority), 13 (PHAT),
            # 14 (Powerbuilding), 16 (Modified Bro Split) all cover 5.
            if goals["fat_loss"]:
                return _cycle_split("modified_bro_split", days)  # Row 16
            if goals["priority"]:
                return _cycle_split("body_part_priority", days)   # Row 12 — explicit lagging-muscle signal
            if goals["strength"] and not goals["muscle_gain"]:
                return _cycle_split("powerbuilding", days)         # Row 14
            if goals["strength"] and goals["muscle_gain"]:
                # Row 13 (PHAT) is the table's dedicated strength+hypertrophy
                # hybrid row, so this specific combo still routes there
                # deliberately — but it's no longer the generic fallback.
                return _cycle_split("phat", days)
            return _cycle_split("bro_split", days)                   # Row 3 default (non upper/lower)

        # days >= 6
        # Cell: rows 1 (PPL), 2 (PPL x2), 3 (Bro Split), 4 (Arnold), 12
        # (Body Part Priority), 14 (Powerbuilding), 16 (Modified Bro Split),
        # 17 (Advanced Arnold + Power Hybrid) all cover 6 for Advanced.
        if goals["fat_loss"]:
            return _cycle_split("modified_bro_split", 6)             # Row 16
        if goals["priority"]:
            return _cycle_split("body_part_priority", 6)              # Row 12
        if goals["strength"] and goals["bodybuilding"]:
            return _cycle_split("advanced_arnold_power_hybrid", 6)     # Row 17
        if goals["strength"] and not goals["muscle_gain"]:
            return _cycle_split("powerbuilding", 6)                     # Row 14
        if goals["bodybuilding"] or goals["muscle_gain"]:
            return _cycle_split("arnold", 6)                            # Row 4
        return _cycle_split("ppl_x2", 6)                                 # Row 2 default

    # Should never hit -- safety net (below/outside table range)
    return _cycle_split("full_body", max(days, 1))


# ── PUBLIC ENTRY POINT ────────────────────────────────────────────────────────
def recommend_split(profile: dict) -> dict:
    """
    profile keys used (all optional except experience/days_per_week/goal):
        experience         : "beginner" | "intermediate" | "advanced"
        days_per_week       : int
        session_duration     : int minutes, or string like "45-60 min"
        goal                : free-text, e.g. "muscle gain", "fat loss"
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

    # Activity level is a MODIFIER ONLY. It nudges the effective day-count
    # used for split *shape* selection by ±1, but never overrides experience,
    # goal, or the originally-requested day count for scheduling purposes.
    modifier = _activity_modifier(profile.get("activity_key", "moderate"))
    effective_days = max(1, min(6, raw_days + modifier))

    final = _decide(tier, effective_days, minutes, goals)

    goal_desc = profile.get("goal", "general fitness")
    recovery_note = (
        " Recovery goal detected — message.txt has no recovery row, so the "
        "lowest-fatigue table entry (Machine-Based / Torso-Limbs) is used instead."
        if goals["recovery"] else ""
    )
    reason = (
        f"{tier.capitalize()}, {raw_days} days/week"
        f"{f' (activity-adjusted to {effective_days})' if modifier else ''}, "
        f"{minutes} min sessions, {goal_desc}.{recovery_note}"
    )

    return {
        "split_name": final["split_name"],
        "sequence": final["sequence"],
        "reason": reason,
    }