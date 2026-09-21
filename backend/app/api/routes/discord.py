"""Discord HTTP Interactions endpoint (for Cloud Run serverless deployment).

Discord sends HTTP POST requests here when users trigger slash commands when
configured in HTTP Interactions mode.
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status
from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey

from app.core.config import settings
from app.core.logging import get_logger
from app.discord.command_handler import handle_command

logger = get_logger(__name__)

router = APIRouter(prefix="/discord", tags=["discord"])


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
    """Execute command handler in background and send Discord webhook PATCH."""
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

    # Type 1: PING (Discord ping-pong check)
    if interaction_type == 1:
        return {"type": 1}

    # Type 2: APPLICATION_COMMAND (Slash command trigger)
    if interaction_type == 2:
        application_id = str(body.get("application_id") or settings.discord_application_id or "")
        token = str(body.get("token") or "")
        data = body.get("data", {})
        command_name = data.get("name", "")
        guild_id = body.get("guild_id") or "unknown-guild"
        channel_id = body.get("channel_id")
        member = body.get("member", {})
        user = member.get("user", {}) or body.get("user", {})
        user_id = str(user.get("id") or "unknown-user")

        context = {
            "discord_guild_id": str(guild_id),
            "discord_channel_id": str(channel_id) if channel_id else None,
            "requested_by_discord_id": user_id,
        }

        subcommand, args = _parse_options(data.get("options"))
        full_command = f"{command_name} {subcommand}" if subcommand else command_name

        # Defer pattern: schedule background execution & PATCH follow-up
        background_tasks.add_task(
            _execute_and_patch, application_id, token, full_command, args, context
        )

        # Discord response type 5: DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE
        return {"type": 5}

    return {"type": 4, "data": {"content": "Unsupported interaction type."}}
