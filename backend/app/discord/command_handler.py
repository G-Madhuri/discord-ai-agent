"""Unified handler for Discord commands.

Shared between Gateway slash commands (bot.py) and HTTP Interaction endpoint
(routes/discord.py). Calls domain service functions directly in-process.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from app.agents.runner import run_assignment
from app.api.deps import tool_context_from
from app.core.errors import DomainError
from app.core.logging import get_logger
from app.db.session import session_scope
from app.schemas.common import ServerContext
from app.schemas.member import MemberCreate
from app.schemas.project import ProjectCreate
from app.schemas.task import TaskCreate
from app.services import knowledge_service, member_service, project_service, task_service
from app.services.assignment import service as assignment_service
from app.services.server_service import resolve_server

logger = get_logger(__name__)


def generate_project_key(name: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]", "", name).upper()
    return cleaned[:8] if cleaned else "PROJ"


async def handle_command(
    arg1: Any,
    arg2: Any = None,
    arg3: Any = None,
    arg4: Any = None,
) -> tuple[str, bool]:
    """Execute command directly against backend services in-process.

    Returns (formatted_message, is_error).
    Supports both handle_command(full_command, args, context) and legacy
    handle_command(client, full_command, args, context).
    """
    if isinstance(arg1, str):
        full_command = arg1
        args = arg2 or {}
        context = arg3 or {}
    else:
        full_command = arg2 or ""
        args = arg3 or {}
        context = arg4 or {}

    try:
        server_ctx = ServerContext.model_validate(context)

        if full_command == "project create":
            name = args.get("name", "")
            key = args.get("key") or generate_project_key(name)
            desc = args.get("description")
            async with session_scope() as session:
                server = await resolve_server(session, server_ctx, create=True)
                payload = ProjectCreate(
                    context=server_ctx,
                    key=key,
                    name=name,
                    description=desc,
                )
                project = await project_service.create_project(session, server.id, payload)
                return f"✅ Project {project.key} — {project.name} created.", False

        elif full_command == "project info":
            project_key = (args.get("name") or args.get("project_key") or args.get("project") or "").upper()
            async with session_scope() as session:
                server = await resolve_server(session, server_ctx)
                project = await project_service.require_project(session, server.id, project_key)
                res = await project_service.get_project_info(session, project)
                members = ", ".join(m.display_name for m in res.members) or "none"
                msg = (
                    f"**{res.project.key} — {res.project.name}**\n"
                    f"Status: {res.project.status} | Plan: {res.project.plan_status}\n"
                    f"Tasks: {res.open_task_count} open of {res.task_count}\n"
                    f"Documents: {res.document_count}\n"
                    f"Team: {members}"
                )
                return msg, False

        elif full_command == "project knowledge":
            project_key = (args.get("project") or args.get("project_key") or "").upper()
            async with session_scope() as session:
                server = await resolve_server(session, server_ctx)
                project = await project_service.require_project(session, server.id, project_key)
                docs = await knowledge_service.list_documents(session, server.id, project.id)
                if not docs:
                    return "No project knowledge indexed yet.", False
                lines = [f"• {d.title} ({d.source_type}, {d.chunk_count} chunks)" for d in docs]
                return "**Project knowledge**\n" + "\n".join(lines), False

        elif full_command in ("project member add", "project_member_add", "project add-members", "project add_members"):
            project_key = (args.get("project") or args.get("project_key") or "").upper()
            user_id = str(args.get("user") or args.get("user_id") or args.get("user_ids") or "")
            project_role = args.get("role") or args.get("project_role")
            async with session_scope() as session:
                server = await resolve_server(session, server_ctx)
                project = await project_service.require_project(session, server.id, project_key)
                
                # Check creator authorization (Issue 2)
                creator_id = project.created_by_user_id
                req_uid = server_ctx.requested_by_discord_id
                if not creator_id or creator_id == "<legacy>" or creator_id != req_uid:
                    c_text = f"<@{creator_id}>" if creator_id and creator_id != "<legacy>" else "project creator"
                    return f"⚠️ Only the project creator ({c_text}) can do this.", True

                # Support comma-separated or space-separated user IDs
                user_ids = [u.strip() for u in user_id.replace(",", " ").split() if u.strip()]
                added_names = []
                for uid in user_ids:
                    try:
                        member = await member_service.require_member(session, server.id, uid)
                        await project_service.add_project_member(session, project, member, project_role=project_role)
                        added_names.append(member.display_name)
                    except Exception as exc:
                        logger.warning("Failed to add user %s to project %s: %s", uid, project_key, exc)

                # Re-run assignment on unassigned tasks for this project
                unassigned_tasks = await task_service.list_tasks(session, server.id, project.id)
                assigned_count = 0
                for t in unassigned_tasks:
                    try:
                        await assignment_service.assign_task(
                            session,
                            server_id=server.id,
                            task_key=t.key,
                            requested_by_discord_id=req_uid,
                        )
                        assigned_count += 1
                    except Exception as exc:
                        logger.warning("Failed assigning %s: %s", t.key, exc)

                names_str = ", ".join(added_names) if added_names else user_id
                return f"✅ Members ({names_str}) added to project **{project.key}**. {assigned_count} unassigned task(s) updated.", False

        elif full_command in ("assign-project", "assign_project"):
            project_key = (args.get("project") or args.get("project_key") or args.get("name") or "").upper()
            async with session_scope() as session:
                server = await resolve_server(session, server_ctx)
                project = await project_service.require_project(session, server.id, project_key)

                # Check creator authorization (Issue 2)
                creator_id = project.created_by_user_id
                req_uid = server_ctx.requested_by_discord_id
                if not creator_id or creator_id == "<legacy>" or creator_id != req_uid:
                    c_text = f"<@{creator_id}>" if creator_id and creator_id != "<legacy>" else "project creator"
                    return f"⚠️ Only the project creator ({c_text}) can do this.", True

                tasks = await task_service.list_tasks(session, server.id, project.id)
                
                lines = []
                for t in tasks:
                    try:
                        res = await assignment_service.assign_task(
                            session,
                            server_id=server.id,
                            task_key=t.key,
                            requested_by_discord_id=req_uid,
                        )
                        m_name = res.assignment.member_display_name
                        reason = res.reasons[0] if res.reasons else "match"
                        lines.append(f"• `{t.key}` → **{m_name}** ({reason})")
                    except Exception as exc:
                        lines.append(f"• `{t.key}` → *unassigned* ({exc})")

                if not lines:
                    return f"No tasks in project **{project.key}**.", False
                return f"**Assignments for project {project.key}:**\n" + "\n".join(lines), False

        elif full_command == "task create":
            project_key = (args.get("project") or args.get("project_key") or "").upper()
            title = args.get("title", "")
            key = args.get("key") or "TASK-101"
            if "key" not in args and "task_id" in args:
                key = str(args["task_id"]).upper()
            desc = args.get("description")
            skills_raw = args.get("skills") or args.get("required_skills") or ""
            skill_list = [{"name": s.strip()} for s in skills_raw.split(",") if s.strip()]
            async with session_scope() as session:
                server = await resolve_server(session, server_ctx)
                project = await project_service.require_project(session, server.id, project_key)
                payload = TaskCreate(
                    context=server_ctx,
                    project_key=project_key,
                    key=key,
                    title=title,
                    description=desc,
                    required_skills=skill_list,
                )
                task = await task_service.create_task(session, server.id, project, payload)
                return f"✅ {task.key} — {task.title} created.", False

        elif full_command == "task list":
            project_key = args.get("project") or args.get("project_key")
            async with session_scope() as session:
                server = await resolve_server(session, server_ctx)
                project = (
                    await project_service.require_project(session, server.id, project_key)
                    if project_key
                    else await project_service.resolve_default_project(
                        session, server.id, discord_channel_id=server_ctx.discord_channel_id
                    )
                )
                tasks = await task_service.list_tasks(session, server.id, project.id)
                if not tasks:
                    return "No tasks yet.", False
                task_reads = [await task_service.to_task_read(session, t, project.key) for t in tasks]
                lines = [
                    f"• `{t.key}` {t.title} — {t.status}"
                    + (f" → {t.assignee_display_name}" if t.assignee_display_name else "")
                    for t in task_reads
                ]
                return "**Tasks**\n" + "\n".join(lines), False

        elif full_command == "member add":
            user_id = str(args.get("user") or args.get("user_id") or context.get("requested_by_discord_id"))
            username = args.get("username") or args.get("display_name") or f"user_{user_id}"
            display_name = args.get("display_name") or username
            role = args.get("role")
            skills_raw = args.get("skills") or ""
            skill_list = [{"name": s.strip()} for s in skills_raw.split(",") if s.strip()]
            async with session_scope() as session:
                server = await resolve_server(session, server_ctx, create=True)
                payload = MemberCreate(
                    context=server_ctx,
                    discord_user_id=user_id,
                    username=username,
                    display_name=display_name,
                    role=role,
                    skills=skill_list,
                )
                member = await member_service.create_or_update_member(session, server.id, payload)
                names = ", ".join(s.skill.name for s in member.skills) if member.skills else "none recorded"
                return f"✅ {member.display_name} saved. Skills: {names}", False

        elif full_command == "member profile":
            user_id = str(args.get("user") or args.get("user_id") or context.get("requested_by_discord_id"))
            async with session_scope() as session:
                server = await resolve_server(session, server_ctx)
                member = await member_service.require_member(session, server.id, user_id)
                skills = ", ".join(f"{s.skill.name} ({s.proficiency}/5)" for s in member.skills)
                msg = (
                    f"**{member.display_name}**\n"
                    f"Role: {member.role or 'not set'} | Availability: {member.availability.value}\n"
                    f"Skills: {skills or 'none recorded'}"
                )
                return msg, False

        elif full_command == "member skills":
            user_id = str(args.get("user") or args.get("user_id") or context.get("requested_by_discord_id"))
            skills_raw = args.get("skills") or ""
            skill_list = [{"name": s.strip()} for s in skills_raw.split(",") if s.strip()]
            replace = bool(args.get("replace", False))
            async with session_scope() as session:
                server = await resolve_server(session, server_ctx)
                member = await member_service.require_member(session, server.id, user_id)
                await member_service.set_member_skills(session, member, skill_list, replace=replace)
                member = await member_service.require_member(session, server.id, user_id)
                names = ", ".join(s.skill.name for s in member.skills)
                return f"✅ Skills for {member.display_name}: {names}", False

        elif full_command == "assign":
            task_key = (args.get("task_id") or args.get("task_key") or "").upper()
            project_key = args.get("project_key") or args.get("project")
            reassign = bool(args.get("reassign", False))
            async with session_scope() as session:
                server = await resolve_server(session, server_ctx)
                if reassign:
                    task = await task_service.require_task(session, server.id, task_key)
                    proj = await session.get(Project, task.project_id)
                    creator_id = proj.created_by_user_id if proj else None
                    req_uid = server_ctx.requested_by_discord_id
                    if not creator_id or creator_id == "<legacy>" or creator_id != req_uid:
                        c_text = f"<@{creator_id}>" if creator_id and creator_id != "<legacy>" else "project creator"
                        return f"⚠️ Only the project creator ({c_text}) can do this.", True

            ctx = tool_context_from(server, server_ctx, project_key.upper() if project_key else None)
            reply = await run_assignment(
                ctx,
                task_key,
                reassign=reassign,
            )
            return reply.message, not reply.ok

        elif full_command in ("assignment history", "assignment_history"):
            task_key = args.get("task_id") or args.get("task_key")
            async with session_scope() as session:
                server = await resolve_server(session, server_ctx)
                entries = await assignment_service.get_assignment_history(
                    session,
                    server.id,
                    task_key=task_key.upper() if task_key else None,
                )
                if not entries:
                    return "No assignment history yet.", False
                lines = [
                    f"• {e.task_key} → {e.member_display_name or 'unassigned'} "
                    f"({e.event.value if hasattr(e.event, 'value') else e.event}) {e.created_at.strftime('%Y-%m-%d %H:%M:%S')}"
                    for e in entries
                ]
                return "**Assignment history**\n" + "\n".join(lines), False

        else:
            return f"Unknown command: {full_command}", True

    except DomainError as exc:
        return f"⚠️ {exc.message}", True
    except Exception as exc:
        logger.exception("Error executing %s", full_command)
        return f"⚠️ Server error: {exc}", True
