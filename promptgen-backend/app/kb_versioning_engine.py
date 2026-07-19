"""
Knowledge Versioning (Engine 32) — scoped to what this app actually needs:
knowing whether the KB's own declared version string (`meta.kb_version`
in knowledge_base.json) still matches its actual content, and stamping
every generated plan with the real version that produced it.

Real, found gap this engine surfaces rather than hides: sessions 18, 19,
21, and 22 all made genuine content edits to knowledge_base.json
(progressions/regressions/substitutions/pairings fills) without ever
bumping `meta.kb_version` (still "7.1.0" from before any of that work).
This engine computes a content hash independent of the declared version
string, so that drift is DETECTABLE going forward instead of silently
assumed away — it does not retroactively guess what the "correct" next
version number should have been; that's an editorial call for a human,
not something to fabricate here.
"""

from __future__ import annotations

import hashlib
import json

from app import knowledge_base as kb


def _content_hash() -> str:
    """Deterministic hash of the KB's actual `engines` content (NOT
    including `meta` itself, so bumping the version string doesn't change
    its own input) — sort_keys=True so key ordering never affects the
    hash, only real content changes do."""
    payload = json.dumps(kb._KB["engines"], sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def get_kb_version_info() -> dict:
    """
    Returns:
      declared_version: the meta.kb_version string as currently written
      content_hash: real hash of current engine content
      total_engines: real count, cross-checked against meta.total_engines
      engine_count_matches_meta: bool — catches a meta.total_engines that
        drifted from the real engines dict (a different, cheaper kind of
        staleness than the content hash catches)
    """
    meta = kb._KB.get("meta", {})
    real_count = len(kb._ENGINES)
    declared_count = meta.get("total_engines")
    return {
        "declared_version": meta.get("kb_version"),
        "content_hash": _content_hash(),
        "total_engines_real": real_count,
        "total_engines_declared": declared_count,
        "engine_count_matches_meta": real_count == declared_count,
    }


def stamp_for_generation() -> dict:
    """What a single plan-generation request should record about which
    KB produced it — content_hash is the part that actually proves
    reproducibility (two plans with the same hash were generated against
    byte-identical KB content); declared_version is just the human-facing
    label, which this session's own finding shows can't be trusted alone."""
    info = get_kb_version_info()
    return {"kb_version": info["declared_version"], "kb_content_hash": info["content_hash"]}
