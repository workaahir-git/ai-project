"""
tempo_engine.py — Engine 16 (Tempo & Intent).

Full coverage verified: all 13 movement_ids this app tags are in the KB's
14 tempo profiles (1 extra: conditioning — not currently tagged on
anything here).

Real data shape: default_tempo (string like "3-1-2-0" = eccentric-pause-
concentric-lockout seconds), the same 4 values as separate int fields,
intent (explosive/controlled/max_force/hypertrophy/rehabilitation),
cue_priority (ordered list).

KB status for this engine is "COMPLETE (Foundation)" per spec_text, not
plain DATA_COMPLETE like the others — noted as-is, not treated as a
lower-confidence signal since the data itself is populated the same way.
"""

from __future__ import annotations

from app import knowledge_base as kb
from app.exercise_database import EXERCISE_DB

_MOVEMENT_ID_BY_EXERCISE: dict[str, str] = {
    ex["_exercise_id"]: ex["_movement_id"]
    for _mg in EXERCISE_DB.values()
    for _cat in _mg.values()
    for ex in _cat
    if ex.get("_exercise_id") and ex.get("_movement_id")
}


def get_profile(exercise_id: str) -> dict | None:
    movement_id = _MOVEMENT_ID_BY_EXERCISE.get(exercise_id)
    if not movement_id:
        return None
    return kb.get_tempo(movement_id)


def get_tempo_instruction(exercise_id: str) -> str | None:
    """One-line coaching instruction combining tempo notation, intent, and
    the top cue. e.g. 'Tempo 3-1-2-0 (hypertrophy) — brace.' None if no
    profile exists."""
    profile = get_profile(exercise_id)
    if not profile:
        return None

    tempo = profile.get("default_tempo")
    intent = profile.get("intent")
    cues = profile.get("cue_priority") or []
    top_cue = cues[0] if cues else None

    parts = []
    if tempo:
        parts.append(f"Tempo {tempo}")
    if intent:
        parts.append(f"({intent})")
    line = " ".join(parts)
    if top_cue:
        line = f"{line} — {top_cue}." if line else f"{top_cue}."
    return line or None
