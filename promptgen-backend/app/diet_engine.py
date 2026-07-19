"""
diet_engine.py
──────────────────────────────────────────────────────────────────────────────
Deterministic replacement for the LLM-generated `diet.meals` block. Same
philosophy as exercise_database.py: real per-ingredient data + Python logic
solving for the actual numbers, not generated/guessed text.

Given a daily calorie/protein target (already computed by
fitness_generator._calculate_macros — this module does not recompute those),
splits it across 5 fixed meal slots, and for each slot solves 3 distinct
ingredient-combo options that hit that slot's kcal/protein target using real
portion sizes (not invented numbers) — the same "fix protein first, fill
remaining calories" logic _calculate_macros() already uses for the daily
total, applied per-meal.

Never raises: any missing ingredient / impossible combo for a given
diet+allergy+budget filter just yields fewer than 3 options for that meal
rather than crashing generation — 1-2 valid options is still useful, zero
Gemini calls needed either way.
"""

from __future__ import annotations

from app.food_database import INGREDIENTS, is_usable, resolve_diet_tier

# Fraction of the day's total calories (and, for simplicity, protein) each
# slot targets. Sums to 1.0.
SLOT_KCAL_FRACTION = {
    "breakfast": 0.22,
    "mid":       0.10,
    "lunch":     0.28,
    "post":      0.15,
    "dinner":    0.25,
}
SLOT_LABELS = {
    "breakfast": "Breakfast",
    "mid":       "Mid-Morning Snack",
    "lunch":     "Lunch",
    "post":      "Post-Workout",
    "dinner":    "Dinner",
}

# Per slot: ordered protein-source priority (best/most-typical first), an
# ordered carb-source priority (None if the slot has no carb component), and
# an optional fixed side item (fruit) added at its natural serving size
# before the protein/carb math runs.
MEAL_TEMPLATES = {
    "breakfast": {
        "protein_priority": ["paneer", "greek_yogurt", "curd_plain", "egg_whole",
                             "whey_protein", "soy_chunks_dry", "moong_dal_cooked"],
        "carb_priority": ["oats_dry", "poha_dry", "roti", "whole_wheat_bread", "idli"],
        "side": "banana",
    },
    "mid": {
        "protein_priority": ["greek_yogurt", "curd_plain", "whey_protein",
                             "peanut_butter", "almonds"],
        "carb_priority": None,   # light snack — protein + a fixed fruit side only
        "side": "apple",
    },
    "lunch": {
        "protein_priority": ["chicken_breast_cooked", "paneer", "fish_cooked",
                             "toor_dal_cooked", "rajma_cooked", "chana_cooked", "tofu"],
        "carb_priority": ["white_rice_cooked", "brown_rice_cooked", "roti", "quinoa_cooked"],
        "side": "mixed_sabzi",
    },
    "post": {
        "protein_priority": ["whey_protein", "egg_whole", "chicken_breast_cooked",
                             "greek_yogurt", "soy_chunks_dry"],
        "carb_priority": ["banana", "white_rice_cooked", "roti"],
        "side": None,
    },
    "dinner": {
        "protein_priority": ["chicken_breast_cooked", "fish_cooked", "paneer",
                             "egg_whole", "toor_dal_cooked", "tofu", "moong_dal_cooked"],
        "carb_priority": ["roti", "white_rice_cooked", "quinoa_cooked"],
        "side": "salad_raw",
    },
}

# Sensible rounding per unit type, so portions read like something a person
# would actually measure out rather than an arbitrary decimal.
def _round_grams(ingredient_id: str, grams: float) -> float:
    ing = INGREDIENTS[ingredient_id]
    unit = ing["unit"]
    if unit in ("piece",):
        return max(1, round(grams / ing["unit_grams"])) * ing["unit_grams"]
    if unit in ("scoop", "tbsp", "10pc", "30g"):
        return max(1, round(grams / ing["unit_grams"])) * ing["unit_grams"]
    # free-scaling weight/volume items (100g / 100ml basis) — nearest 10g,
    # floor of 20g so nothing rounds down to a silly near-zero portion
    return max(20, round(grams / 10) * 10)


def _scale(ingredient_id: str, grams: float) -> dict:
    ing = INGREDIENTS[ingredient_id]
    factor = grams / ing["unit_grams"] if ing["unit"] not in ("100g", "100ml") else grams / 100
    return {
        "grams": grams,
        "kcal": ing["kcal"] * factor,
        "protein_g": ing["protein_g"] * factor,
        "carb_g": ing["carb_g"] * factor,
        "fat_g": ing["fat_g"] * factor,
    }


def _describe_portion(ingredient_id: str, grams: float) -> str:
    ing = INGREDIENTS[ingredient_id]
    unit = ing["unit"]
    if unit == "piece":
        count = round(grams / ing["unit_grams"])
        return f"{count} {ing['name']}" + ("s" if count > 1 and not ing["name"].endswith("s") else "")
    if unit == "scoop":
        count = round(grams / ing["unit_grams"])
        return f"{count} scoop {ing['name']}" if count == 1 else f"{count} scoops {ing['name']}"
    if unit == "tbsp":
        count = round(grams / ing["unit_grams"])
        return f"{count} tbsp {ing['name']}"
    if unit == "10pc":
        count = round(grams / ing["unit_grams"] * 10)
        return f"{count} {ing['name']}"
    if unit == "30g":
        return f"{round(grams)}g {ing['name']}"
    if unit == "100ml":
        return f"{round(grams)}ml {ing['name']}"
    return f"{round(grams)}g {ing['name']}"


def _eligible(priority_list, user_tier, allergy_set, budget_tier, exclude=()):
    return [i for i in (priority_list or [])
            if i not in exclude and is_usable(i, user_tier, allergy_set, budget_tier)]


def _build_option(slot_config, target_kcal, target_protein_g, user_tier, allergy_set,
                   budget_tier, used_proteins, used_carbs):
    proteins = _eligible(slot_config["protein_priority"], user_tier, allergy_set, budget_tier,
                          exclude=used_proteins)
    if not proteins:
        return None
    protein_id = proteins[0]

    side_id = slot_config.get("side")
    side_component = None
    remaining_kcal = target_kcal
    remaining_protein = target_protein_g
    if side_id and is_usable(side_id, user_tier, allergy_set, budget_tier):
        side_grams = INGREDIENTS[side_id]["unit_grams"]
        side_component = _scale(side_id, side_grams)
        remaining_kcal -= side_component["kcal"]
        remaining_protein -= side_component["protein_g"]

    protein_ing = INGREDIENTS[protein_id]
    protein_kcal_per_g = protein_ing["kcal"] / (protein_ing["unit_grams"] if protein_ing["unit"] not in ("100g", "100ml") else 100)
    protein_g_per_g = protein_ing["protein_g"] / (protein_ing["unit_grams"] if protein_ing["unit"] not in ("100g", "100ml") else 100)

    protein_grams_needed = max(remaining_protein, 0) / protein_g_per_g if protein_g_per_g > 0 else 0
    # Don't let the protein source alone blow past the meal's calorie budget,
    # and never exceed a realistic single-meal serving size for this ingredient
    # (see food_database.py's max_grams — this is the fix for the v1 bug where
    # a low-protein-density food like curd got solved up to 600-700g to hit a
    # protein target alone).
    max_grams_by_kcal = remaining_kcal / protein_kcal_per_g if protein_kcal_per_g > 0 else protein_grams_needed
    protein_grams = min(protein_grams_needed, max(max_grams_by_kcal, 0), protein_ing["max_grams"])
    protein_grams = _round_grams(protein_id, protein_grams)
    protein_component = _scale(protein_id, protein_grams)
    remaining_kcal -= protein_component["kcal"]

    carb_component = None
    carb_id = None
    if slot_config.get("carb_priority"):
        carbs = _eligible(slot_config["carb_priority"], user_tier, allergy_set, budget_tier,
                           exclude=used_carbs)
        if carbs:
            carb_id = carbs[0]
            carb_ing = INGREDIENTS[carb_id]
            carb_kcal_per_g = carb_ing["kcal"] / (carb_ing["unit_grams"] if carb_ing["unit"] not in ("100g", "100ml") else 100)
            carb_grams = max(remaining_kcal, 0) / carb_kcal_per_g if carb_kcal_per_g > 0 else 0
            carb_grams = min(carb_grams, carb_ing["max_grams"])
            carb_grams = _round_grams(carb_id, carb_grams)
            carb_component = _scale(carb_id, carb_grams)

    parts = []
    total_kcal = total_protein = total_carb = total_fat = 0.0
    for comp_id, comp in ((protein_id, protein_component), (carb_id, carb_component),
                          (side_id, side_component)):
        if comp is None:
            continue
        parts.append(_describe_portion(comp_id, comp["grams"]))
        total_kcal += comp["kcal"]
        total_protein += comp["protein_g"]
        total_carb += comp["carb_g"]
        total_fat += comp["fat_g"]

    return {
        "food": " + ".join(parts),
        "kcal": round(total_kcal),
        "protein_g": round(total_protein),
        "carb_g": round(total_carb),
        "fat_g": round(total_fat),
        "_protein_id": protein_id,
        "_carb_id": carb_id,
    }


def build_diet_meals(daily_kcal: int, daily_protein_g: int, diet_pref_raw: str,
                      allergy_set: set, budget_tier: str) -> list:
    """
    Returns the full `diet.meals` array (5 slots, up to 3 options each) built
    entirely in Python. Drop-in replacement for what Gemini used to generate
    for this part of the schema.
    """
    user_tier = resolve_diet_tier(diet_pref_raw)
    meals = []

    for slot, label in SLOT_LABELS.items():
        cfg = MEAL_TEMPLATES[slot]
        frac = SLOT_KCAL_FRACTION[slot]
        target_kcal = daily_kcal * frac
        target_protein = daily_protein_g * frac

        options = []
        used_proteins, used_carbs = [], []
        for _ in range(3):
            opt = _build_option(cfg, target_kcal, target_protein, user_tier,
                                 allergy_set, budget_tier, used_proteins, used_carbs)
            if opt is None:
                break
            used_proteins.append(opt["_protein_id"])
            if opt["_carb_id"]:
                used_carbs.append(opt["_carb_id"])
            options.append({k: v for k, v in opt.items() if not k.startswith("_")})

        if not options:
            # Total filter failure (e.g. vegan + every allergy tag at once) —
            # skip this slot rather than crash; better a short diet plan than none.
            continue

        kcal_values = [o["kcal"] for o in options]
        meals.append({
            "id": slot,
            "tab_label": label,
            "title": label,
            "kcal_range": f"{min(kcal_values)}–{max(kcal_values)}",
            "options": options,
        })

    return meals


# ── Allergy free-text parsing (same substring-match style as the rest of
# this codebase — see exercise_database._parse_injury_keywords) ─────────────
ALLERGY_KEYWORDS = {
    "dairy": ("dairy", "lactose", "milk"),
    "gluten": ("gluten", "wheat", "celiac"),
    "egg": ("egg",),
    "nuts": ("nut", "peanut", "almond"),
    "soy": ("soy", "soya"),
    "shellfish": ("shellfish", "prawn", "shrimp"),
}


def parse_allergies(allergies_raw: str) -> set:
    text = (allergies_raw or "").lower()
    if not text or text.strip() == "none":
        return set()
    return {tag for tag, kws in ALLERGY_KEYWORDS.items() if any(kw in text for kw in kws)}


def resolve_budget_tier(budget_raw: str) -> str:
    b = (budget_raw or "medium").lower().strip()
    if b in ("low", "budget", "cheap"):
        return "budget"
    if b in ("high", "premium"):
        return "premium"
    return "medium"
