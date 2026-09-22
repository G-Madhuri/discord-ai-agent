"""Discord HTTP Interactions endpoint (for Cloud Run serverless deployment).

Handles Slash Commands, Modals (type 9), Modal Submissions (type 5),
and Button Component Interactions (type 3).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status
from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey

from app.agents.project_planner import plan_project
from app.core.config import settings
from app.core.errors import DomainError
from app.core.logging import get_logger
from app.db.session import session_scope
from app.discord.command_handler import handle_command
from app.models.enums import DecisionMode, PlanStatus, ProjectStatus
from app.models.project import ProjectPlanApproval
from app.schemas.common import ServerContext
from app.services import member_service, project_service, task_service
from app.services.assignment import service as assignment_service

logger = get_logger(__name__)

router = APIRouter(prefix="/discord", tags=["discord"])

# Temporary in-memory pending modal session store
_PENDING_MODAL_SESSIONS: dict[str, dict[str, Any]] = {}
_PENDING_MEMBER_SELECTIONS: dict[str, list[str]] = {}
_PENDING_MEMBER_DETAILS: dict[str, dict[str, dict[str, str]]] = {}


def verify_signature(raw_body: bytes, signature: str | None, timestamp: str | None) -> None:
    public_key = settings.discord_public_key
    if not public_key:
        logger.warning("auth failed path=/discord/interactions (DISCORD_PUBLIC_KEY not set)")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="DISCORD_PUBLIC_KEY not configured"
        )
    if not signature or not timestamp:
        logger.warning("auth failed path=/discord/interactions (missing headers)")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid request signature"
        )
    try:
        verify_key = VerifyKey(bytes.fromhex(public_key))
        verify_key.verify(timestamp.encode("utf-8") + raw_body, bytes.fromhex(signature))
    except (BadSignatureError, ValueError):
        logger.warning("auth failed path=/discord/interactions (invalid signature)")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid request signature"
        )


def _parse_options(options_list: list[dict[str, Any]] | None) -> tuple[str | None, dict[str, Any]]:
    """Parse Discord interaction options list into subcommand string and args dict."""
    if not options_list:
        return None, {}

    subcommands: list[str] = []
    args: dict[str, Any] = {}

    def _walk(opts: list[dict[str, Any]]) -> None:
        for opt in opts:
            opt_type = opt.get("type")
            opt_name = opt.get("name", "")
            if opt_type in (1, 2):  # SUB_COMMAND or SUB_COMMAND_GROUP
                subcommands.append(opt_name)
                _walk(opt.get("options", []))
            else:
                args[opt_name] = opt.get("value")

    _walk(options_list)
    subcommand_str = " ".join(subcommands) if subcommands else None
    return subcommand_str, args


async def _execute_and_patch(
    application_id: str,
    token: str,
    full_command: str,
    args: dict[str, Any],
    context: dict[str, Any],
) -> None:
    """Execute standard command handler in background and send Discord webhook PATCH."""
    try:
        msg, is_err = await handle_command(full_command, args, context)
    except Exception as exc:
        logger.exception("Error executing background command %s", full_command)
        msg = f"⚠️ Server error: {exc}"
        is_err = True

    webhook_url = f"https://discord.com/api/v10/webhooks/{application_id}/{token}/messages/@original"
    patch_body: dict[str, Any] = {"content": msg}
    if is_err:
        patch_body["flags"] = 64  # EPHEMERAL flag

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.patch(webhook_url, json=patch_body)
            if resp.status_code in (200, 204):
                logger.info("Outbound PATCH for %s -> HTTP %s", full_command, resp.status_code)
            else:
                logger.error(
                    "Outbound PATCH for %s failed -> HTTP %s: %s",
                    full_command,
                    resp.status_code,
                    resp.text,
                )
    except Exception as exc:
        logger.error("Failed to send Discord webhook PATCH for %s: %s", full_command, exc)


async def _execute_and_patch_project_plan(
    application_id: str,
    token: str,
    server_ctx: ServerContext,
    name: str,
    description: str,
    member_discord_ids: list[str],
    constraints: str | None = None,
    file_bytes: bytes | None = None,
    filename: str | None = None,
    member_details: dict[str, dict[str, str]] | None = None,
) -> None:
    """Execute LLM agentic project planning pipeline in background and PATCH Discord with summary + buttons."""
    webhook_url = f"https://discord.com/api/v10/webhooks/{application_id}/{token}/messages/@original"

    try:
        res = await plan_project(
            server_ctx=server_ctx,
            name=name,
            description=description,
            member_discord_ids=member_discord_ids,
            constraints=constraints,
            file_bytes=file_bytes,
            filename=filename,
            member_details=member_details,
        )

        project_key = res["project_key"]
        project_name = res["project_name"]
        plan_source = res["plan_source"]
        draft_assignments = res["draft_assignments"]
        team_ids = res["team_discord_ids"]
        team_str = ", ".join(f"<@{uid}>" for uid in team_ids) if team_ids else "None"

        # Build task assignment bullet points
        task_lines = []
        for key, item in draft_assignments.items():
            t_title = item["task_title"]
            m_name = item["assigned_display_name"]
            m_uid = item["assigned_discord_user_id"]
            m_mention = f"<@{m_uid}>" if m_uid else f"**{m_name}**"
            evidence = "; ".join(item["evidence_bullets"][:2]) if item.get("evidence_bullets") else "skill match"
            task_lines.append(f"• `{key}` — {t_title} → {m_mention}\n  *Evidence: {evidence}*")

        tasks_summary = "\n".join(task_lines[:15])
        if len(task_lines) > 15:
            tasks_summary += f"\n*...and {len(task_lines) - 15} more tasks.*"

        constraints_summary = f"• {constraints}" if constraints else "• None"

        content = (
            f"📋 Project **{project_name}** (`{project_key}`) drafted (not yet active)\n\n"
            f"**Plan:** {res['tasks_count']} task(s) ({plan_source})\n"
            f"**Team:** {team_str}\n\n"
            f"**Tasks & Draft Assignments:**\n{tasks_summary}\n\n"
            f"**Constraints Considered:**\n{constraints_summary}\n\n"
            f"Click **Approve ✅** to activate this project & commit assignments, **Reject ❌** to discard, or **Edit ✏️** to adjust."
        )

        # Build interactive Discord ActionRow with 3 Buttons (Approve ✅, Reject ❌, Edit ✏️)
        components = [
            {
                "type": 1,
                "components": [
                    {
                        "type": 2,
                        "style": 3,  # Green Success
                        "custom_id": f"approve_project:{project_key}",
                        "label": "Approve ✅",
                    },
                    {
                        "type": 2,
                        "style": 4,  # Red Danger
                        "custom_id": f"reject_project:{project_key}",
                        "label": "Reject ❌",
                    },
                    {
                        "type": 2,
                        "style": 2,  # Secondary Grey
                        "custom_id": f"edit_project:{project_key}",
                        "label": "Edit ✏️",
                    },
                ],
            }
        ]

        patch_body = {"content": content, "components": components}

    except Exception as exc:
        logger.exception("Failed to execute agentic project planner for %s", name)
        patch_body = {"content": f"⚠️ Project planning failed: {exc}", "flags": 64}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.patch(webhook_url, json=patch_body)
            logger.info("Outbound plan summary PATCH for %s -> HTTP %s", name, resp.status_code)
    except Exception as exc:
        logger.error("Failed to send plan summary PATCH for %s: %s", name, exc)


async def _execute_and_patch_approve_project(
    application_id: str,
    token: str,
    guild_id: str,
    user_id: str,
    project_key: str,
) -> None:
    """Commit project plan assignments in background with bulk operations and PATCH Discord."""
    webhook_url = f"https://discord.com/api/v10/webhooks/{application_id}/{token}/messages/@original"

    try:
        async with asyncio.timeout(10.0):
            async with session_scope() as session:
                server = await member_service.resolve_server(
                    session, ServerContext(discord_guild_id=guild_id)
                )

                project = await project_service.get_project_by_key(session, server.id, project_key)
                if not project or str(project.server_id) != str(server.id):
                    patch_body = {"content": "⚠️ Permission denied: Project not found on this server.", "components": []}
                else:
                    creator_id = project.created_by_user_id
                    if creator_id and creator_id != "<legacy>" and creator_id != user_id:
                        patch_body = {
                            "content": f"⚠️ Only the project creator (<@{creator_id}>) can do this.",
                            "components": [],
                        }
                    else:
                        draft_map = project.draft_assignments or {}
                        committed_count = 0
                        for t_key, draft_item in draft_map.items():
                            m_id_str = draft_item.get("assigned_member_id")
                            if m_id_str:
                                try:
                                    task = await task_service.get_task_by_key(session, server.id, t_key)
                                    member = await member_service.get_member_by_id(
                                        session, server.id, UUID(m_id_str)
                                    )
                                    if task and member:
                                        await assignment_service.create_assignment(
                                            session,
                                            server.id,
                                            task,
                                            member,
                                            decision_mode=DecisionMode.DETERMINISTIC,
                                            auto_commit=False,
                                        )
                                        committed_count += 1
                                except Exception as exc:
                                    logger.warning("Failed committing draft assignment for %s: %s", t_key, exc)

                        project.status = ProjectStatus.ACTIVE
                        project.plan_status = PlanStatus.ACTIVE

                        approval = ProjectPlanApproval(
                            project_id=project.id,
                            approved_by_user_id=user_id,
                            approved_at=datetime.now(timezone.utc),
                            action="approved",
                        )
                        session.add(approval)
                        await session.commit()

                        patch_body = {
                            "content": f"✅ Project **{project.name}** (`{project_key}`) is now active! {committed_count} assignments committed.",
                            "components": [],
                        }

    except Exception as exc:
        logger.exception("Failed approving project %s", project_key)
        patch_body = {"content": f"⚠️ Approval failed: {exc}", "components": []}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.patch(webhook_url, json=patch_body)
            logger.info("Outbound approval PATCH for %s -> HTTP success", project_key)
    except Exception as exc:
        logger.error("Failed to send approval PATCH for %s: %s", project_key, exc)


async def _execute_and_patch_reject_project(
    application_id: str,
    token: str,
    guild_id: str,
    user_id: str,
    project_key: str,
) -> None:
    """Reject project plan in background and PATCH Discord."""
    webhook_url = f"https://discord.com/api/v10/webhooks/{application_id}/{token}/messages/@original"

    try:
        async with asyncio.timeout(10.0):
            async with session_scope() as session:
                server = await member_service.resolve_server(
                    session, ServerContext(discord_guild_id=guild_id)
                )

                project = await project_service.get_project_by_key(session, server.id, project_key)
                if not project or str(project.server_id) != str(server.id):
                    patch_body = {"content": "⚠️ Permission denied: Project not found on this server.", "components": []}
                else:
                    creator_id = project.created_by_user_id
                    if creator_id and creator_id != "<legacy>" and creator_id != user_id:
                        patch_body = {
                            "content": f"⚠️ Only the project creator (<@{creator_id}>) can do this.",
                            "components": [],
                        }
                    else:
                        project.status = ProjectStatus.ARCHIVED
                        approval = ProjectPlanApproval(
                            project_id=project.id,
                            approved_by_user_id=user_id,
                            approved_at=datetime.now(timezone.utc),
                            action="rejected",
                        )
                        session.add(approval)
                        await session.commit()

                        patch_body = {
                            "content": f"❌ Plan rejected for project **{project.name}** (`{project_key}`). Run `/project create` again to start over.",
                            "components": [],
                        }

    except Exception as exc:
        logger.exception("Failed rejecting project %s", project_key)
        patch_body = {"content": f"⚠️ Rejection failed: {exc}", "components": []}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.patch(webhook_url, json=patch_body)
    except Exception as exc:
        logger.error("Failed to send rejection PATCH for %s: %s", project_key, exc)


@router.post("/interactions")
async def discord_interactions(
    request: Request, background_tasks: BackgroundTasks
) -> dict[str, Any]:
    raw_body = await request.body()
    signature = request.headers.get("X-Signature-Ed25519")
    timestamp = request.headers.get("X-Signature-Timestamp")

    verify_signature(raw_body, signature, timestamp)

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid json body")

    interaction_type = body.get("type")

    # Type 1: PING
    if interaction_type == 1:
        return {"type": 1}

    # Type 2: APPLICATION_COMMAND
    if interaction_type == 2:
        application_id = str(body.get("application_id") or settings.discord_application_id or "")
        token = str(body.get("token") or "")
        data = body.get("data", {})
        command_name = data.get("name", "")
        guild_id = str(body.get("guild_id") or "unknown-guild")
        channel_id = str(body.get("channel_id") or "unknown-channel")
        member = body.get("member", {})
        user = member.get("user", {}) or body.get("user", {})
        user_id = str(user.get("id") or "unknown-user")

        subcommand, args = _parse_options(data.get("options"))
        full_command = f"{command_name} {subcommand}" if subcommand else command_name

        # Command: /project create -> Post User Select message with Continue button (Item 1)
        if full_command in ("project create", "project_create"):
            proj_name = args.get("name") or args.get("project_name") or "New Project"
            session_key = f"{channel_id}:{user_id}"
            _PENDING_MEMBER_SELECTIONS[session_key] = []
            _PENDING_MEMBER_DETAILS[session_key] = {}
            return {
                "type": 4,
                "data": {
                    "content": f"📋 **Create Project: {proj_name}**\nPlease select team members for this project below, then click **Continue ➡️**.",
                    "components": [
                        {
                            "type": 1,
                            "components": [
                                {
                                    "type": 5,  # User Select (MULTI)
                                    "custom_id": f"proj_user_select:{session_key}:{proj_name}",
                                    "placeholder": "Select team members...",
                                    "min_values": 1,
                                    "max_values": 25,
                                }
                            ],
                        },
                        {
                            "type": 1,
                            "components": [
                                {
                                    "type": 2,
                                    "style": 1,  # Primary Button
                                    "custom_id": f"proj_user_continue:{session_key}:{proj_name}",
                                    "label": "Continue ➡️",
                                }
                            ],
                        },
                    ],
                },
            }

        # Command: /project add-members -> Open Modal
        if full_command in ("project add-members", "project add_members"):
            return {
                "type": 9,
                "data": {
                    "custom_id": "project_add_members_modal",
                    "title": "Add Members to Project",
                    "components": [
                        {
                            "type": 1,
                            "components": [
                                {
                                    "type": 4,
                                    "custom_id": "project_key",
                                    "label": "Project Key",
                                    "style": 1,
                                    "required": True,
                                    "placeholder": "e.g. PROJ",
                                }
                            ],
                        },
                        {
                            "type": 1,
                            "components": [
                                {
                                    "type": 8,  # User Select MULTI
                                    "custom_id": "team_members",
                                    "label": "Members to Add",
                                    "min_values": 1,
                                    "max_values": 25,
                                    "required": True,
                                }
                            ],
                        },
                    ],
                },
            }

        # Standard slash commands: schedule background execution & return type 5
        context = {
            "discord_guild_id": guild_id,
            "discord_channel_id": channel_id,
            "requested_by_discord_id": user_id,
        }
        background_tasks.add_task(
            _execute_and_patch, application_id, token, full_command, args, context
        )
        return {"type": 5}

    # Type 5: MODAL_SUBMIT
    if interaction_type == 5:
        application_id = str(body.get("application_id") or settings.discord_application_id or "")
        token = str(body.get("token") or "")
        data = body.get("data", {})
        custom_id = data.get("custom_id", "")
        guild_id = str(body.get("guild_id") or "unknown-guild")
        channel_id = str(body.get("channel_id") or "unknown-channel")
        member = body.get("member", {})
        user = member.get("user", {}) or body.get("user", {})
        user_id = str(user.get("id") or "unknown-user")

        server_ctx = ServerContext(
            discord_guild_id=guild_id,
            discord_channel_id=channel_id,
            requested_by_discord_id=user_id,
        )

        if custom_id.startswith("proj_create_modal_3field:") or custom_id == "project_create_modal":
            # Extract session_key if encoded in custom_id
            session_key = custom_id.split(":", 1)[1] if ":" in custom_id else f"{channel_id}:{user_id}"
            
            # Extract 3 fields from modal submit (Name, Description, Constraints)
            components_rows = data.get("components", [])
            name = ""
            description = ""
            constraints = None

            for row in components_rows:
                for comp in row.get("components", []):
                    c_id = comp.get("custom_id")
                    if c_id == "name":
                        name = comp.get("value", "").strip()
                    elif c_id == "description":
                        description = comp.get("value", "").strip()
                    elif c_id == "constraints":
                        constraints = comp.get("value", "").strip() or None

            logger.info("modal submit: name=%r desc_len=%d constraints=%r", name, len(description), constraints)

            # Retrieve member_discord_ids & member_details from prior UserSelect step
            member_discord_ids = _PENDING_MEMBER_SELECTIONS.get(session_key, [])
            member_details = _PENDING_MEMBER_DETAILS.get(session_key, {})
            if not member_discord_ids:
                member_discord_ids = [user_id]

            # Save pending modal session for Amendment 1 follow-up file upload flow
            _PENDING_MODAL_SESSIONS[session_key] = {
                "application_id": application_id,
                "token": token,
                "server_ctx": server_ctx,
                "name": name,
                "description": description,
                "member_discord_ids": member_discord_ids,
                "constraints": constraints,
                "member_details": member_details,
            }

            # Return type 4 with Skip File Upload button
            return {
                "type": 4,
                "data": {
                    "content": (
                        f"Project **{name}** details received!\n"
                        "Have a spec/brief file? Reply with an attachment (.pdf, .docx, .txt, .md) or click **[Skip File Upload]** below."
                    ),
                    "components": [
                        {
                            "type": 1,
                            "components": [
                                {
                                    "type": 2,
                                    "style": 2,
                                    "custom_id": f"skip_file_upload:{session_key}",
                                    "label": "Skip File Upload ⏭️",
                                }
                            ],
                        }
                    ],
                },
            }

        if custom_id == "project_add_members_modal":
            components_rows = data.get("components", [])
            project_key = ""
            user_ids: list[str] = []
            for row in components_rows:
                for comp in row.get("components", []):
                    c_id = comp.get("custom_id")
                    if c_id == "project_key":
                        project_key = comp.get("value", "").strip()
                    elif c_id == "team_members":
                        user_ids = comp.get("values", [])

            args = {"project_key": project_key, "user_ids": " ".join(user_ids)}
            context = server_ctx.model_dump()
            background_tasks.add_task(
                _execute_and_patch, application_id, token, "project add-members", args, context
            )
            return {"type": 5}

    # Type 3: MESSAGE_COMPONENT (Button Clicks & Select Menus)
    if interaction_type == 3:
        application_id = str(body.get("application_id") or settings.discord_application_id or "")
        token = str(body.get("token") or "")
        data = body.get("data", {})
        custom_id = data.get("custom_id", "")
        guild_id = str(body.get("guild_id") or "unknown-guild")
        channel_id = str(body.get("channel_id") or "unknown-channel")
        user = body.get("member", {}).get("user", {}) or body.get("user", {})
        user_id = str(user.get("id") or "unknown-user")

        # UserSelect Selection Event: Save selected user IDs and resolved details to pending cache
        if custom_id.startswith("proj_user_select:"):
            parts = custom_id.split(":", 2)
            session_key = parts[1] if len(parts) > 1 else f"{channel_id}:{user_id}"
            selected_uids = data.get("values", [])
            resolved = data.get("resolved", {})
            resolved_users = resolved.get("users", {})
            resolved_members = resolved.get("members", {})

            details = {}
            for uid in selected_uids:
                u_info = resolved_users.get(uid, {})
                m_info = resolved_members.get(uid, {})
                username = u_info.get("username") or f"user_{uid}"
                disp_name = m_info.get("nick") or u_info.get("global_name") or username
                details[uid] = {"username": username, "display_name": disp_name}

            _PENDING_MEMBER_SELECTIONS[session_key] = selected_uids
            _PENDING_MEMBER_DETAILS[session_key] = details
            logger.info("Saved user selection for %s: %s (%s)", session_key, selected_uids, details)
            return {"type": 6}  # DEFERRED_UPDATE_MESSAGE

        # Continue Button Click: Open 3-Field Modal (Item 1)
        if custom_id.startswith("proj_user_continue:"):
            parts = custom_id.split(":", 2)
            session_key = parts[1] if len(parts) > 1 else f"{channel_id}:{user_id}"
            proj_name = parts[2] if len(parts) > 2 else "New Project"
            return {
                "type": 9,
                "data": {
                    "custom_id": f"proj_create_modal_3field:{session_key}",
                    "title": f"Create Project: {proj_name[:20]}",
                    "components": [
                        {
                            "type": 1,
                            "components": [
                                {
                                    "type": 4,
                                    "custom_id": "name",
                                    "label": "Project Name",
                                    "style": 1,
                                    "min_length": 1,
                                    "max_length": 80,
                                    "required": True,
                                    "value": proj_name,
                                }
                            ],
                        },
                        {
                            "type": 1,
                            "components": [
                                {
                                    "type": 4,
                                    "custom_id": "description",
                                    "label": "Description / Brief / Topic",
                                    "style": 2,
                                    "min_length": 1,
                                    "max_length": 4000,
                                    "required": True,
                                    "placeholder": "Describe the project brief, tasks, or topic.",
                                }
                            ],
                        },
                        {
                            "type": 1,
                            "components": [
                                {
                                    "type": 4,
                                    "custom_id": "constraints",
                                    "label": "Constraints",
                                    "style": 2,
                                    "required": False,
                                    "max_length": 1000,
                                    "placeholder": "e.g. 'Rahul doesn't know backend', 'deadline 2 weeks'",
                                }
                            ],
                        },
                    ],
                },
            }

        # Button: Skip File Upload
        if custom_id.startswith("skip_file_upload:"):
            session_key = custom_id.split(":", 1)[1]
            sess_data = _PENDING_MODAL_SESSIONS.pop(session_key, None)
            if sess_data:
                background_tasks.add_task(
                    _execute_and_patch_project_plan,
                    sess_data["application_id"],
                    sess_data["token"],
                    sess_data["server_ctx"],
                    sess_data["name"],
                    sess_data["description"],
                    sess_data["member_discord_ids"],
                    sess_data["constraints"],
                    None,
                    None,
                    sess_data.get("member_details"),
                )
                return {
                    "type": 7,
                    "data": {
                        "content": f"⏳ Planning project **{sess_data['name']}**... Generating tasks and calculated assignments.",
                        "components": [],
                    },
                }
            return {"type": 7, "data": {"content": "⏳ Planning project...", "components": []}}

        # Button: Approve Project (Background defer pattern to prevent Discord 3s timeout)
        if custom_id.startswith("approve_project:"):
            project_key = custom_id.split(":", 1)[1]
            background_tasks.add_task(
                _execute_and_patch_approve_project,
                application_id,
                token,
                guild_id,
                user_id,
                project_key,
            )
            return {"type": 6}  # DEFERRED_UPDATE_MESSAGE

        # Button: Reject Project (Background defer pattern)
        if custom_id.startswith("reject_project:"):
            project_key = custom_id.split(":", 1)[1]
            background_tasks.add_task(
                _execute_and_patch_reject_project,
                application_id,
                token,
                guild_id,
                user_id,
                project_key,
            )
            return {"type": 6}  # DEFERRED_UPDATE_MESSAGE

        # Button: Edit Project
        if custom_id.startswith("edit_project:"):
            return {
                "type": 4,
                "data": {
                    "content": "✏️ Inline plan editing is coming soon! For now, please run `/project create` again with your updated prompt or constraints.",
                    "flags": 64,
                },
            }

    return {"type": 4, "data": {"content": "Unsupported interaction type."}}
