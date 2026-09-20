"""Server isolation: data from one Discord guild must never reach another."""

from __future__ import annotations

import pytest

from app.core.errors import NotFoundError
from app.models.enums import DocumentSourceType
from app.schemas.common import ServerContext
from app.schemas.project import DocumentCreate
from app.services import knowledge_service, member_service, project_service, task_service
from app.services.assignment import service as assignment_service
from tests.conftest import make_member, make_project, make_server, make_skill, make_task

pytestmark = pytest.mark.asyncio


async def two_servers(session):
    """Two guilds that deliberately share task keys, project keys and skills."""
    react = await make_skill(session, "react", "React", "frontend")

    server_a = await make_server(session, "guild-a", "Server A")
    project_a = await make_project(session, server_a, "APP", "Server A project")
    rahul = await make_member(
        session,
        server_a,
        project_a,
        display_name="Rahul",
        role="Frontend Engineer",
        skills=[(react, 5)],
    )
    task_a = await make_task(
        session, server_a, project_a, key="TASK-101", title="Build React dashboard", skills=[react]
    )

    server_b = await make_server(session, "guild-b", "Server B")
    project_b = await make_project(session, server_b, "APP", "Server B project")
    priya = await make_member(
        session,
        server_b,
        project_b,
        display_name="Priya",
        role="Frontend Engineer",
        skills=[(react, 5)],
    )
    task_b = await make_task(
        session, server_b, project_b, key="TASK-101", title="Build React dashboard", skills=[react]
    )

    await session.commit()
    return (server_a, project_a, rahul, task_a), (server_b, project_b, priya, task_b)


async def test_same_task_key_resolves_within_its_own_server(session):
    (server_a, _, _, task_a), (server_b, _, _, task_b) = await two_servers(session)

    found_a = await task_service.require_task(session, server_a.id, "TASK-101")
    found_b = await task_service.require_task(session, server_b.id, "TASK-101")

    assert found_a.id == task_a.id
    assert found_b.id == task_b.id
    assert found_a.id != found_b.id


async def test_assignment_only_considers_members_of_the_same_server(session):
    (server_a, _, rahul, _), (server_b, _, priya, _) = await two_servers(session)

    result_a = await assignment_service.assign_task(
        session, server_id=server_a.id, task_key="TASK-101"
    )
    await session.commit()
    result_b = await assignment_service.assign_task(
        session, server_id=server_b.id, task_key="TASK-101"
    )
    await session.commit()

    assert result_a.assignment.member_display_name == "Rahul"
    assert result_b.assignment.member_display_name == "Priya"

    considered_a = {c.display_name for c in result_a.evaluation.ranked}
    considered_b = {c.display_name for c in result_b.evaluation.ranked}
    assert considered_a == {"Rahul"}
    assert considered_b == {"Priya"}


async def test_members_are_not_visible_across_servers(session):
    (server_a, _, rahul, _), (server_b, _, _, _) = await two_servers(session)

    assert (
        await member_service.get_member(session, server_b.id, rahul.user.discord_user_id)
    ) is None
    with pytest.raises(NotFoundError):
        await member_service.get_member_by_id(session, server_b.id, rahul.id)


async def test_project_knowledge_is_not_retrievable_from_another_server(session):
    (server_a, project_a, _, _), (server_b, project_b, _, _) = await two_servers(session)

    await knowledge_service.ingest_document(
        session,
        project_a,
        DocumentCreate(
            context=ServerContext(discord_guild_id=server_a.discord_guild_id),
            project_key=project_a.key,
            title="Server A architecture",
            content="Server A uses React with a private internal design system.",
            source_type=DocumentSourceType.ARCHITECTURE,
        ),
    )
    await session.commit()

    hits_a = await knowledge_service.search_knowledge(session, project_a, "React design system")
    hits_b = await knowledge_service.search_knowledge(session, project_b, "React design system")

    assert hits_a, "the owning server must be able to retrieve its own knowledge"
    assert hits_b == [], "another server must retrieve nothing"

    docs_b = await knowledge_service.list_documents(session, server_b.id, project_b.id)
    assert docs_b == []


async def test_history_is_scoped_to_one_server(session):
    (server_a, _, _, _), (server_b, _, _, _) = await two_servers(session)

    await assignment_service.assign_task(session, server_id=server_a.id, task_key="TASK-101")
    await session.commit()

    assert len(await assignment_service.get_assignment_history(session, server_a.id)) == 1
    assert await assignment_service.get_assignment_history(session, server_b.id) == []


async def test_project_lookup_does_not_cross_servers(session):
    (server_a, project_a, _, _), (server_b, project_b, _, _) = await two_servers(session)

    found = await project_service.require_project(session, server_b.id, "APP")
    assert found.id == project_b.id
    assert found.id != project_a.id
