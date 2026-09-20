from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import DbSession, resolve_scoped_server, verify_internal_token
from app.schemas.common import ServerContext
from app.schemas.project import (
    DocumentCreate,
    DocumentRead,
    KnowledgeSearchResult,
    ProjectCreate,
    ProjectInfo,
    ProjectMemberAdd,
    ProjectRead,
)
from app.services import knowledge_service, member_service, project_service

router = APIRouter(
    prefix="/projects", tags=["projects"], dependencies=[Depends(verify_internal_token)]
)


@router.post("", response_model=ProjectRead, status_code=201)
async def create_project(payload: ProjectCreate, session: DbSession) -> ProjectRead:
    server = await resolve_scoped_server(session, payload.context, create=True)
    project = await project_service.create_project(session, server.id, payload)
    return ProjectRead.model_validate(project)


@router.post("/list", response_model=list[ProjectRead])
async def list_projects(context: ServerContext, session: DbSession) -> list[ProjectRead]:
    server = await resolve_scoped_server(session, context)
    projects = await project_service.list_projects(session, server.id)
    return [ProjectRead.model_validate(p) for p in projects]


@router.post("/{project_key}/info", response_model=ProjectInfo)
async def project_info(project_key: str, context: ServerContext, session: DbSession) -> ProjectInfo:
    server = await resolve_scoped_server(session, context)
    project = await project_service.require_project(session, server.id, project_key)
    return await project_service.get_project_info(session, project)


@router.post("/{project_key}/members", response_model=ProjectInfo, status_code=201)
async def add_member(
    project_key: str, payload: ProjectMemberAdd, context: ServerContext, session: DbSession
) -> ProjectInfo:
    server = await resolve_scoped_server(session, context)
    project = await project_service.require_project(session, server.id, project_key)
    member = await member_service.require_member(session, server.id, payload.discord_user_id)
    await project_service.add_project_member(
        session,
        project,
        member,
        project_role=payload.project_role,
        allocation_percent=payload.allocation_percent,
    )
    return await project_service.get_project_info(session, project)


@router.post("/knowledge", response_model=DocumentRead, status_code=201)
async def add_knowledge(payload: DocumentCreate, session: DbSession) -> DocumentRead:
    """Ingest unstructured project knowledge (requirements, architecture,
    meeting notes, captured Discord discussions)."""
    server = await resolve_scoped_server(session, payload.context)
    project = await project_service.require_project(session, server.id, payload.project_key)
    return await knowledge_service.ingest_document(session, project, payload)


@router.post("/{project_key}/knowledge/list", response_model=list[DocumentRead])
async def list_knowledge(
    project_key: str, context: ServerContext, session: DbSession
) -> list[DocumentRead]:
    server = await resolve_scoped_server(session, context)
    project = await project_service.require_project(session, server.id, project_key)
    return await knowledge_service.list_documents(session, server.id, project.id)


@router.post("/{project_key}/knowledge/search", response_model=KnowledgeSearchResult)
async def search_knowledge(
    project_key: str, query: str, context: ServerContext, session: DbSession, top_k: int = 5
) -> KnowledgeSearchResult:
    server = await resolve_scoped_server(session, context)
    project = await project_service.require_project(session, server.id, project_key)
    return await knowledge_service.search_knowledge_result(session, project, query, top_k=top_k)
