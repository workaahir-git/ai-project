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
    MemberLoginRequest, ReadinessCheckinSubmission,
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
    build_deterministic_plan_data,
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
from app import adherence_engine
from app import plateau_engine
from app import readiness_engine
from app import recovery_capacity_engine
from app import progression_context
from app import goal_optimization_engine
from app import periodization_engine
from app import volume_allocation_engine
from app import programming_engine
from app import weak_point_engine
from app import coaching_explanation_engine
from app import progression_regression_engine
from app import fatigue_management_engine
from app import autoregulation_engine
from app import adaptation_tracking_engine
from app import predictive_progression_engine
from app import diet_phase_engine
from app import diet_engine
from app import exercise_selection_engine
from app import analytics_engine
from app import feedback_engine
from app import intra_cycle_adaptation_engine
from app import decision_audit_engine
from app import monitoring_engine
from app import deployment_engine
from app import kb_versioning_engine
from app import orchestration_engine
from app import governance_engine
from app import continuous_improvement_engine
from app import research_integration_engine
from app import load_prescription_engine
from app import warmup_ramp_engine
from app.exercise_database import get_full_exercise_profile
# Sessions 7-8: biomechanics/validation (built KB-sourced, previously
# missing entirely) + the 5 previously data-only KB engines (movement,
# joint stress, recovery, skill, tempo) that had raw data but no decision
# logic wrapper. See HANDOFF.md sessions 7-8 for what each does and does
# not do.
from app import biomechanics_engine
from app import validation_engine
from app import movement_engine
from app import joint_stress_engine
from app import recovery_engine
from app import skill_engine
from app import tempo_engine


class ManualCORS(BaseHTTPMiddleware):
    # Was hardcoded to "*" regardless of `settings.frontend_origins` (parsed
    # from the FRONTEND_ORIGIN env var in config.py) — that allow-list
    # existed but nothing ever consulted it, so this accepted requests from
    # any origin. Auth here is Bearer-token (see membership.py's
    # HTTPBearer), not cookies, so this was never a credential-theft CORS
    # hole — but it still ignored a real, already-configured restriction
    # for no reason. Now: echo back the request's Origin only if it's in
    # the configured allow-list; otherwise omit the CORS headers entirely
    # (browser blocks the cross-origin response, same effective result as
    # before for anything actually in FRONTEND_ORIGIN, but no longer wide
    # open to arbitrary origins).
    async def dispatch(self, request: Request, call_next):
        origin = request.headers.get("origin")
        allowed_origin = origin if origin in settings.frontend_origins else None

        if request.method == "OPTIONS":
            headers = {
                "Access-Control-Allow-Methods": "*",
                "Access-Control-Allow-Headers": "*",
                "Access-Control-Max-Age": "86400",
            }
            if allowed_origin:
                headers["Access-Control-Allow-Origin"] = allowed_origin
            return JSONResponse(content={}, headers=headers)

        response = await call_next(request)
        if allowed_origin:
            response.headers["Access-Control-Allow-Origin"] = allowed_origin
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
from app import configuration_engine
REASSESSMENT_INTERVAL_MINUTES_TEST = configuration_engine.get_config("reassessment_interval_minutes")


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


# ── Adherence (Engine 15) — read-only, additive. Surfaces attendance/
# completion for the member's CURRENT active cycle: get_adherence_profile's
# cycle_number param means "the cycle about to be generated", reading
# cycle_number - 1 internally, so we pass active_plan.cycle_number + 1 to
# make it read the active plan's own cycle rather than the one before it.
# No active plan -> adherence_engine's own conservative default (score=None).
@app.get("/api/adherence")
def get_adherence(
    member: dict = Depends(get_current_member),
):
    plan = _get_active_plan(member["id"])
    cycle_number = (plan["cycle_number"] + 1) if plan else None
    return adherence_engine.get_adherence_profile(member["id"], cycle_number)


# ── Plateau (Engine 11) — read-only, additive, same pattern/risk profile as
# /api/adherence above. Per-exercise, so the frontend must supply which
# exercise it's asking about (it already has exercise name / id from the
# rendered plan — see Templates/result.html's data-exercise attributes).
# readiness_profile / recovery_capacity_profile intentionally left None:
# no caller in this app computes those same-session profiles yet (Engines
# 9/10 exist but nothing feeds them here), so PL003/PL004 gates are simply
# skipped rather than fed a guessed value — this is plateau_engine.py's own
# documented fallback, not something invented for this endpoint.
@app.get("/api/plateau")
def get_plateau(
    exercise_id: str,
    exercise_name: str,
    movement_id: str | None = None,
    day_index: int | None = None,
    member: dict = Depends(get_current_member),
):
    plan = _get_active_plan(member["id"])
    cycle_number = (plan["cycle_number"] + 1) if plan else None

    readiness_profile = None
    recovery_capacity_profile = None
    if day_index is not None and plan:
        readiness_profile = readiness_engine.get_readiness(
            member["id"], plan["cycle_number"], day_index,
        )
        intake = (plan.get("plan_json") or {}).get("_intake") or {}
        recovery_capacity_profile = recovery_capacity_engine.build_recovery_capacity(
            member["id"], profile=intake, cycle_number=cycle_number,
            readiness_profile=readiness_profile,
        )

    return plateau_engine.detect_plateau(
        member["id"], exercise_id, exercise_name,
        movement_id=movement_id, cycle_number=cycle_number,
        readiness_profile=readiness_profile,
        recovery_capacity_profile=recovery_capacity_profile,
    )


# ── Readiness (Engine 9) — first write path for readiness_checkins.
# readiness_engine.py's own docstring has referenced sql/add_readiness_
# checkins.sql since it was written, but the table never actually existed
# until this session (see that file's header comment) — get_readiness()
# has been silently returning its no-data default on every call. This POST
# is the missing other half; GET is additive, same pattern as
# /api/adherence and /api/plateau above.
@app.post("/api/readiness")
def submit_readiness(
    body: ReadinessCheckinSubmission,
    member: dict = Depends(get_current_member),
):
    row = {**body.model_dump(), "member_id": member["id"]}
    supabase.table("readiness_checkins").upsert(
        row, on_conflict="member_id,cycle_number,day_index",
    ).execute()
    return {"ok": True}


@app.get("/api/readiness")
def get_readiness_checkin(
    day_index: int,
    cycle: int | None = None,
    member: dict = Depends(get_current_member),
):
    if cycle is None:
        plan = _get_active_plan(member["id"])
        cycle = plan["cycle_number"] if plan else None
    return readiness_engine.get_readiness(member["id"], cycle, day_index)


@app.get("/api/recovery-capacity")
def get_recovery_capacity(
    day_index: int | None = None,
    member: dict = Depends(get_current_member),
):
    plan = _get_active_plan(member["id"])
    cycle_number = (plan["cycle_number"] + 1) if plan else None
    intake = ((plan or {}).get("plan_json") or {}).get("_intake") or {}
    readiness_profile = None
    if day_index is not None and plan:
        readiness_profile = readiness_engine.get_readiness(
            member["id"], plan["cycle_number"], day_index,
        )
    return recovery_capacity_engine.build_recovery_capacity(
        member["id"], profile=intake, cycle_number=cycle_number,
        readiness_profile=readiness_profile,
    )


# ── Sessions 7-8 engines (biomechanics, validation, movement, joint stress,
# recovery, skill, tempo) — all read-only, additive, same pattern/risk
# profile as /api/adherence etc. above. All 7 are pure lookups keyed by
# exercise_id/movement_id (no plan/cycle context needed, unlike plateau/
# readiness above), so these endpoints are simpler: no _get_active_plan
# call, just member auth + a direct engine call. member is still required
# so these can't be hit unauthenticated, even though the response doesn't
# depend on which member is asking.

@app.get("/api/biomechanics")
def get_biomechanics_profile(
    exercise_id: str,
    member: dict = Depends(get_current_member),
):
    return {
        "profile": biomechanics_engine.get_profile(exercise_id),
        "rationale": biomechanics_engine.get_rationale(exercise_id),
    }


@app.get("/api/joint-stress")
def get_joint_stress_profile(
    exercise_id: str,
    member: dict = Depends(get_current_member),
):
    return joint_stress_engine.get_profile(exercise_id)


@app.get("/api/recovery-schedule")
def get_recovery_schedule(
    movement_id: str,
    hours_since_last_trained: float,
    member: dict = Depends(get_current_member),
):
    # Named /api/recovery-schedule, not /api/recovery, to stay unambiguous
    # next to /api/recovery-capacity above — Engine 6 (per-movement
    # spacing) vs Engine 10 (today's overall capacity), see
    # recovery_engine.py's module docstring for why these are deliberately
    # different questions with potentially different answers.
    result = recovery_engine.get_recovery_status(movement_id, hours_since_last_trained)
    if result is None:
        raise HTTPException(status_code=404, detail=f"No recovery profile for movement_id '{movement_id}'")
    return result


@app.get("/api/skill")
def get_skill_profile(
    exercise_id: str,
    client_experience: str | None = None,
    member: dict = Depends(get_current_member),
):
    return {
        "profile": skill_engine.get_profile(exercise_id),
        "exceeds_client_skill": (
            skill_engine.exceeds_client_skill(exercise_id, client_experience)
            if client_experience else None
        ),
        "top_coaching_cue": skill_engine.top_coaching_cue(exercise_id),
    }


@app.get("/api/tempo")
def get_tempo_profile(
    exercise_id: str,
    member: dict = Depends(get_current_member),
):
    return {
        "profile": tempo_engine.get_profile(exercise_id),
        "instruction": tempo_engine.get_tempo_instruction(exercise_id),
    }


# ── exercise_selection_engine (Engine 20) — read-only reporting layer,
# same pattern as biomechanics/skill/tempo above. goal is required since
# selection_score/candidate ranking are goal-relative; day_used_fallback
# and day_injury_keywords are optional context from that day's actual
# generation (frontend has these from the rendered plan already, same as
# plateau's exercise_id/exercise_name convention).
@app.get("/api/exercise-selection")
def get_exercise_selection_profile(
    exercise_id: str,
    goal: str,
    day_used_fallback: bool = False,
    day_injury_keywords: str | None = None,
    member: dict = Depends(get_current_member),
):
    keywords = [k.strip() for k in day_injury_keywords.split(",")] if day_injury_keywords else None
    return exercise_selection_engine.build_selection_profile(
        exercise_id, goal, day_used_fallback=day_used_fallback, day_injury_keywords=keywords,
    )


# ── analytics_engine (Engine 42) — real historical rollup across this
# member's completed cycles, not synthetic. periods assembled from two
# real tables: `plans.plan_json` (sets_prescribed — summed straight from
# each cycle's actual prescription, the same ex["sets"] value every day
# already renders) and `workout_set_feedback` (sets_logged — one row per
# logged set, same table /api/workout-feedback/sets already writes to).
# Oldest-first per build_analytics_record()'s own contract. No new engine
# logic here — analytics_engine performs no DB reads itself by design
# (see its own docstring), this endpoint is exactly the caller it expects.
@app.get("/api/analytics")
def get_analytics(member: dict = Depends(get_current_member)):
    plans_res = (
        supabase.table("plans")
        .select("cycle_number, plan_json")
        .eq("member_id", member["id"])
        .order("cycle_number")
        .execute()
    )
    plans = plans_res.data or []

    periods = []
    for plan in plans:
        days = ((plan.get("plan_json") or {}).get("workout") or {}).get("days", [])
        sets_prescribed = sum(
            ex.get("sets", 0)
            for day in days
            for ex in (day.get("exercises") or [])
        )
        fb_res = (
            supabase.table("workout_set_feedback")
            .select("member_id", count="exact")
            .eq("member_id", member["id"])
            .eq("cycle_number", plan["cycle_number"])
            .execute()
        )
        sets_logged = fb_res.count or 0
        periods.append({"sets_prescribed": sets_prescribed, "sets_logged": sets_logged})

    return analytics_engine.build_analytics_record(periods)


# ── Engines 28/31/32/33/34/35 — real infra visibility, not fitness logic.
# No member-auth dependency: these describe the SYSTEM, not a member's
# data, and are meant for whoever operates this app, not the app's users.
# Deliberately unauthenticated for now (same as this app's other internal
# tooling) — add real admin auth before exposing these outside a trusted
# network, same caveat monitoring_engine.py's own docstring states about
# not being a substitute for real observability infra.
@app.get("/api/admin/health")
def get_deployment_health():
    return deployment_engine.validate_environment()


@app.get("/api/admin/metrics")
def get_monitoring_metrics():
    return monitoring_engine.get_metrics()


@app.get("/api/admin/kb-version")
def get_kb_version():
    return kb_versioning_engine.get_kb_version_info()


@app.get("/api/admin/orchestration")
def get_orchestration_order():
    return {
        "declared_order": orchestration_engine.get_declared_order(),
        "validation": orchestration_engine.validate_dependencies(),
    }


@app.get("/api/admin/configuration")
def get_configuration():
    return configuration_engine.get_full_config()


@app.get("/api/admin/governance")
def get_governance_evaluation():
    return governance_engine.evaluate_release()


@app.post("/api/admin/improvement-proposals")
def create_improvement_proposal(body: dict):
    return continuous_improvement_engine.propose_improvement(
        body["source"], body["affected_engines"], body["improvement_type"],
        body["evidence_level"], body["description"],
    )


@app.post("/api/admin/research-integration")
def create_research_submission(body: dict):
    return research_integration_engine.submit_research(
        body["publication_id"], body["source_type"], body["evidence_grade"],
        body["affected_engines"], body["reviewer"],
    )


@app.get("/api/movement-audit")
def get_movement_audit(
    member: dict = Depends(get_current_member),
):
    # Diagnostic endpoint, not client-facing — flags if any exercise in
    # EXERCISE_DB has drifted onto a movement_id the KB doesn't recognize
    # (which would silently blind every other movement-keyed engine to
    # it too). Kept behind member auth like everything else here rather
    # than left fully open, even though it's not member-specific data.
    return movement_engine.audit_app_movement_ids()


@app.post("/api/validate-intake")
def validate_intake_payload(
    body: dict = Body(...),
    member: dict = Depends(get_current_member),
):
    # Takes the raw pre-default form fields (age/height/weight/goal/
    # experience/activity_key/equipment/diet_pref etc.) — see
    # validation_engine.py's own DV001 docstring note on why this only
    # means something if called BEFORE main.py's /result endpoint applies
    # its own Form(...) defaults. Not wired into /result itself this
    # session (that's a deliberate edit to already-shipped intake handling,
    # left for a dedicated pass rather than bundled in here) — this
    # endpoint exists so the frontend CAN call it standalone in the
    # meantime, e.g. as a pre-submit check before the form actually posts.
    return validation_engine.validate_intake(body)


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


def _apply_intra_cycle_adaptation(
    member_id: str, day_index: int, exercise_name: str, classification: str,
) -> dict | None:
    """
    After ONE exercise's feedback is classified, find the NEXT occurrence
    of the same training-day type (same `_token`, later day_index) still
    in the member's currently active plan, and patch the corresponding
    exercise in it — same list position as the exercise just given
    feedback, since both days were built from the same `_compute_day_plan`
    slot template (compound/compound/isolation/... in the same order) for
    that token, so position IS the correct correspondence, not a guess.

    Returns a small summary dict for the response, or None if there was
    nothing to patch (no later same-token day this cycle, exercise not
    found, or no active plan at all — e.g. this was the LAST occurrence of
    that day-type this cycle, which is a normal, expected case).

    Deliberately fails soft: called from inside a try/except at the call
    site so one adaptation failure never blocks the feedback save itself,
    which is the operation that actually matters most here.
    """
    plan = _get_active_plan(member_id)
    if not plan:
        return None
    data = plan.get("plan_json") or {}
    days = (data.get("workout") or {}).get("days") or []
    if day_index >= len(days):
        return None
    origin_day = days[day_index]
    origin_token = origin_day.get("_token")
    if not origin_token:
        return None

    origin_exercises = origin_day.get("exercises") or []
    position = next(
        (i for i, ex in enumerate(origin_exercises) if ex.get("name") == exercise_name), None,
    )
    if position is None:
        return None
    exercise_id = origin_exercises[position].get("exercise_id")

    target_day_index = next(
        (
            i for i in range(day_index + 1, len(days))
            if days[i].get("_token") == origin_token and not days[i].get("is_rest")
        ),
        None,
    )
    if target_day_index is None:
        return None
    target_exercises = days[target_day_index].get("exercises") or []
    if position >= len(target_exercises):
        return None

    decision = intra_cycle_adaptation_engine.decide_exercise_adaptation(exercise_id, classification)
    target_entry = target_exercises[position]

    if decision["action"] == "substitute":
        new_id = decision["new_exercise_id"]
        new_profile = (get_full_exercise_profile(new_id) or {}).get("metadata") or {}
        if not new_profile:
            return None
        target_entry["name"] = new_profile.get("display_name", new_id)
        target_entry["exercise_id"] = new_id
        target_entry["muscle"] = ", ".join(new_profile.get("primary_muscles") or []).title() or target_entry.get("muscle")
        # Fresh exercise for this member — no carried-over weight to base
        # a load off, same "no_last_weight" default compute_final_load
        # itself falls back to. Not guessed.
        load_profile = load_prescription_engine.compute_final_load(
            {"action": "baseline", "last_weight_kg": None}, new_id, target_entry["name"], data.get("_intake", {}).get("goal", ""),
        )
        target_entry.pop("_load_action", None)
        target_entry.pop("_load_note", None)
    else:
        # Hold: carry the just-logged weight forward as this occurrence's
        # starting point, read from the actual set-feedback rows already
        # saved for the origin day (real data, not fabricated) — the most
        # recent set logged for this exercise this cycle.
        sf = (
            supabase.table("workout_set_feedback")
            .select("weight_kg")
            .eq("member_id", member_id)
            .eq("day_index", day_index)
            .eq("exercise", exercise_name)
            .order("set_number", desc=True)
            .limit(1)
            .execute()
        )
        last_weight = sf.data[0]["weight_kg"] if sf.data else None
        adj = {"action": "progress" if classification == "too_easy" else "baseline", "last_weight_kg": last_weight}
        load_profile = load_prescription_engine.compute_final_load(
            adj, exercise_id, target_entry["name"], data.get("_intake", {}).get("goal", ""),
        )

    if load_profile is not None:
        target_entry["_final_load_kg"] = load_profile["final_load"]
        target_entry["_load_basis"] = load_profile["basis"]
        working_weight = load_profile["final_load"]
    else:
        working_weight = None
    target_entry["_warmup_ramp"] = warmup_ramp_engine.build_warmup_ramp(target_entry, working_weight)
    target_entry["_intra_cycle_adaptation"] = decision["reason"]
    if decision.get("requires_attention"):
        target_entry["_requires_attention"] = True

    # Persist the patch AND re-render, so the member's next page load
    # (not just a raw API poll) actually reflects it — plan_json IS the
    # exact `data` dict render_dashboard() already knows how to render,
    # nothing special-cased here.
    html = render_dashboard(data)
    supabase.table("plans").update({"plan_json": data, "rendered_html": html}).eq(
        "member_id", member_id,
    ).eq("status", "active").execute()

    decision_audit_engine.record_decision(
        member_id, "intra_cycle_adaptation",
        source_engines=["intra_cycle_adaptation_engine", "load_prescription_engine", "warmup_ramp_engine"],
        input_data={"day_index": day_index, "exercise": exercise_name, "classification": classification},
        output_data=decision,
    )

    return {
        "day_index": target_day_index,
        "exercise": target_entry["name"],
        "action": decision["action"],
        "reason": decision["reason"],
        "requires_attention": decision.get("requires_attention", False),
    }


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
    # ── feedback_engine (Engine 43/FB) wired here: classify each entry
    # right after it's saved (pain-keyword scan + difficulty banding),
    # returned to the caller so the frontend can surface an immediate
    # "flagged as possible pain" notice without a second round-trip.
    # Pure classification of what was just submitted — no new DB read.
    classifications = [
        feedback_engine.classify_feedback(e.difficulty, e.notes, exercise_id=e.exercise)
        for e in body.entries
    ]
    # ── Intra-cycle adaptation: this same training day-type may recur
    # later in the SAME active 14-day plan (e.g. Push on day 1 and day 4
    # of a 6-day PPL split) — patch that later occurrence now, using this
    # feedback, rather than waiting for the next full plan generation.
    # Best-effort per entry: one failure never blocks the feedback save
    # that already succeeded above.
    adaptations = []
    for entry, cls in zip(body.entries, classifications):
        try:
            result = _apply_intra_cycle_adaptation(
                member["id"], entry.day_index, entry.exercise, cls["classification"],
            )
            if result:
                adaptations.append(result)
        except Exception:
            monitoring_engine.record_error("intra_cycle_adaptation")
            continue
    return {"saved": len(rows), "classifications": classifications, "adaptations": adaptations}


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
    profile["_member_id"] = member["id"]
    profile["_cycle_number"] = next_cycle

    # ── SAFETY GATE (KB File 12) — runs unconditionally, before anything else.
    # This must be the very first thing that happens with the intake data:
    # no LLM call, no exercise selection, no DB write until this clears.
    gate = safety_gate(profile)
    print(f"[/result] safety_gate for member={member.get('id')}: {gate}")

    if gate["action"] == "block":
        # Emergency symptom or under-13. Never generate a workout, never
        # call the LLM, never save a plan. Just the referral message.
        return HTMLResponse(content=emergency_block_html(gate["messages"]))

    # NOTE: build_user_prompt()'s returned text is no longer consumed by
    # anything — the first Gemini call it used to feed is gone (see
    # _run()'s docstring below). Still called for its side effects, which
    # ARE still load-bearing: it sets profile["_weekly_template"],
    # profile["_vol"], profile["activity_level_factor"], and
    # profile["_parsed_allergies"], all read later in this function and
    # inside _run(). Not removed just because its return value is unused.
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
        """Workout content (exercise selection, sets/reps/rest, warmups) is
        produced in Python — build_and_review_workout_days() fills them in
        using the curated exercise database, then runs Trainer Review (a
        second, bounded Gemini call, whitelist-checked by
        review_validation.py — never able to introduce an exercise outside
        the deterministic candidates) before returning. If that second
        call fails, the deterministic days are returned unchanged; a
        Trainer Review outage never blocks plan delivery.

        The FIRST Gemini call (diet content, recovery copy, macro numbers)
        is REMOVED as of this session — build_deterministic_plan_data()
        replaces it entirely with real Python (diet_engine.build_diet_meals
        + recovery_tips_engine + the macro math that was already being
        computed in Python and just handed to the LLM to echo back). See
        that function's own docstring for the full reasoning. Trainer
        Review (the second call) is unchanged and still runs.
        """
        weekly_template = profile.get("_weekly_template", [])
        vol = profile.get("_vol", {})

        with monitoring_engine.track("plan_generation"):
            data = build_deterministic_plan_data(profile)

        data.setdefault("workout", {})
        reviewed = await build_and_review_workout_days(
            profile, weekly_template, vol, generate_with_ollama,
        )
        data["workout"]["days"] = reviewed["days"]
        profile["_trainer_review"] = reviewed["trainer_review"]

        # ── Session 13 wiring: final synthesis layer (Engine 19).
        # programming_engine.build_program() was built assuming callers for
        # goal_optimization/periodization/volume_allocation exist — none did.
        # Compute all three here, reusing the SAME functions the existing
        # /api/recovery-capacity, /api/plateau, /api/adherence endpoints
        # already call (not re-implemented), then call build_program().
        member_id = member["id"]
        cycle_number = next_cycle

        data["volume_allocation"] = volume_allocation_engine.build_volume_allocation(
            data["workout"]["days"], profile.get("experience"),
        )

        recovery_capacity_profile = recovery_capacity_engine.build_recovery_capacity(
            member_id, profile=profile, cycle_number=cycle_number, readiness_profile=None,
        )
        adherence_profile = adherence_engine.get_adherence_profile(member_id, cycle_number)

        # Per-exercise plateau check — one detect_plateau() call per unique
        # exercise in the plan, same dedup key build_program itself uses.
        # cycle_number-gated: returns "none" until >=2 completed cycles
        # exist, same fallback the /api/plateau endpoint already relies on.
        seen_ex = {}
        for day in data["workout"]["days"]:
            for ex in day.get("exercises", []) or []:
                eid = ex.get("_exercise_id") or ex.get("exercise_id") or ex.get("name")
                if eid and eid not in seen_ex:
                    seen_ex[eid] = ex.get("name", eid)
        plateau_flags = [
            plateau_engine.detect_plateau(
                member_id, eid, ename, cycle_number=cycle_number,
                recovery_capacity_profile=recovery_capacity_profile,
            )
            for eid, ename in seen_ex.items()
        ]
        plateau_confirmed = any(p.get("plateau_status") == "confirmed" for p in plateau_flags)

        goal_optimization = goal_optimization_engine.build_goal_profile(
            member_id, profile=profile, plateau_confirmed=plateau_confirmed,
            recovery_capacity_profile=recovery_capacity_profile,
        )
        periodization = periodization_engine.build_periodization_profile(
            member_id, goal_optimization["primary_goal"],
            recovery_capacity_profile=recovery_capacity_profile,
            adherence_profile=adherence_profile,
            plateau_confirmed=plateau_confirmed,
        )

        data["program"] = programming_engine.build_program(
            member_id, data, goal_optimization, periodization,
            recovery_capacity_profile=recovery_capacity_profile,
            plateau_flags=plateau_flags,
        )

        # ── weak_point_engine (WP004) + coaching_explanation_engine wired
        # as a pair: PG005 in programming_engine's own docstring flags that
        # weak-point output never reaches the member as anything but
        # coaching text — this is that text. detect_weak_points() needs
        # >=2 muscle groups' worth of logged feedback from the previous
        # cycle (cycle_number - 1), same convention as plateau/adherence
        # above; [] until then, same conservative-empty pattern.
        weak_points = weak_point_engine.detect_weak_points(member_id, cycle_number)
        data["explanations"] = coaching_explanation_engine.build_plan_explanations(
            data["workout"]["days"], weak_points,
        )

        # ── progression_regression_engine (Engine 3) — per-exercise
        # regress/hold/progress signal from rep-range history + pain
        # language, same dedup key (seen_ex) as the plateau loop above.
        # Attached additively per exercise so a future frontend pass has
        # somewhere to read it from (same "computed, not yet rendered"
        # pattern as _load_note/_final_load_kg/_warmup_ramp above).
        progression_regression_by_ex = {
            eid: progression_regression_engine.evaluate(member_id, eid, ename, cycle_number)
            for eid, ename in seen_ex.items()
        }
        for day in data["workout"]["days"]:
            for ex in day.get("exercises", []) or []:
                eid = ex.get("_exercise_id") or ex.get("exercise_id") or ex.get("name")
                pr = progression_regression_by_ex.get(eid)
                if pr:
                    ex["_progression_regression"] = pr

        # ── fatigue_management_engine (Engine 23) — systemic + local fatigue
        # for this cycle, reusing recovery_capacity_profile already computed
        # above (its fatigue_score) instead of re-deriving it.
        fatigue_profile = fatigue_management_engine.build_fatigue_profile(
            member_id, data["workout"]["days"], cycle_number, recovery_capacity_profile,
        )
        data["fatigue"] = fatigue_profile

        # ── autoregulation_engine (Engine 24) — session-level go/reduce/
        # cancel signal. readiness_profile is None at generation time (no
        # day_index / per-session check-in context here yet, same gap the
        # /api/plateau and /api/recovery-capacity endpoints already have
        # when called without day_index) — degrades to the engine's own
        # documented "no readiness check-in on file, proceeding as planned"
        # default rather than guessing.
        data["autoregulation"] = autoregulation_engine.evaluate_session(
            member_id, readiness_profile=None, recovery_capacity_profile=recovery_capacity_profile,
        )

        # ── adaptation_tracking_engine (Engine 26) — scoped to ONE real
        # domain this app can measure (strength), per the engine's own
        # SUPPORTED_DOMAINS guard. Reuses recovery_capacity/adherence/
        # plateau_confirmed already computed above.
        adaptation_profile = adaptation_tracking_engine.build_adaptation_profile(
            member_id, "strength", goal_optimization["primary_goal"], cycle_number,
            recovery_capacity_profile=recovery_capacity_profile,
            adherence_profile=adherence_profile,
            plateau_confirmed=plateau_confirmed,
        )
        data["adaptation"] = adaptation_profile

        # ── predictive_progression_engine (Engine 27) — projects strength
        # change over a fixed 4-week horizon from the adaptation/fatigue/
        # recovery/adherence profiles just computed, all reused not
        # re-derived. target_metric fixed to "strength_1rm_estimate" since
        # that's the one real logged proxy adaptation_tracking_engine
        # supports (same "strength" scoping as the block above).
        data["prediction"] = predictive_progression_engine.predict_progression(
            member_id, target_metric="strength_1rm_estimate", prediction_horizon_weeks=4,
            adaptation_profile=adaptation_profile,
            recovery_capacity_profile=recovery_capacity_profile,
            adherence_profile=adherence_profile,
            plateau_confirmed=plateau_confirmed,
            fatigue_profile=fatigue_profile,
        )

        # ── diet_phase_engine (Engine 39) — phase/kcal-adjustment recommend-
        # ation, computed here (not inside build_deterministic_plan_data)
        # because it needs recovery_capacity_score, which isn't available
        # until after the workout days exist. profile already carries every
        # other field it needs (current_weight_kg/height_cm/age/gender/goal/
        # experience/activity_level_factor — stamped earlier in this
        # request). Attached under data["diet"]["phase"] — additive key,
        # enforce_schema()'s diet.setdefault only fires when "diet" is
        # missing entirely, so this survives that call below untouched.
        data.setdefault("diet", {})["phase"] = diet_phase_engine.compute_diet_phase(
            profile,
            recovery_capacity_score=(recovery_capacity_profile or {}).get("capacity_score"),
            notes_raw=profile.get("medical_notes"),
            current_cycle=cycle_number,
        )

        # ── Close the loop: diet_phase's target_kcal/macro_split now
        # actually DRIVES the meals, not just informational metadata next
        # to them. build_deterministic_plan_data() already built an
        # initial diet.meals earlier in this request using the simpler
        # static goal-based formula (_calculate_macros) — that had to
        # happen first, before recovery_capacity_score existed. Rebuild
        # here with the real, recovery-aware number and overwrite both
        # data["diet"]["meals"] and every plan.* field a template reads
        # for kcal/protein, so the two never silently disagree with each
        # other again. Same diet_pref/allergy/budget parsing
        # build_deterministic_plan_data used — re-derived from profile's
        # raw fields (still in scope, unchanged this whole request), not
        # guessed.
        phase_data = data["diet"]["phase"]
        allergy_set = diet_engine.parse_allergies(profile.get("allergies", "none"))
        budget_tier = diet_engine.resolve_budget_tier(profile.get("budget", "medium"))
        protein_g = phase_data["macro_split"]["protein_g"]
        data["diet"]["meals"] = diet_engine.build_diet_meals(
            daily_kcal=phase_data["target_kcal"],
            daily_protein_g=protein_g,
            diet_pref_raw=profile.get("diet_pref", "non-vegetarian"),
            allergy_set=allergy_set,
            budget_tier=budget_tier,
        )
        data.setdefault("plan", {})["daily_calories"] = phase_data["target_kcal"]
        data["plan"]["daily_protein_g"] = protein_g
        data["plan"]["protein_range"] = f"{round(protein_g * 0.9)}–{round(protein_g * 1.1)}g"
        data["plan"]["calorie_phase"] = phase_data["phase"].replace("_", " ").title()

        data = enforce_schema(data, profile)
        data["cycle_number"] = next_cycle
        data["_intake"] = {"experience": profile.get("experience"), "age": profile.get("age")}

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
        "valid_until": (datetime.now(timezone.utc) + timedelta(days=configuration_engine.get_config("plan_validity_days"))).isoformat(),
    }).execute()

    # Engine 28 (Decision Audit): real record of this generation — input
    # is the intake profile actually used, output is the program/
    # explanations/diet.phase actually produced, source_engines is this
    # request's real DECLARED_ORDER (Engine 31) plus the always-on
    # deterministic generators that aren't in that list themselves.
    # Fail-soft (record_decision already try/excepts its own DB write) —
    # never allowed to block returning the plan that was just generated.
    decision_audit_engine.record_decision(
        member["id"], "plan_generation",
        source_engines=[e["engine"] for e in orchestration_engine.get_declared_order()],
        input_data=profile,
        output_data={"program": data.get("program"), "explanations": data.get("explanations"), "diet_phase": (data.get("diet") or {}).get("phase")},
    )

    return HTMLResponse(content=html)
