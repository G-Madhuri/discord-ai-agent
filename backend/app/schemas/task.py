from __future__ import annotations

import uuid
from datetime import date

from pydantic import BaseModel, Field, field_validator

from app.models.enums import DependencyType, TaskPriority, TaskStatus
from app.schemas.common import ServerContext


class TaskSkillInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    weight: float = Field(default=1.0, gt=0, le=5)
    is_required: bool = True


class TaskSkillRead(BaseModel):
    slug: str
    name: str
    weight: float
    is_required: bool


class TaskCreate(BaseModel):
    context: ServerContext
    project_key: str
    key: str = Field(min_length=1, max_length=32)
    title: str = Field(min_length=1, max_length=300)
    description: str | None = None
    status: TaskStatus = TaskStatus.TODO
    priority: TaskPriority = TaskPriority.MEDIUM
    role_hint: str | None = None
    estimate_hours: float | None = Field(default=None, gt=0)
    due_date: date | None = None
    labels: list[str] = Field(default_factory=list)
    required_skills: list[TaskSkillInput] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)

    @field_validator("key")
    @classmethod
    def _upper(cls, value: str) -> str:
        return value.strip().upper()


class TaskUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    status: TaskStatus | None = None
    priority: TaskPriority | None = None
    role_hint: str | None = None
    estimate_hours: float | None = Field(default=None, gt=0)
    due_date: date | None = None


class DependencyRead(BaseModel):
    task_key: str
    title: str
    status: TaskStatus
    dependency_type: DependencyType
    is_resolved: bool


class TaskRead(BaseModel):
    id: uuid.UUID
    key: str
    project_key: str
    title: str
    description: str | None = None
    status: TaskStatus
    priority: TaskPriority
    role_hint: str | None = None
    estimate_hours: float | None = None
    due_date: date | None = None
    labels: list[str] = Field(default_factory=list)
    required_skills: list[TaskSkillRead] = Field(default_factory=list)
    assignee_display_name: str | None = None
    assignee_member_id: uuid.UUID | None = None


class DependencyCheck(BaseModel):
    task_key: str
    total: int
    unresolved: list[DependencyRead] = Field(default_factory=list)
    resolved: list[DependencyRead] = Field(default_factory=list)

    @property
    def is_blocked(self) -> bool:
        return bool(self.unresolved)
