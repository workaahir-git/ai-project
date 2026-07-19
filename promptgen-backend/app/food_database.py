"""
food_database.py
──────────────────────────────────────────────────────────────────────────────
Ingredient-level nutrition data for the deterministic Diet Engine. Values are
sourced from USDA FoodData Central / standard published nutrition data (rice,
roti, dal, paneer, chicken breast were cross-checked against multiple sources
this session — see conversation for citations). Values are per COOKED/EDIBLE
state unless noted, per 100g unless the ingredient has a natural discrete unit
(1 egg, 1 roti, 1 banana, 1 scoop) in which case unit_grams gives that unit's
weight for scaling math.

diet_tier (ordinal, ingredient's MINIMUM required user diet tier):
  0 = vegan-safe           (usable by everyone)
  1 = vegetarian (+dairy)  (usable by vegetarian/eggetarian/non-veg users)
  2 = eggetarian (+egg)    (usable by eggetarian/non-veg users)
  3 = non-vegetarian only  (usable by non-veg users only)
An ingredient is usable for a user if ingredient.diet_tier <= user's diet_tier.

allergy_tags: any of {"dairy", "gluten", "egg", "nuts", "soy", "shellfish"}.
cost_tier: "budget" | "medium" | "premium" — rough INR cost per typical serving,
not a precise price (no live pricing data — this is a coarse filter only).
"""

from __future__ import annotations

DIET_TIER = {"vegan": 0, "vegetarian": 1, "eggetarian": 2, "non-vegetarian": 3}


def resolve_diet_tier(diet_pref_raw: str) -> int:
    """Same fuzzy-match spirit as fitness_generator.py's DIET_TOKENS lookup."""
    d = (diet_pref_raw or "non-vegetarian").lower().strip()
    if d in DIET_TIER:
        return DIET_TIER[d]
    for key in ("non-vegetarian", "eggetarian", "vegan", "vegetarian"):
        if key in d or d in key:
            return DIET_TIER[key]
    return DIET_TIER["non-vegetarian"]


# unit: "100g" (scalable by weight) or a discrete unit ("piece", "scoop", "tsp", "100ml")
# unit_grams: grams represented by ONE of {unit_grams} at the stated macros —
# for "100g"-unit items this is always 100 (macros already per 100g).
INGREDIENTS = {
    # ── GRAINS / CARBS ───────────────────────────────────────────────────────
    "white_rice_cooked": {
        "name": "White Rice (cooked)", "category": "grain", "unit": "100g", "unit_grams": 100,
        "kcal": 130, "protein_g": 2.7, "carb_g": 28.0, "fat_g": 0.3,
        "diet_tier": 0, "max_grams": 300, "allergy_tags": set(), "cost_tier": "budget",
    },
    "brown_rice_cooked": {
        "name": "Brown Rice (cooked)", "category": "grain", "unit": "100g", "unit_grams": 100,
        "kcal": 123, "protein_g": 2.7, "carb_g": 26.0, "fat_g": 1.0,
        "diet_tier": 0, "max_grams": 300, "allergy_tags": set(), "cost_tier": "budget",
    },
    "roti": {
        "name": "Whole Wheat Roti", "category": "grain", "unit": "piece", "unit_grams": 40,
        "kcal": 115, "protein_g": 3.0, "carb_g": 20.0, "fat_g": 2.5,
        "diet_tier": 0, "max_grams": 160, "allergy_tags": {"gluten"}, "cost_tier": "budget",
    },
    "oats_dry": {
        "name": "Oats (dry)", "category": "grain", "unit": "100g", "unit_grams": 100,
        "kcal": 389, "protein_g": 17.0, "carb_g": 66.0, "fat_g": 7.0,
        "diet_tier": 0, "max_grams": 80, "allergy_tags": set(), "cost_tier": "budget",
    },
    "poha_dry": {
        "name": "Poha (flattened rice, dry)", "category": "grain", "unit": "100g", "unit_grams": 100,
        "kcal": 356, "protein_g": 6.4, "carb_g": 76.0, "fat_g": 1.2,
        "diet_tier": 0, "max_grams": 80, "allergy_tags": set(), "cost_tier": "budget",
    },
    "dosa": {
        "name": "Plain Dosa", "category": "grain", "unit": "piece", "unit_grams": 60,
        "kcal": 130, "protein_g": 3.0, "carb_g": 20.0, "fat_g": 3.5,
        "diet_tier": 0, "max_grams": 180, "allergy_tags": set(), "cost_tier": "budget",
    },
    "idli": {
        "name": "Idli", "category": "grain", "unit": "piece", "unit_grams": 35,
        "kcal": 40, "protein_g": 1.5, "carb_g": 8.0, "fat_g": 0.2,
        "diet_tier": 0, "max_grams": 210, "allergy_tags": set(), "cost_tier": "budget",
    },
    "whole_wheat_bread": {
        "name": "Whole Wheat Bread", "category": "grain", "unit": "piece", "unit_grams": 30,
        "kcal": 75, "protein_g": 3.0, "carb_g": 13.0, "fat_g": 1.0,
        "diet_tier": 0, "max_grams": 120, "allergy_tags": {"gluten"}, "cost_tier": "budget",
    },
    "quinoa_cooked": {
        "name": "Quinoa (cooked)", "category": "grain", "unit": "100g", "unit_grams": 100,
        "kcal": 120, "protein_g": 4.4, "carb_g": 21.0, "fat_g": 1.9,
        "diet_tier": 0, "max_grams": 200, "allergy_tags": set(), "cost_tier": "medium",
    },

    # ── VEG PROTEIN / DAIRY ──────────────────────────────────────────────────
    "paneer": {
        "name": "Paneer", "category": "protein", "unit": "100g", "unit_grams": 100,
        "kcal": 258, "protein_g": 19.0, "carb_g": 2.4, "fat_g": 15.0,
        "diet_tier": 1, "max_grams": 200, "allergy_tags": {"dairy"}, "cost_tier": "medium",
    },
    "tofu": {
        "name": "Tofu", "category": "protein", "unit": "100g", "unit_grams": 100,
        "kcal": 76, "protein_g": 8.0, "carb_g": 1.9, "fat_g": 4.8,
        "diet_tier": 0, "max_grams": 250, "allergy_tags": {"soy"}, "cost_tier": "medium",
    },
    "curd_plain": {
        "name": "Curd (plain)", "category": "protein", "unit": "100g", "unit_grams": 100,
        "kcal": 60, "protein_g": 3.5, "carb_g": 4.7, "fat_g": 3.3,
        "diet_tier": 1, "max_grams": 300, "allergy_tags": {"dairy"}, "cost_tier": "budget",
    },
    "greek_yogurt": {
        "name": "Greek-style Curd", "category": "protein", "unit": "100g", "unit_grams": 100,
        "kcal": 59, "protein_g": 10.0, "carb_g": 3.6, "fat_g": 0.4,
        "diet_tier": 1, "max_grams": 250, "allergy_tags": {"dairy"}, "cost_tier": "medium",
    },
    "milk_toned": {
        "name": "Milk (toned)", "category": "protein", "unit": "100ml", "unit_grams": 100,
        "kcal": 58, "protein_g": 3.2, "carb_g": 4.7, "fat_g": 3.0,
        "diet_tier": 1, "max_grams": 400, "allergy_tags": {"dairy"}, "cost_tier": "budget",
    },
    "whey_protein": {
        "name": "Whey Protein", "category": "protein", "unit": "scoop", "unit_grams": 30,
        "kcal": 120, "protein_g": 24.0, "carb_g": 3.0, "fat_g": 1.5,
        "diet_tier": 1, "max_grams": 60, "allergy_tags": {"dairy"}, "cost_tier": "premium",
    },
    "soy_chunks_dry": {
        "name": "Soy Chunks (dry)", "category": "protein", "unit": "100g", "unit_grams": 100,
        "kcal": 345, "protein_g": 52.0, "carb_g": 33.0, "fat_g": 0.5,
        "diet_tier": 0, "max_grams": 50, "allergy_tags": {"soy"}, "cost_tier": "budget",
    },
    "moong_dal_cooked": {
        "name": "Moong Dal (cooked)", "category": "legume", "unit": "100g", "unit_grams": 100,
        "kcal": 105, "protein_g": 7.5, "carb_g": 19.0, "fat_g": 0.4,
        "diet_tier": 0, "max_grams": 300, "allergy_tags": set(), "cost_tier": "budget",
    },
    "toor_dal_cooked": {
        "name": "Toor Dal (cooked)", "category": "legume", "unit": "100g", "unit_grams": 100,
        "kcal": 116, "protein_g": 9.0, "carb_g": 20.0, "fat_g": 0.4,
        "diet_tier": 0, "max_grams": 300, "allergy_tags": set(), "cost_tier": "budget",
    },
    "chana_cooked": {
        "name": "Chickpeas (cooked)", "category": "legume", "unit": "100g", "unit_grams": 100,
        "kcal": 164, "protein_g": 8.9, "carb_g": 27.0, "fat_g": 2.6,
        "diet_tier": 0, "max_grams": 250, "allergy_tags": set(), "cost_tier": "budget",
    },
    "rajma_cooked": {
        "name": "Rajma (cooked)", "category": "legume", "unit": "100g", "unit_grams": 100,
        "kcal": 127, "protein_g": 8.7, "carb_g": 22.8, "fat_g": 0.5,
        "diet_tier": 0, "max_grams": 250, "allergy_tags": set(), "cost_tier": "budget",
    },
    "peanut_butter": {
        "name": "Peanut Butter", "category": "fat", "unit": "tbsp", "unit_grams": 16,
        "kcal": 95, "protein_g": 3.6, "carb_g": 3.0, "fat_g": 8.0,
        "diet_tier": 0, "max_grams": 32, "allergy_tags": {"nuts"}, "cost_tier": "medium",
    },
    "almonds": {
        "name": "Almonds", "category": "fat", "unit": "10pc", "unit_grams": 12,
        "kcal": 70, "protein_g": 2.6, "carb_g": 2.6, "fat_g": 6.0,
        "diet_tier": 0, "max_grams": 36, "allergy_tags": {"nuts"}, "cost_tier": "medium",
    },
    "peanuts_roasted": {
        "name": "Roasted Peanuts", "category": "fat", "unit": "30g", "unit_grams": 30,
        "kcal": 170, "protein_g": 7.0, "carb_g": 6.0, "fat_g": 14.0,
        "diet_tier": 0, "max_grams": 60, "allergy_tags": {"nuts"}, "cost_tier": "budget",
    },

    # ── NON-VEG PROTEIN ───────────────────────────────────────────────────────
    "chicken_breast_cooked": {
        "name": "Chicken Breast (cooked)", "category": "protein", "unit": "100g", "unit_grams": 100,
        "kcal": 165, "protein_g": 31.0, "carb_g": 0.0, "fat_g": 3.6,
        "diet_tier": 3, "max_grams": 250, "allergy_tags": set(), "cost_tier": "medium",
    },
    "egg_whole": {
        "name": "Whole Egg", "category": "protein", "unit": "piece", "unit_grams": 50,
        "kcal": 78, "protein_g": 6.3, "carb_g": 0.6, "fat_g": 5.3,
        "diet_tier": 2, "max_grams": 200, "allergy_tags": {"egg"}, "cost_tier": "budget",
    },
    "egg_white": {
        "name": "Egg White", "category": "protein", "unit": "piece", "unit_grams": 33,
        "kcal": 17, "protein_g": 3.6, "carb_g": 0.2, "fat_g": 0.1,
        "diet_tier": 2, "max_grams": 200, "allergy_tags": {"egg"}, "cost_tier": "budget",
    },
    "fish_cooked": {
        "name": "Fish (rohu/basa, cooked)", "category": "protein", "unit": "100g", "unit_grams": 100,
        "kcal": 105, "protein_g": 18.0, "carb_g": 0.0, "fat_g": 3.0,
        "diet_tier": 3, "max_grams": 250, "allergy_tags": set(), "cost_tier": "medium",
    },
    "prawns_cooked": {
        "name": "Prawns (cooked)", "category": "protein", "unit": "100g", "unit_grams": 100,
        "kcal": 99, "protein_g": 21.0, "carb_g": 0.2, "fat_g": 1.0,
        "diet_tier": 3, "max_grams": 200, "allergy_tags": {"shellfish"}, "cost_tier": "premium",
    },

    # ── VEGETABLES ────────────────────────────────────────────────────────────
    "mixed_sabzi": {
        "name": "Mixed Vegetable Sabzi", "category": "vegetable", "unit": "100g", "unit_grams": 100,
        "kcal": 80, "protein_g": 2.0, "carb_g": 10.0, "fat_g": 3.0,
        "diet_tier": 0, "max_grams": 250, "allergy_tags": set(), "cost_tier": "budget",
    },
    "salad_raw": {
        "name": "Raw Salad", "category": "vegetable", "unit": "100g", "unit_grams": 100,
        "kcal": 25, "protein_g": 1.5, "carb_g": 5.0, "fat_g": 0.2,
        "diet_tier": 0, "max_grams": 250, "allergy_tags": set(), "cost_tier": "budget",
    },
    "spinach_cooked": {
        "name": "Spinach (cooked)", "category": "vegetable", "unit": "100g", "unit_grams": 100,
        "kcal": 45, "protein_g": 3.5, "carb_g": 4.0, "fat_g": 2.0,
        "diet_tier": 0, "max_grams": 250, "allergy_tags": set(), "cost_tier": "budget",
    },

    # ── FRUITS (fixed-serving side items) ────────────────────────────────────
    "banana": {
        "name": "Banana", "category": "fruit", "unit": "piece", "unit_grams": 118,
        "kcal": 105, "protein_g": 1.3, "carb_g": 27.0, "fat_g": 0.4,
        "diet_tier": 0, "max_grams": 236, "allergy_tags": set(), "cost_tier": "budget",
    },
    "apple": {
        "name": "Apple", "category": "fruit", "unit": "piece", "unit_grams": 182,
        "kcal": 95, "protein_g": 0.5, "carb_g": 25.0, "fat_g": 0.3,
        "diet_tier": 0, "max_grams": 364, "allergy_tags": set(), "cost_tier": "budget",
    },
    "papaya": {
        "name": "Papaya", "category": "fruit", "unit": "100g", "unit_grams": 100,
        "kcal": 43, "protein_g": 0.5, "carb_g": 11.0, "fat_g": 0.3,
        "diet_tier": 0, "max_grams": 200, "allergy_tags": set(), "cost_tier": "budget",
    },
}


def get_ingredient(ingredient_id: str) -> dict | None:
    return INGREDIENTS.get(ingredient_id)


def is_usable(ingredient_id: str, user_diet_tier: int, allergy_set: set, budget_tier: str) -> bool:
    ing = INGREDIENTS.get(ingredient_id)
    if ing is None:
        return False
    if ing["diet_tier"] > user_diet_tier:
        return False
    if ing["allergy_tags"] & allergy_set:
        return False
    budget_rank = {"budget": 0, "medium": 1, "premium": 2}
    if budget_rank.get(ing["cost_tier"], 1) > budget_rank.get(budget_tier, 1):
        return False
    return True
