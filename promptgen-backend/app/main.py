from fastapi import FastAPI, Depends, HTTPException, Request, Form
from fastapi.responses import JSONResponse, HTMLResponse
from starlette.middleware.base import BaseHTTPMiddleware
from app.config import settings
from app.auth import get_current_user
from app.ollama_client import generate_with_ollama
from app.schemas import GenerateRequest, GenerateResponse
from app.fitness_generator import (
    SYSTEM_PROMPT,
    build_user_prompt,
    parse_llm_json,
    enforce_schema,
    render_dashboard,
    ACTIVITY_LABEL,
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


# ── New: form POST → fitness_generator → Jinja2 dashboard ───────────────────
@app.post("/result", response_class=HTMLResponse)
async def result_page(
    request: Request,
    user: dict = Depends(get_current_user),
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

    user_prompt = build_user_prompt(profile)

    try:
        raw = await generate_with_ollama(user_prompt, system=SYSTEM_PROMPT)
    except RuntimeError as e:
        return HTMLResponse(
            content=(
                f"<h2 style='font-family:sans-serif;padding:40px'>⚠️ LLM error: {e}</h2>"
                f"<p style='padding:0 40px'><a href='javascript:history.back()'>← Go back</a></p>"
            ),
            status_code=503,
        )

    try:
        data = parse_llm_json(raw)
        data = enforce_schema(data, profile)
        html = render_dashboard(data)
    except (ValueError, Exception) as e:
        return HTMLResponse(
            content=(
                f"<pre style='font-family:monospace;padding:40px;white-space:pre-wrap'>"
                f"Parse error: {e}\n\nRaw LLM output:\n{raw}</pre>"
                f"<p style='padding:0 40px'><a href='javascript:history.back()'>← Go back</a></p>"
            ),
            status_code=500,
        )

    return HTMLResponse(content=html)
