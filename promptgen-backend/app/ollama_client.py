from google import genai
from app.config import settings
import asyncio

GEMINI_MODEL = "gemini-2.5-flash"

client = genai.Client(api_key=settings.gemini_api_key)

async def generate_with_ollama(prompt: str, system: str | None = None) -> str:
    try:
        full_prompt = f"{system}\n\n{prompt}" if system else prompt

        response = await asyncio.to_thread(
            client.models.generate_content,
            model=GEMINI_MODEL,
            contents=full_prompt,
        )

        return response.text or ""

    except Exception as e:
        raise RuntimeError(f"Gemini API error: {e}")
