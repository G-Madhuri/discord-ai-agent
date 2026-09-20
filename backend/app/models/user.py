from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, uuid_pk

if TYPE_CHECKING:
    from app.models.member import MemberProfile


class User(Base, TimestampMixin):
    """A Discord identity, global across guilds.

    Nothing server-specific lives here — that belongs on MemberProfile, which is
    scoped to a single server.
    """

    __tablename__ = "users"
    __table_args__ = (sa.UniqueConstraint("discord_user_id", name="uq_users_discord_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    discord_user_id: Mapped[str] = mapped_column(sa.String(32), nullable=False, index=True)
    username: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    global_display_name: Mapped[str | None] = mapped_column(sa.String(100))
    is_bot: Mapped[bool] = mapped_column(sa.Boolean, default=False, nullable=False)

    profiles: Mapped[list[MemberProfile]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User {self.username} discord={self.discord_user_id}>"
