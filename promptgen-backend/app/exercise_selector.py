"""
exercise_selector.py
──────────────────────────────────────────────────────────────────────────────
The single authority for deterministic exercise selection.

WHY THIS EXISTS
    exercise_database.py's select_day_exercises() already does equipment
    filtering, injury filtering, muscle-priority ordering, and beginner-cap
    trimming — that logic isn't duplicated here, it's imported and reused.
    What this module adds is everything the task calls out that wasn't
    there before:

      * movement-pattern tagging for every exercise in the pool, so
        "duplicate movement prevention" and "movement-pattern protection"
        are decisions this module can actually make, not just muscle-level
        bookkeeping.
      * pattern-aware isolation sampling (_pick_diverse) that prefers
        covering distinct movement patterns before ever repeating one —
        e.g. for a 2-pick Shoulders isolation slot, prefer one lateral-raise
        variant + the rear-delt fly over two lateral-raise variants, when
        both are available.
      * a hard rule against literal duplicate exercises within a single
        day's picks (the previous isolation fallback could pad with
        rng.choice() past the pool size, which could repeat the exact same
        named exercise twice in one day — that path is removed here in
        favour of returning fewer, distinct exercises).
      * goal-aware filtering: for a recovery/deload-flagged goal, the
        heaviest free-weight compound lifts are deprioritized in favour of
        machine/dumbbell alternatives already in the pool — never emptying
        a pool to do it, same fail-safe principle as equipment/injury
        filtering below.
      * deterministic substitution: find_substitute() lets a caller (e.g.
        validator.py, when repairing a recoverable issue) ask for "the next
        best option for this muscle/slot, excluding these names, given
        these constraints" without re-deriving the filtering logic itself.

    WHAT THIS MODULE DOES NOT DO: it does not replace exercise_database.py's
    EXERCISE_DB pool, and it does not consume V7's exercise_database engine.
    Per the audit against the current codebase: production's 71-exercise
    pool is hand-tagged with the exact equipment-chip strings the frontend
    sends and the contraindication tags the injury filter depends on; V7's
    equivalent engine only has 5 fully-populated records. Swapping pools
    would silently break equipment/injury filtering for the rest. V7's
    exercise_database engine is used elsewhere (exercise_enrichment.py) as
    additional context, not as a replacement for this pool.

BACKWARD COMPATIBILITY
    select_day_exercises() keeps the exact same signature and 3-tuple
    return shape as exercise_database.select_day_exercises(), plus one new
    optional trailing parameter (`goal_raw`, default ""), so existing
    callers keep working unchanged. select_day_exercises_detailed() is the
    richer entry point new code (the orchestrator, validator.py) should
    prefer — it returns a dict with diagnostics callers can act on.
"""

from __future__ import annotations
import random

from app.equipment import normalize_equipment, requirement_met
from app.exercise_database import (
    EXERCISE_DB,
    INJURY_KEYWORDS,
    _parse_injury_keywords,
    _blocked_by_injury,
    _filter_pool,
    MUSCLE_PRIORITY,
    _order_by_priority,
    EXPERIENCE_RANK,
    _experience_rank,
    BEGINNER_CAP_LEG_DAY,
    BEGINNER_CAP_OTHER_DAY,
    BEGINNER_CAP_UPPER_DAY,
)
from app import knowledge_retriever as kb

__all__ = [
    "select_day_exercises",
    "select_day_exercises_detailed",
    "find_substitute",
    "get_pattern",
    "is_recovery_goal",
]


# ── MOVEMENT-PATTERN TAXONOMY ─────────────────────────────────────────────────
# Hand-tagged, not guessed at runtime — every exercise currently in
# EXERCISE_DB is listed explicitly, so this stays reviewable and doesn't
# silently misclassify a new pool entry as some fuzzy-matched pattern.
# Grouped by the standard strength-training movement-pattern taxonomy
# (squat / hinge-adjacent / lunge / push / pull / isolation-by-joint-action),
# which is what makes "don't give me two lateral raises" or "don't give me
# two ab-flexion moves" a decision this module can actually make.
EXERCISE_MOVEMENT_PATTERN: dict[str, str] = {
    # legs — compound (mandatory squat-named lifts, per hard client rule)
    "Hack Squat Machine": "squat",
    "Smith Machine Squat": "squat",
    "Goblet Squat": "squat",
    "Bodyweight Squat (loaded w/ backpack)": "squat",
    "Barbell Back Squat": "squat",
    # legs — isolation / accessory
    "Leg Press": "squat_accessory",
    "Leg Extension": "knee_extension",
    "Seated Leg Curl": "knee_flexion",
    "Lying Leg Curl": "knee_flexion",
    "Cable Kickback": "hip_extension",
    "Dumbbell Step-Up": "lunge",
    "Bulgarian Split Squat (dumbbell)": "lunge",
    "Glute Bridge (loaded)": "hip_extension",
    "Walking Lunge (bodyweight)": "lunge",
    # calves
    "Seated Calf Raise": "calf_raise",
    "Standing Calf Raise Machine": "calf_raise",
    "Leg Press Calf Raise": "calf_raise",
    "Single-Leg Calf Raise (dumbbell)": "calf_raise",
    "Bodyweight Calf Raise": "calf_raise",
    # back — compound
    "Lat Pulldown": "vertical_pull",
    "Seated Cable Row": "horizontal_pull",
    "Chest-Supported Machine Row": "horizontal_pull",
    "Assisted Pull-up": "vertical_pull",
    "Single-Arm Dumbbell Row": "horizontal_pull",
    # back — isolation
    "Straight-Arm Cable Pulldown": "vertical_pull_isolation",
    "Cable Face Pull": "horizontal_pull_isolation",
    "Machine Rear Delt Fly (targets upper back)": "horizontal_pull_isolation",
    "Dumbbell Pullover": "vertical_pull_isolation",
    "Band Pull-Apart": "horizontal_pull_isolation",
    # chest — compound
    "Machine Chest Press": "horizontal_push",
    "Incline Dumbbell Press": "horizontal_push",
    "Flat Dumbbell Press": "horizontal_push",
    "Smith Machine Bench Press": "horizontal_push",
    "Barbell Bench Press": "horizontal_push",
    # chest — isolation
    "Cable Chest Fly": "horizontal_push_isolation",
    "Pec Deck Machine": "horizontal_push_isolation",
    "Incline Dumbbell Fly": "horizontal_push_isolation",
    "Push-Up (feet elevated)": "horizontal_push_isolation",
    # shoulders — compound
    "Machine Shoulder Press": "vertical_push",
    "Seated Dumbbell Press": "vertical_push",
    "Arnold Press": "vertical_push",
    "Barbell Overhead Press": "vertical_push",
    # shoulders — isolation (the lateral-raise cluster is exactly the case
    # duplicate-movement prevention exists for; rear-delt fly is a distinct
    # pattern so it's preferred as the second pick over a second lateral
    # raise variant when a 2-pick slot allows it)
    "Cable Lateral Raise": "lateral_raise",
    "Dumbbell Lateral Raise": "lateral_raise",
    "Machine Lateral Raise": "lateral_raise",
    "Reverse Pec Deck (rear delt)": "horizontal_pull_isolation",
    "Band Lateral Raise": "lateral_raise",
    # biceps
    "Cable Bicep Curl": "elbow_flexion",
    "Dumbbell Bicep Curl": "elbow_flexion",
    "Incline Dumbbell Curl": "elbow_flexion",
    "Machine Preacher Curl": "elbow_flexion",
    "Band Bicep Curl": "elbow_flexion",
    "Hammer Curl (dumbbell)": "elbow_flexion",
    # triceps
    "Cable Triceps Pushdown": "elbow_extension",
    "Overhead Cable Triceps Extension": "elbow_extension",
    "Dumbbell Overhead Extension": "elbow_extension",
    "Machine Triceps Dip": "elbow_extension",
    "Close-Grip Push-Up": "elbow_extension",
    "Band Triceps Pushdown": "elbow_extension",
    # core
    "Cable Crunch": "core_flexion",
    "Hanging Knee Raise": "core_flexion",
    "Machine Ab Crunch": "core_flexion",
    "Plank (weighted or bodyweight)": "core_stability",
    "Pallof Press (band)": "core_stability",
    # traps
    "Dumbbell Shrug": "shrug",
    "Cable Shrug": "shrug",
    "Smith Machine Shrug": "shrug",
    "Band Shrug": "shrug",
}

# Movement-pattern protection: for these muscles, the mandatory compound
# slot must be filled by an exercise of this pattern whenever the slot is
# filled at all. Currently only "legs" carries an explicit hard rule (one
# kind of squat is mandatory on leg day) — formalized here so validator.py
# has something concrete to check against, rather than relying on the
# pool's shape alone to guarantee it.
PROTECTED_COMPOUND_PATTERN: dict[str, str] = {
    "legs": "squat",
}


def get_pattern(exercise_name: str) -> str:
    """Movement-pattern tag for a named exercise, or "unclassified" if the
    name isn't in the taxonomy (e.g. a future pool addition that hasn't
    been tagged yet — fails open to "unclassified" rather than raising, so
    a missed tagging never crashes selection, just loses dedup benefit for
    that one exercise)."""
    return EXERCISE_MOVEMENT_PATTERN.get(exercise_name, "unclassified")


def get_kb_context(exercise_name: str) -> dict | None:
    """
    Knowledge Base V7 enrichment for a named exercise, via
    app/knowledge_retriever.py — joint-stress detail, coaching cues, and
    KB-sourced pain-free substitutions, where a full V7 record exists for
    this exact exercise (a minority of the pool; see
    knowledge_retriever.py's module docstring). Returns None otherwise.
    Purely additive context attached to a selection result — never used to
    gate or exclude a pick, so its absence changes no selection behavior.
    """
    return kb.get_exercise_context(exercise_name)


# ── GOAL-AWARE FILTERING ──────────────────────────────────────────────────────
# Mirrors the keyword set fitness_generator.py already uses to detect a
# recovery/deload goal (kept identical so "recovery" means the same thing
# everywhere in the codebase, not a second definition drifting from the
# first).
_RECOVERY_GOAL_KEYWORDS = ("recovery", "recover", "deload", "injury", "rehab")

# The heaviest free-weight compound lifts in the pool — each already
# carries a contraindicated_for tag for common joint issues. For a
# recovery-flagged goal, these are deprioritized (excluded from the
# compound candidate pool) in favour of the machine/dumbbell alternatives
# that exist for the same muscle, IF doing so doesn't empty the pool.
# Never a full exclusion rule independent of what else is available —
# same fail-safe principle as equipment/injury filtering.
_RECOVERY_DEPRIORITIZED = frozenset({
    "Barbell Back Squat",
    "Barbell Overhead Press",
    "Barbell Bench Press",
})


def is_recovery_goal(goal_raw: str) -> bool:
    """True if the client's stated goal indicates recovery/deload/rehab
    intent, using the same keyword definition fitness_generator.py uses
    for sets/reps prescription — so goal-aware filtering here and
    goal-based programming elsewhere never disagree about what "recovery"
    means for the same client."""
    goal = str(goal_raw or "").lower()
    return any(kw in goal for kw in _RECOVERY_GOAL_KEYWORDS)


def _compound_pool_for_goal(muscle: str, is_recovery: bool) -> list:
    """The muscle's compound candidate pool, goal-adjusted. Deprioritizes
    (does not unconditionally remove) the heaviest free-weight lifts for a
    recovery goal — falls back to the full pool if excluding them would
    leave nothing."""
    pool = EXERCISE_DB.get(muscle, {}).get("compound", [])
    if not is_recovery:
        return pool
    trimmed = [ex for ex in pool if ex["name"] not in _RECOVERY_DEPRIORITIZED]
    return trimmed if trimmed else pool


# ── PATTERN-AWARE, DUPLICATE-FREE SAMPLING ────────────────────────────────────

def _pick_diverse(pool: list, n: int, rng: random.Random) -> list:
    """
    Pick up to n exercises from `pool` (already equipment/injury filtered),
    preferring to cover distinct movement patterns before ever repeating
    one, and never returning the same named exercise twice.

    Replaces the old "rng.sample if n fits, else pad past the pool size
    with rng.choice()" behaviour — that fallback could return the exact
    same exercise name twice in one day when a muscle's isolation pool was
    smaller than the day's target count. This returns at most len(pool)
    distinct exercises instead: a shorter day is preferable to a
    duplicated exercise (the low-volume-warning path downstream already
    handles a day coming back with fewer exercises than the formula
    targeted, for the same reason the injury filter can skip a slot
    entirely rather than force an unsafe pick).
    """
    if n <= 0 or not pool:
        return []

    if n >= len(pool):
        picks = pool[:]
        rng.shuffle(picks)
        return picks

    groups: dict[str, list] = {}
    for item in pool:
        groups.setdefault(get_pattern(item["name"]), []).append(item)

    group_keys = list(groups.keys())
    rng.shuffle(group_keys)
    for g in groups.values():
        rng.shuffle(g)

    picks: list = []
    gi = 0
    while len(picks) < n:
        if all(not g for g in groups.values()):
            break  # exhausted every group; n was already checked < len(pool) so this shouldn't hit
        key = group_keys[gi % len(group_keys)]
        group = groups[key]
        if group:
            picks.append(group.pop())
        gi += 1

    return picks


# ── CORE SELECTION ────────────────────────────────────────────────────────────

def select_day_exercises_detailed(
    plan: dict,
    equipment_raw: str,
    notes_raw: str,
    experience_raw: str,
    rng: random.Random,
    goal_raw: str = "",
) -> dict:
    """
    Full-detail exercise selection for one training day. Same inputs as
    exercise_database.select_day_exercises(), plus optional `goal_raw` for
    goal-aware compound filtering.

    Returns a dict:
        exercises        -> list of chosen exercise dicts (name, muscle,
                             slot, requires, cue, pattern)
        used_fallback     -> True if any slot had to relax equipment
                             matching, skip entirely due to injury
                             contraindication, or the recovery-goal
                             compound deprioritization couldn't be applied
                             without emptying a pool
        injury_keywords   -> set of disclosed-injury terms matched
        protected_patterns_met -> dict[muscle] -> bool, whether each
                             muscle with a protected compound pattern
                             (currently just "legs" -> "squat") actually
                             got that pattern (False only if the slot had
                             to be skipped entirely for injury reasons)
        patterns_used     -> dict[muscle] -> list of movement-pattern tags
                             actually selected that day, for validator.py's
                             duplicate-movement check
    """
    available_lower = normalize_equipment(equipment_raw)
    injury_keywords = _parse_injury_keywords(notes_raw)
    experience_rank = _experience_rank(experience_raw)
    recovery_goal = is_recovery_goal(goal_raw)
    used_fallback = False

    big_muscles = {"legs", "back", "chest", "shoulders"}
    ordered_muscles = _order_by_priority(plan["muscles"])

    compound_muscles_override = plan.get("compound_muscles")
    compound_muscle_set = (
        set(compound_muscles_override) if compound_muscles_override is not None else big_muscles
    )

    compounds = []
    protected_patterns_met: dict[str, bool] = {}
    patterns_used: dict[str, list] = {}

    for m in ordered_muscles:
        if m not in compound_muscle_set:
            continue
        pool = _compound_pool_for_goal(m, recovery_goal)
        if not pool:
            continue
        if len(pool) != len(EXERCISE_DB.get(m, {}).get("compound", [])):
            used_fallback = True  # recovery deprioritization actually changed the pool

        filtered, fb = _filter_pool(pool, available_lower, injury_keywords)
        used_fallback = used_fallback or fb
        if not filtered:
            if m in PROTECTED_COMPOUND_PATTERN:
                protected_patterns_met[m] = False
            continue

        choice = rng.choice(filtered)
        pattern = get_pattern(choice["name"])
        compounds.append({
            "name": choice["name"],
            "muscle": m,
            "slot": "compound",
            "requires": choice["requires"],
            "cue": choice["cue"],
            "pattern": pattern,
            "kb_context": get_kb_context(choice["name"]),
            "_exercise_id": choice.get("_exercise_id"),
        })
        patterns_used.setdefault(m, []).append(pattern)

        if m in PROTECTED_COMPOUND_PATTERN:
            protected_patterns_met[m] = (pattern == PROTECTED_COMPOUND_PATTERN[m])

    isolation_wishlist = []
    for m in ordered_muscles:
        if m == "traps" and experience_rank == 0:
            continue  # traps accessory work is intermediate/advanced only
        n = plan["isolation_by_muscle"].get(m, 0)
        if n <= 0:
            continue
        pool = EXERCISE_DB.get(m, {}).get("isolation", [])
        if not pool:
            continue
        filtered, fb = _filter_pool(pool, available_lower, injury_keywords)
        used_fallback = used_fallback or fb
        if not filtered:
            continue

        picks = _pick_diverse(filtered, n, rng)
        for choice in picks:
            pattern = get_pattern(choice["name"])
            isolation_wishlist.append({
                "name": choice["name"],
                "muscle": m,
                "slot": "isolation",
                "requires": choice["requires"],
                "cue": choice["cue"],
                "pattern": pattern,
                "kb_context": get_kb_context(choice["name"]),
                "_exercise_id": choice.get("_exercise_id"),
            })
            patterns_used.setdefault(m, []).append(pattern)

    if experience_rank == 0 and not plan.get("no_trim"):
        is_leg_day = "legs" in plan["muscles"]
        is_upper_day = set(plan["muscles"]) == {"back", "chest", "shoulders", "biceps", "triceps"}
        if is_leg_day:
            cap = BEGINNER_CAP_LEG_DAY
        elif is_upper_day:
            cap = BEGINNER_CAP_UPPER_DAY
        else:
            cap = BEGINNER_CAP_OTHER_DAY
        remaining_isolation_slots = max(cap - len(compounds), 0)
        isolation_final = isolation_wishlist[:remaining_isolation_slots]
    else:
        isolation_final = isolation_wishlist

    return {
        "exercises": compounds + isolation_final,
        "used_fallback": used_fallback,
        "injury_keywords": injury_keywords,
        "protected_patterns_met": protected_patterns_met,
        "patterns_used": patterns_used,
    }


def select_day_exercises(
    plan: dict,
    equipment_raw: str,
    notes_raw: str,
    experience_raw: str,
    rng: random.Random,
    goal_raw: str = "",
) -> tuple:
    """
    Drop-in replacement for exercise_database.select_day_exercises() with
    the identical (exercises, used_fallback, injury_keywords) return shape,
    so existing callers don't need to change. New callers that want the
    diagnostics (pattern coverage, protected-pattern status) should call
    select_day_exercises_detailed() instead.
    """
    detailed = select_day_exercises_detailed(
        plan, equipment_raw, notes_raw, experience_raw, rng, goal_raw,
    )
    return detailed["exercises"], detailed["used_fallback"], detailed["injury_keywords"]


# ── DETERMINISTIC SUBSTITUTION ────────────────────────────────────────────────

def find_substitute(
    muscle: str,
    slot: str,
    exclude_names,
    available_lower,
    injury_keywords,
    rng: random.Random,
    prefer_pattern: str | None = None,
    avoid_pattern: str | None = None,
    from_exercise_name: str | None = None,
) -> dict | None:
    """
    Find the next-best replacement exercise for `muscle`/`slot`, excluding
    any name in `exclude_names` (typically exercises already chosen
    elsewhere in the same day, to avoid an accidental cross-slot
    duplicate), subject to the same equipment+injury constraints as normal
    selection.

    Preference order:
      1. If `avoid_pattern` is given (repairing a duplicate-movement-
         pattern issue, where the goal is specifically NOT another exercise
         of the pattern already used), restrict candidates to a different
         pattern first — falls back to the full candidate list only if
         that would leave nothing (a duplicate pattern is still better
         than losing the exercise entirely).
      2. Same movement pattern as `prefer_pattern`, if given and available
         (a like-for-like swap — e.g. replacing an unavailable Barbell
         Back Squat with another squat-pattern option). Mutually exclusive
         with avoid_pattern in practice (callers use one or the other).
      3. If `from_exercise_name` is given and has a Knowledge Base V7
         enrichment record (knowledge_retriever.get_exercise_context)
         whose substitutions_pain_free names a candidate that matches one
         of the disclosed injury keywords, prefer that candidate — it's
         V7's own documented pain-free swap for exactly this exercise and
         condition, not a generic same-muscle pick. Only ever narrows
         among candidates already deemed safe by the equipment/injury
         filter above; never overrides it, and silently falls through if
         no V7 record exists or none of its suggested substitutes are
         actually in the candidate pool.
      4. Any equipment/injury-safe option not already excluded.
      5. None, if nothing safe and unused remains — the caller (validator.py)
         is responsible for deciding whether to drop the slot instead of
         forcing an unsafe or duplicate pick, matching the fail-conservative
         behaviour used everywhere else in this module.
    """
    pool = EXERCISE_DB.get(muscle, {}).get(slot, [])
    filtered, _ = _filter_pool(pool, available_lower, injury_keywords)
    candidates = [ex for ex in filtered if ex["name"] not in exclude_names]
    if not candidates:
        return None

    if avoid_pattern:
        different_pattern = [ex for ex in candidates if get_pattern(ex["name"]) != avoid_pattern]
        if different_pattern:
            candidates = different_pattern
        # else: fall through and pick from the full candidate list — every
        # remaining option shares the pattern we wanted to avoid, so a
        # duplicate is unavoidable here (validator.py reports this as an
        # unresolved warning, not silently).

    if prefer_pattern:
        same_pattern = [ex for ex in candidates if get_pattern(ex["name"]) == prefer_pattern]
        if same_pattern:
            return rng.choice(same_pattern)

    if injury_keywords and from_exercise_name:
        kb_matched = _kb_preferred_candidates(from_exercise_name, candidates, injury_keywords)
        if kb_matched:
            return rng.choice(kb_matched)

    return rng.choice(candidates)


def _kb_preferred_candidates(from_exercise_name: str, candidates: list, injury_keywords) -> list:
    """
    Given the exercise being replaced (`from_exercise_name`), check its V7
    enrichment record for a substitutions_pain_free entry keyed to one of
    the disclosed injury keywords, and return whichever of `candidates`
    matches that V7-recommended substitute name (exact or substring match,
    since V7's suggested names don't always exactly match a production
    pool name). Empty list if there's no V7 record for this exercise, no
    matching injury key, or the suggested substitute isn't in the pool —
    all of which are expected/common, not errors.
    """
    record = get_kb_context(from_exercise_name)
    if not record:
        return []
    pain_free = record.get("substitutions_pain_free") or {}
    if not pain_free:
        return []

    injury_lower = {str(k).lower() for k in injury_keywords}
    suggested_names = []
    for flag, suggestion in pain_free.items():
        flag_lower = str(flag).lower()
        if any(kw in flag_lower or flag_lower in kw for kw in injury_lower):
            suggested_names.append(str(suggestion).lower())
    if not suggested_names:
        return []

    return [
        ex for ex in candidates
        if any(sug in ex["name"].lower() or ex["name"].lower() in sug for sug in suggested_names)
    ]
