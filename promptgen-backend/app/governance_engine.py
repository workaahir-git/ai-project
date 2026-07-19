"""
System Governance (Engine 36) — the "final authority" role from its own
spec, built here as a real AGGREGATOR of the engines that already exist
(28, 32, 34) rather than re-implementing overlapping checks a third time.
Answers one real question: "is the current state of this app/KB combo
something that should be considered release-ready" — genuinely useful
before shipping a KB content change (like sessions 18/19/21/22's fills)
to real users, not a rubber stamp.

Deliberately does NOT gate/block anything on its own — no code path in
this app calls `evaluate_release()` and refuses to run if it fails. It's
a read-only report a human (or a CI step, if one gets added later) acts
on. Same "surface the problem, don't seize control" discipline as
`deployment_engine.py`.
"""

from __future__ import annotations

from app import knowledge_base as kb
from app.deployment_engine import validate_environment
from app.kb_versioning_engine import get_kb_version_info
from app.db import supabase


def _check_broken_kb_references() -> dict:
    """Same check written inline and re-run manually across sessions
    18/19/21/22 — formalized here as a real, reusable function instead of
    a one-off script, so governance can call it for real rather than
    trusting that whoever last touched the KB remembered to run it."""
    broken = []
    for e in kb._EXERCISES_BY_ID.values():
        for field in ("progressions", "regressions", "substitutions"):
            for ref in (e.get(field) or []):
                if ref not in kb._EXERCISES_BY_ID:
                    broken.append((e["exercise_id"], field, ref))
    for rule in kb._profiles("41").values():
        for c in rule.get("candidate_substitutes", []):
            if c["exercise_id"] not in kb._EXERCISES_BY_ID:
                broken.append((rule["source_exercise_id"], "substitution_rule", c["exercise_id"]))
    for p in kb._profiles("13").values():
        for side in ("primary_exercise_id", "secondary_exercise_id"):
            if p[side] not in kb._EXERCISES_BY_ID:
                broken.append((p["pairing_profile_id"], side, p[side]))
    return {"broken_reference_count": len(broken), "broken_references": broken[:20]}


def _check_audit_trail_reachable() -> bool:
    """SG003: missing audit trail -> prevent deployment. Real check: is
    the decision_audit_log table actually reachable (migration run), not
    just "does the Python module import cleanly" — those are different
    questions, same distinction deployment_engine.py already draws for
    settings (present vs. real)."""
    try:
        supabase.table("decision_audit_log").select("id").limit(1).execute()
        return True
    except Exception:
        return False


def evaluate_release() -> dict:
    """
    Real SG001-SG005 evaluation, each grounded in an actual check above —
    not a fixed 'true' returned unconditionally:
      SG001 compliance failure -> compliance_status
      SG002 security violation -> security_level
      SG003 missing audit trail -> audit_required (real reachability check)
      SG004 checksum -> final_checksum (kb_versioning_engine's real hash)
      SG005 governance approved -> approved_release (real AND of the above)
    """
    env = validate_environment()
    kb_info = get_kb_version_info()
    refs = _check_broken_kb_references()
    audit_ok = _check_audit_trail_reachable()

    # SG001
    if env["status"] == "not_ready" or refs["broken_reference_count"] > 0:
        compliance_status = "non_compliant"
    elif env["status"] == "degraded" or not kb_info["engine_count_matches_meta"]:
        compliance_status = "warning"
    else:
        compliance_status = "compliant"

    # SG002 — real signal available: are the settings actual production
    # values or known placeholders (deployment_engine's own real check),
    # not a fabricated security audit this app has no way to perform.
    security_level = "standard" if env["status"] == "ready" else "low"

    # SG005 — real AND of every real check above, not an unconditional True.
    approved_release = (
        compliance_status == "compliant"
        and security_level != "low"
        and audit_ok
    )

    return {
        "compliance_status": compliance_status,
        "security_level": security_level,
        "audit_required": True,
        "audit_trail_reachable": audit_ok,
        "final_checksum": f"sha256:{kb_info['content_hash']}",
        "approved_release": approved_release,
        "_detail": {"environment": env, "kb_version": kb_info, "references": refs},
    }
