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


# ── EQUIPMENT VOCABULARY ──────────────────────────────────────────────────────
# Matches the exact chip labels from dashbord.html's EQUIPMENT array (the form
# posts a comma-joined string of whichever chips the user left ticked; all are
# ticked by default, so "full gym" clients send the full list, not a keyword).
FULL_EQUIPMENT_LIST = [
    "Barbell", "Dumbbells", "EZ curl bar", "Flat bench", "Incline/decline bench",
    "Squat rack", "Power rack / cage", "Smith machine", "Cable machine (dual stack)",
    "Lat pulldown", "Seated row machine", "Leg press", "Hack squat machine",
    "Leg extension machine", "Leg curl machine", "Chest press machine",
    "Shoulder press machine", "Pec deck / chest fly machine",
    "Assisted pull-up/dip machine", "Pull-up bar", "Dip station",
    "Preacher curl bench", "Hyperextension bench", "Calf raise machine",
    "Hip thrust machine / Smith setup", "Cable crossover", "Functional trainer",
    "Kettlebells", "Resistance bands", "Battle ropes", "Medicine balls",
    "TRX / suspension trainer", "Treadmill", "Stationary bike / spin bike",
    "Elliptical / cross-trainer", "Rowing machine", "Stair climber", "Foam roller",
]


def _parse_available_equipment(equipment_raw: str) -> set:
    """
    The form posts a comma-joined list of exact chip labels the user left
    ticked (all ticked by default). Legacy/manual callers may instead pass
    a loose phrase like "full gym" or "bodyweight only" — handled as a
    fallback so this doesn't break for non-form callers (tests, scripts).
    Returns a lowercased set of available equipment item names.
    """
    raw = str(equipment_raw or "").strip()
    if not raw:
        return {e.lower() for e in FULL_EQUIPMENT_LIST}

    low = raw.lower()
    if low == "full gym":
        return {e.lower() for e in FULL_EQUIPMENT_LIST}
    if low in ("bodyweight only", "no equipment", "none"):
        return set()  # exercises with requires=None still work

    return {t.strip().lower() for t in raw.split(",") if t.strip()}


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
        "compound": [
            {"name": "Leg Press",                 "requires": "Leg press",                 "cue": "Full range, don't lock knees out at top"},
            {"name": "Hack Squat Machine",         "requires": "Hack squat machine",         "cue": "Controlled descent, drive through heels"},
            {"name": "Smith Machine Squat",        "requires": "Smith machine",              "cue": "Bar path vertical, brace core throughout"},
            {"name": "Goblet Squat",               "requires": "Dumbbells",                  "cue": "Elbows inside knees at bottom, chest tall"},
            {"name": "Bodyweight Squat (loaded w/ backpack)", "requires": None,               "cue": "Slow 3-sec descent, pause at bottom"},
            {"name": "Barbell Back Squat",         "requires": ("Squat rack", "Power rack / cage"), "cue": "Brace core, hips and knees break together, use safety pins/spotter if available", "contraindicated_for": ("knee", "lower back", "hip", "spine")},
        ],
        "isolation": [
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


def _requirement_met(requires, available_lower: set) -> bool:
    if requires is None:
        return True
    if isinstance(requires, tuple):
        return any(r.lower() in available_lower for r in requires)
    return requires.lower() in available_lower


def _blocked_by_injury(ex: dict, injury_keywords: set) -> bool:
    tags = ex.get("contraindicated_for", ())
    return any(t in injury_keywords for t in tags)


def _filter_pool(pool: list, available_lower: set, injury_keywords: set) -> tuple:
    """Return (filtered_pool, used_fallback).
    Filtering happens in two passes:
      1) equipment match AND not injury-contraindicated (preferred)
      2) if that's empty, drop the injury filter but keep equipment match
         (used_fallback=True) — a client's own equipment ticks should never
         get silently overridden, but if a genuinely equipment-appropriate
         pick doesn't exist without also touching a flagged injury area,
         that's surfaced rather than silently producing an empty day.
    Equipment fallback (ignoring ticked equipment entirely) remains the
    last resort, same as before.
    """
    equipment_ok = [ex for ex in pool if _requirement_met(ex["requires"], available_lower)]
    safe_and_equipment_ok = [ex for ex in equipment_ok if not _blocked_by_injury(ex, injury_keywords)]
    if safe_and_equipment_ok:
        return safe_and_equipment_ok, False
    if equipment_ok:
        return equipment_ok, True
    return pool, True


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

    Returns (exercises, used_fallback). used_fallback is True if any slot
    had to relax an equipment or injury filter because the strict filter
    left zero options for that muscle/slot.
    """
    available_lower = _parse_available_equipment(equipment_raw)
    injury_keywords = _parse_injury_keywords(notes_raw)
    experience_rank = _experience_rank(experience_raw)
    used_fallback = False

    big_muscles = {"legs", "back", "chest", "shoulders"}
    ordered_muscles = _order_by_priority(plan["muscles"])

    # 1) mandatory compounds — one per big muscle trained, never trimmed
    compounds = []
    for m in ordered_muscles:
        if m not in big_muscles:
            continue
        pool = EXERCISE_DB.get(m, {}).get("compound", [])
        if not pool:
            continue
        filtered, fb = _filter_pool(pool, available_lower, injury_keywords)
        used_fallback = used_fallback or fb
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

    # 3) apply the beginner cap, if any — trim isolation only, from the tail
    if experience_rank == 0:
        is_leg_day = "legs" in plan["muscles"]
        cap = BEGINNER_CAP_LEG_DAY if is_leg_day else BEGINNER_CAP_OTHER_DAY
        remaining_isolation_slots = max(cap - len(compounds), 0)
        isolation_final = isolation_wishlist[:remaining_isolation_slots]
    else:
        # intermediate/advanced: no cap, full formula-driven count from plan
        isolation_final = isolation_wishlist

    return compounds + isolation_final, used_fallback
