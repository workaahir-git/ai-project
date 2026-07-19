"""
analytics_engine.py — Engine 42 (Analytics Engine — Rebuilt).

Full spec (KB engines["42"].spec_text): adherence tracking, plan-metadata
versioning, and longitudinal trend detection across sessions (AN001-AN004).

RELATIONSHIP TO adherence_engine.py (Engine 15)
    adherence_engine.py already exists and computes a session-level
    attendance/completion score (0-100) for ONE cycle, used to gate things
    like "did they train enough this week." That is a different metric
    from what THIS engine's spec actually wants: a sets-based
    adherence_pct ("adherence_pct MUST be recomputed from
    sets_logged/sets_prescribed, never entered independently" per the
    spec's own Validation section), tiered (high/moderate/low), tracked
    ACROSS periods to detect trends and consecutive-high-week streaks.
    This module does not replace or duplicate adherence_engine.py — it
    consumes a *sequence* of periods (which the caller assembles, e.g. one
    per completed cycle) and adds the longitudinal layer adherence_engine
    was never designed to do alone.

WHAT THIS MODULE DOES NOT FABRICATE
    volume_trend and strength_trend require real multi-period history
    (prescribed/logged sets per period, and top logged working weights per
    period). If the caller supplies fewer than 2 periods, trend detection
    returns "insufficient_data" rather than guessing a direction from a
    single data point — same "no data, no fabricated signal" rule this
    whole build follows (see weak_point_engine.py, load_prescription_engine.py
    for the same pattern).

ROUTING RULES (AN001-AN004)
    These name specific target engines per the spec. Mapped to this app's
    actual module names:
      AN001 (low adherence, 2+ periods)      -> feedback_engine (Engine 43)
      AN003 (volume down, adherence high)    -> recovery_capacity_engine (Engine 10)
      AN004 (strength down 3+ periods)       -> plateau_engine (Engine 11)
    This module only RETURNS which engine a period should be routed to —
    it does not call those engines itself, keeping this a pure
    classification/detection layer or orchestration would live one level
    up (in fitness_generator.py), consistent with how every other engine
    in this app is composed.
"""

from __future__ import annotations

ADHERENCE_HIGH_THRESHOLD = 80.0     # inclusive lower bound, per spec Validation note
ADHERENCE_MODERATE_THRESHOLD = 50.0  # inclusive lower bound

CONSECUTIVE_HIGH_FOR_PROGRESSION = 4   # AN002
CONSECUTIVE_LOW_FOR_ROUTING = 2        # AN001
CONSECUTIVE_STRENGTH_DECLINE_FOR_PLATEAU = 3  # AN004


def _tier_for(adherence_pct: float) -> str:
    if adherence_pct >= ADHERENCE_HIGH_THRESHOLD:
        return "high"
    if adherence_pct >= ADHERENCE_MODERATE_THRESHOLD:
        return "moderate"
    return "low"


def compute_period_adherence(sets_prescribed: int, sets_logged: int) -> dict:
    """Single-period adherence_pct/tier. adherence_pct is ALWAYS derived
    from these two counts, never accepted as a raw input (per spec
    Validation rule — prevents drift between the number and its inputs).
    """
    if sets_prescribed <= 0:
        return {"adherence_pct": None, "adherence_tier": None}
    adherence_pct = round(100 * min(sets_logged, sets_prescribed) / sets_prescribed, 1)
    return {"adherence_pct": adherence_pct, "adherence_tier": _tier_for(adherence_pct)}


def _trend(values: list[float]) -> str:
    """Simple, honest trend: compares the most recent value to the average
    of everything before it. Not a regression/slope fit — this app has no
    need for that precision, and a simpler rule is easier to explain to a
    user via coaching_explanation_engine (Engine 18) than a statistical
    model would be.
    """
    if len(values) < 2:
        return "insufficient_data"
    *prior, latest = values
    prior_avg = sum(prior) / len(prior)
    if prior_avg == 0:
        return "insufficient_data"
    delta_pct = (latest - prior_avg) / prior_avg
    if delta_pct >= 0.05:
        return "increasing"
    if delta_pct <= -0.05:
        return "decreasing"
    return "stable"


def build_analytics_record(
    periods: list[dict],
    kb_version: str = "43-engine-build",
    engine_versions: dict | None = None,
) -> dict:
    """
    periods: list of dicts, OLDEST FIRST, each with:
        sets_prescribed, sets_logged,
        top_working_weight_kg (optional, for strength_trend),
        period_start, period_end (optional, passthrough)
    Must be real historical periods the caller assembled (e.g. from
    adherence_engine.py's per-cycle output + workout_set_feedback) — this
    function performs no DB reads itself, same separation-of-concerns
    convention as every other engine module in this app.

    Returns a dict matching the KB schema (analytics engine v2.0.0) plus a
    `routing` block naming which engine(s) this period should be routed to,
    per AN001/AN003/AN004.
    """
    if not periods:
        return {
            "adherence_pct": None,
            "adherence_tier": None,
            "consecutive_high_weeks": 0,
            "volume_trend": "insufficient_data",
            "strength_trend": "insufficient_data",
            "kb_version": kb_version,
            "engine_versions": engine_versions or {},
            "routing": [],
        }

    latest = periods[-1]
    latest_adherence = compute_period_adherence(latest["sets_prescribed"], latest["sets_logged"])

    # consecutive_high_weeks — walk back from the most recent period while
    # each period's own tier is "high".
    consecutive_high_weeks = 0
    for p in reversed(periods):
        pct_tier = compute_period_adherence(p["sets_prescribed"], p["sets_logged"])["adherence_tier"]
        if pct_tier == "high":
            consecutive_high_weeks += 1
        else:
            break

    # consecutive_low_weeks — same walk, for AN001's routing trigger.
    consecutive_low_weeks = 0
    for p in reversed(periods):
        pct_tier = compute_period_adherence(p["sets_prescribed"], p["sets_logged"])["adherence_tier"]
        if pct_tier == "low":
            consecutive_low_weeks += 1
        else:
            break

    logged_counts = [p["sets_logged"] for p in periods]
    volume_trend = _trend(logged_counts)

    strength_weights = [p["top_working_weight_kg"] for p in periods if p.get("top_working_weight_kg") is not None]
    strength_trend = _trend(strength_weights) if len(strength_weights) >= 2 else "insufficient_data"

    # Track consecutive strength-decline periods for AN004 (needs its own
    # walk since strength_trend above is a single 2-bucket comparison, not
    # a run length).
    consecutive_strength_decline = 0
    if len(strength_weights) >= 2:
        for i in range(len(strength_weights) - 1, 0, -1):
            if strength_weights[i] < strength_weights[i - 1]:
                consecutive_strength_decline += 1
            else:
                break

    routing = []
    if consecutive_low_weeks >= CONSECUTIVE_LOW_FOR_ROUTING:
        routing.append({"engine": "feedback_engine", "reason": "AN001: low adherence 2+ consecutive periods"})
    if volume_trend == "decreasing" and latest_adherence["adherence_tier"] == "high":
        routing.append({"engine": "recovery_capacity_engine", "reason": "AN003: volume down despite high adherence — possible under-recovery"})
    if strength_trend == "decreasing" and consecutive_strength_decline >= CONSECUTIVE_STRENGTH_DECLINE_FOR_PLATEAU and latest_adherence["adherence_tier"] == "high":
        routing.append({"engine": "plateau_engine", "reason": "AN004: strength down 3+ periods despite high adherence"})

    eligible_for_progression = consecutive_high_weeks >= CONSECUTIVE_HIGH_FOR_PROGRESSION  # AN002

    return {
        "adherence_pct": latest_adherence["adherence_pct"],
        "adherence_tier": latest_adherence["adherence_tier"],
        "consecutive_high_weeks": consecutive_high_weeks,
        "eligible_for_progression": eligible_for_progression,
        "volume_trend": volume_trend,
        "strength_trend": strength_trend,
        "kb_version": kb_version,
        "engine_versions": engine_versions or {},
        "routing": routing,
    }
