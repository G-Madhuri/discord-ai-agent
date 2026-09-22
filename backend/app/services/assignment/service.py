"""Assignment workflow orchestration.

This is the only place a task gets assigned. It runs the documented sequence:

  1. identify the Discord server          9. check task dependencies
  2. identify the project                10. evaluate candidates
  3. identify the requested task         11. select a candidate on the evidence
  4. read the existing task              12. assign the existing task
  5. retrieve relevant project knowledge 13. persist assignment + history
  6. retrieve eligible team members      14. return a concise explanation
  7. retrieve skills/roles/experience    15. (caller) send the result to Discord
  8. retrieve current workload

It never creates tasks and never touches a project plan.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import ConflictError, NoEligibleCandidateError, NotFoundError
from app.core.logging import get_logger
from app.models.assignment import Assignment, AssignmentHistory
from app.models.enums import (
    AssignmentEvent,
    AssignmentStatus,
    DecisionMode,
    TaskStatus,
)
from app.models.member import MemberProfile
from app.models.project import Project
from app.models.task import Task
from app.schemas.assignment import (
    AssignmentEvaluation,
    AssignmentHistoryEntry,
    AssignmentRead,
    AssignmentResult,
    CandidateScore,
)
from app.services import knowledge_service, project_service, task_service
from app.services.assignment.engine import (
    ENGINE_VERSION,
    CandidateInput,
    CandidateSkill,
    DependencyState,
    EvaluationInput,
    KnowledgeSnippet,
    SkillRequirement,
    TaskInput,
    Weights,
    evaluate_candidates,
)

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class AssignmentContext:
    """Everything the engine needs, gathered once per request."""

    project: Project
    task: Task
    evaluation: AssignmentEvaluation


def _weights() -> Weights:
    w = settings.assignment_weights
    return Weights(
        skill=w.skill,
        role=w.role,
        experience=w.experience,
        knowledge=w.knowledge,
        workload=w.workload,
    )


async def _completed_related_tasks(
    session: AsyncSession,
    server_id: uuid.UUID,
    project_id: uuid.UUID,
    member_ids: list[uuid.UUID],
    required_slugs: set[str],
) -> dict[uuid.UUID, list[str]]:
    """Past work in this project that needed at least one of the same skills."""
    if not member_ids:
        return {}

    from app.models.skill import Skill
    from app.models.task import TaskSkill

    stmt = (
        sa.select(Assignment.member_profile_id, Task.key)
        .join(Task, Task.id == Assignment.task_id)
        .where(
            Assignment.server_id == server_id,
            Assignment.project_id == project_id,
            Assignment.member_profile_id.in_(member_ids),
            Assignment.status.in_([AssignmentStatus.ACTIVE, AssignmentStatus.COMPLETED]),
            Task.status == TaskStatus.DONE,
        )
    )
    if required_slugs:
        stmt = stmt.where(
            Task.id.in_(
                sa.select(TaskSkill.task_id)
                .join(Skill, Skill.id == TaskSkill.skill_id)
                .where(Skill.slug.in_(required_slugs))
            )
        )

    history: dict[uuid.UUID, list[str]] = {}
    for member_id, task_key in (await session.execute(stmt)).all():
        history.setdefault(member_id, []).append(task_key)
    return history


async def build_evaluation(
    session: AsyncSession,
    server_id: uuid.UUID,
    task: Task,
    project: Project,
    *,
    working_workload: dict[uuid.UUID, int] | None = None,
) -> AssignmentEvaluation:
    """Steps 4–10: gather evidence and rank candidates. Read-only."""
    requirements = tuple(
        SkillRequirement(
            slug=ts.skill.slug, name=ts.skill.name, weight=ts.weight, is_required=ts.is_required
        )
        for ts in task.required_skills
        if ts.skill is not None
    )
    required_slugs = {r.slug for r in requirements}

    # 5. relevant project knowledge
    query = knowledge_service.build_task_query(
        task.title, task.description, [r.name for r in requirements]
    )
    hits = await knowledge_service.search_knowledge(session, project, query)
    snippets = tuple(
        KnowledgeSnippet(
            document_title=hit.chunk.document_title,
            chunk_id=hit.chunk.chunk_id,
            content=hit.chunk.content,
            score=hit.score,
        )
        for hit in hits
    )

    # 6-7. eligible members with skills, role and experience
    member_rows = await project_service.list_project_members(session, server_id, project.id)
    member_ids = [member.id for _, member in member_rows]

    # 8. current workload
    workload = await project_service.count_active_assignments(session, server_id, member_ids)
    prior = await _completed_related_tasks(
        session, server_id, project.id, member_ids, required_slugs
    )

    candidates_list = []
    for link, member in member_rows:
        db_keys = list(workload.get(member.id, []))
        if working_workload is not None and member.id in working_workload:
            target_count = working_workload[member.id]
            if target_count > len(db_keys):
                extra = target_count - len(db_keys)
                active_keys = tuple(db_keys + [f"working_task_{i}" for i in range(extra)])
            else:
                active_keys = tuple(db_keys[:target_count])
        else:
            active_keys = tuple(db_keys)

        candidates_list.append(
            CandidateInput(
                member_id=member.id,
                discord_user_id=member.user.discord_user_id if member.user else "",
                display_name=member.display_name,
                availability=member.availability,
                role=member.role,
                project_role=link.project_role,
                seniority=member.seniority,
                years_experience=member.years_experience,
                skills=tuple(
                    CandidateSkill(
                        slug=ms.skill.slug,
                        name=ms.skill.name,
                        proficiency=ms.proficiency,
                        years_experience=ms.years_experience,
                    )
                    for ms in member.skills
                    if ms.skill is not None
                ),
                active_task_keys=active_keys,
                capacity=member.max_concurrent_tasks or settings.default_member_capacity,
                completed_related_task_keys=tuple(prior.get(member.id, [])),
            )
        )
    candidates = tuple(candidates_list)

    # 9. dependencies
    dependency_check = await task_service.check_dependencies(session, task)

    # 10. evaluate
    return evaluate_candidates(
        EvaluationInput(
            task=TaskInput(
                key=task.key,
                title=task.title,
                description=task.description,
                role_hint=task.role_hint,
                requirements=requirements,
                labels=tuple(task.labels or []),
            ),
            project_key=project.key,
            candidates=candidates,
            dependencies=DependencyState(
                unresolved=tuple(dependency_check.unresolved),
                resolved=tuple(dependency_check.resolved),
            ),
            knowledge=snippets,
            weights=_weights(),
        )
    )


async def load_context(
    session: AsyncSession,
    server_id: uuid.UUID,
    task_key: str,
    *,
    project_key: str | None = None,
    discord_channel_id: str | None = None,
) -> AssignmentContext:
    """Steps 1–10, resolving the project and task first."""
    if project_key:
        project = await project_service.require_project(session, server_id, project_key)
        task = await task_service.require_task(session, server_id, task_key, project_id=project.id)
    else:
        task = await task_service.require_task(session, server_id, task_key)
        project = await session.get(Project, task.project_id)
        if project is None or project.server_id != server_id:
            raise NotFoundError("Project for this task was not found in this Discord server")

    evaluation = await build_evaluation(session, server_id, task, project)
    return AssignmentContext(project=project, task=task, evaluation=evaluation)


def _format_message(task: Task, candidate_name: str, reasons: list[str]) -> str:
    bullets = "\n".join(f"• {reason}" for reason in reasons)
    return f"✅ {task.key} assigned to {candidate_name}.\nWhy:\n{bullets}"


async def _supersede_existing(
    session: AsyncSession,
    existing: Assignment,
    *,
    actor: str | None,
    reason: str | None,
) -> None:
    session.add(
        AssignmentHistory(
            server_id=existing.server_id,
            assignment_id=existing.id,
            task_id=existing.task_id,
            member_profile_id=existing.member_profile_id,
            event=AssignmentEvent.REASSIGNED,
            from_status=existing.status,
            to_status=AssignmentStatus.REASSIGNED,
            actor=actor,
            reason=reason or "superseded by a new assignment",
            evidence_snapshot=existing.evidence or {},
        )
    )
    existing.status = AssignmentStatus.REASSIGNED
    await session.flush()


async def persist_assignment(
    session: AsyncSession,
    *,
    server_id: uuid.UUID,
    project: Project,
    task: Task,
    member: MemberProfile,
    evaluation: AssignmentEvaluation,
    candidate: CandidateScore | None,
    decision_mode: DecisionMode,
    requested_by_discord_id: str | None,
    rationale: str,
    reassign: bool,
    agent_model: str | None = None,
) -> Assignment:
    """Steps 12–13. Writes the assignment and its audit record together."""
    existing = await task_service.get_live_assignment(session, task.id)
    if existing is not None:
        if not reassign:
            raise ConflictError(
                f"{task.key} is already assigned to {existing.member.display_name}. "
                "Use reassign to override.",
                details={"task_key": task.key, "assignee": existing.member.display_name},
            )
        await _supersede_existing(
            session, existing, actor=requested_by_discord_id, reason=rationale
        )

    assignment = Assignment(
        server_id=server_id,
        project_id=project.id,
        task_id=task.id,
        member_profile_id=member.id,
        status=AssignmentStatus.ACTIVE,
        decision_mode=decision_mode,
        requested_by_discord_id=requested_by_discord_id,
        score=candidate.score if candidate else None,
        rationale=rationale,
        # The full evaluation is the audit record: scores, evidence and the
        # candidates that were considered and rejected.
        evidence=evaluation.model_dump(mode="json"),
        agent_model=agent_model,
        engine_version=ENGINE_VERSION,
    )
    session.add(assignment)
    await session.flush()

    session.add(
        AssignmentHistory(
            server_id=server_id,
            assignment_id=assignment.id,
            task_id=task.id,
            member_profile_id=member.id,
            event=AssignmentEvent.CREATED,
            from_status=None,
            to_status=AssignmentStatus.ACTIVE,
            actor=requested_by_discord_id,
            reason=rationale,
            evidence_snapshot=assignment.evidence,
        )
    )

    if task.status is TaskStatus.BACKLOG:
        task.status = TaskStatus.TODO
    await session.flush()
    return assignment


def to_assignment_read(
    assignment: Assignment, task: Task, project: Project, member: MemberProfile
) -> AssignmentRead:
    return AssignmentRead(
        id=assignment.id,
        task_key=task.key,
        task_title=task.title,
        project_key=project.key,
        member_id=member.id,
        member_display_name=member.display_name,
        member_discord_id=member.user.discord_user_id if member.user else "",
        status=assignment.status,
        decision_mode=assignment.decision_mode,
        score=assignment.score,
        rationale=assignment.rationale,
        assigned_at=assignment.assigned_at,
    )


async def assign_task(
    session: AsyncSession,
    *,
    server_id: uuid.UUID,
    task_key: str,
    project_key: str | None = None,
    discord_channel_id: str | None = None,
    member_discord_id: str | None = None,
    requested_by_discord_id: str | None = None,
    reassign: bool = False,
    decision_mode: DecisionMode = DecisionMode.DETERMINISTIC,
    note: str | None = None,
) -> AssignmentResult:
    """Full workflow. `member_discord_id` forces an assignee but still records
    the evidence, so a manual override is auditable on the same terms."""
    from app.services import member_service

    context = await load_context(
        session,
        server_id,
        task_key,
        project_key=project_key,
        discord_channel_id=discord_channel_id,
    )
    evaluation = context.evaluation

    if member_discord_id:
        member = await member_service.require_member(session, server_id, member_discord_id)
        candidate = next((c for c in evaluation.ranked if c.member_id == member.id), None)
        mode = DecisionMode.LLM if decision_mode == DecisionMode.LLM else DecisionMode.MANUAL
        reasons = (
            list(candidate.reasons)
            if candidate
            else ["Assignee chosen explicitly by the requester"]
        )
    else:
        candidate = evaluation.best
        if candidate is None:
            raise NoEligibleCandidateError(
                f"No eligible member is available for {context.task.key} in project "
                f"{context.project.key}",
                details={
                    "task_key": context.task.key,
                    "excluded": [e.model_dump(mode="json") for e in evaluation.excluded],
                },
            )
        member = await member_service.get_member_by_id(session, server_id, candidate.member_id)
        mode = decision_mode
        reasons = list(candidate.reasons)

    if note:
        reasons.append(f"Requester note: {note}")

    rationale = " | ".join(reasons)
    assignment = await persist_assignment(
        session,
        server_id=server_id,
        project=context.project,
        task=context.task,
        member=member,
        evaluation=evaluation,
        candidate=candidate,
        decision_mode=mode,
        requested_by_discord_id=requested_by_discord_id,
        rationale=rationale,
        reassign=reassign,
    )

    logger.info(
        "assigned %s to %s (mode=%s, score=%s)",
        context.task.key,
        member.display_name,
        mode,
        assignment.score,
    )

    return AssignmentResult(
        assignment=to_assignment_read(assignment, context.task, context.project, member),
        reasons=reasons,
        evaluation=evaluation,
        message=_format_message(context.task, member.display_name, reasons),
    )


async def get_assignment_history(
    session: AsyncSession,
    server_id: uuid.UUID,
    *,
    task_key: str | None = None,
    member_discord_id: str | None = None,
    limit: int = 20,
) -> list[AssignmentHistoryEntry]:
    stmt = (
        sa.select(AssignmentHistory, Task.key, MemberProfile.display_name)
        .join(Task, Task.id == AssignmentHistory.task_id)
        .outerjoin(MemberProfile, MemberProfile.id == AssignmentHistory.member_profile_id)
        .where(AssignmentHistory.server_id == server_id)
        .order_by(AssignmentHistory.created_at.desc())
        .limit(limit)
    )
    if task_key:
        stmt = stmt.where(Task.key == task_key.strip().upper())
    if member_discord_id:
        from app.models.user import User

        stmt = stmt.join(User, User.id == MemberProfile.user_id).where(
            User.discord_user_id == member_discord_id
        )

    rows = (await session.execute(stmt)).all()
    return [
        AssignmentHistoryEntry(
            id=history.id,
            task_key=key,
            member_display_name=display_name,
            event=history.event,
            from_status=history.from_status,
            to_status=history.to_status,
            actor=history.actor,
            reason=history.reason,
            created_at=history.created_at,
        )
        for history, key, display_name in rows
    ]
