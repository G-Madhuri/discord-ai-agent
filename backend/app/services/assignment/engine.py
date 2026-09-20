"""Deterministic candidate-scoring engine.

This is pure domain logic: no database, no network, no LLM. It takes a task,
a set of candidates and the project knowledge that was retrieved for them, and
returns a ranked, fully explained evaluation.

Keeping it deterministic matters for two reasons:

* every decision is reproducible and auditable from the stored evidence;
* the LLM agent can reason *about* the ranking, but it cannot conjure skills,
  members or scores that the data does not support.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.core.text import slugify
from app.models.enums import AvailabilityStatus
from app.schemas.assignment import (
    AssignmentEvaluation,
    CandidateEvidence,
    CandidateScore,
    ComponentScores,
    DependencyEvidence,
    ExcludedCandidate,
    ExperienceEvidence,
    KnowledgeEvidence,
    SkillEvidence,
    WorkloadEvidence,
)
from app.schemas.task import DependencyRead

ENGINE_VERSION = "1.0.0"

# Availability that disqualifies a candidate outright, with the audit reason.
_HARD_EXCLUSIONS: dict[AvailabilityStatus, str] = {
    AvailabilityStatus.ON_LEAVE: "member is on leave",
    AvailabilityStatus.INACTIVE: "member is marked inactive",
}
_BUSY_MULTIPLIER = 0.85
_NEUTRAL = 0.5


@dataclass(frozen=True, slots=True)
class SkillRequirement:
    slug: str
    name: str
    weight: float = 1.0
    is_required: bool = True


@dataclass(frozen=True, slots=True)
class CandidateSkill:
    slug: str
    name: str
    proficiency: int = 3
    years_experience: float | None = None


@dataclass(frozen=True, slots=True)
class KnowledgeSnippet:
    document_title: str
    chunk_id: uuid.UUID
    content: str
    score: float


@dataclass(frozen=True, slots=True)
class TaskInput:
    key: str
    title: str
    description: str | None = None
    role_hint: str | None = None
    requirements: tuple[SkillRequirement, ...] = ()
    labels: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CandidateInput:
    member_id: uuid.UUID
    discord_user_id: str
    display_name: str
    availability: AvailabilityStatus = AvailabilityStatus.AVAILABLE
    role: str | None = None
    project_role: str | None = None
    seniority: str | None = None
    years_experience: float | None = None
    skills: tuple[CandidateSkill, ...] = ()
    active_task_keys: tuple[str, ...] = ()
    capacity: int = 3
    completed_related_task_keys: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DependencyState:
    unresolved: tuple[DependencyRead, ...] = ()
    resolved: tuple[DependencyRead, ...] = ()

    @property
    def is_blocked(self) -> bool:
        return bool(self.unresolved)


@dataclass(frozen=True, slots=True)
class Weights:
    skill: float = 0.45
    role: float = 0.15
    experience: float = 0.15
    knowledge: float = 0.10
    workload: float = 0.15

    def as_dict(self) -> dict[str, float]:
        return {
            "skill": self.skill,
            "role": self.role,
            "experience": self.experience,
            "knowledge": self.knowledge,
            "workload": self.workload,
        }

    @property
    def total(self) -> float:
        return sum(self.as_dict().values()) or 1.0


@dataclass(frozen=True, slots=True)
class EvaluationInput:
    task: TaskInput
    project_key: str
    candidates: tuple[CandidateInput, ...] = ()
    dependencies: DependencyState = field(default_factory=DependencyState)
    knowledge: tuple[KnowledgeSnippet, ...] = ()
    weights: Weights = field(default_factory=Weights)


def _round(value: float) -> float:
    return round(max(0.0, min(1.0, value)), 4)


def _tokens(value: str | None) -> set[str]:
    if not value:
        return set()
    return {tok for tok in slugify(value).split("-") if len(tok) > 2}


def _score_skills(
    task: TaskInput, candidate: CandidateInput
) -> tuple[float, list[SkillEvidence], list[SkillEvidence]]:
    """Weighted coverage of the task's declared skills by the member's skills."""
    by_slug = {s.slug: s for s in candidate.skills}
    matched: list[SkillEvidence] = []
    missing: list[SkillEvidence] = []

    if not task.requirements:
        # No declared requirements: this component carries no signal, so stay
        # neutral rather than inventing a match.
        return _NEUTRAL, matched, missing

    total_weight = sum(r.weight for r in task.requirements) or 1.0
    earned = 0.0
    for req in task.requirements:
        held = by_slug.get(req.slug)
        if held is None:
            missing.append(
                SkillEvidence(slug=req.slug, name=req.name, weight=req.weight, matched=False)
            )
            continue
        proficiency = 0.8 * (held.proficiency / 5.0)
        recency = 0.2 * min((held.years_experience or 0.0) / 5.0, 1.0)
        earned += req.weight * (proficiency + recency)
        matched.append(
            SkillEvidence(
                slug=req.slug,
                name=held.name,
                weight=req.weight,
                proficiency=held.proficiency,
                years_experience=held.years_experience,
                matched=True,
            )
        )
    return _round(earned / total_weight), matched, missing


def _score_role(task: TaskInput, candidate: CandidateInput) -> tuple[float, str]:
    hint = _tokens(task.role_hint) | {slugify(label) for label in task.labels if label}
    hint = {t for t in hint if t}
    if not hint:
        return _NEUTRAL, "no_role_signal"

    held = _tokens(candidate.role) | _tokens(candidate.project_role)
    if not held:
        return _NEUTRAL, "member_role_unknown"

    if slugify(task.role_hint or "") and slugify(task.role_hint or "") in {
        slugify(candidate.role or ""),
        slugify(candidate.project_role or ""),
    }:
        return 1.0, "exact"
    if held & hint:
        return 0.75, "partial"
    return 0.2, "none"


def _score_experience(
    candidate: CandidateInput,
) -> tuple[float, ExperienceEvidence]:
    completed = len(candidate.completed_related_task_keys)
    depth = min(completed / 3.0, 1.0)
    tenure = min((candidate.years_experience or 0.0) / 8.0, 1.0)
    evidence = ExperienceEvidence(
        completed_task_count=completed,
        related_task_keys=list(candidate.completed_related_task_keys),
        years_experience=candidate.years_experience,
    )
    return _round(0.7 * depth + 0.3 * tenure), evidence


def _score_knowledge(
    candidate: CandidateInput, snippets: Sequence[KnowledgeSnippet]
) -> tuple[float, list[KnowledgeEvidence]]:
    """Reward candidates whose skills actually appear in the project's docs.

    This is the RAG layer earning its keep: project documentation stating
    'the dashboard uses React + Recharts' is what connects a React member to a
    dashboard task.
    """
    if not snippets or not candidate.skills:
        return 0.0, []

    terms = {s.name.lower(): s.name for s in candidate.skills}
    terms.update({s.slug.replace("-", " "): s.name for s in candidate.skills})

    evidence: list[KnowledgeEvidence] = []
    for snippet in snippets:
        haystack = snippet.content.lower()
        hit_terms = sorted(
            {
                display
                for term, display in terms.items()
                if term and re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", haystack)
            }
        )
        if not hit_terms:
            continue
        evidence.append(
            KnowledgeEvidence(
                document_title=snippet.document_title,
                chunk_id=snippet.chunk_id,
                excerpt=_excerpt(snippet.content),
                score=round(snippet.score, 4),
                matched_terms=hit_terms,
            )
        )

    if not evidence:
        return 0.0, []
    coverage = min(len(evidence) / 2.0, 1.0)
    relevance = sum(e.score for e in evidence) / len(evidence)
    return _round(0.6 * coverage + 0.4 * min(relevance, 1.0)), evidence


def _score_workload(candidate: CandidateInput) -> tuple[float, WorkloadEvidence]:
    capacity = max(candidate.capacity, 1)
    active = len(candidate.active_task_keys)
    utilisation = active / capacity
    evidence = WorkloadEvidence(
        active_task_count=active,
        capacity=capacity,
        utilisation=round(utilisation, 4),
        active_task_keys=list(candidate.active_task_keys),
    )
    return _round(1.0 - utilisation), evidence


def _excerpt(content: str, limit: int = 220) -> str:
    text = " ".join(content.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _build_reasons(
    matched: Sequence[SkillEvidence],
    missing: Sequence[SkillEvidence],
    role_match: str,
    candidate: CandidateInput,
    workload: WorkloadEvidence,
    experience: ExperienceEvidence,
    knowledge: Sequence[KnowledgeEvidence],
    dependencies: DependencyState,
) -> list[str]:
    """Short, factual bullet points — exactly what Discord shows."""
    reasons: list[str] = []

    if matched:
        names = ", ".join(m.name for m in matched[:4])
        best = max(m.proficiency or 0 for m in matched)
        reasons.append(f"Skill match: {names} (top proficiency {best}/5)")
    if missing:
        reasons.append(f"No declared skill for: {', '.join(m.name for m in missing[:4])}")

    if role_match == "exact":
        reasons.append(f"Role matches the task: {candidate.role or candidate.project_role}")
    elif role_match == "partial":
        reasons.append(f"Related role: {candidate.role or candidate.project_role}")

    if knowledge:
        top = knowledge[0]
        reasons.append(
            f"Project knowledge: '{top.document_title}' mentions {', '.join(top.matched_terms[:3])}"
        )

    if experience.completed_task_count:
        reasons.append(
            f"Previously completed {experience.completed_task_count} related task(s): "
            + ", ".join(experience.related_task_keys[:3])
        )

    reasons.append(
        f"Current workload: {workload.active_task_count} active task(s) of {workload.capacity}"
    )

    if dependencies.is_blocked:
        blockers = ", ".join(f"{d.task_key} ({d.status})" for d in dependencies.unresolved[:3])
        reasons.append(f"Warning — unresolved dependency: {blockers}")
    else:
        reasons.append("No unresolved dependency blocking the task")

    if candidate.availability is AvailabilityStatus.BUSY:
        reasons.append("Member is marked busy — score reduced")

    return reasons


def evaluate_candidates(data: EvaluationInput) -> AssignmentEvaluation:
    """Rank candidates for a task and explain every score."""
    weights = data.weights
    ranked: list[CandidateScore] = []
    excluded: list[ExcludedCandidate] = []

    for candidate in data.candidates:
        hard_reason = _HARD_EXCLUSIONS.get(candidate.availability)
        if hard_reason:
            excluded.append(
                ExcludedCandidate(
                    member_id=candidate.member_id,
                    display_name=candidate.display_name,
                    reason=hard_reason,
                )
            )
            continue

        skill_score, matched, missing = _score_skills(data.task, candidate)
        role_score, role_match = _score_role(data.task, candidate)
        experience_score, experience = _score_experience(candidate)
        knowledge_score, knowledge = _score_knowledge(candidate, data.knowledge)
        workload_score, workload = _score_workload(candidate)

        weighted = (
            weights.skill * skill_score
            + weights.role * role_score
            + weights.experience * experience_score
            + weights.knowledge * knowledge_score
            + weights.workload * workload_score
        ) / weights.total

        if candidate.availability is AvailabilityStatus.BUSY:
            weighted *= _BUSY_MULTIPLIER

        ranked.append(
            CandidateScore(
                member_id=candidate.member_id,
                discord_user_id=candidate.discord_user_id,
                display_name=candidate.display_name,
                score=_round(weighted),
                components=ComponentScores(
                    skill=skill_score,
                    role=role_score,
                    experience=experience_score,
                    knowledge=knowledge_score,
                    workload=workload_score,
                ),
                evidence=CandidateEvidence(
                    matched_skills=matched,
                    missing_skills=missing,
                    role=candidate.role,
                    project_role=candidate.project_role,
                    role_match=role_match,
                    workload=workload,
                    experience=experience,
                    knowledge=knowledge,
                    availability=str(candidate.availability),
                ),
                reasons=_build_reasons(
                    matched,
                    missing,
                    role_match,
                    candidate,
                    workload,
                    experience,
                    knowledge,
                    data.dependencies,
                ),
            )
        )

    # Deterministic ordering: score, then lighter workload, then name.
    ranked.sort(
        key=lambda c: (
            -c.score,
            c.evidence.workload.active_task_count,
            c.display_name.lower(),
        )
    )

    return AssignmentEvaluation(
        task_key=data.task.key,
        task_title=data.task.title,
        project_key=data.project_key,
        engine_version=ENGINE_VERSION,
        weights=weights.as_dict(),
        dependencies=DependencyEvidence(
            is_blocked=data.dependencies.is_blocked,
            unresolved=list(data.dependencies.unresolved),
        ),
        ranked=ranked,
        excluded=excluded,
        knowledge_used=_dedupe_knowledge(ranked),
        generated_at=datetime.now(tz=UTC),
    )


def _dedupe_knowledge(ranked: Iterable[CandidateScore]) -> list[KnowledgeEvidence]:
    seen: dict[uuid.UUID, KnowledgeEvidence] = {}
    for candidate in ranked:
        for hit in candidate.evidence.knowledge:
            seen.setdefault(hit.chunk_id, hit)
    return list(seen.values())
