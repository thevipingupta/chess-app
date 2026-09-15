"""Application settings loaded from environment / .env file."""

from pathlib import Path
from pydantic import field_validator
from pydantic_settings import BaseSettings

# Always resolve .env relative to the project root (two levels up from this file)
_ENV_FILE = Path(__file__).parent.parent / ".env"


class Settings(BaseSettings):
    frontend_origin: str = "http://localhost:5173"
    secret_key: str
    access_token_expire_minutes: int = 480   # 8 hours
    database_url: str = "sqlite:///./chess.db"
    stockfish_path: str = "stockfish"
    # OCR / Vision — Ollama for local dev, Gemini Flash as Railway fallback
    ollama_base_url: str = ""       # e.g. http://localhost:11434  (local only)
    ollama_model:    str = "llava"  # any Ollama vision model you have pulled
    gemini_api_key:  str = ""       # Google AI Studio free key (works on Railway)

    model_config = {"env_file": str(_ENV_FILE)}

    @field_validator("database_url", mode="before")
    @classmethod
    def normalise_postgres_url(cls, v: str) -> str:
        """Render provides legacy postgres:// prefix; SQLAlchemy requires postgresql://."""
        if isinstance(v, str) and v.startswith("postgres://"):
            return v.replace("postgres://", "postgresql://", 1)
        return v


settings = Settings()
