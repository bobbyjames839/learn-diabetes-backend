from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Postgres (Supabase).
    database_url: str

    # Supabase project — used to verify the JWTs the frontend sends.
    supabase_url: str = ""
    # Legacy HS256 projects only. Newer projects sign with a key from the JWKS
    # endpoint and need nothing here.
    supabase_jwt_secret: str = ""

    # Checkpoint question generation. Without a key the app still runs — lessons
    # simply read as before, with no checkpoints.
    openrouter_api_key: str = ""
    question_model: str = "anthropic/claude-sonnet-4.5"
    llm_timeout_seconds: float = 90.0

    cors_origins: str = "http://localhost:5173"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def jwks_url(self) -> str:
        return f"{self.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"


@lru_cache
def get_settings() -> Settings:
    return Settings()
