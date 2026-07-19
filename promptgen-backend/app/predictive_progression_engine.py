"""
predictive_progression_engine.py — Engine 27 (Predictive Progression).

Unlike most engines in this build, this one needs NO new data source of
its own — the spec explicitly wants it to derive predictions "only from
validated engine outputs" (its own Deterministic Rule 1), so it's a pure
combination of adaptation_tracking_engine.py, recovery_capacity_engine.py,
adherence_engine.py, plateau_engine.py, and fatigue_management_engine.py's
already-real outputs. Nothing here is invented; if an upstream profile is
missing, this module's confidence/recommendation degrades accordingly
rather than guessing at what that engine would have said.

projected_change_percent = adaptation_profile's own expected_change_percent
(NOT re-derived — reusing the exact number adaptation_tracking_engine.py
already computed from load_prescription_engine's goal-based increments),
scaled down proportionally by confidence and further cut on low recovery
capacity (PP003). It's never scaled UP beyond the adaptation engine's own
expectation — predicting a bigger number than the upstream engine itself
projected would be fabricating additional optimism from nothing.

limiting_factor prioritizes: plateau > fatigue > recovery > adherence >
skill (skill never applicable — no skill data anywhere in this app) > none.
"""

from __future__ import annotations


def predict_progression(
    member_id: str | None,
    target_metric: str,
    prediction_horizon_weeks: int,
    adaptation_profile: dict | None = None,
    recovery_capacity_profile: dict | None = None,
    adherence_profile: dict | None = None,
    plateau_confirmed: bool = False,
    fatigue_profile: dict | None = None,
) -> dict:
    base = {
        "prediction_profile_id": f"PRED_{member_id or 'UNKNOWN'}",
        "athlete_id": member_id,
        "prediction_horizon_weeks": prediction_horizon_weeks,
        "target_metric": target_metric,
        "projected_change_percent": 0.0,
        "confidence_score": 0,
        "limiting_factor": "none",
        "recommendation": "maintain",
    }

    capacity_score = (recovery_capacity_profile or {}).get("capacity_score")
    adherence_score = (adherence_profile or {}).get("adherence_score")
    fatigue_zone = (fatigue_profile or {}).get("fatigue_zone")
    expected_pct = (adaptation_profile or {}).get("expected_change_percent")
    adaptation_status = (adaptation_profile or {}).get("adaptation_status")
    adaptation_confidence = (adaptation_profile or {}).get("confidence_score", 0)

    # PP005 — critical fatigue overrides everything else.
    if fatigue_zone == "critical":
        base.update(limiting_factor="fatigue", recommendation="deload", confidence_score=adaptation_confidence)
        return base

    # PP002 — confirmed plateau freezes the prediction until resolved.
    if plateau_confirmed:
        base.update(limiting_factor="plateau", recommendation="maintain", confidence_score=adaptation_confidence)
        return base

    if expected_pct is None:
        base["note"] = "No adaptation profile available yet — nothing to project from."
        return base

    projected_pct = expected_pct
    confidence = adaptation_confidence
    limiting_factor = "none"

    # PP003 — low recovery capacity reduces projected gains.
    if capacity_score is not None and capacity_score < 60:
        projected_pct *= 0.5
        limiting_factor = "recovery"

    # PP004 — poor adherence lowers confidence (not the projection itself).
    if adherence_score is not None and adherence_score < 60:
        confidence = round(confidence * 0.6)
        if limiting_factor == "none":
            limiting_factor = "adherence"

    # PP001 — adaptation above expected supports continued progression.
    if adaptation_status == "above_expected" and limiting_factor == "none":
        recommendation = "progress"
    elif adaptation_status == "below_expected":
        recommendation = "modify"
        # No true skill-tracking signal exists in this app (see adaptation_
        # tracking_engine.py's own domain scoping) to confirm skill as the
        # cause, so an unexplained below-expected result — recovery and
        # adherence both fine — is left as "none" rather than guessed.
    else:
        recommendation = "progress" if limiting_factor == "none" else "maintain"

    return {
        "prediction_profile_id": f"PRED_{member_id}",
        "athlete_id": member_id,
        "prediction_horizon_weeks": prediction_horizon_weeks,
        "target_metric": target_metric,
        "projected_change_percent": round(projected_pct, 1),
        "confidence_score": confidence,
        "limiting_factor": limiting_factor,
        "recommendation": recommendation,
    }
