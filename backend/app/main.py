from __future__ import annotations

from app import _boot  # noqa: F401

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.api.errors import register_exception_handlers
from app.api.router import api_router
from app.api.routes import discord, health
from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.db.session import dispose_engine

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    logger.info(
        "starting %s v%s (env=%s, agent_mode=%s, vector_store=%s, embeddings=%s)",
        settings.app_name,
        __version__,
        settings.environment,
        settings.agent_mode,
        settings.vector_store,
        settings.embedding_provider,
    )
    logger.info(
        "ENV | project=%s location=%s vertexai=%s",
        settings.google_cloud_project,
        settings.google_cloud_location,
        settings.google_genai_use_vertexai,
    )
    # No database connection is opened here on purpose: /health must answer
    # even when PostgreSQL is down. Use /readyz to check the database.
    yield
    await dispose_engine()
    logger.info("shutdown complete")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Discord AI Assignment Agent",
        version=__version__,
        summary="Assigns existing tasks to the best-suited member of a Discord server.",
        lifespan=lifespan,
    )
    register_exception_handlers(app)
    app.include_router(health.router)
    app.include_router(discord.router)
    app.include_router(api_router, prefix=settings.api_prefix)
    return app


app = create_app()
