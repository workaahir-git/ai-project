"""
Monitoring & Observability (Engine 35) — scoped to what this app actually
is: a single Python process, no Prometheus/Datadog/Sentry account
connected. Real, honest limitation stated up front: this is IN-PROCESS
memory, not persisted, not shared across workers if this ever runs behind
more than one process, and resets on every restart. That's genuinely
useful for "is this one running instance healthy right now" (an
/api/admin/metrics endpoint) but is NOT a substitute for real observability
infrastructure at actual production scale — wiring a real APM/metrics
backend needs credentials this app doesn't have and shouldn't be
fabricated. Documented here so nobody mistakes this for more than it is.

Usage: `with track("engine_name"): ...` around any call worth watching —
records a count, error count, and timing without changing the wrapped
code's behavior or swallowing its exceptions (re-raises after recording).
"""

from __future__ import annotations

import time
from contextlib import contextmanager

_METRICS: dict[str, dict] = {}
_STARTED_AT = time.time()


@contextmanager
def track(name: str):
    entry = _METRICS.setdefault(name, {"calls": 0, "errors": 0, "total_ms": 0.0})
    start = time.perf_counter()
    entry["calls"] += 1
    try:
        yield
    except Exception:
        entry["errors"] += 1
        raise
    finally:
        entry["total_ms"] += (time.perf_counter() - start) * 1000


def record_error(name: str) -> None:
    """For call sites that already catch-and-continue (e.g. the intra-cycle
    adaptation try/except in main.py) — records the error without needing
    to restructure existing exception handling into the `track()` context
    manager shape."""
    entry = _METRICS.setdefault(name, {"calls": 0, "errors": 0, "total_ms": 0.0})
    entry["errors"] += 1


def get_metrics() -> dict:
    uptime_s = round(time.time() - _STARTED_AT, 1)
    per_engine = {}
    for name, e in _METRICS.items():
        avg_ms = round(e["total_ms"] / e["calls"], 2) if e["calls"] else 0.0
        per_engine[name] = {
            "calls": e["calls"],
            "errors": e["errors"],
            "avg_ms": avg_ms,
            "error_rate": round(e["errors"] / e["calls"], 4) if e["calls"] else 0.0,
        }
    return {"process_uptime_seconds": uptime_s, "engines": per_engine}
