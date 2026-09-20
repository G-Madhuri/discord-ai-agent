"""End-to-end assignment workflow against a real (SQLite) database."""

from __future__ import annotations

import uuid

import pytest

from app.core.errors import ConflictError, NotFoundError, PlanImmutableError
from app.models.enums import (
    AssignmentEvent,
    AssignmentStatus,
    DecisionMode,
    DocumentSourceType,
    PlanStatus,
)
from app.schemas.common import ServerContext
from app.schemas.project import DocumentCreate
from app.services import knowledge_service, project_service
from app.services.assignment import service as assignment_service
from tests.conftest import make_member, make_project, make_server, make_skill, make_task

pytestmark = pytest.mark.asyncio


async def seed_server(session, guild_id: str = "guild-a", project_key: str = "DASH"):
    server = await make_server(session, guild_id, f"Server {guild_id}")
    project = await make_project(session, server, project_key, "Analytics Platform")

    react = await make_skill(session, "react", "React", "frontend")
    javascript = await make_skill(session, "javascript", "JavaScript", "frontend")
    python = await make_skill(session, "python", "Python", "backend")
    nlp = await make_skill(session, "nlp", "NLP", "ml")
    fastapi_skill = await make_skill(session, "fastapi", "FastAPI", "backend")
    postgres = await make_skill(session, "postgresql", "PostgreSQL", "database")

    members = {
        "rahul": await make_member(
            session,
            server,
            project,
            display_name="Rahul",
            role="Frontend Engineer",
            skills=[(react, 5), (javascript, 5)],
        ),
        "ananya": await make_member(
            session,
            server,
            project,
            display_name="Ananya",
            role="ML Engineer",
            skills=[(python, 5), (nlp, 5)],
        ),
        "madhuri": await make_member(
            session,
            server,
            project,
            display_name="Madhuri",
            role="Backend Engineer",
            skills=[(fastapi_skill, 5), (python, 4)],
        ),
        "arjun": await make_member(
            session,
            server,
            project,
            display_name="Arjun",
            role="Backend Engineer",
            skills=[(postgres, 5)],
        ),
    }

    tasks = {
        "TASK-101": await make_task(
            session,
            server,
            project,
            key="TASK-101",
            title="Build React dashboard",
            skills=[react, javascript],
            role_hint="Frontend",
        ),
        "TASK-102": await make_task(
            session, server, project, key="TASK-102", title="Build NLP classifier", skills=[nlp]
        ),
        "TASK-103": await make_task(
            session,
            server,
            project,
            key="TASK-103",
            title="Build FastAPI service",
            skills=[fastapi_skill],
        ),
        "TASK-104": await make_task(
            session,
            server,
            project,
            key="TASK-104",
            title="Design PostgreSQL schema",
            skills=[postgres],
        ),
    }
    await session.commit()
    return server, project, members, tasks


async def test_assign_existing_task_picks_the_matching_member(session):
    server, project, members, _ = await seed_server(session)

    result = await assignment_service.assign_task(
        session,
        server_id=server.id,
        task_key="TASK-101",
        requested_by_discord_id="requester-1",
    )

    assert result.assignment.member_display_name == "Rahul"
    assert result.assignment.task_key == "TASK-101"
    assert result.assignment.status is AssignmentStatus.ACTIVE
    assert result.message.startswith("✅ TASK-101 assigned to Rahul.")
    assert any("React" in reason for reason in result.reasons)


async def test_each_task_goes_to_its_specialist(session):
    server, _, _, _ = await seed_server(session)

    expected = {
        "TASK-101": "Rahul",
        "TASK-102": "Ananya",
        "TASK-103": "Madhuri",
        "TASK-104": "Arjun",
    }
    for task_key, name in expected.items():
        result = await assignment_service.assign_task(
            session, server_id=server.id, task_key=task_key
        )
        assert result.assignment.member_display_name == name, task_key


async def test_assignment_is_persisted_with_auditable_evidence(session):
    server, _, _, _ = await seed_server(session)

    result = await assignment_service.assign_task(
        session, server_id=server.id, task_key="TASK-101", requested_by_discord_id="req-9"
    )
    await session.commit()

    history = await assignment_service.get_assignment_history(session, server.id)
    assert len(history) == 1
    assert history[0].event is AssignmentEvent.CREATED
    assert history[0].to_status is AssignmentStatus.ACTIVE
    assert history[0].actor == "req-9"

    from app.models.assignment import Assignment

    stored = await session.get(Assignment, result.assignment.id)
    assert stored is not None
    assert stored.engine_version
    assert stored.rationale
    # The whole evaluation is retained, including candidates that lost.
    considered = {c["display_name"] for c in stored.evidence["ranked"]}
    assert {"Rahul", "Ananya", "Madhuri", "Arjun"} <= considered
    assert stored.evidence["task_key"] == "TASK-101"


async def test_double_assignment_is_refused_unless_reassign_is_requested(session):
    server, _, _, _ = await seed_server(session)

    await assignment_service.assign_task(session, server_id=server.id, task_key="TASK-101")
    await session.commit()

    with pytest.raises(ConflictError):
        await assignment_service.assign_task(session, server_id=server.id, task_key="TASK-101")
    await session.rollback()


async def test_reassignment_supersedes_and_records_both_events(session):
    server, _, members, _ = await seed_server(session)

    await assignment_service.assign_task(session, server_id=server.id, task_key="TASK-101")
    await session.commit()

    madhuri_discord = members["madhuri"].user.discord_user_id
    result = await assignment_service.assign_task(
        session,
        server_id=server.id,
        task_key="TASK-101",
        member_discord_id=madhuri_discord,
        reassign=True,
    )
    await session.commit()

    assert result.assignment.member_display_name == "Madhuri"
    assert result.assignment.decision_mode is DecisionMode.MANUAL

    events = [h.event for h in await assignment_service.get_assignment_history(session, server.id)]
    assert AssignmentEvent.REASSIGNED in events
    assert events.count(AssignmentEvent.CREATED) == 2


async def test_unknown_task_is_reported_not_invented(session):
    server, _, _, _ = await seed_server(session)

    with pytest.raises(NotFoundError):
        await assignment_service.assign_task(session, server_id=server.id, task_key="TASK-999")


async def test_project_knowledge_informs_the_decision(session):
    server, project, _, _ = await seed_server(session)

    await knowledge_service.ingest_document(
        session,
        project,
        DocumentCreate(
            context=ServerContext(discord_guild_id=server.discord_guild_id),
            project_key=project.key,
            title="Frontend Architecture",
            content=(
                "The frontend uses React and TypeScript.\n\n"
                "The admin dashboard is built with React and Recharts."
            ),
            source_type=DocumentSourceType.ARCHITECTURE,
        ),
    )
    await session.commit()

    context = await assignment_service.load_context(session, server.id, "TASK-101")
    best = context.evaluation.best

    assert best.display_name == "Rahul"
    assert best.components.knowledge > 0
    assert any(hit.document_title == "Frontend Architecture" for hit in best.evidence.knowledge)


async def test_dependency_status_is_carried_into_the_decision(session):
    from app.services import task_service

    server, _, _, tasks = await seed_server(session)
    await task_service.add_dependency(session, server.id, tasks["TASK-101"], "TASK-104")
    await session.commit()

    result = await assignment_service.assign_task(session, server_id=server.id, task_key="TASK-101")

    assert result.evaluation.dependencies.is_blocked is True
    assert result.evaluation.dependencies.unresolved[0].task_key == "TASK-104"
    assert any("TASK-104" in reason for reason in result.reasons)


async def test_workload_shifts_the_choice_to_the_free_member(session):
    """Two equally skilled members: the one already carrying work loses."""
    import sqlalchemy as sa

    from app.models.skill import Skill

    server, project, _, _ = await seed_server(session)
    react = (await session.execute(sa.select(Skill).where(Skill.slug == "react"))).scalar_one()
    javascript = (
        await session.execute(sa.select(Skill).where(Skill.slug == "javascript"))
    ).scalar_one()

    twin = await make_member(
        session,
        server,
        project,
        display_name="Zara",  # sorts after Rahul, so name is not what decides
        role="Frontend Engineer",
        skills=[(react, 5), (javascript, 5)],
    )
    second_frontend_task = await make_task(
        session,
        server,
        project,
        key="TASK-105",
        title="Build React settings screen",
        skills=[react, javascript],
        role_hint="Frontend",
    )
    await session.commit()

    # With both free, the tie breaks on name and Rahul wins.
    first = await assignment_service.assign_task(session, server_id=server.id, task_key="TASK-101")
    await session.commit()
    assert first.assignment.member_display_name == "Rahul"

    # Rahul now has one active task, so the next React task goes to Zara.
    second = await assignment_service.assign_task(
        session, server_id=server.id, task_key=second_frontend_task.key
    )
    await session.commit()

    assert second.assignment.member_display_name == twin.display_name
    rahul_scored = next(c for c in second.evaluation.ranked if c.display_name == "Rahul")
    assert rahul_scored.evidence.workload.active_task_count == 1


async def test_plan_guard_blocks_implicit_replanning(session):
    server, project, _, _ = await seed_server(session)
    project.plan_status = PlanStatus.ACTIVE
    project.plan_version = 1
    await session.commit()

    # Assignment still works on a planned project...
    result = await assignment_service.assign_task(session, server_id=server.id, task_key="TASK-101")
    assert result.assignment.member_display_name == "Rahul"

    # ...but regenerating the plan requires an explicit request.
    with pytest.raises(PlanImmutableError):
        project_service.ensure_plan_mutable(project, explicit_replan=False)
    project_service.ensure_plan_mutable(project, explicit_replan=True)


async def test_decision_mode_records_the_path_that_actually_decided(session):
    """The audit trail must not claim an LLM made a rule-based decision."""
    from app.agents.runner import run_assignment
    from app.models.assignment import Assignment
    from app.tools.context import ToolContext

    server, _, _, _ = await seed_server(session)
    await session.commit()

    ctx = ToolContext(
        server_id=server.id,
        discord_guild_id=server.discord_guild_id,
        requested_by_discord_id="req-1",
    )
    reply = await run_assignment(ctx, "TASK-101")

    assert reply.ok and reply.mode == "deterministic"
    stored = await session.get(Assignment, uuid.UUID(reply.data["assignment"]["id"]))
    assert stored.decision_mode is DecisionMode.DETERMINISTIC
