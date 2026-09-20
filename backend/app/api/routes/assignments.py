from __future__ import annotations

from fastapi import APIRouter, Depends

from app.agents.runner import run_assignment
from app.api.deps import DbSession, resolve_scoped_server, tool_context_from, verify_internal_token
from app.schemas.assignment import (
    AssignmentEvaluation,
    AssignmentHistoryEntry,
    AssignRequest,
)
from app.schemas.common import ServerContext
from app.services.assignment import service as assignment_service

router = APIRouter(
    prefix="/assignments", tags=["assignments"], dependencies=[Depends(verify_internal_token)]
)


@router.post("/assign")
async def assign(payload: AssignRequest, session: DbSession) -> dict:
    """Assign an existing task.

    Runs the agent in whichever mode is configured; both modes go through the
    same tools and persist the same evidence. This endpoint never plans.
    """
    server = await resolve_scoped_server(session, payload.context)
    # The agent tools open their own sessions, so commit the resolved server
    # before handing over.
    await session.commit()

    ctx = tool_context_from(server, payload.context, payload.project_key)
    reply = await run_assignment(
        ctx,
        payload.task_key,
        reassign=payload.reassign,
        member_discord_id=payload.member_discord_id,
        note=payload.note,
    )
    return {"ok": reply.ok, "mode": reply.mode, "message": reply.message, "data": reply.data}


@router.post("/evaluate", response_model=AssignmentEvaluation)
async def evaluate(payload: AssignRequest, session: DbSession) -> AssignmentEvaluation:
    """Dry run: rank candidates and return the evidence without assigning."""
    server = await resolve_scoped_server(session, payload.context)
    context = await assignment_service.load_context(
        session,
        server.id,
        payload.task_key,
        project_key=payload.project_key,
        discord_channel_id=payload.context.discord_channel_id,
    )
    return context.evaluation


@router.post("/history", response_model=list[AssignmentHistoryEntry])
async def history(
    context: ServerContext,
    session: DbSession,
    task_key: str | None = None,
    member_discord_id: str | None = None,
    limit: int = 20,
) -> list[AssignmentHistoryEntry]:
    server = await resolve_scoped_server(session, context)
    return await assignment_service.get_assignment_history(
        session,
        server.id,
        task_key=task_key,
        member_discord_id=member_discord_id,
        limit=limit,
    )
