from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import AssignmentEvent, AssignmentStatus, DecisionMode
from app.schemas.common import ServerContext
from app.schemas.task import DependencyRead


class AssignRequest(BaseModel):
    """Assign an EXISTING task. Never creates or modifies a plan."""

    context: ServerContext
    task_key: str = Field(min_length=1, max_length=32)
    project_key: str | None = None
    member_discord_id: str | None = Field(
        default=None,
        description="Force a specific assignee. Omit to let the engine choose.",
    )
    reassign: bool = Field(
        default=False, description="Allow superseding an existing live assignment."
    )
    note: str | None = None


class SkillEvidence(BaseModel):
    slug: str
    name: str
    weight: float
    proficiency: int | None = None
    years_experience: float | None = None
    matched: bool


class KnowledgeEvidence(BaseModel):
    document_title: str
    chunk_id: uuid.UUID
    excerpt: str
    score: float
    matched_terms: list[str] = Field(default_factory=list)


class WorkloadEvidence(BaseModel):
    active_task_count: int
    capacity: int
    utilisation: float
    active_task_keys: list[str] = Field(default_factory=list)


class ExperienceEvidence(BaseModel):
    completed_task_count: int
    related_task_keys: list[str] = Field(default_factory=list)
    years_experience: float | None = None


class CandidateEvidence(BaseModel):
    """Everything that justified (or did not justify) a candidate's score."""

    matched_skills: list[SkillEvidence] = Field(default_factory=list)
    missing_skills: list[SkillEvidence] = Field(default_factory=list)
    role: str | None = None
    project_role: str | None = None
    role_match: str = "unknown"
    workload: WorkloadEvidence
    experience: ExperienceEvidence
    knowledge: list[KnowledgeEvidence] = Field(default_factory=list)
    availability: str


class ComponentScores(BaseModel):
    skill: float
    role: float
    experience: float
    knowledge: float
    workload: float


class CandidateScore(BaseModel):
    member_id: uuid.UUID
    discord_user_id: str
    display_name: str
    score: float
    components: ComponentScores
    evidence: CandidateEvidence
    reasons: list[str] = Field(default_factory=list)


class ExcludedCandidate(BaseModel):
    member_id: uuid.UUID
    display_name: str
    reason: str


class DependencyEvidence(BaseModel):
    is_blocked: bool
    unresolved: list[DependencyRead] = Field(default_factory=list)


class AssignmentEvaluation(BaseModel):
    """Ranked candidates plus the evidence behind the ranking.

    This is what the agent reads before choosing; it is also what gets stored
    on the assignment for later audit.
    """

    task_key: str
    task_title: str
    project_key: str
    engine_version: str
    weights: dict[str, float]
    dependencies: DependencyEvidence
    ranked: list[CandidateScore] = Field(default_factory=list)
    excluded: list[ExcludedCandidate] = Field(default_factory=list)
    knowledge_used: list[KnowledgeEvidence] = Field(default_factory=list)
    generated_at: datetime

    @property
    def best(self) -> CandidateScore | None:
        return self.ranked[0] if self.ranked else None


class AssignmentRead(BaseModel):
    id: uuid.UUID
    task_key: str
    task_title: str
    project_key: str
    member_id: uuid.UUID
    member_display_name: str
    member_discord_id: str
    status: AssignmentStatus
    decision_mode: DecisionMode
    score: float | None = None
    rationale: str | None = None
    assigned_at: datetime


class AssignmentResult(BaseModel):
    """What the bot renders in Discord after a successful /assign."""

    assignment: AssignmentRead
    reasons: list[str] = Field(default_factory=list)
    evaluation: AssignmentEvaluation | None = None
    message: str


class AssignmentHistoryEntry(BaseModel):
    id: uuid.UUID
    task_key: str
    member_display_name: str | None = None
    event: AssignmentEvent
    from_status: AssignmentStatus | None = None
    to_status: AssignmentStatus | None = None
    actor: str | None = None
    reason: str | None = None
    created_at: datetime
