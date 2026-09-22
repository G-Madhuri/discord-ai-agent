from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends

from app.api.deps import verify_internal_token
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


@router.get("/debug/db-ping", dependencies=[Depends(verify_internal_token)])
async def debug_db_ping() -> dict[str, Any]:
    from sqlalchemy import text
    from app.db.session import session_scope

    try:
        t0 = time.time()
        async with session_scope() as session:
            res = await session.execute(text("SELECT 1"))
            val = res.scalar()
        elapsed = time.time() - t0
        return {"ok": True, "result": val, "latency_seconds": round(elapsed, 3)}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "error_type": type(exc).__name__}


@router.get("/debug/llm-ping", dependencies=[Depends(verify_internal_token)])
async def debug_llm_ping() -> dict[str, Any]:
    import asyncio
    import traceback

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(
            vertexai=settings.google_genai_use_vertexai,
            project=settings.google_cloud_project,
            location=settings.google_cloud_location,
        )

        async def _call():
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                None,
                lambda: client.models.generate_content(
                    model=settings.gemini_model,
                    contents='Return JSON: {"ok": true}',
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        temperature=0.0,
                    ),
                ),
            )

        resp = await asyncio.wait_for(_call(), timeout=15.0)
        raw_text = resp.text or ""
        return {"ok": True, "raw": raw_text, "model": settings.gemini_model}
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "error_type": type(exc).__name__,
            "traceback": traceback.format_exc(),
        }

