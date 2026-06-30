from google import genai
from app.config import settings

client = genai.Client(api_key=settings.gemini_api_key)

async def generate_with_ollama(prompt: str, system: str | None = None) -> str:
try:
full_prompt = prompt

    if system:
        full_prompt = f"{system}\n\n{prompt}"

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=full_prompt,
    )

    return response.text

except Exception as e:
    raise RuntimeError(f"Gemini API error: {str(e)}")