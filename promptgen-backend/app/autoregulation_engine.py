"""
autoregulation_engine.py — Engine 24 (Autoregulation), built on Engines 9
(Readiness) and 10 (Recovery Capacity), which now exist (Phase 2).

Full spec (KB engines["24"].spec_text) wants readiness_score, fatigue_score,
recovery_score as inputs. This app has:

  readiness_score  — real, from readiness_engine.py, for the specific
                     session being evaluated (needs a check-in on file).
  fatigue_score    — real, reused directly from recovery_capacity_engine.py's
                     own fatigue proxy (average recent difficulty rating) —
                     not recomputed a second way, so the two engines never
                     disagree on what "fatigue" means in this app.
  recovery_score   — NOT collected anywhere (that's Engine 6, not built).
                     Always None, not invented.

AR005 (safety flag) reuses readiness_profile's pain_flag/illness_flag —
the same signals RD001/RD002 already gate on in readiness_engine.py, so a
"cancel" decision here always traces back to the same real flag readiness_
engine reported, never a separately-invented safety check.

planned_load_modifier_percent / planned_volume_modifier_percent are set
from the decision band (AR001-004), expressed as a percent adjustment to
whatever main.py/load_prescription_engine already prescribed for the
session — this module does NOT recompute the load itself, it just says
how much to nudge it.

Never raises. Missing readiness data (no check-in yet) returns
session_decision="proceed" with modifiers at 0 — the safe default: absence
of a bad signal isn't itself a bad signal, same philosophy readiness_
engine.py uses for its own missing-check-in fallback.
"""

from __future__ import annotations


def _default_result(member_id: str | None, note: str) -> dict:
    return {
        "autoregulation_profile_id": f"AUTO_{member_id or 'UNKNOWN'}",
        "athlete_id": member_id,
        "readiness_score": None,
        "fatigue_score": None,
        "recovery_score": None,
        "planned_load_modifier_percent": 0.0,
        "planned_volume_modifier_percent": 0.0,
        "session_decision": "proceed",
        "note": note,
    }


def evaluate_session(
    member_id: str | None,
    readiness_profile: dict | None = None,
    recovery_capacity_profile: dict | None = None,
) -> dict:
    """
    readiness_profile is Engine 9's output for the SPECIFIC session being
    evaluated (has a real check-in, or is the conservative default if not).
    recovery_capacity_profile is Engine 10's output for the current cycle —
    used here only for its fatigue_score, not its capacity_score (that
    already governs volume elsewhere, in recovery_capacity_engine.py and
    load_prescription_engine.py's LP002).
    """
    if not member_id:
        return _default_result(member_id, "No member_id provided.")

    readiness_score = (readiness_profile or {}).get("readiness_score")
    fatigue_score = (recovery_capacity_profile or {}).get("recovery_inputs", {}).get("fatigue_score")
    pain_flag = bool((readiness_profile or {}).get("pain_flag", False))
    illness_flag = bool((readiness_profile or {}).get("illness_flag", False))

    # AR005 — safety flag overrides everything else.
    if pain_flag or illness_flag:
        return {
            "autoregulation_profile_id": f"AUTO_{member_id}",
            "athlete_id": member_id,
            "readiness_score": readiness_score,
            "fatigue_score": fatigue_score,
            "recovery_score": None,
            "planned_load_modifier_percent": -100.0,
            "planned_volume_modifier_percent": -100.0,
            "session_decision": "cancel",
            "note": "Pain or illness flagged — cancel or substitute this session.",
        }

    if readiness_score is None:
        return _default_result(member_id, "No readiness check-in on file for this session — proceeding as planned.")

    low_fatigue = fatigue_score is not None and fatigue_score < 30

    if readiness_score >= 85 and low_fatigue:
        # AR001
        decision = "modified"
        load_mod, vol_mod = 3.5, 0.0
        note = "High readiness, low fatigue — small load increase warranted."
    elif readiness_score >= 60:
        # AR002
        decision = "proceed"
        load_mod, vol_mod = 0.0, 0.0
        note = None
    elif readiness_score >= 40:
        # AR003
        decision = "modified"
        load_mod, vol_mod = -12.5, -15.0
        note = "Moderate readiness — reducing load and volume for this session."
    else:
        # AR004
        decision = "deload"
        load_mod, vol_mod = -30.0, -40.0
        note = "Low readiness — replacing with a lighter recovery-oriented session."

    return {
        "autoregulation_profile_id": f"AUTO_{member_id}",
        "athlete_id": member_id,
        "readiness_score": readiness_score,
        "fatigue_score": fatigue_score,
        "recovery_score": None,
        "planned_load_modifier_percent": load_mod,
        "planned_volume_modifier_percent": vol_mod,
        "session_decision": decision,
        "note": note,
    }
