"""
Continuous Improvement (Engine 37) — the general case
`research_integration_engine.py` (38) is the specific one of: any proposal
to change the KB, not just ones sourced from published research.
Concretely, this is what sessions 18/19/21/22 of this app's own history
WERE, retroactively formalized: "fill missing progressions/regressions
from real shared attributes" was a `content_update` improvement proposal
with `evidence_level: moderate` (algorithmic derivation, not hand-curated)
that got approved and implemented without ever being recorded as one.
Going forward, a real proposal record exists instead of only a HANDOFF.md
paragraph.

CI002 (conflicts existing rule -> route to Knowledge Consistency) is
real here, not aspirational: `propose_improvement()` calls
`kb_versioning_engine`'s real content-hash / meta-mismatch check as its
stand-in for Engine 30 (Knowledge Consistency was never built — flagging
that honestly rather than pretending this routes somewhere real that
doesn't exist) and flags the proposal `needs_consistency_review` if the
KB is already showing drift, rather than silently accepting a new change
on top of an already-inconsistent state.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.db import supabase
from app.kb_versioning_engine import get_kb_version_info

SOURCES = ("user_feedback", "scientific_evidence", "audit", "performance_metrics")
IMPROVEMENT_TYPES = ("rule_update", "schema_update", "content_update", "bug_fix")
EVIDENCE_LEVELS = ("low", "moderate", "high")


def propose_improvement(
    source: str,
    affected_engines: list[str],
    improvement_type: str,
    evidence_level: str,
    description: str,
) -> dict:
    if source not in SOURCES:
        raise ValueError(f"source must be one of {SOURCES}, got {source!r}")
    if improvement_type not in IMPROVEMENT_TYPES:
        raise ValueError(f"improvement_type must be one of {IMPROVEMENT_TYPES}, got {improvement_type!r}")
    if evidence_level not in EVIDENCE_LEVELS:
        raise ValueError(f"evidence_level must be one of {EVIDENCE_LEVELS}, got {evidence_level!r}")

    # CI002, real not aspirational: Engine 30 (Knowledge Consistency) was
    # never built in this app — kb_versioning_engine's declared-vs-real
    # mismatch check is the closest real signal available, used honestly
    # as a substitute rather than routing to something that doesn't exist.
    kb_info = get_kb_version_info()
    needs_consistency_review = not kb_info["engine_count_matches_meta"]

    record = {
        "source": source,
        "affected_engines": affected_engines,
        "improvement_type": improvement_type,
        "evidence_level": evidence_level,
        "description": description,
        "validation_status": "proposed",
        "needs_consistency_review": needs_consistency_review,
        "kb_content_hash_at_proposal": kb_info["content_hash"],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        result = supabase.table("improvement_proposals").insert(record).execute()
        if result.data:
            record["improvement_record_id"] = result.data[0].get("id")
    except Exception:
        pass
    return record


def approve_improvement(improvement_record_id, implementation_version: str) -> dict:
    """CI004: approved proposal -> schedule next release. Same discipline
    as research_integration_engine's approve_research() — flips status
    only, never auto-applies the actual KB edit."""
    update = {"validation_status": "approved", "implementation_version": implementation_version}
    try:
        supabase.table("improvement_proposals").update(update).eq(
            "id", improvement_record_id,
        ).execute()
    except Exception:
        pass
    return {"improvement_record_id": improvement_record_id, **update}
