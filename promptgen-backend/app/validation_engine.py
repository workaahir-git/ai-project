"""
validation_engine.py — Engine 29 (Data Validation), scoped to what this app
actually collects.

This is a LOGIC_ONLY engine (KB engines["29"] has spec_text, no data
profiles) — same category as safety_engine.py and programming_rules.py per
knowledge_base.py's module docstring. Full spec defines 5 rule categories
(DV001-DV005) over "athlete|exercise|program|session|recovery" datasets.
This module implements what real fields in this app's intake actually
support — the `/result` form in main.py (age, height, weight, target,
goal, experience, activity, days, equipment, diet, meals, region, budget,
allergies) — not the full generic spec, which also covers exercise/program/
session/recovery dataset types this app doesn't submit through a single
validated payload anywhere.

  DV001 (missing required field) — checked against the 4 fields the rest
        of the pipeline actually depends on to not silently fall back:
        goal, experience, activity_key, equipment. NOTE: main.py's
        `/result` already applies its own defaults ("Intermediate",
        "moderate", etc.) to every Form(...) field before this would ever
        see them — so DV001 is only meaningful if called BEFORE those
        defaults are applied (i.e. on the raw submitted values). Calling
        it after main.py's defaults are already in place will never fire
        DV001, which is correct: the field is genuinely no longer missing
        at that point, main.py just chose a value for the user.

  DV002 (enum outside canonical values) — checked against enums that
        actually exist as real code, not invented ones: `experience`
        against fitness_generator.EXPERIENCE keys (Beginner/Intermediate/
        Advanced), `activity_key` against fitness_generator.ACTIVITY_
        FACTOR keys, `diet_pref` against fitness_generator.DIET_TOKENS
        keys. Gender is intentionally NOT enum-checked — nothing else in
        this codebase enforces or reads gender as a closed set, it only
        ever gets string-interpolated into the prompt/template, so
        constraining it here would be inventing a rule the app doesn't
        actually have.

  DV004 (numeric value outside limits) — age (10-100), height_cm
        (100-250), weight_kg (30-300), target_weight_kg (30-300). These
        bounds are plain human-plausibility ranges, not sourced from any
        KB profile (none exists for this) — documented as such rather
        than implied to be KB-derived.

  DV003 (invalid engine reference) — NOT implemented. This app's intake
        carries no cross-engine ID references (no exercise_id/movement_id
        submitted by a user) for this rule to check. Nothing to validate.

  DV005 (safety contradiction) — NOT duplicated here. app/safety_engine.py
        already does real contradiction/emergency-keyword screening on
        `medical_notes` earlier in the pipeline; re-implementing that
        logic here would risk the two drifting out of sync. This module
        defers entirely — callers should run safety_engine's checks
        separately, same as they do today.

Matches the fail-conservative pattern used elsewhere in this app (main.py
never hard-rejects a signup over a bad enum, it substitutes a default) —
so every finding here is a WARNING, not an ERROR that blocks anything.
validation_status only ever reaches "failed" if a caller explicitly wants
a numeric field (DV004) treated as blocking; default is informational.

Never raises. Unknown/missing input just produces warnings, not an
exception — this must never be able to break plan generation by throwing.
"""

from __future__ import annotations

from app.fitness_generator import ACTIVITY_FACTOR, DIET_TOKENS

_EXPERIENCE_LEVELS = ("Beginner", "Intermediate", "Advanced")

_NUMERIC_BOUNDS = {
    "age":              (10, 100),
    "height_cm":        (100, 250),
    "weight_kg":        (30, 300),
    "target_weight_kg": (30, 300),
}

_REQUIRED_FIELDS = ("goal", "experience", "activity_key", "equipment")


def _to_number(value) -> float | None:
    """Best-effort numeric coercion — intake fields arrive as raw strings
    from Form(...) (e.g. "25", "" , "—"). Returns None rather than raising
    on anything non-numeric, so a placeholder like "—" is treated the same
    as genuinely missing, not a crash."""
    if value is None:
        return None
    try:
        return float(str(value).strip())
    except (ValueError, TypeError):
        return None


def validate_intake(raw: dict) -> dict:
    """raw: the pre-default form values (or an already-defaulted profile —
    see DV001 note above on what that changes). Returns the spec's schema
    shape: validation_status, validation_score, errors, warnings,
    corrected_fields. corrected_fields is always [] — this module only
    reports, it never mutates the caller's data."""
    errors: list[str] = []
    warnings: list[str] = []

    # DV001 — missing required fields
    for field in _REQUIRED_FIELDS:
        value = raw.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            warnings.append(f"DV001: '{field}' missing — pipeline will fall back to a default")

    # DV002 — enum outside canonical values (only checked if present at all)
    experience = raw.get("experience")
    if experience and experience not in _EXPERIENCE_LEVELS:
        warnings.append(
            f"DV002: experience='{experience}' not in {_EXPERIENCE_LEVELS} — "
            f"will not match any experience-specific rule branch"
        )

    activity_key = raw.get("activity_key")
    if activity_key and activity_key not in ACTIVITY_FACTOR:
        warnings.append(
            f"DV002: activity_key='{activity_key}' not in {tuple(ACTIVITY_FACTOR)} — "
            f"calorie activity factor lookup will use its own fallback"
        )

    diet_pref = raw.get("diet_pref")
    if diet_pref and diet_pref.strip().lower() not in DIET_TOKENS:
        warnings.append(
            f"DV002: diet_pref='{diet_pref}' does not exactly match {tuple(DIET_TOKENS)} — "
            f"fitness_generator's fuzzy match may still catch it, but an exact miss "
            f"silently falls back to non-vegetarian diet text"
        )

    # DV004 — numeric bounds
    for field, (low, high) in _NUMERIC_BOUNDS.items():
        num = _to_number(raw.get(field))
        if num is None:
            continue  # not DV001's job to re-flag here, and not numeric at all (e.g. "—")
        if not (low <= num <= high):
            warnings.append(f"DV004: {field}={num} outside plausible range [{low}, {high}]")

    validation_score = max(0, 100 - 10 * len(warnings) - 25 * len(errors))
    status = "failed" if errors else ("warning" if warnings else "passed")

    return {
        "validation_status": status,
        "validation_score": validation_score,
        "errors": errors,
        "warnings": warnings,
        "corrected_fields": [],
    }
