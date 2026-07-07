"""
split_engine.py
──────────────────────────────────────────────────────────────────────────────
STRICT rule-engine built directly from message.txt (the workout-split
reference table, 24 rows — see below). Every branch in _decide() cites the
exact row(s) of message.txt that justify it. No split is invented that isn't
in the table, and no split is offered outside the Experience / Days-per-week
cell it belongs to in the table.

message.txt table (source of truth) — 24 rows, Day-columns preserved:
    1.  Push / Pull                        Beg       2–4   Push, Pull
    2.  Push / Legs / Pull                 Beg       3     Push, Legs, Pull
    3.  Legs / Push / Pull                 Beg       3     Legs, Push, Pull
    4.  Beginner Bodybuilding               Beg       4     Chest+Tri, Back+Bi, Legs, Shoulders+Abs
    5.  Machine-Based Split                 Beg       2–4   UpperMach, LowerMach, (Rest), FullMach, Cardio+Core
    6.  Compound Strength Split             Beg       3–4   Squat, Bench, Deadlift, OHP+Access.
    7.  Strength Foundation Split           Beg       3–4   Push, Pull, Legs, Full Accessory
    8.  Push / Pull / Legs (PPL)            Int–Adv   3–6   Push, Pull, Legs (cycles)
    9.  Torso / Limbs                       Int       4     Torso, Limbs, (Rest), Torso, Limbs, (Rest)
    10. Classic Bodybuilding                Int       5     Chest, Back, Legs, Shoulders, Arms
    11. Powerbuilding                       Int–Adv   4–6   HeavyPush/Pull/Legs, HyperPush/Pull/Legs
    12. Athletic Performance                Int       4–5   LowerStr, UpperStr, Conditioning, Explosive, FullCircuit
    13. Modified Bro Split                  Int–Adv   5–6   Chest, Back, Legs, Shoulders, Arms, WeakPoint
    14. Hybrid PPL + Specialization         Int       5–6   Push, Pull, Legs, Chest+Shoulders, Back+Arms, Legs+Core
    15. PPL x2                              Adv       6     PushH, PullH, LegsH, PushV, PullV, LegsV
    16. Arnold Split                        Adv       6     Chest+Back, Shoulders+Arms, Legs (cycles)
    17. Bro Split                           Adv       5–6   Chest, Back, Legs, Shoulders, Arms, WeakPoint
    18. Body Part Priority                  Adv       5–6   Priority, Legs, Push, Pull, Shoulders, Arms+Core
    19. PHAT                                Adv       5     UpperPower, LowerPower, (Rest), Back+ShoulderHyper, LowerHyper, Chest+ArmsHyper
    20. Advanced Arnold + Power Hybrid       Adv       6     HeavyChest+Back, HeavyLegs, HeavyShoulders, Chest+BackVol, Arms+Shoulders, LegsVol
    21. High Volume Specialization          Adv       5–6   Chest, Back, Legs, Shoulders, Arms, Priority
    22. PPL + Specialization                Adv       6     Push, Pull, Legs, Push, Pull, WeakPoint
    23. Functional Bodybuilding              Adv       4–6   Strength, Hypertrophy, Athletic, Conditioning, WeakPoint, Mobility
    24. Competition Prep                     Adv       6     Chest, Back, Legs, Shoulders, Arms, Conditioning+Abs

NOTE on "Rest" columns: message.txt shows Rest as an explicit column for a
few splits (Push/Pull, Machine-Based, Torso/Limbs, PHAT). Rest-day placement
across the actual 7-weekday calendar is handled separately and deterministically
by fitness_generator._build_weekly_template() (spread evenly, independent of
which split is chosen). So every `pattern`/`sequence` below intentionally
contains ONLY the non-rest training-day tokens, in the table's left-to-right
order — this is consistent with the app's existing design, not a deviation.

SEQUENCE MODES
    "cyclic": a short repeating unit (e.g. ["push", "pull", "legs"]) that is
        tiled out to however many training days/week were requested — this is
        correct for rows whose table cells literally repeat the same cycle as
        the day count grows (PPL: 3→6 repeats push/pull/legs twice; Push/Pull:
        2→4 repeats push/pull twice).
    "fixed": the table gives a DIFFERENT, non-repeating token at each position
        up to some maximum (e.g. PPL+Specialization's day 6 is "Weak Point",
        not a 4th repeat of "Legs"). For these, `pattern` holds the full
        maximum-length sequence exactly as it appears in the table, and a
        request for fewer days/week takes the first N tokens (each split's own
        days/week column in message.txt only ever *adds* a day at the end, so
        trimming from the tail is safe); a request for more days than the
        table defines cycles the full fixed pattern instead of inventing rows.

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
            "mode":         "cyclic" | "fixed",
            "pattern":      ["push", "pull", "legs"],
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
    # -- row 1 (Push/Pull, Beg, 2–4) -----------------------------------------
    "push_pull": {
        "display_name": "Push / Pull",
        "mode": "cyclic",
        "pattern": ["push", "pull"],
    },
    # -- row 2 (Push/Legs/Pull, Beg, 3) ---------------------------------------
    "push_legs_pull": {
        "display_name": "Push / Legs / Pull",
        "mode": "cyclic",
        "pattern": ["push", "legs", "pull"],
    },
    # -- row 3 (Legs/Push/Pull, Beg, 3) -----------------------------------------
    "legs_push_pull": {
        "display_name": "Legs / Push / Pull",
        "mode": "cyclic",
        "pattern": ["legs", "push", "pull"],
    },
    # -- row 4 (Beginner Bodybuilding, Beg, 4) -----------------------------------
    "beginner_bodybuilding": {
        "display_name": "Beginner Bodybuilding Split",
        "mode": "fixed",
        "pattern": ["chest_triceps", "back_biceps", "legs", "shoulders_abs"],
    },
    # -- row 5 (Machine-Based Split, Beg, 2–4) -----------------------------------
    "machine_based": {
        "display_name": "Machine-Based Split",
        "mode": "fixed",
        "pattern": ["upper_machines", "lower_machines", "full_body_machines", "cardio_core"],
    },
    # -- row 6 (Compound Strength Split, Beg, 3–4) -----------------------------------
    "compound_strength": {
        "display_name": "Compound Strength Split",
        "mode": "fixed",
        "pattern": ["squat_focus", "bench_focus", "deadlift_focus", "overhead_press_accessories"],
    },
    # -- row 7 (Strength Foundation Split, Beg, 3–4) -----------------------------------
    "foundation_strength": {
        "display_name": "Strength Foundation Split",
        "mode": "fixed",
        "pattern": ["push", "pull", "legs", "full_accessory"],
    },
    # -- row 8 (PPL, Int–Adv, 3–6) -----------------------------------
    "ppl": {
        "display_name": "Push Pull Legs",
        "mode": "cyclic",
        "pattern": ["push", "pull", "legs"],
    },
    # -- Master_Workout_Split_Table.md §3.2 (Upper/Lower, 4 days) -------------
    # Added for the new master-doc decision tree (see recommend_split_master
    # / programming_rules.SPLIT_DECISION below). Cyclic so it also serves the
    # 2-day "Upper A / Lower A" minimal case if ever requested directly.
    "upper_lower": {
        "display_name": "Upper / Lower",
        "mode": "cyclic",
        "pattern": ["upper", "lower"],
    },
    # -- Master_Workout_Split_Table.md §3.4 (PPL + Upper/Lower Hybrid, 5 days)
    "ppl_upper_lower_hybrid": {
        "display_name": "PPL + Upper/Lower Hybrid",
        "mode": "fixed",
        "pattern": ["push", "pull", "legs", "upper", "lower"],
    },
    # -- row 9 (Torso/Limbs, Int, 4) -----------------------------------
    "torso_limbs": {
        "display_name": "Torso / Limbs",
        "mode": "cyclic",
        "pattern": ["torso", "limbs"],
    },
    # -- row 10 (Classic Bodybuilding, Int, 5) -----------------------------------
    "classic_bodybuilding": {
        "display_name": "Classic Bodybuilding Split",
        "mode": "fixed",
        "pattern": ["chest", "back", "legs", "shoulders", "arms"],
    },
    # -- row 11 (Powerbuilding, Int–Adv, 4–6) -----------------------------------
    "powerbuilding": {
        "display_name": "Powerbuilding Split",
        "mode": "fixed",
        "pattern": ["heavy_push", "heavy_pull", "heavy_legs",
                    "hypertrophy_push", "hypertrophy_pull", "hypertrophy_legs"],
    },
    # -- row 12 (Athletic Performance, Int, 4–5) -----------------------------------
    "athletic_performance": {
        "display_name": "Athletic Performance Split",
        "mode": "fixed",
        "pattern": ["lower_strength", "upper_strength", "conditioning",
                    "explosive_training", "full_athletic_circuit"],
    },
    # -- row 13 (Modified Bro Split, Int–Adv, 5–6) -----------------------------------
    "modified_bro_split": {
        "display_name": "Modified Bro Split",
        "mode": "fixed",
        "pattern": ["chest", "back", "legs", "shoulders", "arms", "weak_point"],
    },
    # -- row 14 (Hybrid PPL + Specialization, Int, 5–6) -----------------------------------
    "hybrid_ppl_specialization": {
        "display_name": "Hybrid PPL + Specialization",
        "mode": "fixed",
        "pattern": ["push", "pull", "legs", "chest_shoulders", "back_arms", "legs_core"],
    },
    # -- row 15 (PPL x2, Adv, 6) -----------------------------------
    "ppl_x2": {
        "display_name": "PPL x2",
        "mode": "fixed",
        "pattern": ["push_heavy", "pull_heavy", "legs_heavy",
                    "push_volume", "pull_volume", "legs_volume"],
    },
    # -- row 16 (Arnold Split, Adv, 6) -----------------------------------
    "arnold": {
        "display_name": "Arnold Split",
        "mode": "cyclic",
        "pattern": ["chest_back", "shoulders_arms", "legs"],
    },
    # -- row 17 (Bro Split, Adv, 5–6) -----------------------------------
    "bro_split": {
        "display_name": "Bro Split",
        "mode": "fixed",
        "pattern": ["chest", "back", "legs", "shoulders", "arms", "weak_point"],
    },
    # -- row 18 (Body Part Priority, Adv, 5–6) -----------------------------------
    "body_part_priority": {
        "display_name": "Body Part Priority Split",
        "mode": "fixed",
        "pattern": ["priority_muscle", "legs", "push", "pull", "shoulders", "arms_core"],
    },
    # -- row 19 (PHAT, Adv, 5) -----------------------------------
    "phat": {
        "display_name": "PHAT (Power Hypertrophy Adaptive Training)",
        "mode": "fixed",
        "pattern": ["upper_power", "lower_power", "back_shoulders_hypertrophy",
                    "lower_hypertrophy", "chest_arms_hypertrophy"],
    },
    # -- row 20 (Advanced Arnold + Power Hybrid, Adv, 6) -----------------------------------
    "advanced_arnold_power_hybrid": {
        "display_name": "Advanced Arnold + Power Hybrid",
        "mode": "fixed",
        "pattern": ["heavy_chest_back", "heavy_legs", "heavy_shoulders",
                    "chest_back_volume", "arms_shoulders", "legs_volume"],
    },
    # -- row 21 (High Volume Specialization, Adv, 5–6) -----------------------------------
    "high_volume_specialization": {
        "display_name": "High Volume Specialization Split",
        "mode": "fixed",
        "pattern": ["chest", "back", "legs", "shoulders", "arms", "priority_muscle"],
    },
    # -- row 22 (PPL + Specialization, Adv, 6) -----------------------------------
    "ppl_specialization": {
        "display_name": "Push / Pull / Legs + Specialization",
        "mode": "fixed",
        "pattern": ["push", "pull", "legs", "push", "pull", "weak_point"],
    },
    # -- row 23 (Functional Bodybuilding, Adv, 4–6) -----------------------------------
    "functional_bodybuilding": {
        "display_name": "Functional Bodybuilding Split",
        "mode": "fixed",
        "pattern": ["strength", "hypertrophy", "athletic", "conditioning", "weak_point", "mobility"],
    },
    # -- row 24 (Competition Prep, Adv, 6) -----------------------------------
    "competition_prep": {
        "display_name": "Competition Prep Split",
        "mode": "fixed",
        "pattern": ["chest", "back", "legs", "shoulders", "arms", "conditioning_abs"],
    },
    # -- NOT in message.txt — used ONLY as the mechanical fallback for a
    # literal 1-day/week schedule, where no "split" is definitionally
    # possible (message.txt's lowest day-count is 2). Kept out of the
    # decision tree for every day-count that message.txt actually covers.
    "full_body": {
        "display_name": "Full Body",
        "mode": "cyclic",
        "pattern": ["full"],
    },
}


def _cycle_split(key: str, days: int) -> dict:
    """
    Expand a split's pattern out to `days` entries and return the standard
    split dict.

    "cyclic" patterns (a short repeating unit) tile indefinitely — correct
    for splits whose table row literally repeats as day-count grows (PPL,
    Push/Pull, Arnold, Torso/Limbs, etc.).

    "fixed" patterns hold the exact maximum-length sequence from message.txt.
    A request for fewer days than the pattern's length takes the first N
    tokens (trimming from the tail, since message.txt's own day-count ranges
    only ever ADD a day at the end — e.g. Bro Split's 5-day version is just
    its 6-day version minus the trailing Weak Point day). A request for MORE
    days than the pattern defines has no basis in the table, so the full
    fixed pattern is cycled instead of inventing new rows.
    """
    lib = SPLIT_LIBRARY[key]
    pattern = lib["pattern"]
    mode = lib.get("mode", "cyclic")
    if mode == "fixed":
        if days <= len(pattern):
            sequence = pattern[:days]
        else:
            sequence = [pattern[i % len(pattern)] for i in range(days)]
    else:
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
        # Row 18 (Body Part Priority Split) is explicitly for "bringing up
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
    # in the table is used: Machine-Based Split (row 5 — beginner-friendly,
    # lowest-intensity entry) for low frequency, or Torso/Limbs (row 9) once
    # frequency climbs, since both spread load thinner than the bodypart-
    # isolation splits.
    if goals["recovery"]:
        if days <= 4:
            return _cycle_split("machine_based", days)
        return _cycle_split("torso_limbs", days)

    # -- BEGINNER ---------------------------------------------------------
    # message.txt Beginner rows only go up to 4 days/week (rows 1-7).
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
            # Cell: rows 1 (Push/Pull, 2-4) and 5 (Machine-Based, 2-4) cover 2.
            if goals["fat_loss"] or goals["confidence_building"]:
                return _cycle_split("machine_based", capped_days)       # Row 5
            return _cycle_split("push_pull", capped_days)                # Row 1

        if capped_days == 3:
            # Cell: rows 1, 2 (Push/Legs/Pull), 3 (Legs/Push/Pull), 5, 6
            # (Compound Strength), 7 (Strength Foundation) all cover 3.
            if goals["confidence_building"]:
                return _cycle_split("machine_based", capped_days)        # Row 5
            if goals["fat_loss"]:
                return _cycle_split("legs_push_pull", capped_days)        # Row 3
            if goals["strength"]:
                # Row 6 is pure barbell-compound strength work; Row 7 is a
                # push/pull/legs split with strength-flavoured accessory —
                # pick the dedicated compound-lift row for a plain strength goal.
                return _cycle_split("compound_strength", capped_days)      # Row 6
            if goals["muscle_gain"] or goals["bodybuilding"]:
                return _cycle_split("push_legs_pull", capped_days)         # Row 2
            return _cycle_split("push_pull", capped_days)                  # Row 1 default

        # capped_days == 4
        # Cell: rows 1, 4 (Beginner Bodybuilding), 5, 6, 7 all cover 4.
        if goals["confidence_building"]:
            return _cycle_split("machine_based", capped_days)             # Row 5
        if goals["fat_loss"]:
            return _cycle_split("beginner_bodybuilding", capped_days)      # Row 4
        if goals["strength"]:
            return _cycle_split("compound_strength", capped_days)          # Row 6
        if goals["muscle_gain"] or goals["bodybuilding"]:
            return _cycle_split("beginner_bodybuilding", capped_days)      # Row 4
        return _cycle_split("push_pull", capped_days)                      # Row 1 default

    # -- INTERMEDIATE --------------------------------------------------------
    if tier == "intermediate":
        if days <= 1:
            return _cycle_split("full_body", days)  # below table range

        if days == 2:
            # No Intermediate cell covers 2 days in message.txt; fall back
            # to the lowest-frequency row that spans down to 2 (Push/Pull is
            # Beginner-only in the new table, so use Torso/Limbs shape at 2).
            return _cycle_split("push_pull", days)

        if days == 3:
            # Cell: only row 8 (PPL, 3-6) covers 3 for Intermediate.
            return _cycle_split("ppl", days)

        if days == 4:
            # Cell: rows 8 (PPL, 3-6), 9 (Torso/Limbs, 4), 11 (Powerbuilding,
            # 4-6) all cover 4. Row 9 is Upper/Lower-style — explicit-request
            # only. Row 11 (Powerbuilding) is the table's dedicated
            # strength-and-size row, so a plain strength goal routes there.
            if goals["strength"]:
                return _cycle_split("powerbuilding", days)      # Row 11
            if goals["upper_lower_preference"]:
                return _cycle_split("torso_limbs", days)          # Row 9 — explicit request only
            return _cycle_split("ppl", days)                       # Row 8 default

        if days == 5:
            # Cell: rows 10 (Classic Bodybuilding, 5), 11 (Powerbuilding,
            # 4-6), 12 (Athletic Performance, 4-5), 13 (Modified Bro
            # Split, 5-6), 14 (Hybrid PPL + Specialization, 5-6) all cover 5.
            if goals["fat_loss"]:
                return _cycle_split("modified_bro_split", days)   # Row 13 (built-in weak-point/varied day)
            if goals["strength"]:
                return _cycle_split("powerbuilding", days)          # Row 11
            if goals["confidence_building"]:
                return _cycle_split("athletic_performance", days)   # Row 12
            if goals["muscle_gain"] or goals["bodybuilding"]:
                return _cycle_split("hybrid_ppl_specialization", days)  # Row 14
            return _cycle_split("classic_bodybuilding", days)        # Row 10 default

        # days >= 6
        # Cell: rows 8 (PPL, up to 6), 11 (Powerbuilding), 13 (Modified Bro
        # Split), 14 (Hybrid PPL + Specialization) all cover 6.
        if goals["strength"]:
            return _cycle_split("powerbuilding", 6)                # Row 11
        if goals["fat_loss"]:
            return _cycle_split("modified_bro_split", 6)             # Row 13
        if goals["muscle_gain"] or goals["bodybuilding"]:
            return _cycle_split("hybrid_ppl_specialization", 6)       # Row 14
        return _cycle_split("ppl", 6)                                  # Row 8 default

    # -- ADVANCED --------------------------------------------------------------
    if tier == "advanced":
        if days <= 2:
            return _cycle_split("full_body", days)  # below table range

        if days == 3:
            # No Advanced cell covers exactly 3 in message.txt (advanced rows
            # start at 4-5); fall back to PPL, the closest lower-frequency
            # base pattern shared with Intermediate.
            return _cycle_split("ppl", days)

        if days == 4:
            # Cell: rows 11 (Powerbuilding, 4-6) and 23 (Functional
            # Bodybuilding, 4-6) cover 4 for Advanced.
            if goals["strength"]:
                return _cycle_split("powerbuilding", days)          # Row 11
            return _cycle_split("functional_bodybuilding", days)      # Row 23 default

        if days == 5:
            # Cell: rows 11 (Powerbuilding), 17 (Bro Split), 18 (Body Part
            # Priority), 19 (PHAT), 21 (High Volume Specialization), 23
            # (Functional Bodybuilding) all cover 5.
            if goals["priority"]:
                return _cycle_split("body_part_priority", days)      # Row 18 — explicit lagging-muscle signal
            if goals["strength"] and goals["muscle_gain"]:
                # Row 19 (PHAT) is the table's dedicated strength+hypertrophy
                # hybrid row, so this specific combo routes there deliberately.
                return _cycle_split("phat", days)
            if goals["strength"]:
                return _cycle_split("powerbuilding", days)            # Row 11
            if goals["muscle_gain"] or goals["bodybuilding"]:
                return _cycle_split("high_volume_specialization", days)  # Row 21
            if goals["confidence_building"]:
                return _cycle_split("functional_bodybuilding", days)   # Row 23
            return _cycle_split("bro_split", days)                       # Row 17 default

        # days >= 6
        # Cell: rows 11 (Powerbuilding), 15 (PPL x2), 16 (Arnold), 17 (Bro
        # Split), 18 (Body Part Priority), 20 (Adv Arnold + Power Hybrid),
        # 21 (High Volume Specialization), 22 (PPL + Specialization), 23
        # (Functional Bodybuilding), 24 (Competition Prep) all cover 6.
        if goals["priority"]:
            return _cycle_split("body_part_priority", 6)               # Row 18
        if goals["strength"] and goals["bodybuilding"]:
            return _cycle_split("advanced_arnold_power_hybrid", 6)       # Row 20
        if goals["strength"]:
            return _cycle_split("powerbuilding", 6)                      # Row 11
        if goals["bodybuilding"] and goals["muscle_gain"]:
            # Both signals firing together -> the table's dedicated
            # "aesthetics + advanced bodybuilding" row.
            return _cycle_split("arnold", 6)                             # Row 16
        if goals["muscle_gain"]:
            return _cycle_split("ppl_specialization", 6)                  # Row 22
        if goals["fat_loss"]:
            return _cycle_split("competition_prep", 6)                    # Row 24 (built-in conditioning day)
        if goals["confidence_building"]:
            return _cycle_split("functional_bodybuilding", 6)              # Row 23
        return _cycle_split("ppl_x2", 6)                                   # Row 15 default

    # Should never hit -- safety net (below/outside table range)
    return _cycle_split("full_body", max(days, 1))


# ── LEGACY ENTRY POINT (24-row message.txt table) ───────────────────────────
# Kept for reference / rollback. The active default is now
# recommend_split_master() below, built from 1_Master_Workout_Split_Table.md.
def _recommend_split_legacy_24row(profile: dict) -> dict:
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


# ── MASTER-DOC ENTRY POINT (1_Master_Workout_Split_Table.md, §2) ───────────
def _decide_master(training_age_yrs: float, days: int, goals: dict) -> dict:
    """
    Implements the decision tree in 1_Master_Workout_Split_Table.md §2
    literally, branch for branch. `days` is the raw requested days/week —
    the master doc's tree does not apply the activity ±1 adjustment used by
    the legacy table, so none is applied here.

    Two hardwired rules from the doc are enforced unconditionally, matching
    its own language ("this is a hardwired rule, not a preference"):
      - never assign Bro Split under 2 years training age (§2 coach note)
      - never program 7 hard days for a natural lifter (§2, days==7 branch)
    """
    if days <= 2:
        return _cycle_split("full_body", max(days, 1))          # §2: Minimalist 2-Day Full Body

    if days == 3:
        if training_age_yrs < 1:
            return _cycle_split("full_body", 3)                  # Full Body A/B/C
        if goals["strength"]:
            return _cycle_split("full_body", 3)                  # "Full Body (if strength-focused)"
        return _cycle_split("ppl", 3)                             # PPL (3-day)

    if days == 4:
        if goals["strength"]:
            return _cycle_split("compound_strength", 4)          # Squat/Bench/Deadlift 4-day
        return _cycle_split("upper_lower", 4)                     # Upper/Lower (hypertrophy default)

    if days == 5:
        if training_age_yrs >= 5 and goals["priority"]:
            return _cycle_split("bro_split", 5)                   # explicit specialization signal only
        return _cycle_split("ppl_upper_lower_hybrid", 5)          # default for <2yr and >=2yr hypertrophy alike

    if days == 6:
        return _cycle_split("ppl_x2", 6)                          # PPL x2 (6-day) — standard int-adv

    # days >= 7 — hardwired: never program 7 hard days for a natural lifter.
    six_day = _cycle_split("ppl_x2", 6)
    return {
        "split_name": six_day["split_name"] + " + Active Recovery Day",
        "sequence": six_day["sequence"] + ["mobility"],
        "_key": six_day["_key"],
    }


def recommend_split_master(profile: dict) -> dict:
    """
    Default split-selection entry point, built directly from
    1_Master_Workout_Split_Table.md §2's decision tree (see _decide_master).
    Signature and return shape match the legacy recommend_split() so callers
    (fitness_generator.py) need no changes beyond the import.
    """
    from . import programming_rules

    raw_days = int(profile.get("days_per_week", 4))
    days = max(1, min(7, raw_days))
    goals = _goal_flags(profile.get("goal", ""))
    training_age_yrs = programming_rules.training_age_years(profile)

    # Recovery goal: doc 1 has no dedicated recovery row; keep the same
    # lowest-fatigue fallback the legacy table used, since it's still the
    # most defensible choice available in this split library.
    if goals["recovery"]:
        final = _cycle_split("machine_based", min(days, 4)) if days <= 4 else _cycle_split("torso_limbs", days)
    else:
        final = _decide_master(training_age_yrs, days, goals)

    goal_desc = profile.get("goal", "general fitness")
    reason = (
        f"~{training_age_yrs:g} yrs training age, {raw_days} days/week, "
        f"{goal_desc} — per Master Workout Split Table §2 decision tree."
    )

    return {
        "split_name": final["split_name"],
        "sequence": final["sequence"],
        "reason": reason,
    }


# recommend_split() is the name every other module imports. It now points at
# the master-doc tree by default; call _recommend_split_legacy_24row directly
# if you need the old table for comparison/testing.
def recommend_split(profile: dict) -> dict:
    return recommend_split_master(profile)