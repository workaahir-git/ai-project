"""
programming_rules.py
──────────────────────────────────────────────────────────────────────────────
Single source of truth for the numeric coaching rules that used to be
guessed at, hard-coded per-tier, or left to the LLM. Every table in this
file is transcribed directly from the uploaded coaching methodology docs:

    1_Master_Workout_Split_Table.md  -> SPLIT_DECISION (§2 decision tree),
                                         SESSION_DURATION_CAP (§4)
    2_Programming_Rules.md           -> SETS_REPS_BY_GOAL (§1),
                                         RIR_BY_TRAINING_AGE (§2)
    8_Weekly_Muscle_Volume.md        -> MUSCLE_VOLUME_MEV_MAV (§2),
                                         goal volume bias (§4)

WHY A SEPARATE MODULE
    split_engine.py and fitness_generator.py both need these numbers, and
    previously each had its own slightly-different guess (a flat
    "isolation_count" per experience tier, a 24-row split table from an
    older reference doc). Centralizing them here means both modules now
    cite the same paragraph of the same doc instead of silently drifting
    apart, and the next doc update only has to change one file.

WHAT THIS FILE DOES NOT DO
    It does not pick exercises (exercise_database.py), does not build the
    weekly day sequence (split_engine.py), and does not write prompt/JSON
    (fitness_generator.py). It only answers "how many sets/reps/RIR/rest,
    how much weekly volume, and which split" for a given input.
"""

from __future__ import annotations
import math


# ── 1. WEEKLY VOLUME TABLE (doc 8, §2 — MEV/MAV, direct volume) ─────────────
# Every row below is the (MEV, MAV) pair from the Master Volume Table for the
# given training age. "MRV ceiling" from the doc is intentionally not encoded
# as a target — the doc is explicit that MRV is only approached temporarily
# in specialization blocks, never used as a default programming target.
#
# App muscle keys map onto doc rows as follows (documented so a future editor
# doesn't have to re-derive the mapping):
#   legs      -> "Quads" row       (hamstrings/glutes aren't tracked separately
#                                    in this app's muscle model)
#   back      -> "Back (total)" row
#   traps     -> "Upper Back/Traps" row
#   shoulders -> "Shoulders (direct)" row
#   core      -> "Abs" row
MUSCLE_VOLUME_MEV_MAV = {
    "chest":     {"beginner": (6, 10),  "intermediate": (10, 18), "advanced": (12, 22)},
    "back":      {"beginner": (8, 12),  "intermediate": (12, 20), "advanced": (14, 25)},
    "traps":     {"beginner": (4, 8),   "intermediate": (6, 12),  "advanced": (8, 16)},
    "shoulders": {"beginner": (4, 8),   "intermediate": (8, 16),  "advanced": (10, 20)},
    "biceps":    {"beginner": (4, 8),   "intermediate": (8, 14),  "advanced": (10, 20)},
    "triceps":   {"beginner": (4, 8),   "intermediate": (8, 14),  "advanced": (10, 20)},
    "legs":      {"beginner": (6, 10),  "intermediate": (10, 18), "advanced": (12, 22)},
    "calves":    {"beginner": (4, 8),   "intermediate": (8, 16),  "advanced": (10, 20)},
    "core":      {"beginner": (4, 8),   "intermediate": (6, 12),  "advanced": (8, 16)},
}


def _goal_key(raw_goal: str) -> str:
    g = (raw_goal or "").lower()
    if any(t in g for t in ("fat loss", "weight loss", "cut", "lean")):
        return "fat_loss"
    if any(t in g for t in ("strength", "powerlift", "power lift")):
        return "strength"
    if any(t in g for t in ("general fitness", "general health", "wellness")):
        return "general_fitness"
    if any(t in g for t in ("athletic", "performance", "sport")):
        return "athletic"
    return "muscle_gain"  # default: bodybuilding/hypertrophy/muscle gain


def weekly_volume_target(muscle: str, training_age: str, goal: str = "") -> int:
    """
    Returns a single target weekly SET count for `muscle`, per doc 8 §2
    (base MEV/MAV) modified by doc 8 §4 / doc 7 §1 (goal-based volume bias):
      - muscle_gain / bodybuilding : aim MAV (standard table)
      - strength                  : reduced accessory/isolation volume
                                     (~30-40% cut from MAV, per doc 8 §4)
      - fat_loss                  : maintain near-current MEV-MAV midpoint,
                                     not a deliberate increase (doc 8 §4)
      - general_fitness           : MEV is sufficient, no need to chase MAV
      - athletic                  : reduced isolation volume vs MAV
    """
    training_age = training_age if training_age in ("beginner", "intermediate", "advanced") else "intermediate"
    mev, mav = MUSCLE_VOLUME_MEV_MAV.get(muscle, {}).get(training_age, (0, 0))
    if mev == 0 and mav == 0:
        return 0

    goal_key = _goal_key(goal)
    if goal_key == "general_fitness":
        return mev
    if goal_key == "strength":
        return round(mav * 0.65)          # ~30-40% below MAV
    if goal_key == "fat_loss":
        return round((mev + mav) / 2)     # midpoint, not pushed to MAV
    if goal_key == "athletic":
        return round(mav * 0.8)
    return mav                             # muscle_gain / bodybuilding default


# ── 2. SETS / REPS / RIR / REST BY GOAL (doc 2, §1 + §2) ───────────────────
SETS_REPS_BY_GOAL = {
    "strength":       {"reps": "1–6",   "sets_per_exercise": 4, "rest": "3–5 min"},
    "muscle_gain":    {"reps": "6–12",  "sets_per_exercise": 4, "rest": "60–120s (compound), 45–90s (isolation)"},
    "fat_loss":       {"reps": "8–15",  "sets_per_exercise": 3, "rest": "45–90s"},
    "general_fitness":{"reps": "8–15",  "sets_per_exercise": 3, "rest": "60–90s"},
    "athletic":       {"reps": "1–5 (power) / 6–12 (accessory)", "sets_per_exercise": 3, "rest": "3–5 min (power), 60–90s (accessory)"},
}


def sets_reps_rest_for_goal(goal: str) -> dict:
    return SETS_REPS_BY_GOAL.get(_goal_key(goal), SETS_REPS_BY_GOAL["muscle_gain"])


# RIR guidance by training age (doc 2 §2) — for display/coaching text, not
# used in exercise-count math.
RIR_BY_TRAINING_AGE = {
    "beginner":     {"compound": "3–4 RIR", "isolation": "2–3 RIR", "failure": "Avoid true failure entirely"},
    "intermediate": {"compound": "1–2 RIR", "isolation": "0–1 RIR", "failure": "Regularly on last set of isolation work"},
    "advanced":     {"compound": "0–2 RIR (autoregulated)", "isolation": "0 RIR common", "failure": "Programmed failure, forced reps, drop sets as planned tools"},
}


# ── 3. SESSION DURATION CAPS (doc 1, §4) ────────────────────────────────────
# Caps the TOTAL number of exercises in one session so a plan never
# recommends more work than the client's stated session length can hold.
# Values are the upper bound of each row's exercise-count guidance.
def session_duration_cap(minutes: int) -> int:
    if minutes <= 20:
        return 3    # 1 compound + 1 accessory per pattern, supersetted
    if minutes <= 30:
        return 4    # 2 compound + 1-2 accessories
    if minutes <= 45:
        return 5    # standard minimalist session
    if minutes <= 60:
        return 7    # standard full session, 5-6 exercises (+1 buffer for arm floor)
    if minutes <= 90:
        return 9    # full session + extra accessory/isolation/intensity work
    return 11       # 120 min, advanced/powerlifting peaking only


# ── 4. SPLIT DECISION TREE (doc 1, §2) ──────────────────────────────────────
# Maps (training_age_years, days_per_week, goal) -> a split_engine.py
# SPLIT_LIBRARY key. This intentionally reuses the existing token vocabulary
# (push/pull/legs/upper/lower/full) rather than inventing a parallel one, so
# exercise_database.py and fitness_generator.py's TOKEN_MUSCLE_MAP need no
# changes to consume it. See split_engine.recommend_split_master() for the
# function that walks this tree.
#
# "Never assign a bro split under 2 years training age" and "never program
# 7 hard days for natural lifters" (doc 1 §2 coach notes) are enforced as
# hard guards in split_engine.py, not just as data here.
def training_age_years(profile: dict) -> float:
    """
    Doc 1 works in training-AGE-YEARS brackets (<1yr / 1-5yr / 5-10+yr), but
    this app's intake form only collects a 3-tier experience label. Where a
    caller has an explicit `training_age_years` field, use it; otherwise
    approximate with the midpoint of each tier's doc-defined bracket:
      beginner     -> 0.5  (doc 1: "Never trained - 1 yr")
      intermediate -> 3    (doc 1: "1-10+ yrs" bracket, most splits land 2-5yr)
      advanced     -> 7    (doc 1: "5+ yrs")
    """
    explicit = profile.get("training_age_years")
    if isinstance(explicit, (int, float)):
        return float(explicit)
    tier = str(profile.get("experience", "intermediate")).strip().lower()
    if tier.startswith("beg"):
        return 0.5
    if tier.startswith("adv"):
        return 7.0
    return 3.0
