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
──────────────────────────────────────────────────────────────────────────────
"""

import json
import math
import re
from pathlib import Path
from jinja2 import Environment, FileSystemLoader


# ── TEMPLATE DIR ─────────────────────────────────────────────────────────────
TEMPLATE_DIR = Path(__file__).parent.parent / "Templates"
TEMPLATE_FILE = "result.html"


# ── PROTEIN MULTIPLIER TABLE ──────────────────────────────────────────────────
# Keys match the <select> values in dashbord.html  (Beginner / Intermediate / Advanced)
# These are the OUTER bounds for each tier. The actual multiplier used is computed
# dynamically within this band based on activity level + BMI — see _protein_multiplier().
# NOTE: this is the single source of truth for these numbers. dashbord.html (both
# copies — root and promptgen-backend/) has its OWN duplicate PROTEIN_RANGE /
# PROTEIN_MIDPOINT constants used only for the client-side preview card; keep them
# in sync with this table by hand whenever it changes.
PROTEIN_MULTIPLIER = {
    "beginner":     (1.0, 1.0),    # g of protein per kg of body-weight — flat, not a band
    "intermediate": (1.1, 1.3),
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


def _resolve_exp_key(experience_raw: str) -> str:
    """
    Single source of truth for turning a free-text experience value (from the
    form, e.g. "Beginner") into one of the PROTEIN_MULTIPLIER / EXERCISE_VOLUME
    keys. Previously this prefix-matching logic was copy-pasted in three
    different places (_calculate_macros, build_user_prompt, enforce_schema)
    and could drift out of sync with each other — now there's one place to fix.
    """
    exp_raw = (experience_raw or "intermediate").lower().strip()
    for k in PROTEIN_MULTIPLIER:
        if exp_raw.startswith(k[:3]):   # beg / int / adv prefix match
            return k
    return "intermediate"


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
        "exercises_per_day": "4–5",
        "sets_per_exercise":  "2–3",
        "rest_between_sets":  "60–90 sec",
        "intensity_note": (
            "Focus on form and machine/cable-based movements. Avoid advanced "
            "techniques (no drop sets, no supersets, no failure training)."
        ),
    },
    "intermediate": {
        "exercises_per_day": "5–7",
        "sets_per_exercise":  "3–4",
        "rest_between_sets":  "60–90 sec",
        "intensity_note": (
            "Mix compound and isolation movements. One superset pairing per "
            "session is fine. Train close to failure on the last set of each exercise."
        ),
    },
    "advanced": {
        "exercises_per_day": "7–9",
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

# Hard numeric ceilings derived from EXERCISE_VOLUME above. The LLM is *told* to
# respect EXERCISE_VOLUME via the prompt, but prompt instructions aren't
# guaranteed to be followed — this table is enforced in code (see enforce_schema)
# as a backstop so a beginner can never actually end up with an advanced-volume
# workout even if the model ignores the instructions.
EXERCISE_LIMITS = {
    "beginner":     {"max_exercises": 5, "max_sets": 3},
    "intermediate": {"max_exercises": 7, "max_sets": 4},
    "advanced":     {"max_exercises": 9, "max_sets": 5},
}

# ── WORKOUT SPLIT TABLE ──────────────────────────────────────────────────────
# Previously the prompt just said "choose appropriate splits (e.g. Push/Pull/
# Legs, Upper/Lower, Full-Body)" with no link to experience tier or training
# days/week — so the LLM consistently defaulted to the easiest listed example,
# Full Body, for EVERY day regardless of how many days were selected. This
# table makes the split deterministic in code, same as the protein/calorie
# numbers above, instead of leaving it to the model to infer.
# Keys match WARMUP_LIBRARY so warmup selection lines up with the split.
SPLIT_LABELS = {
    "full":  "Full Body",
    "upper": "Upper Body",
    "lower": "Lower Body",
    "push":  "Push (Chest / Shoulders / Triceps)",
    "pull":  "Pull (Back / Biceps)",
    "legs":  "Legs (Quads / Hamstrings / Glutes)",
}

SPLIT_TEMPLATES = {
    "beginner": {
        1: ["full"],
        2: ["full", "full"],
        3: ["full", "full", "full"],
        4: ["upper", "lower", "upper", "lower"],
        5: ["upper", "lower", "full", "upper", "lower"],
        6: ["upper", "lower", "full", "upper", "lower", "full"],
        7: ["upper", "lower", "full", "upper", "lower", "full", "upper"],
    },
    "intermediate": {
        1: ["full"],
        2: ["upper", "lower"],
        3: ["push", "pull", "legs"],
        4: ["upper", "lower", "upper", "lower"],
        5: ["push", "pull", "legs", "upper", "lower"],
        6: ["push", "pull", "legs", "push", "pull", "legs"],
        7: ["push", "pull", "legs", "upper", "lower", "push", "pull"],
    },
    "advanced": {
        1: ["full"],
        2: ["upper", "lower"],
        3: ["push", "pull", "legs"],
        4: ["upper", "lower", "upper", "lower"],
        5: ["push", "pull", "legs", "upper", "lower"],
        6: ["push", "pull", "legs", "push", "pull", "legs"],
        7: ["push", "pull", "legs", "push", "pull", "legs", "upper"],
    },
}


def _resolve_split_sequence(exp_key: str, days_per_week: int) -> list:
    """
    Deterministic ordered list of split keys (one per training day) for the
    given experience tier + training frequency. Clamps days_per_week into
    1-7 so an out-of-range form value can't KeyError.
    """
    days = max(1, min(7, int(days_per_week)))
    table = SPLIT_TEMPLATES.get(exp_key, SPLIT_TEMPLATES["intermediate"])
    return table.get(days, table[min(table.keys(), key=lambda d: abs(d - days))])

# Machine/cable/dumbbell-safe compound movements, keyed by muscle group. This is
# the single source of truth for the "COMPOUND MOVEMENT LIBRARY" block injected
# into the prompt AND for _is_compound_exercise() below (used when trimming a
# day's exercise list so compound lifts are never the ones cut to make room).
COMPOUND_MOVEMENT_LIBRARY = {
    "Legs": [
        "Leg Press", "Hack Squat Machine", "Smith Machine Squat",
        "Goblet Squat (dumbbell)", "Walking Lunges (dumbbell)",
        "Romanian Deadlift (dumbbell)",
    ],
    "Back": [
        "Lat Pulldown", "Seated Cable Row", "Chest-Supported Machine Row",
        "Assisted Pull-up", "Single-Arm Dumbbell Row",
    ],
    "Chest": [
        "Machine Chest Press", "Incline Dumbbell Press", "Flat Dumbbell Press",
        "Smith Machine Bench Press",
    ],
    "Shoulders": [
        "Machine Shoulder Press", "Seated Dumbbell Press", "Arnold Press",
    ],
}
_COMPOUND_NAMES_LOWER = [
    n.lower() for names in COMPOUND_MOVEMENT_LIBRARY.values() for n in names
]


def _is_compound_exercise(ex: dict) -> bool:
    """
    Best-effort check for whether an exercise object is a compound movement,
    using the COMPOUND_MOVEMENT_LIBRARY names. Bidirectional substring match so
    it still recognises e.g. "Romanian Deadlift" against the library's "Romanian
    Deadlift (dumbbell)", or vice versa, without needing an exact string match.
    """
    name = str(ex.get("name", "")).lower().strip()
    if not name:
        return False
    return any(c in name or name in c for c in _COMPOUND_NAMES_LOWER)


def _trim_preserving_compounds(exercises: list, max_n: int) -> list:
    """
    Trim an exercise list down to max_n entries WITHOUT dropping compound
    movements first — compound lifts are kept, isolation work is cut to make
    room, and original relative ordering (compound → isolation, largest
    muscle → smallest) is preserved.
    """
    if len(exercises) <= max_n:
        return exercises
    compound_idx = [i for i, ex in enumerate(exercises) if _is_compound_exercise(ex)]
    other_idx = [i for i in range(len(exercises)) if i not in compound_idx]
    keep_idx = set(compound_idx[:max_n])  # if compounds alone exceed max_n, cap there too
    for i in other_idx:
        if len(keep_idx) >= max_n:
            break
        keep_idx.add(i)
    return [exercises[i] for i in sorted(keep_idx)]

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
    exp_key = _resolve_exp_key(experience)
    p_low, p_high = PROTEIN_MULTIPLIER[exp_key]
    p_dynamic = _protein_multiplier(exp_key, profile.get("activity_key", "moderate"), bmi)
    protein_g_low  = math.ceil(weight * p_low)
    protein_g_high = math.ceil(weight * p_high)
    protein_g_mid  = round(weight * p_dynamic)

    # Calorie target
    if "fat loss" in goal or "weight loss" in goal or "cut" in goal:
        target_kcal = round(tdee * 0.82)   # ~18% deficit
        phase_label = "Calorie deficit"
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
    "weekly_schedule": [
      { "short": "Mon", "label": "Push", "is_rest": false, "bar_width": 88 }
      // 7 entries Mon–Sun; bar_width 15 for rest
    ],
    "days": [
      {
        "short":   "MON",
        "name":    "Monday",
        "type":    "Legs Day — Quads · Hamstrings · Glutes",
        "is_rest": false,
        "warmup_exercises": [
          "5 min stationary bike",
          "20× bodyweight squats",
          "15× leg swings each leg (front-back + lateral)",
          "10× hip circles each side"
        ],
        "exercises": [
          {
            "name":   "Leg Press",
            "muscle": "Quads · glutes (largest muscle group → trained first)",
            "sets":   "4",
            "reps":   "10–12 reps",
            "rest":   "90–120 sec",
            "tempo_or_cue": "Full range, don't lock knees out at top"
          },
          {
            "name":   "Romanian Deadlift (Dumbbell)",
            "muscle": "Hamstrings · glutes",
            "sets":   "4",
            "reps":   "10–12 reps",
            "rest":   "90 sec",
            "tempo_or_cue": "Hinge at hips, slight knee bend, feel the stretch"
          },
          {
            "name":   "Walking Lunges (Dumbbell)",
            "muscle": "Quads · glutes",
            "sets":   "3",
            "reps":   "12 reps each leg",
            "rest":   "75 sec",
            "tempo_or_cue": "Controlled descent, front knee stays over ankle"
          },
          {
            "name":   "Seated Calf Raise",
            "muscle": "Calves (smallest muscle group → trained last)",
            "sets":   "3",
            "reps":   "15–20 reps",
            "rest":   "45–60 sec",
            "tempo_or_cue": "Pause 1 sec at full stretch"
          }
          // continue for the FULL exercise count required — see EXERCISE VOLUME RULES below.
          // Note the ordering: largest muscle group's compound movement FIRST, smallest
          // isolation movement LAST — apply this same priority on every training day.
        ],
        "safety": "Keep chest up, knees tracking over toes, controlled tempo on every rep."
      }
      // 7 entries; rest days only need short/name/type/is_rest
    ]
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

FINAL REMINDER: Output ONLY the raw JSON object.
The very first character of your response must be '{' and the very last must be '}'.
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
    # fuzzy match
    diet_token = DIET_TOKENS["non-vegetarian"]
    for key in DIET_TOKENS:
        if key in diet_raw or diet_raw in key:
            diet_token = DIET_TOKENS[key]
            break

    # ── 4. Experience token
    exp_key = _resolve_exp_key(profile.get("experience", "intermediate"))
    protein_mult_str = (
        f"{m['protein_multiplier']} g/kg "
        f"(computed for {exp_key} tier, band {PROTEIN_MULTIPLIER[exp_key][0]}–{PROTEIN_MULTIPLIER[exp_key][1]} g/kg, "
        f"weighted by activity level + BMI {m['bmi']})"
    )
    vol = EXERCISE_VOLUME[exp_key]

    # ── 5. Warmup hints per training days
    training_days_per_week = int(profile.get("days_per_week", 4))
    split_sequence = _resolve_split_sequence(exp_key, training_days_per_week)
    split_plan_lines = "\n".join(
        f"  Training day {i + 1} → {SPLIT_LABELS[s]}"
        for i, s in enumerate(split_sequence)
    )
    duration    = profile.get("session_duration", "45–60 min")
    region      = profile.get("region", "India")
    budget      = profile.get("budget", "medium")
    allergies   = profile.get("allergies", "none")
    target_wt   = profile.get("target_weight_kg", "—")
    medical     = profile.get("medical_notes", "none")
    meals_count = int(profile.get("meals_per_day", 5))

    # ── 6. Goal sentence
    goal = profile.get("goal", "fat loss")
    goal_label = "Fat Loss Plan"
    if "muscle" in goal.lower() or "bulk" in goal.lower() or "gain" in goal.lower():
        goal_label = "Muscle Gain Plan"
    elif "maintain" in goal.lower():
        goal_label = "Maintenance Plan"

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
Allergies / intolerances: {allergies}

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
Client experience level: {profile.get('experience', 'Intermediate')} → training rigour MUST match this tier, not a generic plan.
Design exactly {training_days_per_week} training days and {7 - training_days_per_week} rest day(s) per week.

WORKOUT SPLIT — MANDATORY, ALREADY DECIDED, DO NOT CHANGE IT:
This exact split was computed for a {exp_key} lifter training {training_days_per_week} day(s)/week.
Use it in this order for the training days (place rest day(s) wherever makes sense across the week,
e.g. spaced out or at the end — only the TRAINING-day split order below is fixed):
{split_plan_lines}
Do NOT default every training day to Full Body — only use Full Body where this list says so.
Each day's "type" and "name" fields should reflect the split shown above for that day
(e.g. "Push Day — Chest · Shoulders · Triceps" for a Push day, "Upper Body — ..." for an Upper day).
Session duration is {duration} — size the exercise volume accordingly.
Avoid free-weight barbell squat, deadlift, barbell bench press, overhead barbell press (injury risk) —
use the machine/cable/dumbbell compound equivalents listed below instead.

MUSCLE PRIORITY RULES (mandatory — bigger muscle groups get MORE exercises, not just trained
"first and harder" — this directly sets the exercise COUNT per muscle group, not just ordering):

Muscle size rank (1 = largest, 6 = smallest):
  1) Legs/Glutes (quads, hamstrings, glutes)
  2) Back (lats, mid-back, rear delts)
  3) Chest
  4) Shoulders (front/side delts)
  5) Arms (biceps, triceps)
  6) Calves / Core / small isolation

ALLOCATION RULE — when a single training day works MORE THAN ONE muscle group from this list
(e.g. a Push day works Chest + Shoulders + Triceps), distribute that day's total exercise count
({vol['exercises_per_day']}) across the trained muscle groups in proportion to their rank:
  - The HIGHEST-ranked (largest) muscle trained that day gets the MOST exercises — never fewer
    exercises than any lower-ranked muscle trained the same day.
  - As a concrete split for a day training 2 muscle groups: ~60% of exercises to the larger
    group, ~40% to the smaller (round in favour of the larger group).
  - For a day training 3 muscle groups: roughly 45% / 35% / 20% from largest to smallest.
  - A muscle group ranked 5–6 (Arms, Calves, Core) NEVER receives more exercises in a single
    day than a muscle group ranked 1–3 (Legs, Back, Chest) trained that same day.
- Order each day's "exercises" array by this same rank, largest → smallest (compound movements
  for the largest muscle group come first; isolation work for the smallest comes last).
- A day's first 1–2 exercises must ALWAYS be a compound movement for that day's largest
  trained muscle group (see COMPOUND MOVEMENT LIBRARY below) — never open a session with
  an isolation exercise.
- Across the whole weekly split, Legs and Back (rank 1–2) must each appear in at least as many
  total weekly exercise slots as Arms alone (rank 5) — do not under-train the big muscle groups
  relative to the small ones over the course of the week.

COMPOUND MOVEMENT LIBRARY (machine/cable/dumbbell-safe — use these as the opening 1–2
exercises for the relevant muscle group instead of banned free-weight lifts):
{chr(10).join(f"  {group:<10}→ {', '.join(names)}" for group, names in COMPOUND_MOVEMENT_LIBRARY.items())}
Isolation work (lateral raises, curls, triceps extensions, calf raises, core/abs) comes AFTER
the compound movement(s) for that day, never before.

EXERCISE VOLUME RULES (mandatory, scaled to {exp_key} level):
- Exercises per training day: {vol['exercises_per_day']} (a single exercise per day is NEVER acceptable — every
  non-rest day's "exercises" array must contain this many distinct movements, ordered compound → isolation,
  AND ordered by muscle size per the MUSCLE PRIORITY RULES above).
- Sets per exercise: {vol['sets_per_exercise']}
- Rest between sets: {vol['rest_between_sets']}
- Intensity guidance: {vol['intensity_note']}
- For EVERY exercise object, include "rest" (e.g. "75–90 sec") and "tempo_or_cue" (a short form cue or
  tempo instruction), in addition to name/muscle/sets/reps — do not omit these fields.
- Vary exercise selection across the week's training days; do not repeat the exact same exercise list on
  every day even within the same split type.

WARMUP (warmup_exercises[] array) — THIS FIELD IS MANDATORY, NOT OPTIONAL:
- EVERY non-rest day's JSON object MUST include a non-empty "warmup_exercises" array with
  4–5 specific exercises tailored to that day's split. A training day with an empty or
  missing warmup_exercises array is an INVALID response — never output one.
- Only rest days (is_rest: true) omit warmup_exercises entirely.
Example warmup tokens by split type:
  Push day  → {WARMUP_LIBRARY['push']}
  Pull day  → {WARMUP_LIBRARY['pull']}
  Legs day  → {WARMUP_LIBRARY['legs']}
  Full body → {WARMUP_LIBRARY['full']}
  Cardio    → {WARMUP_LIBRARY['cardio']}
  Rest day  → omit warmup_exercises entirely (is_rest: true)

━━ MEDICAL / OTHER NOTES ━━
{medical}

━━ INSTRUCTIONS ━━
1. Every value you output must be consistent with the client profile above.
2. The diet options must strictly respect the diet restriction token — do not add ANY forbidden food.
3. The macro numbers (kcal, protein_g, carb_g, fat_g) in each meal option must be realistic and add up.
4. warmup_exercises must be an array of strings for each non-rest day.
5. Use only the schema defined by the system prompt — no extra keys, no missing keys.

Generate the complete fitness dashboard JSON now.
"""


# ── PARSE LLM RESPONSE ────────────────────────────────────────────────────────
def parse_llm_json(raw: str) -> dict:
    text = raw

    text = re.sub(r"```(?:json)?\s*", "", text)
    text = text.replace("```", "").strip()

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(
            f"No JSON object found in LLM response.\n\nRaw output:\n{raw[:500]}"
        )
    text = match.group(0)

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
        return json.loads(single_to_double)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"LLM returned JSON that could not be auto-repaired: {e}\n\n"
            f"Cleaned text (first 800 chars):\n{text[:800]}\n\n"
            f"Original raw output (first 500 chars):\n{raw[:500]}"
        )


# ── WEIGHT FORMATTING HELPERS ─────────────────────────────────────────────────
# result.html's stat cards hard-code the "kg" unit (e.g. "{{ user.current_weight }} kg").
# Previously user.current_weight / user.target_weight / plan.weight_to_lose were
# left entirely to the LLM to fill in, and the LLM would often *also* write "kg"
# into the value itself (its own schema example for weight_to_lose even shows
# "~6–8 kg to lose", and the template appended " to lose" again on top of that) —
# producing visible "kg kg" / "to lose to lose" duplication.
# Fix: these three fields are deterministic from form input, so compute them in
# Python and overwrite whatever the LLM returned, instead of trusting its
# formatting.
def _clean_weight_label(val) -> str:
    """Strip any embedded kg unit text so the template's own 'kg' suffix never doubles up."""
    s = str(val).strip()
    if not s:
        return "—"
    s = re.sub(r"\s*kgs?\b", "", s, flags=re.IGNORECASE).strip()
    return s if s else "—"


def _weight_change_phrase(current_w, target_w) -> str:
    """
    Deterministic '~X kg to lose' / '~X kg to gain' / 'Maintain current weight'
    string. Falls back to '—' if target isn't a single parseable number (e.g.
    left blank or entered as a range) — never leaves it to the LLM to invent
    wording, since the template doesn't add a unit/suffix of its own anymore.
    """
    try:
        cur = float(current_w)
        tgt = float(str(target_w).strip())
    except (TypeError, ValueError):
        return "—"
    diff = cur - tgt
    if abs(diff) < 0.5:
        return "Maintain current weight"
    direction = "to lose" if diff > 0 else "to gain"
    return f"~{abs(diff):.0f} kg {direction}"


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

    # ── Deterministic overrides ────────────────────────────────────────────
    # These three are fully determined by the form input — never trust the
    # LLM's own formatting for them (see _clean_weight_label / _weight_change_phrase
    # docstrings for why).
    if profile is not None:
        data["user"]["current_weight"] = _clean_weight_label(
            profile.get("current_weight_kg", data["user"]["current_weight"])
        )
        data["user"]["target_weight"] = _clean_weight_label(
            profile.get("target_weight_kg", data["user"]["target_weight"])
        )
        data["plan"]["weight_to_lose"] = _weight_change_phrase(
            profile.get("current_weight_kg"), profile.get("target_weight_kg")
        )
    else:
        # No profile available (e.g. unit-testing this function directly) —
        # still strip stray "kg" text from whatever the LLM returned so the
        # template can't double up the unit.
        data["user"]["current_weight"] = _clean_weight_label(data["user"]["current_weight"])
        data["user"]["target_weight"] = _clean_weight_label(data["user"]["target_weight"])

    data["workout"].setdefault("weekly_schedule", [])
    data["workout"].setdefault("days", [])

    # Experience tier drives the hard exercise-volume ceiling below.
    exp_key = _resolve_exp_key(profile.get("experience", "intermediate")) if profile else "intermediate"
    limits = EXERCISE_LIMITS.get(exp_key, EXERCISE_LIMITS["intermediate"])

    # Ensure warmup_exercises exists on every non-rest day, and enforce the
    # tier's volume ceiling in code (not just via the prompt) so a beginner
    # can never end up with an advanced-volume workout.
    for day in data["workout"].get("days", []):
        if not day.get("is_rest", False):
            day.setdefault("warmup_exercises", [])
            day.setdefault("exercises", [])
            if len(day["warmup_exercises"]) == 0:
                day["_missing_warmup_warning"] = (
                    "No warmup_exercises were generated for this training day "
                    "despite being mandatory — the LLM dropped this field."
                )
            if len(day["exercises"]) < 4:
                day["_low_volume_warning"] = (
                    f"Only {len(day['exercises'])} exercise(s) generated for this day — "
                    f"below the requested minimum. Consider regenerating."
                )
            # Hard cap: trim any excess exercises beyond what this tier allows,
            # so beginners never get overloaded even if the LLM over-generated.
            # Compound movements are always kept; isolation work is cut first.
            if len(day["exercises"]) > limits["max_exercises"]:
                day["exercises"] = _trim_preserving_compounds(day["exercises"], limits["max_exercises"])
            # Hard cap: clamp each exercise's set count to the tier's ceiling.
            for ex in day["exercises"]:
                sets_match = re.search(r"\d+", str(ex.get("sets", "")))
                if sets_match and int(sets_match.group()) > limits["max_sets"]:
                    ex["sets"] = str(limits["max_sets"])

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

    return data


# ── RENDER ────────────────────────────────────────────────────────────────────
def render_dashboard(data: dict) -> str:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=False,
    )
    tmpl = env.get_template(TEMPLATE_FILE)
    return tmpl.render(**data)


# ── MAIN PIPELINE ─────────────────────────────────────────────────────────────
def generate_dashboard(profile: dict, llm_caller) -> str:
    user_prompt  = build_user_prompt(profile)
    raw_response = llm_caller(SYSTEM_PROMPT, user_prompt)
    data = parse_llm_json(raw_response)
    data = enforce_schema(data, profile)
    return render_dashboard(data)
