from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, PlanImmutableError
from app.models.assignment import Assignment
from app.models.enums import AssignmentStatus, TaskStatus
from app.models.member import MemberProfile
from app.models.project import Project, ProjectDocument, ProjectMember
from app.models.task import Task
from app.schemas.project import (
    ProjectCreate,
    ProjectInfo,
    ProjectMemberRead,
    ProjectRead,
    ProjectUpdate,
)


async def get_project(
    session: AsyncSession, server_id: uuid.UUID, project_key: str
) -> Project | None:
    result = await session.execute(
        sa.select(Project).where(
            Project.server_id == server_id, Project.key == project_key.strip().upper()
        )
    )
    return result.scalar_one_or_none()


# Alias for backward compatibility across route handlers
get_project_by_key = get_project


async def require_project(session: AsyncSession, server_id: uuid.UUID, project_key: str) -> Project:
    project = await get_project(session, server_id, project_key)
    if project is None:
        raise NotFoundError(
            f"Project {project_key} does not exist in this Discord server",
            details={"project_key": project_key},
        )
    return project


async def list_projects(session: AsyncSession, server_id: uuid.UUID) -> list[Project]:
    result = await session.execute(
        sa.select(Project).where(Project.server_id == server_id).order_by(Project.key)
    )
    return list(result.scalars())


async def resolve_default_project(
    session: AsyncSession, server_id: uuid.UUID, *, discord_channel_id: str | None = None
) -> Project:
    """Pick the project a command refers to when the user did not name one.

    Preference order: the project bound to this channel, then the single
    project in the server. If the server holds several unbound projects the
    caller must disambiguate — guessing would risk acting on the wrong plan.
    """
    if discord_channel_id:
        result = await session.execute(
            sa.select(Project).where(
                Project.server_id == server_id,
                Project.discord_channel_id == discord_channel_id,
            )
        )
        bound = result.scalars().first()
        if bound is not None:
            return bound

    projects = await list_projects(session, server_id)
    if not projects:
        raise NotFoundError("This Discord server has no projects yet")
    if len(projects) > 1:
        raise ConflictError(
            "This server has several projects — specify which one",
            details={"projects": [p.key for p in projects]},
        )
    return projects[0]


async def create_project(
    session: AsyncSession, server_id: uuid.UUID, payload: ProjectCreate
) -> Project:
    existing = await get_project(session, server_id, payload.key)
    if existing is not None:
        raise ConflictError(f"Project {payload.key} already exists in this server")

    project = Project(
        server_id=server_id,
        key=payload.key,
        name=payload.name,
        description=payload.description,
        status=payload.status,
        start_date=payload.start_date,
        target_date=payload.target_date,
        discord_channel_id=payload.context.discord_channel_id,
        created_by_user_id=payload.context.requested_by_discord_id,
    )
    session.add(project)
    await session.flush()
    return project


async def update_project(
    session: AsyncSession, project: Project, payload: ProjectUpdate
) -> Project:
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(project, field, value)
    await session.flush()
    return project


def ensure_plan_mutable(project: Project, *, explicit_replan: bool) -> None:
    """Guard the existing plan.

    An assignment flow must never regenerate or modify a plan. Planning is only
    permitted when the user explicitly asked for planning/replanning, which the
    caller signals with `explicit_replan`.
    """
    if project.has_plan and not explicit_replan:
        raise PlanImmutableError(
            f"Project {project.key} already has a plan "
            f"(status={project.plan_status}, version={project.plan_version}). "
            "Ask explicitly for replanning to change it.",
            details={
                "project_key": project.key,
                "plan_status": str(project.plan_status),
                "plan_version": project.plan_version,
            },
        )


async def add_project_member(
    session: AsyncSession,
    project: Project,
    member: MemberProfile,
    *,
    project_role: str | None = None,
    allocation_percent: int = 100,
) -> ProjectMember:
    result = await session.execute(
        sa.select(ProjectMember).where(
            ProjectMember.project_id == project.id,
            ProjectMember.member_profile_id == member.id,
        )
    )
    link = result.scalar_one_or_none()
    if link is None:
        link = ProjectMember(
            server_id=project.server_id,
            project_id=project.id,
            member_profile_id=member.id,
            project_role=project_role,
            allocation_percent=allocation_percent,
        )
        session.add(link)
    else:
        link.is_active = True
        if project_role:
            link.project_role = project_role
        link.allocation_percent = allocation_percent
    await session.flush()
    return link


async def list_project_members(
    session: AsyncSession, server_id: uuid.UUID, project_id: uuid.UUID
) -> list[tuple[ProjectMember, MemberProfile]]:
    result = await session.execute(
        sa.select(ProjectMember, MemberProfile)
        .join(MemberProfile, MemberProfile.id == ProjectMember.member_profile_id)
        .where(
            ProjectMember.server_id == server_id,
            ProjectMember.project_id == project_id,
            ProjectMember.is_active.is_(True),
            MemberProfile.is_active.is_(True),
        )
        .order_by(MemberProfile.display_name)
    )
    return [(link, member) for link, member in result.all()]


async def get_project_info(session: AsyncSession, project: Project) -> ProjectInfo:
    async def _count(stmt) -> int:
        return int((await session.execute(stmt)).scalar_one())

    member_rows = await list_project_members(session, project.server_id, project.id)
    task_count = await _count(
        sa.select(sa.func.count(Task.id)).where(Task.project_id == project.id)
    )
    open_count = await _count(
        sa.select(sa.func.count(Task.id)).where(
            Task.project_id == project.id,
            Task.status.notin_([TaskStatus.DONE, TaskStatus.CANCELLED]),
        )
    )
    doc_count = await _count(
        sa.select(sa.func.count(ProjectDocument.id)).where(ProjectDocument.project_id == project.id)
    )

    return ProjectInfo(
        project=ProjectRead.model_validate(project),
        member_count=len(member_rows),
        task_count=task_count,
        open_task_count=open_count,
        document_count=doc_count,
        members=[
            ProjectMemberRead(
                member_id=member.id,
                display_name=member.display_name,
                discord_user_id=member.user.discord_user_id if member.user else "",
                project_role=link.project_role,
                allocation_percent=link.allocation_percent,
                is_active=link.is_active,
            )
            for link, member in member_rows
        ],
    )


async def count_active_assignments(
    session: AsyncSession, server_id: uuid.UUID, member_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[str]]:
    """Active task keys per member — the workload signal for the engine."""
    if not member_ids:
        return {}
    result = await session.execute(
        sa.select(Assignment.member_profile_id, Task.key)
        .join(Task, Task.id == Assignment.task_id)
        .where(
            Assignment.server_id == server_id,
            Assignment.member_profile_id.in_(member_ids),
            Assignment.status.in_([AssignmentStatus.PROPOSED, AssignmentStatus.ACTIVE]),
            Task.status.notin_([TaskStatus.DONE, TaskStatus.CANCELLED]),
        )
    )
    workload: dict[uuid.UUID, list[str]] = {mid: [] for mid in member_ids}
    for member_id, task_key in result.all():
        workload.setdefault(member_id, []).append(task_key)
    return workload
