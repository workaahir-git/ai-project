"""
warmup_ramp_engine.py — Engine 40 (Warm-up & Load Prescription Ramp).

Full spec (KB engines["40"].spec_text): converts a prescribed working
weight (this app already computes one via load_prescription_engine.py)
into an actual warm-up ramp — real, loadable weights, rounded to
achievable plate/dumbbell increments. Closes the gap the spec names
directly: Engine 21 computes a working weight, but nothing before this
converted that into what a member actually puts on the bar for their
warm-up sets.

WHY THIS SCALES OFF WORKING WEIGHT, NOT 1RM
    The spec's schema has both `estimated_1rm_kg` and `working_set_pct`,
    but its own ramp-set percentages in the sample profile are relative to
    working_weight_kg (e.g. 45kg / 112kg working weight = 40%, matching
    the ramp table's "40%" row) — not relative to 1RM. That's convenient,
    because this app has never collected or estimated a 1RM anywhere
    (only logged working weights via workout_set_feedback). Rule 1 in the
    spec ("Ramp SHALL scale to the actual working weight, not a fixed
    absolute") makes this the correct behavior, not a workaround: this
    module takes load_prescription_engine's real, already-computed
    working_weight_kg and ramps relative to it. No 1RM is estimated or
    guessed anywhere in this module.

EQUIPMENT MODEL
    exercise_database.py's `requires` field uses an exact-chip vocabulary:
    'Barbell', 'Dumbbells', 'Kettlebells', 'Resistance bands',
    'Smith machine', 'TRX / suspension trainer', or None, plus tuples for
    either-of. There is no separate "machine"/"cable" tag in this app's
    actual exercise data (despite an older docstring elsewhere describing
    that vocabulary) — machine/cable exercises (e.g. "Machine Chest
    Press", "Single-Arm Cable Row") are simply tagged `requires: None`,
    the same as genuine bodyweight moves.

    BUGFIX (found during integration smoke-testing, not caught by this
    module's own unit tests since they only exercised `requires: None`
    together with `working_weight_kg=None`): the first version of this
    module treated ANY empty tag set as unloadable/RPE-only, which
    silently sent every machine/cable exercise through the RPE fallback
    even when load_prescription_engine had a real, numeric working weight
    for it. Fixed by keying "unloadable" off an EXPLICIT bands/TRX tag
    only — `requires: None` with a real working_weight_kg now gets a
    numeric ramp (see the machine/cable branch below), while `requires:
    None` with no working_weight_kg still correctly falls back to RPE
    (that part was never the bug — genuine bodyweight-only, no-baseline
    cases still work as intended).

    - 'Barbell'  -> real plate math against BAR_WEIGHT_KG + AVAILABLE_PLATES_KG.
    - 'Smith machine' -> plate math too, but flagged: real Smith machines
      vary in counterweighting (some are ~0kg effective, some ~20kg) and
      this app has no per-gym equipment record of which. Uses the same bar
      weight as a labeled ASSUMPTION, not a verified fact — see
      `bar_weight_is_assumed` in the return.
    - 'Dumbbells' / 'Kettlebells' -> per-hand rounding to
      DUMBBELL_INCREMENT_KG, no bar weight involved.
    - `requires: None` (machine/cable/bodyweight) WITH a real
      working_weight_kg -> numeric ramp, rounded to DUMBBELL_INCREMENT_KG
      granularity as a stand-in for real per-machine pin spacing, which
      this app doesn't track.
    - 'Resistance bands' / 'TRX / suspension trainer' (explicit tag), OR no
      working_weight_kg at all regardless of equipment -> RPE-anchored 2-set
      ramp, never a fabricated weight.

RAMP SHAPE
    - Compound, loadable equipment, working weight prescribed: full 6-row
      ramp per spec's canonical table (40/55/70/85/95/100%).
    - Isolation OR unloadable equipment (bodyweight/bands/TRX): shortened
      2-set ramp per spec rule 3 (50%x8, 75%x4), or RPE-anchored if there's
      no numeric weight at all.
    - No working_weight_kg available (load_prescription_engine returned
      None — e.g. no logged baseline yet): RPE-anchored ramp, same as the
      unloadable-equipment path. Consistent with this whole build's rule:
      no data, no fabricated number.
"""

from __future__ import annotations

# ── Plate/dumbbell physical constants ───────────────────────────────────────
BAR_WEIGHT_KG = 20.0
SMITH_MACHINE_BAR_WEIGHT_KG = 20.0   # labeled assumption — see module docstring
AVAILABLE_PLATES_KG = [25, 20, 15, 10, 5, 2.5, 1.25]   # one side of bar
DUMBBELL_INCREMENT_KG = 2.5          # per-hand rounding step
KETTLEBELL_INCREMENT_KG = 4.0        # standard KB step (e.g. 16/20/24kg)

# Equipment tags exactly as they appear in exercise_database.py's `requires`.
BARBELL_TAGS = {"Barbell"}
SMITH_TAGS = {"Smith machine"}
DUMBBELL_TAGS = {"Dumbbells"}
KETTLEBELL_TAGS = {"Kettlebells"}
UNLOADABLE_TAGS = {"Resistance bands", "TRX / suspension trainer"}   # + None (bodyweight)

# Canonical 6-row ramp (spec table), pct is of WORKING WEIGHT (see docstring).
FULL_RAMP = [
    (0.40, "8-10"),
    (0.55, "5"),
    (0.70, "3"),
    (0.85, "2"),
    (0.95, "1"),
]
# Shortened 2-set ramp for isolation/machine work (spec rule 3).
SHORT_RAMP = [
    (0.50, "8"),
    (0.75, "4"),
]
# Full-ramp threshold — spec: "working set >= 75% 1RM" gets the full ramp.
# Approximated here against load_prescription's own working_set_pct when
# supplied; defaults to treating compound lifts as full-ramp-eligible since
# this app's compound slot is, by definition, the heavy primary lift for
# the muscle that day (see exercise_database.py's slot design).
FULL_RAMP_MIN_WORKING_PCT = 0.75


def _requires_to_tags(requires) -> set:
    if requires is None:
        return set()
    if isinstance(requires, tuple):
        return set(requires)
    return {requires}


def _round_to_plates(target_weight_kg: float) -> tuple[float, bool]:
    """Rounds to the nearest ACHIEVABLE barbell weight (bar + plates on both
    sides), never an unloadable one. Returns (rounded_weight, exact_match).
    """
    if target_weight_kg <= BAR_WEIGHT_KG:
        return BAR_WEIGHT_KG, target_weight_kg == BAR_WEIGHT_KG

    target_per_side = (target_weight_kg - BAR_WEIGHT_KG) / 2
    smallest = AVAILABLE_PLATES_KG[-1]

    # Greedy: find nearest achievable per-side total that's a sum of
    # available plates, rounding to the nearest multiple of the smallest
    # plate (this app doesn't track a literal per-gym plate inventory
    # count, so "achievable" here means "a valid multiple of the smallest
    # increment", consistent with load_prescription_engine's own
    # ROUNDING_INCREMENT_KG approach elsewhere in this codebase).
    rounded_per_side = round(target_per_side / smallest) * smallest
    rounded_total = BAR_WEIGHT_KG + rounded_per_side * 2
    return round(rounded_total, 2), abs(rounded_total - target_weight_kg) < 0.01


def _round_to_increment(weight_kg: float, increment_kg: float) -> float:
    return round(round(weight_kg / increment_kg) * increment_kg, 2)


def build_warmup_ramp(
    exercise: dict,
    working_weight_kg: float | None,
    working_set_pct: float | None = None,
) -> dict:
    """
    exercise: one of exercise_database.py's exercise dicts — needs at least
        `name`, `requires`, `slot` ("compound"/"isolation"), `exercise_id`.
    working_weight_kg: load_prescription_engine's prescribed weight for
        this exercise/session, or None if no baseline exists yet.
    working_set_pct: optional — if load_prescription_engine ever starts
        tracking %1RM directly, pass it to gate full-vs-short ramp per
        spec's "75% 1RM" rule instead of the slot-based approximation.

    Returns a dict matching the KB schema (warmup_ramp engine v1.0.0).
    """
    tags = _requires_to_tags(exercise.get("requires"))
    is_isolation = exercise.get("slot") == "isolation"
    exercise_id = exercise.get("exercise_id") or exercise.get("_exercise_id")

    is_barbell = bool(tags & BARBELL_TAGS)
    is_smith = bool(tags & SMITH_TAGS)
    is_dumbbell = bool(tags & DUMBBELL_TAGS)
    is_kettlebell = bool(tags & KETTLEBELL_TAGS)
    # BUGFIX (found during Engine 40 integration smoke-testing): this
    # originally also treated an EMPTY tag set (`not tags`) as unloadable.
    # But exercise_database.py uses requires=None for machine/cable
    # exercises too (e.g. "Machine Chest Press", "Single-Arm Cable Row") --
    # None there means "no equipment checkbox gate needed", NOT "no
    # external load exists". Those exercises get real numeric
    # working_weight_kg from load_prescription_engine and were being sent
    # through the RPE-anchored fallback incorrectly. Only an EXPLICIT
    # bands/TRX tag means genuinely unloadable -- everything else with a
    # real working_weight_kg gets a numeric ramp (see the machine/cable
    # branch below).
    is_explicitly_unloadable = bool(tags & UNLOADABLE_TAGS)

    base = {
        "ramp_id": f"RAMP_{exercise_id or 'UNKNOWN'}",
        "exercise_id": exercise_id,
        "working_weight_kg": working_weight_kg,
        "bar_weight_kg": None,
        "smallest_plate_kg": None,
        "bar_weight_is_assumed": False,
        "rounding_strategy": "nearest_available",
    }

    # Rule 4 — no numeric weight to ramp from (no baseline logged yet, or
    # genuinely unloadable equipment like bands/TRX/bodyweight-only).
    # RPE-anchored, no fabricated numbers.
    if working_weight_kg is None or is_explicitly_unloadable:
        base["ramp_sets"] = [
            {"pct": None, "reps": "12", "weight_kg": None, "note": "RPE-anchored: light warm-up, RPE 4-5"},
            {"pct": None, "reps": "8", "weight_kg": None, "note": "RPE-anchored: approach set, RPE 6-7"},
        ]
        return base

    # Decide full vs. short ramp (rule 3: isolation/machine always short).
    eligible_for_full = not is_isolation
    if working_set_pct is not None:
        eligible_for_full = eligible_for_full and working_set_pct >= FULL_RAMP_MIN_WORKING_PCT
    ramp_table = FULL_RAMP if eligible_for_full else SHORT_RAMP

    if is_barbell or is_smith:
        bar_weight = SMITH_MACHINE_BAR_WEIGHT_KG if is_smith else BAR_WEIGHT_KG
        base["bar_weight_kg"] = bar_weight
        base["smallest_plate_kg"] = AVAILABLE_PLATES_KG[-1]
        base["bar_weight_is_assumed"] = is_smith
        ramp_sets = []
        for pct, reps in ramp_table:
            target = working_weight_kg * pct
            rounded, _ = _round_to_plates(max(target, bar_weight))
            ramp_sets.append({"pct": round(pct * 100), "reps": reps, "weight_kg": rounded})
        working_rounded, _ = _round_to_plates(working_weight_kg)
        ramp_sets.append({"pct": 100, "reps": "as prescribed", "weight_kg": working_rounded})
        base["working_weight_kg"] = working_rounded

    elif is_dumbbell or is_kettlebell:
        increment = KETTLEBELL_INCREMENT_KG if is_kettlebell else DUMBBELL_INCREMENT_KG
        base["smallest_plate_kg"] = increment
        ramp_sets = []
        for pct, reps in ramp_table:
            rounded = _round_to_increment(working_weight_kg * pct, increment)
            ramp_sets.append({"pct": round(pct * 100), "reps": reps, "weight_kg": rounded})
        working_rounded = _round_to_increment(working_weight_kg, increment)
        ramp_sets.append({"pct": 100, "reps": "as prescribed", "weight_kg": working_rounded})
        base["working_weight_kg"] = working_rounded

    else:
        # Machine/cable exercises (requires=None in this app's vocabulary,
        # or any tag not in the recognized set) -- has a real weight-stack
        # number, per the bugfix above, so ramp it numerically rather than
        # falling back to RPE. Weight-stack pins are typically ~2.5-5kg
        # increments; use the same DUMBBELL_INCREMENT_KG granularity as a
        # reasonable default since this app has no per-machine pin-spacing
        # data to be more precise than that.
        base["smallest_plate_kg"] = DUMBBELL_INCREMENT_KG
        ramp_sets = []
        for pct, reps in ramp_table:
            rounded = _round_to_increment(working_weight_kg * pct, DUMBBELL_INCREMENT_KG)
            ramp_sets.append({"pct": round(pct * 100), "reps": reps, "weight_kg": rounded})
        working_rounded = _round_to_increment(working_weight_kg, DUMBBELL_INCREMENT_KG)
        ramp_sets.append({"pct": 100, "reps": "as prescribed", "weight_kg": working_rounded})
        base["working_weight_kg"] = working_rounded

    base["ramp_sets"] = ramp_sets
    return base
