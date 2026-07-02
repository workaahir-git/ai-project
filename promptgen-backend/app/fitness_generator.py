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

from .split_engine import recommend_split, SPLIT_LIBRARY


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
Session duration is {duration} — size the exercise volume accordingly.
{"RECOVERY GOAL OVERRIDE: this client's goal is recovery/deload/rehab. Regardless of experience tier, stay well short of failure on every set (leave 3-4 reps in reserve), avoid ALL intensity techniques (no drop sets, no supersets, no rest-pause, no partials) even if the tier's intensity guidance below mentions them, and favour controlled tempo, full range of motion, and lighter loads over heavy loading." if is_recovery_goal else ""}

AI SPLIT ANALYSIS

Chosen Split:
{split['split_name']}

Reason:
{split['reason']}

This split was selected using:

1. Experience level
2. Days per week
3. Session duration
4. Primary goal
5. BMI
6. Activity level

Generate workouts strictly following this day-type sequence, repeating/cycling it across the
{training_days_per_week} training days in the week ({7 - training_days_per_week} rest day(s) placed
sensibly between blocks, not all bunched at the end):

{split_sequence_str}

Do NOT substitute a different split. Each day's "type" field must clearly name the split
segment it belongs to (e.g. "Push Day — Chest · Shoulders · Triceps"), consistent with the
sequence above.

BANNED EXERCISES — high injury-risk or spotter/technique-assistance-required movements. NEVER
include any of these, in any variant (barbell, dumbbell, or otherwise), under any name:
  - Any deadlift variant: conventional deadlift, Romanian deadlift (RDL), stiff-leg deadlift,
    sumo deadlift, single-leg RDL, trap-bar deadlift
  - Free-weight barbell back squat, barbell front squat
  - Free-weight barbell bench press, barbell overhead/military press
  - Olympic lifts: snatch, clean and jerk, power clean
  - Any movement that ordinarily requires a spotter or a coach physically assisting the lift
Squat-pattern and press-pattern compounds are still REQUIRED and welcome — just use the
machine/Smith/dumbbell-safe versions listed in the COMPOUND MOVEMENT LIBRARY below, never the
free-weight/hinge versions banned above.

SESSION STRUCTURE RULE (mandatory — applies to EVERY non-rest day):
- For EACH "big" muscle group (rank 1–4: Legs, Back, Chest, Shoulders — see MUSCLE PRIORITY
  RULES below) trained on a given day, exercise selection MUST open with ONE compound movement
  from the COMPOUND MOVEMENT LIBRARY for that specific muscle group, before any isolation work
  for that muscle group appears. A day training two big muscle groups (e.g. a Push day working
  Chest + Shoulders) therefore gets TWO compound movements that day — one per big group — not
  just one shared compound for the whole session. A day training only one big muscle group
  (e.g. a Legs day) gets exactly one compound.
  - Arms (rank 5) and Calves/Core (rank 6) NEVER get their own dedicated compound lift — they
    are isolation-only muscle groups in this program.
- LEGS-SPECIFIC RULE: whenever Legs is the (or a) big muscle group trained that day, its
  compound MUST specifically be a squat-pattern movement — Leg Press, Hack Squat Machine, Smith
  Machine Squat, or Goblet Squat (dumbbell). Do not substitute Walking Lunges or any hinge
  movement as the Legs compound; lunges may still appear later as isolation/accessory work if
  volume allows, but never as exercise #1 for a Legs-trained day.
- Every exercise that is NOT one of these per-muscle-group compounds is ISOLATION work —
  single-joint, single-muscle movements (lateral raises, curls, triceps extensions, leg
  extensions/curls, calf raises, core/abs, etc.).

MUSCLE PRIORITY RULES (mandatory — bigger muscle groups get MORE isolation exercises, not just
trained "first and harder" — this directly sets the isolation exercise COUNT per muscle group):

Muscle size rank (1 = largest, 6 = smallest):
  1) Legs/Glutes (quads, hamstrings, glutes)
  2) Back (lats, mid-back, rear delts)
  3) Chest
  4) Shoulders (front/side delts)
  5) Arms (biceps, triceps)
  6) Calves / Core / small isolation

ARM ISOLATION FLOOR (mandatory, HARD MINIMUM — overrides the proportional ALLOCATION RULE below
whenever they'd conflict):
- On ANY training day that works biceps, biceps get a MINIMUM of 2 dedicated isolation
  exercises that day. On ANY training day that works triceps, triceps get a MINIMUM of 2
  dedicated isolation exercises that day. This is a floor, not a target — go above 2 for either
  if the day's volume allows (e.g. a dedicated Arms day), but never below 2 for a muscle that is
  trained that day at all.
- If satisfying this floor would push a day's total exercise count above the tier's normal
  "exercises per day" figure in EXERCISE VOLUME RULES below, the floor wins — expand the day's
  total exercise count as needed rather than shorting biceps/triceps below 2 each.

ALLOCATION RULE — when a single training day works MORE THAN ONE muscle group from this list
(e.g. a Push day works Chest + Shoulders + Triceps), distribute that day's ISOLATION exercise
count ({vol['isolation_count']}, i.e. everything after the compound(s)) across the trained muscle
groups in proportion to their rank, SUBJECT TO the ARM ISOLATION FLOOR above:
  - The HIGHEST-ranked (largest) muscle trained that day gets the MOST isolation exercises —
    never fewer than any lower-ranked muscle trained the same day.
  - As a concrete split for a day training 2 muscle groups: ~60% of the isolation slots to the
    larger group, ~40% to the smaller (round in favour of the larger group), then apply the arm
    floor on top if arms are one of the two groups.
  - For a day training 3 muscle groups: roughly 45% / 35% / 20% from largest to smallest, then
    apply the arm floor on top if arms are one of the three groups.
  - A muscle group ranked 5–6 (Arms, Calves, Core) NEVER receives more isolation exercises in a
    single day than a muscle group ranked 1–3 (Legs, Back, Chest) trained that same day — UNLESS
    the ARM ISOLATION FLOOR requires it to reach the 2-exercise minimum, in which case the floor
    takes precedence over this ordering guideline.
- Order each day's "exercises" array as: compound(s) first (largest trained group's compound
  before smaller trained group's compound), then isolation work ordered largest → smallest.
- Across the whole weekly split, Legs and Back (rank 1–2) must each appear in at least as many
  total weekly exercise slots as Arms alone (rank 5) — do not under-train the big muscle groups
  relative to the small ones over the course of the week.

COMPOUND MOVEMENT LIBRARY (machine/cable/dumbbell-safe — for each big muscle group (rank 1-4)
trained that day, use exactly ONE of these as that group's opening compound, instead of any
banned free-weight/hinge lift. NOTE: RDL/deadlift variants have been removed from this library
entirely per the BANNED EXERCISES list above — Legs compounds are squat-pattern only):
  Legs    → Leg Press, Hack Squat Machine, Smith Machine Squat, Goblet Squat (dumbbell)
            [squat-pattern only — see LEGS-SPECIFIC RULE above]
  Back    → Lat Pulldown, Seated Cable Row, Chest-Supported Machine Row, Assisted Pull-up,
            Single-Arm Dumbbell Row
  Chest   → Machine Chest Press, Incline Dumbbell Press, Flat Dumbbell Press, Smith Machine
            Bench Press
  Shoulders → Machine Shoulder Press, Seated Dumbbell Press, Arnold Press
Every exercise that isn't one of these per-group compounds MUST be isolation work.

EXERCISE VOLUME RULES (mandatory — scaled to {exp_key} level; this is a FLOOR, not a ceiling,
whenever the ARM ISOLATION FLOOR or the one-compound-per-big-muscle-group rule above requires
more exercises than the base figure below):
- Base exercises per training day: {vol['exercises_per_day']} total
  ({vol['compound_count']} compound as exercise #1, then {vol['isolation_count']} isolation
  exercises) — use this exact figure UNLESS the day trains 2+ big muscle groups (which adds one
  compound per extra big group) or the ARM ISOLATION FLOOR requires 2+ dedicated exercises for
  biceps and/or triceps that day, in which case increase the day's total exercise count to
  satisfy both. Never go below this base figure, and never go below either mandatory floor.
  A single exercise per day is NEVER acceptable.
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
    user_prompt  = build_user_prompt(profile)
    raw_response = llm_caller(SYSTEM_PROMPT, user_prompt)
    data = parse_llm_json(raw_response)
    data = enforce_schema(data, profile)
    return render_dashboard(data)
