"""
Reasoning Orchestration (Engine 31) — deliberately built as an OBSERVER,
not a controller. The real execution order across ~30 wired engines
already exists and works, hand-built and carefully verified across
sessions 13-20 of HANDOFF.md — rewriting `main.py`'s `_run()` into a
declarative "orchestrator runs the list" architecture would mean
re-deriving a large amount of already-correct, already-tested control
flow, for a real risk of breaking it, in exchange for a formalism this
app's actual size (one process, one team) doesn't clearly need yet. See
HANDOFF's own session on this: 31 was flagged as "the concept already
exists, just hardcoded" rather than a real gap — this module makes that
concept inspectable without touching the working code it describes.

DECLARED_ORDER below is extracted from main.py's actual `_run()` source
(grepped, not recalled from memory) — kept here as living documentation
of the real dependency chain. If a future session changes call order in
main.py, this file should be re-derived from the real source again, not
hand-edited to match — the whole point is this stays a description of
what the code does, not an independent claim about what it should do.
"""

from __future__ import annotations

# Real order, as of session 25 — grepped from app/main.py's `_run()`.
# Each entry's dependencies are the OTHER entries in this list whose
# output it reads (per the actual `main.py` code, not inferred).
DECLARED_ORDER = [
    {"engine": "recovery_capacity_engine", "depends_on": []},
    {"engine": "adherence_engine", "depends_on": []},
    {"engine": "volume_allocation_engine", "depends_on": []},
    {"engine": "goal_optimization_engine", "depends_on": ["recovery_capacity_engine"]},
    {"engine": "periodization_engine", "depends_on": ["recovery_capacity_engine", "adherence_engine"]},
    {"engine": "programming_engine", "depends_on": ["goal_optimization_engine", "periodization_engine"]},
    {"engine": "weak_point_engine", "depends_on": []},
    {"engine": "coaching_explanation_engine", "depends_on": ["weak_point_engine"]},
    {"engine": "fatigue_management_engine", "depends_on": ["recovery_capacity_engine"]},
    {"engine": "autoregulation_engine", "depends_on": ["recovery_capacity_engine"]},
    {"engine": "adaptation_tracking_engine", "depends_on": ["recovery_capacity_engine", "adherence_engine"]},
    {"engine": "predictive_progression_engine", "depends_on": ["adaptation_tracking_engine", "fatigue_management_engine"]},
    {"engine": "diet_phase_engine", "depends_on": ["recovery_capacity_engine"]},
    {"engine": "diet_engine", "depends_on": ["diet_phase_engine"]},
]


def get_declared_order() -> list[dict]:
    return list(DECLARED_ORDER)


def validate_dependencies() -> dict:
    """Real, cheap sanity check: does every declared dependency actually
    appear earlier in the list than the engine that depends on it? Catches
    a future hand-edit mistake in DECLARED_ORDER itself — not a claim
    about main.py's real runtime behavior, just internal consistency of
    this file's own documentation."""
    seen = set()
    violations = []
    for entry in DECLARED_ORDER:
        for dep in entry["depends_on"]:
            if dep not in seen:
                violations.append({"engine": entry["engine"], "missing_or_late_dependency": dep})
        seen.add(entry["engine"])
    return {"valid": not violations, "violations": violations}
