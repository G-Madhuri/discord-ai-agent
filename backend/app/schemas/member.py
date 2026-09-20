from __future__ import annotations

import uuid
from datetime import date

from pydantic import BaseModel, Field

from app.models.enums import AvailabilityStatus, SkillSource
from app.schemas.common import ORMModel, ServerContext


class SkillInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    proficiency: int = Field(default=3, ge=1, le=5)
    years_experience: float | None = Field(default=None, ge=0)
    last_used_on: date | None = None
    category: str | None = None
    source: SkillSource = SkillSource.DECLARED


class SkillRead(ORMModel):
    slug: str
    name: str
    category: str | None = None


class MemberSkillRead(BaseModel):
    slug: str
    name: str
    category: str | None = None
    proficiency: int
    years_experience: float | None = None
    last_used_on: date | None = None
    source: SkillSource


class MemberCreate(BaseModel):
    context: ServerContext
    discord_user_id: str = Field(min_length=1, max_length=32)
    username: str = Field(min_length=1, max_length=100)
    display_name: str | None = None
    role: str | None = None
    seniority: str | None = None
    years_experience: float | None = Field(default=None, ge=0)
    timezone: str | None = None
    max_concurrent_tasks: int | None = Field(default=None, ge=1)
    availability: AvailabilityStatus = AvailabilityStatus.AVAILABLE
    skills: list[SkillInput] = Field(default_factory=list)


class MemberUpdate(BaseModel):
    role: str | None = None
    seniority: str | None = None
    years_experience: float | None = Field(default=None, ge=0)
    timezone: str | None = None
    max_concurrent_tasks: int | None = Field(default=None, ge=1)
    availability: AvailabilityStatus | None = None
    notes: str | None = None
    is_active: bool | None = None


class MemberSkillsUpdate(BaseModel):
    skills: list[SkillInput]
    replace: bool = False


class MemberRead(BaseModel):
    id: uuid.UUID
    discord_user_id: str
    display_name: str
    role: str | None = None
    seniority: str | None = None
    years_experience: float | None = None
    timezone: str | None = None
    availability: AvailabilityStatus
    max_concurrent_tasks: int | None = None
    is_active: bool
    skills: list[MemberSkillRead] = Field(default_factory=list)


class MemberWorkload(BaseModel):
    member_id: uuid.UUID
    display_name: str
    active_task_count: int
    capacity: int
    task_keys: list[str] = Field(default_factory=list)
