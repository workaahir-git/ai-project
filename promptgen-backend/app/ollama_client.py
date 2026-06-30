import httpx
from app.config import settings


async def generate_with_ollama(prompt: str, system: str | None = None) -> str:
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    async with httpx.AsyncClient(timeout=180.0) as client:
        try:
            resp = await client.post(
                "curl "https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent",
                headers={
                    "Authorization": f"Bearer {settings.groq_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "llama-3.3-70b-versatile",
                    "messages": messages,
                    # 0 was forcing fully greedy decoding, which tends to make
                    # "3 distinct meal options" collapse into near-duplicates.
                    # A small amount of temperature gives real variety while
                    # staying consistent enough for structured JSON.
                    "temperature": 0.4,
                    # Without an explicit cap, Groq applies its own default
                    # max_completion_tokens, which was very likely truncating
                    # the larger plan (15 meal options + up to 9 exercises/day
                    # x 7 days). llama-3.3-70b-versatile supports up to 32768
                    # output tokens on Groq; 8000 comfortably covers the full
                    # schema with headroom.
                    "max_tokens": 8000,
                    # Ask Groq to enforce valid JSON output server-side, on
                    # top of the manual cleanup already done in parse_llm_json.
                    "response_format": {"type": "json_object"},
                },
            )
            resp.raise_for_status()
        except httpx.ConnectError:
            raise RuntimeError("Could not reach Groq API. Check your API key.")
        except httpx.TimeoutException:
            raise RuntimeError(
                "Groq API timed out generating the plan. Try again — "
                "large detailed plans can occasionally take longer than expected."
            )
        except httpx.HTTPStatusError as e:
            raise RuntimeError(
                f"Groq API returned an error ({e.response.status_code}): "
                f"{e.response.text[:300]}"
            )

        data = resp.json()

        choice = data["choices"][0]
        finish_reason = choice.get("finish_reason")
        content = choice["message"]["content"]

        if finish_reason == "length":
            # The model hit max_tokens before finishing the JSON object.
            # Surface this clearly instead of letting parse_llm_json fail
            # with a confusing "no JSON object found" error downstream.
            raise RuntimeError(
                "LLM response was cut off before completing (hit the token "
                "limit). The generated plan was too long for the current "
                "max_tokens setting — try increasing max_tokens further or "
                "reducing the requested plan size (e.g. fewer training days "
                "or meals per day)."
            )

        return content