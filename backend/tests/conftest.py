"""Test fixtures.

Tests run against an in-memory SQLite database. The models use dialect-neutral
column types precisely so this works; the PostgreSQL-specific behaviour that
matters (partial unique index on live assignments) is declared for both
dialects on the model.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db import session as session_module
from app.models import Base
from app.models.enums import AvailabilityStatus
from app.models.member import MemberProfile
from app.models.project import Project, ProjectMember
from app.models.server import Server
from app.models.skill import MemberSkill, Skill
from app.models.task import Task, TaskSkill
from app.models.user import User

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def engine():
    engine = create_async_engine(TEST_DATABASE_URL, future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def sessionmaker_(engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


@pytest_asyncio.fixture
async def session(sessionmaker_, monkeypatch) -> AsyncIterator[AsyncSession]:
    # Point the application's session factory at the test database so agent
    # tools (which open their own sessions) hit the same engine.
    monkeypatch.setattr(session_module, "_sessionmaker", sessionmaker_, raising=False)
    async with sessionmaker_() as s:
        yield s
        await s.commit()


async def make_skill(
    session: AsyncSession, slug: str, name: str, category: str | None = None
) -> Skill:
    skill = Skill(slug=slug, name=name, category=category)
    session.add(skill)
    await session.flush()
    return skill


async def make_server(session: AsyncSession, guild_id: str, name: str) -> Server:
    server = Server(discord_guild_id=guild_id, name=name)
    session.add(server)
    await session.flush()
    return server


async def make_project(session: AsyncSession, server: Server, key: str, name: str) -> Project:
    project = Project(server_id=server.id, key=key, name=name)
    session.add(project)
    await session.flush()
    return project


async def make_member(
    session: AsyncSession,
    server: Server,
    project: Project | None,
    *,
    display_name: str,
    role: str | None = None,
    skills: list[tuple[Skill, int]] | None = None,
    availability: AvailabilityStatus = AvailabilityStatus.AVAILABLE,
    capacity: int | None = None,
    years_experience: float | None = None,
) -> MemberProfile:
    user = User(discord_user_id=str(uuid.uuid4().int)[:18], username=display_name.lower())
    session.add(user)
    await session.flush()

    member = MemberProfile(
        server_id=server.id,
        user_id=user.id,
        display_name=display_name,
        role=role,
        availability=availability,
        max_concurrent_tasks=capacity,
        years_experience=years_experience,
    )
    session.add(member)
    await session.flush()

    for skill, proficiency in skills or []:
        session.add(
            MemberSkill(member_profile_id=member.id, skill_id=skill.id, proficiency=proficiency)
        )
    if project is not None:
        session.add(
            ProjectMember(server_id=server.id, project_id=project.id, member_profile_id=member.id)
        )
    await session.flush()
    await session.refresh(member)
    return member


async def make_task(
    session: AsyncSession,
    server: Server,
    project: Project,
    *,
    key: str,
    title: str,
    skills: list[Skill] | None = None,
    role_hint: str | None = None,
) -> Task:
    task = Task(
        server_id=server.id, project_id=project.id, key=key, title=title, role_hint=role_hint
    )
    session.add(task)
    await session.flush()
    for skill in skills or []:
        session.add(TaskSkill(task_id=task.id, skill_id=skill.id))
    await session.flush()
    await session.refresh(task)
    return task


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
