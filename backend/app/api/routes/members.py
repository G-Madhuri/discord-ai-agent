from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import DbSession, resolve_scoped_server, verify_internal_token
from app.schemas.common import ServerContext
from app.schemas.member import MemberCreate, MemberRead, MemberSkillsUpdate, MemberUpdate
from app.services import member_service

router = APIRouter(
    prefix="/members", tags=["members"], dependencies=[Depends(verify_internal_token)]
)


@router.post("", response_model=MemberRead, status_code=201)
async def upsert_member(payload: MemberCreate, session: DbSession) -> MemberRead:
    server = await resolve_scoped_server(session, payload.context, create=True)
    member = await member_service.create_or_update_member(session, server.id, payload)
    return member_service.to_member_read(member)


@router.post("/list", response_model=list[MemberRead])
async def list_members(context: ServerContext, session: DbSession) -> list[MemberRead]:
    server = await resolve_scoped_server(session, context)
    members = await member_service.list_members(session, server.id)
    return [member_service.to_member_read(m) for m in members]


@router.post("/{discord_user_id}/profile", response_model=MemberRead)
async def member_profile(
    discord_user_id: str, context: ServerContext, session: DbSession
) -> MemberRead:
    server = await resolve_scoped_server(session, context)
    member = await member_service.require_member(session, server.id, discord_user_id)
    return member_service.to_member_read(member)


@router.patch("/{discord_user_id}", response_model=MemberRead)
async def update_member(
    discord_user_id: str, payload: MemberUpdate, context: ServerContext, session: DbSession
) -> MemberRead:
    server = await resolve_scoped_server(session, context)
    member = await member_service.require_member(session, server.id, discord_user_id)
    await member_service.update_member(session, member, payload)
    return member_service.to_member_read(member)


@router.post("/{discord_user_id}/skills", response_model=MemberRead)
async def set_skills(
    discord_user_id: str,
    payload: MemberSkillsUpdate,
    context: ServerContext,
    session: DbSession,
) -> MemberRead:
    server = await resolve_scoped_server(session, context)
    member = await member_service.require_member(session, server.id, discord_user_id)
    await member_service.set_member_skills(session, member, payload.skills, replace=payload.replace)
    return member_service.to_member_read(member)
