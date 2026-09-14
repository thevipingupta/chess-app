"""Application settings loaded from environment / .env file."""

from pathlib import Path
from pydantic_settings import BaseSettings

# Always resolve .env relative to the project root (two levels up from this file)
_ENV_FILE = Path(__file__).parent.parent / ".env"


class Settings(BaseSettings):
    frontend_origin: str = "http://localhost:5173"
    secret_key: str
    access_token_expire_minutes: int = 480   # 8 hours
    database_url: str = "sqlite:///./chess.db"
    stockfish_path: str = "stockfish"

    model_config = {"env_file": str(_ENV_FILE)}


settings = Settings()
