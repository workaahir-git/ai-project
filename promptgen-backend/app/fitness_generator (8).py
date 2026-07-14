"""
fitness_generator.py  ── UPGRADED
──────────────────────────────────────────────────────────────────────────────
Changes vs original:
  • SYSTEM_PROMPT is now a lean structural contract (schema + output rules only)
  • build_user_prompt() does ALL personalisation:
      - Protein multiplier computed from experience tier (beginner/inter/advanced)
      - Protein target, calorie target, deficit/surplus — calculated in Python
        and injected as *exact numbers*, not vague instructions
      - Diet tokens baked in (e.g. "STRICT VEGETARIAN — absolutely no eggs,
        no meat, no fish, no seafood") so the LLM can't misread them
      - Warmup prescribed per workout split (push/pull/legs/full-body/rest)
      - Macros (protein_g, carb_g, fat_g, kcal) required per every meal option
      - All form fields mapped to hard tokens the LLM must honour
  • JSON schema extended: meal options now carry carb_g + fat_g fields;
    workout days carry a warmup_exercises[] array instead of a plain string
  • enforce_schema() updated to match the extended schema
  • per-day exercise counts are PRECOMPUTED in Python (_compute_day_plan /
    _render_day_plan_table) and injected as an authoritative fill-in checklist,
    so the local LLM never has to do proportional math. This fixes (a) Legs days
    not opening with a squat-pattern compound, and (b) Push days coming back with
    only one chest movement.
  • parse_llm_json() now extracts EXACTLY the first brace-balanced {...} object
    via _extract_first_json_object() instead of a greedy `\\{.*\\}` regex. This
    fixes the "Extra data" JSONDecodeError that occurred when the LLM appended a
    duplicate object / repeated block / trailing prose after the first object.
──────────────────────────────────────────────────────────────────────────────
"""

import json
import math
import random
import re
from pathlib import Path
from jinja2 import Environment, FileSystemLoader

from .split_engine import recommend_split, SPLIT_LIBRARY, _session_minutes
from .exercise_database import (
    MUSCLE_PRIORITY,
    BEGINNER_CAP_LEG_DAY as BEGINNER_LEG_DAY_TARGET_TOTAL,
)
# select_day_exercises now comes from exercise_selector.py, not
# exercise_database.py directly — it's a documented drop-in replacement
# (identical (exercises, used_fallback, injury_keywords) return shape)
# that adds movement-pattern-aware isolation sampling, a hard rule against
# literal duplicate exercises in one day, and goal-aware compound
# filtering (recovery goals deprioritize heavy free-weight compounds).
# exercise_database.py's own select_day_exercises() is left in place and
# untouched for any other caller that still imports it directly.
from .exercise_selector import select_day_exercises
from .validator import (
    validate_and_repair_day,
    validate_week,
    get_condition_intensity_flags,
    check_condition_pattern_conflicts,
)
from .programming_rules import (
    weekly_volume_target,
    sets_reps_rest_for_goal,
    session_duration_cap,
)
from . import knowledge_retriever as kb
from . import trainer_review as trainer_review_mod
from . import review_validation as review_validation_mod
from . import allergy_engine
# NOTE on progression.py: attempted to source per-exercise rest from
# progression.py's rest_for_exercise_type() (compound vs isolation bands),
# but that function is keyed by intensity band only, not by goal — there's
# no authoritative goal->exercise_type mapping in engines/programming
# (confirmed via its own GAPS.md, which explicitly leaves that
# classification to the caller). Deriving one via a %1RM cutoff produced
# a real conflict with programming_rules.SETS_REPS_BY_GOAL's own already-
# vetted text (180-300s vs. the KB's stated 60-120s for muscle-gain
# compound work). Reverted to parsing the compound/isolation split that
# already exists as text in SETS_REPS_BY_GOAL itself (see
# _rest_string_for_slot below) instead of pulling from progression.py.
# progression.py is not currently imported by this file as a result —
# see conversation for what a legitimate future use of it here would need
# (a sourced goal/RIR-band -> exercise_type mapping that doesn't exist yet).


# ── TEMPLATE DIR ─────────────────────────────────────────────────────────────
# Render's filesystem is case-sensitive Linux; a local Windows/Mac checkout
# can silently have a differently-cased or differently-located Templates
# folder and still work locally. Try every plausible location/casing instead
# of hard-failing on the first guess — this is a common source of a 500 that
# reproduces only in production and never locally.
TEMPLATE_FILE = "result.html"

_CANDIDATE_TEMPLATE_DIRS = [
    Path(__file__).parent.parent / "Templates",
    Path(__file__).parent.parent / "templates",
    Path(__file__).parent / "Templates",
    Path(__file__).parent / "templates",
    Path.cwd() / "Templates",
    Path.cwd() / "templates",
]

TEMPLATE_DIR = next(
    (p for p in _CANDIDATE_TEMPLATE_DIRS if (p / TEMPLATE_FILE).is_file()),
    _CANDIDATE_TEMPLATE_DIRS[0],  # fall back to original guess so the error
                                   # message below still points somewhere sane
)

if not (TEMPLATE_DIR / TEMPLATE_FILE).is_file():
    print(
        f"[fitness_generator] WARNING: could not find '{TEMPLATE_FILE}' in any "
        f"of: {[str(p) for p in _CANDIDATE_TEMPLATE_DIRS]}. "
        f"render_dashboard() will fail until the Templates folder is committed "
        f"to the repo at one of these paths."
    )


# ── PROTEIN MULTIPLIER TABLE ──────────────────────────────────────────────────
# Keys match the <select> values in dashbord.html  (Beginner / Intermediate / Advanced)
# These are the OUTER bounds for each tier. The actual multiplier used is computed
# dynamically within this band based on activity level + BMI — see _protein_multiplier().
PROTEIN_MULTIPLIER = {
    "beginner":     (1.0, 1.1),    # g of protein per kg of body-weight
    "intermediate": (1.2, 1.4),
    "advanced":     (1.5, 2.0),
}

# Activity level → how far up the tier's protein band a client sits (0 = bottom, 1 = top).
# More active clients break down more muscle protein and need more to recover/grow.
_ACTIVITY_PROTEIN_SCORE = {
    "sedentary":   0.0,
    "light":       0.25,
    "moderate":    0.5,
    "very_active": 0.75,
    "extreme":     1.0,
}


def _bmi_protein_score(bmi: float) -> float:
    """
    Higher BMI (more total mass, proportionally more fat) pulls the multiplier
    DOWN within the tier's band, since g/kg applied to total bodyweight would
    otherwise overshoot real protein needs for someone carrying more fat mass.
    Lower BMI (lean/underweight, trying to build mass) pulls it UP.
    """
    if bmi < 18.5:
        return 1.0       # underweight — push toward top of band to support mass gain
    elif bmi < 25:
        return 0.6        # normal range — slightly above mid-band
    elif bmi < 30:
        return 0.3        # overweight — lower portion of band
    else:
        return 0.0        # obese — bottom of band


def _protein_multiplier(exp_key: str, activity_key: str, bmi: float) -> float:
    """
    Returns a single g/kg multiplier, computed within [low, high] for the
    client's experience tier, weighted by activity level and BMI.
    """
    low, high = PROTEIN_MULTIPLIER[exp_key]
    span = high - low
    activity_score = _ACTIVITY_PROTEIN_SCORE.get(activity_key, 0.5)
    bmi_score = _bmi_protein_score(bmi)
    combined_score = (activity_score + bmi_score) / 2
    return round(low + span * combined_score, 2)

# ── EXERCISE VOLUME TABLE ──────────────────────────────────────────────────────
# Scales workout rigour to experience level. Drives the hard exercise-count
# instruction injected into the prompt so the LLM can't default to "1 exercise".
EXERCISE_VOLUME = {
    "beginner": {
        "exercises_per_day": "3",       # HARD CAP: 1 compound + 2 isolation
        "compound_count": 1,
        "isolation_count": 2,
        "sets_per_exercise":  "2–3",
        "rest_between_sets":  "60–90 sec",
        "intensity_note": (
            "Focus on form and machine/cable-based movements. Avoid advanced "
            "techniques (no drop sets, no supersets, no failure training)."
        ),
    },
    "intermediate": {
        "exercises_per_day": "5",       # HARD CAP: 1 compound + 4 isolation
        "compound_count": 1,
        "isolation_count": 4,
        "sets_per_exercise":  "3–4",
        "rest_between_sets":  "60–90 sec",
        "intensity_note": (
            "Mix compound and isolation movements. One superset pairing per "
            "session is fine. Train close to failure on the last set of each exercise."
        ),
    },
    "advanced": {
        "exercises_per_day": "6–8",     # HIGHER volume: 1–2 compound + rest isolation
        "compound_count": "1–2",
        "isolation_count": "4–6",
        "sets_per_exercise":  "4–5",
        "rest_between_sets":  "45–75 sec (60–120 sec only on heavy compounds)",
        "intensity_note": (
            "High training density. Include at least 1–2 intensity techniques per "
            "session (supersets, drop sets, rest-pause, or partials on the final "
            "set of an isolation exercise). Push working sets close to or to failure. "
            "Sessions should feel demanding and time-efficient, not relaxed."
        ),
    },
}

# Minimum (floor) exercise count per tier, used to size the schema-enforcement
# low-volume warning. Advanced uses the low end of its "6–8" range.
MIN_EXERCISES = {
    "beginner": 3,
    "intermediate": 5,
    "advanced": 6,
}


# ── TOKEN → MUSCLE GROUP MAP ──────────────────────────────────────────────────
# Maps split-day tokens (from split_engine.py) to the ordered list of muscle
# groups trained that day. "big" groups (rank 1–4) each get exactly one compound;
# rank 5–6 (arms, calves, core) are isolation-only.
TOKEN_MUSCLE_MAP = {
    "push":  ["chest", "shoulders", "triceps"],
    "pull":  ["back", "biceps"],
    "legs":  ["legs", "calves"],
    "upper": ["back", "chest", "shoulders", "biceps", "triceps"],
    "lower": ["legs", "calves"],
    "full":  ["legs", "back", "chest", "shoulders"],
    "cardio": [],
    "rest":  [],

    # ── New day-type tokens introduced by the expanded 24-row split table
    # (split_engine.py SPLIT_LIBRARY). Each maps to the muscle groups that
    # day actually trains; _compute_day_plan() below works generically off
    # this list (fixed floors for 4+ muscles trained, round-robin + arm-floor
    # for 1-3), so no further special-casing is needed per token.
    "chest_triceps":   ["chest", "triceps"],
    "back_biceps":     ["back", "biceps"],
    "shoulders_abs":   ["shoulders", "core"],
    "upper_machines":  ["back", "chest", "shoulders", "biceps", "triceps"],   # alias of "upper"
    "lower_machines":  ["legs", "calves"],                                    # alias of "legs"
    "full_body_machines": ["legs", "back", "chest", "shoulders"],             # alias of "full"
    "cardio_core":     ["core"],
    "squat_focus":       ["legs"],
    "bench_focus":       ["chest"],
    "deadlift_focus":    ["back"],
    "overhead_press_accessories": ["shoulders"],
    "full_accessory":  ["legs", "back", "chest", "shoulders"],                # alias of "full"
    "torso":           ["chest", "back", "shoulders"],
    "limbs":           ["legs", "biceps", "triceps"],
    "chest":           ["chest"],
    "back":            ["back"],
    "shoulders":       ["shoulders"],
    "arms":            ["biceps", "triceps"],
    "heavy_push":        ["chest", "shoulders", "triceps"],                   # alias of "push"
    "heavy_pull":        ["back", "biceps"],                                  # alias of "pull"
    "heavy_legs":        ["legs", "calves"],                                  # alias of "legs"
    "hypertrophy_push":  ["chest", "shoulders", "triceps"],
    "hypertrophy_pull":  ["back", "biceps"],
    "hypertrophy_legs":  ["legs", "calves"],
    "lower_strength":    ["legs", "calves"],
    "upper_strength":    ["back", "chest", "shoulders", "biceps", "triceps"],
    "conditioning":      [],   # cardio-style, no weight-room exercises
    "explosive_training": [],  # plyometric/athletic, no weight-room exercises
    "full_athletic_circuit": ["legs", "back", "chest", "shoulders"],
    # "Weak Point" / "Priority Muscle" are, per message.txt, whichever muscle
    # group the individual client is lagging in — that's a per-client fact,
    # not something derivable from the split table alone. Defaulting to
    # biceps/triceps (the most commonly-lagging group) is a reasonable
    # placeholder; pass profile["weak_point_muscle"] (see _resolve_weak_point
    # below) to target a specific muscle instead.
    "weak_point":        ["biceps", "triceps"],
    "priority_muscle":   ["biceps", "triceps"],
    "chest_shoulders":   ["chest", "shoulders"],
    "back_arms":         ["back", "biceps", "triceps"],
    "legs_core":         ["legs", "calves", "core"],
    "push_heavy":        ["chest", "shoulders", "triceps"],
    "pull_heavy":        ["back", "biceps"],
    "legs_heavy":        ["legs", "calves"],
    "push_volume":       ["chest", "shoulders", "triceps"],
    "pull_volume":       ["back", "biceps"],
    "legs_volume":       ["legs", "calves"],
    "chest_back":        ["chest", "back"],
    "shoulders_arms":    ["shoulders", "biceps", "triceps"],
    "arms_core":         ["biceps", "triceps", "core"],
    "upper_power":       ["back", "chest", "shoulders", "biceps", "triceps"],
    "lower_power":       ["legs", "calves"],
    "back_shoulders_hypertrophy": ["back", "shoulders"],
    "lower_hypertrophy": ["legs", "calves"],
    "chest_arms_hypertrophy": ["chest", "biceps", "triceps"],
    "heavy_chest_back":  ["chest", "back"],
    "heavy_shoulders":   ["shoulders"],
    "chest_back_volume": ["chest", "back"],
    "arms_shoulders":    ["biceps", "triceps", "shoulders"],
    # "Strength" / "Hypertrophy" / "Athletic" (Functional Bodybuilding, row 23)
    # are deliberately generic full-body-style days per message.txt / the
    # template txt — mapped to the same 4 big muscles as "full".
    "strength":          ["legs", "back", "chest", "shoulders"],
    "hypertrophy":       ["legs", "back", "chest", "shoulders"],
    "athletic":          [],   # conditioning/plyo-style, no weight-room exercises
    "mobility":          [],   # stretching/mobility work, no weight-room exercises
    "conditioning_abs":  ["core"],
}

# Tokens with no muscles (cardio, conditioning, athletic, mobility, rest) get
# treated like "cardio" for warmup/day-building purposes — see
# build_deterministic_workout_days() and _render_day_plan_table() below.
NO_LIFTING_TOKENS = {
    "cardio", "conditioning", "explosive_training", "athletic", "mobility", "rest",
}

# Muscle size classification. "Big" muscles (rank <= 4 conceptually) each get
# a compound; ordering/priority itself now comes from the shared
# MUSCLE_PRIORITY table (imported from exercise_database.py) so this file and
# exercise_database.py can never disagree on relative priority again.
_BIG_MUSCLES = {"legs", "back", "chest", "shoulders"}
_ARM_MUSCLES = {"biceps", "triceps"}

# Which compound-library entry heads each big muscle group (squat-pattern only for legs).
_COMPOUND_HINT = {
    "legs":      "A squat variant ONLY — Hack Squat / Smith Machine Squat / Goblet Squat / Bodyweight Squat / Barbell Back Squat (never a lunge, hinge, or leg press)",
    "back":      "Lat Pulldown / Seated Cable Row / Chest-Supported Machine Row / Assisted Pull-up",
    "chest":     "Machine Chest Press / Incline Dumbbell Press / Flat Dumbbell Press / Smith Machine Bench Press",
    "shoulders": "Machine Shoulder Press / Seated Dumbbell Press / Arnold Press",
}

ARM_ISOLATION_FLOOR = 2  # hard minimum isolation exercises per arm muscle trained

# ── HARD-RULE BEGINNER PUSH / PULL / LEGS DISTRIBUTION ───────────────────────
# Per explicit client requirement: beginner Push/Pull/Legs days do NOT use the
# generic round-robin isolation split — they use this exact, fixed breakdown:
#   Push: 3 chest total (1 compound + 2 isolation), 2 triceps, 1 shoulder
#   Pull: 3 back total (1 compound + 2 isolation), 2 biceps, 0 traps
#   Legs: 6 total leg exercises (1 compound + 4 isolation legs + 1 calf)
# Only the compound muscle(s) listed get a compound lift; every other muscle
# on the day is isolation-only (this is what keeps beginner Push at exactly
# 1 shoulder exercise instead of a second compound + isolation).
BEGINNER_FIXED_DAY_PLANS = {
    "push": {
        "muscles": ["chest", "triceps", "shoulders"],
        "compound_muscles": ["chest"],
        "isolation_by_muscle": {"chest": 2, "triceps": 2, "shoulders": 1},
    },
    "pull": {
        "muscles": ["back", "biceps"],
        "compound_muscles": ["back"],
        "isolation_by_muscle": {"back": 2, "biceps": 2},
    },
    "legs": {
        "muscles": ["legs", "calves"],
        "compound_muscles": ["legs"],
        "isolation_by_muscle": {"legs": 4, "calves": 1},
    },
}
BEGINNER_FIXED_DAY_PLANS["lower"] = BEGINNER_FIXED_DAY_PLANS["legs"]  # "lower" is legs day under some splits


# ── Client hard-rule: combined "upper" day exact exercise breakdown ────────
# Every upper-body day (back + chest + shoulders + biceps + triceps trained
# together, e.g. the Upper/Lower split used for 4 days/week) keeps back and
# chest as the ONLY compound muscles, so back/chest never get
# out-prioritized by arm volume the way the old formula-driven/
# volume-override logic could do.
#
# STEP 2 (Workout Volume Calibration): this used to be ONE identical
# breakdown (2 compound + 5 isolation = 7 total) for every experience tier,
# which bypassed the MEV/MAV-driven per-tier volume system entirely (this
# branch returns early with "no_trim": True, before the muscle_frequency/
# weekly_volume_target override further down ever runs) and directly
# contradicted "beginners get lower volume, advanced get the highest volume"
# — a beginner's single combined upper day was landing above
# BEGINNER_CAP_OTHER_DAY (5) and even BEGINNER_CAP_LEG_DAY (6) at once.
# Muscle PRIORITY/ORDER is unchanged by this fix — back and chest are still
# the only compound muscles and still get first claim on isolation work at
# every tier; only the TOTAL isolation volume now scales beginner <
# intermediate < advanced, same direction EXERCISE_VOLUME and
# MUSCLE_VOLUME_MEV_MAV already use everywhere else in this file.
UPPER_FIXED_DAY_PLAN_BY_TIER = {
    "beginner": {
        "muscles": ["back", "chest", "shoulders", "biceps", "triceps"],
        "compound_muscles": ["back", "chest"],
        # No dedicated arm isolation slot on this combined day at the
        # beginner tier — the back/chest compounds already load biceps/
        # triceps synergistically, and beginners get direct, floor-protected
        # arm isolation on their (far more common) Push/Pull/Legs days via
        # BEGINNER_FIXED_DAY_PLANS instead. Total = 5, matching
        # BEGINNER_CAP_OTHER_DAY.
        "isolation_by_muscle": {"back": 1, "chest": 1, "shoulders": 1, "biceps": 0, "triceps": 0},
    },
    "intermediate": {
        "muscles": ["back", "chest", "shoulders", "biceps", "triceps"],
        "compound_muscles": ["back", "chest"],
        # Original hard-rule numbers, unchanged — this is the "moderate"
        # baseline the beginner/advanced tiers now scale down/up from.
        "isolation_by_muscle": {"back": 1, "chest": 1, "shoulders": 1, "biceps": 1, "triceps": 1},
    },
    "advanced": {
        "muscles": ["back", "chest", "shoulders", "biceps", "triceps"],
        "compound_muscles": ["back", "chest"],
        # Highest volume of the three tiers — extra back/chest isolation
        # (the two priority muscles), arms/shoulders unchanged from the
        # intermediate baseline. Total = 9.
        "isolation_by_muscle": {"back": 2, "chest": 2, "shoulders": 1, "biceps": 1, "triceps": 1},
    },
}
# Back-compat alias for any other caller/import expecting a single plan —
# points at the intermediate tier, i.e. the original numbers, unchanged.
UPPER_FIXED_DAY_PLAN = UPPER_FIXED_DAY_PLAN_BY_TIER["intermediate"]


def _parse_low_int(val, default: int) -> int:
    """'4–6' → 4, '5' → 5, 5 → 5. Takes the LOW end of any range."""
    if isinstance(val, (int, float)):
        return int(val)
    nums = re.findall(r"\d+", str(val))
    return int(nums[0]) if nums else default


def _muscle_frequency(sequence: list, exp_key: str) -> dict:
    """
    Counts how many days/week each muscle is trained, given the FULL split
    sequence for the week. This is the "frequency" input the Weekly Muscle
    Volume table (8_Weekly_Muscle_Volume.md §3) needs to convert a weekly
    set target into a per-session set target — a muscle trained 2x/week
    needs half the isolation work per session that a muscle trained 1x/week
    does to hit the same weekly total.
    Mirrors the same muscle-list expansion _compute_day_plan uses per-token
    (traps added to pull days for int/adv, beginner fixed-plan muscle lists)
    so frequency counting and per-day counting never disagree.
    """
    freq = {}
    for token in sequence:
        if exp_key == "beginner" and token in BEGINNER_FIXED_DAY_PLANS:
            muscles = BEGINNER_FIXED_DAY_PLANS[token]["muscles"]
        else:
            muscles = list(TOKEN_MUSCLE_MAP.get(token, []))
            if token == "pull" and exp_key in ("intermediate", "advanced"):
                muscles.append("traps")
        for m in muscles:
            freq[m] = freq.get(m, 0) + 1
    return freq


def _compute_day_plan(
    token: str,
    vol: dict,
    exp_key: str = "intermediate",
    goal: str = "",
    muscle_frequency: dict | None = None,
    session_minutes: int | None = None,
    progression_context: dict | None = None,
) -> dict:
    """
    Precompute the EXACT exercise breakdown for one training day so the LLM
    never has to do proportional math. Returns a dict:
      {
        "muscles":            [ordered highest→lowest priority],
        "compound_count":     int,
        "isolation_by_muscle": {muscle: int, ...},
        "total_exercises":    int,
      }
    Rules applied (in Python, not by the LLM):
      • one compound per BIG muscle group trained that day
      • isolation budget is distributed round-robin IN PRIORITY ORDER
        (MUSCLE_PRIORITY, shared with exercise_database.py) across every
        trained muscle — this is what guarantees chest > triceps > shoulders
        on push day and back > biceps > traps on pull day in total exercise
        count, since the highest-priority muscle always gets first pick.
      • arm floor (biceps/triceps >= ARM_ISOLATION_FLOOR isolation) is then
        topped up ONLY by borrowing from a LOWER-priority muscle's existing
        allocation — never from anything ranked above the arm — so the arm
        floor can't silently starve chest/back the way a pre-allocated floor
        used to.
      • pull day gets a trailing traps slot, but ONLY for intermediate/advanced
        (exp_key is the same beginner/intermediate/advanced tier used everywhere
        else — see _resolve_exp_key()); beginners never see traps at all.
      • beginner leg day gets a boosted isolation budget so it can actually
        reach BEGINNER_CAP_LEG_DAY (6) total exercises — the tier's generic
        isolation_count (2) is too low to ever hit that cap on its own, since
        caps only trim excess, they never manufacture additional exercises.
        This checks token in ("legs", "lower") since some splits label leg
        day "lower" rather than "legs" — same muscles, different token.
      • the combined "upper" day (back+chest+shoulders+biceps+triceps, 5
        muscle groups at once) does NOT use the round-robin/arm-floor-borrow
        logic at all — a tier's isolation budget (as low as 2 for beginners)
        can't cover 5 muscles that way without starving whoever's last in
        priority order, and the borrow rule has nothing left to borrow from
        once it reaches the last-priority muscle. Instead it uses fixed,
        tier-independent isolation floors (back 3, chest 3, shoulders 2,
        biceps 2, triceps 2) so no muscle group can silently vanish.

    `progression_context` (optional, see progression_context.py) is where
    adaptive progression connects to generation: its "volume_multiplier"
    (computed by progression_engine.py — deload cuts, plateau bumps,
    low-compliance cuts, etc.) scales each muscle's weekly_volume_target
    in the MEV/MAV override branch below, exactly like the integration
    spec's "reducing weekly volume" / "applying a deload" examples. `None`
    (the default, and what every existing caller still passes) behaves
    identically to before this param existed — this is what "generate
    workouts exactly as before" when there's no reassessment data means in
    practice. NOTE: the "upper" and beginner fixed-plan branches above
    return early and don't reach the MEV/MAV override, so volume_multiplier
    has no effect on those two day types — a known, documented gap rather
    than a silent one, consistent with this task's "do not redesign the
    generator" scope.
    """
    # ── Combined "upper" day uses a fixed, explicit distribution per client
    # hard-rule (see UPPER_FIXED_DAY_PLAN above) — applies to EVERY
    # experience tier, unlike BEGINNER_FIXED_DAY_PLANS below, and takes
    # priority over both the beginner fixed plans and the generic
    # formula-driven/volume-override logic further down (including the
    # muscle_frequency override), so back/chest counts can never be
    # squeezed out by arm-floor borrowing or weekly-volume math.
    if token == "upper":
        # STEP 2: pick the tier-scaled plan (beginner < intermediate <
        # advanced total volume) instead of one flat plan for every tier.
        fixed = UPPER_FIXED_DAY_PLAN_BY_TIER.get(exp_key, UPPER_FIXED_DAY_PLAN_BY_TIER["intermediate"])
        isolation_by_muscle = dict(fixed["isolation_by_muscle"])
        total = len(fixed["compound_muscles"]) + sum(isolation_by_muscle.values())
        return {
            "muscles": list(fixed["muscles"]),
            "compound_count": len(fixed["compound_muscles"]),
            "compound_muscles": list(fixed["compound_muscles"]),
            "isolation_by_muscle": isolation_by_muscle,
            "total_exercises": total,
            "no_trim": True,  # exact by design — session-duration cap must not touch this
        }

    # ── Beginner Push/Pull/Legs (and "lower" alias) use a fixed, explicit
    # distribution per client hard-rule (see BEGINNER_FIXED_DAY_PLANS above)
    # instead of any of the generic logic below — no round-robin, no
    # compound-per-big-muscle default (that would otherwise also give
    # shoulders its own compound on push day, which is not wanted here).
    if exp_key == "beginner" and token in BEGINNER_FIXED_DAY_PLANS:
        fixed = BEGINNER_FIXED_DAY_PLANS[token]
        isolation_by_muscle = dict(fixed["isolation_by_muscle"])
        total = len(fixed["compound_muscles"]) + sum(isolation_by_muscle.values())
        return {
            "muscles": list(fixed["muscles"]),
            "compound_count": len(fixed["compound_muscles"]),
            "compound_muscles": list(fixed["compound_muscles"]),
            "isolation_by_muscle": isolation_by_muscle,
            "total_exercises": total,
            "no_trim": True,  # exact by design — beginner cap trimming must not touch this
        }

    muscles = list(TOKEN_MUSCLE_MAP.get(token, []))
    if token == "pull" and exp_key in ("intermediate", "advanced"):
        muscles.append("traps")
    if not muscles:  # cardio / conditioning / athletic / mobility / rest / unknown
        return {
            "muscles": [], "compound_count": 0,
            "isolation_by_muscle": {}, "total_exercises": 0,
        }

    # order highest → lowest priority, using the SAME table exercise_database.py
    # uses for display/trimming, so count-allocation and display never disagree.
    muscles = sorted(muscles, key=lambda m: MUSCLE_PRIORITY.get(m, 9))

    big_trained = [m for m in muscles if m in _BIG_MUSCLES]
    compound_count = len(big_trained)   # one compound per big group

    isolation_by_muscle = {m: 0 for m in muscles}

    if len(muscles) >= 4:
        # Combined multi-muscle day (4+ groups at once — "upper", "torso",
        # "full_athletic_circuit", "upper_power", etc). A tier-based isolation
        # budget distributed round-robin in priority order runs out before it
        # ever reaches the tail of that order, and the "borrow from a lower-
        # priority muscle" rescue has nothing left to borrow from once it
        # reaches the last muscle — so an entire muscle group can vanish from
        # the day. Fixed, tier-independent floors replace the round-robin/
        # borrow logic entirely so every trained muscle always shows up:
        # big muscles (legs/back/chest/shoulders) get a floor of 3, everything
        # else (arms/traps/calves/core) gets a floor of 2.
        for m in muscles:
            isolation_by_muscle[m] = 3 if m in _BIG_MUSCLES else 2
    else:
        # base isolation budget from the tier (strip ranges like "4–6" → take low end)
        iso_base = _parse_low_int(vol["isolation_count"], default=4)
        if token in ("legs", "lower") and exp_key == "beginner":
            # "lower" is this split's token for leg day (same muscles as
            # "legs" elsewhere) — without matching both tokens the beginner
            # leg-day boost silently never fires on splits that label the
            # day "lower" instead of "legs".
            iso_base = max(iso_base, BEGINNER_LEG_DAY_TARGET_TOTAL - compound_count)
        if token == "pull" and "traps" in muscles:
            # Without this, the biceps arm-floor top-up below deterministically
            # donates traps down to 0 on every single pull day for every
            # intermediate/advanced client — traps would exist in the data but
            # never actually appear. One extra slot is enough to cover the floor
            # without cannibalizing traps entirely.
            iso_base += 1

        # 1) distribute the isolation budget round-robin in priority order across
        #    EVERY trained muscle (big and arm alike) — highest priority gets
        #    first pick each round, so this alone produces the right ordering.
        remaining_iso = iso_base
        i = 0
        while remaining_iso > 0 and muscles:
            m = muscles[i % len(muscles)]
            isolation_by_muscle[m] += 1
            remaining_iso -= 1
            i += 1

        # 2) top up any arm muscle below its mandatory floor — but only by
        #    borrowing from a muscle ranked LOWER in priority than that arm,
        #    never from anything ranked above it. This protects arm volume
        #    without letting the floor silently outrank chest/back again.
        arms_trained = [m for m in muscles if m in _ARM_MUSCLES]
        for arm in arms_trained:
            idx_arm = muscles.index(arm)
            deficit = ARM_ISOLATION_FLOOR - isolation_by_muscle[arm]
            donor_idx = len(muscles) - 1
            while deficit > 0 and donor_idx > idx_arm:
                donor = muscles[donor_idx]
                if isolation_by_muscle[donor] > 0:
                    isolation_by_muscle[donor] -= 1
                    isolation_by_muscle[arm] += 1
                    deficit -= 1
                else:
                    donor_idx -= 1

    # ── VOLUME-DRIVEN OVERRIDE (8_Weekly_Muscle_Volume.md §2 + §6) ─────────
    # Replaces the flat per-tier isolation_by_muscle computed above with a
    # count derived from each muscle's actual weekly MEV/MAV target divided
    # by how many times/week the chosen split trains it. This is what makes
    # exercise count scale with the NUMBER of muscles trained in a session
    # (an upper day training 5 muscles gets 5 independently-sized isolation
    # counts, not one shared flat budget) instead of a fixed per-day cap.
    # Only runs when a caller supplies muscle_frequency (both current call
    # sites now do); falls back to the logic above otherwise so nothing
    # that doesn't pass it breaks.
    if muscle_frequency is not None:
        sets_per_ex = sets_reps_rest_for_goal(goal)["sets_per_exercise"]
        # Deterministic adaptive-progression hook (see progression_context.py
        # docstring above): 1.0 whenever there's no progression context yet,
        # i.e. identical to pre-integration behaviour.
        volume_multiplier = (progression_context or {}).get("volume_multiplier", 1.0) or 1.0
        for m in muscles:
            freq = max(muscle_frequency.get(m, 1), 1)
            weekly_target = weekly_volume_target(m, exp_key, goal) * volume_multiplier
            if weekly_target <= 0:
                continue
            sets_needed_this_session = weekly_target / freq
            compound_sets_here = sets_per_ex if m in big_trained else 0
            iso_sets_needed = max(sets_needed_this_session - compound_sets_here, 0)
            iso_count = math.ceil(iso_sets_needed / sets_per_ex) if sets_per_ex else 0
            if m in _ARM_MUSCLES:
                iso_count = max(iso_count, ARM_ISOLATION_FLOOR)
            isolation_by_muscle[m] = iso_count

    total_iso = sum(isolation_by_muscle.values())
    total_exercises = compound_count + total_iso

    # ── SESSION DURATION CAP (1_Master_Workout_Split_Table.md §4) ──────────
    # Applies across ALL experience tiers (the doc's table isn't beginner-
    # specific) — trims isolation only, smallest-priority muscle first,
    # same trim direction exercise_database.py already uses for the
    # beginner-only cap, so the two caps never disagree about what gets cut.
    if session_minutes is not None and total_exercises > 0:
        cap = session_duration_cap(session_minutes, goal)
        if total_exercises > cap:
            overflow = total_exercises - cap
            # pass 1: trim every muscle down to its protected floor (arm
            # muscles keep ARM_ISOLATION_FLOOR, everything else can go to 0),
            # smallest-priority muscle first — this is what stops a 5-muscle
            # combined day (e.g. "upper") from silently trimming arm work to
            # zero just because it's last in priority order.
            for m in reversed(muscles):
                if overflow <= 0:
                    break
                floor = ARM_ISOLATION_FLOOR if m in _ARM_MUSCLES else 0
                cut = min(max(isolation_by_muscle[m] - floor, 0), overflow)
                isolation_by_muscle[m] -= cut
                overflow -= cut
            # pass 2: only if the session is genuinely too short even for
            # floor-only isolation, trim below the arm floor too, as a last
            # resort — still smallest-priority-muscle first.
            if overflow > 0:
                for m in reversed(muscles):
                    if overflow <= 0:
                        break
                    cut = min(isolation_by_muscle[m], overflow)
                    isolation_by_muscle[m] -= cut
                    overflow -= cut
            total_iso = sum(isolation_by_muscle.values())
            total_exercises = compound_count + total_iso

    return {
        "muscles": muscles,
        "compound_count": compound_count,
        "isolation_by_muscle": isolation_by_muscle,
        "total_exercises": total_exercises,
    }


def _render_day_plan_table(
    sequence: list,
    vol: dict,
    exp_key: str = "intermediate",
    goal: str = "",
    session_minutes: int | None = None,
) -> str:
    """
    Turn the split sequence into a literal, per-day fill-in checklist the LLM
    must obey verbatim — no math left for the model to do.
    """
    muscle_frequency = _muscle_frequency(sequence, exp_key)
    lines = []
    for idx, token in enumerate(sequence, start=1):
        if token == "rest":
            lines.append(f"  DAY {idx} (REST): REST — no exercises, omit warmup_exercises.")
            continue
        if token in NO_LIFTING_TOKENS:
            lines.append(
                f"  DAY {idx} ({token.upper()}): 1 steady-state or interval cardio/conditioning "
                f"block + optional core. Include warmup_exercises."
            )
            continue

        plan = _compute_day_plan(
            token, vol, exp_key,
            goal=goal, muscle_frequency=muscle_frequency, session_minutes=session_minutes,
        )
        parts = []
        # compounds first (largest big group's compound first). Some day
        # types (e.g. beginner push/pull/legs) specify EXACTLY which
        # muscle(s) get a compound via plan["compound_muscles"] rather than
        # "every big muscle trained gets one" — respect that when present.
        compound_muscles = plan.get("compound_muscles")
        if compound_muscles is None:
            compound_muscles = [m for m in plan["muscles"] if m in _BIG_MUSCLES]
        for m in plan["muscles"]:
            if m in compound_muscles:
                hint = _COMPOUND_HINT.get(m, f"a squat/press/row/pull pattern for {m}")
                parts.append(f"1× COMPOUND for {m.upper()} [{hint}]")
        # isolation next, largest → smallest
        for m in plan["muscles"]:
            n = plan["isolation_by_muscle"].get(m, 0)
            if n > 0:
                floor_tag = " (ARM FLOOR — mandatory)" if m in _ARM_MUSCLES else ""
                parts.append(f"{n}× ISOLATION for {m.upper()}{floor_tag}")

        checklist = "\n        • ".join(parts)
        lines.append(
            f"  DAY {idx} ({token.upper()} — {' · '.join(m.title() for m in plan['muscles'])}): "
            f"EXACTLY {plan['total_exercises']} exercises, in this order:\n"
            f"        • {checklist}"
        )
    return "\n".join(lines)


# ── DETERMINISTIC EXERCISE SELECTION (Python owns this, not the LLM) ─────────
# Reps now come from the same goal-based table as sets/rest (2_Programming_Rules.md
# §1), so a fat-loss client actually gets a higher-rep/lighter-weight prescription
# (8–15) and a muscle-gain client gets fewer/heavier reps (6–12) instead of every
# goal seeing the same fixed 8–10 / 12–15 split by slot type.

_REST_SPLIT_PATTERN = re.compile(
    r"([\d\u2013\u2013\-\s]+(?:s|min))\s*\((compound|power)\)\s*,\s*"
    r"([\d\u2013\-\s]+(?:s|min))\s*\((isolation|accessory)\)",
    re.IGNORECASE,
)


def _rest_string_for_slot(goal: str, slot: str) -> str:
    """
    Per-exercise rest range, slot-aware (compound vs isolation) where the
    source data actually supports that split, otherwise the goal's single
    flat value.

    programming_rules.SETS_REPS_BY_GOAL's own "rest" text already encodes
    a compound/isolation (or power/accessory) split as descriptive text
    for goals where 2_Programming_Rules.md specifies one (muscle_gain:
    "60-120s (compound), 45-90s (isolation)"; athletic: "3-5 min (power),
    60-90s (accessory)") — this parses that existing text and returns the
    half that matches the exercise's actual slot, instead of the previous
    behavior of printing the whole dual-value string on every exercise
    regardless of slot.

    For goals where the KB gives one flat value with no split (strength,
    fat_loss, general_fitness), that same flat value is used for both
    compound and isolation — deliberately NOT inventing a split there.
    An earlier version of this function picked progression.py's
    heavy_compound_ge_85pct vs moderate_compound_65_80pct band based on
    an 85%-1RM cutoff invented for this purpose; that produced 180-300s
    for muscle-gain compound work, contradicting the 60-120s already in
    the KB text above. engines/programming's rest_for_exercise_type() is
    keyed by intensity band only, not by goal (confirmed in its own
    GAPS.md: goal-to-exercise-type classification is explicitly left to
    the caller, not sourced) — so deriving it from goal via a made-up
    threshold isn't a legitimate use of that function. Parsing the
    already-vetted goal-level text directly avoids introducing a second,
    conflicting number.
    """
    rest_text = sets_reps_rest_for_goal(goal)["rest"]
    match = _REST_SPLIT_PATTERN.search(rest_text)
    if not match:
        return rest_text  # flat value, no split in the source — use as-is for every slot
    compound_part, _, isolation_part, _ = match.groups()
    return compound_part.strip() if slot == "compound" else isolation_part.strip()


_SAFETY_DEFAULT_BY_TOKEN = {
    "push":  "Keep shoulder blades back and down, controlled tempo on every rep — stop a set short of failure if form breaks down.",
    "pull":  "Drive with the elbows, not the hands — avoid shrugging or using momentum to move the weight.",
    "legs":  "Keep knees tracking over toes, chest up, controlled descent on every rep.",
    "upper": "Controlled tempo throughout, full range of motion, stop a rep short of failure if form breaks down.",
    "lower": "Keep knees tracking over toes, chest up, controlled descent on every rep.",
    "full":  "Controlled tempo throughout, prioritise form over load on every movement.",
    "cardio": "Keep effort conversational-to-moderate unless the session specifically calls for intervals.",
}


def build_deterministic_workout_days(profile: dict, weekly_template: list, vol: dict) -> list:
    """
    Builds the full workout.days[] content in Python — exercise selection,
    sets/reps/rest, tempo cues, and warmup — using exercise_database.py.
    The LLM is not involved in deciding which exercises appear at all;
    this replaces that entire responsibility.

    Uses a fresh, unseeded random.Random() per call so regenerating the
    same profile produces variety across the week and across re-runs,
    while the day's *structure* (counts, compound-vs-isolation, ordering,
    caps) stays fully deterministic via _compute_day_plan() + the cap/
    priority logic inside select_day_exercises().
    """
    equipment_raw = profile.get("equipment", "full gym")
    notes_raw = profile.get("medical_notes") or profile.get("notes") or ""
    experience_raw = profile.get("experience", "Intermediate")
    exp_key = _resolve_exp_key(profile)
    goal = profile.get("goal", "")
    session_minutes = _session_minutes(profile.get("session_duration"))
    muscle_frequency = _muscle_frequency(weekly_template, exp_key)
    rng = random.Random()

    # Final Backend Integration: consume the latest adaptive progression
    # decision, if main.py attached one (see progression_context.py).
    # `None` here (no reassessment yet, or the optional load failed) means
    # every branch below behaves exactly as it did before this hook existed.
    progression_context = profile.get("_progression_context")
    if progression_context and progression_context.get("pain_flags"):
        # Fold biweekly check-in pain flags into the same free-text injury
        # detection notes_raw already goes through (exercise_selector.py's
        # _parse_injury_keywords), so newly-reported pain gets the same
        # exclusion treatment as intake-form pain — no duplicate injury
        # logic, just a wider input to the existing filter.
        notes_raw = f"{notes_raw}, {', '.join(progression_context['pain_flags'])} pain (from biweekly check-in)"

    # Condition-based defense-in-depth flags don't depend on any one day,
    # so this is computed once per profile rather than re-parsing
    # notes_raw on every training day.
    condition_flags = get_condition_intensity_flags(notes_raw)

    days = []
    # Post-repair exercise lists, index-aligned with weekly_template
    # (including rest/cardio days as empty placeholders), so validate_week()
    # can run its cross-day checks (push/pull balance, split consistency,
    # weekly volume sanity) against exactly what actually shipped in each
    # day, not the pre-repair picks.
    week_exercises_by_day: list = []

    for token in weekly_template:
        if token == "rest":
            days.append({"is_rest": True})
            week_exercises_by_day.append([])
            continue

        if token in NO_LIFTING_TOKENS:
            days.append({
                "is_rest": False,
                "warmup_exercises": WARMUP_LIBRARY.get(token, WARMUP_LIBRARY.get("cardio", [])),
                "exercises": [],
                "safety": _SAFETY_DEFAULT_BY_TOKEN.get(token, _SAFETY_DEFAULT_BY_TOKEN.get("cardio", "")),
            })
            week_exercises_by_day.append([])
            continue

        plan = _compute_day_plan(
            token, vol, exp_key,
            goal=goal, muscle_frequency=muscle_frequency, session_minutes=session_minutes,
            progression_context=progression_context,
        )
        picks, _used_fallback, _injury_kw = select_day_exercises(
            plan, equipment_raw, notes_raw, experience_raw, rng,
            goal_raw=goal,
        )

        # Post-generation QA + auto-repair pass (validator.py). This is
        # defense-in-depth on top of exercise_selector.py's own
        # equipment/injury/duplicate-pattern handling at selection time,
        # not a replacement for it — exercise_selector.py is still what
        # prevents most issues from ever reaching this point. What lands
        # here in practice is mostly a no-op; repairs/rejections only fire
        # when selection-time filtering already had to fall back (e.g. a
        # pattern duplicate the pool couldn't avoid) or on any future
        # caller that builds a day's exercise list without going through
        # select_day_exercises() at all.
        repair_result = validate_and_repair_day(
            picks, plan, equipment_raw, notes_raw, experience_raw, rng,
        )
        picks = repair_result["exercises"]
        day_warnings = list(repair_result["warnings"])
        day_warnings.extend(check_condition_pattern_conflicts(picks, condition_flags))

        week_exercises_by_day.append(picks)

        # Sets/reps still come from 2_Programming_Rules.md §1 (goal-based),
        # unchanged. Rest is now per-exercise slot-aware (see
        # _rest_string_for_slot) instead of one flat string applied to
        # every exercise in the day regardless of compound vs isolation.
        goal_prescription = sets_reps_rest_for_goal(goal)
        exercises = []
        for p in picks:
            exercises.append({
                "name": p["name"],
                "muscle": p["muscle"].title(),
                "sets": goal_prescription["sets_per_exercise"],
                "reps": goal_prescription["reps"] + " reps",
                "rest": _rest_string_for_slot(goal, p.get("slot", "isolation")),
                "tempo_or_cue": p["cue"],
            })

        day_entry = {
            "is_rest": False,
            "warmup_exercises": WARMUP_LIBRARY.get(token, WARMUP_LIBRARY.get(_nearest_warmup_category(token), [])),
            "exercises": exercises,
            "safety": _SAFETY_DEFAULT_BY_TOKEN.get(token, "Controlled tempo, full range of motion on every rep."),
        }
        if _injury_kw:
            day_entry["_injury_safety_note"] = (
                f"Some exercises were excluded or reduced today because you flagged: "
                f"{', '.join(sorted(_injury_kw))}. If this is inaccurate, update your "
                f"intake notes; if the area is still symptomatic, check with a "
                f"coach or doctor before pushing load through it."
            )
        # Additive, optional diagnostics from validator.py — only attached
        # when there's actually something to report, same convention as
        # _injury_safety_note above. Nothing here removes or renames an
        # existing key, so main.py/schemas/frontend consumers that don't
        # know about this key are unaffected.
        if repair_result["repairs"] or repair_result["rejected"] or day_warnings:
            day_entry["_validation"] = {
                "repairs": repair_result["repairs"],
                "rejected": repair_result["rejected"],
                "warnings": day_warnings,
            }
        # Additive, optional — same convention as _injury_safety_note /
        # _validation above. Purely informational surfacing of the
        # deterministic decision progression_engine.py already made; this
        # does not change exercise selection itself (that already happened
        # via the volume_multiplier applied inside _compute_day_plan above).
        if progression_context and (
            progression_context.get("deload_required") or progression_context.get("plateau_detected")
        ):
            notes = []
            if progression_context.get("deload_required"):
                notes.append("Deload cycle — volume reduced based on your last check-in.")
            if progression_context.get("plateau_detected"):
                notes.append("Progress has plateaued — consider pushing harder or varying exercises this cycle.")
            day_entry["_progression_note"] = " ".join(notes)
        days.append(day_entry)

    # Weekly cross-day checks (push/pull balance, split consistency,
    # squat/hip-dominant balance, weekly volume sanity) run once against
    # the final, post-repair per-day exercise lists. These are
    # informational only — validate_week() doesn't auto-repair anything,
    # since fixing a weekly imbalance means changing a different day's
    # selection, which isn't this function's job to reach into. Attached
    # to `profile` (mutated in place) rather than changed into the return
    # shape, matching the existing profile["_weekly_template"]/["_vol"]
    # convention elsewhere in this pipeline — main.py's caller keeps
    # getting a plain list of day dicts back, unchanged.
    week_result = validate_week(week_exercises_by_day, weekly_template, TOKEN_MUSCLE_MAP, experience_raw)
    if week_result["warnings"]:
        profile["_validation_warnings"] = week_result["warnings"]

    return days


# ── KNOWLEDGE RETRIEVER + TRAINER REVIEW + REVIEW VALIDATION ─────────────────
# New pipeline stage implementing the desired flow:
#   Split Engine -> Exercise Selector -> Progression -> Validator ->
#   Knowledge Retriever -> Trainer Review -> Python Review Validation ->
#   Final Workout.
#
# This wraps build_deterministic_workout_days() (left completely unmodified
# above — it's what tests/regression/run_regression.py calls directly and
# snapshots against a frozen baseline; a Trainer Review pass belongs AFTER
# that deterministic core, not inside it, so the regression baseline stays
# meaningful) and adds the Gemini review + re-validation pass on top as an
# additive stage.
#
# main.py's /result route already calls this (generate_dashboard() below
# still calls build_deterministic_workout_days() directly and has no
# Trainer Review pass — that's a separate, non-API code path, not the
# live /result route).
async def build_and_review_workout_days(
    profile: dict, weekly_template: list, vol: dict, llm_caller,
) -> dict:
    """
    Full pipeline: build the deterministic workout days (unchanged core),
    then run them through Trainer Review and Python Review Validation.

    Returns:
        {
          "days": list,              # final, post-review workout days
          "trainer_review": {
              "accepted": [...], "rejected": [...], "flags": [...],
              "parse_error": str | None,
          },
        }

    Fail-safe: if the Gemini call itself raises (network/timeout/API
    error), the deterministic days are returned unchanged with
    trainer_review=None — a Trainer Review outage must never block plan
    delivery, matching the fail-conservative principle used throughout
    this pipeline (equipment/injury filtering, validator.py repairs, etc).
    """
    days = build_deterministic_workout_days(profile, weekly_template, vol)

    notes_raw = profile.get("medical_notes") or profile.get("notes") or ""
    condition_flags = get_condition_intensity_flags(notes_raw)

    try:
        review = await trainer_review_mod.review_workout(
            days=days, profile=profile, condition_flags=condition_flags,
            llm_caller=llm_caller,
        )
    except Exception as e:  # noqa: BLE001 — see fail-safe note above
        return {
            "days": days,
            "trainer_review": {
                "accepted": [], "rejected": [], "flags": [],
                "parse_error": f"trainer_review_call_failed: {e}",
            },
        }

    result = review_validation_mod.apply_review(days=days, review=review, profile=profile)
    return {
        "days": result["days"],
        "trainer_review": {
            "accepted": result["accepted"],
            "rejected": result["rejected"],
            "flags": result["flags"],
            "parse_error": review.get("parse_error"),
        },
    }


def _resolve_exp_key(profile: dict) -> str:
    """Same beg/int/adv prefix-match logic used elsewhere, exposed as a
    small helper so both build_user_prompt() and generate_dashboard() stay
    in sync on which tier a client falls into."""
    exp_raw = str(profile.get("experience", "intermediate")).lower()
    for k in PROTEIN_MULTIPLIER:
        if exp_raw.startswith(k[:3]):
            return k
    return "intermediate"


# ── DIET RESTRICTION TOKENS ───────────────────────────────────────────────────
# Hard tokens injected into the prompt so the model cannot misread the setting.
DIET_TOKENS = {
    "vegetarian":     (
        "STRICT VEGETARIAN — absolutely NO eggs, NO meat, NO fish, NO seafood, NO chicken. "
        "Use only: dal, paneer, tofu, curd, milk, whey protein, nuts, seeds, legumes, "
        "sabzi, roti, rice, oats, fruits."
    ),
    "vegan":          (
        "STRICT VEGAN — NO dairy (no milk, no paneer, no curd, no whey), NO eggs, "
        "NO meat, NO fish. Use only: dal, tofu, soy milk, oats, nuts, seeds, legumes, "
        "fruits, vegetables, rice, roti."
    ),
    "non-vegetarian": (
        "NON-VEGETARIAN — may include chicken breast, eggs, fish (rohu/surmai), "
        "paneer, dal, curd, milk. Prefer lean proteins. No red meat unless specifically requested."
    ),
    "eggetarian":     (
        "EGGETARIAN — eggs are ALLOWED; absolutely NO chicken, NO meat, NO fish, NO seafood. "
        "Use eggs, paneer, dal, curd, milk, whey protein, nuts, seeds, legumes."
    ),
}

# ── WARMUP LIBRARY ───────────────────────────────────────────────────────────
# Specific warmup exercises keyed to workout type keywords
WARMUP_LIBRARY = {
    "push": [
        "5 min incline treadmill walk",
        "20× arm circles (forward + backward)",
        "15× shoulder rotations each side",
        "10× band pull-aparts",
        "10× push-up negatives (slow 4-sec down)",
    ],
    "pull": [
        "5 min rowing machine (low resistance)",
        "15× scapular retractions",
        "20× band pull-aparts",
        "10× doorway lat stretch each side",
        "10× face-pulls with resistance band",
    ],
    "legs": [
        "5 min stationary bike",
        "20× bodyweight squats",
        "15× leg swings each leg (front-back + lateral)",
        "10× hip circles each side",
        "10× glute bridges",
    ],
    "upper": [
        "5 min incline treadmill walk",
        "20× arm circles",
        "15× shoulder rotations each side",
        "10× band pull-aparts",
        "10× cat-cow neck rolls",
    ],
    "lower": [
        "5 min stationary bike",
        "20× bodyweight squats",
        "15× leg swings each leg",
        "10× hip circles each side",
        "10× ankle circles each side",
    ],
    "full": [
        "5 min light treadmill jog",
        "15× arm circles",
        "15× bodyweight squats",
        "10× hip circles each side",
        "10× inchworm stretches",
    ],
    "cardio": [
        "2 min brisk walk → 3 min light jog",
        "10× high knees",
        "10× butt kicks",
        "10× lateral shuffles each side",
    ],
    "rest": [],
}


# ── DETERMINISTIC WEEKLY SCHEDULE ─────────────────────────────────────────────
# Everything below removes the LLM's discretion over WHICH weekday is which
# type, and lets us overwrite the labelling fields after generation so the
# displayed split is always correct even if the LLM's own output drifted.
WEEKDAY_NAMES       = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
WEEKDAY_SHORT_UPPER = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]   # matches workout.days[].short
WEEKDAY_SHORT_TITLE = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]   # matches weekly_schedule[].short

TOKEN_TITLE = {
    "push":  "Push Day",
    "pull":  "Pull Day",
    "legs":  "Legs Day",
    "upper": "Upper Body",
    "lower": "Lower Body",
    "full":  "Full Body",
    "cardio": "Cardio Day",
}

TOKEN_MUSCLE_LABEL = {
    "push":  "Chest · Shoulders · Triceps",
    "pull":  "Back · Biceps",
    "legs":  "Quads · Hamstrings · Glutes · Calves",
    "upper": "Chest · Back · Shoulders · Arms",
    "lower": "Quads · Hamstrings · Glutes · Calves",
    "full":  "Full Body",
    "cardio": "Cardio · Core",
}


def _nearest_warmup_category(token: str) -> str:
    """
    Many new day-type tokens (chest_triceps, torso, heavy_push, etc.) don't
    have their own bespoke WARMUP_LIBRARY entry — rather than silently
    falling back to an empty warmup list, pick the closest existing category
    (push/pull/legs/upper/lower/full/cardio) based on which muscles that
    token actually trains, so every lifting day still gets a sensible warmup.
    """
    if token in WARMUP_LIBRARY:
        return token
    muscles = set(TOKEN_MUSCLE_MAP.get(token, []))
    if not muscles:
        return "cardio"
    if "legs" in muscles or "calves" in muscles:
        if len(muscles) <= 2:
            return "legs"
        return "full"
    if {"chest", "shoulders", "triceps"} & muscles and not ({"back", "biceps"} & muscles):
        return "push"
    if {"back", "biceps"} & muscles and not ({"chest", "shoulders", "triceps"} & muscles):
        return "pull"
    if len(muscles) >= 4:
        return "upper"
    return "full"


def _prettify_token(token: str) -> str:
    """'chest_triceps' -> 'Chest + Triceps'; 'ppl_x2'-style single words just
    Title-Case normally. Used as the fallback for any new day-type token that
    doesn't have a bespoke TOKEN_TITLE/TOKEN_MUSCLE_LABEL entry."""
    words = token.split("_")
    return " + ".join(w.capitalize() for w in words)


def _build_weekly_template(sequence: list, training_days_per_week: int) -> list:
    """
    Deterministically decide which of the 7 weekdays are training vs rest,
    and which split-day token each training slot gets — BEFORE the LLM ever
    runs. This is what removes "rest day placement" from the model's job
    entirely; it only has to fill in exercises for a schedule we've already
    fixed. Rest days are spread as evenly as possible rather than bunched.
    """
    training_days_per_week = max(0, min(7, training_days_per_week))
    rest_days = 7 - training_days_per_week

    rest_positions = set()
    for i in range(1, rest_days + 1):
        pos = round(i * 7 / (rest_days + 1)) - 1
        rest_positions.add(max(0, min(6, pos)))
    # Rounding can occasionally collide and leave us one rest slot short —
    # backfill deterministically (first free slot) rather than under-resting.
    i = 0
    while len(rest_positions) < rest_days and i < 7:
        if i not in rest_positions:
            rest_positions.add(i)
        i += 1

    template = []
    seq_i = 0
    for day_i in range(7):
        if day_i in rest_positions:
            template.append("rest")
        else:
            template.append(sequence[seq_i % len(sequence)] if sequence else "full")
            seq_i += 1
    return template


def apply_deterministic_day_labels(data: dict, template: list) -> dict:
    """
    Overwrite name/type/short/is_rest for all 7 days (and weekly_schedule)
    using the SAME template that was injected into the prompt — so the split
    shown to the member is always correct regardless of what the LLM actually
    wrote in these fields. Exercise/warmup content from the LLM is preserved
    as-is (position-matched by weekday); only the labelling is forced.
    """
    data.setdefault("workout", {})
    days = data["workout"].get("days", [])

    fixed_days = []
    fixed_schedule = []
    for i, token in enumerate(template):
        is_rest = token == "rest"
        source = days[i] if i < len(days) else {}
        day = dict(source)  # keep whatever exercises/warmup/safety the LLM wrote
        day["short"] = WEEKDAY_SHORT_UPPER[i]
        day["is_rest"] = is_rest
        if is_rest:
            day["name"] = WEEKDAY_NAMES[i]
            day["type"] = "Rest Day"
            day.pop("exercises", None)
            day.pop("warmup_exercises", None)
        else:
            day["name"] = f"{WEEKDAY_NAMES[i]}: {TOKEN_TITLE.get(token, _prettify_token(token))}"
            day["type"] = TOKEN_MUSCLE_LABEL.get(token, _prettify_token(token))
        fixed_days.append(day)

        fixed_schedule.append({
            "short": WEEKDAY_SHORT_TITLE[i],
            "label": "Rest" if is_rest else TOKEN_TITLE.get(token, _prettify_token(token)).replace(" Day", ""),
            "is_rest": is_rest,
            "bar_width": 15 if is_rest else 88,
        })

    data["workout"]["days"] = fixed_days
    data["workout"]["weekly_schedule"] = fixed_schedule
    return data


def workout_matches_template(data: dict, template: list) -> bool:
    """
    Content-level check (separate from the label override above): for every
    non-rest day, confirm the LLM's OWN exercise 'muscle' fields mention at
    least one muscle genuinely expected for that day's token. This is what
    actually catches "every day came back as generic full-body exercises"
    before a member ever sees it — the label override alone can't catch this,
    since it only fixes the text, not whether the exercises underneath match.
    """
    days = data.get("workout", {}).get("days", [])
    if len(days) < len(template):
        return False
    for i, token in enumerate(template):
        if token == "rest":
            continue
        expected = TOKEN_MUSCLE_MAP.get(token, [])
        if not expected:
            continue
        exercises = days[i].get("exercises", []) if i < len(days) else []
        if not exercises:
            return False
        muscle_text = " ".join(str(e.get("muscle", "")).lower() for e in exercises)
        if not any(m in muscle_text for m in expected):
            return False
    return True


# ── CALORIE & MACRO CALCULATOR ────────────────────────────────────────────────
def _calculate_macros(profile: dict) -> dict:
    """
    Returns a dict with:
      bmr, tdee, target_kcal, protein_g_low, protein_g_high,
      protein_g_mid, carb_g, fat_g, phase_label
    """
    weight  = float(profile["current_weight_kg"])
    height  = float(profile["height_cm"])
    age     = int(profile["age"])
    gender  = profile["gender"].lower()
    goal    = profile["goal"].lower()
    activity = profile.get("activity_level_factor", 1.55)
    experience = profile.get("experience", "intermediate").lower()

    # Mifflin-St Jeor BMR
    if gender == "female":
        bmr = 10 * weight + 6.25 * height - 5 * age - 161
    else:
        bmr = 10 * weight + 6.25 * height - 5 * age + 5

    tdee = bmr * activity

    # BMI (for protein multiplier weighting)
    height_m = height / 100
    bmi = weight / (height_m ** 2) if height_m > 0 else 22.0

    # Protein multiplier band + dynamic single value within it
    exp_key = "intermediate"
    for k in PROTEIN_MULTIPLIER:
        if experience.startswith(k[:3]):   # beg / int / adv prefix match
            exp_key = k
            break
    p_low, p_high = PROTEIN_MULTIPLIER[exp_key]
    p_dynamic = _protein_multiplier(exp_key, profile.get("activity_key", "moderate"), bmi)
    protein_g_low  = math.ceil(weight * p_low)
    protein_g_high = math.ceil(weight * p_high)
    protein_g_mid  = round(weight * p_dynamic)

    # Calorie target
    is_recovery = any(t in goal for t in ("recovery", "recover", "deload", "injury", "rehab"))
    if "fat loss" in goal or "weight loss" in goal or "cut" in goal:
        target_kcal = round(tdee * 0.82)   # ~18% deficit
        phase_label = "Calorie deficit"
    elif is_recovery:
        # Recovery/deload/rehab clients should NOT be in a deficit or surplus —
        # under-fueling impairs tissue repair, over-fueling isn't the goal
        # either. Hold at maintenance and let training volume (handled in
        # split_engine.py / EXERCISE_VOLUME) do the actual de-loading.
        target_kcal = round(tdee)
        phase_label = "Recovery — maintenance calories"
    elif "muscle" in goal or "bulk" in goal or "gain" in goal or "mass" in goal:
        target_kcal = round(tdee * 1.10)   # ~10% surplus
        phase_label = "Lean bulk"
    else:
        target_kcal = round(tdee)
        phase_label = "Maintenance"

    # Carbs & fats from remaining calories after protein
    protein_kcal = protein_g_mid * 4
    remaining    = target_kcal - protein_kcal
    fat_g        = round((remaining * 0.28) / 9)   # 28% of remaining from fat
    carb_g       = round((remaining * 0.72) / 4)   # 72% of remaining from carb

    return {
        "bmr":           round(bmr),
        "bmi":           round(bmi, 1),
        "protein_multiplier": p_dynamic,
        "tdee":          round(tdee),
        "target_kcal":   target_kcal,
        "protein_g_low": protein_g_low,
        "protein_g_high":protein_g_high,
        "protein_g_mid": protein_g_mid,
        "carb_g":        carb_g,
        "fat_g":         fat_g,
        "phase_label":   phase_label,
    }


# ── ACTIVITY FACTOR MAP ───────────────────────────────────────────────────────
ACTIVITY_FACTOR = {
    "sedentary":   1.2,
    "light":       1.375,
    "moderate":    1.55,
    "very_active": 1.725,
    "extreme":     1.9,
}

ACTIVITY_LABEL = {
    "sedentary":   "Sedentary (desk job, little exercise)",
    "light":       "Lightly active (1–3 gym sessions/week)",
    "moderate":    "Moderately active (3–5 gym sessions/week)",
    "very_active": "Very active (6–7 gym sessions/week)",
    "extreme":     "Extremely active (physical job + daily training)",
}


# ── SYSTEM PROMPT (structural contract only) ──────────────────────────────────
SYSTEM_PROMPT = """
You are an expert fitness coach and sports dietitian specialising in Indian gym clients.

CRITICAL OUTPUT RULES — READ BEFORE GENERATING:
1. Output ONLY a raw JSON object. Nothing before it, nothing after it.
2. Do NOT wrap JSON in markdown code fences (no ```json, no ```, no backticks of any kind).
3. Do NOT add comments inside the JSON (no // or /* */ comments).
4. Do NOT add trailing commas after the last item in any array or object.
5. All string values must use straight double-quotes only (no smart/curly quotes).
6. Boolean values must be lowercase: true or false.
7. Null values must be written as null (not None/NULL).
8. Every required field in the schema below MUST be present — do not skip any key.
9. If you are unsure of a value, use a sensible default rather than omitting the field.
10. Your response must parse successfully with Python's json.loads() with zero modifications.
11. Output the JSON object EXACTLY ONCE. Do NOT repeat, restate, or duplicate the object.
    After the final closing '}' of the single JSON object, STOP immediately — emit no further text.
12. Do NOT repeat a key within the same object (each key appears at most once).

SCHEMA (copy key names precisely):

{
  "user": {
    "name": "string",
    "current_weight": <integer kg>,
    "target_weight": "string e.g. 72–74"
  },
  "plan": {
    "goal_label":      "string e.g. Fat Loss Plan",
    "daily_calories":  <integer>,
    "protein_range":   "string e.g. 88–97g",
    "daily_protein_g": <integer — midpoint>,
    "weight_to_lose":  "string e.g. ~6–8 kg to lose",
    "calorie_phase":   "string e.g. Calorie deficit"
  },
  "workout": {
    "weekly_schedule": [],
    "days": []
    // Both are populated by Python after generation, not by you.
    // Leave these exactly as empty arrays — do not invent workout content.
  },
  "diet": {
    "meals": [
      {
        "id":        "breakfast",
        "tab_label": "Breakfast",
        "title":     "Breakfast",
        "kcal_range":"600–650",
        "options": [
          {
            "food":      "80g Oats + 300ml Milk + 1 Banana",
            "kcal":      620,
            "protein_g": 24,
            "carb_g":    90,
            "fat_g":     8
          },
          {
            "food":      "3 Egg Whites + 1 Whole Egg Bhurji + 2 Multigrain Roti",
            "kcal":      610,
            "protein_g": 28,
            "carb_g":    72,
            "fat_g":     16
          },
          {
            "food":      "150g Greek-style Curd + 40g Granola + 1 Apple",
            "kcal":      600,
            "protein_g": 22,
            "carb_g":    85,
            "fat_g":     12
          }
        ]
        // EXACTLY 3 distinct options per meal — see MEAL RULES below
      }
      // 5 meals: breakfast, mid, lunch, post, dinner
    ]
  },
  "recovery": {
    "daily_nonneg": [
      { "icon": "💧", "value": "3.5–4 L", "label": "Water" },
      { "icon": "👟", "value": "10K",     "label": "Steps" },
      { "icon": "🌙", "value": "8–9 h",   "label": "Sleep" },
      { "icon": "☀️", "value": "A.M.",    "label": "Sunlight" }
    ],
    "key_numbers": [
      { "icon": "💧", "value": "3.5–4 L", "label": "Water daily" },
      { "icon": "👟", "value": "10,000",  "label": "Steps daily" },
      { "icon": "🌙", "value": "7.5–9 h", "label": "Sleep target" },
      { "icon": "⏰", "value": "2 PM",    "label": "Last caffeine" }
    ],
    "tip_sections": [
      {
        "title": "Sleep protocol",
        "tips": ["tip1", "tip2", "tip3", "tip4"]
      },
      {
        "title": "Active recovery",
        "tips": ["tip1", "tip2", "tip3", "tip4"]
      }
    ]
  }
}

FINAL REMINDER: Output ONLY the raw JSON object, EXACTLY ONCE.
The very first character of your response must be '{' and the very last must be '}'.
Do not write anything after that final '}'.
"""


# ── BUILD USER PROMPT (all personalisation lives here) ───────────────────────
def build_user_prompt(profile: dict) -> str:
    """
    Convert a client intake profile dict into a fully personalised LLM prompt.
    Every token the model needs to produce a correct, personalised plan is
    computed HERE in Python and injected as hard values — not left to the LLM
    to interpret from vague labels.
    """
    # ── 1. Resolve activity factor & attach to profile for macro calc
    activity_key = profile.get("activity_key", "moderate")
    profile["activity_level_factor"] = ACTIVITY_FACTOR.get(activity_key, 1.55)

    # ── 2. Compute macros
    m = _calculate_macros(profile)

    # ── 3. Diet restriction token
    diet_raw = profile.get("diet_pref", "non-vegetarian").lower().strip()
    # Exact match first, then fuzzy — checked MOST-SPECIFIC key first.
    # BUG FIX: the old loop iterated DIET_TOKENS in insertion order and used
    # a plain substring test ("key in diet_raw or diet_raw in key"). Since
    # "vegetarian" is a substring of "non-vegetarian" AND of "eggetarian",
    # and "vegetarian" is the first key in the dict, EVERY non-vegetarian and
    # eggetarian selection was silently matching "vegetarian" first and
    # being served a strict-vegetarian diet token — selecting non-veg had no
    # effect on the generated plan. Checking specific keys before the
    # generic "vegetarian" fixes this.
    diet_token = DIET_TOKENS["non-vegetarian"]
    if diet_raw in DIET_TOKENS:
        diet_token = DIET_TOKENS[diet_raw]
    else:
        for key in ("non-vegetarian", "eggetarian", "vegan", "vegetarian"):
            if key in diet_raw or diet_raw in key:
                diet_token = DIET_TOKENS[key]
                break

    # ── 4. Experience token
    exp_raw = profile.get("experience", "intermediate").lower()
    exp_key = _resolve_exp_key(profile)
    protein_mult_str = (
        f"{m['protein_multiplier']} g/kg "
        f"(computed for {exp_key} tier, band {PROTEIN_MULTIPLIER[exp_key][0]}–{PROTEIN_MULTIPLIER[exp_key][1]} g/kg, "
        f"weighted by activity level + BMI {m['bmi']})"
    )
    vol = EXERCISE_VOLUME[exp_key]

    # ── 5. Warmup hints per training days
    training_days_per_week = int(profile.get("days_per_week", 4))
    duration    = profile.get("session_duration", "45–60 min")
    region      = profile.get("region", "India")
    budget      = profile.get("budget", "medium")
    allergies   = profile.get("allergies", "none")
    # STEP 1 (Food Allergy Enforcement): expand the raw free-text field into
    # a structured, synonym-expanded constraint and stash it on the profile
    # so the post-generation validation layer (enforce_schema ->
    # allergy_engine.enforce_allergy_safety) re-checks against the exact
    # same banned-term list the prompt was built from.
    parsed_allergies = allergy_engine.parse_allergies(allergies)
    profile["_parsed_allergies"] = parsed_allergies
    allergy_prompt_block = allergy_engine.build_allergy_prompt_block(parsed_allergies)
    target_wt   = profile.get("target_weight_kg", "—")
    medical     = profile.get("medical_notes", "none")
    meals_count = int(profile.get("meals_per_day", 5))

    # ── 6. Goal sentence
    goal = profile.get("goal", "fat loss")
    goal_lower = goal.lower()
    is_recovery_goal = any(t in goal_lower for t in ("recovery", "recover", "deload", "injury", "rehab"))
    if "fat loss" in goal_lower or "weight loss" in goal_lower or "cut" in goal_lower:
        goal_label = "Fat Loss Plan"
    elif is_recovery_goal:
        goal_label = "Recovery Plan"
    elif "muscle" in goal_lower or "bulk" in goal_lower or "gain" in goal_lower:
        goal_label = "Muscle Gain Plan"
    elif "maintain" in goal_lower:
        goal_label = "Maintenance Plan"
    else:
        goal_label = "Fat Loss Plan"

    # ── 7. Profile-aware split recommendation (experience + days + duration +
    #      goal + BMI + activity — see split_engine.py for the full decision
    #      tree). This replaces any static experience→template lookup so two
    #      clients at the same experience/day-count but different goals or
    #      session lengths can land on genuinely different splits.
    split = recommend_split({
        "experience":         exp_raw,
        "days_per_week":      training_days_per_week,
        "session_duration":   duration,
        "goal":               goal,
        "height_cm":          profile.get("height_cm", 170),
        "current_weight_kg":  profile.get("current_weight_kg", 70),
        "activity_key":       activity_key,
    })
    split_sequence_str = " → ".join(split["sequence"])

    # ── 7a. DETERMINISTIC weekly schedule — decides which weekday is which
    #      type (and where rest days fall) in Python, BEFORE the LLM runs.
    #      Exposed on `profile` (mutated in place, same pattern as
    #      activity_level_factor above) so main.py can reuse the exact same
    #      template after generation to force-correct labels and validate content.
    weekly_template = _build_weekly_template(split["sequence"], training_days_per_week)
    profile["_weekly_template"] = weekly_template
    profile["_vol"] = vol
    weekly_schedule_str = "\n".join(
        f"  {WEEKDAY_NAMES[i]}: {'REST' if tok == 'rest' else tok.upper()}"
        for i, tok in enumerate(weekly_template)
    )

    # ── 8. PRECOMPUTED per-day exercise checklist — no LLM math required.
    #      This is the authoritative fix for "no squats on Legs day" and
    #      "only 1 chest exercise on Push day": counts + compounds are decided
    #      here in Python and handed to the LLM as a literal fill-in list.
    day_plan_table = _render_day_plan_table(
        weekly_template, vol, exp_key,
        goal=goal, session_minutes=_session_minutes(duration),
    )

    return f"""
CLIENT PROFILE — read every line carefully; ALL of it must be reflected in the JSON you return.

━━ IDENTITY ━━
Name:           {profile['name']}
Age:            {profile['age']} years old
Gender:         {profile['gender']}
Height:         {profile['height_cm']} cm
Current weight: {profile['current_weight_kg']} kg
Target weight:  {target_wt} kg
Goal:           {goal}  →  generate a "{goal_label}"

━━ EXPERIENCE & TRAINING ━━
Experience level: {profile.get('experience', 'Intermediate')}
Training days/week: {training_days_per_week} days
Session duration:   {duration}
Equipment:          {profile.get('equipment', 'full gym')}
Activity level:     {ACTIVITY_LABEL.get(activity_key, activity_key)}

━━ CALCULATED TARGETS (USE THESE EXACT NUMBERS) ━━
BMR:              {m['bmr']} kcal
TDEE:             {m['tdee']} kcal
BMI:              {m['bmi']}
Daily calorie target: {m['target_kcal']} kcal  ({m['phase_label']})
Protein multiplier:   {protein_mult_str}
Protein target:   {m['protein_g_mid']} g/day — this is the EXACT target, already calculated
                  from bodyweight × the activity/BMI-weighted multiplier above
                  (full tier band for reference only: {m['protein_g_low']}–{m['protein_g_high']} g/day)
Carbohydrate target:  {m['carb_g']} g/day
Fat target:           {m['fat_g']} g/day

Set plan.daily_calories  = {m['target_kcal']}
Set plan.protein_range   = "{m['protein_g_low']}–{m['protein_g_high']}g"
Set plan.daily_protein_g = {m['protein_g_mid']}
Set plan.calorie_phase   = "{m['phase_label']}"
Set plan.goal_label      = "{goal_label}"

━━ DIET — CRITICAL RESTRICTION ━━
{diet_token}
Meals per day: {meals_count}  (spread the {m['target_kcal']} kcal across exactly {meals_count} meals)
Region / cuisine preference: {region} — use locally available kirana/sabzi mandi ingredients
Food budget: {budget}

━━ ALLERGIES / INTOLERANCES ━━
{allergy_prompt_block}

For EVERY meal option supply these EXACT fields:
  "food": "<ingredient list with grams>",
  "kcal": <integer>,
  "protein_g": <integer>,
  "carb_g": <integer>,
  "fat_g": <integer>

MEAL OPTION RULES (mandatory):
- Each meal slot (breakfast, mid, lunch, post, dinner) MUST contain EXACTLY 3 entries in "options".
- The 3 options for a given meal must use genuinely different core ingredients/dishes from
  each other (not the same dish with a swapped garnish) — give the client real variety to
  rotate through during the week.
- All 3 options for a meal must independently land within roughly ±10% of that meal's target
  kcal/protein share, so any one of the 3 is a valid swap-in.
- Do not skip this for any of the 5 meals — 5 meals × 3 options = 15 total option objects in "diet.meals[].options".

The sum of protein_g across the day's meals (best option of each) must hit ~{m['protein_g_mid']} g.
The sum of kcal must be within ±80 kcal of {m['target_kcal']}.

━━ WORKOUT ━━
Workout content (exercise selection, sets/reps/rest, warmups) is generated
entirely in Python, not by you. Leave workout.weekly_schedule and
workout.days as empty arrays in your output — do not invent any exercises,
schedule, or workout content of any kind.

━━ MEDICAL / OTHER NOTES ━━
{medical}

━━ INSTRUCTIONS ━━
1. Every value you output must be consistent with the client profile above.
2. The diet options must strictly respect the diet restriction token — do not add ANY forbidden food.
2a. The diet options must ALSO strictly respect the allergy exclusion list above — this is a
    safety constraint, not a preference, and takes priority over cuisine/regional authenticity.
3. The macro numbers (kcal, protein_g, carb_g, fat_g) in each meal option must be realistic and add up.
4. Use only the schema defined by the system prompt — no extra keys, no missing keys.
5. Leave workout.weekly_schedule and workout.days as empty arrays — that content is Python-generated.
6. Output the JSON object EXACTLY ONCE. Stop immediately after the final closing brace.

Generate the complete fitness dashboard JSON now.
"""


# ── PARSE LLM RESPONSE ────────────────────────────────────────────────────────
def _extract_first_json_object(text: str):
    """
    Scan `text` and return the substring of the FIRST complete, brace-balanced
    JSON object (from its opening '{' to the matching '}'), correctly skipping
    braces that appear inside string literals. Returns None if no balanced
    object is found.

    This replaces a greedy `\\{.*\\}` regex, which grabbed from the first '{'
    to the LAST '}' in the whole response — so when the LLM appended a second
    object or repeated a block (a common local-LLM failure), everything got
    swept in and json.loads() raised "Extra data". Brace-counting stops at the
    end of the first valid object and discards any trailing junk.
    """
    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escape = False

    for i in range(start, len(text)):
        ch = text[i]

        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue

        # not inside a string
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                # matched the opening brace — this is the end of the first object
                return text[start:i + 1]

    return None  # unbalanced / never closed


def parse_llm_json(raw: str) -> dict:
    text = raw

    text = re.sub(r"```(?:json)?\s*", "", text)
    text = text.replace("```", "").strip()

    # Extract EXACTLY the first balanced {...} object (ignores any duplicate
    # object or trailing prose the LLM appended after it).
    candidate = _extract_first_json_object(text)
    if candidate is None:
        raise ValueError(
            f"No JSON object found in LLM response.\n\nRaw output:\n{raw[:500]}"
        )
    text = candidate

    text = re.sub(r"(?<!:)//[^\n\"]*", "", text)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)

    text = re.sub(r"\bTrue\b", "true", text)
    text = re.sub(r"\bFalse\b", "false", text)
    text = re.sub(r"\bNone\b", "null", text)
    text = re.sub(r"\bNULL\b", "null", text)

    for ch in "\u201c\u201d\u2018\u2019\u00ab\u00bb":
        text = text.replace(ch, '"')

    text = re.sub(r",\s*([}\]])", r"\1", text)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    try:
        single_to_double = re.sub(
            r"'([^'\\]*(?:\\.[^'\\]*)*)'",
            lambda m: '"' + m.group(1).replace('"', '\\"') + '"',
            text,
        )
        single_to_double = re.sub(r",\s*([}\]])", r"\1", single_to_double)
        # brace-balance again after the quote swap, in case the swap re-exposed
        # a trailing duplicate that was previously masked
        rebalanced = _extract_first_json_object(single_to_double)
        if rebalanced is not None:
            single_to_double = rebalanced
        return json.loads(single_to_double)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"LLM returned JSON that could not be auto-repaired: {e}\n\n"
            f"Cleaned text (first 800 chars):\n{text[:800]}\n\n"
            f"Original raw output (first 500 chars):\n{raw[:500]}"
        )


# ── SCHEMA ENFORCER ───────────────────────────────────────────────────────────
_RECOVERY_DEFAULT = {
    "daily_nonneg": [
        {"icon": "💧", "value": "3.5–4 L", "label": "Water"},
        {"icon": "👟", "value": "10K",     "label": "Steps"},
        {"icon": "🌙", "value": "8–9 h",   "label": "Sleep"},
        {"icon": "☀️", "value": "A.M.",    "label": "Sunlight"},
    ],
    "key_numbers": [
        {"icon": "💧", "value": "3.5–4 L", "label": "Water daily"},
        {"icon": "👟", "value": "10,000",  "label": "Steps daily"},
        {"icon": "🌙", "value": "7.5–9 h", "label": "Sleep target"},
        {"icon": "⏰", "value": "2 PM",    "label": "Last caffeine"},
    ],
    "tip_sections": [
        {
            "title": "Sleep protocol",
            "tips": [
                "No screens 60 min before bed",
                "No caffeine after 2 PM",
                "Morning sunlight within 30 min of waking",
                "Cool room, dark environment = deeper sleep",
            ],
        },
        {
            "title": "Active recovery",
            "tips": [
                "8000–10000 steps daily — even on rest days",
                "Foam roll after training sessions",
                "Light walk or mobility on rest days",
                "If soreness is extreme, skip the day",
            ],
        },
    ],
}


def enforce_schema(data: dict, profile: dict | None = None) -> dict:
    data.setdefault("user", {})
    data.setdefault("plan", {})
    data.setdefault("workout", {})
    data.setdefault("diet", {"meals": []})
    data.setdefault("recovery", {})

    for key, default in _RECOVERY_DEFAULT.items():
        data["recovery"].setdefault(key, default)

    plan_defaults = {
        "goal_label":      "Fitness Plan",
        "daily_calories":  2000,
        "protein_range":   "120–150g",
        "daily_protein_g": 135,
        "weight_to_lose":  "—",
        "calorie_phase":   "Maintenance",
    }
    for key, val in plan_defaults.items():
        data["plan"].setdefault(key, val)

    user_defaults = {
        "name":           "User",
        "current_weight": 0,
        "target_weight":  "—",
    }
    for key, val in user_defaults.items():
        data["user"].setdefault(key, val)

    data["workout"].setdefault("weekly_schedule", [])
    data["workout"].setdefault("days", [])

    # Ensure warmup_exercises exists on every non-rest day
    # min_expected is derived from the client's experience tier (beginner=3,
    # intermediate=5, advanced=6) so a beginner day landing exactly on its
    # cap of 3 is never wrongly flagged. Falls back to 3 (the lowest/safest
    # floor) if no profile was supplied.
    exp_key = _resolve_exp_key(profile) if profile else "beginner"
    min_expected = MIN_EXERCISES.get(exp_key, 3)
    for day in data["workout"].get("days", []):
        if not day.get("is_rest", False):
            day.setdefault("warmup_exercises", [])
            day.setdefault("exercises", [])
            if len(day["warmup_exercises"]) == 0:
                day["_missing_warmup_warning"] = (
                    "No warmup_exercises were generated for this training day "
                    "despite being mandatory — the LLM dropped this field."
                )
            if len(day["exercises"]) < min_expected:
                day["_low_volume_warning"] = (
                    f"Only {len(day['exercises'])} exercise(s) generated for this day — "
                    f"below the requested minimum of {min_expected}. Consider regenerating."
                )

    # Ensure each meal has full macro fields
    for meal in data["diet"].get("meals", []):
        meal.setdefault("options", [])
        meal.setdefault("kcal_range", "—")
        if len(meal["options"]) < 3:
            meal["_low_variety_warning"] = (
                f"Only {len(meal['options'])} option(s) generated for "
                f"{meal.get('title', meal.get('id', 'this meal'))} — expected 3."
            )
        for opt in meal["options"]:
            opt.setdefault("kcal", 0)
            opt.setdefault("protein_g", 0)
            opt.setdefault("carb_g", 0)
            opt.setdefault("fat_g", 0)

    # STEP 1 (Food Allergy Enforcement) — independent Python-side re-check
    # of every meal option against the disclosed allergens, run here so it
    # applies uniformly to every caller of enforce_schema (main.py's
    # /result _run(), generate_dashboard(), generate_dashboard_with_review()).
    # No-op if profile is None or no allergies were disclosed.
    data = allergy_engine.enforce_allergy_safety(data, profile)

    return data


# ── RENDER ────────────────────────────────────────────────────────────────────
def render_dashboard(data: dict) -> str:
    if not (TEMPLATE_DIR / TEMPLATE_FILE).is_file():
        raise FileNotFoundError(
            f"Template '{TEMPLATE_FILE}' not found at {TEMPLATE_DIR}. "
            f"Checked: {[str(p) for p in _CANDIDATE_TEMPLATE_DIRS]}. "
            f"Make sure the Templates folder is committed to the repo and its "
            f"name/casing matches exactly (Linux/Render is case-sensitive)."
        )
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=False,
    )
    tmpl = env.get_template(TEMPLATE_FILE)
    return tmpl.render(**data)


# ── MAIN PIPELINE ─────────────────────────────────────────────────────────────
def generate_dashboard(profile: dict, llm_caller) -> str:
    from .safety_engine import (
        safety_gate, emergency_block_html,
        DEFAULT_SAFE_SEQUENCE, DEFAULT_SAFE_VOL,
    )

    # KB File 12: runs before anything else — no LLM call, no exercise
    # selection, until this clears. See main.py's /result for the primary
    # entry point; this mirrors the same gate for any other caller of
    # generate_dashboard().
    gate = safety_gate(profile)
    if gate["action"] == "block":
        return emergency_block_html(gate["messages"])

    user_prompt  = build_user_prompt(profile)

    if gate["action"] == "default_template":
        profile["_weekly_template"] = DEFAULT_SAFE_SEQUENCE
        profile["_vol"] = DEFAULT_SAFE_VOL

    raw_response = llm_caller(SYSTEM_PROMPT, user_prompt)
    data = parse_llm_json(raw_response)

    # Workout content is built entirely in Python — the LLM's own
    # workout.days output (left as an empty array per the prompt) is
    # ignored, not merged. build_user_prompt() stashed the weekly
    # template + volume table on `profile` above so we don't recompute
    # the split/volume logic a second time here.
    weekly_template = profile.get("_weekly_template") or _build_weekly_template(
        recommend_split({
            "experience": profile.get("experience", "intermediate"),
            "days_per_week": int(profile.get("days_per_week", 4)),
            "session_duration": profile.get("session_duration", "45–60 min"),
            "goal": profile.get("goal", "fat loss"),
            "height_cm": profile.get("height_cm", 170),
            "current_weight_kg": profile.get("current_weight_kg", 70),
            "activity_key": profile.get("activity_key", "moderate"),
        })["sequence"],
        int(profile.get("days_per_week", 4)),
    )
    vol = profile.get("_vol") or EXERCISE_VOLUME[_resolve_exp_key(profile)]

    data.setdefault("workout", {})
    data["workout"]["days"] = build_deterministic_workout_days(profile, weekly_template, vol)

    data = enforce_schema(data, profile)
    data = apply_deterministic_day_labels(data, weekly_template)
    return render_dashboard(data)


async def generate_dashboard_with_review(profile: dict, llm_caller, review_llm_caller=None) -> str:
    """
    Async sibling of generate_dashboard() that runs the workout through
    Trainer Review (see build_and_review_workout_days above) before
    rendering. generate_dashboard() itself is left unmodified — its
    llm_caller is called synchronously with a different argument order
    (system, user) and this file doesn't know every existing caller of it,
    so this is an additive new entry point rather than a signature change
    to an existing one.

    `review_llm_caller` defaults to `llm_caller` if not given, and must be
    an async callable matching app.ollama_client.generate_with_ollama's
    signature: `async def f(prompt: str, system: str | None = None) -> str`.
    """
    from .safety_engine import (
        safety_gate, emergency_block_html,
        DEFAULT_SAFE_SEQUENCE, DEFAULT_SAFE_VOL,
    )

    gate = safety_gate(profile)
    if gate["action"] == "block":
        return emergency_block_html(gate["messages"])

    user_prompt = build_user_prompt(profile)

    if gate["action"] == "default_template":
        profile["_weekly_template"] = DEFAULT_SAFE_SEQUENCE
        profile["_vol"] = DEFAULT_SAFE_VOL

    raw_response = llm_caller(SYSTEM_PROMPT, user_prompt)
    data = parse_llm_json(raw_response)

    weekly_template = profile.get("_weekly_template") or _build_weekly_template(
        recommend_split({
            "experience": profile.get("experience", "intermediate"),
            "days_per_week": int(profile.get("days_per_week", 4)),
            "session_duration": profile.get("session_duration", "45–60 min"),
            "goal": profile.get("goal", "fat loss"),
            "height_cm": profile.get("height_cm", 170),
            "current_weight_kg": profile.get("current_weight_kg", 70),
            "activity_key": profile.get("activity_key", "moderate"),
        })["sequence"],
        int(profile.get("days_per_week", 4)),
    )
    vol = profile.get("_vol") or EXERCISE_VOLUME[_resolve_exp_key(profile)]

    reviewed = await build_and_review_workout_days(
        profile, weekly_template, vol, review_llm_caller or llm_caller,
    )
    data.setdefault("workout", {})
    data["workout"]["days"] = reviewed["days"]
    profile["_trainer_review"] = reviewed["trainer_review"]

    data = enforce_schema(data, profile)
    data = apply_deterministic_day_labels(data, weekly_template)
    return render_dashboard(data)