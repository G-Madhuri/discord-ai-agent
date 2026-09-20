from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.text import normalize_task_key
from app.models.assignment import Assignment
from app.models.enums import AssignmentStatus, TaskStatus
from app.models.project import Project
from app.models.task import Task, TaskDependency, TaskSkill
from app.schemas.task import (
    DependencyCheck,
    DependencyRead,
    TaskCreate,
    TaskRead,
    TaskSkillInput,
    TaskSkillRead,
    TaskUpdate,
)
from app.services.member_service import get_or_create_skill

RESOLVED_STATUSES = (TaskStatus.DONE,)


async def get_task(
    session: AsyncSession,
    server_id: uuid.UUID,
    task_key: str,
    *,
    project_id: uuid.UUID | None = None,
) -> Task | None:
    """Look a task up by key inside one server.

    Keys are unique per project, so without a project the lookup must be
    unambiguous across the server's projects.
    """
    stmt = sa.select(Task).where(
        Task.server_id == server_id, Task.key == normalize_task_key(task_key)
    )
    if project_id is not None:
        stmt = stmt.where(Task.project_id == project_id)
    matches = list((await session.execute(stmt)).scalars())

    if not matches:
        return None
    if len(matches) > 1:
        raise ConflictError(
            f"Task key {task_key} exists in several projects — specify the project",
            details={"task_key": task_key},
        )
    return matches[0]


async def require_task(
    session: AsyncSession,
    server_id: uuid.UUID,
    task_key: str,
    *,
    project_id: uuid.UUID | None = None,
) -> Task:
    task = await get_task(session, server_id, task_key, project_id=project_id)
    if task is None:
        raise NotFoundError(
            f"Task {normalize_task_key(task_key)} was not found in this Discord server",
            details={"task_key": normalize_task_key(task_key)},
        )
    return task


async def list_tasks(
    session: AsyncSession,
    server_id: uuid.UUID,
    project_id: uuid.UUID,
    *,
    status: TaskStatus | None = None,
    unassigned_only: bool = False,
) -> list[Task]:
    stmt = sa.select(Task).where(Task.server_id == server_id, Task.project_id == project_id)
    if status is not None:
        stmt = stmt.where(Task.status == status)
    if unassigned_only:
        live = sa.select(Assignment.task_id).where(
            Assignment.status.in_([AssignmentStatus.PROPOSED, AssignmentStatus.ACTIVE])
        )
        stmt = stmt.where(Task.id.notin_(live))
    stmt = stmt.order_by(Task.key)
    return list((await session.execute(stmt)).scalars())


async def set_task_skills(
    session: AsyncSession, task: Task, skills: list[TaskSkillInput], *, replace: bool = True
) -> Task:
    if replace:
        await session.execute(sa.delete(TaskSkill).where(TaskSkill.task_id == task.id))
        await session.flush()
    for entry in skills:
        skill = await get_or_create_skill(session, entry.name, None)
        session.add(
            TaskSkill(
                task_id=task.id,
                skill_id=skill.id,
                weight=entry.weight,
                is_required=entry.is_required,
            )
        )
    await session.flush()
    await session.refresh(task, attribute_names=["required_skills"])
    return task


async def create_task(
    session: AsyncSession, server_id: uuid.UUID, project: Project, payload: TaskCreate
) -> Task:
    existing = await session.execute(
        sa.select(Task).where(Task.project_id == project.id, Task.key == payload.key)
    )
    if existing.scalar_one_or_none() is not None:
        raise ConflictError(f"Task {payload.key} already exists in project {project.key}")

    task = Task(
        server_id=server_id,
        project_id=project.id,
        key=payload.key,
        title=payload.title,
        description=payload.description,
        status=payload.status,
        priority=payload.priority,
        role_hint=payload.role_hint,
        estimate_hours=payload.estimate_hours,
        due_date=payload.due_date,
        labels=list(payload.labels),
    )
    session.add(task)
    await session.flush()

    if payload.required_skills:
        await set_task_skills(session, task, payload.required_skills)
    for dep_key in payload.depends_on:
        await add_dependency(session, server_id, task, dep_key)
    return task


async def update_task(session: AsyncSession, task: Task, payload: TaskUpdate) -> Task:
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(task, field, value)
    await session.flush()
    return task


async def add_dependency(
    session: AsyncSession, server_id: uuid.UUID, task: Task, depends_on_key: str
) -> TaskDependency:
    target = await require_task(session, server_id, depends_on_key, project_id=task.project_id)
    if target.id == task.id:
        raise ValidationError("A task cannot depend on itself")

    existing = await session.execute(
        sa.select(TaskDependency).where(
            TaskDependency.task_id == task.id, TaskDependency.depends_on_task_id == target.id
        )
    )
    dependency = existing.scalar_one_or_none()
    if dependency is None:
        dependency = TaskDependency(task_id=task.id, depends_on_task_id=target.id)
        session.add(dependency)
        await session.flush()
    return dependency


async def check_dependencies(session: AsyncSession, task: Task) -> DependencyCheck:
    """Which of a task's dependencies are still open.

    An unresolved blocker does not prevent assignment, but it is recorded as
    evidence and surfaced in the Discord reply.
    """
    result = await session.execute(
        sa.select(TaskDependency, Task)
        .join(Task, Task.id == TaskDependency.depends_on_task_id)
        .where(TaskDependency.task_id == task.id)
    )
    resolved: list[DependencyRead] = []
    unresolved: list[DependencyRead] = []
    for dependency, other in result.all():
        is_resolved = other.status in RESOLVED_STATUSES
        entry = DependencyRead(
            task_key=other.key,
            title=other.title,
            status=other.status,
            dependency_type=dependency.dependency_type,
            is_resolved=is_resolved,
        )
        (resolved if is_resolved else unresolved).append(entry)

    return DependencyCheck(
        task_key=task.key,
        total=len(resolved) + len(unresolved),
        resolved=resolved,
        unresolved=unresolved,
    )


async def get_live_assignment(session: AsyncSession, task_id: uuid.UUID) -> Assignment | None:
    result = await session.execute(
        sa.select(Assignment).where(
            Assignment.task_id == task_id,
            Assignment.status.in_([AssignmentStatus.PROPOSED, AssignmentStatus.ACTIVE]),
        )
    )
    return result.scalars().first()


async def to_task_read(session: AsyncSession, task: Task, project_key: str) -> TaskRead:
    assignment = await get_live_assignment(session, task.id)
    return TaskRead(
        id=task.id,
        key=task.key,
        project_key=project_key,
        title=task.title,
        description=task.description,
        status=task.status,
        priority=task.priority,
        role_hint=task.role_hint,
        estimate_hours=task.estimate_hours,
        due_date=task.due_date,
        labels=list(task.labels or []),
        required_skills=[
            TaskSkillRead(
                slug=ts.skill.slug,
                name=ts.skill.name,
                weight=ts.weight,
                is_required=ts.is_required,
            )
            for ts in task.required_skills
            if ts.skill is not None
        ],
        assignee_display_name=assignment.member.display_name if assignment else None,
        assignee_member_id=assignment.member_profile_id if assignment else None,
    )
