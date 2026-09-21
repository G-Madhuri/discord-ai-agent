"""Planning tools for Phase 6 agentic project creation."""

from __future__ import annotations

from typing import Any

from app.core.errors import DomainError
from app.core.logging import get_logger
from app.services import member_service, task_service
from app.services.document_parser import parse_document
from app.tools.context import ToolContext

logger = get_logger(__name__)


async def list_server_members_for_planning(ctx: ToolContext) -> dict[str, Any]:
    """Fetch compact summary of all server members (skills, roles, active workload)
    so the LLM can reason over team capabilities during project planning.
    """
    try:
        async with ctx.session() as session:
            members = await member_service.list_server_members(session, ctx.server_id)
            summaries = []
            for m in members:
                skills_list = [f"{s.skill.name} ({s.proficiency}/5)" for s in m.skills]
                active_tasks = await task_service.count_active_member_tasks(session, ctx.server_id, m.id)
                summaries.append(
                    {
                        "member_id": str(m.id),
                        "discord_user_id": m.discord_user_id,
                        "display_name": m.display_name,
                        "username": m.username,
                        "role": m.role,
                        "skills": skills_list,
                        "active_task_count": active_tasks,
                        "availability": m.availability.value,
                    }
                )
            return {"ok": True, "members": summaries}
    except DomainError as exc:
        return {"ok": False, "error": {"code": exc.code, "message": exc.message}}


def parse_uploaded_document(file_bytes: bytes, filename: str) -> dict[str, Any]:
    """Parse an uploaded project brief or specification document (.pdf, .docx, .txt, .md)."""
    try:
        res = parse_document(file_bytes, filename)
        return {"ok": True, **res}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
