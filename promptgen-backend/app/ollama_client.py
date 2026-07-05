from google import genai
from google.genai import types
from app.config import settings
import asyncio

GEMINI_MODEL = "gemini-3.1-flash-lite"

MAX_OUTPUT_TOKENS = 32768
GEMINI_TIMEOUT_SECONDS = 55

client = genai.Client(api_key=settings.gemini_api_key)


async def generate_with_ollama(prompt: str, system: str | None = None) -> str:
    try:
        full_prompt = f"{system}\n\n{prompt}" if system else prompt

        response = await asyncio.wait_for(
            client.aio.models.generate_content(
                model=GEMINI_MODEL,
                contents=full_prompt,
                config=types.GenerateContentConfig(
                    thinking_config=types.ThinkingConfig(thinking_level="low"),
                    response_mime_type="application/json",
                    max_output_tokens=MAX_OUTPUT_TOKENS,
                ),
            ),
            timeout=GEMINI_TIMEOUT_SECONDS,
        )
        print(f"[TOKEN USAGE] prompt={response.usage_metadata.prompt_token_count} "
      f"thinking={response.usage_metadata.thoughts_token_count} "
      f"output={response.usage_metadata.candidates_token_count} "
      f"total={response.usage_metadata.total_token_count}")

        return response.text or ""

    except asyncio.TimeoutError:
        raise RuntimeError(
            f"Gemini API timed out after {GEMINI_TIMEOUT_SECONDS}s — the "
            f"request was cancelled server-side, no partial call is left running."
        )
    except Exception as e:
        raise RuntimeError(f"Gemini API error: {e}")
