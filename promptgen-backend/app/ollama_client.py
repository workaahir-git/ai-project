import httpx
from app.config import settings

GEMINI_MODEL = "gemini-2.5-flash"


async def generate_with_ollama(prompt: str, system: str | None = None) -> str:
    url =(
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent?key={settings.gemini_api_key}"
)

    body = {
        "contents": [
            {"role": "user", "parts": [{"text": prompt}]},
        ],
        "generationConfig": {
            # 0 was forcing fully greedy decoding, which tends to make
            # "3 distinct meal options" collapse into near-duplicates.
            # A small amount of temperature gives real variety while
            # staying consistent enough for structured JSON.
            "temperature": 0.4,
            # Without an explicit cap, Gemini applies its own default
            # max output tokens, which was very likely truncating
            # the larger plan (15 meal options + up to 9 exercises/day
            # x 7 days). gemini-2.5-flash supports a large output window;
            # 8000 comfortably covers the full schema with headroom.
            "maxOutputTokens": 8000,
            # Ask Gemini to enforce valid JSON output server-side, on
            # top of the manual cleanup already done in parse_llm_json.
            "responseMimeType": "application/json",
        },
    }

    if system:
        body["system_instruction"] = {"parts": [{"text": system}]}

    async with httpx.AsyncClient(timeout=180.0) as client:
        try:
            resp = await client.post(
                url,
                headers={"Content-Type": "application/json"},
                json=body,
            )
            resp.raise_for_status()
        except httpx.ConnectError:
            raise RuntimeError("Could not reach Gemini API. Check your network connection.")
        except httpx.TimeoutException:
            raise RuntimeError(
                "Gemini API timed out generating the plan. Try again — "
                "large detailed plans can occasionally take longer than expected."
            )
        except httpx.HTTPStatusError as e:
            raise RuntimeError(
                f"Gemini API returned an error ({e.response.status_code}): "
                f"{e.response.text[:300]}"
            )

        data = resp.json()

        candidates = data.get("candidates") or []
        if not candidates:
            # Most commonly happens when the prompt was blocked by safety
            # filters before any candidate was generated.
            feedback = data.get("promptFeedback")
            raise RuntimeError(
                f"Gemini API returned no candidates. Prompt feedback: {feedback}"
            )

        candidate = candidates[0]
        finish_reason = candidate.get("finishReason")

        parts = candidate.get("content", {}).get("parts", [])
        content = "".join(part.get("text", "") for part in parts)

        if finish_reason == "MAX_TOKENS":
            # The model hit maxOutputTokens before finishing the JSON object.
            # Surface this clearly instead of letting parse_llm_json fail
            # with a confusing "no JSON object found" error downstream.
            raise RuntimeError(
                "LLM response was cut off before completing (hit the token "
                "limit). The generated plan was too long for the current "
                "maxOutputTokens setting — try increasing it further or "
                "reducing the requested plan size (e.g. fewer training days "
                "or meals per day)."
            )

        if not content:
            raise RuntimeError(
                f"Gemini API returned an empty response (finishReason: {finish_reason})."
            )

        return content
