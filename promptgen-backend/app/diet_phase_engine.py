"""
diet_phase_engine.py — Engine 39 (Diet Phase Engine).

Full spec (KB engines["39"].spec_text): computes calorie targets, macro
splits, and diet-phase classification (bulk/cut/recomp/maintenance),
coordinated with the athlete's goal (Engine 25) and recovery capacity
(Engine 10). Per the spec's own status note, this is a V1 addition, not
ported from an older KB file — the old `engines/nutrition` module (the
12-domain package from the pre-refactor repo) explicitly only covers
supplement safety, never macros/meal planning, so there is no prior
tested logic to port in here. This is a fresh build against the spec.

WHAT ALREADY EXISTED VS. WHAT THIS MODULE ADDS
    fitness_generator.py's `_calculate_macros()` already implements the
    KB's Mifflin-St Jeor BMR/TDEE formula and a basic goal-string-matched
    surplus/deficit (fixed 18% cut / 10% bulk / maintenance). That part is
    correct and is NOT duplicated here — this module calls a passed-in
    BMR/TDEE (or computes it the same way, for standalone use/testing) and
    focuses on exactly what was missing:

      DP001–DP003: phase SELECTION driven by goal + training age, not just
                   a goal-string match (novice bulk vs. experienced recomp).
      DP004:       deficit is CAPPED at 15% when recovery capacity is low,
                   regardless of what the goal alone would prescribe.
      DP005:       a disclosed eating-disorder history blocks any
                   auto-generated deficit/surplus target and routes to
                   maintenance + a flag for human/coach review — this
                   never fires on inference, only on explicit disclosure.
      DP006:       phase duration tracking + a re-assessment flag once the
                   phase's expected duration has elapsed, instead of
                   running a bulk/cut indefinitely.

KNOWN GAPS (documented per this codebase's convention: no data, no
fabricated number)
    - No numeric `training_age_years` is collected anywhere in this app —
      only categorical `experience` (Beginner/Intermediate/Advanced). DP002
      /DP003 are approximated as experience == "beginner" -> < 1yr,
      otherwise -> >= 1yr. This is a documented approximation, not the
      literal spec field.
    - `phase_start_cycle` is not currently persisted anywhere by main.py.
      DP006's re-assessment flag only evaluates when the caller supplies
      both `phase_start_cycle` and `current_cycle`; with neither, this
      module returns needs_reassessment=False rather than guessing a
      phase age it has no record of. Wiring that persistence is a
      follow-up, not something to fake here.
    - refeed_frequency is a fixed per-phase default (see PHASE_TABLE), not
      computed from any adherence/metabolic-adaptation signal — no such
      signal exists in this app yet.
"""

from __future__ import annotations

import math

# ── Canonical phase table (spec's "Canonical Phase Rules") ─────────────────
# kcal_adjustment_pct: signed fraction applied to TDEE (before any DP004 cap).
# protein_per_kg: (low, high) band per phase.
PHASE_TABLE = {
    "bulk": {
        "kcal_adjustment_pct": 0.15,      # midpoint of +10-20%
        "protein_per_kg": (1.6, 2.2),
        "phase_duration_weeks": 12,       # midpoint of 8-16wk
        "refeed_frequency": "none",
    },
    "cut": {
        "kcal_adjustment_pct": -0.20,     # midpoint of -15-25%
        "protein_per_kg": (2.0, 2.6),
        "phase_duration_weeks": 9,        # midpoint of 6-12wk
        "refeed_frequency": "weekly",
    },
    "recomp": {
        "kcal_adjustment_pct": 0.0,       # +/-5%, held at 0 (spec allows either sign)
        "protein_per_kg": (1.8, 2.4),
        "phase_duration_weeks": 16,       # midpoint of 12-20wk
        "refeed_frequency": "none",
    },
    "maintenance": {
        "kcal_adjustment_pct": 0.0,
        "protein_per_kg": (1.6, 2.0),
        "phase_duration_weeks": None,     # ongoing, per spec
        "refeed_frequency": "none",
    },
}

DP004_DEFICIT_CAP_PCT = 0.15          # max deficit when recovery_capacity_score < 50
DP004_RECOVERY_THRESHOLD = 50

# Explicit-disclosure only — never inferred from body-composition talk,
# calorie questions, or restrictive-sounding goals alone. Matches the same
# "fails conservative, never guesses a clinical signal" philosophy as
# safety_engine.py's EMERGENCY_KEYWORDS / progression_engine's pain
# detection. This list intentionally stays short and explicit rather than
# trying to catch every possible phrasing — false negatives here are safer
# than the alternative of pattern-matching casual language as a disclosure.
_DISORDERED_EATING_DISCLOSURE_TERMS = (
    "eating disorder",
    "disordered eating",
    "anorexia",
    "bulimia",
    "history of an ed",
)


def _contains_disclosed_ed_history(notes_raw: str | None) -> bool:
    if not notes_raw:
        return False
    text = notes_raw.lower()
    return any(term in text for term in _DISORDERED_EATING_DISCLOSURE_TERMS)


def _bmr_tdee(profile: dict) -> tuple[float, float]:
    """Same Mifflin-St Jeor formula as fitness_generator.py's
    _calculate_macros() — duplicated here (not imported) so this module
    can be used/tested standalone without importing the whole generator.
    Kept numerically identical on purpose; if you change one, change both.
    """
    weight = float(profile["current_weight_kg"])
    height = float(profile["height_cm"])
    age = int(profile["age"])
    gender = str(profile.get("gender", "male")).lower()
    activity = float(profile.get("activity_level_factor", 1.55))

    if gender == "female":
        bmr = 10 * weight + 6.25 * height - 5 * age - 161
    else:
        bmr = 10 * weight + 6.25 * height - 5 * age + 5

    tdee = bmr * activity
    return bmr, tdee


def _select_phase(goal_raw: str, experience_raw: str) -> str:
    """DP001-DP003. Approximates training_age via experience (see module
    docstring's KNOWN GAPS)."""
    goal = (goal_raw or "").lower()
    is_novice = str(experience_raw or "intermediate").lower().startswith("beg")

    if "fat loss" in goal or "weight loss" in goal or "cut" in goal:
        return "cut"                                    # DP001
    if "muscle" in goal or "bulk" in goal or "gain" in goal or "mass" in goal:
        return "bulk" if is_novice else "recomp"         # DP002 / DP003
    return "maintenance"


def compute_diet_phase(
    profile: dict,
    recovery_capacity_score: int | None = None,
    notes_raw: str | None = None,
    phase_start_cycle: int | None = None,
    current_cycle: int | None = None,
) -> dict:
    """
    profile requires: current_weight_kg, height_cm, age, gender, goal,
    experience, activity_level_factor (same convention as
    fitness_generator.py's _calculate_macros()).

    recovery_capacity_score: Engine 10's capacity_score (0-100), pass the
        caller's already-computed value rather than re-deriving it here.
    notes_raw: free-text intake/check-in notes — checked ONLY for an
        explicit disordered-eating disclosure (DP005), same field
        progression_engine.py already scans for pain language.

    Returns a dict matching the KB schema (diet_phase engine v1.0.0) plus
    an `flags` block explaining any override that fired.
    """
    bmr, tdee = _bmr_tdee(profile)
    weight_kg = float(profile["current_weight_kg"])

    flags = {
        "ed_history_disclosed": False,
        "deficit_capped_by_recovery": False,
        "needs_reassessment": False,
    }

    # DP005 — highest priority, same "block before anything else" pattern
    # as safety_engine.py's emergency check. Routes to maintenance and
    # flags for human review rather than silently doing nothing.
    if _contains_disclosed_ed_history(notes_raw):
        phase = "maintenance"
        flags["ed_history_disclosed"] = True
        kcal_adjustment_pct = 0.0
    else:
        phase = _select_phase(profile.get("goal", ""), profile.get("experience", "intermediate"))
        kcal_adjustment_pct = PHASE_TABLE[phase]["kcal_adjustment_pct"]

        # DP004 — cap deficit at 15% regardless of requested phase when
        # recovery capacity is low. Only ever tightens a deficit (negative
        # adjustment); never touches a surplus or maintenance.
        if (
            recovery_capacity_score is not None
            and recovery_capacity_score < DP004_RECOVERY_THRESHOLD
            and kcal_adjustment_pct < -DP004_DEFICIT_CAP_PCT
        ):
            kcal_adjustment_pct = -DP004_DEFICIT_CAP_PCT
            flags["deficit_capped_by_recovery"] = True

    target_kcal = round(tdee * (1 + kcal_adjustment_pct))

    protein_low, protein_high = PHASE_TABLE[phase]["protein_per_kg"]
    protein_per_kg = protein_high if phase in ("cut",) else round((protein_low + protein_high) / 2, 2)
    protein_g = round(weight_kg * protein_per_kg)

    protein_kcal = protein_g * 4
    remaining = max(target_kcal - protein_kcal, 0)
    fat_g = round((remaining * 0.28) / 9)
    carb_g = round((remaining * 0.72) / 4)

    # DP006 — only evaluated if the caller actually tracks phase age;
    # otherwise stays False rather than guessing (see module docstring).
    duration_weeks = PHASE_TABLE[phase]["phase_duration_weeks"]
    if (
        duration_weeks is not None
        and phase_start_cycle is not None
        and current_cycle is not None
    ):
        elapsed_weeks = max(current_cycle - phase_start_cycle, 0)  # 1 cycle == 1 week, same convention as other engines' cycle_number
        if elapsed_weeks >= duration_weeks:
            flags["needs_reassessment"] = True

    return {
        "bmr_kcal": round(bmr),
        "tdee_kcal": round(tdee),
        "phase": phase,
        "target_kcal": target_kcal,
        "macro_split": {
            "protein_g": protein_g,
            "carbs_g": carb_g,
            "fat_g": fat_g,
        },
        "protein_per_kg": protein_per_kg,
        "phase_duration_weeks": duration_weeks,
        "refeed_frequency": PHASE_TABLE[phase]["refeed_frequency"],
        "flags": flags,
    }
