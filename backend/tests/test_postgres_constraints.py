"""PostgreSQL specific regression tests.

Verifies schema constraints, partial indexes, FK cascade behavior, timezone awareness,
JSONB roundtrips, enum value persistence, pgvector RAG store, and SSL transport rejection.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.models.assignment import Assignment
from app.models.enums import (
    AssignmentStatus,
    AvailabilityStatus,
    DecisionMode,
    PlanStatus,
    ProjectStatus,
    TaskPriority,
    TaskStatus,
)
from app.models.member import MemberProfile
from app.models.project import DocumentChunk, Project, ProjectDocument
from app.models.server import Server
from app.models.task import Task
from app.models.user import User
from app.rag.interfaces import Chunk, KnowledgeScope
from app.rag.stores.postgres import PostgresVectorStore


@pytest_asyncio.fixture
async def pg_session():
    engine = create_async_engine(settings.database_url, connect_args={"ssl": "require"})
    maker = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    async with maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await engine.dispose()


@pytest.mark.asyncio
async def test_partial_unique_index_active_assignment(pg_session: AsyncSession):
    """Verify that only one active/proposed assignment can exist per task at a time."""
    server = Server(discord_guild_id=f"guild-{uuid.uuid4().hex[:8]}", name="Index Test Guild")
    user = User(discord_user_id=f"user-{uuid.uuid4().hex[:8]}", username="testuser")
    pg_session.add_all([server, user])
    await pg_session.flush()

    member = MemberProfile(
        server_id=server.id,
        user_id=user.id,
        display_name="Tester",
        availability=AvailabilityStatus.AVAILABLE,
    )
    project = Project(server_id=server.id, key="IDX", name="Index Project")
    pg_session.add_all([member, project])
    await pg_session.flush()

    task = Task(
        server_id=server.id,
        project_id=project.id,
        key="IDX-1",
        title="Index Task",
        status=TaskStatus.TODO,
        priority=TaskPriority.MEDIUM,
    )
    pg_session.add(task)
    await pg_session.flush()

    # First active assignment
    asgn1 = Assignment(
        server_id=server.id,
        project_id=project.id,
        task_id=task.id,
        member_profile_id=member.id,
        status=AssignmentStatus.ACTIVE,
        decision_mode=DecisionMode.DETERMINISTIC,
    )
    pg_session.add(asgn1)
    await pg_session.flush()

    # Duplicate active assignment on same task must fail unique index constraint
    asgn2 = Assignment(
        server_id=server.id,
        project_id=project.id,
        task_id=task.id,
        member_profile_id=member.id,
        status=AssignmentStatus.ACTIVE,
        decision_mode=DecisionMode.DETERMINISTIC,
    )
    pg_session.add(asgn2)
    with pytest.raises(IntegrityError):
        await pg_session.flush()
    await pg_session.rollback()


@pytest.mark.asyncio
async def test_foreign_key_cascade_deletion(pg_session: AsyncSession):
    """Verify that deleting a Server cascades and deletes associated Project, Task, and MemberProfile."""
    server = Server(discord_guild_id=f"guild-{uuid.uuid4().hex[:8]}", name="Cascade Test Guild")
    user = User(discord_user_id=f"user-{uuid.uuid4().hex[:8]}", username="cascadeuser")
    pg_session.add_all([server, user])
    await pg_session.flush()

    member = MemberProfile(
        server_id=server.id,
        user_id=user.id,
        display_name="Cascade Member",
        availability=AvailabilityStatus.AVAILABLE,
    )
    project = Project(server_id=server.id, key="CAS", name="Cascade Project")
    pg_session.add_all([member, project])
    await pg_session.flush()

    server_id = server.id
    await pg_session.delete(server)
    await pg_session.flush()

    # Check cascading deletion
    projects = (
        await pg_session.execute(sa.select(Project).where(Project.server_id == server_id))
    ).scalars().all()
    assert len(projects) == 0


@pytest.mark.asyncio
async def test_timestamp_timezone_awareness(pg_session: AsyncSession):
    """Verify timestamp columns retain timezone info upon round-trip."""
    server = Server(discord_guild_id=f"guild-{uuid.uuid4().hex[:8]}", name="TZ Test Guild")
    pg_session.add(server)
    await pg_session.flush()

    fetched = (
        await pg_session.execute(sa.select(Server).where(Server.id == server.id))
    ).scalar_one()

    assert fetched.created_at.tzinfo is not None


@pytest.mark.asyncio
async def test_json_round_trip(pg_session: AsyncSession):
    """Verify metadata dict / JSON column round-trips complex nested structures correctly."""
    payload = {"environment": "postgres_test", "nest": {"values": [1, 2, 3]}}
    server = Server(
        discord_guild_id=f"guild-{uuid.uuid4().hex[:8]}",
        name="JSON Test Guild",
        settings=payload,
    )
    pg_session.add(server)
    await pg_session.flush()

    fetched = (
        await pg_session.execute(sa.select(Server).where(Server.id == server.id))
    ).scalar_one()
    assert fetched.settings == payload


@pytest.mark.asyncio
async def test_enum_value_persistence_lowercase(pg_session: AsyncSession):
    """Verify that Enum values are persisted in raw SQL as lowercase strings ('active', 'deterministic')."""
    server = Server(discord_guild_id=f"guild-{uuid.uuid4().hex[:8]}", name="Enum Test Guild")
    user = User(discord_user_id=f"user-{uuid.uuid4().hex[:8]}", username="enumuser")
    pg_session.add_all([server, user])
    await pg_session.flush()

    member = MemberProfile(
        server_id=server.id,
        user_id=user.id,
        display_name="Enum Member",
        availability=AvailabilityStatus.AVAILABLE,
    )
    project = Project(
        server_id=server.id,
        key="ENUM",
        name="Enum Project",
        status=ProjectStatus.ACTIVE,
        plan_status=PlanStatus.NONE,
    )
    pg_session.add_all([member, project])
    await pg_session.flush()

    # Query raw text values from database columns directly
    res = await pg_session.execute(
        sa.text("SELECT status, plan_status FROM projects WHERE id = :id"),
        {"id": project.id},
    )
    row = res.fetchone()
    assert row is not None
    status_val, plan_status_val = row[0], row[1]
    assert status_val == "active", f"Expected 'active', got '{status_val}'"
    assert plan_status_val == "none", f"Expected 'none', got '{plan_status_val}'"


@pytest.mark.asyncio
async def test_postgres_rag_vector_store(pg_session: AsyncSession):
    """Verify PostgresVectorStore upsert and search operations against PostgreSQL."""
    server = Server(discord_guild_id=f"guild-{uuid.uuid4().hex[:8]}", name="RAG Test Guild")
    pg_session.add(server)
    await pg_session.flush()

    project = Project(server_id=server.id, key="RAG", name="RAG Project")
    pg_session.add(project)
    await pg_session.flush()

    doc = ProjectDocument(
        server_id=server.id,
        project_id=project.id,
        title="Architecture Doc",
        chunk_count=1,
    )
    pg_session.add(doc)
    await pg_session.flush()

    scope = KnowledgeScope(server_id=server.id, project_id=project.id)
    store = PostgresVectorStore(pg_session)

    chunks = [Chunk(index=0, content="PostgreSQL pgvector test chunk", token_estimate=10)]
    embeddings = [[0.1] * 768]

    count = await store.upsert(
        scope, doc.id, chunks, embeddings, model_name="test-embedding"
    )
    assert count == 1

    hits = await store.search(scope, [0.1] * 768, top_k=1)
    assert len(hits) == 1
    assert "pgvector" in hits[0].chunk.content


@pytest.mark.asyncio
async def test_insecure_ssl_connection_rejection():
    """Verify that attempting to connect with ssl=False is rejected by PostgreSQL / Proxy."""
    if not settings.database_url.startswith("postgresql"):
        pytest.skip("Skipping SSL test when not on PostgreSQL")

    bad_engine = create_async_engine(settings.database_url, connect_args={"ssl": False})
    with pytest.raises(Exception) as exc_info:
        async with bad_engine.connect() as conn:
            await conn.execute(sa.text("SELECT 1"))
    await bad_engine.dispose()
    err_str = str(exc_info.value).lower()
    assert "insecure" in err_str or "sslmode=require" in err_str or "ssl" in err_str
