"""Environment-driven application configuration.

Local development reads `.env` at the repository root. In production nothing is
read from disk: Cloud Run injects the same variable names from Google Cloud
Secret Manager.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_DIR.parent

AgentMode = Literal["deterministic", "llm"]
EmbeddingProviderName = Literal["hashing", "vertex"]
VectorStoreName = Literal["memory", "postgres"]


class AssignmentWeights(BaseSettings):
    """Relative weights of the scoring components of the assignment engine.

    Weights are normalised at scoring time, so the absolute values only matter
    relative to each other.
    """

    model_config = SettingsConfigDict(env_prefix="ASSIGNMENT_WEIGHT_", extra="ignore")

    skill: float = 0.45
    role: float = 0.15
    experience: float = 0.15
    knowledge: float = 0.10
    workload: float = 0.15


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Application ---
    app_name: str = "discord-assignment-agent"
    environment: Literal["local", "dev", "staging", "production"] = "local"
    log_level: str = "INFO"
    api_prefix: str = "/api/v1"

    # --- Database ---
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/discord_agent"
    db_echo: bool = False
    db_pool_size: int = 5
    db_max_overflow: int = 5

    # --- Google Cloud / Vertex AI ---
    google_cloud_project: str | None = None
    google_cloud_location: str = "us-central1"
    google_genai_use_vertexai: bool = True
    gemini_model: str = "gemini-2.5-flash"

    # --- Agent ---
    agent_mode: AgentMode = "deterministic"

    # --- Discord ---
    discord_bot_token: SecretStr | None = None
    discord_application_id: str | None = None
    discord_public_key: str | None = None
    discord_dev_guild_id: str | None = None

    # --- Bot -> Backend ---
    backend_base_url: str = "http://localhost:8000"
    internal_api_token: SecretStr | None = None

    # --- Knowledge / RAG ---
    embedding_provider: EmbeddingProviderName = "hashing"
    embedding_model: str = "text-embedding-004"
    embedding_dimension: int = 768
    vector_store: VectorStoreName = "postgres"
    rag_chunk_size: int = 1200
    rag_chunk_overlap: int = 150
    rag_top_k: int = 5

    # --- Assignment policy ---
    default_member_capacity: int = 3
    require_dependencies_resolved: bool = False
    assignment_weights: AssignmentWeights = Field(default_factory=AssignmentWeights)

    @field_validator("database_url")
    @classmethod
    def _require_async_driver(cls, value: str) -> str:
        if value and value.startswith("postgresql://"):
            # A sync URL silently breaks the async engine; fix it early instead.
            return value.replace("postgresql://", "postgresql+asyncpg://", 1)
        return value

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def sync_database_url(self) -> str:
        """Alembic runs migrations through the async engine, but tooling that
        needs a psycopg-style URL (e.g. `psql`, ad-hoc scripts) can use this."""
        return self.database_url.replace("+asyncpg", "")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
