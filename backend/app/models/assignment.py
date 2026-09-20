from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, enum_column, utcnow, uuid_pk
from app.models.enums import AssignmentEvent, AssignmentStatus, DecisionMode

if TYPE_CHECKING:
    from app.models.member import MemberProfile
    from app.models.task import Task


class Assignment(Base, TimestampMixin):
    """A task assigned to a member, with the evidence behind the decision.

    `evidence` holds the scored snapshot (matched skills, workload, dependency
    status, knowledge references, rejected candidates) so any decision can be
    audited long after the fact.
    """

    __tablename__ = "assignments"
    __table_args__ = (
        sa.Index("ix_assignments_server_project", "server_id", "project_id"),
        sa.Index("ix_assignments_member", "member_profile_id"),
        # At most one live assignment per task; history keeps superseded ones.
        sa.Index(
            "uq_assignment_active_task",
            "task_id",
            unique=True,
            postgresql_where=sa.text("status IN ('proposed', 'active')"),
            sqlite_where=sa.text("status IN ('proposed', 'active')"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    server_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("servers.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    member_profile_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("member_profiles.id", ondelete="CASCADE"), nullable=False
    )

    status: Mapped[AssignmentStatus] = enum_column(
        AssignmentStatus, default=AssignmentStatus.ACTIVE, nullable=False
    )
    assigned_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=utcnow, server_default=sa.func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    decision_mode: Mapped[DecisionMode] = enum_column(
        DecisionMode, default=DecisionMode.DETERMINISTIC, nullable=False
    )
    requested_by_discord_id: Mapped[str | None] = mapped_column(sa.String(32))
    score: Mapped[float | None] = mapped_column(sa.Float)
    rationale: Mapped[str | None] = mapped_column(sa.Text)
    evidence: Mapped[dict] = mapped_column(sa.JSON, default=dict, nullable=False)
    agent_model: Mapped[str | None] = mapped_column(sa.String(100))
    engine_version: Mapped[str | None] = mapped_column(sa.String(32))

    task: Mapped[Task] = relationship(back_populates="assignments")
    member: Mapped[MemberProfile] = relationship(back_populates="assignments", lazy="selectin")
    history: Mapped[list[AssignmentHistory]] = relationship(
        back_populates="assignment", cascade="all, delete-orphan"
    )

    @property
    def is_live(self) -> bool:
        return self.status in (AssignmentStatus.PROPOSED, AssignmentStatus.ACTIVE)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Assignment task={self.task_id} member={self.member_profile_id} {self.status}>"


class AssignmentHistory(Base, TimestampMixin):
    """Append-only audit trail. One row per state change, never updated."""

    __tablename__ = "assignment_history"
    __table_args__ = (
        sa.Index("ix_assignment_history_server_task", "server_id", "task_id"),
        sa.Index("ix_assignment_history_member", "member_profile_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    server_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("servers.id", ondelete="CASCADE"), nullable=False
    )
    assignment_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("assignments.id", ondelete="CASCADE")
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    member_profile_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("member_profiles.id", ondelete="SET NULL")
    )

    event: Mapped[AssignmentEvent] = enum_column(AssignmentEvent, nullable=False)
    from_status: Mapped[AssignmentStatus | None] = enum_column(AssignmentStatus, nullable=True)
    to_status: Mapped[AssignmentStatus | None] = enum_column(AssignmentStatus, nullable=True)
    actor: Mapped[str | None] = mapped_column(sa.String(100))
    reason: Mapped[str | None] = mapped_column(sa.Text)
    evidence_snapshot: Mapped[dict] = mapped_column(sa.JSON, default=dict, nullable=False)

    assignment: Mapped[Assignment | None] = relationship(back_populates="history")
