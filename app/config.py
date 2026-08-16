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
    # The chatbot answers while someone waits, so it gets its own setting — a
    # faster model is worth more here than in the build-step generators.
    chat_model: str = "anthropic/claude-sonnet-4.5"
    llm_timeout_seconds: float = 90.0
    # Generous headroom over the worst case a reply can validate as: a 4000-char
    # "reply" plus a four-option "check" whose options and responses run to 600
    # chars each is ~8000 characters of JSON. Left unset, the provider's own
    # default applies instead — small enough that a full reply-plus-check can be
    # cut off mid-object, which leaves no closing brace for either JSON parser
    # to find and turns a normal-length answer into a 503.
    llm_max_tokens: int = 4000

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
