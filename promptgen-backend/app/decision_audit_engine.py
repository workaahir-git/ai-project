"""
Decision Audit (Engine 28) — records every real plan-generation and
intra-cycle-adaptation decision this app makes, with input/output hashes
for traceability: "why did this member get this plan/substitution" should
be answerable from a real row, not a guess at what the code probably did
that day.

Real DB-backed, same pattern as every other write-path in this app
(readiness_checkins, workout_set_feedback, etc.) — needs
sql/add_decision_audit_log.sql run in Supabase before rows persist.
Degrades the same way `_apply_intra_cycle_adaptation`'s DB calls already
do: best-effort, wrapped in try/except at the call site, one audit-write
failure never blocks the actual decision it's recording.

input_hash / output_hash are content hashes of the actual input/output
dicts (sorted-key JSON, sha256, truncated for storage) — NOT full
input/output payloads. Real reproducibility check ("did these two runs
get the identical input and produce the identical output") without
storing a member's full profile twice over in a second table.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from app.db import supabase
from app.kb_versioning_engine import stamp_for_generation


def _hash(payload) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]


def record_decision(
    member_id: str,
    decision_type: str,
    source_engines: list[str],
    input_data,
    output_data,
) -> dict:
    """
    decision_type: "plan_generation" | "intra_cycle_adaptation" | ...
      (open vocabulary — callers name their own decision types; this
      engine doesn't gate on a fixed enum, since new decision-producing
      code paths get added over time, same as the rest of this app).
    source_engines: real list of engine module names that contributed to
      this decision — the caller's own knowledge of what it actually
      called, not inferred here (this module has no way to know that
      reliably from outside).
    """
    kb_stamp = stamp_for_generation()
    record = {
        "member_id": member_id,
        "decision_type": decision_type,
        "source_engines": source_engines,
        "input_hash": _hash(input_data),
        "output_hash": _hash(output_data),
        "kb_version": kb_stamp["kb_version"],
        "kb_content_hash": kb_stamp["kb_content_hash"],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        supabase.table("decision_audit_log").insert(record).execute()
    except Exception:
        # Same fail-soft convention as _apply_intra_cycle_adaptation's own
        # DB writes — an audit-log failure must never block the real
        # decision it's trying to record.
        pass
    return record
