from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator

from app.models.enums import DocumentSourceType, PlanStatus, ProjectStatus
from app.schemas.common import ORMModel, ServerContext


class ProjectCreate(BaseModel):
    context: ServerContext
    key: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    status: ProjectStatus = ProjectStatus.ACTIVE
    start_date: date | None = None
    target_date: date | None = None

    @field_validator("key")
    @classmethod
    def _upper(cls, value: str) -> str:
        return value.strip().upper()


class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    status: ProjectStatus | None = None
    start_date: date | None = None
    target_date: date | None = None
    discord_channel_id: str | None = None


class ProjectMemberAdd(BaseModel):
    context: ServerContext
    discord_user_id: str
    project_role: str | None = None
    allocation_percent: int = Field(default=100, ge=1, le=100)


class ProjectMemberRead(BaseModel):
    member_id: uuid.UUID
    display_name: str
    discord_user_id: str
    project_role: str | None = None
    allocation_percent: int
    is_active: bool


class ProjectRead(ORMModel):
    id: uuid.UUID
    key: str
    name: str
    description: str | None = None
    status: ProjectStatus
    plan_status: PlanStatus
    plan_version: int
    plan_locked_at: datetime | None = None
    start_date: date | None = None
    target_date: date | None = None


class ProjectInfo(BaseModel):
    project: ProjectRead
    member_count: int
    task_count: int
    open_task_count: int
    document_count: int
    members: list[ProjectMemberRead] = Field(default_factory=list)


class DocumentCreate(BaseModel):
    context: ServerContext
    project_key: str
    title: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=1)
    source_type: DocumentSourceType = DocumentSourceType.OTHER
    source_uri: str | None = None
    metadata: dict = Field(default_factory=dict)


class DocumentRead(BaseModel):
    id: uuid.UUID
    title: str
    source_type: DocumentSourceType
    chunk_count: int
    ingested_at: datetime | None = None


class KnowledgeHit(BaseModel):
    document_id: uuid.UUID
    document_title: str
    source_type: DocumentSourceType
    chunk_id: uuid.UUID
    chunk_index: int
    content: str
    score: float


class KnowledgeSearchResult(BaseModel):
    query: str
    project_key: str
    hits: list[KnowledgeHit] = Field(default_factory=list)
