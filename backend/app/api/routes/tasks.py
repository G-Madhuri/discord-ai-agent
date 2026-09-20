from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import DbSession, resolve_scoped_server, verify_internal_token
from app.models.enums import TaskStatus
from app.schemas.common import ServerContext
from app.schemas.task import DependencyCheck, TaskCreate, TaskRead
from app.services import project_service, task_service

router = APIRouter(prefix="/tasks", tags=["tasks"], dependencies=[Depends(verify_internal_token)])


@router.post("", response_model=TaskRead, status_code=201)
async def create_task(payload: TaskCreate, session: DbSession) -> TaskRead:
    server = await resolve_scoped_server(session, payload.context)
    project = await project_service.require_project(session, server.id, payload.project_key)
    task = await task_service.create_task(session, server.id, project, payload)
    return await task_service.to_task_read(session, task, project.key)


@router.post("/list", response_model=list[TaskRead])
async def list_tasks(
    context: ServerContext,
    session: DbSession,
    project_key: str | None = None,
    status: TaskStatus | None = None,
    unassigned_only: bool = False,
) -> list[TaskRead]:
    server = await resolve_scoped_server(session, context)
    project = (
        await project_service.require_project(session, server.id, project_key)
        if project_key
        else await project_service.resolve_default_project(
            session, server.id, discord_channel_id=context.discord_channel_id
        )
    )
    tasks = await task_service.list_tasks(
        session, server.id, project.id, status=status, unassigned_only=unassigned_only
    )
    return [await task_service.to_task_read(session, t, project.key) for t in tasks]


@router.post("/{task_key}", response_model=TaskRead)
async def get_task(
    task_key: str, context: ServerContext, session: DbSession, project_key: str | None = None
) -> TaskRead:
    from app.models.project import Project

    server = await resolve_scoped_server(session, context)
    project_id = None
    if project_key:
        project = await project_service.require_project(session, server.id, project_key)
        project_id = project.id

    task = await task_service.require_task(session, server.id, task_key, project_id=project_id)
    owning_project = await session.get(Project, task.project_id)
    return await task_service.to_task_read(
        session, task, owning_project.key if owning_project else ""
    )


@router.post("/{task_key}/dependencies", response_model=DependencyCheck)
async def check_dependencies(
    task_key: str, context: ServerContext, session: DbSession, project_key: str | None = None
) -> DependencyCheck:
    server = await resolve_scoped_server(session, context)
    project_id = None
    if project_key:
        project = await project_service.require_project(session, server.id, project_key)
        project_id = project.id
    task = await task_service.require_task(session, server.id, task_key, project_id=project_id)
    return await task_service.check_dependencies(session, task)
