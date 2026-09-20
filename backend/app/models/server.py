from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, uuid_pk

if TYPE_CHECKING:
    from app.models.member import MemberProfile
    from app.models.project import Project


class Server(Base, TimestampMixin):
    """A Discord guild. Root of every isolation boundary in the system."""

    __tablename__ = "servers"
    __table_args__ = (sa.UniqueConstraint("discord_guild_id", name="uq_servers_guild"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    discord_guild_id: Mapped[str] = mapped_column(sa.String(32), nullable=False, index=True)
    name: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True, nullable=False)
    settings: Mapped[dict] = mapped_column(sa.JSON, default=dict, nullable=False)

    projects: Mapped[list[Project]] = relationship(
        back_populates="server", cascade="all, delete-orphan"
    )
    members: Mapped[list[MemberProfile]] = relationship(
        back_populates="server", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<Server {self.name} guild={self.discord_guild_id}>"
