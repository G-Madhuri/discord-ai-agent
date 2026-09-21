from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import get_db
from app.models.server import Server
from app.schemas.common import ServerContext
from app.services import server_service
from app.tools.context import ToolContext

logger = get_logger(__name__)

DbSession = Annotated[AsyncSession, Depends(get_db)]


async def verify_internal_token(
    request: Request,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> None:
    """Shared-secret check for the bot -> backend hop.

    Cloud Run keeps this service private; the token is a second layer.
    """
    expected = settings.internal_api_token
    if expected is None:
        return
    if not x_internal_token or x_internal_token != expected.get_secret_value():
        logger.warning("auth failed path=%s", request.url.path)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid internal token"
        )


async def resolve_scoped_server(
    session: AsyncSession, context: ServerContext, *, create: bool = False
) -> Server:
    return await server_service.resolve_server(session, context, create=create)


def tool_context_from(
    server: Server, context: ServerContext, project_key: str | None = None
) -> ToolContext:
    return ToolContext(
        server_id=server.id,
        discord_guild_id=server.discord_guild_id,
        discord_channel_id=context.discord_channel_id,
        requested_by_discord_id=context.requested_by_discord_id,
        project_key=project_key,
    )
