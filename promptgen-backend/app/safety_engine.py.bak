"""
safety_engine.py
──────────────────────────────────────────────────────────────────────────────
Implements the safety-critical subset of knowledge_base_v5 that must run
BEFORE any workout is generated:

  • File 11 (Assessment & Intake Engine)      -> confidence tier calculation
  • File 12 (Safety & Medical Red-Flag Engine) -> safety_gate()
  • File 14 (Default Safe Template)            -> DEFAULT_SAFE_SEQUENCE/VOL

This is NOT the full 21-file rule engine translated line-for-line. It is the
smallest set of rules where a gap produces an UNSAFE plan rather than just a
wrong one. Everything here fails conservative: if a signal is ambiguous, the
less aggressive path is taken — never the more complete one.

⚠️ Per File 12 of the knowledge base: this clinical logic has NOT been
reviewed by a licensed physician/PT/CSCS. This is a first pass closing the
gap between the KB design and the running code — not a substitute for real
clinical review before this touches paying users, especially the pregnancy
and high-risk-condition paths below.

WHY THIS EXISTS AS ITS OWN MODULE
    Before this, the ONLY safety logic in the codebase was a substring match
    against 11 injury keywords, used solely to swap out individual exercises
    (exercise_database.py). There was no age gating, no high-risk-condition
    handling, no emergency-symptom check, and no concept of "we don't know
    enough about this client to program them aggressively" (File 11's
    confidence tier). This module is the missing front door: every request
    must pass through safety_gate() before generate_with_ollama() or
    build_deterministic_workout_days() are called.
"""

from __future__ import annotations


# ── EMERGENCY SYMPTOMS (File 12 "Emergency symptom list") ───────────────────
# If ANY of these appear, we NEVER generate a workout — no tier, no template,
# just an urgent, non-programmable message. This mirrors File 12's rule that
# emergency stops are never overridden or programmed around.
EMERGENCY_KEYWORDS = (
    "chest pain", "can't breathe", "cant breathe", "difficulty breathing",
    "severe headache", "worst headache", "fainted", "passed out",
    "numbness on one side", "slurred speech", "anaphylaxis",
    "allergic reaction", "suicidal", "chest tightness",
)

# ── HIGH-RISK CONDITIONS requiring clearance before full programming ───────
# (File 11 "High-risk condition list")
HIGH_RISK_KEYWORDS = (
    "heart condition", "heart disease", "cardiac", "pacemaker",
    "uncontrolled diabetes", "seizure", "epilepsy", "stroke",
    "recent surgery", "osteoporosis", "blood clot", "pulmonary",
    "eating disorder", "anorexia", "bulimia", "pregnant", "pregnancy",
)

# ── ACUTE / SHARP PAIN (File 12 "Pain triage decision tree") ───────────────
# Distinguished from ordinary DOMS/muscle soreness, which is left alone.
ACUTE_PAIN_KEYWORDS = (
    "sharp pain", "sudden pain", "just injured", "swelling",
    "can't bear weight", "cannot bear weight", "locked", "popped",
)

_IGNORABLE_NOTES = {"", "none", "n/a", "na", "no", "nil", "nothing"}


def _contains_any(text: str, keywords: tuple[str, ...]) -> list[str]:
    text = (text or "").lower()
    return [k for k in keywords if k in text]


def _parse_age(profile: dict) -> tuple[int, list[str]]:
    """Never let a bad age field crash generation — parse defensively and
    treat unparseable age as unknown (safer to assume nothing than to 500)."""
    raw = str(profile.get("age", "25")).strip()
    try:
        return int(raw), []
    except (ValueError, TypeError):
        return 25, ["Age could not be read from the form — treated as unknown."]


def compute_confidence_tier(profile: dict) -> tuple[str, list[str]]:
    """
    Returns (tier, reasons) per File 11's system-wide confidence tier:
      green  – no flags, full programming
      yellow – minor flags (teen, 65+, an unrecognized-but-disclosed note)
      orange – high-risk condition or acute/sharp pain disclosed, no
               clearance on file -> default safe template
      red    – emergency symptom or under-13 -> block generation entirely
    """
    notes = str(profile.get("medical_notes") or profile.get("notes") or "")
    age, reasons = _parse_age(profile)

    if age < 13:
        return "red", reasons + [
            "Client reports age under 13 — this product is not intended "
            "for children under 13 (KB File 11 edge case: under-13 rejection)."
        ]

    emergency_hits = _contains_any(notes, EMERGENCY_KEYWORDS)
    if emergency_hits:
        return "red", reasons + [
            f"Emergency symptom language detected in notes: {', '.join(emergency_hits)}. "
            f"Seek medical attention before training."
        ]

    high_risk_hits = _contains_any(notes, HIGH_RISK_KEYWORDS)
    acute_pain_hits = _contains_any(notes, ACUTE_PAIN_KEYWORDS)

    if high_risk_hits or acute_pain_hits:
        if high_risk_hits:
            reasons.append(
                f"High-risk condition disclosed: {', '.join(high_risk_hits)} — "
                f"medical clearance recommended before full-intensity programming."
            )
        if acute_pain_hits:
            reasons.append(
                f"Acute/sharp pain language detected: {', '.join(acute_pain_hits)} — "
                f"defaulting to a conservative template until this clears or is assessed."
            )
        return "orange", reasons

    if age >= 65:
        reasons.append("Client is 65+ — fall-risk and joint-loading caution applied.")
        return "yellow", reasons

    if 13 <= age <= 17:
        reasons.append("Client is a minor (13–17) — advanced/near-failure techniques disabled.")
        return "yellow", reasons

    if notes.strip().lower() not in _IGNORABLE_NOTES:
        reasons.append(
            "Client disclosed a health note that isn't in our recognized list — "
            "treated cautiously pending human review."
        )
        return "yellow", reasons

    return "green", reasons


def safety_gate(profile: dict) -> dict:
    """
    File 12's safetyGate() — the unconditional pre-hook. Call this BEFORE
    any LLM call or deterministic workout build.

    Returns:
      {"action": "block" | "default_template" | "proceed",
       "tier": "green"|"yellow"|"orange"|"red",
       "messages": [str, ...]}
    """
    tier, reasons = compute_confidence_tier(profile)

    if tier == "red":
        return {"action": "block", "tier": tier, "messages": reasons}
    if tier == "orange":
        return {"action": "default_template", "tier": tier, "messages": reasons}
    return {"action": "proceed", "tier": tier, "messages": reasons}


# ── DEFAULT SAFE TEMPLATE (File 14) ──────────────────────────────────────────
# Fixed, conservative fallback used whenever safety_gate() returns
# "default_template": 3x/week full body, low volume, capped intensity, no
# progression until re-screened. Matches File 14's spec directly.
DEFAULT_SAFE_SEQUENCE = ["full", "rest", "full", "rest", "full", "rest", "rest"]

DEFAULT_SAFE_VOL = {
    "exercises_per_day": "5",
    "compound_count": 1,
    "isolation_count": 2,
    "sets_per_exercise": "2",
    "rest_between_sets": "90 sec",
    "intensity_note": (
        "DEFAULT SAFE TEMPLATE (KB File 14) — fixed low-intensity full-body "
        "plan. RPE capped at 6, no advanced techniques, no unsupervised "
        "max-effort or overhead work, no progression until re-screened. "
        "This is applied automatically because the intake disclosed something "
        "that needs a closer look before full programming is safe."
    ),
}


def emergency_block_html(messages: list[str]) -> str:
    """Rendered directly by main.py when safety_gate() returns 'block'. Never
    generates a workout, never calls the LLM, never gets saved as a plan."""
    items = "".join(f"<li>{m}</li>" for m in messages)
    return f"""
    <div style="font-family:sans-serif;max-width:640px;margin:60px auto;padding:32px;
                border:2px solid #d33;border-radius:12px;background:#fff5f5;">
      <h2 style="color:#b00020;margin-top:0;">⚠️ We can't generate a workout right now</h2>
      <p>Something in your intake needs attention before we can safely put together
      a training plan:</p>
      <ul>{items}</ul>
      <p><strong>If this describes a medical emergency, please seek immediate
      medical attention or contact local emergency services.</strong> Otherwise,
      please check in with a doctor or qualified coach before continuing, then
      come back and update your intake notes.</p>
    </div>
    """
