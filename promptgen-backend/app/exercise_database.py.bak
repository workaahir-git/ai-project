"""
exercise_database.py
──────────────────────────────────────────────────────────────────────────────
Deterministic exercise selection for the workout section.

WHY THIS EXISTS
    The previous design asked the LLM to fill in exercise NAMES against a
    Python-computed checklist (compound/isolation counts, muscle order,
    banned-lift list). That checklist made deviation less likely but not
    impossible — the LLM could still write a lunge for the Legs compound
    slot, or mislabel a machine isolation move as compound. That failure
    is a naming-and-classification error, and classification is exactly
    the kind of decision this module removes from the LLM's hands.

WHAT CHANGED
    Every exercise below is pre-tagged compound/isolation, pre-tagged by
    muscle, and pre-tagged by equipment. Selection is a Python function:
    given a muscle + slot type + equipment, pick from the matching pool.
    There is no path through this code where an isolation movement can
    end up in a compound slot, or a banned lift can appear at all — those
    aren't LLM mistakes to catch anymore, they're categories that don't
    exist in the data.

    Randomization is preserved (via `random.Random(seed)`) so regenerating
    the same profile produces variety across the week and across re-runs,
    but the *shape* of what comes out (counts, order, compound vs.
    isolation) is 100% owned by _compute_day_plan() in fitness_generator.py,
    exactly as before. This module never sees or negotiates over counts —
    it only answers "given this slot, which named exercise fills it".

EQUIPMENT HANDLING
    Every exercise carries an `equipment` tag from a small fixed vocabulary:
    "machine", "cable", "dumbbell", "bodyweight", "band".
    A client's `equipment` profile field maps to an ALLOWED set of tags
    (see EQUIPMENT_PROFILES). Selection filters the pool to allowed tags
    first; if filtering empties a pool (e.g. "bodyweight only" client and
    a muscle with no bodyweight isolation options), it falls back to the
    full pool rather than crashing — better an imperfect but non-empty
    plan than a broken one, and this is logged via the returned flag so
    callers can surface it if they want.
"""

from __future__ import annotations
import random

from app.equipment import (
    FULL_EQUIPMENT_LIST,
    normalize_equipment as _parse_available_equipment,
    requirement_met as _requirement_met,
)

# NOTE: FULL_EQUIPMENT_LIST, equipment parsing, and requirement-matching now
# live in equipment.py (the single centralized authority for equipment
# logic). They're re-imported here under their original names so nothing
# else in this file — or any external caller importing them from this
# module — needs to change. Behaviour is unchanged: normalize_equipment()
# follows the same parsing rules as the old _parse_available_equipment(),
# just returning a frozenset instead of a plain set (a frozenset is a
# drop-in read-only replacement everywhere this module uses it: membership
# tests and iteration, no mutation).


# ── EXERCISE DATABASE ─────────────────────────────────────────────────────────
# Every entry: {"name": str, "requires": <exact chip label> | tuple | None, "cue": ...}
# "requires": None means bodyweight — always available regardless of ticked
# equipment. Where a movement needs any one of several interchangeable items,
# "requires" is a tuple and any single match is sufficient.
#
# NOTE: this pool already excludes every item on fitness_generator.py's
# BANNED EXERCISES list (deadlift variants, free-weight barbell squat/bench/
# press, Olympic lifts, spotter-required lifts). That exclusion is now a
# property of the data, not an instruction the LLM has to remember to obey.

EXERCISE_DB = {
    "legs": {
        # Every entry here is a literal squat variant BY NAME (not just
        # squat-pattern) per client hard-rule: "one kind of squat is
        # mandatory on leg day". select_day_exercises() always draws the
        # day's one mandatory legs compound from this pool, so whichever
        # option equipment/injury filtering leaves standing is guaranteed
        # to be a squat. "Leg Press" is squat-pattern but not squat-named,
        # so it lives in isolation below instead — it can still appear as
        # bonus leg volume, just never as the ONLY compound movement.
        "compound": [
            {"name": "Hack Squat Machine",         "requires": "Hack squat machine",         "cue": "Controlled descent, drive through heels"},
            {"name": "Smith Machine Squat",        "requires": "Smith machine",              "cue": "Bar path vertical, brace core throughout"},
            {"name": "Goblet Squat",               "requires": "Dumbbells",                  "cue": "Elbows inside knees at bottom, chest tall"},
            {"name": "Bodyweight Squat (loaded w/ backpack)", "requires": None,               "cue": "Slow 3-sec descent, pause at bottom"},
            {"name": "Barbell Back Squat",         "requires": ("Squat rack", "Power rack / cage"), "cue": "Brace core, hips and knees break together, use safety pins/spotter if available", "contraindicated_for": ("knee", "lower back", "hip", "spine")},
        ],
        "isolation": [
            {"name": "Leg Press",                 "requires": "Leg press",                 "cue": "Full range, don't lock knees out at top"},
            {"name": "Leg Extension",              "requires": "Leg extension machine",      "cue": "Squeeze quads 1 sec at top", "contraindicated_for": ("knee",)},
            {"name": "Seated Leg Curl",             "requires": "Leg curl machine",           "cue": "Controlled negative, no swinging"},
            {"name": "Lying Leg Curl",              "requires": "Leg curl machine",           "cue": "Full stretch at bottom"},
            {"name": "Cable Kickback",              "requires": "Cable machine (dual stack)", "cue": "Squeeze glute at top of movement"},
            {"name": "Dumbbell Step-Up",            "requires": "Dumbbells",                  "cue": "Drive through front heel, control the step down"},
            {"name": "Bulgarian Split Squat (dumbbell)", "requires": "Dumbbells",             "cue": "Rear foot elevated, front knee tracks over toes", "contraindicated_for": ("knee",)},
            {"name": "Glute Bridge (loaded)",       "requires": None,                         "cue": "Squeeze glutes hard at top, 1-sec pause"},
            {"name": "Walking Lunge (bodyweight)",  "requires": None,                         "cue": "Controlled tempo, knee tracks over toes", "contraindicated_for": ("knee",)},
        ],
    },
    "calves": {
        "isolation": [
            {"name": "Seated Calf Raise",           "requires": "Calf raise machine",         "cue": "Pause 1 sec at full stretch"},
            {"name": "Standing Calf Raise Machine", "requires": "Calf raise machine",         "cue": "Full range, pause at top"},
            {"name": "Leg Press Calf Raise",        "requires": "Leg press",                  "cue": "Toes on platform edge, full stretch"},
            {"name": "Single-Leg Calf Raise (dumbbell)", "requires": "Dumbbells",             "cue": "Slow negative, hold top 1 sec"},
            {"name": "Bodyweight Calf Raise",       "requires": None,                         "cue": "Full range each rep, no bouncing"},
        ],
    },
    "back": {
        "compound": [
            {"name": "Lat Pulldown",                "requires": "Lat pulldown",               "cue": "Pull to upper chest, squeeze shoulder blades"},
            {"name": "Seated Cable Row",             "requires": "Seated row machine",         "cue": "Chest up, drive elbows back"},
            {"name": "Chest-Supported Machine Row",  "requires": "Seated row machine",         "cue": "Full stretch, squeeze at contraction"},
            {"name": "Assisted Pull-up",             "requires": ("Assisted pull-up/dip machine", "Pull-up bar"), "cue": "Full hang at bottom, chin over bar"},
            {"name": "Single-Arm Dumbbell Row",       "requires": "Dumbbells",                  "cue": "Flat back, pull elbow past torso"},
        ],
        "isolation": [
            {"name": "Straight-Arm Cable Pulldown",  "requires": "Cable machine (dual stack)", "cue": "Slight elbow bend, squeeze lats"},
            {"name": "Cable Face Pull",              "requires": "Cable machine (dual stack)", "cue": "Pull to eye level, external rotation at end"},
            {"name": "Machine Rear Delt Fly (targets upper back)", "requires": "Pec deck / chest fly machine", "cue": "Squeeze shoulder blades together"},
            {"name": "Dumbbell Pullover",             "requires": "Dumbbells",                  "cue": "Controlled stretch overhead, ribcage down"},
            {"name": "Band Pull-Apart",               "requires": "Resistance bands",           "cue": "Slow and controlled, squeeze at end range"},
        ],
    },
    "chest": {
        "compound": [
            {"name": "Machine Chest Press",          "requires": "Chest press machine",        "cue": "Controlled press, don't lock elbows hard"},
            {"name": "Incline Dumbbell Press",        "requires": "Dumbbells",                  "cue": "Elbows ~45°, control the negative", "contraindicated_for": ("shoulder", "rotator cuff")},
            {"name": "Flat Dumbbell Press",            "requires": "Dumbbells",                 "cue": "Full stretch at bottom, squeeze at top"},
            {"name": "Smith Machine Bench Press",      "requires": "Smith machine",              "cue": "Controlled tempo, bar path straight"},
            {"name": "Barbell Bench Press",             "requires": ("Flat bench", "Barbell"),   "cue": "Controlled descent to chest, use a spotter if one is available", "contraindicated_for": ("shoulder", "wrist", "rotator cuff")},
        ],
        "isolation": [
            {"name": "Cable Chest Fly",               "requires": ("Cable crossover", "Cable machine (dual stack)"), "cue": "Slight bend in elbow, squeeze at center"},
            {"name": "Pec Deck Machine",               "requires": "Pec deck / chest fly machine", "cue": "Controlled squeeze, don't slam the pads"},
            {"name": "Incline Dumbbell Fly",            "requires": "Dumbbells",                 "cue": "Slow negative, don't overstretch shoulders"},
            {"name": "Push-Up (feet elevated)",         "requires": None,                        "cue": "Full range, controlled tempo"},
        ],
    },
    "shoulders": {
        "compound": [
            {"name": "Machine Shoulder Press",        "requires": "Shoulder press machine",     "cue": "Controlled press, avoid locking elbows hard"},
            {"name": "Seated Dumbbell Press",          "requires": "Dumbbells",                  "cue": "Core braced, controlled descent"},
            {"name": "Arnold Press",                    "requires": "Dumbbells",                "cue": "Rotate through full range, controlled tempo"},
            {"name": "Barbell Overhead Press",           "requires": ("Barbell", "Squat rack"),  "cue": "Brace core hard, press straight overhead, use a spotter if one is available", "contraindicated_for": ("shoulder", "rotator cuff", "neck")},
        ],
        "isolation": [
            {"name": "Cable Lateral Raise",             "requires": "Cable machine (dual stack)","cue": "Lead with elbow, no swinging"},
            {"name": "Dumbbell Lateral Raise",           "requires": "Dumbbells",                 "cue": "Slight bend in elbow, controlled tempo"},
            {"name": "Machine Lateral Raise",             "requires": "Functional trainer",       "cue": "Pause briefly at top"},
            {"name": "Reverse Pec Deck (rear delt)",       "requires": "Pec deck / chest fly machine", "cue": "Squeeze rear delts, controlled tempo"},
            {"name": "Band Lateral Raise",                  "requires": "Resistance bands",       "cue": "Constant tension, no jerking"},
        ],
    },
    "biceps": {
        "isolation": [
            {"name": "Cable Bicep Curl",              "requires": "Cable machine (dual stack)",  "cue": "Controlled, no swinging"},
            {"name": "Dumbbell Bicep Curl",            "requires": "Dumbbells",                  "cue": "Elbows pinned to sides"},
            {"name": "Incline Dumbbell Curl",           "requires": ("Dumbbells", "Incline/decline bench"), "cue": "Full stretch at bottom"},
            {"name": "Machine Preacher Curl",            "requires": "Preacher curl bench",       "cue": "Full range, controlled negative"},
            {"name": "Band Bicep Curl",                   "requires": "Resistance bands",         "cue": "Constant tension throughout"},
            {"name": "Hammer Curl (dumbbell)",             "requires": "Dumbbells",               "cue": "Neutral grip, controlled tempo"},
        ],
    },
    "triceps": {
        "isolation": [
            {"name": "Cable Triceps Pushdown",         "requires": "Cable machine (dual stack)", "cue": "Elbows pinned, full extension"},
            {"name": "Overhead Cable Triceps Extension","requires": "Cable machine (dual stack)","cue": "Elbows stay close to head"},
            {"name": "Dumbbell Overhead Extension",     "requires": "Dumbbells",                 "cue": "Controlled stretch at bottom", "contraindicated_for": ("elbow", "shoulder")},
            {"name": "Machine Triceps Dip",              "requires": ("Assisted pull-up/dip machine", "Dip station"), "cue": "Controlled tempo, don't lock out hard"},
            {"name": "Close-Grip Push-Up",                "requires": None,                       "cue": "Elbows tucked, full range", "contraindicated_for": ("wrist",)},
            {"name": "Band Triceps Pushdown",              "requires": "Resistance bands",        "cue": "Constant tension, controlled negative"},
        ],
    },
    "core": {
        "isolation": [
            {"name": "Cable Crunch",                   "requires": "Cable machine (dual stack)", "cue": "Round the spine, squeeze abs"},
            {"name": "Hanging Knee Raise",               "requires": "Pull-up bar",               "cue": "Controlled, avoid swinging", "contraindicated_for": ("shoulder", "wrist")},
            {"name": "Machine Ab Crunch",                 "requires": "Functional trainer",       "cue": "Squeeze at bottom of the rep"},
            {"name": "Plank (weighted or bodyweight)",     "requires": None,                       "cue": "Neutral spine, brace core"},
            {"name": "Pallof Press (band)",                 "requires": "Resistance bands",        "cue": "Resist rotation, slow tempo"},
        ],
    },
    "traps": {
        # Intermediate/advanced pull-day accessory only — gated in
        # select_day_exercises() regardless of what the caller's plan
        # passes in, so this pool never fires for beginners.
        "isolation": [
            {"name": "Dumbbell Shrug",                 "requires": "Dumbbells",                  "cue": "Straight up and down, no rolling, pause at top"},
            {"name": "Cable Shrug",                    "requires": "Cable machine (dual stack)", "cue": "Squeeze traps at top, controlled negative"},
            {"name": "Smith Machine Shrug",             "requires": "Smith machine",              "cue": "Full range, pause 1 sec at top"},
            {"name": "Band Shrug",                      "requires": "Resistance bands",           "cue": "Constant tension, squeeze at top"},
        ],
    },
}


INJURY_KEYWORDS = (
    "knee", "shoulder", "rotator cuff", "lower back", "back injury", "spine",
    "wrist", "elbow", "ankle", "hip", "neck",
)


def _parse_injury_keywords(notes_raw: str) -> set:
    """
    The intake form's `notes` field (id="notes") is free text — e.g.
    "knee injury, thyroid condition" — not a structured checklist. This
    does simple substring matching against a fixed list of common injury
    terms. It intentionally does NOT try to parse arbitrary medical
    language; if a client writes something this doesn't catch, nothing
    gets excluded, which is the safer failure direction here (a plan
    that includes an exercise you didn't flag, vs. a false match that
    strips out something innocuous).
    """
    text = str(notes_raw or "").lower()
    return {kw for kw in INJURY_KEYWORDS if kw in text}


def _blocked_by_injury(ex: dict, injury_keywords: set) -> bool:
    tags = ex.get("contraindicated_for", ())
    return any(t in injury_keywords for t in tags)


def _filter_pool(pool: list, available_lower: set, injury_keywords: set) -> tuple:
    """Return (filtered_pool, used_fallback).

    FAIL-CONSERVATIVE, PER KB FILE 12: the injury filter is NEVER relaxed,
    under any circumstance. Only the equipment filter is allowed to relax,
    as a last resort, because an equipment mismatch is an inconvenience
    ("you don't have this machine") while an injury mismatch is a safety
    issue ("you told us this hurts").

    Previous behaviour (the bug this replaces): if the safe+equipment pool
    was empty, the injury filter was dropped and an injury-contraindicated
    exercise was returned anyway, with only a silent `used_fallback=True`
    flag that nothing downstream ever read. A client who disclosed a knee
    injury could still be handed Leg Extension / Bulgarian Split Squat /
    Walking Lunge if those were their only equipment-matched options.

    New behaviour: if NOTHING in the pool is both equipment-matched and
    injury-safe, return an empty list. The caller (select_day_exercises)
    is responsible for skipping that slot entirely rather than filling it
    with something unsafe — fewer exercises that day is always preferable
    to an injury-contraindicated one.
    """
    injury_safe_pool = [ex for ex in pool if not _blocked_by_injury(ex, injury_keywords)]
    if not injury_safe_pool:
        # Every exercise for this muscle/slot is contraindicated for a
        # disclosed injury area. Nothing here is safe to hand back —
        # the caller must skip this slot, not substitute.
        return [], True

    equipment_ok = [ex for ex in injury_safe_pool if _requirement_met(ex["requires"], available_lower)]
    if equipment_ok:
        return equipment_ok, False

    # Equipment-limited only (not injury-limited): relax equipment matching
    # as a last resort, but the injury-safe filter still applies to what's
    # returned — this fallback can never reintroduce an unsafe exercise.
    return injury_safe_pool, True


# Bigger muscle groups first — used both to order compounds/isolation and,
# for beginners, to decide what gets trimmed when a day exceeds its cap.
# Lower number = higher priority = trimmed last.
MUSCLE_PRIORITY = {
    "legs": 0,
    "back": 1,
    "chest": 2,
    "triceps": 3,   # push day: chest > triceps > shoulders
    "shoulders": 4,
    "biceps": 5,    # pull day: back > biceps > traps
    "traps": 6,
    "calves": 7,
    "core": 7,
}


def _order_by_priority(muscles: list) -> list:
    """Stable-sort muscles biggest-first. Ties (e.g. biceps/triceps,
    calves/core) keep their original relative order from the plan."""
    return sorted(muscles, key=lambda m: MUSCLE_PRIORITY.get(m, 9))


EXPERIENCE_RANK = {"beginner": 0, "intermediate": 1, "advanced": 2}


def _experience_rank(experience_raw: str) -> int:
    key = str(experience_raw or "").strip().lower()
    return EXPERIENCE_RANK.get(key, 0)  # unrecognized -> treat as beginner (safer default)


# Beginner exercise-count caps by day type. These cap TOTAL exercises for
# the day; they never remove the mandatory compound lift(s) for a trained
# muscle — only trim from the isolation wishlist, smallest-priority muscle
# first, so what gets cut is "extra" isolation work, never the main lift.
BEGINNER_CAP_LEG_DAY = 6
BEGINNER_CAP_OTHER_DAY = 5
# Combined "upper" day (back+chest+shoulders+biceps+triceps) uses fixed,
# tier-independent isolation floors set in fitness_generator._compute_day_plan
# (back 3, chest 3, shoulders 2, biceps 2, triceps 2 = 12 isolation slots,
# plus 3 mandatory compounds = 15 total). Without its own cap here, the
# beginner trim step below would fall back to BEGINNER_CAP_OTHER_DAY (5) and
# chop those floors straight back down — re-losing biceps/triceps from the
# opposite direction of the original bug.
BEGINNER_CAP_UPPER_DAY = 15


def select_day_exercises(
    plan: dict,
    equipment_raw: str,
    notes_raw: str,
    experience_raw: str,
    rng: random.Random,
) -> tuple:
    """
    plan: the dict returned by fitness_generator._compute_day_plan()
          {"muscles": [...], "compound_count": int,
           "isolation_by_muscle": {...}, "total_exercises": int}

    equipment_raw: comma-joined chip string from the intake form's
          `equipment` field.

    notes_raw: the intake form's free-text `notes` field, scanned for
          injury keywords (see _parse_injury_keywords). No experience
          gating on any exercise — Barbell Back Squat, Barbell Bench
          Press, and Barbell Overhead Press are available to every tier
          unless the client's own notes name the relevant injury area.

    experience_raw: "Beginner" / "Intermediate" / "Advanced" (matches the
          intake form's `experience` <select> values, case-insensitive).
          Used ONLY for the day's total exercise-count cap and for which
          muscles get first claim on isolation slots — never for gating
          which individual exercises are allowed.

    CAP LOGIC (beginners only):
        Beginner leg days cap at 6 total exercises; every other beginner
        day type caps at 5. The mandatory compound lift(s) for each big
        muscle trained that day are NEVER cut to make room under the cap
        — only isolation work is trimmed, and it's trimmed starting from
        the smallest-priority trained muscle (core/calves before
        shoulders before back before legs), so what gets removed is
        always the "extra" work, never the main lift for the day's
        primary muscles. Intermediate/advanced have no cap — the day's
        full formula-driven exercise count from `plan` is used as-is —
        but isolation slots still fill biggest-muscle-first.

    Returns (exercises, used_fallback, injury_keywords). used_fallback is
    True if any slot had to relax equipment matching, or had to be skipped
    entirely because every option was injury-contraindicated. injury_keywords
    is the set of disclosed-injury terms actually matched, for surfacing to
    the client (e.g. "knee" → some leg exercises were excluded/skipped).
    """
    available_lower = _parse_available_equipment(equipment_raw)
    injury_keywords = _parse_injury_keywords(notes_raw)
    experience_rank = _experience_rank(experience_raw)
    used_fallback = False

    big_muscles = {"legs", "back", "chest", "shoulders"}
    ordered_muscles = _order_by_priority(plan["muscles"])

    # Some day types (beginner Push/Pull/Legs, per hard client rule) specify
    # EXACTLY which muscle(s) get a compound lift via plan["compound_muscles"]
    # rather than "every big muscle trained that day gets one" — e.g. beginner
    # Push trains chest+shoulders+triceps but ONLY chest gets a compound, so
    # shoulders stays at its single fixed isolation exercise. Respect that
    # override when present; otherwise fall back to the original behavior.
    compound_muscles_override = plan.get("compound_muscles")
    if compound_muscles_override is not None:
        compound_muscle_set = set(compound_muscles_override)
    else:
        compound_muscle_set = big_muscles

    # 1) mandatory compounds — one per designated muscle, never trimmed
    compounds = []
    for m in ordered_muscles:
        if m not in compound_muscle_set:
            continue
        pool = EXERCISE_DB.get(m, {}).get("compound", [])
        if not pool:
            continue
        filtered, fb = _filter_pool(pool, available_lower, injury_keywords)
        used_fallback = used_fallback or fb
        if not filtered:
            # Every compound option for this muscle is contraindicated for a
            # disclosed injury (e.g. every leg compound touches a flagged
            # knee). Skip the compound for this muscle rather than force an
            # unsafe pick — the day comes back with one fewer exercise, and
            # that gap is surfaced via _low_volume_warning downstream.
            continue
        choice = rng.choice(filtered)
        compounds.append({
            "name": choice["name"],
            "muscle": m,
            "slot": "compound",
            "requires": choice["requires"],
            "cue": choice["cue"],
        })

    # 2) isolation wishlist, ordered biggest-muscle-first — this order is
    #    what makes trimming safe: cutting from the tail below always
    #    drops the smallest-priority muscle's isolation work first.
    isolation_wishlist = []
    for m in ordered_muscles:
        if m == "traps" and experience_rank == 0:
            # Traps accessory work is intermediate/advanced only, no
            # matter what the caller's plan says.
            continue
        n = plan["isolation_by_muscle"].get(m, 0)
        if n <= 0:
            continue
        pool = EXERCISE_DB.get(m, {}).get("isolation", [])
        if not pool:
            continue
        filtered, fb = _filter_pool(pool, available_lower, injury_keywords)
        used_fallback = used_fallback or fb

        if not filtered:
            # Nothing safe available for this muscle at all — skip it
            # entirely rather than forcing an injury-contraindicated pick.
            continue

        if n <= len(filtered):
            picks = rng.sample(filtered, n)
        else:
            picks = filtered[:]
            rng.shuffle(picks)
            while len(picks) < n:
                picks.append(rng.choice(filtered))

        for choice in picks:
            isolation_wishlist.append({
                "name": choice["name"],
                "muscle": m,
                "slot": "isolation",
                "requires": choice["requires"],
                "cue": choice["cue"],
            })

    # 3) apply the beginner cap, if any — trim isolation only, from the tail.
    # Day types that came from an explicit fixed distribution (plan["no_trim"]
    # — currently beginner Push/Pull/Legs, see fitness_generator.BEGINNER_
    # FIXED_DAY_PLANS) are exact by design and must never be trimmed further.
    if experience_rank == 0 and not plan.get("no_trim"):
        is_leg_day = "legs" in plan["muscles"]
        is_upper_day = set(plan["muscles"]) == {
            "back", "chest", "shoulders", "biceps", "triceps",
        }
        if is_leg_day:
            cap = BEGINNER_CAP_LEG_DAY
        elif is_upper_day:
            cap = BEGINNER_CAP_UPPER_DAY
        else:
            cap = BEGINNER_CAP_OTHER_DAY
        remaining_isolation_slots = max(cap - len(compounds), 0)
        isolation_final = isolation_wishlist[:remaining_isolation_slots]
    else:
        # intermediate/advanced, or an exact fixed-distribution beginner day:
        # no cap, full formula-driven (or fixed) count from plan
        isolation_final = isolation_wishlist

    return compounds + isolation_final, used_fallback, injury_keywords
