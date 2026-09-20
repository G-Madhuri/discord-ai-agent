"""Seed one demo Discord server so /assign can be exercised end to end.

Run against a local database:  python scripts/seed_demo.py
Safe to re-run: it upserts by key.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.db.session import session_scope  # noqa: E402
from app.models.enums import DocumentSourceType  # noqa: E402
from app.schemas.common import ServerContext  # noqa: E402
from app.schemas.member import MemberCreate, SkillInput  # noqa: E402
from app.schemas.project import DocumentCreate, ProjectCreate  # noqa: E402
from app.schemas.task import TaskCreate, TaskSkillInput  # noqa: E402
from app.services import knowledge_service, member_service, project_service, task_service  # noqa: E402
from app.services.server_service import resolve_server  # noqa: E402

GUILD_ID = "demo-guild-1"
CONTEXT = ServerContext(discord_guild_id=GUILD_ID, guild_name="Demo Server")

TEAM = [
    ("101", "rahul", "Rahul", "Frontend Engineer", ["React", "JavaScript", "Frontend"]),
    ("102", "ananya", "Ananya", "ML Engineer", ["Python", "NLP", "ML"]),
    ("103", "madhuri", "Madhuri", "Backend Engineer", ["FastAPI", "Python", "GenAI"]),
    ("104", "arjun", "Arjun", "Backend Engineer", ["PostgreSQL", "SQL", "Backend"]),
]

TASKS = [
    ("TASK-101", "Build React dashboard", ["React", "JavaScript"], "Frontend"),
    ("TASK-102", "Build NLP classifier", ["NLP", "Python"], "ML"),
    ("TASK-103", "Build FastAPI service", ["FastAPI", "Python"], "Backend"),
    ("TASK-104", "Design PostgreSQL schema", ["PostgreSQL", "SQL"], "Backend"),
]

KNOWLEDGE = """
The platform is a team analytics product.

The frontend uses React and TypeScript, and the dashboard uses Recharts for
charting. Dashboard work is owned by the frontend group.

The backend is a FastAPI service in Python. It exposes the analytics API and
talks to PostgreSQL.

Text classification of incoming feedback is handled by an NLP model trained in
Python.
"""


async def main() -> None:
    async with session_scope() as session:
        server = await resolve_server(session, CONTEXT, create=True)

        project = await project_service.get_project(session, server.id, "DASH")
        if project is None:
            project = await project_service.create_project(
                session,
                server.id,
                ProjectCreate(
                    context=CONTEXT,
                    key="DASH",
                    name="Analytics Platform",
                    description="Team analytics dashboard and services",
                ),
            )

        for discord_id, username, display, role, skills in TEAM:
            member = await member_service.create_or_update_member(
                session,
                server.id,
                MemberCreate(
                    context=CONTEXT,
                    discord_user_id=discord_id,
                    username=username,
                    display_name=display,
                    role=role,
                    skills=[SkillInput(name=s, proficiency=5) for s in skills],
                ),
            )
            await project_service.add_project_member(session, project, member, project_role=role)

        for key, title, skills, role_hint in TASKS:
            if await task_service.get_task(session, server.id, key, project_id=project.id):
                continue
            await task_service.create_task(
                session,
                server.id,
                project,
                TaskCreate(
                    context=CONTEXT,
                    project_key="DASH",
                    key=key,
                    title=title,
                    role_hint=role_hint,
                    required_skills=[TaskSkillInput(name=s) for s in skills],
                ),
            )

        await knowledge_service.ingest_document(
            session,
            project,
            DocumentCreate(
                context=CONTEXT,
                project_key="DASH",
                title="Technical Overview",
                content=KNOWLEDGE,
                source_type=DocumentSourceType.ARCHITECTURE,
            ),
        )

    print(f"Seeded guild {GUILD_ID}: project DASH, {len(TEAM)} members, {len(TASKS)} tasks.")


if __name__ == "__main__":
    asyncio.run(main())
