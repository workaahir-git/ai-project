"""
conflict_engine.py — Engine 14 (Conflict Detection), scoped to what real data
supports today.

Implements, per KB spec_text for Engine 14:
  CF003 (joint_stress conflict) — two consecutive exercises both hitting a
         high joint-stress rating (>=7) on the SAME joint. Detected AND
         resolved (reordered) where possible; falls back to a note if no
         non-conflicting reorder exists.
  CF001 (movement conflict)     — same movement_id appearing 3+ times in one
         day. Flagged only (informational) — replacing exercises to fix this
         is out of scope for a reorder-only pass.
  CF004 (equipment), CF005 (medical) — already fully handled upstream by
         exercise_database.py's equipment/injury filtering before any
         exercise reaches this module. Not re-implemented here.
  CF002 (fatigue exceeds MRV)   — needs the Recovery Capacity Engine (10),
         not built. Explicitly NOT implemented — do not guess at it.

Also implements the Engine 13 (Pairing) ordering nudge: cluster known
pairing partners adjacent to each other, as long as doing so doesn't
reintroduce a joint-stress conflict. Conflict safety always wins — pairing
is cosmetic ordering, joint-stress separation is not.

Never raises. Never drops an exercise. Only ever reorders the list it's
given and/or attaches note strings.
"""

from __future__ import annotations

from app import knowledge_base as kb

HIGH_STRESS_THRESHOLD = 7


def _joint_stress_info(exercise: dict) -> dict | None:
    mv = exercise.get("movement_id")
    if not mv:
        return None
    return kb.get_joint_stress(mv)


def _is_high_stress(exercise: dict) -> bool:
    info = _joint_stress_info(exercise)
    return bool(info and info.get("stress_rating", 0) >= HIGH_STRESS_THRESHOLD)


def _shares_joint(ex_a: dict, ex_b: dict) -> bool:
    a, b = _joint_stress_info(ex_a), _joint_stress_info(ex_b)
    if not a or not b:
        return False
    joints_a = {a.get("primary_joint"), a.get("secondary_joint")}
    joints_b = {b.get("primary_joint"), b.get("secondary_joint")}
    joints_a.discard(None)
    joints_b.discard(None)
    return bool(joints_a & joints_b)


def _is_joint_conflict(ex_a: dict, ex_b: dict) -> bool:
    return _is_high_stress(ex_a) and _is_high_stress(ex_b) and _shares_joint(ex_a, ex_b)


def _resolve_joint_stress_conflicts(exercises: list) -> tuple:
    """CF003: detect + reorder to fix; note if unresolvable. O(n^2), fine for
    day lists that are always well under 20 items."""
    exercises = exercises[:]
    notes = []
    i = 1
    while i < len(exercises):
        prev, cur = exercises[i - 1], exercises[i]
        if _is_joint_conflict(prev, cur):
            swapped = False
            for j in range(i + 1, len(exercises)):
                candidate = exercises[j]
                if not _is_joint_conflict(prev, candidate):
                    exercises[i], exercises[j] = exercises[j], exercises[i]
                    swapped = True
                    break
            if not swapped:
                notes.append(
                    f"{prev['name']} and {cur['name']} both load similar joints hard back "
                    f"to back — consider extra rest between them if either feels off."
                )
        i += 1
    return exercises, notes


def _flag_movement_redundancy(exercises: list) -> list:
    """CF001: informational only — no reorder, no removal."""
    counts = {}
    for ex in exercises:
        mv = ex.get("movement_id")
        if mv:
            counts[mv] = counts.get(mv, 0) + 1
    notes = []
    for mv, n in counts.items():
        if n >= 3:
            notes.append(
                f"{n} exercises today share the same movement pattern ({mv.replace('_', ' ')}) — "
                f"that's intentional volume, not a mistake, but flagging in case it wasn't."
            )
    return notes


def _apply_pairing_preference(exercises: list) -> list:
    """
    Engine 13 nudge: for each exercise (left to right), if it has a known
    pairing partner later in this same day's list and they aren't already
    adjacent, move the partner to sit right after it. Each exercise is only
    ever moved once (locked after use) to avoid oscillation. This runs
    BEFORE the joint-stress pass, which has final say — a pairing move that
    creates a joint conflict gets un-done by the pass that follows it.
    """
    exercises = exercises[:]
    locked = set()
    i = 0
    while i < len(exercises) - 1:
        ex = exercises[i]
        eid = ex.get("exercise_id")
        if not eid or id(ex) in locked:
            i += 1
            continue
        try:
            pairings = kb.get_pairings(eid)
        except Exception:
            pairings = []
        partner_id = None
        for p in pairings:
            other = p["secondary_exercise_id"] if p["primary_exercise_id"] == eid else p["primary_exercise_id"]
            if other != eid:
                partner_id = other
                break
        if partner_id:
            for j in range(i + 2, len(exercises)):
                if exercises[j].get("exercise_id") == partner_id and id(exercises[j]) not in locked:
                    exercises.insert(i + 1, exercises.pop(j))
                    locked.add(id(exercises[i + 1]))
                    break
        locked.add(id(ex))
        i += 1
    return exercises


def optimize_day_order(exercises: list) -> tuple:
    """
    Entry point. Takes the day's finalized exercise list (each dict needs
    'exercise_id', 'movement_id', 'name' — already present on everything
    select_day_exercises() returns), returns (reordered_list, notes).
    Never raises; on any internal error, returns the original order unchanged
    with no notes rather than risk corrupting the day's exercise list.
    """
    try:
        ordered = _apply_pairing_preference(exercises)
        ordered, joint_notes = _resolve_joint_stress_conflicts(ordered)
        redundancy_notes = _flag_movement_redundancy(ordered)
        return ordered, joint_notes + redundancy_notes
    except Exception:
        return exercises, []
