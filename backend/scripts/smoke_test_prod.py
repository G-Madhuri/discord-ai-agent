"""Post-deployment smoke test script for production Cloud Run deployment.

Verifies:
1. Public /health endpoint (200 {"status":"ok"})
2. Production Auth Guard (/api/v1/* without token -> 401)
3. Production Signature Verification (/discord/interactions with bad sig -> 401)
4. Full Slash Command Workflow (project create, member add, project member add, task create, assign, history)
5. Production Cross-Guild Isolation (unregistered guild -> 404)

Usage:
  python backend/scripts/smoke_test_prod.py [SERVICE_URL]
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
import uuid
import httpx

# Force UTF-8 encoding on Windows
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

# Ensure backend directory is in path
sys.path.insert(0, os.path.abspath("backend"))

from app.core.config import settings
from app.discord.api_client import BackendClient
from app.discord.command_handler import handle_command

PROD_URL = (
    sys.argv[1]
    if len(sys.argv) > 1
    else "https://discord-agent-499979613721.us-central1.run.app"
)
GUILD_ID = settings.discord_guild_id or "1551394254557941840"
USER_ID = "999000111"


async def run_prod_verifications() -> None:
    print(f"=== Running Production Smoke Verification against {PROD_URL} ===")

    # 1. Health check & cold start latency
    t0 = time.time()
    async with httpx.AsyncClient(base_url=PROD_URL, timeout=30.0) as client:
        res_h = await client.get("/health")
        latency = time.time() - t0
        print(
            f"1. /health response: {res_h.status_code} {res_h.text.strip()} "
            f"(Response latency: {latency:.2f}s)"
        )
        assert res_h.status_code == 200
        assert res_h.json() == {"status": "ok"}

    # 2. Auth Guard
    print("\n--- 2. Auth Guard Production Test ---")
    async with httpx.AsyncClient(base_url=PROD_URL, timeout=30.0) as client:
        res_auth = await client.post(
            "/api/v1/projects/list", json={"discord_guild_id": GUILD_ID}
        )
        print(f"Request without X-Internal-Token status: {res_auth.status_code}")
        assert res_auth.status_code == 401
        print("[PASS] Production Auth Guard rejected unauthenticated request with 401.")

    # 3. Signature Verification
    print("\n--- 3. Signature Verification Production Test ---")
    async with httpx.AsyncClient(base_url=PROD_URL, timeout=30.0) as client:
        headers = {
            "X-Signature-Ed25519": "00" * 64,
            "X-Signature-Timestamp": "1234567890",
        }
        res_sig = await client.post(
            "/discord/interactions", content=b'{"type": 1}', headers=headers
        )
        print(f"Request with bad signature status: {res_sig.status_code}")
        assert res_sig.status_code == 401
        print(
            "[PASS] Production Signature Verification rejected bad signature with 401."
        )

    # 4. E2E Command Workflow
    print("\n--- 4. Full E2E Slash Command Production Workflow ---")
    b_client = BackendClient(base_url=PROD_URL)
    await b_client.start()

    ctx = {
        "discord_guild_id": GUILD_ID,
        "discord_channel_id": "888000111",
        "requested_by_discord_id": USER_ID,
    }

    # Step A: /project create
    proj_key = f"PROD{str(uuid.uuid4())[:4].upper()}"
    msg_a, err_a = await handle_command(
        b_client, "project create", {"name": "Smoke Test", "key": proj_key}, ctx
    )
    print(f"Step A (/project create): {msg_a}")
    assert not err_a

    # Step B: /member add
    msg_b, err_b = await handle_command(
        b_client,
        "member add",
        {
            "user": USER_ID,
            "display_name": "Smoke User",
            "role": "QA Engineer",
            "skills": "Python, FastAPI",
        },
        ctx,
    )
    print(f"Step B (/member add): {msg_b}")
    assert not err_b

    # Step C: /project member add
    msg_c, err_c = await handle_command(
        b_client,
        "project member add",
        {"project": proj_key, "user": USER_ID, "role": "QA Engineer"},
        ctx,
    )
    print(f"Step C (/project member add): {msg_c}")
    assert not err_c

    # Step D: /task create
    task_key = f"TASK-{str(uuid.uuid4())[:4].upper()}"
    msg_d, err_d = await handle_command(
        b_client,
        "task create",
        {
            "project": proj_key,
            "key": task_key,
            "title": "Smoke Task",
            "skills": "FastAPI",
        },
        ctx,
    )
    print(f"Step D (/task create): {msg_d}")
    assert not err_d

    # Step E: /assign
    msg_e, err_e = await handle_command(
        b_client, "assign", {"task_id": task_key}, ctx
    )
    print(f"Step E (/assign {task_key}):\n{msg_e}")
    assert not err_e
    assert "Smoke User" in msg_e

    # Step F: /assignment history
    msg_f, err_f = await handle_command(
        b_client, "assignment history", {"task_id": task_key}, ctx
    )
    print(f"Step F (/assignment history {task_key}):\n{msg_f}")
    assert not err_f
    assert "assigned" in msg_f or "TASK-" in msg_f

    # 5. Cross-Guild Isolation
    print("\n--- 5. Cross-Guild Production Test ---")
    unregistered_ctx = {
        "discord_guild_id": "unregistered-prod-guild-999",
        "discord_channel_id": "000000",
        "requested_by_discord_id": USER_ID,
    }
    msg_cg, err_cg = await handle_command(
        b_client, "assign", {"task_id": task_key}, unregistered_ctx
    )
    print(f"Cross-guild /assign reply: {msg_cg}")
    assert err_cg is True
    print("[PASS] Production Cross-Guild Isolation Test PASSED.")

    await b_client.close()
    print("\n=== ALL PRODUCTION SMOKE VERIFICATIONS PASSED PERFECTLY ===")


if __name__ == "__main__":
    asyncio.run(run_prod_verifications())
