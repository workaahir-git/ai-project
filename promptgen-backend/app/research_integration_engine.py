"""
Research Integration (Engine 38) — scoped to what this app can honestly
do: it has no literature-search capability and no way to independently
verify a study's actual quality. What it CAN do for real: be the
structured intake + gate for a HUMAN who's reviewed a piece of research
and wants to propose it change the KB — same shape as the exercise-data
fills sessions 18/19/21/22 did by hand this session, formalized so that
process has a real record instead of "someone edited knowledge_base.json
and wrote a paragraph about it in HANDOFF.md."

Real DB-backed (new table, same "run this SQL migration" pattern as
decision_audit_log). RI003 (evidence grade below threshold -> reject) is
enforced here for real — grade D is auto-rejected on submission, not
left as a manual step someone might skip.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.db import supabase

EVIDENCE_GRADES = ("A", "B", "C", "D")
# RI003: real threshold, not decorative. Grade D (expert consensus / weak
# evidence) is auto-rejected — matches the KB's own spec table, which
# lists "Evidence grade below threshold -> Reject integration" as a rule,
# not a suggestion.
MIN_ACCEPTABLE_GRADE = "C"


def submit_research(
    publication_id: str,
    source_type: str,
    evidence_grade: str,
    affected_engines: list[str],
    reviewer: str,
) -> dict:
    if evidence_grade not in EVIDENCE_GRADES:
        raise ValueError(f"evidence_grade must be one of {EVIDENCE_GRADES}, got {evidence_grade!r}")

    # RI003 enforced at submission, not left as a downstream manual check.
    status = "rejected" if evidence_grade > MIN_ACCEPTABLE_GRADE else "pending"

    record = {
        "publication_id": publication_id,
        "source_type": source_type,
        "evidence_grade": evidence_grade,
        "affected_engines": affected_engines,
        "integration_status": status,
        "reviewer": reviewer,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        result = supabase.table("research_integration_log").insert(record).execute()
        if result.data:
            record["research_record_id"] = result.data[0].get("id")
    except Exception:
        # Same fail-soft convention as decision_audit_engine — a logging
        # failure must never be the thing that blocks a real review.
        pass
    return record


def approve_research(research_record_id, implementation_version: str) -> dict:
    """RI004: governance approval received -> schedule implementation. This
    only flips the status flag — it does NOT touch knowledge_base.json
    itself. Actually applying an approved research change to the KB is
    real content work (like sessions 18/19/21/22 did), not something to
    automate here; automating "apply this research finding to the KB" is
    exactly the kind of fabrication this app's whole discipline has been
    refusing to do."""
    update = {"integration_status": "approved", "implementation_version": implementation_version}
    try:
        supabase.table("research_integration_log").update(update).eq(
            "id", research_record_id,
        ).execute()
    except Exception:
        pass
    return {"research_record_id": research_record_id, **update}
