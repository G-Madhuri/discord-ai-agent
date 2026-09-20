from __future__ import annotations

from fastapi import APIRouter

from app.core.config import settings
from app.db.session import check_database
from app.schemas.common import HealthResponse, ReadinessResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Liveness probe. Deliberately does not touch the database, so a DB
    outage does not make Cloud Run recycle healthy instances."""
    return HealthResponse(status="ok")


@router.get("/readyz", response_model=ReadinessResponse)
async def readiness() -> ReadinessResponse:
    """Readiness probe: reports whether the database is actually reachable."""
    db_ok = await check_database()
    return ReadinessResponse(
        status="ok" if db_ok else "degraded",
        database="connected" if db_ok else "unavailable",
        environment=settings.environment,
        agent_mode=settings.agent_mode,
    )
