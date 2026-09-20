from __future__ import annotations

import uuid
from datetime import date
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, enum_column, uuid_pk
from app.models.enums import DependencyType, TaskPriority, TaskStatus

if TYPE_CHECKING:
    from app.models.assignment import Assignment
    from app.models.project import Project
    from app.models.skill import Skill


class Task(Base, TimestampMixin):
    """An existing unit of work. The agent assigns these; it never invents them."""

    __tablename__ = "tasks"
    __table_args__ = (
        sa.UniqueConstraint("project_id", "key", name="uq_task_project_key"),
        sa.Index("ix_tasks_server_project", "server_id", "project_id"),
        sa.Index("ix_tasks_key", "key"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    server_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("servers.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    key: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    title: Mapped[str] = mapped_column(sa.String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text)
    status: Mapped[TaskStatus] = enum_column(TaskStatus, default=TaskStatus.TODO, nullable=False)
    priority: Mapped[TaskPriority] = enum_column(
        TaskPriority, default=TaskPriority.MEDIUM, nullable=False
    )
    role_hint: Mapped[str | None] = mapped_column(sa.String(100))
    estimate_hours: Mapped[float | None] = mapped_column(sa.Float)
    due_date: Mapped[date | None] = mapped_column(sa.Date)
    labels: Mapped[list] = mapped_column(sa.JSON, default=list, nullable=False)
    metadata_json: Mapped[dict] = mapped_column("metadata", sa.JSON, default=dict, nullable=False)

    project: Mapped[Project] = relationship(back_populates="tasks")
    required_skills: Mapped[list[TaskSkill]] = relationship(
        back_populates="task", cascade="all, delete-orphan", lazy="selectin"
    )
    dependencies: Mapped[list[TaskDependency]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
        foreign_keys="TaskDependency.task_id",
        lazy="selectin",
    )
    assignments: Mapped[list[Assignment]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )

    @property
    def is_open(self) -> bool:
        return self.status not in (TaskStatus.DONE, TaskStatus.CANCELLED)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Task {self.key} {self.title}>"


class TaskSkill(Base, TimestampMixin):
    """Skill a task needs, with how much it matters.

    Kept normalised (rather than a free-text list on Task) so assignment
    evidence can point at the same skill rows members declare.
    """

    __tablename__ = "task_skills"
    __table_args__ = (sa.UniqueConstraint("task_id", "skill_id", name="uq_task_skill"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    task_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    skill_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("skills.id", ondelete="CASCADE"), nullable=False
    )
    weight: Mapped[float] = mapped_column(sa.Float, default=1.0, nullable=False)
    is_required: Mapped[bool] = mapped_column(sa.Boolean, default=True, nullable=False)

    task: Mapped[Task] = relationship(back_populates="required_skills")
    skill: Mapped[Skill] = relationship(lazy="selectin")


class TaskDependency(Base, TimestampMixin):
    __tablename__ = "task_dependencies"
    __table_args__ = (
        sa.UniqueConstraint("task_id", "depends_on_task_id", name="uq_task_dependency"),
        sa.CheckConstraint("task_id <> depends_on_task_id", name="ck_task_dependency_not_self"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    task_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    depends_on_task_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    dependency_type: Mapped[DependencyType] = enum_column(
        DependencyType, default=DependencyType.BLOCKS, nullable=False
    )

    task: Mapped[Task] = relationship(back_populates="dependencies", foreign_keys=[task_id])
    depends_on: Mapped[Task] = relationship(foreign_keys=[depends_on_task_id], lazy="selectin")
