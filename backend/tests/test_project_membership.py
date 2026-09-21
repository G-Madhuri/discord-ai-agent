import pytest
from app.core.errors import NoEligibleCandidateError
from app.schemas.common import ServerContext
from app.schemas.member import MemberCreate, SkillInput
from app.schemas.project import ProjectCreate, ProjectMemberAdd
from app.schemas.task import TaskCreate, TaskSkillInput
from app.services import member_service, project_service, server_service, task_service
from app.services.assignment import service as assignment_service


@pytest.mark.asyncio
async def test_member_add_does_not_create_project_member_rows(session):
    ctx = ServerContext(discord_guild_id="guild-proj-mem-test-1")
    server = await server_service.resolve_server(session, ctx, create=True)

    # Create project 1
    proj_payload = ProjectCreate(context=ctx, key="PROJ1", name="Project 1")
    project = await project_service.create_project(session, server.id, proj_payload)

    # Add member to server directory
    mem_payload = MemberCreate(
        context=ctx,
        discord_user_id="user_p1",
        username="User One",
        display_name="User One",
        role="Developer",
        skills=[SkillInput(name="Python", proficiency=5)],
    )
    member = await member_service.create_or_update_member(session, server.id, mem_payload)

    # 6a. Confirm MemberProfile exists, but NO ProjectMember row was auto-created
    roster = await project_service.list_project_members(session, server.id, project.id)
    assert len(roster) == 0, "Expected project roster to be empty after server /member add"


@pytest.mark.asyncio
async def test_list_project_members_returns_empty_when_unpopulated(session):
    ctx = ServerContext(discord_guild_id="guild-proj-mem-test-2")
    server = await server_service.resolve_server(session, ctx, create=True)

    # Create project
    proj_payload = ProjectCreate(context=ctx, key="PROJ2", name="Project 2")
    project = await project_service.create_project(session, server.id, proj_payload)

    # Add 3 members to server directory
    for i in range(3):
        await member_service.create_or_update_member(
            session,
            server.id,
            MemberCreate(
                context=ctx,
                discord_user_id=f"user_dir_{i}",
                username=f"Directory User {i}",
                display_name=f"Directory User {i}",
            ),
        )

    # 6b. Confirm list_project_members returns []
    roster = await project_service.list_project_members(session, server.id, project.id)
    assert roster == []


@pytest.mark.asyncio
async def test_explicit_project_member_add_populates_roster(session):
    ctx = ServerContext(discord_guild_id="guild-proj-mem-test-3")
    server = await server_service.resolve_server(session, ctx, create=True)

    proj_payload = ProjectCreate(context=ctx, key="PROJ3", name="Project 3")
    project = await project_service.create_project(session, server.id, proj_payload)

    member = await member_service.create_or_update_member(
        session,
        server.id,
        MemberCreate(
            context=ctx,
            discord_user_id="user_p3",
            username="User Three",
            display_name="User Three",
        ),
    )

    # Before explicit add: empty
    assert len(await project_service.list_project_members(session, server.id, project.id)) == 0

    # 6c. Explicitly add to project
    await project_service.add_project_member(
        session, project, member, project_role="Lead Engineer"
    )

    # After explicit add: populated
    roster = await project_service.list_project_members(session, server.id, project.id)
    assert len(roster) == 1
    link, m = roster[0]
    assert m.id == member.id
    assert link.project_role == "Lead Engineer"


@pytest.mark.asyncio
async def test_assignment_engine_only_considers_project_members(session):
    ctx = ServerContext(discord_guild_id="guild-proj-mem-test-4")
    server = await server_service.resolve_server(session, ctx, create=True)

    proj = await project_service.create_project(
        session, server.id, ProjectCreate(context=ctx, key="PROJ4", name="Project 4")
    )

    # Add member to server with exact skill, but DO NOT add to project
    await member_service.create_or_update_member(
        session,
        server.id,
        MemberCreate(
            context=ctx,
            discord_user_id="user_unassigned",
            username="Unassigned Dev",
            display_name="Unassigned Dev",
            skills=[SkillInput(name="Go", proficiency=5)],
        ),
    )

    task = await task_service.create_task(
        session,
        server.id,
        proj,
        TaskCreate(
            context=ctx,
            project_key="PROJ4",
            key="TASK-P4",
            title="Go Microservice",
            required_skills=[TaskSkillInput(name="Go")],
        ),
    )

    # 6d. Attempt assign -> must fail because member is not in project roster
    with pytest.raises(NoEligibleCandidateError):
        await assignment_service.assign_task(
            session, server_id=server.id, task_key=task.key, project_key=proj.key
        )


@pytest.mark.asyncio
async def test_cross_project_isolation_on_same_server(session):
    ctx = ServerContext(discord_guild_id="guild-proj-mem-test-5")
    server = await server_service.resolve_server(session, ctx, create=True)

    projA = await project_service.create_project(
        session, server.id, ProjectCreate(context=ctx, key="PROJA", name="Project A")
    )
    projB = await project_service.create_project(
        session, server.id, ProjectCreate(context=ctx, key="PROJB", name="Project B")
    )

    memA = await member_service.create_or_update_member(
        session,
        server.id,
        MemberCreate(
            context=ctx,
            discord_user_id="user_projA_only",
            username="Dev A",
            display_name="Dev A",
            skills=[SkillInput(name="Rust", proficiency=5)],
        ),
    )

    # Add memA to Project A ONLY
    await project_service.add_project_member(session, projA, memA)

    taskB = await task_service.create_task(
        session,
        server.id,
        projB,
        TaskCreate(
            context=ctx,
            project_key="PROJB",
            key="TASK-PB",
            title="Rust Core",
            required_skills=[TaskSkillInput(name="Rust")],
        ),
    )

    # 6e. Assign task on Project B -> memA must NOT be considered for Project B
    with pytest.raises(NoEligibleCandidateError):
        await assignment_service.assign_task(
            session, server_id=server.id, task_key=taskB.key, project_key=projB.key
        )
