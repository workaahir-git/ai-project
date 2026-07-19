"""
Deployment & Environment (Engine 34) — scoped to what this app actually
runs on: one FastAPI process reading one .env file and one
knowledge_base.json, not multi-environment (dev/staging/prod) fleet
orchestration. What that means here: a real, callable check that the
required runtime prerequisites are actually present BEFORE a request
fails halfway through generation with a confusing error — not a
deployment pipeline.

Deliberately does NOT crash the app on a failed check (no `raise` on
import, no FastAPI startup-event abort) — see PLACEHOLDER_MARKERS below;
during active development (like every session in this HANDOFF's own
history) `GEMINI_API_KEY=PASTE_KEY_HERE` is a completely normal, expected
.env state, and a hard crash on that would have blocked every single
verification test run in every prior session. `validate_environment()`
reports real status; callers (an admin endpoint, or a future explicit
startup check) decide what to do with a failure — same "surface the
problem, don't fabricate a fix or force a shutdown" discipline as
everywhere else in this app.
"""

from __future__ import annotations

import sys

REQUIRED_SETTINGS = [
    "supabase_url", "supabase_jwt_secret", "supabase_service_role_key",
    "demo_gym_id", "gemini_api_key",
]

# Values that indicate a setting is PRESENT (so pydantic Settings didn't
# already fail loud on a missing required field) but still a placeholder,
# not real. Found by checking config.py + this session's own .env test
# fixture pattern used across every prior session's verification runs.
PLACEHOLDER_MARKERS = {"PASTE_KEY_HERE", "dummysecret", "dummykey", ""}


def _check_settings() -> dict:
    from app.config import settings
    results = {}
    for name in REQUIRED_SETTINGS:
        val = getattr(settings, name, None)
        if val is None:
            results[name] = "MISSING"
        elif any(marker and marker in str(val) for marker in PLACEHOLDER_MARKERS):
            results[name] = "PLACEHOLDER"
        else:
            results[name] = "OK"
    return results


def _check_kb() -> dict:
    try:
        from app import knowledge_base as kb
        return {
            "loads": True,
            "engine_count": len(kb._ENGINES),
            "exercise_count": len(kb._EXERCISES_BY_ID),
        }
    except Exception as e:
        return {"loads": False, "error": str(e)}


def _check_python() -> dict:
    return {
        "version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "meets_minimum_3_11": sys.version_info >= (3, 11),
    }


def validate_environment() -> dict:
    """
    Real environment check, not a simulated one. Returns:
      status: "ready" | "degraded" | "not_ready"
        - not_ready: any REQUIRED_SETTINGS entry is MISSING, or the KB
          fails to load at all — the app genuinely cannot serve a real
          request.
        - degraded: settings present but PLACEHOLDER (dev/test .env,
          exactly like every prior session's own test fixture) — the app
          runs, but is not production-ready.
        - ready: everything real.
      settings / kb / python: the three real sub-checks.
    """
    settings_check = _check_settings()
    kb_check = _check_kb()
    python_check = _check_python()

    if any(v == "MISSING" for v in settings_check.values()) or not kb_check.get("loads"):
        status = "not_ready"
    elif any(v == "PLACEHOLDER" for v in settings_check.values()):
        status = "degraded"
    else:
        status = "ready"

    return {"status": status, "settings": settings_check, "kb": kb_check, "python": python_check}
