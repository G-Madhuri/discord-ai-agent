"""Server (Discord guild) resolution — the entry point of every scoped flow."""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ServerScopeError
from app.models.server import Server
from app.schemas.common import ServerContext


async def get_server_by_guild(session: AsyncSession, discord_guild_id: str) -> Server | None:
    result = await session.execute(
        sa.select(Server).where(Server.discord_guild_id == discord_guild_id)
    )
    return result.scalar_one_or_none()


async def resolve_server(
    session: AsyncSession, context: ServerContext, *, create: bool = False
) -> Server:
    """Resolve the guild a request belongs to.

    Every service call downstream takes the returned `Server.id` and filters on
    it, which is how data from one Discord server stays out of another's.
    """
    server = await get_server_by_guild(session, context.discord_guild_id)
    if server is None:
        if not create:
            raise NotFoundError(
                f"Discord server {context.discord_guild_id} is not registered. "
                "Run /project create (or POST /servers) first.",
                details={"discord_guild_id": context.discord_guild_id},
            )
        server = Server(
            discord_guild_id=context.discord_guild_id,
            name=context.guild_name or f"guild-{context.discord_guild_id}",
        )
        session.add(server)
        await session.flush()
    elif context.guild_name and server.name != context.guild_name:
        server.name = context.guild_name
    return server


def assert_same_server(server_id: uuid.UUID, entity_server_id: uuid.UUID, label: str) -> None:
    """Defensive check for code paths that take an id from the caller.

    Queries already filter by server_id; this catches the case where an id was
    supplied directly (e.g. by an agent tool argument) and belongs elsewhere.
    """
    if server_id != entity_server_id:
        raise ServerScopeError(
            f"{label} belongs to a different Discord server",
            details={"expected_server_id": str(server_id)},
        )
