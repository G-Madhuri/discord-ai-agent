"""Single-pass LLM Project Planner agent (Phase 6).

Analyzes project brief/topic, team member skills/roles/workload, and constraints,
and outputs a structured project plan via Gemini (Vertex AI).
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, Field
import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from app.models.task import Task, TaskSkill

from app.core.config import settings
from app.core.errors import DomainError
from app.core.logging import get_logger
from app.models.enums import PlanStatus, ProjectStatus
from app.models.project import Project
from app.schemas.common import ServerContext
from app.services import member_service, project_service, server_service, task_service
from app.services.assignment import service as assignment_service
from app.services.document_parser import parse_document
from app.tools.context import ToolContext

logger = get_logger(__name__)

# Timeout cap for LLM call (Amendment 6)
LLM_TIMEOUT_SECONDS = 30.0


class MemberExclusion(BaseModel):
    member_id: str | None = Field(default=None, description="UUID string of member profile if resolved from team list")
    display_name: str | None = Field(default=None, description="Name or handle of member")
    reason: str = Field(default="Excluded by user constraint", description="Reason for exclusion")
    scope: str = Field(default="all", description="Scope of exclusion, e.g. 'backend', 'frontend', 'all'")


class ConstraintsParsed(BaseModel):
    deadline_hint: str | None = Field(default=None, validation_alias=AliasChoices("deadline_hint", "deadline"))
    member_exclusions: list[MemberExclusion] = Field(default_factory=list, validation_alias=AliasChoices("member_exclusions", "exclusions"))
    unresolved_constraints: list[str] = Field(default_factory=list, validation_alias=AliasChoices("unresolved_constraints", "unresolved"))
    other: list[str] = Field(default_factory=list, validation_alias=AliasChoices("other", "notes"))


class TaskPlanItem(BaseModel):
    title: str = Field(
        validation_alias=AliasChoices("title", "name", "task_name", "task_title"),
        description="Title of the task. CRITICAL: If explicit in user description or file, MUST NOT be rewritten or paraphrased."
    )
    description: str = Field(default="", validation_alias=AliasChoices("description", "desc", "details"))
    required_skills: list[str] = Field(default_factory=list, validation_alias=AliasChoices("required_skills", "skills"))
    suggested_role: str | None = Field(default=None, validation_alias=AliasChoices("suggested_role", "role"))
    estimated_effort_hours: float = Field(default=8.0, validation_alias=AliasChoices("estimated_effort_hours", "effort_hours", "hours"))
    depends_on_task_indices: list[int] = Field(default_factory=list, validation_alias=AliasChoices("depends_on_task_indices", "dependencies", "deps"))
    source: Literal["user_provided", "llm_inferred"] = Field(default="user_provided", validation_alias=AliasChoices("source", "task_source"))


class ProjectPlanOutput(BaseModel):
    plan_source: Literal["user_provided", "llm_inferred", "mixed"] = Field(default="mixed", validation_alias=AliasChoices("plan_source", "source"))
    reasoning: str = Field(default="Plan generated based on input requirements.", validation_alias=AliasChoices("reasoning", "explanation", "summary"))
    tasks: list[TaskPlanItem] = Field(min_length=1, max_length=20)
    constraints_parsed: ConstraintsParsed = Field(default_factory=ConstraintsParsed, validation_alias=AliasChoices("constraints_parsed", "constraints"))


SYSTEM_PROMPT = """You are an expert AI software project planner for a technical team. Your task is to analyze a project name, description/brief, team member capabilities, and constraints, and output a structured project plan in JSON.

CRITICAL INSTRUCTIONS ON EXPLICIT TASKS:
1. DO NOT REWRITE EXPLICIT TASKS. If the user description or attached document contains explicit tasks or a task list, extract them VERBATIM as tasks with source='user_provided'. DO NOT rewrite, paraphrase, simplify, or rename explicit task titles or descriptions provided by the user.
2. If the input is a high-level topic or brief with no explicit tasks, infer 5-15 logical technical tasks and set source='llm_inferred'.
3. If the input contains both explicit tasks and general topic requirements, mark each task accordingly with source='user_provided' or source='llm_inferred'. Set plan_source to 'user_provided', 'llm_inferred', or 'mixed'.
4. Task dependencies must reference earlier task indices only (0-indexed). Dependencies must be acyclic.
5. Honor user constraints strictly. If a constraint states a team member cannot do a specific type of work (e.g. "Rahul doesn't know backend"), do NOT suggest or assign them for those tasks.
6. Do not invent skills that do not exist in the team member profiles.
7. Return strictly valid JSON adhering to the required schema. No additional commentary.
"""


def _generate_fallback_plan(
    name: str, description: str, constraints: str | None = None
) -> ProjectPlanOutput:
    """Fallback plan generator used for offline testing or when LLM API is unavailable."""
    # Check if user description contains explicit bulleted/numbered tasks
    lines = [line.strip() for line in description.splitlines() if line.strip()]
    task_lines = [
        re.sub(r"^[-*•\d+.\)]\s*", "", line)
        for line in lines
        if re.match(r"^[-*•\d+.\)]\s+", line)
    ]

    if task_lines:
        plan_source = "user_provided"
        tasks = [
            TaskPlanItem(
                title=t,
                description=f"User-provided task: {t}",
                source="user_provided",
            )
            for t in task_lines[:20]
        ]
    else:
        plan_source = "llm_inferred"
        tasks = [
            TaskPlanItem(
                title=f"{name} — Architecture & Setup",
                description="Set up codebase and core architecture.",
                required_skills=["Python", "Architecture"],
                source="llm_inferred",
            ),
            TaskPlanItem(
                title=f"{name} — Core Functionality",
                description="Implement primary feature workflows.",
                required_skills=["Python", "FastAPI"],
                depends_on_task_indices=[0],
                source="llm_inferred",
            ),
            TaskPlanItem(
                title=f"{name} — Testing & Deployment",
                description="Write unit tests and prepare deployment.",
                required_skills=["Testing", "Docker"],
                depends_on_task_indices=[1],
                source="llm_inferred",
            ),
        ]

    member_exclusions = []
    unresolved = []
    if constraints:
        c_lower = constraints.lower()
        if "doesn't know" in c_lower or "cannot do" in c_lower or "exclude" in c_lower:
            parts = constraints.split()
            name_part = parts[0] if parts else "Unknown"
            scope_part = "backend" if "backend" in c_lower else ("frontend" if "frontend" in c_lower else "all")
            member_exclusions.append(
                MemberExclusion(
                    display_name=name_part,
                    reason=constraints,
                    scope=scope_part,
                )
            )
        else:
            unresolved.append(constraints)

    return ProjectPlanOutput(
        plan_source=plan_source,  # type: ignore[arg-type]
        reasoning=f"Generated plan for project {name} with {len(tasks)} tasks.",
        tasks=tasks,
        constraints_parsed=ConstraintsParsed(
            member_exclusions=member_exclusions,
            unresolved_constraints=unresolved,
            other=[constraints] if constraints and not member_exclusions and not unresolved else [],
        ),
    )


async def _call_gemini_planner(
    name: str,
    description: str,
    constraints: str | None,
    members_info: list[dict[str, Any]],
    stricter_retry: bool = False,
) -> ProjectPlanOutput:
    """Call Gemini model via Vertex AI with 30s timeout cap and Pydantic validation."""
    user_prompt = (
        ("RETRY NOTICE: Output MUST contain between 1 and 20 tasks, adhering strictly to the JSON schema.\n\n" if stricter_retry else "")
        + "CRITICAL INSTRUCTION: DO NOT REWRITE EXPLICIT TASKS PROVIDED BY THE USER. Extract them verbatim.\n\n"
        + f"Project Name: {name}\n"
        + f"Description / Brief:\n{description}\n\n"
        + f"User Constraints:\n{constraints or 'None'}\n\n"
        + f"Team Members Available:\n{json.dumps(members_info, indent=2)}\n"
    )

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(
            vertexai=settings.google_genai_use_vertexai,
            project=settings.google_cloud_project,
            location=settings.google_cloud_location,
        )

        sys_inst = SYSTEM_PROMPT
        if stricter_retry:
            sys_inst += "\nIMPORTANT: Ensure tasks list length is at most 20 tasks."

        async def _make_call():
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                None,
                lambda: client.models.generate_content(
                    model=settings.gemini_model,
                    contents=user_prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=sys_inst,
                        response_mime_type="application/json",
                        temperature=0.1,
                    ),
                ),
            )

        resp = await asyncio.wait_for(_make_call(), timeout=LLM_TIMEOUT_SECONDS)
        raw_text = resp.text or ""
        data = json.loads(raw_text)
        return ProjectPlanOutput.model_validate(data)

    except (ImportError, Exception) as exc:
        logger.warning("Gemini LLM call failed or unavailable: %s. Using fallback planner.", exc)
        return _generate_fallback_plan(name, description, constraints)


async def plan_project(
    server_ctx: ServerContext,
    name: str,
    description: str,
    member_discord_ids: list[str],
    constraints: str | None = None,
    file_bytes: bytes | None = None,
    filename: str | None = None,
) -> dict[str, Any]:
    """Execute single-pass agentic project creation pipeline:
    Gather -> LLM Plan -> Persist Draft -> Evidence Assignment -> Return Summary Data.
    """
    ctx = ToolContext(
        server_id=server_ctx.discord_guild_id,
        discord_guild_id=server_ctx.discord_guild_id,
        discord_channel_id=server_ctx.discord_channel_id,
        requested_by_discord_id=server_ctx.requested_by_discord_id,
    )

    # STEP 1: Parse optional uploaded file
    file_text = ""
    file_extraction_info = {}
    if file_bytes and filename:
        parse_res = parse_document(file_bytes, filename)
        file_text = parse_res.get("text", "")
        if parse_res.get("warnings"):
            logger.warning("File parse warnings: %s", parse_res["warnings"])
        file_extraction_info = parse_res

    full_description = description
    if file_text:
        full_description += f"\n\n--- Attached Document ({filename}) ---\n{file_text}"

    # STEP 2: Gather team member profiles for selected IDs
    async with ctx.session() as session:
        server = await server_service.resolve_server(session, server_ctx, create=True)
        server_id = server.id

        members_info = []
        member_profiles = []
        for d_id in member_discord_ids:
            try:
                mem = await member_service.require_member(session, server_id, d_id)
                member_profiles.append(mem)
                skills_list = [f"{s.skill.name} ({s.proficiency}/5)" for s in mem.skills]
                active_count = await task_service.count_active_member_tasks(session, server_id, mem.id)
                members_info.append(
                    {
                        "member_id": str(mem.id),
                        "discord_user_id": mem.discord_user_id,
                        "display_name": mem.display_name,
                        "role": mem.role,
                        "skills": skills_list,
                        "active_task_count": active_count,
                    }
                )
            except Exception:
                logger.warning("Member %s not found on server %s", d_id, server_id)

    # STEP 3: Call Gemini Planner (with retry on validation error)
    plan_out: ProjectPlanOutput | None = None
    try:
        plan_out = await _call_gemini_planner(name, full_description, constraints, members_info)
    except Exception as exc:
        logger.warning("Initial LLM call failed: %s. Retrying once with stricter prompt.", exc)
        try:
            plan_out = await _call_gemini_planner(
                name, full_description, constraints, members_info, stricter_retry=True
            )
        except Exception as retry_exc:
            raise DomainError(f"Project planning failed: {retry_exc}") from retry_exc

    if not plan_out:
        raise DomainError("Project planning returned an empty plan.")

    # STEP 4: Persist Draft Project & Tasks (Single Transaction)
    # Generate project key from name
    cleaned_key = re.sub(r"[^a-zA-Z0-9]", "", name).upper()
    key = cleaned_key[:8] if cleaned_key else "PROJ"

    async with ctx.session() as session:
        # Check if project key already exists on server, append suffix if needed
        existing = (await session.execute(select(Project).where(Project.server_id == server_id, Project.key == key))).scalar_one_or_none()
        if existing:
            key = f"{key[:5]}{str(int(asyncio.get_event_loop().time()) % 1000).zfill(3)}"

        # Create Project row (status='draft')
        project = Project(
            server_id=server_id,
            key=key,
            name=name,
            description=full_description,
            status=ProjectStatus.DRAFT,
            plan_status=PlanStatus.DRAFT,
            plan_source=plan_out.plan_source,
            constraints=constraints,
        )
        session.add(project)
        await session.flush()

        # Add project members
        for mem in member_profiles:
            await project_service.add_project_member(session, project, mem)

        # Create Tasks & Task Dependencies
        created_tasks = []
        idx_to_task = {}
        for idx, task_item in enumerate(plan_out.tasks):
            task_key = f"{project.key}-{(idx + 1):03d}"
            skill_list = [{"name": s} for s in task_item.required_skills]

            # Build TaskCreate payload
            from app.schemas.task import TaskCreate

            t_payload = TaskCreate(
                context=server_ctx,
                project_key=project.key,
                key=task_key,
                title=task_item.title,
                description=task_item.description,
                required_skills=skill_list,
            )
            t_row = await task_service.create_task(session, server_id, project, t_payload)
            created_tasks.append(t_row)
            idx_to_task[idx] = t_row

            # Dependencies
            for dep_idx in task_item.depends_on_task_indices:
                if dep_idx in idx_to_task and dep_idx < idx:
                    dep_task = idx_to_task[dep_idx]
                    await task_service.add_dependency(session, server_id, t_row, depends_on_key=dep_task.key)

        # Record document extraction if file was parsed
        if file_extraction_info:
            from app.models.project import ProjectDocumentExtraction

            doc_ext = ProjectDocumentExtraction(
                project_id=project.id,
                extracted_tasks={"tasks": [t.model_dump() for t in plan_out.tasks]},
                extraction_notes=f"Parsed file: {filename}",
            )
            session.add(doc_ext)

        # STEP 5: Run Deterministic Assignment Engine for evidence calculation (Amendment 4)
        # Store in project.draft_assignments JSONB (DO NOT write to assignments/assignment_history)
        draft_assignments_map = {}
        for t_row in created_tasks:
            t_loaded_res = await session.execute(
                sa.select(Task)
                .options(sa.orm.selectinload(Task.required_skills).selectinload(TaskSkill.skill))
                .where(Task.id == t_row.id)
            )
            t_loaded = t_loaded_res.scalar_one()
            eval_res = await assignment_service.build_evaluation(session, server_id, t_loaded, project)
            # Filter ranked candidates by member_exclusions using member_id BEFORE scoring (Item 5)
            filtered_ranked = []
            if eval_res and eval_res.ranked:
                for cand in eval_res.ranked:
                    is_excluded = False
                    for excl in plan_out.constraints_parsed.member_exclusions:
                        # Item 2: STRICT MATCH ONLY BY member_id (exact UUID)
                        if excl.member_id and str(excl.member_id).lower() == str(cand.member_id).lower():
                            scope = (excl.scope or "all").lower()
                            task_text = f"{t_loaded.title} {t_loaded.description or ''}".lower()
                            task_skills = [s.skill.name.lower() for s in t_loaded.required_skills if s.skill]
                            if scope in ("all", "any") or scope in task_text or any(scope in sk for sk in task_skills):
                                is_excluded = True
                                break
                    if not is_excluded:
                        filtered_ranked.append(cand)

            best_cand = filtered_ranked[0] if filtered_ranked else None

            if best_cand:
                draft_assignments_map[t_row.key] = {
                    "task_id": str(t_row.id),
                    "task_key": t_row.key,
                    "task_title": t_row.title,
                    "assigned_member_id": str(best_cand.member_id),
                    "assigned_display_name": best_cand.display_name,
                    "assigned_discord_user_id": best_cand.discord_user_id,
                    "score": round(best_cand.score, 3),
                    "evidence_bullets": best_cand.reasons,
                }
            else:
                draft_assignments_map[t_row.key] = {
                    "task_id": str(t_row.id),
                    "task_key": t_row.key,
                    "task_title": t_row.title,
                    "assigned_member_id": None,
                    "assigned_display_name": "unassigned",
                    "assigned_discord_user_id": None,
                    "score": 0.0,
                    "evidence_bullets": ["Excluded by user constraint" if eval_res and eval_res.ranked else "No suitable candidate found"],
                }

        project.draft_assignments = draft_assignments_map
        await session.commit()

        return {
            "ok": True,
            "project_id": str(project.id),
            "project_key": project.key,
            "project_name": project.name,
            "plan_source": plan_out.plan_source,
            "reasoning": plan_out.reasoning,
            "constraints": constraints,
            "tasks_count": len(created_tasks),
            "draft_assignments": draft_assignments_map,
            "team_discord_ids": member_discord_ids,
        }
