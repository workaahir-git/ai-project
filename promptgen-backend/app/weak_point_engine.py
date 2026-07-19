"""
weak_point_engine.py — Engine 12 (Weak Point Detection), scoped to what real
data actually supports.

Full spec (KB engines["12"].spec_text) covers 5 categories: muscle, movement,
mobility, stability, skill — requiring ROM tracking, technique-breakdown
flags, and asymmetry data. NONE of that is captured anywhere in this app
(the intake/feedback forms only ever collect difficulty rating + free-text
notes + weight/reps). Building detection for signal that doesn't exist would
mean inventing conclusions, not detecting them — so this module implements
ONLY:

  WP004 (muscle plateau) — a muscle group rated consistently harder
        (difficulty) than the member's other muscle groups. This is the one
        category real data actually supports: workout_exercise_feedback's
        difficulty rating, aggregated per muscle bucket.

  WP005 (pain present -> defer to Injury Engine) — respected by EXCLUDING
        any pain-flagged row from the muscle-plateau aggregation (spec rule
        #2: "Injury SHALL take precedence over weak point correction").
        Pain itself is progression_engine.py's job (flag_pain), not
        reported here — this module must not double-diagnose a pain signal
        as a "weak point."

  WP001 (mobility/ROM), WP002 (skill/technique), WP003 (asymmetry) — NOT
        implemented. No ROM, technique, or per-side data exists anywhere in
        this app to detect these from. Do not guess at them.

Never raises. Returns [] on any missing/insufficient data rather than a
false signal — a plateau flag with weak evidence is worse than no flag.

CYCLE SCOPING: now that workout_exercise_feedback carries a cycle_number
column (see sql/add_cycle_tracking.sql) and is no longer pure upsert-only,
rows PERSIST across cycles instead of being overwritten. Without scoping,
this module's aggregation would silently start averaging difficulty across
a member's entire multi-cycle history instead of reflecting current state
— a real behavior change hiding inside what's meant to be a schema fix.
detect_weak_points() takes an optional cycle_number (the cycle currently
being generated) and, when given, scopes the read to the single previous
cycle (cycle_number - 1) — same window this module always effectively had
before (the upsert only ever left one row per exercise on the table).
cycle_number=None preserves the old fully-unscoped read for backward
compatibility. Aggregating a real multi-cycle trend here instead of a
single-cycle snapshot is a legitimate future enhancement (arguably closer
to true Engine 11 Plateau territory) but is a deliberate scope decision,
not something to change silently as a side effect of this fix.
"""

from __future__ import annotations

from app.db import supabase
from app.exercise_database import EXERCISE_DB
from app.load_adjustment_engine import _contains_pain_language

# Same threshold philosophy as progression_engine's decision table: 4-5 is
# "felt hard." A muscle needs BOTH a high absolute average AND a meaningful
# gap over the member's other muscles to count — a lagging muscle is one
# that's harder than everything else, not just generally hard training.
PLATEAU_MIN_AVG_DIFFICULTY = 4.0
PLATEAU_MIN_GAP = 1.0


def _exercise_to_muscle_map() -> dict:
    mapping = {}
    for muscle, slots in EXERCISE_DB.items():
        for slot_items in slots.values():
            for item in slot_items:
                mapping[item["name"]] = muscle
    return mapping


def _fetch_all_difficulty(member_id: str, read_cycle: int | None) -> list:
    try:
        q = (
            supabase.table("workout_exercise_feedback")
            .select("exercise, difficulty, notes")
            .eq("member_id", member_id)
        )
        if read_cycle is not None:
            q = q.eq("cycle_number", read_cycle)
        res = q.execute()
        return res.data or []
    except Exception:
        return []


def _suggest_accessory_work(muscle: str, exclude_names: set, limit: int = 2) -> list:
    pool = EXERCISE_DB.get(muscle, {}).get("isolation", [])
    candidates = [e["name"] for e in pool if e["name"] not in exclude_names]
    return candidates[:limit] if candidates else [e["name"] for e in pool[:limit]]


def detect_weak_points(member_id: str | None, cycle_number: int | None = None) -> list:
    """
    Returns a list of weak-point dicts (WP004 only, see module docstring).
    [] if there's no member_id, no feedback data, fewer than 2 muscle
    groups with data (can't compute a relative gap with only one), or
    nothing actually clears the plateau threshold.

    cycle_number is the cycle number of the plan currently being generated.
    When given, reads only the previous cycle's feedback (cycle_number - 1)
    — see module docstring's CYCLE SCOPING note. cycle_number=1 or less
    means no previous cycle exists yet; returns [] immediately, no
    Supabase call. cycle_number=None preserves the old fully-unscoped read.
    """
    if not member_id:
        return []

    read_cycle = None
    if cycle_number is not None:
        read_cycle = cycle_number - 1
        if read_cycle < 1:
            return []

    rows = _fetch_all_difficulty(member_id, read_cycle)
    if not rows:
        return []

    ex_to_muscle = _exercise_to_muscle_map()
    muscle_difficulties: dict = {}
    muscle_exercise_names: dict = {}

    for row in rows:
        difficulty = row.get("difficulty")
        exercise_name = row.get("exercise")
        if difficulty is None or exercise_name is None:
            continue
        if _contains_pain_language(row.get("notes")):
            # WP005 — defer to the Injury Engine (progression_engine's
            # flag_pain path already surfaces this elsewhere). Excluded
            # here so pain doesn't get miscounted as a training plateau.
            continue
        muscle = ex_to_muscle.get(exercise_name)
        if not muscle:
            continue
        muscle_difficulties.setdefault(muscle, []).append(difficulty)
        muscle_exercise_names.setdefault(muscle, set()).add(exercise_name)

    if len(muscle_difficulties) < 2:
        return []

    averages = {m: sum(v) / len(v) for m, v in muscle_difficulties.items()}
    weak_points = []

    for muscle, avg in averages.items():
        others = [a for m2, a in averages.items() if m2 != muscle]
        others_avg = sum(others) / len(others)
        gap = avg - others_avg
        if avg >= PLATEAU_MIN_AVG_DIFFICULTY and gap >= PLATEAU_MIN_GAP:
            severity = "high" if gap >= 2.0 else "moderate"
            accessory = _suggest_accessory_work(muscle, muscle_exercise_names.get(muscle, set()))
            weak_points.append({
                "weak_point_type": "muscle",
                "severity": severity,
                "affected_region": muscle,
                "evidence": {
                    "failed_range": False,
                    "technical_breakdown": False,
                    "asymmetry": False,
                    "plateau": True,
                },
                "corrective_actions": {
                    "accessory_work": accessory,
                    "mobility": [],
                    "cueing": [],
                    "progression_change": (
                        "reduce_load_focus_on_form" if severity == "high" else "maintain_load"
                    ),
                },
                "reassessment_sessions": 4,
                "note": (
                    f"{muscle.title()} has been rating harder than your other muscle groups "
                    f"lately ({avg:.1f}/5 vs {others_avg:.1f}/5 elsewhere) — likely lagging, "
                    f"worth extra accessory focus for a few weeks."
                ),
            })

    return weak_points
