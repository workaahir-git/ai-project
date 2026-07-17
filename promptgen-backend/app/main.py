import asyncio
import traceback
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, Depends, HTTPException, Request, Form, Header, Body
from fastapi.responses import JSONResponse, HTMLResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware
from app.config import settings
from app.auth import issue_member_token
from app.membership import (
    get_current_member,
    find_member_by_login_code,
    _extract_gym_slug,
)
from app.gym_scope import resolve_gym_id, GymLookupError
from app.db import supabase
from app.ollama_client import generate_with_ollama
from app.schemas import (
    GenerateRequest, GenerateResponse, FeedbackSubmission, CheckinSubmission,
    SetFeedbackSubmission, ExerciseFeedbackSubmission,
    MemberLoginRequest,
)
from app.fitness_generator import (
    SYSTEM_PROMPT,
    build_user_prompt,
    parse_llm_json,
    enforce_schema,
    render_dashboard,
    apply_deterministic_day_labels,
    build_and_review_workout_days,
    build_deterministic_workout_days,
    ACTIVITY_LABEL,
)
from app.safety_engine import (
    safety_gate,
    emergency_block_html,
    DEFAULT_SAFE_SEQUENCE,
    DEFAULT_SAFE_VOL,
)
from app import checkin_engine
from app import progression_engine
from app import progression_context


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


# ── Multi-gym access-link skeleton ──────────────────────────────────────────
# The dev-console's link generator (admin-dashboard-backend + dev-console,
# separate repos) already produces links shaped like
# `{MEMBER_APP_BASE_URL}/member/login?gym={slug}`. Historically nothing on
# this side served that path — the real member login page is the static
# frontend (login.html, deployed on Vercel), not this FastAPI backend.
# This route is a thin, environment-portable bridge: it 302s straight to the
# real login page with `?gym=` preserved, so MEMBER_APP_BASE_URL can point at
# *this* backend's public URL without the dev-console needing to know the
# separate Vercel frontend's URL/filename. If/when the frontend gets a clean
# `/member/login` path of its own (see vercel.json rewrite added alongside
# this), MEMBER_APP_BASE_URL can just point at Vercel directly instead and
# this route becomes redundant, not broken — safe either way.
@app.get("/member/login")
def member_login_redirect(gym: str | None = None):
    if not settings.member_frontend_url:
        # Fail loudly rather than guess at frontend_origins[0] - that list
        # is CORS config, not guaranteed to be prod-URL-first (see
        # config.py). An unset member_frontend_url means this hasn't been
        # configured for the current environment yet.
        raise HTTPException(
            status_code=503,
            detail="MEMBER_FRONTEND_URL is not set on this deployment - "
                   "/member/login can't redirect anywhere yet.",
        )
    target = f"{settings.member_frontend_url.rstrip('/')}/member/login"
    if gym:
        target += f"?gym={gym}"
    return RedirectResponse(url=target, status_code=302)


# ── Member auth: login_code only ─────────────────────────────────────────────
# The identity anchor is the gym-issued 8-digit `login_code` (a code that
# doesn't match any row in this gym is "wrong code", never "create a new
# account" — there is no signup). There is no member password: the code
# alone is the credential, matching what the gym hands out.
#
# One endpoint, unauthenticated (no session token yet — that's the whole
# point):
#   POST /member/login - code only. Issues a session token immediately.
#
# Known gap: no rate limiting on this endpoint. The code is the only
# factor, so per-IP/per-gym attempt throttling is worth adding before this
# goes to real users at scale — left as a follow-up.
def _resolve_gym_or_404(request: Request, x_gym_slug: str | None, body_gym: str | None) -> str:
    slug = _extract_gym_slug(request, x_gym_slug) or body_gym
    try:
        return resolve_gym_id(slug)
    except GymLookupError as e:
        raise HTTPException(status_code=404, detail=str(e))


def _require_code(code: str | None) -> str:
    code = (code or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="Enter your login code.")
    return code


@app.post("/member/login")
def member_login(
    body: MemberLoginRequest,
    request: Request,
    x_gym_slug: str | None = Header(default=None, alias="X-Gym-Slug"),
):
    gym_id = _resolve_gym_or_404(request, x_gym_slug, body.gym)
    code = _require_code(body.code)

    member = find_member_by_login_code(gym_id, code)
    if not member:
        raise HTTPException(status_code=401, detail="Invalid code. Check with your gym and try again.")
    if member.get("status") != "active":
        raise HTTPException(status_code=403, detail="Your account isn't active. Contact your gym.")

    token = issue_member_token(member["id"], gym_id)
    return {
        "token": token,
        "member": {"id": member["id"], "name": member.get("name"), "gym_id": gym_id},
    }


# ── Dev-only raw engine test endpoint ───────────────────────────────────────
# Proxied to by the dev-console's app/routers/ai_testing.py
# (POST {MEMBER_APP_BASE_URL}/generate/test). Runs ONLY the deterministic
# core (split_engine.py / programming_rules.py via
# build_deterministic_workout_days) on a raw profile dict — no Supabase
# auth, no member join, no LLM call, no DB write. Lets the dev console
# sanity-check the deterministic engine output directly.
#
# Gated by a shared secret (DEV_TEST_KEY) rather than a JWT, since the
# caller is a *different backend*, not a logged-in browser. Unset key ->
# 503, matching this project's existing fail-loud-not-fabricated pattern
# (see ai_testing.py's own comment on the dev-console side). Set the SAME
# value for DEV_TEST_KEY here and MEMBER_APP_DEV_TEST_KEY (or however you
# name it) in the dev-console's .env once you wire this up for real.
@app.post("/generate/test")
def generate_test(
    profile: dict = Body(...),
    x_dev_test_key: str | None = Header(default=None, alias="X-Dev-Test-Key"),
):
    if not settings.dev_test_key:
        raise HTTPException(
            status_code=503,
            detail="DEV_TEST_KEY is not set on this deployment — /generate/test is disabled.",
        )
    if x_dev_test_key != settings.dev_test_key:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Dev-Test-Key.")

    gate = safety_gate(profile)
    if gate["action"] == "block":
        return {"safety_gate": gate, "days": None}

    weekly_template = DEFAULT_SAFE_SEQUENCE if gate["action"] == "default_template" else []
    vol = DEFAULT_SAFE_VOL if gate["action"] == "default_template" else {}

    days = build_deterministic_workout_days(profile, weekly_template, vol)
    return {"safety_gate": gate, "days": days}


# ── Existing JSON API (kept intact) ─────────────────────────────────────────
@app.post("/api/generate", response_model=GenerateResponse)
async def generate(
    body: GenerateRequest,
    member: dict = Depends(get_current_member),
):
    try:
        result = await generate_with_ollama(body.prompt, body.system)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return GenerateResponse(result=result)


@app.get("/api/me")
def whoami(member: dict = Depends(get_current_member)):
    return {"id": member.get("id"), "name": member.get("name"), "gym_id": member.get("gym_id")}


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


# ── Phase 6: Biweekly Reassessment & Adaptive Progression ──────────────────
# checkin_engine.py / progression_engine.py are fully deterministic (no LLM
# calls) and operate independently of workout generation — see their own
# module docstrings. Nothing below this point changes how /result builds a
# plan; it only collects check-in data and stores a progression decision
# for a future generation pass to read, per this phase's scope.
def _get_latest_reassessment(member_id: str) -> dict | None:
    res = (
        supabase.table("reassessments")
        .select("*")
        .eq("member_id", member_id)
        .order("cycle_number", desc=True)
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else None


def _get_latest_plan_any_status(member_id: str) -> dict | None:
    """Most recent plan row regardless of active/expired status — used to
    find the current cycle's start date for the 14-day eligibility check.
    Unlike _get_active_plan(), this intentionally ignores valid_until so
    eligibility can still be computed right after a plan has expired."""
    res = (
        supabase.table("plans")
        .select("*")
        .eq("member_id", member_id)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else None


REASSESSMENT_INTERVAL_DAYS = 14
# TEMP TEST OVERRIDE: firing the check-in after 5 minutes instead of 14 days
# so the flow can be verified quickly. Remove/revert this block to go back
# to the real 14-day cadence.
REASSESSMENT_INTERVAL_MINUTES_TEST = 5


@app.get("/api/checkin/eligibility")
def checkin_eligibility(
    member: dict = Depends(get_current_member),
):
    """Tells the frontend whether the Biweekly Progress check-in should be
    shown yet. Eligible once REASSESSMENT_INTERVAL_DAYS have passed since
    the current cycle's plan was created AND no check-in has already been
    submitted for that cycle."""
    plan = _get_latest_plan_any_status(member["id"])
    if not plan:
        return {
            "eligible": False, "already_submitted": False,
            "cycle_number": 1, "next_eligible_at": None,
        }

    cycle_number = plan["cycle_number"]
    created_at = datetime.fromisoformat(plan["created_at"].replace("Z", "+00:00"))
    eligible_at = created_at + timedelta(minutes=REASSESSMENT_INTERVAL_MINUTES_TEST)
    now = datetime.now(timezone.utc)

    history = checkin_engine.get_reassessment_history(member["id"], limit=1)
    already_submitted = bool(history) and history[0]["cycle_number"] == cycle_number

    return {
        "eligible": now >= eligible_at and not already_submitted,
        "already_submitted": already_submitted,
        "cycle_number": cycle_number,
        "next_eligible_at": eligible_at.isoformat(),
    }


@app.post("/api/checkin")
def submit_checkin(
    body: CheckinSubmission,
    member: dict = Depends(get_current_member),
):
    """End-of-cycle biweekly reassessment. Runs the fully deterministic
    checkin_engine -> progression_engine pipeline (no LLM involved in the
    decision) and stores the result for a future /result generation to
    read. Expires the member's current active plan so their next login
    starts a new cycle — the generation logic itself is untouched and
    still ignores this data, per this phase's scope.
    """
    try:
        inputs = checkin_engine.assemble_reassessment_inputs(
            member["id"], body.model_dump(),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    plan = _get_active_plan(member["id"]) or _get_latest_plan_any_status(member["id"])
    goal = ((plan or {}).get("plan_json") or {}).get("plan", {}).get("goal_label", "")

    result = progression_engine.compute(inputs, goal=goal)

    supabase.table("reassessments").insert({
        "member_id":       member["id"],
        "cycle_number":    result["cycle_number"],
        "checkin_id":      inputs["checkin"]["id"],
        "progress_state":  result["progress_state"],
        "compliance_pct":  result["compliance_pct"],
        "is_deload":       result["is_deload"],
        "plateau_counter": result["plateau_counter"],
        "adaptations":     result["adaptations"],
    }).execute()

    # Force the NEXT /result call to regenerate instead of serving the
    # now-stale cached plan, starting the next cycle.
    supabase.table("plans").update({"status": "expired"}).eq(
        "member_id", member["id"]
    ).eq("status", "active").execute()

    return {
        "progress_state":  result["progress_state"],
        "compliance_pct":  result["compliance_pct"],
        "is_deload":       result["is_deload"],
        "plateau_counter": result["plateau_counter"],
        "actions":         result["adaptations"]["actions"],
    }


@app.post("/api/reset-plan")
def reset_plan(
    member: dict = Depends(get_current_member),
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


@app.post("/api/submit-feedback")
def submit_feedback(
    body: FeedbackSubmission,
    member: dict = Depends(get_current_member),
):
    """Stores the weight-per-set and difficulty-star ratings the member
    entered at the end of their training week. Each row is one set of one
    exercise on one day. This is the raw signal a future generation pass
    can read to auto-progress load / adjust volume for the NEXT plan —
    that read side isn't wired up yet (see fitness_generator.py), this
    endpoint just captures and persists the data reliably first.
    """
    rows = [
        {
            "member_id": member["id"],
            "day_index": e.day_index,
            "day_name": e.day_name,
            "exercise": e.exercise,
            "set_number": e.set_number,
            "weight_kg": e.weight_kg,
            "difficulty_rating": e.difficulty,
        }
        for e in body.entries
    ]
    if rows:
        supabase.table("plan_feedback").insert(rows).execute()
    return {"saved": len(rows)}


@app.get("/api/my-plan")

def my_plan(
    member: dict = Depends(get_current_member),
):
    """Called by the frontend right after login. If an active plan already
    exists, the frontend redirects straight to it instead of showing the
    generate-plan form again."""
    plan = _get_active_plan(member["id"])
    if plan:
        return {"has_plan": True, "html": plan["rendered_html"]}
    return {"has_plan": False}


# ── Workout set/exercise feedback (backs the progress tracker on the plan
# page — see Templates/result.html). Replaces direct browser->Supabase
# writes that relied on Supabase Auth + RLS, which no longer exist for
# members (see auth.py). Scoped to member["id"] from the verified session
# token; the frontend never sends a member_id, so there's no way to read or
# write anyone else's rows through this endpoint.
@app.get("/api/workout-feedback")
def get_workout_feedback(
    cycle: int = 1,
    member: dict = Depends(get_current_member),
):
    sets = (
        supabase.table("workout_set_feedback")
        .select("day_index, exercise, set_number, weight_kg, reps_used")
        .eq("member_id", member["id"])
        .eq("cycle_number", cycle)
        .execute()
    )
    exercises = (
        supabase.table("workout_exercise_feedback")
        .select("day_index, exercise, difficulty, notes")
        .eq("member_id", member["id"])
        .eq("cycle_number", cycle)
        .execute()
    )
    return {"sets": sets.data or [], "exercises": exercises.data or []}


@app.post("/api/workout-feedback/sets")
def submit_set_feedback(
    body: SetFeedbackSubmission,
    member: dict = Depends(get_current_member),
):
    if not body.entries:
        return {"saved": 0}
    rows = [{**e.model_dump(), "member_id": member["id"]} for e in body.entries]
    supabase.table("workout_set_feedback").upsert(
        rows, on_conflict="member_id,cycle_number,day_index,exercise,set_number",
    ).execute()
    return {"saved": len(rows)}


@app.post("/api/workout-feedback/exercises")
def submit_exercise_feedback(
    body: ExerciseFeedbackSubmission,
    member: dict = Depends(get_current_member),
):
    if not body.entries:
        return {"saved": 0}
    rows = [{**e.model_dump(), "member_id": member["id"]} for e in body.entries]
    supabase.table("workout_exercise_feedback").upsert(
        rows, on_conflict="member_id,cycle_number,day_index,exercise",
    ).execute()
    return {"saved": len(rows)}


# ── New: form POST → fitness_generator → Jinja2 dashboard ───────────────────
@app.post("/result", response_class=HTMLResponse)
async def result_page(
    request: Request,
    member: dict = Depends(get_current_member),
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

    # Phase 6: cycle_number must actually increment for checkin_engine's
    # cycle-scoped reads (get_current_cycle_number, reassessment history,
    # the reassessments table's per-cycle unique constraint) to mean
    # anything. Computed once, up front, so it can be (a) stamped onto
    # `data` for the template/frontend to tag feedback rows with, and (b)
    # reused as-is on the plans insert below.
    prev_plan = _get_latest_plan_any_status(member["id"])
    next_cycle = (prev_plan["cycle_number"] + 1) if prev_plan else 1

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

    # Final Backend Integration: load the latest adaptive-progression
    # decision (if any) so THIS generation can consume it, closing the
    # loop described in the integration spec. Purely additive, same
    # profile["_xxx"] convention as _weekly_template/_vol below.
    # load_latest_progression_context() returns None (never raises) when
    # there's no reassessment yet or the optional read fails, in which
    # case build_deterministic_workout_days() generates exactly as before.
    profile["_progression_context"] = progression_context.load_latest_progression_context(
        member["id"], goal=goal,
    )

    # ── SAFETY GATE (KB File 12) — runs unconditionally, before anything else.
    # This must be the very first thing that happens with the intake data:
    # no LLM call, no exercise selection, no DB write until this clears.
    gate = safety_gate(profile)
    print(f"[/result] safety_gate for member={member.get('id')}: {gate}")

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
        build_and_review_workout_days() fills them in Python using the
        curated exercise database, then runs Trainer Review (a second,
        bounded Gemini call, whitelist-checked by review_validation.py —
        never able to introduce an exercise outside the deterministic
        candidates) before returning. If that second call fails, the
        deterministic days are returned unchanged; a Trainer Review outage
        never blocks plan delivery. That removes the old retry-on-mismatch
        step entirely: there's nothing left in the LLM's own workout output
        to validate against the weekly template, since it never produces
        any. The LLM is still used for diet content, recovery copy, and
        macro numbers, which do get parsed from its response as before.
        """
        weekly_template = profile.get("_weekly_template", [])
        vol = profile.get("_vol", {})

        raw = await generate_with_ollama(user_prompt, system=SYSTEM_PROMPT)
        data = parse_llm_json(raw)

        data.setdefault("workout", {})
        reviewed = await build_and_review_workout_days(
            profile, weekly_template, vol, generate_with_ollama,
        )
        data["workout"]["days"] = reviewed["days"]
        profile["_trainer_review"] = reviewed["trainer_review"]

        data = enforce_schema(data, profile)
        data["cycle_number"] = next_cycle

        if weekly_template:
            data = apply_deterministic_day_labels(data, weekly_template)

        return data, render_dashboard(data)

    user_key = str(member.get("id", "anonymous"))

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
        "cycle_number": next_cycle,
        "plan_json": data,
        "rendered_html": html,
        "status": "active",
        "valid_until": (datetime.now(timezone.utc) + timedelta(days=14)).isoformat(),
    }).execute()

    return HTMLResponse(content=html)
