from __future__ import annotations

import uuid
from datetime import date
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, enum_column, uuid_pk
from app.models.enums import SkillSource

if TYPE_CHECKING:
    from app.models.member import MemberProfile


class Skill(Base, TimestampMixin):
    """Shared skill vocabulary (e.g. 'react', 'postgresql').

    Intentionally global and free of server data: it is a term list, never a
    place where one guild's information could leak into another's.
    """

    __tablename__ = "skills"
    __table_args__ = (sa.UniqueConstraint("slug", name="uq_skills_slug"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    slug: Mapped[str] = mapped_column(sa.String(100), nullable=False, index=True)
    name: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    category: Mapped[str | None] = mapped_column(sa.String(50))

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Skill {self.slug}>"


class MemberSkill(Base, TimestampMixin):
    __tablename__ = "member_skills"
    __table_args__ = (
        sa.UniqueConstraint("member_profile_id", "skill_id", name="uq_member_skill"),
        sa.CheckConstraint("proficiency BETWEEN 1 AND 5", name="ck_member_skill_proficiency"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    member_profile_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("member_profiles.id", ondelete="CASCADE"), nullable=False
    )
    skill_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("skills.id", ondelete="CASCADE"), nullable=False
    )
    proficiency: Mapped[int] = mapped_column(sa.Integer, default=3, nullable=False)
    years_experience: Mapped[float | None] = mapped_column(sa.Float)
    last_used_on: Mapped[date | None] = mapped_column(sa.Date)
    source: Mapped[SkillSource] = enum_column(
        SkillSource, default=SkillSource.DECLARED, nullable=False
    )

    member: Mapped[MemberProfile] = relationship(back_populates="skills")
    skill: Mapped[Skill] = relationship(lazy="selectin")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<MemberSkill member={self.member_profile_id} skill={self.skill_id}>"
