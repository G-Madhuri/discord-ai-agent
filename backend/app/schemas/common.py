from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ServerContext(BaseModel):
    """Identifies the Discord guild every request is scoped to.

    Nothing reaches a service without one of these; it is the isolation key.
    """

    discord_guild_id: str = Field(min_length=1, max_length=32)
    guild_name: str | None = None
    discord_channel_id: str | None = None
    requested_by_discord_id: str | None = None


class Page[T](BaseModel):
    items: list[T]
    total: int


class HealthResponse(BaseModel):
    status: str


class ReadinessResponse(BaseModel):
    status: str
    database: str
    environment: str
    agent_mode: str


class ErrorResponse(BaseModel):
    code: str
    message: str
    details: dict = Field(default_factory=dict)


class EntityRef(ORMModel):
    id: uuid.UUID
    name: str


class Timestamped(ORMModel):
    created_at: datetime
    updated_at: datetime
