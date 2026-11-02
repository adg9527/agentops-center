from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AGENTOPS_", env_file=BACKEND_DIR / ".env", extra="ignore")

    data_dir: Path = PROJECT_DIR / "data"
    # Default: SQLite for local dev. PostgreSQL: postgresql+asyncpg://user:pass@localhost:5432/agentops
    database_url: str = ""
    jwt_expire_minutes: int = 60 * 12
    # Global cap on concurrently running agent processes
    max_global_concurrency: int = 4
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    @property
    def db_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite+aiosqlite:///{(self.data_dir / 'agentops.db').as_posix()}"

    @property
    def workspaces_dir(self) -> Path:
        return self.data_dir / "workspaces"

    @property
    def keys_dir(self) -> Path:
        return self.data_dir / "keys"


@lru_cache
def get_settings() -> Settings:
    return Settings()
