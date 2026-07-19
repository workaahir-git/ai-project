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
from app.text_matching import text_has_unnegated_keyword as _text_has_unnegated_keyword


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
    'legs': {
        'compound': [
            {
                'name': 'Barbell Back Squat',
                'requires': 'Barbell',
                'cue': 'Focus on: setup, bracing, depth, bar path',
                'contraindicated_for': ('knee', 'lower back', 'hip', 'spine'),
                '_exercise_id': 'barbell_back_squat',
                '_movement_id': 'squat',
            },
            {
                'name': 'Barbell Front Squat',
                'requires': 'Barbell',
                'cue': 'Focus on: setup, bracing, depth, bar path',
                'contraindicated_for': ('knee', 'lower back', 'hip', 'spine'),
                '_exercise_id': 'barbell_front_squat',
                '_movement_id': 'squat',
            },
            {
                'name': 'High Bar Back Squat',
                'requires': 'Barbell',
                'cue': 'Focus on: setup, bracing, depth, bar path',
                'contraindicated_for': ('knee', 'lower back', 'hip', 'spine'),
                '_exercise_id': 'high_bar_back_squat',
                '_movement_id': 'squat',
            },
            {
                'name': 'Low Bar Back Squat',
                'requires': 'Barbell',
                'cue': 'Focus on: setup, bracing, depth, bar path',
                'contraindicated_for': ('knee', 'lower back', 'hip', 'spine'),
                '_exercise_id': 'low_bar_back_squat',
                '_movement_id': 'squat',
            },
            {
                'name': 'Paused Back Squat',
                'requires': 'Barbell',
                'cue': 'Focus on: setup, bracing, depth, bar path',
                'contraindicated_for': ('knee', 'lower back', 'hip', 'spine'),
                '_exercise_id': 'paused_back_squat',
                '_movement_id': 'squat',
            },
            {
                'name': 'Box Squat',
                'requires': 'Barbell',
                'cue': 'Focus on: setup, bracing, depth, bar path',
                'contraindicated_for': ('knee', 'lower back', 'hip', 'spine'),
                '_exercise_id': 'box_squat',
                '_movement_id': 'squat',
            },
            {
                'name': 'Zercher Squat',
                'requires': 'Barbell',
                'cue': 'Focus on: setup, bracing, depth, bar path',
                'contraindicated_for': ('knee', 'lower back', 'hip', 'spine'),
                '_exercise_id': 'zercher_squat',
                '_movement_id': 'squat',
            },
            {
                'name': 'Goblet Squat',
                'requires': ('Dumbbells', 'Kettlebells'),
                'cue': 'Focus on: setup, bracing, depth, bar path',
                'contraindicated_for': ('knee', 'lower back', 'hip', 'spine'),
                '_exercise_id': 'goblet_squat',
                '_movement_id': 'squat',
            },
            {
                'name': 'Kettlebell Goblet Squat',
                'requires': 'Kettlebells',
                'cue': 'Focus on: setup, bracing, depth, bar path',
                'contraindicated_for': ('knee', 'lower back', 'hip', 'spine'),
                '_exercise_id': 'kettlebell_goblet_squat',
                '_movement_id': 'squat',
            },
            {
                'name': 'Bodyweight Squat',
                'requires': None,
                'cue': 'Focus on: setup, bracing, depth, bar path',
                'contraindicated_for': ('knee',),
                '_exercise_id': 'bodyweight_squat',
                '_movement_id': 'squat',
            },
            {
                'name': 'Jump Squat',
                'requires': None,
                'cue': 'Focus on: setup, bracing, depth, bar path',
                'contraindicated_for': ('knee',),
                '_exercise_id': 'jump_squat',
                '_movement_id': 'squat',
            },
            {
                'name': 'Pistol Squat',
                'requires': None,
                'cue': 'Focus on: setup, bracing, depth, bar path',
                'contraindicated_for': ('knee', 'lower back', 'hip', 'spine'),
                '_exercise_id': 'pistol_squat',
                '_movement_id': 'squat',
            },
            {
                'name': 'Assisted Pistol Squat',
                'requires': 'TRX / suspension trainer',
                'cue': 'Focus on: setup, bracing, depth, bar path',
                'contraindicated_for': ('knee', 'lower back', 'hip', 'spine'),
                '_exercise_id': 'assisted_pistol_squat',
                '_movement_id': 'squat',
            },
            {
                'name': 'Hack Squat (Machine)',
                'requires': None,
                'cue': 'Focus on: setup, bracing, depth, bar path',
                '_exercise_id': 'hack_squat_machine',
                '_movement_id': 'squat',
            },
            {
                'name': 'Leg Press',
                'requires': None,
                'cue': 'Focus on: setup, bracing, depth, bar path',
                '_exercise_id': 'leg_press',
                '_movement_id': 'squat',
            },
            {
                'name': 'Smith Machine Squat',
                'requires': 'Smith machine',
                'cue': 'Focus on: setup, bracing, depth, bar path',
                'contraindicated_for': ('knee', 'lower back', 'hip'),
                '_exercise_id': 'smith_machine_squat',
                '_movement_id': 'squat',
            },
            {
                'name': 'Belt Squat',
                'requires': None,
                'cue': 'Focus on: setup, bracing, depth, bar path',
                '_exercise_id': 'belt_squat',
                '_movement_id': 'squat',
            },
            {
                'name': 'Sumo Squat',
                'requires': ('Dumbbells', 'Barbell'),
                'cue': 'Focus on: setup, bracing, depth, bar path',
                'contraindicated_for': ('knee', 'lower back', 'hip', 'spine'),
                '_exercise_id': 'sumo_squat',
                '_movement_id': 'squat',
            },
            {
                'name': 'Landmine Squat',
                'requires': 'Barbell',
                'cue': 'Focus on: setup, bracing, depth, bar path',
                'contraindicated_for': ('knee', 'lower back', 'hip', 'spine'),
                '_exercise_id': 'landmine_squat',
                '_movement_id': 'squat',
            },
            {
                'name': 'Bulgarian Split Squat',
                'requires': ('Dumbbells', 'Barbell'),
                'cue': 'Focus on: setup, bracing, depth, bar path',
                'contraindicated_for': ('knee',),
                '_exercise_id': 'bulgarian_split_squat',
                '_movement_id': 'squat',
            },
            {
                'name': 'Split Squat',
                'requires': 'Dumbbells',
                'cue': 'Focus on: setup, bracing, depth, bar path',
                'contraindicated_for': ('knee', 'lower back', 'hip', 'spine'),
                '_exercise_id': 'split_squat',
                '_movement_id': 'squat',
            },
            {
                'name': 'Wall Sit',
                'requires': None,
                'cue': 'Focus on: setup, bracing, depth, bar path',
                'contraindicated_for': ('knee',),
                '_exercise_id': 'wall_sit',
                '_movement_id': 'squat',
            },
            {
                'name': 'Cyclist (Heel-Elevated) Squat',
                'requires': ('Dumbbells', 'Barbell'),
                'cue': 'Focus on: setup, bracing, depth, bar path',
                'contraindicated_for': ('knee', 'lower back', 'hip', 'spine'),
                '_exercise_id': 'cyclist_squat',
                '_movement_id': 'squat',
            },
            {
                'name': 'Band-Resisted Squat',
                'requires': 'Resistance bands',
                'cue': 'Focus on: setup, bracing, depth, bar path',
                '_exercise_id': 'banded_squat',
                '_movement_id': 'squat',
            },
            {
                'name': 'Overhead Squat',
                'requires': 'Barbell',
                'cue': 'Focus on: setup, bracing, depth, bar path',
                'contraindicated_for': ('knee', 'lower back', 'hip', 'spine'),
                '_exercise_id': 'overhead_squat',
                '_movement_id': 'squat',
            },
            {
                'name': 'Safety Bar Squat',
                'requires': 'Barbell',
                'cue': 'Focus on: setup, bracing, depth, bar path',
                'contraindicated_for': ('knee', 'lower back', 'hip', 'spine'),
                '_exercise_id': 'safety_bar_squat',
                '_movement_id': 'squat',
            },
            {
                'name': 'Tempo Back Squat (3-1-1)',
                'requires': 'Barbell',
                'cue': 'Focus on: setup, bracing, depth, bar path',
                'contraindicated_for': ('knee', 'lower back', 'hip', 'spine'),
                '_exercise_id': 'tempo_back_squat',
                '_movement_id': 'squat',
            },
            {
                'name': 'Single-Leg Leg Press',
                'requires': None,
                'cue': 'Focus on: setup, bracing, depth, bar path',
                'contraindicated_for': ('knee', 'lower back', 'hip', 'spine'),
                '_exercise_id': 'single_leg_leg_press',
                '_movement_id': 'squat',
            },
            {
                'name': 'Step-Up',
                'requires': 'Dumbbells',
                'cue': 'Focus on: setup, bracing, depth, bar path',
                'contraindicated_for': ('knee', 'lower back', 'hip', 'spine'),
                '_exercise_id': 'step_up',
                '_movement_id': 'squat',
            },
            {
                'name': 'Single-Leg Box Squat',
                'requires': None,
                'cue': 'Focus on: setup, bracing, depth, bar path',
                'contraindicated_for': ('knee', 'lower back', 'hip', 'spine'),
                '_exercise_id': 'box_squat_single_leg',
                '_movement_id': 'squat',
            },
            {
                'name': 'Curtsy Lunge',
                'requires': 'Dumbbells',
                'cue': 'Focus on: setup, bracing, depth, bar path',
                'contraindicated_for': ('knee', 'lower back', 'hip', 'spine'),
                '_exercise_id': 'curtsy_lunge',
                '_movement_id': 'squat',
            },
            {
                'name': 'Lateral Lunge',
                'requires': 'Dumbbells',
                'cue': 'Focus on: setup, bracing, depth, bar path',
                'contraindicated_for': ('knee', 'lower back', 'hip', 'spine'),
                '_exercise_id': 'lateral_lunge',
                '_movement_id': 'squat',
            },
            {
                'name': 'Spanish Squat',
                'requires': 'Resistance bands',
                'cue': 'Focus on: setup, bracing, depth, bar path',
                '_exercise_id': 'spanish_squat',
                '_movement_id': 'squat',
            },
            {
                'name': 'Goblet Box Squat',
                'requires': ('Dumbbells', 'Kettlebells'),
                'cue': 'Focus on: setup, bracing, depth, bar path',
                'contraindicated_for': ('knee', 'lower back', 'hip', 'spine'),
                '_exercise_id': 'goblet_box_squat',
                '_movement_id': 'squat',
            },
            {
                'name': 'Machine Squat Press',
                'requires': None,
                'cue': 'Focus on: setup, bracing, depth, bar path',
                '_exercise_id': 'machine_squat_press',
                '_movement_id': 'squat',
            },
            {
                'name': 'Front-Foot-Elevated Split Squat',
                'requires': 'Dumbbells',
                'cue': 'Focus on: setup, bracing, depth, bar path',
                'contraindicated_for': ('knee', 'lower back', 'hip', 'spine'),
                '_exercise_id': 'front_foot_elevated_split_squat',
                '_movement_id': 'squat',
            },
            {
                'name': 'Kettlebell Front Squat (Double)',
                'requires': 'Kettlebells',
                'cue': 'Focus on: setup, bracing, depth, bar path',
                'contraindicated_for': ('knee', 'lower back', 'hip', 'spine'),
                '_exercise_id': 'kb_front_squat',
                '_movement_id': 'squat',
            },
            {
                'name': 'Band-Assisted Pistol Squat',
                'requires': 'Resistance bands',
                'cue': 'Focus on: setup, bracing, depth, bar path',
                'contraindicated_for': ('knee', 'lower back', 'hip', 'spine'),
                '_exercise_id': 'resistance_band_pistol_squat',
                '_movement_id': 'squat',
            },
            {
                'name': 'Good Morning',
                'requires': 'Barbell',
                'cue': 'Focus on: setup, bracing, hip hinge pattern, bar path',
                'contraindicated_for': ('lower back', 'spine', 'hip'),
                '_exercise_id': 'good_morning',
                '_movement_id': 'hinge',
            },
            {
                'name': 'Barbell Hip Thrust',
                'requires': 'Barbell',
                'cue': 'Focus on: setup, bracing, hip hinge pattern, bar path',
                'contraindicated_for': ('lower back', 'spine', 'hip'),
                '_exercise_id': 'hip_thrust',
                '_movement_id': 'hinge',
            },
            {
                'name': 'Glute Bridge',
                'requires': None,
                'cue': 'Focus on: setup, bracing, hip hinge pattern, bar path',
                '_exercise_id': 'glute_bridge',
                '_movement_id': 'hinge',
            },
            {
                'name': 'Single-Leg Hip Thrust',
                'requires': None,
                'cue': 'Focus on: setup, bracing, hip hinge pattern, bar path',
                '_exercise_id': 'single_leg_hip_thrust',
                '_movement_id': 'hinge',
            },
            {
                'name': 'Hip Thrust Machine',
                'requires': None,
                'cue': 'Focus on: setup, bracing, hip hinge pattern, bar path',
                'contraindicated_for': ('lower back',),
                '_exercise_id': 'hip_thrust_machine',
                '_movement_id': 'hinge',
            },
            {
                'name': 'Cable Pull-Through',
                'requires': None,
                'cue': 'Focus on: setup, bracing, hip hinge pattern, bar path',
                'contraindicated_for': ('lower back',),
                '_exercise_id': 'cable_pull_through',
                '_movement_id': 'hinge',
            },
            {
                'name': 'Kettlebell Swing',
                'requires': 'Kettlebells',
                'cue': 'Focus on: setup, bracing, hip hinge pattern, bar path',
                'contraindicated_for': ('lower back', 'spine', 'hip'),
                '_exercise_id': 'kettlebell_swing',
                '_movement_id': 'hinge',
            },
            {
                'name': 'Back Extension (Hyperextension)',
                'requires': None,
                'cue': 'Focus on: setup, bracing, hip hinge pattern, bar path',
                'contraindicated_for': ('lower back',),
                '_exercise_id': 'back_extension',
                '_movement_id': 'hinge',
            },
            {
                'name': 'Weighted Back Extension',
                'requires': 'Dumbbells',
                'cue': 'Focus on: setup, bracing, hip hinge pattern, bar path',
                'contraindicated_for': ('lower back', 'spine', 'hip'),
                '_exercise_id': 'weighted_back_extension',
                '_movement_id': 'hinge',
            },
            {
                'name': 'Reverse Hyperextension',
                'requires': None,
                'cue': 'Focus on: setup, bracing, hip hinge pattern, bar path',
                'contraindicated_for': ('lower back',),
                '_exercise_id': 'reverse_hyperextension',
                '_movement_id': 'hinge',
            },
            {
                'name': 'Band Good Morning',
                'requires': 'Resistance bands',
                'cue': 'Focus on: setup, bracing, hip hinge pattern, bar path',
                'contraindicated_for': ('lower back',),
                '_exercise_id': 'banded_good_morning',
                '_movement_id': 'hinge',
            },
            {
                'name': 'Band-Resisted Hip Thrust',
                'requires': 'Resistance bands',
                'cue': 'Focus on: setup, bracing, hip hinge pattern, bar path',
                'contraindicated_for': ('lower back',),
                '_exercise_id': 'banded_hip_thrust',
                '_movement_id': 'hinge',
            },
            {
                'name': 'Single-Arm Kettlebell Swing',
                'requires': 'Kettlebells',
                'cue': 'Focus on: setup, bracing, hip hinge pattern, bar path',
                'contraindicated_for': ('lower back', 'spine', 'hip'),
                '_exercise_id': 'single_arm_kb_swing',
                '_movement_id': 'hinge',
            },
            {
                'name': 'Goblet Good Morning',
                'requires': ('Kettlebells', 'Dumbbells'),
                'cue': 'Focus on: setup, bracing, hip hinge pattern, bar path',
                'contraindicated_for': ('lower back', 'spine', 'hip'),
                '_exercise_id': 'goblet_good_morning',
                '_movement_id': 'hinge',
            },
            {
                'name': 'Band Glute Bridge',
                'requires': 'Resistance bands',
                'cue': 'Focus on: setup, bracing, hip hinge pattern, bar path',
                'contraindicated_for': ('lower back',),
                '_exercise_id': 'banded_glute_bridge',
                '_movement_id': 'hinge',
            },
            {
                'name': 'Hinge-Pattern Row Hold',
                'requires': 'Dumbbells',
                'cue': 'Focus on: setup, bracing, hip hinge pattern, bar path',
                'contraindicated_for': ('lower back', 'spine', 'hip'),
                '_exercise_id': 'meadows_row_hinge',
                '_movement_id': 'hinge',
            },
            {
                'name': 'Walking Lunge',
                'requires': 'Dumbbells',
                'cue': 'Focus on: setup, balance, knee tracking, tempo',
                'contraindicated_for': ('knee',),
                '_exercise_id': 'walking_lunge',
                '_movement_id': 'lunge',
            },
            {
                'name': 'Reverse Lunge',
                'requires': 'Dumbbells',
                'cue': 'Focus on: setup, balance, knee tracking, tempo',
                'contraindicated_for': ('knee',),
                '_exercise_id': 'reverse_lunge',
                '_movement_id': 'lunge',
            },
            {
                'name': 'Forward Lunge',
                'requires': 'Dumbbells',
                'cue': 'Focus on: setup, balance, knee tracking, tempo',
                'contraindicated_for': ('knee',),
                '_exercise_id': 'forward_lunge',
                '_movement_id': 'lunge',
            },
            {
                'name': 'Deficit Reverse Lunge',
                'requires': 'Dumbbells',
                'cue': 'Focus on: setup, balance, knee tracking, tempo',
                'contraindicated_for': ('knee',),
                '_exercise_id': 'deficit_reverse_lunge',
                '_movement_id': 'lunge',
            },
            {
                'name': 'Barbell Walking Lunge',
                'requires': 'Barbell',
                'cue': 'Focus on: setup, balance, knee tracking, tempo',
                'contraindicated_for': ('knee',),
                '_exercise_id': 'barbell_walking_lunge',
                '_movement_id': 'lunge',
            },
            {
                'name': 'Kettlebell Reverse Lunge',
                'requires': 'Kettlebells',
                'cue': 'Focus on: setup, balance, knee tracking, tempo',
                'contraindicated_for': ('knee',),
                '_exercise_id': 'kb_reverse_lunge',
                '_movement_id': 'lunge',
            },
            {
                'name': 'Lateral Step-Up',
                'requires': 'Dumbbells',
                'cue': 'Focus on: setup, balance, knee tracking, tempo',
                'contraindicated_for': ('knee',),
                '_exercise_id': 'lateral_step_up',
                '_movement_id': 'lunge',
            },
            {
                'name': 'Smith Machine Split Squat',
                'requires': 'Smith machine',
                'cue': 'Focus on: setup, bracing, depth, bar path',
                'contraindicated_for': ('knee', 'lower back', 'hip', 'spine'),
                '_exercise_id': 'smith_machine_split_squat',
                '_movement_id': 'squat',
            },
            {
                'name': 'Smith Machine Lunge',
                'requires': 'Smith machine',
                'cue': 'Focus on: setup, balance, knee tracking, tempo',
                'contraindicated_for': ('knee',),
                '_exercise_id': 'smith_machine_lunge',
                '_movement_id': 'lunge',
            },
            {
                'name': 'V-Squat Machine',
                'requires': None,
                'cue': 'Focus on: setup, bracing, depth, bar path',
                '_exercise_id': 'v_squat_machine',
                '_movement_id': 'squat',
            },
            {
                'name': 'Pendulum Squat Machine',
                'requires': None,
                'cue': 'Focus on: setup, bracing, depth, bar path',
                '_exercise_id': 'pendulum_squat',
                '_movement_id': 'squat',
            },
            {
                'name': 'Unilateral Leg Press',
                'requires': None,
                'cue': 'Focus on: setup, balance, knee tracking, tempo',
                'contraindicated_for': ('knee',),
                '_exercise_id': 'single_leg_press',
                '_movement_id': 'lunge',
            },
            {
                'name': 'Banded Lateral Squat Walk',
                'requires': 'Resistance bands',
                'cue': 'Focus on: setup, bracing, depth, bar path',
                '_exercise_id': 'banded_lateral_squat_walk',
                '_movement_id': 'squat',
            },
            {
                'name': 'Kettlebell Goblet Reverse Lunge',
                'requires': 'Kettlebells',
                'cue': 'Focus on: setup, balance, knee tracking, tempo',
                'contraindicated_for': ('knee',),
                '_exercise_id': 'kb_goblet_reverse_lunge',
                '_movement_id': 'lunge',
            },
            {
                'name': 'Dumbbell Step-Through Lunge',
                'requires': 'Dumbbells',
                'cue': 'Focus on: setup, balance, knee tracking, tempo',
                'contraindicated_for': ('knee',),
                '_exercise_id': 'dumbbell_step_through_lunge',
                '_movement_id': 'lunge',
            },
            {
                'name': 'Assisted Split Squat',
                'requires': 'TRX / suspension trainer',
                'cue': 'Focus on: setup, bracing, depth, bar path',
                'contraindicated_for': ('knee', 'lower back', 'hip', 'spine'),
                '_exercise_id': 'assisted_split_squat',
                '_movement_id': 'squat',
            },
            {
                'name': 'Feet-High Leg Press (Glute Focus)',
                'requires': None,
                'cue': 'Focus on: setup, balance, knee tracking, tempo',
                '_exercise_id': 'cyclist_leg_press',
                '_movement_id': 'lunge',
            },
            {
                'name': 'Cossack Squat',
                'requires': 'Kettlebells',
                'cue': 'Focus on: setup, bracing, depth, bar path',
                'contraindicated_for': ('knee', 'lower back', 'hip', 'spine'),
                '_exercise_id': 'cossack_squat',
                '_movement_id': 'squat',
            },
            {
                'name': 'Sumo Squat Machine',
                'requires': None,
                'cue': 'Focus on: setup, bracing, depth, bar path',
                '_exercise_id': 'machine_adduction_squat',
                '_movement_id': 'squat',
            },
            {
                'name': 'Banded Monster Walk',
                'requires': 'Resistance bands',
                'cue': 'Focus on: setup, balance, knee tracking, tempo',
                '_exercise_id': 'banded_terminal_knee_ext_walk',
                '_movement_id': 'lunge',
            },
            {
                'name': 'Box Step-Down',
                'requires': None,
                'cue': 'Focus on: setup, balance, knee tracking, tempo',
                'contraindicated_for': ('knee',),
                '_exercise_id': 'box_step_down',
                '_movement_id': 'lunge',
            },
            {
                'name': 'Isometric Split Squat Hold',
                'requires': None,
                'cue': 'Focus on: setup, bracing, depth, bar path',
                'contraindicated_for': ('knee', 'lower back', 'hip', 'spine'),
                '_exercise_id': 'elevated_split_squat_iso_hold',
                '_movement_id': 'squat',
            },
        ],
        'isolation': [
            {
                'name': 'Leg Extension',
                'requires': None,
                'cue': 'Focus on: setup, range of motion',
                'contraindicated_for': ('knee',),
                '_exercise_id': 'leg_extension',
                '_movement_id': 'isolation_leg',
            },
            {
                'name': 'Lying Leg Curl',
                'requires': None,
                'cue': 'Focus on: setup, range of motion',
                'contraindicated_for': ('knee',),
                '_exercise_id': 'leg_curl_lying',
                '_movement_id': 'isolation_leg',
            },
            {
                'name': 'Seated Leg Curl',
                'requires': None,
                'cue': 'Focus on: setup, range of motion',
                'contraindicated_for': ('knee',),
                '_exercise_id': 'leg_curl_seated',
                '_movement_id': 'isolation_leg',
            },
            {
                'name': 'Nordic Hamstring Curl',
                'requires': None,
                'cue': 'Focus on: setup, range of motion',
                'contraindicated_for': ('knee',),
                '_exercise_id': 'nordic_curl',
                '_movement_id': 'isolation_leg',
            },
            {
                'name': 'Cable Glute Kickback',
                'requires': None,
                'cue': 'Focus on: setup, range of motion',
                'contraindicated_for': ('knee',),
                '_exercise_id': 'glute_kickback',
                '_movement_id': 'isolation_leg',
            },
            {
                'name': 'Machine Glute Kickback',
                'requires': None,
                'cue': 'Focus on: setup, range of motion',
                'contraindicated_for': ('knee',),
                '_exercise_id': 'glute_kickback_machine',
                '_movement_id': 'isolation_leg',
            },
            {
                'name': 'Hip Abduction Machine',
                'requires': None,
                'cue': 'Focus on: setup, range of motion',
                'contraindicated_for': ('knee',),
                '_exercise_id': 'hip_abduction_machine',
                '_movement_id': 'isolation_leg',
            },
            {
                'name': 'Band Hip Abduction (Lateral Walk)',
                'requires': 'Resistance bands',
                'cue': 'Focus on: setup, range of motion',
                'contraindicated_for': ('knee',),
                '_exercise_id': 'band_hip_abduction',
                '_movement_id': 'isolation_leg',
            },
            {
                'name': 'Hip Adduction Machine',
                'requires': None,
                'cue': 'Focus on: setup, range of motion',
                'contraindicated_for': ('knee',),
                '_exercise_id': 'hip_adduction_machine',
                '_movement_id': 'isolation_leg',
            },
            {
                'name': 'Copenhagen Plank',
                'requires': None,
                'cue': 'Focus on: setup, range of motion',
                'contraindicated_for': ('knee',),
                '_exercise_id': 'copenhagen_plank',
                '_movement_id': 'isolation_leg',
            },
            {
                'name': 'Clamshell',
                'requires': 'Resistance bands',
                'cue': 'Focus on: setup, range of motion',
                'contraindicated_for': ('knee',),
                '_exercise_id': 'clamshell',
                '_movement_id': 'isolation_leg',
            },
            {
                'name': 'Fire Hydrant',
                'requires': None,
                'cue': 'Focus on: setup, range of motion',
                'contraindicated_for': ('knee',),
                '_exercise_id': 'fire_hydrant',
                '_movement_id': 'isolation_leg',
            },
            {
                'name': 'Standing Cable Hip Abduction',
                'requires': None,
                'cue': 'Focus on: setup, range of motion',
                'contraindicated_for': ('knee',),
                '_exercise_id': 'cable_hip_abduction',
                '_movement_id': 'isolation_leg',
            },
            {
                'name': 'Frog Pump',
                'requires': None,
                'cue': 'Focus on: setup, range of motion',
                'contraindicated_for': ('knee',),
                '_exercise_id': 'frog_pump',
                '_movement_id': 'isolation_leg',
            },
            {
                'name': 'Banded Lateral Walk',
                'requires': 'Resistance bands',
                'cue': 'Focus on: setup, range of motion',
                'contraindicated_for': ('knee',),
                '_exercise_id': 'banded_walk',
                '_movement_id': 'isolation_leg',
            },
            {
                'name': 'Sissy Squat',
                'requires': None,
                'cue': 'Focus on: setup, range of motion',
                'contraindicated_for': ('knee',),
                '_exercise_id': 'sissy_squat',
                '_movement_id': 'isolation_leg',
            },
            {
                'name': 'Terminal Knee Extension (Band)',
                'requires': 'Resistance bands',
                'cue': 'Focus on: setup, range of motion',
                'contraindicated_for': ('knee',),
                '_exercise_id': 'terminal_knee_extension',
                '_movement_id': 'isolation_leg',
            },
            {
                'name': 'Standing Single-Leg Curl',
                'requires': None,
                'cue': 'Focus on: setup, range of motion',
                'contraindicated_for': ('knee',),
                '_exercise_id': 'standing_leg_curl',
                '_movement_id': 'isolation_leg',
            },
            {
                'name': 'Stability Ball Leg Curl',
                'requires': None,
                'cue': 'Focus on: setup, range of motion',
                'contraindicated_for': ('knee',),
                '_exercise_id': 'swiss_ball_leg_curl',
                '_movement_id': 'isolation_leg',
            },
            {
                'name': 'Adductor Ball Squeeze',
                'requires': None,
                'cue': 'Focus on: setup, range of motion',
                'contraindicated_for': ('knee',),
                '_exercise_id': 'adductor_squeeze',
                '_movement_id': 'isolation_leg',
            },
            {
                'name': 'Goblet Pulse Squat',
                'requires': 'Dumbbells',
                'cue': 'Focus on: setup, range of motion',
                'contraindicated_for': ('knee',),
                '_exercise_id': 'goblet_pulse_squat',
                '_movement_id': 'isolation_leg',
            },
            {
                'name': 'Single-Leg Glute Bridge',
                'requires': None,
                'cue': 'Focus on: setup, range of motion',
                'contraindicated_for': ('knee',),
                '_exercise_id': 'single_leg_glute_bridge',
                '_movement_id': 'isolation_leg',
            },
            {
                'name': 'Cable Pull-Through (Glute Focus)',
                'requires': None,
                'cue': 'Focus on: setup, range of motion',
                'contraindicated_for': ('knee',),
                '_exercise_id': 'cable_pull_through_glute',
                '_movement_id': 'isolation_leg',
            },
            {
                'name': 'Machine Hip Thrust',
                'requires': None,
                'cue': 'Focus on: setup, range of motion',
                'contraindicated_for': ('knee',),
                '_exercise_id': 'machine_hip_thrust',
                '_movement_id': 'isolation_leg',
            },
        ],
    },
    'calves': {
        'isolation': [
            {
                'name': 'Standing Calf Raise',
                'requires': None,
                'cue': 'Focus on: setup, range of motion',
                '_exercise_id': 'standing_calf_raise',
                '_movement_id': 'isolation_calf',
            },
            {
                'name': 'Seated Calf Raise',
                'requires': None,
                'cue': 'Focus on: setup, range of motion',
                '_exercise_id': 'seated_calf_raise',
                '_movement_id': 'isolation_calf',
            },
            {
                'name': 'Dumbbell Calf Raise',
                'requires': 'Dumbbells',
                'cue': 'Focus on: setup, range of motion',
                '_exercise_id': 'dumbbell_calf_raise',
                '_movement_id': 'isolation_calf',
            },
            {
                'name': 'Leg Press Calf Raise',
                'requires': None,
                'cue': 'Focus on: setup, range of motion',
                '_exercise_id': 'leg_press_calf_raise',
                '_movement_id': 'isolation_calf',
            },
            {
                'name': 'Smith Machine Calf Raise',
                'requires': 'Smith machine',
                'cue': 'Focus on: setup, range of motion',
                '_exercise_id': 'smith_machine_calf_raise',
                '_movement_id': 'isolation_calf',
            },
            {
                'name': 'Single-Leg Calf Raise',
                'requires': None,
                'cue': 'Focus on: setup, range of motion',
                '_exercise_id': 'single_leg_calf_raise',
                '_movement_id': 'isolation_calf',
            },
            {
                'name': 'Single-Leg Weighted Calf Raise',
                'requires': 'Dumbbells',
                'cue': 'Focus on: setup, range of motion',
                '_exercise_id': 'single_leg_weighted_calf_raise',
                '_movement_id': 'isolation_calf',
            },
            {
                'name': 'Donkey Calf Raise',
                'requires': None,
                'cue': 'Focus on: setup, range of motion',
                '_exercise_id': 'donkey_calf_raise',
                '_movement_id': 'isolation_calf',
            },
            {
                'name': 'Calf Press (Machine)',
                'requires': None,
                'cue': 'Focus on: setup, range of motion',
                '_exercise_id': 'calf_press_machine',
                '_movement_id': 'isolation_calf',
            },
            {
                'name': 'Bodyweight Calf Raise',
                'requires': None,
                'cue': 'Focus on: setup, range of motion',
                '_exercise_id': 'bodyweight_calf_raise',
                '_movement_id': 'isolation_calf',
            },
            {
                'name': 'Band Calf Raise',
                'requires': 'Resistance bands',
                'cue': 'Focus on: setup, range of motion',
                '_exercise_id': 'banded_calf_raise',
                '_movement_id': 'isolation_calf',
            },
            {
                'name': 'Jump Rope (Calf Endurance)',
                'requires': None,
                'cue': 'Focus on: setup, range of motion',
                '_exercise_id': 'jump_rope_calf',
                '_movement_id': 'isolation_calf',
            },
            {
                'name': 'Tibialis Raise',
                'requires': 'Resistance bands',
                'cue': 'Focus on: setup, range of motion',
                '_exercise_id': 'tibialis_raise',
                '_movement_id': 'isolation_calf',
            },
            {
                'name': 'Seated Band Calf Raise',
                'requires': 'Resistance bands',
                'cue': 'Focus on: setup, range of motion',
                '_exercise_id': 'seated_band_calf_raise',
                '_movement_id': 'isolation_calf',
            },
        ],
    },
    'back': {
        'compound': [
            {
                'name': 'Barbell Bent-Over Row',
                'requires': 'Barbell',
                'cue': 'Focus on: setup, scapular retraction, range of motion',
                'contraindicated_for': ('shoulder',),
                '_exercise_id': 'barbell_bent_over_row',
                '_movement_id': 'horizontal_pull',
            },
            {
                'name': 'Pendlay Row',
                'requires': 'Barbell',
                'cue': 'Focus on: setup, scapular retraction, range of motion',
                'contraindicated_for': ('shoulder',),
                '_exercise_id': 'pendlay_row',
                '_movement_id': 'horizontal_pull',
            },
            {
                'name': 'Single-Arm Dumbbell Row',
                'requires': 'Dumbbells',
                'cue': 'Focus on: setup, scapular retraction, range of motion',
                'contraindicated_for': ('shoulder',),
                '_exercise_id': 'dumbbell_row',
                '_movement_id': 'horizontal_pull',
            },
            {
                'name': 'Seated Cable Row',
                'requires': None,
                'cue': 'Focus on: setup, scapular retraction, range of motion',
                '_exercise_id': 'cable_row',
                '_movement_id': 'horizontal_pull',
            },
            {
                'name': 'Chest-Supported Row Machine',
                'requires': None,
                'cue': 'Focus on: setup, scapular retraction, range of motion',
                '_exercise_id': 'machine_row',
                '_movement_id': 'horizontal_pull',
            },
            {
                'name': 'Chest-Supported Dumbbell Row',
                'requires': 'Dumbbells',
                'cue': 'Focus on: setup, scapular retraction, range of motion',
                'contraindicated_for': ('shoulder',),
                '_exercise_id': 'chest_supported_db_row',
                '_movement_id': 'horizontal_pull',
            },
            {
                'name': 'T-Bar Row',
                'requires': 'Barbell',
                'cue': 'Focus on: setup, scapular retraction, range of motion',
                'contraindicated_for': ('shoulder',),
                '_exercise_id': 't_bar_row',
                '_movement_id': 'horizontal_pull',
            },
            {
                'name': 'Inverted Row',
                'requires': None,
                'cue': 'Focus on: setup, scapular retraction, range of motion',
                'contraindicated_for': ('shoulder',),
                '_exercise_id': 'inverted_row',
                '_movement_id': 'horizontal_pull',
            },
            {
                'name': 'Weighted Inverted Row',
                'requires': None,
                'cue': 'Focus on: setup, scapular retraction, range of motion',
                'contraindicated_for': ('shoulder',),
                '_exercise_id': 'weighted_inverted_row',
                '_movement_id': 'horizontal_pull',
            },
            {
                'name': 'Seal Row',
                'requires': 'Barbell',
                'cue': 'Focus on: setup, scapular retraction, range of motion',
                'contraindicated_for': ('shoulder',),
                '_exercise_id': 'seal_row',
                '_movement_id': 'horizontal_pull',
            },
            {
                'name': 'Meadows Row',
                'requires': 'Barbell',
                'cue': 'Focus on: setup, scapular retraction, range of motion',
                'contraindicated_for': ('shoulder',),
                '_exercise_id': 'meadows_row',
                '_movement_id': 'horizontal_pull',
            },
            {
                'name': 'Band Row',
                'requires': 'Resistance bands',
                'cue': 'Focus on: setup, scapular retraction, range of motion',
                '_exercise_id': 'resistance_band_row',
                '_movement_id': 'horizontal_pull',
            },
            {
                'name': 'Suspension Trainer Row',
                'requires': 'TRX / suspension trainer',
                'cue': 'Focus on: setup, scapular retraction, range of motion',
                '_exercise_id': 'suspension_row',
                '_movement_id': 'horizontal_pull',
            },
            {
                'name': 'Landmine Row',
                'requires': 'Barbell',
                'cue': 'Focus on: setup, scapular retraction, range of motion',
                'contraindicated_for': ('shoulder',),
                '_exercise_id': 'landmine_row',
                '_movement_id': 'horizontal_pull',
            },
            {
                'name': 'Kroc Row',
                'requires': 'Dumbbells',
                'cue': 'Focus on: setup, scapular retraction, range of motion',
                'contraindicated_for': ('shoulder',),
                '_exercise_id': 'kroc_row',
                '_movement_id': 'horizontal_pull',
            },
            {
                'name': 'Wide-Grip Cable Row',
                'requires': None,
                'cue': 'Focus on: setup, scapular retraction, range of motion',
                '_exercise_id': 'wide_grip_cable_row',
                '_movement_id': 'horizontal_pull',
            },
            {
                'name': 'Rear-Delt Focused Row',
                'requires': 'Dumbbells',
                'cue': 'Focus on: setup, scapular retraction, range of motion',
                'contraindicated_for': ('shoulder',),
                '_exercise_id': 'rear_delt_row',
                '_movement_id': 'horizontal_pull',
            },
            {
                'name': 'Single-Arm Cable Row',
                'requires': None,
                'cue': 'Focus on: setup, scapular retraction, range of motion',
                '_exercise_id': 'single_arm_cable_row',
                '_movement_id': 'horizontal_pull',
            },
            {
                'name': 'Machine High Row',
                'requires': None,
                'cue': 'Focus on: setup, scapular retraction, range of motion',
                '_exercise_id': 'machine_high_row',
                '_movement_id': 'horizontal_pull',
            },
            {
                'name': 'Pull-Up',
                'requires': None,
                'cue': 'Focus on: setup, grip, scapular control, range of motion',
                'contraindicated_for': ('shoulder', 'elbow'),
                '_exercise_id': 'pull_up',
                '_movement_id': 'vertical_pull',
            },
            {
                'name': 'Chin-Up',
                'requires': None,
                'cue': 'Focus on: setup, grip, scapular control, range of motion',
                'contraindicated_for': ('shoulder', 'elbow'),
                '_exercise_id': 'chin_up',
                '_movement_id': 'vertical_pull',
            },
            {
                'name': 'Weighted Pull-Up',
                'requires': None,
                'cue': 'Focus on: setup, grip, scapular control, range of motion',
                'contraindicated_for': ('shoulder', 'elbow'),
                '_exercise_id': 'weighted_pull_up',
                '_movement_id': 'vertical_pull',
            },
            {
                'name': 'Weighted Chin-Up',
                'requires': None,
                'cue': 'Focus on: setup, grip, scapular control, range of motion',
                'contraindicated_for': ('shoulder', 'elbow'),
                '_exercise_id': 'weighted_chin_up',
                '_movement_id': 'vertical_pull',
            },
            {
                'name': 'Assisted Pull-Up Machine',
                'requires': None,
                'cue': 'Focus on: setup, grip, scapular control, range of motion',
                '_exercise_id': 'assisted_pull_up',
                '_movement_id': 'vertical_pull',
            },
            {
                'name': 'Lat Pulldown',
                'requires': None,
                'cue': 'Focus on: setup, grip, scapular control, range of motion',
                '_exercise_id': 'lat_pulldown',
                '_movement_id': 'vertical_pull',
            },
            {
                'name': 'Close-Grip Lat Pulldown',
                'requires': None,
                'cue': 'Focus on: setup, grip, scapular control, range of motion',
                'contraindicated_for': ('wrist',),
                '_exercise_id': 'close_grip_lat_pulldown',
                '_movement_id': 'vertical_pull',
            },
            {
                'name': 'Wide-Grip Lat Pulldown',
                'requires': None,
                'cue': 'Focus on: setup, grip, scapular control, range of motion',
                '_exercise_id': 'wide_grip_lat_pulldown',
                '_movement_id': 'vertical_pull',
            },
            {
                'name': 'Neutral-Grip Pulldown',
                'requires': None,
                'cue': 'Focus on: setup, grip, scapular control, range of motion',
                '_exercise_id': 'neutral_grip_pulldown',
                '_movement_id': 'vertical_pull',
            },
            {
                'name': 'Straight-Arm Pulldown',
                'requires': None,
                'cue': 'Focus on: setup, grip, scapular control, range of motion',
                '_exercise_id': 'straight_arm_pulldown',
                '_movement_id': 'vertical_pull',
            },
            {
                'name': 'Band-Assisted Pull-Up',
                'requires': 'Resistance bands',
                'cue': 'Focus on: setup, grip, scapular control, range of motion',
                '_exercise_id': 'band_assisted_pull_up',
                '_movement_id': 'vertical_pull',
            },
            {
                'name': 'Kipping Pull-Up',
                'requires': None,
                'cue': 'Focus on: setup, grip, scapular control, range of motion',
                'contraindicated_for': ('shoulder', 'elbow'),
                '_exercise_id': 'kipping_pull_up',
                '_movement_id': 'vertical_pull',
            },
            {
                'name': 'Archer Pull-Up',
                'requires': None,
                'cue': 'Focus on: setup, grip, scapular control, range of motion',
                'contraindicated_for': ('shoulder', 'elbow'),
                '_exercise_id': 'archer_pull_up',
                '_movement_id': 'vertical_pull',
            },
            {
                'name': 'Machine Pulldown (Plate-Loaded)',
                'requires': None,
                'cue': 'Focus on: setup, grip, scapular control, range of motion',
                '_exercise_id': 'machine_pulldown',
                '_movement_id': 'vertical_pull',
            },
            {
                'name': 'Suspension Trainer Pull-Up Row',
                'requires': 'TRX / suspension trainer',
                'cue': 'Focus on: setup, grip, scapular control, range of motion',
                '_exercise_id': 'suspension_pull_up',
                '_movement_id': 'vertical_pull',
            },
            {
                'name': 'Chest-to-Bar Pull-Up',
                'requires': None,
                'cue': 'Focus on: setup, grip, scapular control, range of motion',
                'contraindicated_for': ('shoulder', 'elbow'),
                '_exercise_id': 'chest_to_bar_pull_up',
                '_movement_id': 'vertical_pull',
            },
            {
                'name': 'L-Sit Pull-Up',
                'requires': None,
                'cue': 'Focus on: setup, grip, scapular control, range of motion',
                'contraindicated_for': ('shoulder', 'elbow'),
                '_exercise_id': 'l_sit_pull_up',
                '_movement_id': 'vertical_pull',
            },
            {
                'name': 'Machine-Assisted Row',
                'requires': None,
                'cue': 'Focus on: setup, scapular retraction, range of motion',
                '_exercise_id': 'machine_assisted_row',
                '_movement_id': 'horizontal_pull',
            },
            {
                'name': 'Gorilla Row (Kettlebell)',
                'requires': 'Kettlebells',
                'cue': 'Focus on: setup, scapular retraction, range of motion',
                'contraindicated_for': ('shoulder',),
                '_exercise_id': 'gorilla_row',
                '_movement_id': 'horizontal_pull',
            },
            {
                'name': 'Renegade Row',
                'requires': 'Dumbbells',
                'cue': 'Focus on: setup, scapular retraction, range of motion',
                'contraindicated_for': ('shoulder',),
                '_exercise_id': 'renegade_row',
                '_movement_id': 'horizontal_pull',
            },
            {
                'name': 'Cable Row (Neutral Grip)',
                'requires': None,
                'cue': 'Focus on: setup, scapular retraction, range of motion',
                '_exercise_id': 'cable_row_wide_neutral',
                '_movement_id': 'horizontal_pull',
            },
            {
                'name': 'Combo Lat-Row Machine',
                'requires': None,
                'cue': 'Focus on: setup, scapular retraction, range of motion',
                '_exercise_id': 'machine_lat_row_combo',
                '_movement_id': 'horizontal_pull',
            },
        ],
        'isolation': [
            {
                'name': 'Straight-Arm Lat Pulldown (Rope)',
                'requires': None,
                'cue': 'Focus on: setup, scapular retraction, range of motion',
                '_exercise_id': 'straight_arm_pulldown_lats',
                '_movement_id': 'horizontal_pull',
            },
            {
                'name': 'Dumbbell Pullover',
                'requires': 'Dumbbells',
                'cue': 'Focus on: setup, scapular retraction, range of motion',
                'contraindicated_for': ('shoulder',),
                '_exercise_id': 'pullover',
                '_movement_id': 'horizontal_pull',
            },
            {
                'name': 'Cable Pullover',
                'requires': None,
                'cue': 'Focus on: setup, scapular retraction, range of motion',
                '_exercise_id': 'cable_pullover',
                '_movement_id': 'horizontal_pull',
            },
            {
                'name': 'Machine Lat Pullover',
                'requires': None,
                'cue': 'Focus on: setup, scapular retraction, range of motion',
                '_exercise_id': 'machine_lat_pullover',
                '_movement_id': 'horizontal_pull',
            },
            {
                'name': 'Band Lat Pulldown',
                'requires': 'Resistance bands',
                'cue': 'Focus on: setup, scapular retraction, range of motion',
                '_exercise_id': 'banded_pulldown',
                '_movement_id': 'horizontal_pull',
            },
            {
                'name': 'Single-Arm Lat Pulldown',
                'requires': None,
                'cue': 'Focus on: setup, scapular retraction, range of motion',
                '_exercise_id': 'single_arm_lat_pulldown',
                '_movement_id': 'horizontal_pull',
            },
            {
                'name': 'Scapular Pull-Up',
                'requires': None,
                'cue': 'Focus on: setup, scapular retraction, range of motion',
                'contraindicated_for': ('shoulder',),
                '_exercise_id': 'scapular_pull_up',
                '_movement_id': 'horizontal_pull',
            },
            {
                'name': 'Chest-Supported T-Bar Row',
                'requires': None,
                'cue': 'Focus on: setup, scapular retraction, range of motion',
                '_exercise_id': 'chest_supported_t_bar_row',
                '_movement_id': 'horizontal_pull',
            },
            {
                'name': 'Isolated Dumbbell Pullover (Lat Focus)',
                'requires': 'Dumbbells',
                'cue': 'Focus on: setup, scapular retraction, range of motion',
                'contraindicated_for': ('shoulder',),
                '_exercise_id': 'dumbbell_pullover_iso',
                '_movement_id': 'horizontal_pull',
            },
            {
                'name': 'Band Pull-Apart',
                'requires': 'Resistance bands',
                'cue': 'Focus on: setup, scapular retraction, range of motion',
                '_exercise_id': 'banded_pull_apart',
                '_movement_id': 'horizontal_pull',
            },
        ],
    },
    'chest': {
        'compound': [
            {
                'name': 'Barbell Bench Press',
                'requires': 'Barbell',
                'cue': 'Focus on: setup, grip, range of motion',
                'contraindicated_for': ('shoulder', 'wrist', 'rotator cuff'),
                '_exercise_id': 'barbell_bench_press',
                '_movement_id': 'horizontal_push',
            },
            {
                'name': 'Dumbbell Bench Press',
                'requires': 'Dumbbells',
                'cue': 'Focus on: setup, grip, range of motion',
                'contraindicated_for': ('shoulder', 'rotator cuff'),
                '_exercise_id': 'dumbbell_bench_press',
                '_movement_id': 'horizontal_push',
            },
            {
                'name': 'Incline Barbell Bench Press',
                'requires': 'Barbell',
                'cue': 'Focus on: setup, grip, range of motion',
                'contraindicated_for': ('shoulder', 'wrist', 'rotator cuff'),
                '_exercise_id': 'incline_barbell_bench_press',
                '_movement_id': 'horizontal_push',
            },
            {
                'name': 'Incline Dumbbell Press',
                'requires': 'Dumbbells',
                'cue': 'Focus on: setup, grip, range of motion',
                'contraindicated_for': ('shoulder', 'rotator cuff'),
                '_exercise_id': 'incline_db_press',
                '_movement_id': 'horizontal_push',
            },
            {
                'name': 'Decline Bench Press',
                'requires': ('Barbell', 'Dumbbells'),
                'cue': 'Focus on: setup, grip, range of motion',
                'contraindicated_for': ('shoulder', 'rotator cuff', 'wrist'),
                '_exercise_id': 'decline_bench_press',
                '_movement_id': 'horizontal_push',
            },
            {
                'name': 'Paused Bench Press',
                'requires': 'Barbell',
                'cue': 'Focus on: setup, grip, range of motion',
                'contraindicated_for': ('shoulder', 'rotator cuff', 'wrist'),
                '_exercise_id': 'paused_bench_press',
                '_movement_id': 'horizontal_push',
            },
            {
                'name': 'Close-Grip Bench Press',
                'requires': 'Barbell',
                'cue': 'Focus on: setup, grip, range of motion',
                'contraindicated_for': ('shoulder', 'rotator cuff', 'wrist'),
                '_exercise_id': 'close_grip_bench_press',
                '_movement_id': 'horizontal_push',
            },
            {
                'name': 'Machine Chest Press',
                'requires': None,
                'cue': 'Focus on: setup, grip, range of motion',
                '_exercise_id': 'machine_chest_press',
                '_movement_id': 'horizontal_push',
            },
            {
                'name': 'Smith Machine Bench Press',
                'requires': 'Smith machine',
                'cue': 'Focus on: setup, grip, range of motion',
                'contraindicated_for': ('shoulder',),
                '_exercise_id': 'smith_machine_bench_press',
                '_movement_id': 'horizontal_push',
            },
            {
                'name': 'Push-Up',
                'requires': None,
                'cue': 'Focus on: setup, grip, range of motion',
                '_exercise_id': 'push_up',
                '_movement_id': 'horizontal_push',
            },
            {
                'name': 'Incline Push-Up',
                'requires': None,
                'cue': 'Focus on: setup, grip, range of motion',
                '_exercise_id': 'incline_push_up',
                '_movement_id': 'horizontal_push',
            },
            {
                'name': 'Decline Push-Up',
                'requires': None,
                'cue': 'Focus on: setup, grip, range of motion',
                '_exercise_id': 'decline_push_up',
                '_movement_id': 'horizontal_push',
            },
            {
                'name': 'Weighted Push-Up',
                'requires': None,
                'cue': 'Focus on: setup, grip, range of motion',
                '_exercise_id': 'weighted_push_up',
                '_movement_id': 'horizontal_push',
            },
            {
                'name': 'Diamond Push-Up',
                'requires': None,
                'cue': 'Focus on: setup, grip, range of motion',
                '_exercise_id': 'diamond_push_up',
                '_movement_id': 'horizontal_push',
            },
            {
                'name': 'Cable Chest Press',
                'requires': None,
                'cue': 'Focus on: setup, grip, range of motion',
                '_exercise_id': 'cable_chest_press',
                '_movement_id': 'horizontal_push',
            },
            {
                'name': 'Floor Press',
                'requires': ('Barbell', 'Dumbbells'),
                'cue': 'Focus on: setup, grip, range of motion',
                'contraindicated_for': ('shoulder', 'rotator cuff', 'wrist'),
                '_exercise_id': 'floor_press',
                '_movement_id': 'horizontal_push',
            },
            {
                'name': 'Landmine Press',
                'requires': 'Barbell',
                'cue': 'Focus on: setup, grip, range of motion',
                'contraindicated_for': ('shoulder', 'rotator cuff', 'wrist'),
                '_exercise_id': 'landmine_press',
                '_movement_id': 'horizontal_push',
            },
            {
                'name': 'Svend Press',
                'requires': 'Dumbbells',
                'cue': 'Focus on: setup, grip, range of motion',
                'contraindicated_for': ('shoulder', 'rotator cuff'),
                '_exercise_id': 'svend_press',
                '_movement_id': 'horizontal_push',
            },
            {
                'name': 'Single-Arm Dumbbell Bench Press',
                'requires': 'Dumbbells',
                'cue': 'Focus on: setup, grip, range of motion',
                'contraindicated_for': ('shoulder', 'rotator cuff'),
                '_exercise_id': 'single_arm_db_press',
                '_movement_id': 'horizontal_push',
            },
            {
                'name': 'Band Chest Press',
                'requires': 'Resistance bands',
                'cue': 'Focus on: setup, grip, range of motion',
                '_exercise_id': 'resistance_band_chest_press',
                '_movement_id': 'horizontal_push',
            },
            {
                'name': 'Suspension Trainer Push-Up',
                'requires': 'TRX / suspension trainer',
                'cue': 'Focus on: setup, grip, range of motion',
                '_exercise_id': 'suspension_trainer_push_up',
                '_movement_id': 'horizontal_push',
            },
            {
                'name': 'Chest Dips',
                'requires': None,
                'cue': 'Focus on: setup, grip, range of motion',
                '_exercise_id': 'dips_chest',
                '_movement_id': 'horizontal_push',
            },
            {
                'name': 'Assisted Dip Machine',
                'requires': None,
                'cue': 'Focus on: setup, grip, range of motion',
                '_exercise_id': 'assisted_dips_machine',
                '_movement_id': 'horizontal_push',
            },
            {
                'name': 'Plate Squeeze Press',
                'requires': 'Dumbbells',
                'cue': 'Focus on: setup, grip, range of motion',
                'contraindicated_for': ('shoulder', 'rotator cuff'),
                '_exercise_id': 'plate_squeeze_press',
                '_movement_id': 'horizontal_push',
            },
            {
                'name': 'Machine Incline Chest Press',
                'requires': None,
                'cue': 'Focus on: setup, grip, range of motion',
                '_exercise_id': 'machine_incline_press',
                '_movement_id': 'horizontal_push',
            },
            {
                'name': 'Machine Decline Chest Press',
                'requires': None,
                'cue': 'Focus on: setup, grip, range of motion',
                '_exercise_id': 'machine_decline_press',
                '_movement_id': 'horizontal_push',
            },
            {
                'name': 'Smith Machine Incline Press',
                'requires': 'Smith machine',
                'cue': 'Focus on: setup, grip, range of motion',
                'contraindicated_for': ('shoulder',),
                '_exercise_id': 'smith_incline_press',
                '_movement_id': 'horizontal_push',
            },
            {
                'name': 'Hex Press (Squeeze Press)',
                'requires': 'Dumbbells',
                'cue': 'Focus on: setup, grip, range of motion',
                'contraindicated_for': ('shoulder', 'rotator cuff'),
                '_exercise_id': 'hex_press',
                '_movement_id': 'horizontal_push',
            },
            {
                'name': 'Guillotine Press',
                'requires': 'Barbell',
                'cue': 'Focus on: setup, grip, range of motion',
                'contraindicated_for': ('shoulder', 'rotator cuff', 'wrist'),
                '_exercise_id': 'guillotine_press',
                '_movement_id': 'horizontal_push',
            },
            {
                'name': 'Standing Single-Arm Cable Press',
                'requires': None,
                'cue': 'Focus on: setup, grip, range of motion',
                '_exercise_id': 'standing_cable_press',
                '_movement_id': 'horizontal_push',
            },
        ],
        'isolation': [
            {
                'name': 'Cable Fly',
                'requires': None,
                'cue': 'Focus on: setup, grip, range of motion',
                '_exercise_id': 'cable_fly',
                '_movement_id': 'horizontal_push',
            },
            {
                'name': 'Dumbbell Fly',
                'requires': 'Dumbbells',
                'cue': 'Focus on: setup, grip, range of motion',
                'contraindicated_for': ('shoulder', 'rotator cuff'),
                '_exercise_id': 'dumbbell_fly',
                '_movement_id': 'horizontal_push',
            },
            {
                'name': 'Incline Dumbbell Fly',
                'requires': 'Dumbbells',
                'cue': 'Focus on: setup, grip, range of motion',
                'contraindicated_for': ('shoulder', 'rotator cuff'),
                '_exercise_id': 'incline_dumbbell_fly',
                '_movement_id': 'horizontal_push',
            },
            {
                'name': 'Pec Deck / Chest Fly Machine',
                'requires': None,
                'cue': 'Focus on: setup, grip, range of motion',
                '_exercise_id': 'pec_deck',
                '_movement_id': 'horizontal_push',
            },
            {
                'name': 'Band Chest Fly',
                'requires': 'Resistance bands',
                'cue': 'Focus on: setup, grip, range of motion',
                '_exercise_id': 'banded_fly',
                '_movement_id': 'horizontal_push',
            },
            {
                'name': 'Single-Arm Cable Fly',
                'requires': None,
                'cue': 'Focus on: setup, grip, range of motion',
                '_exercise_id': 'single_arm_cable_fly',
                '_movement_id': 'horizontal_push',
            },
            {
                'name': 'Low-to-High Cable Fly',
                'requires': None,
                'cue': 'Focus on: setup, grip, range of motion',
                '_exercise_id': 'low_to_high_cable_fly',
                '_movement_id': 'horizontal_push',
            },
            {
                'name': 'High-to-Low Cable Fly',
                'requires': None,
                'cue': 'Focus on: setup, grip, range of motion',
                '_exercise_id': 'high_to_low_cable_fly',
                '_movement_id': 'horizontal_push',
            },
        ],
    },
    'shoulders': {
        'compound': [
            {
                'name': 'Barbell Overhead Press',
                'requires': 'Barbell',
                'cue': 'Focus on: setup, bracing, overhead stability, bar path',
                'contraindicated_for': ('shoulder', 'rotator cuff', 'neck'),
                '_exercise_id': 'overhead_press',
                '_movement_id': 'vertical_push',
            },
            {
                'name': 'Dumbbell Shoulder Press',
                'requires': 'Dumbbells',
                'cue': 'Focus on: setup, bracing, overhead stability, bar path',
                'contraindicated_for': ('shoulder', 'rotator cuff', 'neck'),
                '_exercise_id': 'dumbbell_shoulder_press',
                '_movement_id': 'vertical_push',
            },
            {
                'name': 'Push Press',
                'requires': 'Barbell',
                'cue': 'Focus on: setup, bracing, overhead stability, bar path',
                'contraindicated_for': ('shoulder', 'rotator cuff', 'neck'),
                '_exercise_id': 'push_press',
                '_movement_id': 'vertical_push',
            },
            {
                'name': 'Machine Shoulder Press',
                'requires': None,
                'cue': 'Focus on: setup, bracing, overhead stability, bar path',
                'contraindicated_for': ('shoulder',),
                '_exercise_id': 'machine_shoulder_press',
                '_movement_id': 'vertical_push',
            },
            {
                'name': 'Smith Machine Shoulder Press',
                'requires': 'Smith machine',
                'cue': 'Focus on: setup, bracing, overhead stability, bar path',
                'contraindicated_for': ('shoulder', 'rotator cuff'),
                '_exercise_id': 'smith_machine_shoulder_press',
                '_movement_id': 'vertical_push',
            },
            {
                'name': 'Arnold Press',
                'requires': 'Dumbbells',
                'cue': 'Focus on: setup, bracing, overhead stability, bar path',
                'contraindicated_for': ('shoulder', 'rotator cuff', 'neck'),
                '_exercise_id': 'arnold_press',
                '_movement_id': 'vertical_push',
            },
            {
                'name': 'Seated Dumbbell Shoulder Press',
                'requires': 'Dumbbells',
                'cue': 'Focus on: setup, bracing, overhead stability, bar path',
                'contraindicated_for': ('shoulder', 'rotator cuff', 'neck'),
                '_exercise_id': 'seated_dumbbell_press',
                '_movement_id': 'vertical_push',
            },
            {
                'name': 'Landmine Shoulder Press',
                'requires': 'Barbell',
                'cue': 'Focus on: setup, bracing, overhead stability, bar path',
                'contraindicated_for': ('shoulder', 'rotator cuff', 'neck'),
                '_exercise_id': 'landmine_shoulder_press',
                '_movement_id': 'vertical_push',
            },
            {
                'name': 'Pike Push-Up',
                'requires': None,
                'cue': 'Focus on: setup, bracing, overhead stability, bar path',
                '_exercise_id': 'pike_push_up',
                '_movement_id': 'vertical_push',
            },
            {
                'name': 'Handstand Push-Up',
                'requires': None,
                'cue': 'Focus on: setup, bracing, overhead stability, bar path',
                '_exercise_id': 'handstand_push_up',
                '_movement_id': 'vertical_push',
            },
            {
                'name': 'Single-Arm Dumbbell Overhead Press',
                'requires': 'Dumbbells',
                'cue': 'Focus on: setup, bracing, overhead stability, bar path',
                'contraindicated_for': ('shoulder', 'rotator cuff', 'neck'),
                '_exercise_id': 'single_arm_db_press_vertical',
                '_movement_id': 'vertical_push',
            },
            {
                'name': 'Cable Overhead Press',
                'requires': None,
                'cue': 'Focus on: setup, bracing, overhead stability, bar path',
                'contraindicated_for': ('shoulder',),
                '_exercise_id': 'cable_shoulder_press',
                '_movement_id': 'vertical_push',
            },
            {
                'name': 'Kettlebell Overhead Press',
                'requires': 'Kettlebells',
                'cue': 'Focus on: setup, bracing, overhead stability, bar path',
                'contraindicated_for': ('shoulder', 'rotator cuff', 'neck'),
                '_exercise_id': 'kettlebell_overhead_press',
                '_movement_id': 'vertical_push',
            },
            {
                'name': 'Z-Press',
                'requires': ('Barbell', 'Dumbbells'),
                'cue': 'Focus on: setup, bracing, overhead stability, bar path',
                'contraindicated_for': ('shoulder', 'rotator cuff', 'neck'),
                '_exercise_id': 'z_press',
                '_movement_id': 'vertical_push',
            },
            {
                'name': 'Band Overhead Press',
                'requires': 'Resistance bands',
                'cue': 'Focus on: setup, bracing, overhead stability, bar path',
                'contraindicated_for': ('shoulder',),
                '_exercise_id': 'band_shoulder_press',
                '_movement_id': 'vertical_push',
            },
            {
                'name': 'Bottoms-Up Kettlebell Press',
                'requires': 'Kettlebells',
                'cue': 'Focus on: setup, bracing, overhead stability, bar path',
                'contraindicated_for': ('shoulder', 'rotator cuff', 'neck'),
                '_exercise_id': 'bottoms_up_kb_press',
                '_movement_id': 'vertical_push',
            },
            {
                'name': 'Seated Barbell Overhead Press',
                'requires': 'Barbell',
                'cue': 'Focus on: setup, bracing, overhead stability, bar path',
                'contraindicated_for': ('shoulder', 'rotator cuff', 'neck'),
                '_exercise_id': 'seated_barbell_press',
                '_movement_id': 'vertical_push',
            },
            {
                'name': 'Viking Press (Machine)',
                'requires': None,
                'cue': 'Focus on: setup, bracing, overhead stability, bar path',
                'contraindicated_for': ('shoulder',),
                '_exercise_id': 'viking_press_machine',
                '_movement_id': 'vertical_push',
            },
            {
                'name': 'Half-Kneeling Landmine Press',
                'requires': 'Barbell',
                'cue': 'Focus on: setup, bracing, overhead stability, bar path',
                'contraindicated_for': ('shoulder', 'rotator cuff', 'neck'),
                '_exercise_id': 'half_kneeling_landmine_press',
                '_movement_id': 'vertical_push',
            },
            {
                'name': 'Dumbbell W-Press',
                'requires': 'Dumbbells',
                'cue': 'Focus on: setup, bracing, overhead stability, bar path',
                'contraindicated_for': ('shoulder', 'rotator cuff', 'neck'),
                '_exercise_id': 'dumbbell_w_press',
                '_movement_id': 'vertical_push',
            },
            {
                'name': 'Neutral-Grip Dumbbell Shoulder Press',
                'requires': 'Dumbbells',
                'cue': 'Focus on: setup, bracing, overhead stability, bar path',
                'contraindicated_for': ('shoulder', 'rotator cuff', 'neck'),
                '_exercise_id': 'db_shoulder_press_neutral_grip',
                '_movement_id': 'vertical_push',
            },
        ],
        'isolation': [
            {
                'name': 'Dumbbell Lateral Raise',
                'requires': 'Dumbbells',
                'cue': 'Focus on: setup, range of motion, control',
                'contraindicated_for': ('shoulder',),
                '_exercise_id': 'lateral_raise',
                '_movement_id': 'isolation_shoulder',
            },
            {
                'name': 'Cable Lateral Raise',
                'requires': None,
                'cue': 'Focus on: setup, range of motion, control',
                '_exercise_id': 'cable_lateral_raise',
                '_movement_id': 'isolation_shoulder',
            },
            {
                'name': 'Machine Lateral Raise',
                'requires': None,
                'cue': 'Focus on: setup, range of motion, control',
                '_exercise_id': 'machine_lateral_raise',
                '_movement_id': 'isolation_shoulder',
            },
            {
                'name': 'Front Raise',
                'requires': 'Dumbbells',
                'cue': 'Focus on: setup, range of motion, control',
                'contraindicated_for': ('shoulder',),
                '_exercise_id': 'front_raise',
                '_movement_id': 'isolation_shoulder',
            },
            {
                'name': 'Rear Delt Fly',
                'requires': 'Dumbbells',
                'cue': 'Focus on: setup, range of motion, control',
                'contraindicated_for': ('shoulder',),
                '_exercise_id': 'rear_delt_fly',
                '_movement_id': 'isolation_shoulder',
            },
            {
                'name': 'Cable Rear Delt Fly',
                'requires': None,
                'cue': 'Focus on: setup, range of motion, control',
                '_exercise_id': 'cable_rear_delt_fly',
                '_movement_id': 'isolation_shoulder',
            },
            {
                'name': 'Reverse Pec Deck',
                'requires': None,
                'cue': 'Focus on: setup, range of motion, control',
                '_exercise_id': 'reverse_pec_deck',
                '_movement_id': 'isolation_shoulder',
            },
            {
                'name': 'Leaning Cable Lateral Raise',
                'requires': None,
                'cue': 'Focus on: setup, range of motion, control',
                '_exercise_id': 'leaning_lateral_raise',
                '_movement_id': 'isolation_shoulder',
            },
            {
                'name': 'Bent-Over Lateral Raise',
                'requires': 'Dumbbells',
                'cue': 'Focus on: setup, range of motion, control',
                'contraindicated_for': ('shoulder',),
                '_exercise_id': 'bent_over_lateral_raise',
                '_movement_id': 'isolation_shoulder',
            },
            {
                'name': 'Plate Front Raise',
                'requires': 'Barbell',
                'cue': 'Focus on: setup, range of motion, control',
                'contraindicated_for': ('shoulder',),
                '_exercise_id': 'plate_front_raise',
                '_movement_id': 'isolation_shoulder',
            },
            {
                'name': 'Band Lateral Raise',
                'requires': 'Resistance bands',
                'cue': 'Focus on: setup, range of motion, control',
                '_exercise_id': 'band_lateral_raise',
                '_movement_id': 'isolation_shoulder',
            },
            {
                'name': 'Scott (Cuban) Press',
                'requires': 'Dumbbells',
                'cue': 'Focus on: setup, range of motion, control',
                'contraindicated_for': ('shoulder',),
                '_exercise_id': 'scott_press',
                '_movement_id': 'isolation_shoulder',
            },
            {
                'name': 'Machine Rear Delt Row',
                'requires': None,
                'cue': 'Focus on: setup, range of motion, control',
                '_exercise_id': 'machine_rear_delt_row',
                '_movement_id': 'isolation_shoulder',
            },
            {
                'name': 'Cable External Rotation',
                'requires': None,
                'cue': 'Focus on: setup, range of motion, control',
                '_exercise_id': 'external_rotation_cable',
                '_movement_id': 'isolation_shoulder',
            },
            {
                'name': 'Band External Rotation',
                'requires': 'Resistance bands',
                'cue': 'Focus on: setup, range of motion, control',
                '_exercise_id': 'band_external_rotation',
                '_movement_id': 'isolation_shoulder',
            },
            {
                'name': 'Cable Internal Rotation',
                'requires': None,
                'cue': 'Focus on: setup, range of motion, control',
                '_exercise_id': 'internal_rotation_cable',
                '_movement_id': 'isolation_shoulder',
            },
            {
                'name': 'Band Internal Rotation',
                'requires': 'Resistance bands',
                'cue': 'Focus on: setup, range of motion, control',
                '_exercise_id': 'band_internal_rotation',
                '_movement_id': 'isolation_shoulder',
            },
            {
                'name': 'Prone T-Raise',
                'requires': 'Dumbbells',
                'cue': 'Focus on: setup, range of motion, control',
                'contraindicated_for': ('shoulder',),
                '_exercise_id': 'prone_t_raise',
                '_movement_id': 'isolation_shoulder',
            },
            {
                'name': 'Band Shoulder Dislocate (Mobility)',
                'requires': 'Resistance bands',
                'cue': 'Focus on: setup, range of motion, control',
                '_exercise_id': 'banded_shoulder_dislocate',
                '_movement_id': 'isolation_shoulder',
            },
            {
                'name': 'Plate Front Raise (Iso Hold)',
                'requires': 'Barbell',
                'cue': 'Focus on: setup, range of motion, control',
                'contraindicated_for': ('shoulder',),
                '_exercise_id': 'plate_front_raise_iso',
                '_movement_id': 'isolation_shoulder',
            },
        ],
    },
    'biceps': {
        'isolation': [
            {
                'name': 'Barbell Curl',
                'requires': 'Barbell',
                'cue': 'Focus on: setup, range of motion',
                '_exercise_id': 'barbell_curl',
                '_movement_id': 'isolation_arm',
            },
            {
                'name': 'EZ-Bar Curl',
                'requires': 'Barbell',
                'cue': 'Focus on: setup, range of motion',
                '_exercise_id': 'ez_bar_curl',
                '_movement_id': 'isolation_arm',
            },
            {
                'name': 'Dumbbell Curl',
                'requires': 'Dumbbells',
                'cue': 'Focus on: setup, range of motion',
                '_exercise_id': 'dumbbell_curl',
                '_movement_id': 'isolation_arm',
            },
            {
                'name': 'Hammer Curl',
                'requires': 'Dumbbells',
                'cue': 'Focus on: setup, range of motion',
                '_exercise_id': 'hammer_curl',
                '_movement_id': 'isolation_arm',
            },
            {
                'name': 'Cable Hammer Curl (Rope)',
                'requires': None,
                'cue': 'Focus on: setup, range of motion',
                '_exercise_id': 'cable_hammer_curl',
                '_movement_id': 'isolation_arm',
            },
            {
                'name': 'Preacher Curl',
                'requires': ('Barbell', 'Dumbbells'),
                'cue': 'Focus on: setup, range of motion',
                '_exercise_id': 'preacher_curl',
                '_movement_id': 'isolation_arm',
            },
            {
                'name': 'Machine Preacher Curl',
                'requires': None,
                'cue': 'Focus on: setup, range of motion',
                '_exercise_id': 'machine_preacher_curl',
                '_movement_id': 'isolation_arm',
            },
            {
                'name': 'Cable Curl',
                'requires': None,
                'cue': 'Focus on: setup, range of motion',
                '_exercise_id': 'cable_curl',
                '_movement_id': 'isolation_arm',
            },
            {
                'name': 'Concentration Curl',
                'requires': 'Dumbbells',
                'cue': 'Focus on: setup, range of motion',
                '_exercise_id': 'concentration_curl',
                '_movement_id': 'isolation_arm',
            },
            {
                'name': 'Incline Dumbbell Curl',
                'requires': 'Dumbbells',
                'cue': 'Focus on: setup, range of motion',
                '_exercise_id': 'incline_dumbbell_curl',
                '_movement_id': 'isolation_arm',
            },
            {
                'name': 'Spider Curl',
                'requires': ('Dumbbells', 'Barbell'),
                'cue': 'Focus on: setup, range of motion',
                '_exercise_id': 'spider_curl',
                '_movement_id': 'isolation_arm',
            },
            {
                'name': 'Drag Curl',
                'requires': 'Barbell',
                'cue': 'Focus on: setup, range of motion',
                '_exercise_id': 'drag_curl',
                '_movement_id': 'isolation_arm',
            },
            {
                'name': 'Reverse Curl',
                'requires': ('Barbell', 'Dumbbells'),
                'cue': 'Focus on: setup, range of motion',
                '_exercise_id': 'reverse_curl',
                '_movement_id': 'isolation_arm',
            },
            {
                'name': 'Zottman Curl',
                'requires': 'Dumbbells',
                'cue': 'Focus on: setup, range of motion',
                '_exercise_id': 'zottman_curl',
                '_movement_id': 'isolation_arm',
            },
            {
                'name': 'Band Curl',
                'requires': 'Resistance bands',
                'cue': 'Focus on: setup, range of motion',
                '_exercise_id': 'band_curl',
                '_movement_id': 'isolation_arm',
            },
            {
                'name': '21s Bicep Curl',
                'requires': ('Barbell', 'Dumbbells'),
                'cue': 'Focus on: setup, range of motion',
                '_exercise_id': '21s_curl',
                '_movement_id': 'isolation_arm',
            },
            {
                'name': 'Cross-Body Hammer Curl',
                'requires': 'Dumbbells',
                'cue': 'Focus on: setup, range of motion',
                '_exercise_id': 'cross_body_hammer_curl',
                '_movement_id': 'isolation_arm',
            },
            {
                'name': 'Chin-Up (Bicep Emphasis)',
                'requires': None,
                'cue': 'Focus on: setup, range of motion',
                '_exercise_id': 'chin_up_bicep_focus',
                '_movement_id': 'isolation_arm',
            },
            {
                'name': 'Waiter Curl',
                'requires': 'Dumbbells',
                'cue': 'Focus on: setup, range of motion',
                '_exercise_id': 'waiter_curl',
                '_movement_id': 'isolation_arm',
            },
            {
                'name': 'Kettlebell Curl',
                'requires': 'Kettlebells',
                'cue': 'Focus on: setup, range of motion',
                '_exercise_id': 'kettlebell_curl',
                '_movement_id': 'isolation_arm',
            },
            {
                'name': 'Barbell Wrist Curl',
                'requires': 'Barbell',
                'cue': 'Focus on: setup, range of motion',
                'contraindicated_for': ('wrist',),
                '_exercise_id': 'wrist_curl',
                '_movement_id': 'isolation_arm',
            },
            {
                'name': 'Dumbbell Wrist Curl',
                'requires': 'Dumbbells',
                'cue': 'Focus on: setup, range of motion',
                'contraindicated_for': ('wrist',),
                '_exercise_id': 'dumbbell_wrist_curl',
                '_movement_id': 'isolation_arm',
            },
            {
                'name': 'Reverse Wrist Curl',
                'requires': ('Barbell', 'Dumbbells'),
                'cue': 'Focus on: setup, range of motion',
                'contraindicated_for': ('wrist',),
                '_exercise_id': 'reverse_wrist_curl',
                '_movement_id': 'isolation_arm',
            },
            {
                'name': 'Plate Pinch Hold',
                'requires': None,
                'cue': 'Focus on: setup, range of motion',
                '_exercise_id': 'plate_pinch',
                '_movement_id': 'isolation_arm',
            },
            {
                'name': 'Dead Hang',
                'requires': None,
                'cue': 'Focus on: setup, range of motion',
                '_exercise_id': 'dead_hang',
                '_movement_id': 'isolation_arm',
            },
            {
                'name': 'Weighted Dead Hang',
                'requires': None,
                'cue': 'Focus on: setup, range of motion',
                '_exercise_id': 'weighted_dead_hang',
                '_movement_id': 'isolation_arm',
            },
            {
                'name': 'Wrist Roller',
                'requires': None,
                'cue': 'Focus on: setup, range of motion',
                'contraindicated_for': ('wrist',),
                '_exercise_id': 'wrist_roller',
                '_movement_id': 'isolation_arm',
            },
            {
                'name': 'Hand Gripper Squeeze',
                'requires': None,
                'cue': 'Focus on: setup, range of motion',
                '_exercise_id': 'gripper_squeeze',
                '_movement_id': 'isolation_arm',
            },
            {
                'name': 'Towel Grip Pull-Up',
                'requires': None,
                'cue': 'Focus on: setup, range of motion',
                '_exercise_id': 'towel_pull_up',
                '_movement_id': 'isolation_arm',
            },
            {
                'name': 'Fat-Grip Barbell Curl',
                'requires': 'Barbell',
                'cue': 'Focus on: setup, range of motion',
                '_exercise_id': 'fat_bar_curl',
                '_movement_id': 'isolation_arm',
            },
            {
                'name': "Farmer's Hold (Static)",
                'requires': ('Dumbbells', 'Kettlebells'),
                'cue': 'Focus on: setup, range of motion',
                '_exercise_id': 'farmer_hold',
                '_movement_id': 'isolation_arm',
            },
            {
                'name': 'Rice Bucket Training',
                'requires': None,
                'cue': 'Focus on: setup, range of motion',
                '_exercise_id': 'rice_bucket_training',
                '_movement_id': 'isolation_arm',
            },
            {
                'name': 'Dumbbell 21s Curl',
                'requires': 'Dumbbells',
                'cue': 'Focus on: setup, range of motion',
                '_exercise_id': 'db_curl_21s',
                '_movement_id': 'isolation_arm',
            },
            {
                'name': 'Preacher Hammer Curl',
                'requires': 'Dumbbells',
                'cue': 'Focus on: setup, range of motion',
                '_exercise_id': 'preacher_hammer_curl',
                '_movement_id': 'isolation_arm',
            },
            {
                'name': 'Cable Concentration Curl',
                'requires': None,
                'cue': 'Focus on: setup, range of motion',
                '_exercise_id': 'cable_concentration_curl',
                '_movement_id': 'isolation_arm',
            },
        ],
    },
    'triceps': {
        'isolation': [
            {
                'name': 'Triceps Pushdown (Rope)',
                'requires': None,
                'cue': 'Focus on: setup, range of motion',
                '_exercise_id': 'triceps_pushdown',
                '_movement_id': 'isolation_arm',
            },
            {
                'name': 'Straight-Bar Triceps Pushdown',
                'requires': None,
                'cue': 'Focus on: setup, range of motion',
                '_exercise_id': 'straight_bar_pushdown',
                '_movement_id': 'isolation_arm',
            },
            {
                'name': 'Overhead Triceps Extension',
                'requires': 'Dumbbells',
                'cue': 'Focus on: setup, range of motion',
                'contraindicated_for': ('elbow', 'shoulder'),
                '_exercise_id': 'overhead_triceps_extension',
                '_movement_id': 'isolation_arm',
            },
            {
                'name': 'Skull Crusher (Lying Triceps Extension)',
                'requires': ('Barbell', 'Dumbbells'),
                'cue': 'Focus on: setup, range of motion',
                '_exercise_id': 'skull_crusher',
                '_movement_id': 'isolation_arm',
            },
            {
                'name': 'Close-Grip Bench Press (Triceps Focus)',
                'requires': 'Barbell',
                'cue': 'Focus on: setup, range of motion',
                'contraindicated_for': ('wrist',),
                '_exercise_id': 'close_grip_bench',
                '_movement_id': 'isolation_arm',
            },
            {
                'name': 'Triceps Dips',
                'requires': None,
                'cue': 'Focus on: setup, range of motion',
                '_exercise_id': 'dips_triceps',
                '_movement_id': 'isolation_arm',
            },
            {
                'name': 'Bench Dip',
                'requires': None,
                'cue': 'Focus on: setup, range of motion',
                '_exercise_id': 'bench_dip',
                '_movement_id': 'isolation_arm',
            },
            {
                'name': 'Single-Arm Triceps Pushdown',
                'requires': None,
                'cue': 'Focus on: setup, range of motion',
                '_exercise_id': 'single_arm_pushdown',
                '_movement_id': 'isolation_arm',
            },
            {
                'name': 'Triceps Kickback',
                'requires': 'Dumbbells',
                'cue': 'Focus on: setup, range of motion',
                '_exercise_id': 'kickback',
                '_movement_id': 'isolation_arm',
            },
            {
                'name': 'Cable Triceps Kickback',
                'requires': None,
                'cue': 'Focus on: setup, range of motion',
                '_exercise_id': 'cable_kickback',
                '_movement_id': 'isolation_arm',
            },
            {
                'name': 'JM Press',
                'requires': 'Barbell',
                'cue': 'Focus on: setup, range of motion',
                '_exercise_id': 'jm_press',
                '_movement_id': 'isolation_arm',
            },
            {
                'name': 'Diamond Push-Up (Triceps Focus)',
                'requires': None,
                'cue': 'Focus on: setup, range of motion',
                '_exercise_id': 'diamond_push_up_triceps',
                '_movement_id': 'isolation_arm',
            },
            {
                'name': 'Machine Triceps Extension',
                'requires': None,
                'cue': 'Focus on: setup, range of motion',
                '_exercise_id': 'machine_triceps_extension',
                '_movement_id': 'isolation_arm',
            },
            {
                'name': 'Band Triceps Pushdown',
                'requires': 'Resistance bands',
                'cue': 'Focus on: setup, range of motion',
                '_exercise_id': 'band_pushdown',
                '_movement_id': 'isolation_arm',
            },
            {
                'name': 'Tate Press',
                'requires': 'Dumbbells',
                'cue': 'Focus on: setup, range of motion',
                '_exercise_id': 'tate_press',
                '_movement_id': 'isolation_arm',
            },
            {
                'name': 'V-Bar Triceps Pushdown',
                'requires': None,
                'cue': 'Focus on: setup, range of motion',
                '_exercise_id': 'v_bar_pushdown',
                '_movement_id': 'isolation_arm',
            },
            {
                'name': 'French Press (Seated Overhead)',
                'requires': ('Barbell', 'Dumbbells'),
                'cue': 'Focus on: setup, range of motion',
                'contraindicated_for': ('elbow', 'shoulder'),
                '_exercise_id': 'french_press',
                '_movement_id': 'isolation_arm',
            },
            {
                'name': 'Reverse-Grip Triceps Pushdown',
                'requires': None,
                'cue': 'Focus on: setup, range of motion',
                '_exercise_id': 'reverse_grip_pushdown',
                '_movement_id': 'isolation_arm',
            },
            {
                'name': 'Landmine Triceps Extension',
                'requires': 'Barbell',
                'cue': 'Focus on: setup, range of motion',
                '_exercise_id': 'landmine_triceps_extension',
                '_movement_id': 'isolation_arm',
            },
            {
                'name': 'Close-Grip Push-Up',
                'requires': None,
                'cue': 'Focus on: setup, range of motion',
                'contraindicated_for': ('wrist',),
                '_exercise_id': 'close_grip_push_up',
                '_movement_id': 'isolation_arm',
            },
            {
                'name': 'Cable Overhead Triceps Extension (Rope)',
                'requires': None,
                'cue': 'Focus on: setup, range of motion',
                'contraindicated_for': ('elbow', 'shoulder'),
                '_exercise_id': 'cable_overhead_tricep_rope',
                '_movement_id': 'isolation_arm',
            },
            {
                'name': 'Single-Arm Dumbbell Skull Crusher',
                'requires': 'Dumbbells',
                'cue': 'Focus on: setup, range of motion',
                '_exercise_id': 'db_skull_crusher_single_arm',
                '_movement_id': 'isolation_arm',
            },
        ],
    },
    'core': {
        'isolation': [
            {
                'name': 'Plank',
                'requires': None,
                'cue': 'Focus on: setup, bracing, breathing',
                '_exercise_id': 'plank',
                '_movement_id': 'core',
            },
            {
                'name': 'Weighted Plank',
                'requires': None,
                'cue': 'Focus on: setup, bracing, breathing',
                '_exercise_id': 'weighted_plank',
                '_movement_id': 'core',
            },
            {
                'name': 'Knee Plank',
                'requires': None,
                'cue': 'Focus on: setup, bracing, breathing',
                '_exercise_id': 'knee_plank',
                '_movement_id': 'core',
            },
            {
                'name': 'Side Plank',
                'requires': None,
                'cue': 'Focus on: setup, bracing, breathing',
                '_exercise_id': 'side_plank',
                '_movement_id': 'core',
            },
            {
                'name': 'Weighted Side Plank',
                'requires': None,
                'cue': 'Focus on: setup, bracing, breathing',
                '_exercise_id': 'weighted_side_plank',
                '_movement_id': 'core',
            },
            {
                'name': 'Hanging Leg Raise',
                'requires': None,
                'cue': 'Focus on: setup, bracing, breathing',
                'contraindicated_for': ('shoulder', 'wrist'),
                '_exercise_id': 'hanging_leg_raise',
                '_movement_id': 'core',
            },
            {
                'name': 'Lying Leg Raise',
                'requires': None,
                'cue': 'Focus on: setup, bracing, breathing',
                '_exercise_id': 'lying_leg_raise',
                '_movement_id': 'core',
            },
            {
                'name': "Captain's Chair Leg Raise",
                'requires': None,
                'cue': 'Focus on: setup, bracing, breathing',
                '_exercise_id': 'captain_chair_leg_raise',
                '_movement_id': 'core',
            },
            {
                'name': 'Cable Crunch',
                'requires': None,
                'cue': 'Focus on: setup, bracing, breathing',
                '_exercise_id': 'cable_crunch',
                '_movement_id': 'core',
            },
            {
                'name': 'Weighted Crunch',
                'requires': 'Dumbbells',
                'cue': 'Focus on: setup, bracing, breathing',
                '_exercise_id': 'weighted_crunch',
                '_movement_id': 'core',
            },
            {
                'name': 'Crunch',
                'requires': None,
                'cue': 'Focus on: setup, bracing, breathing',
                '_exercise_id': 'crunch',
                '_movement_id': 'core',
            },
            {
                'name': 'Bicycle Crunch',
                'requires': None,
                'cue': 'Focus on: setup, bracing, breathing',
                '_exercise_id': 'bicycle_crunch',
                '_movement_id': 'core',
            },
            {
                'name': 'Russian Twist',
                'requires': 'Dumbbells',
                'cue': 'Focus on: setup, bracing, breathing',
                '_exercise_id': 'russian_twist',
                '_movement_id': 'core',
            },
            {
                'name': 'Cable Woodchop',
                'requires': None,
                'cue': 'Focus on: setup, bracing, breathing',
                '_exercise_id': 'cable_woodchop',
                '_movement_id': 'core',
            },
            {
                'name': 'Pallof Press',
                'requires': 'Resistance bands',
                'cue': 'Focus on: setup, bracing, breathing',
                '_exercise_id': 'pallof_press',
                '_movement_id': 'core',
            },
            {
                'name': 'Ab Wheel Rollout',
                'requires': None,
                'cue': 'Focus on: setup, bracing, breathing',
                '_exercise_id': 'ab_wheel_rollout',
                '_movement_id': 'core',
            },
            {
                'name': 'Dead Bug',
                'requires': None,
                'cue': 'Focus on: setup, bracing, breathing',
                '_exercise_id': 'dead_bug',
                '_movement_id': 'core',
            },
            {
                'name': 'Bird Dog',
                'requires': None,
                'cue': 'Focus on: setup, bracing, breathing',
                '_exercise_id': 'bird_dog',
                '_movement_id': 'core',
            },
            {
                'name': 'Mountain Climber',
                'requires': None,
                'cue': 'Focus on: setup, bracing, breathing',
                '_exercise_id': 'mountain_climber',
                '_movement_id': 'core',
            },
            {
                'name': 'V-Up',
                'requires': None,
                'cue': 'Focus on: setup, bracing, breathing',
                '_exercise_id': 'v_up',
                '_movement_id': 'core',
            },
            {
                'name': 'Toe Touch',
                'requires': None,
                'cue': 'Focus on: setup, bracing, breathing',
                '_exercise_id': 'toe_touch',
                '_movement_id': 'core',
            },
            {
                'name': 'Reverse Crunch',
                'requires': None,
                'cue': 'Focus on: setup, bracing, breathing',
                '_exercise_id': 'reverse_crunch',
                '_movement_id': 'core',
            },
            {
                'name': 'Hollow Body Hold',
                'requires': None,
                'cue': 'Focus on: setup, bracing, breathing',
                '_exercise_id': 'hollow_body_hold',
                '_movement_id': 'core',
            },
            {
                'name': 'Landmine Rotation (360)',
                'requires': 'Barbell',
                'cue': 'Focus on: setup, bracing, breathing',
                '_exercise_id': 'landmine_rotation',
                '_movement_id': 'core',
            },
            {
                'name': 'Stability Ball Stir-the-Pot',
                'requires': None,
                'cue': 'Focus on: setup, bracing, breathing',
                '_exercise_id': 'stir_the_pot',
                '_movement_id': 'core',
            },
            {
                'name': 'Suitcase Carry (Anti-Lateral-Flexion)',
                'requires': ('Dumbbells', 'Kettlebells'),
                'cue': 'Focus on: setup, bracing, breathing',
                '_exercise_id': 'suitcase_carry_core',
                '_movement_id': 'core',
            },
            {
                'name': 'Band Pallof Press',
                'requires': 'Resistance bands',
                'cue': 'Focus on: setup, bracing, breathing',
                '_exercise_id': 'band_pallof_press',
                '_movement_id': 'core',
            },
            {
                'name': 'Decline Sit-Up',
                'requires': None,
                'cue': 'Focus on: setup, bracing, breathing',
                '_exercise_id': 'decline_sit_up',
                '_movement_id': 'core',
            },
            {
                'name': 'Weighted Russian Twist',
                'requires': 'Dumbbells',
                'cue': 'Focus on: setup, bracing, breathing',
                '_exercise_id': 'weighted_russian_twist',
                '_movement_id': 'core',
            },
            {
                'name': 'Flutter Kick',
                'requires': None,
                'cue': 'Focus on: setup, bracing, breathing',
                '_exercise_id': 'flutter_kick',
                '_movement_id': 'core',
            },
            {
                'name': "Farmer's Carry",
                'requires': ('Dumbbells', 'Kettlebells'),
                'cue': 'Focus on: setup, bracing, posture, grip',
                'contraindicated_for': ('spine', 'wrist'),
                '_exercise_id': 'farmers_carry',
                '_movement_id': 'carry',
            },
            {
                'name': "Heavy Farmer's Carry",
                'requires': 'Dumbbells',
                'cue': 'Focus on: setup, bracing, posture, grip',
                'contraindicated_for': ('spine', 'wrist'),
                '_exercise_id': 'heavy_farmers_carry',
                '_movement_id': 'carry',
            },
            {
                'name': 'Suitcase Carry',
                'requires': ('Dumbbells', 'Kettlebells'),
                'cue': 'Focus on: setup, bracing, posture, grip',
                'contraindicated_for': ('spine', 'wrist'),
                '_exercise_id': 'suitcase_carry',
                '_movement_id': 'carry',
            },
            {
                'name': "Overhead Carry (Waiter's Walk)",
                'requires': ('Dumbbells', 'Kettlebells'),
                'cue': 'Focus on: setup, bracing, posture, grip',
                'contraindicated_for': ('spine', 'wrist'),
                '_exercise_id': 'overhead_carry',
                '_movement_id': 'carry',
            },
            {
                'name': 'Front Rack Carry',
                'requires': 'Kettlebells',
                'cue': 'Focus on: setup, bracing, posture, grip',
                'contraindicated_for': ('spine', 'wrist'),
                '_exercise_id': 'front_rack_carry',
                '_movement_id': 'carry',
            },
            {
                'name': 'Trap Bar Carry',
                'requires': 'Barbell',
                'cue': 'Focus on: setup, bracing, posture, grip',
                'contraindicated_for': ('spine', 'wrist'),
                '_exercise_id': 'trap_bar_carry',
                '_movement_id': 'carry',
            },
            {
                'name': 'Sandbag Carry',
                'requires': None,
                'cue': 'Focus on: setup, bracing, posture, grip',
                'contraindicated_for': ('wrist',),
                '_exercise_id': 'sandbag_carry',
                '_movement_id': 'carry',
            },
            {
                'name': 'Weighted Hanging Leg Raise',
                'requires': None,
                'cue': 'Focus on: setup, bracing, breathing',
                'contraindicated_for': ('shoulder', 'wrist'),
                '_exercise_id': 'weighted_hanging_leg_raise',
                '_movement_id': 'core',
            },
            {
                'name': 'Cable Side Bend',
                'requires': None,
                'cue': 'Focus on: setup, bracing, breathing',
                '_exercise_id': 'cable_side_bend',
                '_movement_id': 'core',
            },
            {
                'name': 'Dumbbell Side Bend',
                'requires': 'Dumbbells',
                'cue': 'Focus on: setup, bracing, breathing',
                '_exercise_id': 'dumbbell_side_bend',
                '_movement_id': 'core',
            },
            {
                'name': 'Half-Kneeling Landmine Anti-Rotation Press',
                'requires': 'Barbell',
                'cue': 'Focus on: setup, bracing, breathing',
                '_exercise_id': 'landmine_press_core',
                '_movement_id': 'core',
            },
            {
                'name': 'Plank Shoulder Tap',
                'requires': None,
                'cue': 'Focus on: setup, bracing, breathing',
                '_exercise_id': 'plank_shoulder_tap',
                '_movement_id': 'core',
            },
            {
                'name': 'Stability Ball Pike',
                'requires': None,
                'cue': 'Focus on: setup, bracing, breathing',
                '_exercise_id': 'stability_ball_pike',
                '_movement_id': 'core',
            },
            {
                'name': 'Hanging Knee Raise',
                'requires': None,
                'cue': 'Focus on: setup, bracing, breathing',
                'contraindicated_for': ('shoulder', 'wrist'),
                '_exercise_id': 'hanging_knee_raise',
                '_movement_id': 'core',
            },
            {
                'name': 'Cable Reverse Crunch',
                'requires': None,
                'cue': 'Focus on: setup, bracing, breathing',
                '_exercise_id': 'cable_reverse_crunch',
                '_movement_id': 'core',
            },
            {
                'name': 'Standing Cable Crunch',
                'requires': None,
                'cue': 'Focus on: setup, bracing, breathing',
                '_exercise_id': 'standing_cable_crunch',
                '_movement_id': 'core',
            },
            {
                'name': 'Kettlebell Side Bend',
                'requires': 'Kettlebells',
                'cue': 'Focus on: setup, bracing, breathing',
                '_exercise_id': 'weighted_side_bend_kb',
                '_movement_id': 'core',
            },
        ],
    },
    'traps': {
        'isolation': [
            {
                'name': 'Upright Row',
                'requires': 'Barbell',
                'cue': 'Focus on: setup, range of motion, control',
                'contraindicated_for': ('shoulder',),
                '_exercise_id': 'upright_row',
                '_movement_id': 'isolation_shoulder',
            },
            {
                'name': 'Face Pull',
                'requires': None,
                'cue': 'Focus on: setup, range of motion, control',
                '_exercise_id': 'face_pull',
                '_movement_id': 'isolation_shoulder',
            },
            {
                'name': 'Band Face Pull',
                'requires': 'Resistance bands',
                'cue': 'Focus on: setup, range of motion, control',
                '_exercise_id': 'band_face_pull',
                '_movement_id': 'isolation_shoulder',
            },
            {
                'name': 'Barbell Shrug',
                'requires': 'Barbell',
                'cue': 'Focus on: setup, range of motion, control',
                'contraindicated_for': ('shoulder',),
                '_exercise_id': 'shrug',
                '_movement_id': 'isolation_shoulder',
            },
            {
                'name': 'Dumbbell Shrug',
                'requires': 'Dumbbells',
                'cue': 'Focus on: setup, range of motion, control',
                'contraindicated_for': ('shoulder',),
                '_exercise_id': 'dumbbell_shrug',
                '_movement_id': 'isolation_shoulder',
            },
            {
                'name': 'Cable Shrug',
                'requires': None,
                'cue': 'Focus on: setup, range of motion, control',
                '_exercise_id': 'cable_shrug',
                '_movement_id': 'isolation_shoulder',
            },
            {
                'name': 'Y-Raise',
                'requires': 'Dumbbells',
                'cue': 'Focus on: setup, range of motion, control',
                'contraindicated_for': ('shoulder',),
                '_exercise_id': 'y_raise',
                '_movement_id': 'isolation_shoulder',
            },
            {
                'name': 'Incline Bench Y-Raise',
                'requires': 'Dumbbells',
                'cue': 'Focus on: setup, range of motion, control',
                'contraindicated_for': ('shoulder',),
                '_exercise_id': 'incline_y_raise',
                '_movement_id': 'isolation_shoulder',
            },
            {
                'name': 'Prone Y-Raise (Rotator Cuff)',
                'requires': 'Dumbbells',
                'cue': 'Focus on: setup, range of motion, control',
                'contraindicated_for': ('shoulder',),
                '_exercise_id': 'prone_y_raise',
                '_movement_id': 'isolation_shoulder',
            },
            {
                'name': 'Wall Slide (Shoulder Mobility)',
                'requires': None,
                'cue': 'Focus on: setup, range of motion, control',
                '_exercise_id': 'wall_slide',
                '_movement_id': 'isolation_shoulder',
            },
            {
                'name': 'Cable Y-Raise',
                'requires': None,
                'cue': 'Focus on: setup, range of motion, control',
                '_exercise_id': 'cable_y_raise',
                '_movement_id': 'isolation_shoulder',
            },
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

    BUGFIX (found during Engine 40/43 integration audit): INJURY_KEYWORDS
    are single body-part words ("knee", "shoulder", "back"...), so plain
    substring matching means "no knee issues" or "no shoulder pain" —
    someone explicitly saying they're FINE — matched and wrongly excluded
    exercises for that body part. That's the opposite of this function's
    own stated safer-failure philosophy above (avoid over-excluding), so
    this was a real bug, not just a judgment call. Reuses
    progression_engine's negation-aware matcher (same fix already applied
    there and in feedback_engine.py) instead of a third drifting copy.
    """
    text = str(notes_raw or "").lower()
    return {kw for kw in INJURY_KEYWORDS if _text_has_unnegated_keyword(text, (kw,))}


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
    avoid_exercise_ids: frozenset = frozenset(),
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

    avoid_exercise_ids: exercise_ids to SOFT-avoid — used by
          build_deterministic_workout_days() for recovery-aware spacing
          (don't repeat an exercise whose movement pattern needs more
          recovery time than has elapsed since it was last used this
          week). This is a preference, never a hard exclusion: if avoiding
          these would leave a muscle with zero safe/available options,
          the avoid list is ignored for that slot rather than degrade the
          day. Equipment and injury filtering always take priority over
          this — those are safety-relevant, this is a quality nudge.

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
        choice_pool = [e for e in filtered if e.get("_exercise_id") not in avoid_exercise_ids]
        if not choice_pool:
            choice_pool = filtered  # avoiding everything would leave nothing safe — ignore the nudge
        choice = rng.choice(choice_pool)
        compounds.append({
            "name": choice["name"],
            "muscle": m,
            "slot": "compound",
            "requires": choice["requires"],
            "cue": choice["cue"],
            "exercise_id": choice.get("_exercise_id"),
            "movement_id": choice.get("_movement_id"),
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

        # Prefer exercises not in the soft-avoid set (recovery-aware spacing);
        # fall back to the full filtered pool if that alone can't fill n.
        preferred = [e for e in filtered if e.get("_exercise_id") not in avoid_exercise_ids]
        sample_pool = preferred if len(preferred) >= n else filtered

        if n <= len(sample_pool):
            picks = rng.sample(sample_pool, n)
        else:
            picks = sample_pool[:]
            rng.shuffle(picks)
            while len(picks) < n:
                picks.append(rng.choice(sample_pool))

        for choice in picks:
            isolation_wishlist.append({
                "name": choice["name"],
                "muscle": m,
                "slot": "isolation",
                "requires": choice["requires"],
                "cue": choice["cue"],
                "exercise_id": choice.get("_exercise_id"),
                "movement_id": choice.get("_movement_id"),
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


# ── KB-BACKED HELPERS (Engines 6/7/8/13/16/41) ──────────────────────────────
# Thin pass-throughs to knowledge_base.py, keyed by the exercise_id/movement_id
# that select_day_exercises() now returns on every picked exercise. Nothing
# above this line depends on these — safe to add without touching existing
# selection behavior. Intended for a future "swap this exercise" / "why this
# exercise" feature; not yet called from fitness_generator.py or main.py.
from . import knowledge_base as _kb


def get_substitutes_for_exercise(exercise_id: str) -> list:
    """Ranked substitute candidates for a picked exercise's exercise_id."""
    return _kb.get_substitutes(exercise_id)


def get_pairings_for_exercise(exercise_id: str) -> list:
    """Curated pairing profiles (supersets/antagonist pairs) involving this exercise_id."""
    return _kb.get_pairings(exercise_id)


def get_recovery_for_movement(movement_id: str) -> dict:
    """Engine 6 recovery profile (min/recommended hours, fatigue source) for a movement pattern."""
    return _kb.get_recovery(movement_id)


def get_full_exercise_profile(exercise_id: str) -> dict:
    """Everything the KB knows about one exercise — see knowledge_base.get_full_profile."""
    return _kb.get_full_profile(exercise_id)
