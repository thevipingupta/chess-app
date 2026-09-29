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
    # Coach Mode — local-only narration via Ollama; silently unavailable if unset
    ollama_base_url: str = ""            # e.g. http://localhost:11434
    ollama_model:    str = "llama3.1"    # any Ollama text model you have pulled

    model_config = {"env_file": str(_ENV_FILE)}

    @field_validator("database_url", mode="before")
    @classmethod
    def normalise_postgres_url(cls, v: str) -> str:
        """Render/Railway provide postgres:// or bare postgresql://. Pin the
        driver to psycopg2 explicitly — newer SQLAlchemy versions resolve a
        driver-less postgresql:// URL to the psycopg (v3) dialect by default,
        which isn't installed here, causing a ModuleNotFoundError at startup."""
        if not isinstance(v, str):
            return v
        if v.startswith("postgres://"):
            v = v.replace("postgres://", "postgresql://", 1)
        if v.startswith("postgresql://"):
            v = v.replace("postgresql://", "postgresql+psycopg2://", 1)
        return v


settings = Settings()
