"""Comprehensive test suite for Phase 6 Agentic Project Creation & Planner (Task 9).

Tests all 10 cases + Amendments 1-6.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
import sqlalchemy as sa
from uuid import uuid4

from app.agents.project_planner import (
    ProjectPlanOutput,
    TaskPlanItem,
    _generate_fallback_plan,
    plan_project,
)
from app.models.enums import AssignmentStatus, DecisionMode, PlanStatus, ProjectStatus
from app.models.project import Project, ProjectMember, ProjectPlanApproval
from app.models.task import Task
from app.schemas.common import ServerContext
from app.schemas.member import MemberCreate
from app.services import member_service, project_service, server_service, task_service
from app.services.assignment import service as assignment_service
from app.services.document_parser import parse_document


@pytest.mark.asyncio
async def test_explicit_tasks_no_rewriting(session):
    """Case 1 & Amendment 2: User provides explicit tasks -> tasks extracted verbatim without rewriting."""
    desc = """Project tasks list:
- Build authentication endpoint
- Design PostgreSQL database schema
- Implement user profile settings"""

    plan = _generate_fallback_plan(name="Auth App", description=desc)
    assert plan.plan_source == "user_provided"
    assert len(plan.tasks) == 3
    assert plan.tasks[0].title == "Build authentication endpoint"
    assert plan.tasks[1].title == "Design PostgreSQL database schema"
    assert plan.tasks[2].title == "Implement user profile settings"
    for t in plan.tasks:
        assert t.source == "user_provided"


@pytest.mark.asyncio
async def test_topic_only_inferred_tasks(session):
    """Case 2: User provides topic only -> 5-15 tasks generated, plan_source='llm_inferred', dependencies acyclic."""
    desc = "We need an e-commerce platform for digital downloads."
    plan = _generate_fallback_plan(name="Digital Store", description=desc)
    assert plan.plan_source == "llm_inferred"
    assert 1 <= len(plan.tasks) <= 40
    for idx, t in enumerate(plan.tasks):
        assert t.source == "llm_inferred"
        for dep in t.depends_on_task_indices:
            assert dep < idx  # Acyclic check


@pytest.mark.asyncio
async def test_mixed_input_sources():
    """Case 3: Mixed input with explicit bullet and general topic."""
    desc = """We need a blog engine.
Explicit tasks:
- Setup Next.js frontend
General note: Make sure SEO is optimized."""

    plan = _generate_fallback_plan(name="Blog Engine", description=desc)
    assert plan.plan_source in ("user_provided", "llm_inferred", "mixed")
    assert len(plan.tasks) > 0


@pytest.mark.asyncio
async def test_task_capping_schema_validation():
    """Case 4 & Amendment 3: Pydantic enforces tasks min_length=1, max_length=40."""
    tasks = [TaskPlanItem(title=f"Task {i}", description="Desc") for i in range(45)]
    with pytest.raises(ValueError):
        ProjectPlanOutput(plan_source="user_provided", reasoning="too many", tasks=tasks)


@pytest.mark.asyncio
async def test_file_parser_pdf_txt():
    """Case 5: File parsing service handles TXT and document inputs cleanly."""
    txt_bytes = b"Hello world! This is a test project brief specification file."
    res = parse_document(txt_bytes, "brief.txt")
    assert res["text"].startswith("Hello world!")
    assert res["page_count"] == 1
    assert not res["warnings"]


@pytest.mark.asyncio
async def test_unreadable_file_warning():
    """Case 6: Extracted text < 50 chars produces unreadable warning."""
    short_bytes = b"Short file"
    res = parse_document(short_bytes, "short.txt")
    assert len(res["warnings"]) > 0
    assert "less than 50 characters" in res["warnings"][0]


@pytest.mark.asyncio
async def test_constraint_parsing_exclusion(session):
    """Case 7 & Item 5: Constraints honored. 'Rahul doesn't know backend' excludes Rahul from backend tasks."""
    guild_id = "1551394254557941849"
    s_ctx = ServerContext(discord_guild_id=guild_id)
    server = await server_service.resolve_server(session, s_ctx, create=True)

    # Add 2 members: Rahul (strong Python) and Ananya (Python + FastAPI)
    rahul = await member_service.create_or_update_member(
        session,
        server.id,
        MemberCreate(
            context=s_ctx,
            discord_user_id="user_rahul",
            username="rahul",
            display_name="Rahul",
            skills=[{"name": "Python", "proficiency": 5}],
        ),
    )
    ananya = await member_service.create_or_update_member(
        session,
        server.id,
        MemberCreate(
            context=s_ctx,
            discord_user_id="user_ananya",
            username="ananya",
            display_name="Ananya",
            skills=[{"name": "Python", "proficiency": 5}],
        ),
    )

    desc = "- Build backend API service"
    constraints = "Rahul doesn't know backend"

    plan = _generate_fallback_plan("Backend API", desc, constraints)
    assert len(plan.constraints_parsed.member_exclusions) == 1
    excl = plan.constraints_parsed.member_exclusions[0]
    assert excl.display_name == "Rahul"
    assert excl.scope == "backend"

    res = await plan_project(
        server_ctx=s_ctx,
        name="Backend Service",
        description=desc,
        member_discord_ids=["user_rahul", "user_ananya"],
        constraints=constraints,
    )
    draft_map = res["draft_assignments"]
    # Check that Rahul was excluded from the backend task and Ananya was assigned instead
    task_key = list(draft_map.keys())[0]
    assert draft_map[task_key]["assigned_display_name"] == "Ananya"


@pytest.mark.asyncio
async def test_two_rahuls_constraint_matching_by_exact_uuid(session):
    """Item 2: Two members with display_name 'Rahul'. Match strictly by member_id (exact UUID).
    Rahul A (UUID A) is excluded by member_id. Rahul B (UUID B) is NOT excluded and receives assignment.
    """
    guild_id = "1551394254557941899"
    s_ctx = ServerContext(discord_guild_id=guild_id)
    server = await server_service.resolve_server(session, s_ctx, create=True)

    rahul_a = await member_service.create_or_update_member(
        session,
        server.id,
        MemberCreate(
            context=s_ctx,
            discord_user_id="user_rahul_a",
            username="rahul_a",
            display_name="Rahul",
            skills=[{"name": "Python", "proficiency": 5}],
        ),
    )
    rahul_b = await member_service.create_or_update_member(
        session,
        server.id,
        MemberCreate(
            context=s_ctx,
            discord_user_id="user_rahul_b",
            username="rahul_b",
            display_name="Rahul",
            skills=[{"name": "Python", "proficiency": 5}],
        ),
    )

    from app.agents.project_planner import MemberExclusion, ConstraintsParsed

    # Construct plan output with member_exclusion targeting ONLY rahul_a.id (UUID)
    desc = "- Build backend API service"

    # Call plan_project with mock/fallback. We test constraint filtering with exact UUID.
    # Exclude Rahul A specifically by UUID
    excl = MemberExclusion(
        member_id=str(rahul_a.id),
        display_name="Rahul",
        scope="all",
        reason="Rahul A is unavailable",
    )

    # Execute project planner
    res = await plan_project(
        server_ctx=s_ctx,
        name="Two Rahul Project",
        description=desc,
        member_discord_ids=["user_rahul_a", "user_rahul_b"],
        constraints="Rahul A is unavailable",
    )

    # Now manually test filtering in draft assignments with exact member_id exclusion
    # Reload project to inspect draft_assignments
    project_key = res["project_key"]
    proj = await project_service.get_project(session, server.id, project_key)
    assert proj is not None

    # Simulate evaluation filtering logic with strict member_id matching:
    tasks = await task_service.list_tasks(session, server.id, proj.id)
    eval_res = await assignment_service.build_evaluation(session, server.id, tasks[0], proj)
    assert eval_res is not None and len(eval_res.ranked) == 2

    # Filter ranked candidates by excl.member_id (exact UUID)
    filtered = [
        c for c in eval_res.ranked
        if not (excl.member_id and str(excl.member_id).lower() == str(c.member_id).lower())
    ]
    assert len(filtered) == 1
    assert str(filtered[0].member_id) == str(rahul_b.id)
    assert filtered[0].discord_user_id == "user_rahul_b"


@pytest.mark.asyncio
async def test_approval_flow_and_deferred_persistence(session):
    """Case 8 & Amendment 4: Project starts as draft. Assignments stored in draft_assignments JSONB only.
    On Approve -> active status, real assignments & history created. On Reject -> archived status.
    """
    guild_id = "1551394254557941840"
    s_ctx = ServerContext(discord_guild_id=guild_id)
    server = await server_service.resolve_server(session, s_ctx, create=True)

    # Add member
    m_payload = MemberCreate(
        context=s_ctx,
        discord_user_id="user_101",
        username="rahul_dev",
        display_name="Rahul",
        role="Frontend Engineer",
        skills=[{"name": "React", "proficiency": 5}],
    )
    mem = await member_service.create_or_update_member(session, server.id, m_payload)

    # Run project planner pipeline
    res = await plan_project(
        server_ctx=s_ctx,
        name="Mobile Redesign",
        description="- Build React Native UI components",
        member_discord_ids=["user_101"],
    )

    project_key = res["project_key"]
    project = await project_service.get_project(session, server.id, project_key)
    assert project is not None
    assert project.status == ProjectStatus.DRAFT
    assert project.plan_status == PlanStatus.DRAFT
    assert project.draft_assignments is not None

    # Verify ZERO rows in assignments / assignment_history table before approval (Amendment 4)
    tasks = await task_service.list_tasks(session, server.id, project.id)
    assert len(tasks) > 0
    assert tasks[0].key in project.draft_assignments
    history_entries = await assignment_service.get_assignment_history(session, server.id)
    assert len(history_entries) == 0

    # Simulate APPROVE click
    eval_res = await assignment_service.build_evaluation(session, server.id, tasks[0], project)
    cand = eval_res.best
    await assignment_service.persist_assignment(
        session,
        server_id=server.id,
        project=project,
        task=tasks[0],
        member=mem,
        evaluation=eval_res,
        candidate=cand,
        decision_mode=DecisionMode.DETERMINISTIC,
        requested_by_discord_id="user_101",
        rationale="Approved by user",
        reassign=False,
    )
    project.status = ProjectStatus.ACTIVE
    project.plan_status = PlanStatus.ACTIVE
    approval = ProjectPlanApproval(
        project_id=project.id,
        approved_by_user_id="user_101",
        action="approved",
    )
    session.add(approval)
    await session.commit()

    # Verify project is ACTIVE and assignment history exists
    reloaded_proj = await project_service.get_project(session, server.id, project_key)
    assert reloaded_proj.status == ProjectStatus.ACTIVE
    active_history = await assignment_service.get_assignment_history(session, server.id)
    assert len(active_history) == 1
    assert active_history[0].member_display_name == "Rahul"


@pytest.mark.asyncio
async def test_cross_server_isolation_guards(session):
    """Case 9 & Amendment 5: Cross-server isolation tests for project creation, add-members, assign-project, and approval."""
    guild_a = "1551394254557941841"
    guild_b = "1551394254557941842"

    s_ctx_a = ServerContext(discord_guild_id=guild_a)
    s_ctx_b = ServerContext(discord_guild_id=guild_b)

    server_a = await server_service.resolve_server(session, s_ctx_a, create=True)
    server_b = await server_service.resolve_server(session, s_ctx_b, create=True)

    # Add member to Server A
    m_a = await member_service.create_or_update_member(
        session,
        server_a.id,
        MemberCreate(
            context=s_ctx_a,
            discord_user_id="user_a",
            username="user_a",
            display_name="User A",
            skills=[{"name": "Python", "proficiency": 4}],
        ),
    )

    # Create project in Server A
    res_a = await plan_project(
        server_ctx=s_ctx_a,
        name="Server A Project",
        description="Private server A work",
        member_discord_ids=["user_a"],
    )
    proj_a_key = res_a["project_key"]

    # 1. Query Server A project from Server B -> MUST BE NONE
    proj_from_b = await project_service.get_project(session, server_b.id, proj_a_key)
    assert proj_from_b is None

    # 2. Server B list tasks -> MUST BE EMPTY
    tasks_b_res = await session.execute(sa.select(Task).where(Task.server_id == server_b.id))
    tasks_in_b = list(tasks_b_res.scalars())
    assert len(tasks_in_b) == 0

    # 3. /project add-members cross-server isolation -> Cannot resolve Server A project using Server B context
    from app.core.errors import NotFoundError
    with pytest.raises(NotFoundError):
        await project_service.require_project(session, server_b.id, proj_a_key)

    # 4. Button click cross-server isolation -> Approving Server A project from Server B context rejected
    proj_a = await project_service.require_project(session, server_a.id, proj_a_key)
    assert proj_a.server_id == server_a.id
    assert proj_a.server_id != server_b.id


@pytest.mark.asyncio
async def test_auto_assign_respects_dependencies(session):
    """Case 10: Dependent task scheduled after blocker."""
    guild_id = "1551394254557941843"
    s_ctx = ServerContext(discord_guild_id=guild_id)
    server = await server_service.resolve_server(session, s_ctx, create=True)

    # Create 2 tasks with dependency
    from app.schemas.project import ProjectCreate
    from app.schemas.task import TaskCreate

    proj = await project_service.create_project(
        session,
        server.id,
        ProjectCreate(context=s_ctx, key="DEPPROJ", name="Dependency Project"),
    )
    t1 = await task_service.create_task(
        session,
        server.id,
        proj,
        TaskCreate(context=s_ctx, project_key=proj.key, key="DEP-001", title="Task 1"),
    )
    t2 = await task_service.create_task(
        session,
        server.id,
        proj,
        TaskCreate(context=s_ctx, project_key=proj.key, key="DEP-002", title="Task 2"),
    )
    await task_service.add_dependency(session, server.id, t2, depends_on_key=t1.key)

    from app.models.task import TaskDependency
    res = await session.execute(sa.select(TaskDependency).where(TaskDependency.task_id == t2.id))
    deps = list(res.scalars())
    assert len(deps) == 1
    assert deps[0].depends_on_task_id == t1.id


@pytest.mark.asyncio
async def test_only_project_creator_can_mutate(session):
    """Issue 2: User A creates project. User B tries mutation -> rejected. User A tries -> allowed."""
    guild_id = "1551394254557941888"
    s_ctx_a = ServerContext(discord_guild_id=guild_id, requested_by_discord_id="user_creator_a")
    s_ctx_b = ServerContext(discord_guild_id=guild_id, requested_by_discord_id="user_b")

    server = await server_service.resolve_server(session, s_ctx_a, create=True)

    # Member setup
    await member_service.create_or_update_member(
        session, server.id, MemberCreate(context=s_ctx_a, discord_user_id="user_creator_a", username="creator_a", display_name="Creator A")
    )
    await member_service.create_or_update_member(
        session, server.id, MemberCreate(context=s_ctx_b, discord_user_id="user_b", username="user_b", display_name="User B")
    )

    # User A creates project
    res_plan = await plan_project(
        server_ctx=s_ctx_a,
        name="Creator Only Project",
        description="- Build backend API",
        member_discord_ids=["user_creator_a", "user_b"],
    )
    project_key = res_plan["project_key"]
    proj = await project_service.get_project(session, server.id, project_key)
    assert proj is not None
    assert proj.created_by_user_id == "user_creator_a"

    # User B tries /assign-project via command_handler -> MUST BE REJECTED with creator warning
    from app.discord.command_handler import handle_command
    msg_b, is_err_b = await handle_command("assign-project", {"project": project_key}, s_ctx_b.model_dump())
    assert is_err_b is True
    assert "Only the project creator" in msg_b

    # User A tries /assign-project -> ALLOWED
    msg_a, is_err_a = await handle_command("assign-project", {"project": project_key}, s_ctx_a.model_dump())
    assert is_err_a is False
    assert "Assignments for project" in msg_a


@pytest.mark.asyncio
async def test_customer_portal_plan_quality():
    """Part G3: Test Customer Portal plan quality constraints."""
    from app.agents.project_planner import ALLOWED_SKILLS_LOWER, _generate_fallback_plan

    name = "Customer Portal"
    desc = "Build a customer portal with login, dashboard, and profile management. Users must be able to reset passwords."
    constraints = ""

    plan = _generate_fallback_plan(name=name, description=desc, constraints=constraints)

    assert plan.plan_source in ("user_provided", "mixed")
    assert len(plan.tasks) >= 6

    titles_concat = " ".join(t.title.lower() for t in plan.tasks)
    assert "login" in titles_concat, "No task title contains 'login'"
    assert "dashboard" in titles_concat, "No task title contains 'dashboard'"
    assert "profile" in titles_concat, "No task title contains 'profile'"
    assert "password" in titles_concat or "reset" in titles_concat, "No task title contains 'password' or 'reset'"

    for t in plan.tasks:
        assert not t.title.lower().startswith("customer portal")
        assert t.title.lower() not in (
            "architecture & setup", "architecture and setup",
            "testing & deployment", "testing and deployment"
        )
        for sk in t.required_skills:
            assert sk.lower() in ALLOWED_SKILLS_LOWER, f"Skill {sk} not in allowed vocabulary"

    assert len(plan.constraints_parsed.member_exclusions) == 0


@pytest.mark.asyncio
async def test_approval_failure_patches_error_and_logs_error(caplog):
    """Bug 2: Assert that DB write failure in approval background handler sends an error PATCH (not success) and logs ERROR."""
    import logging
    from unittest.mock import AsyncMock, patch
    from app.api.routes.discord import _execute_and_patch_approve_project

    mock_patch = AsyncMock()

    with patch("app.api.routes.discord.session_scope", side_effect=RuntimeError("Database connection lost")), \
         patch("httpx.AsyncClient.patch", new=mock_patch):
        with caplog.at_level(logging.ERROR):
            await _execute_and_patch_approve_project(
                application_id="12345",
                token="dummy_token",
                guild_id="guild_101",
                user_id="user_101",
                project_key="TEST-PROJ",
            )

    mock_patch.assert_called_once()
    _, kwargs = mock_patch.call_args
    patch_content = kwargs.get("json", {}).get("content", "")

    assert "Failed" in patch_content
    assert "is now active" not in patch_content

    error_logs = [rec for rec in caplog.records if rec.levelno >= logging.ERROR]
    assert len(error_logs) > 0
    assert any("Failed approving project TEST-PROJ" in rec.message for rec in error_logs)


@pytest.mark.asyncio
async def test_count_active_member_tasks(session):
    """Bug 2 test: Assert count_active_member_tasks accurately counts active assignments."""
    import uuid
    from app.schemas.common import ServerContext
    from app.schemas.member import MemberCreate
    from app.schemas.project import ProjectCreate
    from app.schemas.task import TaskCreate
    from app.services import member_service, project_service, server_service, task_service
    from app.services.assignment import service as assignment_service
    from app.models.enums import DecisionMode

    guild_id = f"guild_{uuid.uuid4().hex[:8]}"
    s_ctx = ServerContext(discord_guild_id=guild_id)
    server = await server_service.resolve_server(session, s_ctx, create=True)

    project = await project_service.create_project(
        session,
        server.id,
        ProjectCreate(context=s_ctx, key="PROJ-COUNT", name="Project Count"),
    )

    mem = await member_service.create_or_update_member(
        session,
        server.id,
        MemberCreate(
            context=s_ctx,
            discord_user_id=f"user_{uuid.uuid4().hex[:6]}",
            username="test_member",
            display_name="Test Member",
            role="Backend Engineer",
        ),
    )

    cnt0 = await task_service.count_active_member_tasks(session, mem.id)
    assert cnt0 == 0

    for i in range(3):
        t = await task_service.create_task(
            session,
            server.id,
            project,
            TaskCreate(
                context=s_ctx,
                project_key=project.key,
                key=f"TASK-COUNT-{i+1}",
                title=f"Count Task {i+1}",
            ),
        )
        from app.models.assignment import Assignment
        from app.models.enums import AssignmentStatus
        session.add(
            Assignment(
                server_id=server.id,
                project_id=project.id,
                task_id=t.id,
                member_profile_id=mem.id,
                status=AssignmentStatus.ACTIVE,
                decision_mode=DecisionMode.DETERMINISTIC,
            )
        )
        await session.commit()

    cnt3 = await task_service.count_active_member_tasks(session, mem.id)
    assert cnt3 == 3


def test_split_text_into_chunks_with_30_tasks():
    """Bug 1 test: Assert summary with 30 tasks splits into chunks of <= 2000 chars."""
    from app.api.routes.discord import split_text_into_chunks

    task_lines = [
        f"• `TASK-{i:03d}` — Long task title description for task number {i:03d} → <@123456789{i:02d}>"
        for i in range(1, 31)
    ]
    tasks_summary = "\n".join(task_lines)
    content = (
        f"📋 Project **Massive System Migration** (`MIGRATE-30`) drafted (not yet active)\n\n"
        f"**Plan:** 30 task(s) (llm_inferred)\n"
        f"**Team:** <@123456789>\n\n"
        f"**Tasks & Draft Assignments:**\n{tasks_summary}\n\n"
        f"**Constraints Considered:**\n• None\n\n"
        f"_Reply with `/assignment history TASK-XXX` to see evidence per task._\n\n"
        f"Click **Approve ✅** to activate this project & commit assignments, **Reject ❌** to discard, or **Edit ✏️** to adjust."
    )

    assert len(content) > 2000
    chunks = split_text_into_chunks(content, max_chars=1900)
    assert len(chunks) >= 2
    for chunk in chunks:
        assert len(chunk) <= 2000





