"""Execution context for agent tools.

The LLM never supplies a server id. It is bound here, once, from the Discord
guild the request arrived on, and every tool filters by it. That is what keeps
Server A's data out of Server B's answers even if a prompt asks for it.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import session_scope
from app.models.enums import DecisionMode


@dataclass(frozen=True, slots=True)
class ToolContext:
    server_id: uuid.UUID
    discord_guild_id: str
    discord_channel_id: str | None = None
    requested_by_discord_id: str | None = None
    project_key: str | None = None
    # Recorded on every assignment so the audit trail says which path
    # actually made the decision.
    decision_mode: DecisionMode = DecisionMode.DETERMINISTIC

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with session_scope() as session:
            yield session

    def scoped(self, **overrides) -> ToolContext:
        from dataclasses import replace

        return replace(self, **overrides)
