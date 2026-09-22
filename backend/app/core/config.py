"""Environment-driven application configuration.

Local development reads `.env` at the repository root. In production nothing is
read from disk: Cloud Run injects the same variable names from Google Cloud
Secret Manager.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_DIR.parent

AgentMode = Literal["deterministic", "llm"]
EmbeddingProviderName = Literal["hashing", "vertex"]
VectorStoreName = Literal["memory", "postgres", "pinecone"]


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

    # --- Application / Phase 5 Deployment ---
    app_name: str = "discord-assignment-agent"
    environment: Literal["local", "dev", "staging", "production"] = "local"
    env: str = Field(default="local", validation_alias=AliasChoices("ENV", "ENVIRONMENT"))
    log_level: str = "INFO"
    api_prefix: str = "/api/v1"
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # --- Database (Phase 2) ---
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/discord_agent"
    direct_url: str | None = None
    db_echo: bool = False
    db_pool_size: int = 5
    db_max_overflow: int = 5

    # --- Google Cloud / Vertex AI (Phase 3) ---
    google_cloud_project: str | None = None
    google_cloud_location: str = "us-central1"
    google_genai_use_vertexai: bool = True
    gemini_model: str = "gemini-2.5-flash"

    # --- Agent Mode ---
    agent_mode: AgentMode = "deterministic"

    # --- Discord (Phase 4) ---
    discord_bot_token: SecretStr | None = None
    discord_application_id: str | None = None
    discord_public_key: str | None = None
    discord_guild_id: str | None = None
    discord_dev_guild_id: str | None = None

    # --- Bot -> Backend ---
    backend_base_url: str = "http://localhost:8000"
    internal_api_token: SecretStr | None = None

    # --- pgvector + Hybrid RAG (Phase 6) ---
    rag_vector_store: VectorStoreName = "postgres"
    rag_retrieval_mode: str = "hybrid"
    rag_chunk_method: str = "semantic"
    rag_chunk_min_tokens: int = 100
    rag_chunk_max_tokens: int = 800
    rag_semantic_chunk_threshold: float | None = None
    rag_chunk_overlap_sentences: int = 1
    rag_semantic_top_k: int = 20
    rag_keyword_top_k: int = 20
    rag_final_top_k: int = 5
    rag_rrf_k: int = 60
    rag_embedding_model: str = "text-embedding-004"
    rag_chunk_size: int = 1200
    rag_chunk_overlap: int = 150

    # --- Legacy / Internal RAG defaults ---
    embedding_provider: EmbeddingProviderName = "hashing"
    embedding_model: str = "text-embedding-004"
    embedding_dimension: int = 768
    vector_store: VectorStoreName = "postgres"
    rag_top_k: int = 5

    # --- Assignment policy ---
    default_member_capacity: int = 3
    require_dependencies_resolved: bool = False
    assignment_weights: AssignmentWeights = Field(default_factory=AssignmentWeights)

    @field_validator("database_url", "direct_url")
    @classmethod
    def _require_async_driver(cls, value: str | None) -> str | None:
        if value and value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+asyncpg://", 1)
        return value

    @field_validator("rag_chunk_size", "rag_chunk_overlap", mode="before")
    @classmethod
    def _parse_empty_int(cls, value: Any, info) -> int:
        if value is None or value == "":
            if info.field_name == "rag_chunk_size":
                return 1200
            if info.field_name == "rag_chunk_overlap":
                return 150
        return int(value)

    @property
    def is_production(self) -> bool:
        return self.environment == "production" or self.env == "production"

    @property
    def sync_database_url(self) -> str:
        """Alembic runs migrations through the async engine, but tooling that
        needs a psycopg-style URL (e.g. `psql`, ad-hoc scripts) can use this."""
        return self.database_url.replace("+asyncpg", "")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
