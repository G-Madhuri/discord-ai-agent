"""The agent's tool surface.

Every function here is a controlled application operation: it validates input,
filters by the bound Discord server, and returns plain JSON-serialisable data.
The model calls these; it never issues SQL and never writes to the database
directly. Writes are limited to `assign_task`, which is the single mutation an
assignment conversation is allowed to make — nothing here can create, modify or
regenerate a project plan.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import DomainError
from app.core.logging import get_logger
from app.models.project import Project
from app.services import knowledge_service, member_service, project_service, task_service
from app.services.assignment import service as assignment_service
from app.tools.context import ToolContext

logger = get_logger(__name__)

ToolFn = Callable[..., Awaitable[dict[str, Any]]]


def _error(exc: DomainError) -> dict[str, Any]:
    """Domain failures come back as data so the agent can explain them rather
    than crash the turn."""
    return {"ok": False, "error": {"code": exc.code, "message": exc.message, **exc.details}}


async def _resolve_project(
    session: AsyncSession, ctx: ToolContext, project_key: str | None
) -> Project:
    key = project_key or ctx.project_key
    if key:
        return await project_service.require_project(session, ctx.server_id, key)
    return await project_service.resolve_default_project(
        session, ctx.server_id, discord_channel_id=ctx.discord_channel_id
    )


def build_toolset(ctx: ToolContext) -> dict[str, ToolFn]:
    """Bind every tool to one Discord server for the duration of a request."""

    async def get_project(project_key: str | None = None) -> dict[str, Any]:
        """Return a project's details and plan status for this Discord server."""
        try:
            async with ctx.session() as session:
                project = await _resolve_project(session, ctx, project_key)
                info = await project_service.get_project_info(session, project)
                return {"ok": True, "project": info.model_dump(mode="json")}
        except DomainError as exc:
            return _error(exc)

    async def list_projects() -> dict[str, Any]:
        """List every project in this Discord server."""
        async with ctx.session() as session:
            projects = await project_service.list_projects(session, ctx.server_id)
            return {
                "ok": True,
                "projects": [
                    {
                        "key": p.key,
                        "name": p.name,
                        "status": str(p.status),
                        "plan_status": str(p.plan_status),
                    }
                    for p in projects
                ],
            }

    async def get_task(task_key: str, project_key: str | None = None) -> dict[str, Any]:
        """Return an existing task: status, priority, required skills, assignee."""
        try:
            async with ctx.session() as session:
                project = await _resolve_project(session, ctx, project_key)
                task = await task_service.require_task(
                    session, ctx.server_id, task_key, project_id=project.id
                )
                read = await task_service.to_task_read(session, task, project.key)
                return {"ok": True, "task": read.model_dump(mode="json")}
        except DomainError as exc:
            return _error(exc)

    async def list_tasks(
        project_key: str | None = None, unassigned_only: bool = False
    ) -> dict[str, Any]:
        """List a project's tasks."""
        try:
            async with ctx.session() as session:
                project = await _resolve_project(session, ctx, project_key)
                tasks = await task_service.list_tasks(
                    session, ctx.server_id, project.id, unassigned_only=unassigned_only
                )
                return {
                    "ok": True,
                    "project_key": project.key,
                    "tasks": [
                        (await task_service.to_task_read(session, t, project.key)).model_dump(
                            mode="json"
                        )
                        for t in tasks
                    ],
                }
        except DomainError as exc:
            return _error(exc)

    async def get_team_members(project_key: str | None = None) -> dict[str, Any]:
        """List the members of a project in this server, with their roles."""
        try:
            async with ctx.session() as session:
                project = await _resolve_project(session, ctx, project_key)
                rows = await project_service.list_project_members(
                    session, ctx.server_id, project.id
                )
                return {
                    "ok": True,
                    "project_key": project.key,
                    "members": [
                        {
                            "member_id": str(member.id),
                            "display_name": member.display_name,
                            "role": member.role,
                            "project_role": link.project_role,
                            "availability": str(member.availability),
                            "skills": [ms.skill.name for ms in member.skills if ms.skill],
                        }
                        for link, member in rows
                    ],
                }
        except DomainError as exc:
            return _error(exc)

    async def get_member_profile(discord_user_id: str) -> dict[str, Any]:
        """Return one member's profile: role, seniority, availability, skills."""
        try:
            async with ctx.session() as session:
                member = await member_service.require_member(
                    session, ctx.server_id, discord_user_id
                )
                return {
                    "ok": True,
                    "member": member_service.to_member_read(member).model_dump(mode="json"),
                }
        except DomainError as exc:
            return _error(exc)

    async def get_member_skills(discord_user_id: str) -> dict[str, Any]:
        """Return the declared skills of one member. Never infer skills that
        are not recorded here."""
        try:
            async with ctx.session() as session:
                member = await member_service.require_member(
                    session, ctx.server_id, discord_user_id
                )
                read = member_service.to_member_read(member)
                return {
                    "ok": True,
                    "display_name": read.display_name,
                    "skills": [s.model_dump(mode="json") for s in read.skills],
                }
        except DomainError as exc:
            return _error(exc)

    async def get_current_assignments(project_key: str | None = None) -> dict[str, Any]:
        """Return current workload: active task keys per member."""
        try:
            async with ctx.session() as session:
                project = await _resolve_project(session, ctx, project_key)
                rows = await project_service.list_project_members(
                    session, ctx.server_id, project.id
                )
                workload = await project_service.count_active_assignments(
                    session, ctx.server_id, [m.id for _, m in rows]
                )
                return {
                    "ok": True,
                    "project_key": project.key,
                    "workload": [
                        {
                            "member_id": str(member.id),
                            "display_name": member.display_name,
                            "active_task_count": len(workload.get(member.id, [])),
                            "active_task_keys": workload.get(member.id, []),
                            "capacity": member.max_concurrent_tasks,
                        }
                        for _, member in rows
                    ],
                }
        except DomainError as exc:
            return _error(exc)

    async def get_project_knowledge(project_key: str | None = None) -> dict[str, Any]:
        """List the knowledge documents indexed for a project."""
        try:
            async with ctx.session() as session:
                project = await _resolve_project(session, ctx, project_key)
                docs = await knowledge_service.list_documents(session, ctx.server_id, project.id)
                return {
                    "ok": True,
                    "project_key": project.key,
                    "documents": [d.model_dump(mode="json") for d in docs],
                }
        except DomainError as exc:
            return _error(exc)

    async def search_project_knowledge(
        query: str, project_key: str | None = None, top_k: int = 5
    ) -> dict[str, Any]:
        """Search a project's documentation, requirements and discussions."""
        try:
            async with ctx.session() as session:
                project = await _resolve_project(session, ctx, project_key)
                result = await knowledge_service.search_knowledge_result(
                    session, project, query, top_k=top_k
                )
                return {"ok": True, **result.model_dump(mode="json")}
        except DomainError as exc:
            return _error(exc)

    async def check_task_dependencies(
        task_key: str, project_key: str | None = None
    ) -> dict[str, Any]:
        """Return which of a task's dependencies are still unresolved."""
        try:
            async with ctx.session() as session:
                project = await _resolve_project(session, ctx, project_key)
                task = await task_service.require_task(
                    session, ctx.server_id, task_key, project_id=project.id
                )
                check = await task_service.check_dependencies(session, task)
                return {
                    "ok": True,
                    "task_key": check.task_key,
                    "is_blocked": check.is_blocked,
                    "unresolved": [d.model_dump(mode="json") for d in check.unresolved],
                    "resolved": [d.model_dump(mode="json") for d in check.resolved],
                }
        except DomainError as exc:
            return _error(exc)

    async def evaluate_task_candidates(
        task_key: str, project_key: str | None = None
    ) -> dict[str, Any]:
        """Rank eligible members for an existing task and return the evidence
        behind each score. Read-only — use assign_task to commit a choice."""
        try:
            async with ctx.session() as session:
                context = await assignment_service.load_context(
                    session,
                    ctx.server_id,
                    task_key,
                    project_key=project_key or ctx.project_key,
                    discord_channel_id=ctx.discord_channel_id,
                )
                return {"ok": True, "evaluation": context.evaluation.model_dump(mode="json")}
        except DomainError as exc:
            return _error(exc)

    async def assign_task(
        task_key: str,
        member_discord_id: str | None = None,
        project_key: str | None = None,
        reassign: bool = False,
        note: str | None = None,
    ) -> dict[str, Any]:
        """Assign an EXISTING task and persist the decision with its evidence.

        Leave `member_discord_id` empty to take the engine's top-ranked
        candidate. This never creates tasks and never alters a project plan.
        """
        try:
            async with ctx.session() as session:
                result = await assignment_service.assign_task(
                    session,
                    server_id=ctx.server_id,
                    task_key=task_key,
                    project_key=project_key or ctx.project_key,
                    discord_channel_id=ctx.discord_channel_id,
                    member_discord_id=member_discord_id,
                    requested_by_discord_id=ctx.requested_by_discord_id,
                    reassign=reassign,
                    decision_mode=ctx.decision_mode,
                    note=note,
                )
                return {
                    "ok": True,
                    "assignment": result.assignment.model_dump(mode="json"),
                    "reasons": result.reasons,
                    "message": result.message,
                }
        except DomainError as exc:
            return _error(exc)

    async def get_assignment_history(
        task_key: str | None = None, member_discord_id: str | None = None, limit: int = 20
    ) -> dict[str, Any]:
        """Return the audit trail of assignment decisions in this server."""
        try:
            async with ctx.session() as session:
                entries = await assignment_service.get_assignment_history(
                    session,
                    ctx.server_id,
                    task_key=task_key,
                    member_discord_id=member_discord_id,
                    limit=limit,
                )
                return {"ok": True, "history": [e.model_dump(mode="json") for e in entries]}
        except DomainError as exc:
            return _error(exc)

    return {
        "get_project": get_project,
        "list_projects": list_projects,
        "get_task": get_task,
        "list_tasks": list_tasks,
        "get_team_members": get_team_members,
        "get_member_profile": get_member_profile,
        "get_member_skills": get_member_skills,
        "get_current_assignments": get_current_assignments,
        "get_project_knowledge": get_project_knowledge,
        "search_project_knowledge": search_project_knowledge,
        "check_task_dependencies": check_task_dependencies,
        "evaluate_task_candidates": evaluate_task_candidates,
        "assign_task": assign_task,
        "get_assignment_history": get_assignment_history,
    }


READ_ONLY_TOOLS = frozenset(
    {
        "get_project",
        "list_projects",
        "get_task",
        "list_tasks",
        "get_team_members",
        "get_member_profile",
        "get_member_skills",
        "get_current_assignments",
        "get_project_knowledge",
        "search_project_knowledge",
        "check_task_dependencies",
        "evaluate_task_candidates",
        "get_assignment_history",
    }
)
