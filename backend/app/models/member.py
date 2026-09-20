from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, enum_column, uuid_pk
from app.models.enums import AvailabilityStatus

if TYPE_CHECKING:
    from app.models.assignment import Assignment
    from app.models.project import ProjectMember
    from app.models.server import Server
    from app.models.skill import MemberSkill
    from app.models.user import User


class MemberProfile(Base, TimestampMixin):
    """A user's membership of one Discord server, with everything the
    assignment engine needs: role, seniority, capacity and availability."""

    __tablename__ = "member_profiles"
    __table_args__ = (
        sa.UniqueConstraint("server_id", "user_id", name="uq_member_server_user"),
        sa.Index("ix_member_profiles_server", "server_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    server_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("servers.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    display_name: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    role: Mapped[str | None] = mapped_column(sa.String(100))
    seniority: Mapped[str | None] = mapped_column(sa.String(50))
    years_experience: Mapped[float | None] = mapped_column(sa.Float)
    timezone: Mapped[str | None] = mapped_column(sa.String(64))
    availability: Mapped[AvailabilityStatus] = enum_column(
        AvailabilityStatus, default=AvailabilityStatus.AVAILABLE, nullable=False
    )
    max_concurrent_tasks: Mapped[int | None] = mapped_column(sa.Integer)
    notes: Mapped[str | None] = mapped_column(sa.Text)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True, nullable=False)

    server: Mapped[Server] = relationship(back_populates="members")
    user: Mapped[User] = relationship(back_populates="profiles", lazy="selectin")
    skills: Mapped[list[MemberSkill]] = relationship(
        back_populates="member", cascade="all, delete-orphan", lazy="selectin"
    )
    project_memberships: Mapped[list[ProjectMember]] = relationship(
        back_populates="member", cascade="all, delete-orphan"
    )
    assignments: Mapped[list[Assignment]] = relationship(back_populates="member")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<MemberProfile {self.display_name} role={self.role}>"
