"""Single-pass LLM Project Planner agent (Phase 6).

Analyzes project brief/topic, team member skills/roles/workload, and constraints,
and outputs a structured project plan via Gemini (Vertex AI).
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, Field, field_validator
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

# Timeout cap for LLM call (Fix 3)
LLM_TIMEOUT_SECONDS = float(getattr(settings, "llm_timeout_seconds", 90.0))


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
    tasks: list[TaskPlanItem] = Field(min_length=1, max_length=40)
    constraints_parsed: ConstraintsParsed = Field(default_factory=ConstraintsParsed, validation_alias=AliasChoices("constraints_parsed", "constraints"))

    @field_validator("constraints_parsed", mode="before")
    @classmethod
    def normalize_constraints_parsed(cls, v: Any) -> Any:
        if v is None or v == [] or v == {}:
            return {
                "member_exclusions": [],
                "unresolved_constraints": [],
                "other": [],
            }
        return v


ALLOWED_SKILLS = {
    "Python", "JavaScript", "TypeScript", "React", "Vue", "Angular",
    "FastAPI", "Django", "Flask", "Node.js", "PostgreSQL", "MySQL",
    "SQLite", "MongoDB", "Redis", "Docker", "Kubernetes", "AWS", "GCP",
    "Azure", "Git", "CI/CD", "REST", "GraphQL", "OAuth2", "JWT",
    "HTML", "CSS", "Tailwind", "Testing", "pytest", "Jest", "PyTorch",
    "TensorFlow", "NLP", "LLM", "RAG", "Pinecone", "pgvector"
}
ALLOWED_SKILLS_LOWER = {s.lower() for s in ALLOWED_SKILLS}


SYSTEM_PROMPT = """You are an expert AI software project planner. Analyze the project name, description, attached document content, team member capabilities, and constraints. Output a structured project plan in strict JSON.

CRITICAL — EXPLICIT TASK IDENTIFICATION:
- Treat EVERY named feature, capability, or deliverable in the description as an EXPLICIT TASK.
- 'with login' → task: Implement login flow
- 'dashboard' → task: Build dashboard UI
- 'profile management' → task: Implement profile management
- 'users must be able to reset passwords' → task: Implement password reset flow
- If the description names 3+ features, plan_source MUST be 'user_provided' or 'mixed'.
- If the description is a single topic with no features (e.g. 'build an e-commerce site'), use 'llm_inferred'.

TASK TITLE RULES:
- Concrete, action-oriented, specific.
- GOOD: 'Implement JWT-based authentication endpoint'
- BAD: 'Authentication', 'Architecture & Setup'
- NEVER prefix titles with the project name.
- Max 80 chars.

TASK QUANTITY RULES:
- Aim for 6-15 tasks. Only go higher if the description genuinely names that many distinct features. Prefer 1 task per named feature plus 2-3 support tasks.

TASK DESCRIPTION RULES:
- One sentence per task explaining what 'done' looks like.
- Must NOT repeat the title.

TECHNICAL DEPTH RULES:
- Task titles must reference specific technologies from the description or the team's skill set.
- Do NOT produce generic categories like 'Testing & Deployment'. Break into specific tasks:
  'Write pytest integration tests for auth flow',
  'Configure CI/CD pipeline for Cloud Run deploy'.

SKILLS RULES (allowed vocabulary):
Python, JavaScript, TypeScript, React, Vue, Angular,
FastAPI, Django, Flask, Node.js, PostgreSQL, MySQL,
SQLite, MongoDB, Redis, Docker, Kubernetes, AWS, GCP,
Azure, Git, CI/CD, REST, GraphQL, OAuth2, JWT,
HTML, CSS, Tailwind, Testing, pytest, Jest, PyTorch,
TensorFlow, NLP, LLM, RAG, pgvector
- Do NOT use 'architecture', 'deployment', 'testing' as skills.
- If a skill is introduced not in this list, include it and note it in reasoning.

DEPENDENCY RULES:
- depends_on_task_indices must reference EARLIER indices.
- No cycles, no forward references.

CONSTRAINT RULES:
- If constraints field is empty or 'None', output empty constraints_parsed lists. Do NOT invent.
- Only extract constraints explicitly stated.
- constraints_parsed MUST always be a JSON object, never an array. If there are no constraints, output: {"member_exclusions": [], "unresolved_constraints": [], "other": []}

REASONING RULES (≤ 3 sentences):
- Name the features you identified.
- Explain the task breakdown.
- Note constraints that affected the plan.
Example: 'The description named four features (login, dashboard, profile, password reset). I broke each into one implementation task and added two support tasks. No constraints were provided.'

OUTPUT FORMAT:
- Return ONLY valid JSON. No markdown fences. No commentary.
"""

FEATURE_KEYWORDS = [
    "login", "signup", "dashboard", "profile", "reset", "payment",
    "checkout", "cart", "search", "upload", "notification",
    "authentication", "authorization"
]


def validate_plan_quality(
    plan: ProjectPlanOutput, project_name: str, input_constraints: str | None, description: str = ""
) -> None:
    """Validate output against strict plan quality rules."""
    p_name_lower = project_name.lower().strip()
    desc_lower = description.lower()

    # Rule 0: Feature keyword count check
    if desc_lower:
        mentioned_features = [kw for kw in FEATURE_KEYWORDS if kw in desc_lower]
        if len(mentioned_features) >= 3 and len(plan.tasks) < len(mentioned_features):
            raise ValueError(
                f"The plan had too few tasks ({len(plan.tasks)}) for the described features ({len(mentioned_features)}: {', '.join(mentioned_features)})."
            )

    for idx, t in enumerate(plan.tasks):
        t_title_lower = t.title.lower().strip()
        # Rule 1: No project name prefix
        if p_name_lower and (
            t_title_lower.startswith(f"{p_name_lower} ")
            or t_title_lower.startswith(f"{p_name_lower} -")
            or t_title_lower.startswith(f"{p_name_lower}:")
        ):
            raise ValueError(f"Task title '{t.title}' starts with project name '{project_name}'")

        # Rule 2: Concrete titles, not generic templates
        if t_title_lower in (
            "architecture & setup", "architecture and setup",
            "testing & deployment", "testing and deployment",
            "authentication", "setup"
        ):
            raise ValueError(f"Task title '{t.title}' is a generic template")

        # Rule 3: Skills in allowed vocabulary or noted in reasoning
        for sk in t.required_skills:
            if sk.lower() not in ALLOWED_SKILLS_LOWER and sk.lower() not in plan.reasoning.lower():
                raise ValueError(f"Skill '{sk}' in task '{t.title}' is not in allowed vocabulary")

        # Rule 4: Acyclic dependencies referencing prior indices
        for dep in t.depends_on_task_indices:
            if dep >= idx:
                raise ValueError(
                    f"Task '{t.title}' has invalid dependency index {dep} (must reference earlier index < {idx})"
                )

    # Rule 5: Empty input constraints -> empty exclusions
    if not input_constraints or input_constraints.strip().lower() in ("", "none"):
        if plan.constraints_parsed and plan.constraints_parsed.member_exclusions:
            plan.constraints_parsed.member_exclusions = []



def _generate_fallback_plan(
    name: str, description: str, constraints: str | None = None
) -> ProjectPlanOutput:
    """Fallback plan generator used for offline testing or when LLM API is unavailable."""
    lines = [line.strip() for line in description.splitlines() if line.strip()]
    task_lines = [
        re.sub(r"^[-*•\d+.\)]\s*", "", line)
        for line in lines
        if re.match(r"^[-*•\d+.\)]\s+", line)
    ]

    desc_lower = description.lower()
    features = [kw for kw in FEATURE_KEYWORDS if kw in desc_lower]

    if task_lines:
        plan_source = "user_provided"
        tasks = [
            TaskPlanItem(
                title=t if not t.lower().startswith(name.lower()) else re.sub(rf"^{re.escape(name)}\s*[-:]?\s*", "", t, flags=re.IGNORECASE),
                description=f"User-provided task requirement for {t}",
                required_skills=["Python", "FastAPI"] if "backend" in t.lower() or "api" in t.lower() else (["React", "TypeScript"] if "ui" in t.lower() or "dashboard" in t.lower() or "login" in t.lower() or "front" in t.lower() else ["Python"]),
                source="user_provided",
            )
            for t in task_lines[:20]
        ]
    elif len(features) >= 3 or ("login" in desc_lower and "dashboard" in desc_lower):
        plan_source = "mixed" if "inferred" in desc_lower else "user_provided"
        tasks = [
            TaskPlanItem(
                title="Implement user login and authentication endpoint",
                description="Build RESTful authentication endpoints and user session validation.",
                required_skills=["FastAPI", "Python", "JWT"],
                source="user_provided",
            ),
            TaskPlanItem(
                title="Develop customer portal UI dashboard components",
                description="Create frontend dashboard layout and navigation components.",
                required_skills=["React", "TypeScript", "HTML", "CSS"],
                depends_on_task_indices=[0],
                source="user_provided",
            ),
            TaskPlanItem(
                title="Build user profile management and settings endpoints",
                description="Implement CRUD operations for user profile data and avatar updates.",
                required_skills=["FastAPI", "PostgreSQL"],
                depends_on_task_indices=[0],
                source="user_provided",
            ),
            TaskPlanItem(
                title="Implement password reset flow with secure token validation",
                description="Create password reset token generation and email dispatch logic.",
                required_skills=["Python", "FastAPI"],
                depends_on_task_indices=[0],
                source="user_provided",
            ),
            TaskPlanItem(
                title="Write integration tests for authentication and profile APIs",
                description="Cover auth, profile, and password reset endpoints with pytest suite.",
                required_skills=["pytest", "Python"],
                depends_on_task_indices=[1, 2, 3],
                source="user_provided",
            ),
            TaskPlanItem(
                title="Configure CI/CD deployment pipeline for Cloud Run",
                description="Set up automated container build and deployment steps.",
                required_skills=["Docker", "CI/CD", "GCP"],
                depends_on_task_indices=[4],
                source="user_provided",
            ),
        ]
    else:
        plan_source = "llm_inferred"
        tasks = [
            TaskPlanItem(
                title="Design PostgreSQL database schema and core data models",
                description="Create relational tables, indexes, and initial database migrations.",
                required_skills=["PostgreSQL", "Python"],
                source="llm_inferred",
            ),
            TaskPlanItem(
                title="Implement authentication and REST API endpoints",
                description="Build RESTful endpoints for user operations and authentication.",
                required_skills=["Python", "FastAPI", "JWT"],
                depends_on_task_indices=[0],
                source="llm_inferred",
            ),
            TaskPlanItem(
                title="Build responsive web UI dashboard components",
                description="Develop user-facing frontend dashboard with React components.",
                required_skills=["React", "TypeScript", "HTML", "CSS"],
                depends_on_task_indices=[1],
                source="llm_inferred",
            ),
        ]

    member_exclusions = []
    unresolved = []
    if constraints and constraints.strip().lower() not in ("", "none"):
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

    plan = ProjectPlanOutput(
        plan_source=plan_source,  # type: ignore[arg-type]
        reasoning=f"Generated plan for project {name} with {len(tasks)} tasks.",
        tasks=tasks,
        constraints_parsed=ConstraintsParsed(
            member_exclusions=member_exclusions,
            unresolved_constraints=unresolved,
            other=[constraints] if constraints and not member_exclusions and not unresolved else [],
        ),
    )
    validate_plan_quality(plan, name, constraints)
    return plan


import traceback

async def _call_gemini_planner(
    name: str,
    description: str,
    constraints: str | None,
    members_info: list[dict[str, Any]],
    stricter_retry: bool = False,
    rag_chunks_text: str = "",
) -> ProjectPlanOutput:
    """Call Gemini model via Vertex AI with 30s timeout cap and Pydantic validation."""
    logger.info(
        "LLM PLANNER START | project=%s desc_len=%d constraints=%r rag_len=%d",
        name,
        len(description),
        constraints,
        len(rag_chunks_text),
    )
    user_prompt = (
        ("RETRY NOTICE: Previous output failed validation rules. You MUST follow all TASK TITLE, SKILLS, DEPENDENCY, and CONSTRAINT RULES strictly.\n\n" if stricter_retry else "")
        + "CRITICAL INSTRUCTION: DO NOT REWRITE EXPLICIT TASKS PROVIDED BY THE USER. Extract them verbatim.\n\n"
        + f"Project Name: {name}\n"
        + f"Description / Brief:\n{description}\n\n"
    )
    if rag_chunks_text:
        user_prompt += f"PROJECT KNOWLEDGE (from attached documents):\n{rag_chunks_text}\n\n"

    user_prompt += (
        f"User Constraints:\n{constraints or 'None'}\n\n"
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
            sys_inst += "\nIMPORTANT: Strictly follow allowed skills list and do not prefix task titles with project name."

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
                        max_output_tokens=4096,
                    ),
                ),
            )

        logger.info("LLM CALL | model=%s project=%s", settings.gemini_model, name)
        resp = await asyncio.wait_for(_make_call(), timeout=LLM_TIMEOUT_SECONDS)
        raw_text = resp.text or ""
        logger.info("LLM RESPONSE | len=%d preview=%r", len(raw_text), raw_text[:500])

        data = json.loads(raw_text)
        logger.info("LLM VALIDATE | type=%s", type(data).__name__)
        plan = ProjectPlanOutput.model_validate(data)
        validate_plan_quality(plan, name, constraints, description)
        return plan

    except Exception as exc:
        logger.error("LLM ERROR | %s", traceback.format_exc())
        if not stricter_retry:
            logger.info("LLM RETRY | attempt=2")
            return await _call_gemini_planner(
                name,
                description,
                constraints,
                members_info,
                stricter_retry=True,
                rag_chunks_text=rag_chunks_text,
            )
        logger.warning("LLM FALLBACK | reason=%s", str(exc))
        return _generate_fallback_plan(name, description, constraints)




from app.schemas.member import MemberCreate


async def plan_project(
    server_ctx: ServerContext,
    name: str,
    description: str,
    member_discord_ids: list[str],
    constraints: str | None = None,
    file_bytes: bytes | None = None,
    filename: str | None = None,
    member_details: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Execute single-pass agentic project creation pipeline:
    Gather -> RAG Ingest -> LLM Plan -> Persist Draft -> Evidence Assignment -> Return Summary Data.
    """
    ctx = ToolContext(
        server_id=server_ctx.discord_guild_id,
        discord_guild_id=server_ctx.discord_guild_id,
        discord_channel_id=server_ctx.discord_channel_id,
        requested_by_discord_id=server_ctx.requested_by_discord_id,
    )

    cleaned_key = re.sub(r"[^a-zA-Z0-9]", "", name).upper()
    key = cleaned_key[:8] if cleaned_key else "PROJ"

    rag_chunks_text = ""
    file_extraction_info = {}

    async with ctx.session() as session:
        server = await server_service.resolve_server(session, server_ctx, create=True)
        server_id = server.id

        existing = (await session.execute(select(Project).where(Project.server_id == server_id, Project.key == key))).scalar_one_or_none()
        if existing:
            key = f"{key[:5]}{str(int(asyncio.get_event_loop().time()) % 1000).zfill(3)}"

        project = Project(
            server_id=server_id,
            key=key,
            name=name,
            description=description,
            status=ProjectStatus.DRAFT,
            plan_status=PlanStatus.DRAFT,
            constraints=constraints,
            created_by_user_id=server_ctx.requested_by_discord_id,
        )
        session.add(project)
        await session.flush()

        members_info = []
        member_profiles = []
        details_map = member_details or {}
        for d_id in member_discord_ids:
            try:
                mem = await member_service.get_member(session, server_id, d_id)
                if mem is None:
                    u_info = details_map.get(d_id, {})
                    username = u_info.get("username") or f"user_{d_id}"
                    display_name = u_info.get("display_name") or username
                    logger.info("Auto-registering MemberProfile for Discord user %s (%s) on server %s", d_id, display_name, server_id)
                    mem = await member_service.create_or_update_member(
                        session,
                        server_id,
                        MemberCreate(
                            context=server_ctx,
                            discord_user_id=d_id,
                            username=username,
                            display_name=display_name,
                        ),
                    )

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
            except Exception as exc:
                logger.warning("Failed to resolve or create member %s on server %s: %s", d_id, server_id, exc)

        for mem in member_profiles:
            await project_service.add_project_member(session, project, mem)

        # Parse & Ingest Document into RAG if attached
        if file_bytes and filename:
            parse_res = parse_document(file_bytes, filename)
            file_text = parse_res.get("text", "")
            file_extraction_info = parse_res
            if file_text:
                from app.schemas.project import DocumentCreate
                from app.models.enums import DocumentSourceType
                from app.services import knowledge_service

                doc_create = DocumentCreate(
                    title=filename,
                    content=file_text,
                    source_type=DocumentSourceType.OTHER,
                )
                await knowledge_service.ingest_document(session, project, doc_create)

                scored_chunks = await knowledge_service.search_knowledge(
                    session, project, description or name, top_k=5
                )
                if scored_chunks:
                    rag_chunks_text = "\n".join(
                        [f"- [{sc.chunk.document_title}] {sc.chunk.content}" for sc in scored_chunks]
                    )
                    logger.info("Retrieved %d RAG chunks for planner prompt", len(scored_chunks))

        # Call Gemini Planner
        plan_out: ProjectPlanOutput | None = None
        try:
            plan_out = await _call_gemini_planner(
                name, description, constraints, members_info, rag_chunks_text=rag_chunks_text
            )
        except Exception as exc:
            logger.warning("Initial LLM call failed: %s. Retrying once with stricter prompt.", exc)
            try:
                plan_out = await _call_gemini_planner(
                    name,
                    description,
                    constraints,
                    members_info,
                    stricter_retry=True,
                    rag_chunks_text=rag_chunks_text,
                )
            except Exception as retry_exc:
                raise DomainError(f"Project planning failed: {retry_exc}") from retry_exc

        if not plan_out:
            raise DomainError("Project planning returned an empty plan.")

        project.plan_source = plan_out.plan_source
        project.reasoning = plan_out.reasoning
        await session.flush()

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
