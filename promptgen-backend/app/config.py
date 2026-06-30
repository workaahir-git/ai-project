from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    supabase_url: str
    supabase_jwt_secret: str
    gemini_api_key: str
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1"
    frontend_origin: str = "http://127.0.0.1:5500,https://ai-project-login-44q4.vercel.app"

    @property
    def frontend_origins(self) -> list[str]:
        return [o.strip() for o in self.frontend_origin.split(",") if o.strip()]

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
