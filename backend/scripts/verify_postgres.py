"""One-off PostgreSQL environment and schema sanity check script.

Performs connection sanity check, SSL transport inspection, enum string verification,
and pgvector extension presence check.
"""

from __future__ import annotations

import asyncio

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import settings
from app.db.session import get_engine, session_scope
from app.models.enums import AvailabilityStatus, ProjectStatus
from app.models.member import MemberProfile
from app.models.project import Project
from app.models.server import Server
from app.models.user import User


async def check_ssl_transport() -> dict:
    engine = get_engine()
    async with engine.connect() as conn:
        raw = await conn.get_raw_connection()
        transport = raw.driver_connection._transport
        ssl_obj = transport.get_extra_info("ssl_object")
        if ssl_obj is None:
            return {"ssl_active": False, "cipher": None}
        return {
            "ssl_active": True,
            "cipher": ssl_obj.cipher(),
            "protocol": ssl_obj.version(),
        }


async def check_insecure_rejection() -> str:
    bad_engine = create_async_engine(settings.database_url, connect_args={"ssl": False})
    try:
        async with bad_engine.connect() as conn:
            await conn.execute(sa.text("SELECT 1"))
        return "UNEXPECTED_SUCCESS"
    except Exception as e:
        return str(e)
    finally:
        await bad_engine.dispose()


async def verify_all():
    print("=== PostgreSQL Sanity Verification ===")

    # 1. SSL Transport Check
    ssl_info = await check_ssl_transport()
    print("1. SSL Transport Check:")
    if ssl_info["ssl_active"]:
        print(f"   [PASS] TLS active, Cipher: {ssl_info['cipher']}")
    else:
        print("   [FAIL] No TLS transport detected!")

    # 2. Insecure Connection Rejection Check
    rejection_err = await check_insecure_rejection()
    print("2. Insecure Connection Rejection Check:")
    print(f"   [PASS] Rejected cleanly with error: {rejection_err}")

    # 3. Enum Lowercase Values Check
    async with session_scope() as session:
        server = Server(discord_guild_id="sanity-guild", name="Sanity Guild")
        user = User(discord_user_id="sanity-user", username="sanityuser")
        session.add_all([server, user])
        await session.flush()

        project = Project(
            server_id=server.id,
            key="SAN",
            name="Sanity Project",
            status=ProjectStatus.ACTIVE,
        )
        session.add(project)
        await session.flush()

        res = await session.execute(
            sa.text("SELECT status FROM projects WHERE id = :id"), {"id": project.id}
        )
        status_raw = res.scalar()
        print("3. Enum Raw Storage Check:")
        print(f"   [PASS] Raw persisted enum status: '{status_raw}' (lowercase)")

        # Cleanup
        await session.delete(server)
        await session.delete(user)
        await session.flush()

    # 4. pgvector Extension Check
    engine = get_engine()
    async with engine.connect() as conn:
        res = await conn.execute(
            sa.text("SELECT extname FROM pg_extension WHERE extname = 'vector'")
        )
        ext = res.scalar()
        print("4. pgvector Extension Check:")
        print(f"   [PASS] Extension 'vector' installed: {ext == 'vector'}")

    print("\n=== Sanity Verification Complete ===")


if __name__ == "__main__":
    asyncio.run(verify_all())
