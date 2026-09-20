"""Entry point the API uses to run an assignment request.

Two modes share the same tools and the same persistence path:

* `deterministic` — the scoring engine decides and the service persists. No
  model call, fully reproducible. This is the default and what the first
  milestone runs on.
* `llm` — a Gemini agent (Google ADK) reasons over the same tools and calls
  `assign_task` itself.

Both write the same evidence, so an assignment made in either mode is audited
the same way.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import settings
from app.core.errors import DomainError
from app.core.logging import get_logger
from app.models.enums import DecisionMode
from app.tools.context import ToolContext
from app.tools.toolset import build_toolset

logger = get_logger(__name__)


@dataclass(slots=True)
class AgentReply:
    message: str
    mode: str
    data: dict | None = None
    ok: bool = True


async def run_assignment(
    ctx: ToolContext,
    task_key: str,
    *,
    reassign: bool = False,
    member_discord_id: str | None = None,
    note: str | None = None,
) -> AgentReply:
    """Assign one existing task. Never plans, never creates tasks."""
    if settings.agent_mode == "llm":
        return await _run_llm(
            ctx.scoped(decision_mode=DecisionMode.LLM),
            f"Assign {task_key}."
            + (f" Assign it to <@{member_discord_id}>." if member_discord_id else "")
            + (" Reassign if it already has an assignee." if reassign else "")
            + (f" Note: {note}" if note else ""),
        )

    tools = build_toolset(ctx.scoped(decision_mode=DecisionMode.DETERMINISTIC))
    result = await tools["assign_task"](
        task_key=task_key,
        member_discord_id=member_discord_id,
        reassign=reassign,
        note=note,
    )
    if not result.get("ok"):
        error = result["error"]
        return AgentReply(
            message=f"⚠️ {error['message']}", mode="deterministic", data=result, ok=False
        )
    return AgentReply(message=result["message"], mode="deterministic", data=result)


async def _run_llm(ctx: ToolContext, prompt: str) -> AgentReply:
    """Run the ADK agent for one turn and return its final text."""
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    from app.agents.assignment_agent import build_agent

    try:
        agent = build_agent(ctx)
    except RuntimeError as exc:
        return AgentReply(message=f"⚠️ {exc}", mode="llm", ok=False)

    session_service = InMemorySessionService()
    # Sessions are keyed by guild so no conversation state crosses servers.
    app_name = settings.app_name
    user_id = ctx.requested_by_discord_id or "discord-user"
    session_id = f"{ctx.discord_guild_id}:{ctx.discord_channel_id or 'default'}"
    await session_service.create_session(app_name=app_name, user_id=user_id, session_id=session_id)

    runner = Runner(agent=agent, app_name=app_name, session_service=session_service)
    message = types.Content(role="user", parts=[types.Part(text=prompt)])

    final_text = ""
    async for event in runner.run_async(
        user_id=user_id, session_id=session_id, new_message=message
    ):
        if event.is_final_response() and event.content and event.content.parts:
            final_text = "".join(part.text or "" for part in event.content.parts)

    if not final_text:
        return AgentReply(message="⚠️ The agent did not produce a response.", mode="llm", ok=False)
    return AgentReply(message=final_text.strip(), mode="llm")


async def run_tool(ctx: ToolContext, name: str, **kwargs) -> dict:
    """Direct tool invocation, used by the bot's non-agent slash commands."""
    tools = build_toolset(ctx)
    if name not in tools:
        raise DomainError(f"Unknown tool: {name}")
    return await tools[name](**kwargs)
