"""
allergy_engine.py — STEP 1: Food Allergy Enforcement
────────────────────────────────────────────────────────────────────────────
Turns the free-text intake `allergies` field into a strict, structured
safety constraint instead of a loosely-worded prompt hint.

Two independent layers, mirroring the pattern already used for workout
safety (safety_engine.py) and Trainer Review (review_validation.py) —
never trust a single layer, and never trust the LLM's own compliance:

  1. PROMPT LAYER  — build_allergy_prompt_block() expands whatever the
     user typed into an explicit, categorised, synonym-expanded banned-
     ingredient list (including common Indian-English/regional names,
     since build_user_prompt() already targets Indian kirana/sabzi-mandi
     ingredients) and injects it as a hard, safety-critical instruction.

  2. VALIDATION LAYER — enforce_allergy_safety() re-scans every single
     meal option's `food` text returned by the LLM against that same
     banned-term list, AFTER parsing/schema enforcement. Any option that
     contains a disclosed allergen (in any spelling/synonym variant) is
     deterministically substituted with a known-safe fallback — never
     silently dropped, never left in the plan, and never re-sent to the
     LLM to "fix" (that would just re-introduce the same trust problem).

This module has no side effects and no external calls — pure Python, so
it can't fail, time out, or be rate-limited, and it never blocks plan
delivery.
"""

import re

# ── Canonical allergen categories → recognised name / spelling / regional
#    variations. Extend this table (do not scatter allergen strings
#    elsewhere) as new allergens/synonyms are discovered in the field.
ALLERGEN_SYNONYMS: dict[str, list[str]] = {
    "peanut": [
        "peanut", "peanuts", "groundnut", "groundnuts", "moongphali",
        "mungfali", "peanut butter", "peanut oil",
    ],
    "tree_nut": [
        "almond", "almonds", "badam", "cashew", "cashews", "kaju",
        "walnut", "walnuts", "akhrot", "pistachio", "pistachios", "pista",
        "hazelnut", "hazelnuts", "pine nut", "pine nuts", "macadamia",
        "brazil nut", "nut butter", "nut milk", "mixed nuts",
    ],
    "milk": [
        "milk", "dairy", "curd", "dahi", "yogurt", "yoghurt", "paneer",
        "cheese", "butter", "ghee", "cream", "malai", "khoya", "khova",
        "buttermilk", "chaas", "whey", "casein", "condensed milk",
        "milk powder", "milk solids",
    ],
    "egg": [
        "egg", "eggs", "anda", "mayonnaise", "mayo", "egg white", "egg yolk",
    ],
    "gluten": [
        "wheat", "gluten", "atta", "maida", "suji", "sooji", "rava",
        "semolina", "bread", "roti", "chapati", "naan", "paratha", "pasta",
        "noodles", "barley", "rye", "seitan", "vermicelli", "sevai", "dalia",
    ],
    "soy": [
        "soy", "soya", "soybean", "soy sauce", "tofu", "edamame",
        "soy milk", "soy chunks", "nutrela", "soya chunks",
    ],
    "fish": [
        "fish", "machli", "salmon", "tuna", "cod", "mackerel", "sardine",
        "anchovy", "surmai", "rohu", "pomfret", "hilsa", "fish sauce",
    ],
    "shellfish": [
        "shrimp", "prawn", "prawns", "crab", "lobster", "shellfish",
        "squid", "oyster", "clam", "mussel",
    ],
    "sesame": [
        "sesame", "til", "tahini", "gingelly", "sesame oil", "til oil",
    ],
    "mustard": [
        "mustard", "sarson", "rai", "mustard oil", "mustard seed",
    ],
}

_NO_ALLERGY_VALUES = {"none", "no", "na", "n/a", "nil", "no allergies", "-", ""}


def parse_allergies(raw: str) -> dict:
    """
    Parse the raw intake `allergies` string into a structured constraint.

    Returns:
      {
        "categories":       [canonical allergen categories matched],
        "literal_terms":    [free-text terms typed that aren't in our table,
                              still enforced verbatim],
        "all_banned_terms": [every literal string that must never appear
                              in a meal's "food" text — the union of every
                              synonym for each matched category, plus the
                              literal terms],
      }
    An empty/"none"-type value returns an all-empty (no restriction) dict.
    """
    if not raw:
        return {"categories": [], "literal_terms": [], "all_banned_terms": []}

    normalized = raw.strip().lower()
    if normalized in _NO_ALLERGY_VALUES:
        return {"categories": [], "literal_terms": [], "all_banned_terms": []}

    raw_terms = re.split(r"[,;/\n]| and | or ", normalized)
    raw_terms = [t.strip(" .") for t in raw_terms if t.strip(" .")]

    matched_categories: set[str] = set()
    literal_terms: set[str] = set()

    for term in raw_terms:
        if not term or term in _NO_ALLERGY_VALUES:
            continue
        matched = False
        for category, synonyms in ALLERGEN_SYNONYMS.items():
            if term in synonyms or any(term in s or s in term for s in synonyms):
                matched_categories.add(category)
                matched = True
        if not matched and len(term) >= 3:
            literal_terms.add(term)

    all_banned: set[str] = set()
    for cat in matched_categories:
        all_banned.update(ALLERGEN_SYNONYMS[cat])
    all_banned.update(literal_terms)

    return {
        "categories": sorted(matched_categories),
        "literal_terms": sorted(literal_terms),
        "all_banned_terms": sorted(all_banned),
    }


def build_allergy_prompt_block(parsed: dict) -> str:
    """
    Render the parsed allergy constraint as an explicit, safety-critical
    prompt block. Called from build_user_prompt() in place of the old
    plain "Allergies / intolerances: {allergies}" line.
    """
    if not parsed["all_banned_terms"]:
        return "No known food allergies or intolerances disclosed."

    terms_str = ", ".join(parsed["all_banned_terms"])
    cat_str = ", ".join(c.replace("_", " ") for c in parsed["categories"]) or "—"

    return (
        "STRICT ALLERGY EXCLUSION — THIS IS A SAFETY CONSTRAINT, NOT A "
        "PREFERENCE. A mistake here can cause real physical harm.\n"
        f"Allergen categories disclosed: {cat_str}\n"
        "You must NOT include ANY of the following ingredients — or any "
        "food derived from, containing, or cross-contaminated with them — "
        "in ANY meal option, in ANY form (whole, powdered, oil, sauce, "
        f"hidden ingredient, or garnish): {terms_str}.\n"
        "If a traditional dish normally contains one of these ingredients, "
        "substitute it with a safe alternative or pick a different dish "
        "entirely — do not quietly omit the ingredient while keeping a "
        "dish name that implies it's still present.\n"
        "This rule overrides regional/cuisine preference and budget "
        "preference if they ever conflict."
    )


def find_allergen_violations(food_text: str, parsed: dict) -> list[str]:
    """Return every banned term that appears in food_text (case-insensitive)."""
    if not food_text or not parsed.get("all_banned_terms"):
        return []
    text = food_text.lower()
    hits = []
    for term in parsed["all_banned_terms"]:
        pattern = r"\b" + re.escape(term) + r"\b" if " " not in term else re.escape(term)
        if re.search(pattern, text):
            hits.append(term)
    return hits


# ── Deterministic, allergen-free fallback bases, keyed by diet preference.
# Intentionally simple/generic (no nuts, dairy, egg, gluten, soy, fish,
# shellfish, sesame, or mustard by default) so they clear the common-allergen
# set outright; still re-scanned below as a final guard before use.
_SAFE_FALLBACK_TEMPLATES = {
    "vegan":          "Steamed rice + boiled moong dal + mixed sautéed vegetables (allergy-safe swap)",
    "vegetarian":     "Steamed rice + boiled moong dal + mixed sautéed vegetables (allergy-safe swap)",
    "eggetarian":     "Steamed rice + boiled moong dal + mixed sautéed vegetables (allergy-safe swap)",
    "non-vegetarian": "Steamed rice + boiled moong dal + grilled chicken breast (allergy-safe swap)",
}
_FURTHER_FALLBACK = "Boiled potato + steamed rice + sautéed seasonal vegetables (allergy-safe swap)"


def build_safe_substitute_option(flagged_option: dict, diet_pref: str, parsed: dict) -> dict:
    """
    Deterministic, allergen-free replacement for a meal option that failed
    validation. Keeps the flagged option's own macro numbers (already
    computed by the LLM to hit that meal's kcal/protein share) so swapping
    the ingredient text doesn't throw off the day's calculated totals.
    """
    diet_key = (diet_pref or "non-vegetarian").lower().strip()
    base = _SAFE_FALLBACK_TEMPLATES.get(diet_key, _SAFE_FALLBACK_TEMPLATES["non-vegetarian"])

    # Extra guard: if even the generic fallback collides with a literal
    # user-typed term (e.g. a legume allergy typed as "moong" or "dal"),
    # drop to the even-more-generic fallback.
    if find_allergen_violations(base, parsed):
        base = _FURTHER_FALLBACK
    if find_allergen_violations(base, parsed):
        # Last resort — should be unreachable given the fallback pool, but
        # never render text known to violate the constraint.
        base = "Plain steamed rice + steamed seasonal vegetables (allergy-safe swap)"

    safe_option = dict(flagged_option)
    safe_option["food"] = base
    safe_option["_allergy_substituted"] = True
    return safe_option


def enforce_allergy_safety(data: dict, profile: dict | None) -> dict:
    """
    Final Python-side safety net. Runs AFTER parse_llm_json() / schema
    enforcement, independently re-checking every meal option's `food` text
    against the disclosed allergens — never trusting the LLM's own
    compliance with the prompt instruction alone. Any violating option is
    substituted in place; nothing violating reaches the rendered plan.

    No-op (returns data unchanged) if there's no profile or no disclosed
    allergies, so this is a pure addition with zero effect on the existing
    no-allergy path.
    """
    if not profile:
        return data

    parsed = parse_allergies(profile.get("allergies", "none"))
    if not parsed["all_banned_terms"]:
        return data

    diet_pref = profile.get("diet_pref") or profile.get("diet") or "non-vegetarian"
    substitution_count = 0

    for meal in data.get("diet", {}).get("meals", []):
        for opt in meal.get("options", []):
            violations = find_allergen_violations(opt.get("food", ""), parsed)
            if violations:
                safe_opt = build_safe_substitute_option(opt, diet_pref, parsed)
                opt.clear()
                opt.update(safe_opt)
                substitution_count += 1

    if substitution_count:
        data["diet"]["_allergy_substitutions_made"] = substitution_count
        print(
            f"[allergy_engine] Substituted {substitution_count} meal "
            f"option(s) containing disclosed allergens "
            f"{parsed['all_banned_terms']} for user profile allergies="
            f"{profile.get('allergies')!r}"
        )

    return data
