"""
knowledge_base.py
──────────────────────────────────────────────────────────────────────────────
Loads KNOWLEDGE_BASE_ENGINES_1_TO_43_COMBINED.json (data/knowledge_base.json)
once at import time and exposes typed lookup functions over the 10
DATA_COMPLETE engines: Movement (1), Exercise Metadata (2), Joint Stress (4),
Stimulus-to-Fatigue (5), Recovery (6), Skill (7), Biomechanics (8), Pairing
(13), Tempo & Intent (16), Substitution (41).

The other 33 engines in the file are LOGIC_ONLY (decision rules, not data) —
this module does not surface those; they get implemented as plain Python
where each is needed (safety_engine.py, programming_rules.py, etc.), reading
`spec_text` from the JSON directly if/when that work happens.

Every lookup here is by `exercise_id` or `movement_id` — the same IDs stored
on each EXERCISE_DB entry in exercise_database.py as `_exercise_id` /
`_movement_id`. Nothing in this module changes existing behavior; it's new
surface area for features that don't exist yet (exercise swap, pairing-aware
day building, recovery-aware scheduling).
"""

from __future__ import annotations
import json
import os

_KB_PATH = os.path.join(os.path.dirname(__file__), "data", "knowledge_base.json")

with open(_KB_PATH, "r") as _f:
    _KB = json.load(_f)

_ENGINES = _KB["engines"]


def _profiles(engine_num: str) -> dict:
    return _ENGINES[engine_num]["data"]["profiles"]


# ── Engine 1 — Movement Intelligence (canonical movement_id taxonomy) ──────
def get_canonical_movement_ids() -> tuple[str, ...]:
    """The KB's authoritative movement_id list (engine 1's structured data,
    not its stale markdown spec_text table — see movement_engine.py's
    module docstring for why those two diverge)."""
    return tuple(_ENGINES["1"]["data"]["canonical_movement_ids"])


# ── Engine 2 — Exercise Metadata (401 exercises) ────────────────────────────
_EXERCISES_BY_ID = {e["exercise_id"]: e for e in _ENGINES["2"]["data"]["exercises"]}


def get_exercise(exercise_id: str) -> dict | None:
    """Full Engine-2 metadata for one exercise_id, or None if unknown."""
    return _EXERCISES_BY_ID.get(exercise_id)


# ── Engine 4 / 5 / 6 / 7 / 8 / 16 — movement-level profiles ─────────────────
_JOINT_STRESS_BY_MV = {p["movement_id"]: p for p in _profiles("4").values()}
_FATIGUE_BY_MV = {p["movement_id"]: p for p in _profiles("5").values()}
_RECOVERY_BY_MV = {p["movement_id"]: p for p in _profiles("6").values()}
_SKILL_BY_MV = {p["movement_id"]: p for p in _profiles("7").values()}
_BIOMECHANICS_BY_MV = {p["movement_id"]: p for p in _profiles("8").values()}
_TEMPO_BY_MV = {p["movement_id"]: p for p in _profiles("16").values()}


def get_joint_stress(movement_id: str) -> dict | None:
    return _JOINT_STRESS_BY_MV.get(movement_id)


def get_fatigue(movement_id: str) -> dict | None:
    return _FATIGUE_BY_MV.get(movement_id)


def get_recovery(movement_id: str) -> dict | None:
    return _RECOVERY_BY_MV.get(movement_id)


def get_skill(movement_id: str) -> dict | None:
    return _SKILL_BY_MV.get(movement_id)


def get_biomechanics(movement_id: str) -> dict | None:
    return _BIOMECHANICS_BY_MV.get(movement_id)


def get_tempo(movement_id: str) -> dict | None:
    return _TEMPO_BY_MV.get(movement_id)


# ── Engine 13 — Pairing (exercise-level) ────────────────────────────────────
_PAIRINGS_BY_EXERCISE: dict[str, list[dict]] = {}
for _p in _profiles("13").values():
    _PAIRINGS_BY_EXERCISE.setdefault(_p["primary_exercise_id"], []).append(_p)
    _PAIRINGS_BY_EXERCISE.setdefault(_p["secondary_exercise_id"], []).append(_p)


def get_pairings(exercise_id: str) -> list[dict]:
    """All curated pairing profiles involving this exercise_id (either side)."""
    return list(_PAIRINGS_BY_EXERCISE.get(exercise_id, []))


# ── Engine 41 — Substitution (exercise-level) ───────────────────────────────
_SUBSTITUTION_BY_SOURCE = {
    p["source_exercise_id"]: p for p in _profiles("41").values()
}


def get_substitutes(exercise_id: str, min_equivalence: int = 0) -> list[dict]:
    """
    Ranked candidate substitutes for exercise_id, best (highest
    equivalence_score) first. Returns [] if the exercise has no
    substitution rule on file or its only rule is `no_safe_substitute`.
    `min_equivalence` filters out weak matches for callers that need a
    stricter bar than the data's own >=40 floor.
    """
    rule = _SUBSTITUTION_BY_SOURCE.get(exercise_id)
    if not rule or rule.get("no_safe_substitute"):
        return []
    candidates = rule.get("candidate_substitutes", [])
    filtered = [c for c in candidates if c["equivalence_score"] >= min_equivalence]
    return sorted(filtered, key=lambda c: -c["equivalence_score"])


def get_conflict_partners(exercise_id: str) -> list[str]:
    rule = _SUBSTITUTION_BY_SOURCE.get(exercise_id)
    if not rule:
        return []
    return list(rule.get("conflict_partners", []))


# ── Convenience: full per-exercise engine bundle, one lookup ────────────────
def get_full_profile(exercise_id: str) -> dict | None:
    """
    Everything the KB knows about one exercise in a single dict — metadata
    plus its movement's joint stress / fatigue / recovery / skill /
    biomechanics / tempo, plus its own pairings and substitutes. Convenience
    wrapper for future features (exercise detail view, swap UI) that would
    otherwise need 7 separate lookups.
    """
    meta = get_exercise(exercise_id)
    if meta is None:
        return None
    mv = meta["movement_id"]
    return {
        "metadata": meta,
        "joint_stress": get_joint_stress(mv),
        "fatigue": get_fatigue(mv),
        "recovery": get_recovery(mv),
        "skill": get_skill(mv),
        "biomechanics": get_biomechanics(mv),
        "tempo": get_tempo(mv),
        "pairings": get_pairings(exercise_id),
        "substitutes": get_substitutes(exercise_id),
    }
