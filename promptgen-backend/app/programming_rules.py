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
#
# STEP 3 (Workout Duration Enforcement): instead of a hand-guessed bucket
# table, the cap is DERIVED from an actual time budget — warm-up +
# (transition + sets * (work + rest)) per exercise — so a 47-minute session
# isn't silently treated the same as a 60-minute one, and the modeled
# workout is mathematically guaranteed to fit inside `minutes` * 1.10.
import re

# Time (seconds) a single working set takes, excluding rest — unrack/setup,
# the rep itself, re-rack. Compounds take a little longer than isolations
# because of heavier loading/setup; a day plan mixes both, so we use their
# midpoint as the per-set constant.
_COMPOUND_SET_SECONDS = 45
_ISOLATION_SET_SECONDS = 30
_AVG_SET_SECONDS = (_COMPOUND_SET_SECONDS + _ISOLATION_SET_SECONDS) / 2  # 37.5s

# Time (seconds) to move from one exercise to the next — walk to the next
# station, adjust the bench/rack, change plates.
_TRANSITION_SECONDS = 45

# Max overrun allowed vs. the client's selected duration (doc requirement:
# "never exceed the selected duration by more than approximately 10%").
_MAX_OVERRUN = 1.10


def _warmup_minutes(minutes: int) -> float:
    """
    Warm-up scales with session length — a 20-minute session can't afford
    a 10-minute warm-up, but a 90-minute session should get a real one
    (general movement prep + specific ramp-up sets).
    """
    if minutes <= 30:
        return 5.0
    if minutes <= 45:
        return 7.0
    if minutes <= 60:
        return 8.0
    if minutes <= 90:
        return 10.0
    return 12.0


def _rest_seconds_for_goal(goal: str | None) -> float:
    """
    Parses the midpoint rest time (in seconds) out of SETS_REPS_BY_GOAL's
    "rest" string for the given goal (e.g. "60–120s" -> 90, "3–5 min" ->
    240). Only the FIRST numeric range in the string is used — goals with
    a split rest scheme (e.g. athletic's power vs. accessory work) still
    only need one representative number here since this feeds a session-
    wide exercise-count cap, not per-exercise timing.
    """
    rest_str = sets_reps_rest_for_goal(goal or "").get("rest", "60–90s")
    match = re.search(r"(\d+)\s*[–\-]\s*(\d+)\s*(s|min)", rest_str)
    if not match:
        return 75.0  # sane fallback: midpoint of the most common 60-90s range
    low, high, unit = int(match.group(1)), int(match.group(2)), match.group(3)
    seconds = (low + high) / 2
    return seconds * 60 if unit == "min" else seconds


def estimate_session_minutes(total_exercises: int, minutes: int, goal: str | None = None) -> float:
    """
    Given a candidate exercise count, returns the realistic total workout
    time (warm-up + all exercises' sets/rest/transitions) in minutes. Used
    both by session_duration_cap() to solve for the cap, and available
    directly so callers/tests can sanity-check "does this plan actually
    fit the selected duration".
    """
    if total_exercises <= 0:
        return _warmup_minutes(minutes)
    sets_per_ex = sets_reps_rest_for_goal(goal or "")["sets_per_exercise"]
    rest_seconds = _rest_seconds_for_goal(goal)
    per_exercise_seconds = _TRANSITION_SECONDS + sets_per_ex * (_AVG_SET_SECONDS + rest_seconds)
    return _warmup_minutes(minutes) + (total_exercises * per_exercise_seconds) / 60.0


def session_duration_cap(minutes: int, goal: str | None = None) -> int:
    """
    Max total exercise count (compound + isolation combined) that
    realistically fits inside `minutes`, including warm-up, per-set rest,
    and transition time between exercises. Guaranteed to keep the modeled
    workout at or under `minutes * 1.10`.

    `goal` is optional so existing callers that only ever passed `minutes`
    keep working — it falls back to the "muscle_gain" rest/set profile,
    which is the middle-of-the-road case (4 sets, 60-120s rest).
    """
    if not minutes or minutes <= 0:
        minutes = 45
    budget_minutes = minutes * _MAX_OVERRUN - _warmup_minutes(minutes)
    if budget_minutes <= 0:
        return 1
    sets_per_ex = sets_reps_rest_for_goal(goal or "")["sets_per_exercise"]
    rest_seconds = _rest_seconds_for_goal(goal)
    per_exercise_seconds = _TRANSITION_SECONDS + sets_per_ex * (_AVG_SET_SECONDS + rest_seconds)
    cap = int((budget_minutes * 60) // per_exercise_seconds)
    return max(cap, 1)


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
