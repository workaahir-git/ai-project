import asyncio
import traceback
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, Depends, HTTPException, Request, Form
from fastapi.responses import JSONResponse, HTMLResponse
from starlette.middleware.base import BaseHTTPMiddleware
from app.config import settings
from app.auth import get_current_user
from app.membership import get_or_join_member
from app.db import supabase
from app.ollama_client import generate_with_ollama
from app.schemas import GenerateRequest, GenerateResponse
from app.fitness_generator import (
    SYSTEM_PROMPT,
    build_user_prompt,
    parse_llm_json,
    enforce_schema,
    render_dashboard,
    apply_deterministic_day_labels,
    build_deterministic_workout_days,
    ACTIVITY_LABEL,
)
from app.safety_engine import (
    safety_gate,
    emergency_block_html,
    DEFAULT_SAFE_SEQUENCE,
    DEFAULT_SAFE_VOL,
)


class ManualCORS(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS":
            return JSONResponse(
                content={},
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Methods": "*",
                    "Access-Control-Allow-Headers": "*",
                    "Access-Control-Max-Age": "86400",
                },
            )
        response = await call_next(request)
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "*"
        response.headers["Access-Control-Allow-Headers"] = "*"
        return response


app = FastAPI(title="Prompt Generator API")
app.add_middleware(ManualCORS)

# ── In-flight request dedup ─────────────────────────────────────────────────
# Keyed by user id. If the same user's browser fires a second /result request
# while their first one is still running (a frontend retry-on-timeout, or an
# impatient double-click on "Generate My Plan"), we attach to the SAME Gemini
# call instead of starting a new one. This is what actually fixes "requests
# counted that we never manually triggered" — the retry/double-click no longer
# turns into a second billed Gemini request.
_inflight_results: dict[str, asyncio.Task] = {}

print("Gemini key loaded:", bool(settings.gemini_api_key))
print(
    "Gemini key prefix:",
    settings.gemini_api_key[:5] if settings.gemini_api_key else "NONE"
)


@app.get("/health")
def health():
    return {"status": "ok"}


# ── Existing JSON API (kept intact) ─────────────────────────────────────────
@app.post("/api/generate", response_model=GenerateResponse)
async def generate(
    body: GenerateRequest,
    user: dict = Depends(get_current_user),
):
    try:
        result = await generate_with_ollama(body.prompt, body.system)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return GenerateResponse(result=result)


@app.get("/api/me")
def whoami(user: dict = Depends(get_current_user)):
    return {"sub": user.get("sub"), "email": user.get("email")}


def _get_active_plan(member_id: str) -> dict | None:
    res = (
        supabase.table("plans")
        .select("*")
        .eq("member_id", member_id)
        .eq("status", "active")
        .gt("valid_until", datetime.now(timezone.utc).isoformat())
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else None


@app.post("/api/reset-plan")
def reset_plan(
    user: dict = Depends(get_current_user),
    member: dict = Depends(get_or_join_member),
):
    """Dev/testing helper: expires the caller's own active plan row(s) so the
    NEXT /result call is forced to regenerate from scratch (fresh Jinja2
    render off whatever is currently in Templates/result.html) instead of
    serving the cached rendered_html from Supabase.

    Scoped to the authenticated caller's own member_id only — this cannot be
    used to reset anyone else's plan. Safe to call as many times as you like;
    if there's nothing active it's just a no-op.
    """
    res = (
        supabase.table("plans")
        .update({"status": "expired"})
        .eq("member_id", member["id"])
        .eq("status", "active")
        .execute()
    )
    cleared = len(res.data) if res.data else 0
    return {"cleared": cleared, "member_id": member["id"]}


@app.get("/api/my-plan")

def my_plan(
    user: dict = Depends(get_current_user),
    member: dict = Depends(get_or_join_member),
):
    """Called by the frontend right after login. If an active plan already
    exists, the frontend redirects straight to it instead of showing the
    generate-plan form again."""
    plan = _get_active_plan(member["id"])
    if plan:
        return {"has_plan": True, "html": plan["rendered_html"]}
    return {"has_plan": False}


# ── New: form POST → fitness_generator → Jinja2 dashboard ───────────────────
@app.post("/result", response_class=HTMLResponse)
async def result_page(
    request: Request,
    user: dict = Depends(get_current_user),
    member: dict = Depends(get_or_join_member),
    # ── Basic bio ────────────────────────────────────────────────────────────
    name:       str = Form(""),
    age:        str = Form(""),
    gender:     str = Form("Male"),
    height:     str = Form(""),
    weight:     str = Form(""),
    target:     str = Form(""),
    # ── Goal & experience ────────────────────────────────────────────────────
    goal:       str = Form("Fat loss"),
    experience: str = Form("Intermediate"),   # Beginner / Intermediate / Advanced
    # ── Training preferences ─────────────────────────────────────────────────
    activity:   str = Form("moderate"),       # sedentary/light/moderate/very_active/extreme
    days:       str = Form("4"),              # training days per week
    duration:   str = Form("45-60 min"),
    equipment:  str = Form("full gym"),
    # ── Diet preferences ─────────────────────────────────────────────────────
    diet:       str = Form("Non-vegetarian"), # Non-vegetarian/Vegetarian/Vegan/Eggetarian
    meals:      str = Form("5"),              # meals per day
    region:     str = Form(""),
    budget:     str = Form("medium"),
    allergies:  str = Form("none"),
    # ── Health notes ─────────────────────────────────────────────────────────
    notes:      str = Form(""),
):
    # ── Lock check: an active, unexpired plan already exists → serve it,
    # zero LLM calls. This is what actually prevents API-limit burn on
    # repeat logins, independent of whatever the frontend does.
    existing = _get_active_plan(member["id"])
    if existing:
        return HTMLResponse(content=existing["rendered_html"])

    profile = {
        # Identity
        "name":               name or "User",
        "age":                age or "25",
        "gender":             gender,
        "height_cm":          height or "170",
        "current_weight_kg":  weight or "70",
        "target_weight_kg":   target or "—",
        # Goal & experience
        "goal":               goal,
        "experience":         experience,
        # Training
        "activity_key":       activity,           # raw key used for factor lookup
        "days_per_week":      days or "4",
        "session_duration":   duration,
        "equipment":          equipment or "full gym",
        # Diet — pass the RAW value so diet_token lookup can fuzzy-match it
        "diet_pref":          diet,
        "meals_per_day":      meals or "5",
        "region":             region or "India",
        "budget":             budget,
        "allergies":          allergies or "none",
        # Health
        "medical_notes":      notes or "none",
    }

    # ── SAFETY GATE (KB File 12) — runs unconditionally, before anything else.
    # This must be the very first thing that happens with the intake data:
    # no LLM call, no exercise selection, no DB write until this clears.
    gate = safety_gate(profile)
    print(f"[/result] safety_gate for user={user.get('sub')}: {gate}")

    if gate["action"] == "block":
        # Emergency symptom or under-13. Never generate a workout, never
        # call the LLM, never save a plan. Just the referral message.
        return HTMLResponse(content=emergency_block_html(gate["messages"]))

    user_prompt = build_user_prompt(profile)

    if gate["action"] == "default_template":
        # High-risk condition or acute/sharp pain disclosed, no clearance on
        # file. Override the computed split/volume with KB File 14's fixed
        # conservative template — applied AFTER build_user_prompt() so the
        # diet/macro text it generated is unaffected, but the actual workout
        # structure _run() reads below is now the safe template, not the
        # normal formula-driven one.
        profile["_weekly_template"] = DEFAULT_SAFE_SEQUENCE
        profile["_vol"] = DEFAULT_SAFE_VOL

    async def _run() -> tuple[dict, str]:
        """The actual Gemini call + parse + render, run at most once per user
        no matter how many overlapping /result requests come in for them.

        Workout content (exercise selection, sets/reps/rest, warmups) is no
        longer produced by the LLM at all — build_user_prompt() tells it to
        leave workout.weekly_schedule / workout.days as empty arrays, and
        build_deterministic_workout_days() fills them in Python using the
        curated exercise database. That removes the old retry-on-mismatch
        step entirely: there's nothing left in the LLM's own output to
        validate against the weekly template, since it never produces any.
        The LLM is still used for diet content, recovery copy, and macro
        numbers, which do get parsed from its response as before.
        """
        weekly_template = profile.get("_weekly_template", [])
        vol = profile.get("_vol", {})

        raw = await generate_with_ollama(user_prompt, system=SYSTEM_PROMPT)
        data = parse_llm_json(raw)

        data.setdefault("workout", {})
        data["workout"]["days"] = build_deterministic_workout_days(profile, weekly_template, vol)

        data = enforce_schema(data, profile)

        if weekly_template:
            data = apply_deterministic_day_labels(data, weekly_template)

        return data, render_dashboard(data)

    user_key = str(user.get("sub", "anonymous"))

    # If this user already has a generation in flight (frontend retry after a
    # client-side timeout, or a double-click on "Generate My Plan"), attach to
    # that SAME task instead of kicking off a second Gemini call.
    task = _inflight_results.get(user_key)
    if task is None or task.done():
        task = asyncio.create_task(_run())
        _inflight_results[user_key] = task

    try:
        data, html = await task
    except RuntimeError as e:
        print(f"[/result] LLM error for user={user_key}: {e}")
        traceback.print_exc()
        return HTMLResponse(
            content=(
                f"<h2 style='font-family:sans-serif;padding:40px'>⚠️ LLM error: {e}</h2>"
                f"<p style='padding:0 40px'><a href='javascript:history.back()'>← Go back</a></p>"
            ),
            status_code=503,
        )
    except Exception as e:
        print(f"[/result] Unhandled error for user={user_key}: {e}")
        traceback.print_exc()
        return HTMLResponse(
            content=(
                f"<pre style='font-family:monospace;padding:40px;white-space:pre-wrap'>"
                f"Parse error: {e}</pre>"
                f"<p style='padding:0 40px'><a href='javascript:history.back()'>← Go back</a></p>"
            ),
            status_code=500,
        )
    finally:
        # Only clear the slot if it's still pointing at OUR task — it may have
        # already been replaced by a newer request from the same user.
        if _inflight_results.get(user_key) is task and task.done():
            _inflight_results.pop(user_key, None)

    if gate["action"] == "default_template":
        # Make sure the client actually SEES why their plan is conservative
        # rather than this being a silent, unexplained downgrade.
        notes_html = "".join(f"<li>{m}</li>" for m in gate["messages"])
        banner = (
            "<div style='font-family:sans-serif;max-width:640px;margin:20px auto;"
            "padding:20px;border:2px solid #d99;border-radius:10px;background:#fffaf5;'>"
            "<strong>⚠️ You've been given a conservative starter template</strong>"
            f"<ul>{notes_html}</ul>"
            "<p>This isn't your full personalized plan — it's a safe, low-intensity "
            "default until the item(s) above are reviewed. Update your intake or "
            "check with a coach/doctor to unlock full programming.</p></div>"
        )
        html = banner + html

    # Save so this doesn't get regenerated on the member's next login —
    # the lock check at the top of this route reads from this table.
    supabase.table("plans").insert({
        "member_id": member["id"],
        "cycle_number": 1,
        "plan_json": data,
        "rendered_html": html,
        "status": "active",
        "valid_until": (datetime.now(timezone.utc) + timedelta(days=14)).isoformat(),
    }).execute()

    return HTMLResponse(content=html)
