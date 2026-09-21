from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, enum_column, uuid_pk
from app.models.enums import DocumentSourceType, PlanStatus, ProjectStatus

if TYPE_CHECKING:
    from app.models.member import MemberProfile
    from app.models.server import Server
    from app.models.task import Task


class Project(Base, TimestampMixin):
    """A project inside one Discord server. A server can hold many."""

    __tablename__ = "projects"
    __table_args__ = (
        sa.UniqueConstraint("server_id", "key", name="uq_project_server_key"),
        sa.Index("ix_projects_server", "server_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    server_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("servers.id", ondelete="CASCADE"), nullable=False
    )
    key: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    name: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text)
    status: Mapped[ProjectStatus] = enum_column(
        ProjectStatus, default=ProjectStatus.ACTIVE, nullable=False
    )
    discord_channel_id: Mapped[str | None] = mapped_column(sa.String(32))

    # Plan state. Anything other than NONE means a plan exists and is off-limits
    # to anything but an explicit planning/replanning request.
    plan_status: Mapped[PlanStatus] = enum_column(
        PlanStatus, default=PlanStatus.NONE, nullable=False
    )
    plan_version: Mapped[int] = mapped_column(sa.Integer, default=0, nullable=False)
    plan_locked_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    start_date: Mapped[date | None] = mapped_column(sa.Date)
    target_date: Mapped[date | None] = mapped_column(sa.Date)
    deadline: Mapped[date | None] = mapped_column(sa.Date)
    constraints: Mapped[str | None] = mapped_column(sa.Text)
    plan_source: Mapped[str | None] = mapped_column(sa.String(50))
    draft_assignments: Mapped[dict | None] = mapped_column(sa.JSON)
    metadata_json: Mapped[dict] = mapped_column("metadata", sa.JSON, default=dict, nullable=False)

    server: Mapped[Server] = relationship(back_populates="projects")
    members: Mapped[list[ProjectMember]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    tasks: Mapped[list[Task]] = relationship(back_populates="project", cascade="all, delete-orphan")
    documents: Mapped[list[ProjectDocument]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )

    @property
    def has_plan(self) -> bool:
        return self.plan_status is not PlanStatus.NONE

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Project {self.key} {self.name}>"


class ProjectMember(Base, TimestampMixin):
    """Which server members are on a project, and in what project role."""

    __tablename__ = "project_members"
    __table_args__ = (
        sa.UniqueConstraint("project_id", "member_profile_id", name="uq_project_member"),
        sa.Index("ix_project_members_server", "server_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    server_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("servers.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    member_profile_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("member_profiles.id", ondelete="CASCADE"), nullable=False
    )
    project_role: Mapped[str | None] = mapped_column(sa.String(100))
    allocation_percent: Mapped[int] = mapped_column(sa.Integer, default=100, nullable=False)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True, nullable=False)

    project: Mapped[Project] = relationship(back_populates="members")
    member: Mapped[MemberProfile] = relationship(
        back_populates="project_memberships", lazy="selectin"
    )


class ProjectDocument(Base, TimestampMixin):
    """Unstructured project knowledge: requirements, architecture notes,
    meeting notes, uploads, captured Discord discussions.

    The text itself is chunked into `document_chunks` for retrieval. Structured
    facts (team, tasks, assignments) never live here.
    """

    __tablename__ = "project_documents"
    __table_args__ = (sa.Index("ix_project_documents_server_project", "server_id", "project_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    server_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("servers.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(sa.String(300), nullable=False)
    source_type: Mapped[DocumentSourceType] = enum_column(
        DocumentSourceType, default=DocumentSourceType.OTHER, nullable=False
    )
    source_uri: Mapped[str | None] = mapped_column(sa.String(1000))
    content_hash: Mapped[str | None] = mapped_column(sa.String(64), index=True)
    chunk_count: Mapped[int] = mapped_column(sa.Integer, default=0, nullable=False)
    embedding_model: Mapped[str | None] = mapped_column(sa.String(100))
    ingested_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    metadata_json: Mapped[dict] = mapped_column("metadata", sa.JSON, default=dict, nullable=False)

    project: Mapped[Project] = relationship(back_populates="documents")
    chunks: Mapped[list[DocumentChunk]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class DocumentChunk(Base, TimestampMixin):
    """A retrievable slice of a ProjectDocument plus its embedding.

    The embedding is stored as a JSON float array so the schema works on plain
    PostgreSQL. Swapping in pgvector or Vertex AI Vector Search means replacing
    the VectorStore implementation, not the domain code (see app/rag).
    """

    __tablename__ = "document_chunks"
    __table_args__ = (
        sa.UniqueConstraint("document_id", "chunk_index", name="uq_chunk_document_index"),
        sa.Index("ix_document_chunks_server_project", "server_id", "project_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    server_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("servers.id", ondelete="CASCADE"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("project_documents.id", ondelete="CASCADE"), nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    content: Mapped[str] = mapped_column(sa.Text, nullable=False)
    token_estimate: Mapped[int] = mapped_column(sa.Integer, default=0, nullable=False)
    embedding: Mapped[list | None] = mapped_column(sa.JSON)
    embedding_model: Mapped[str | None] = mapped_column(sa.String(100))
    metadata_json: Mapped[dict] = mapped_column("metadata", sa.JSON, default=dict, nullable=False)

    document: Mapped[ProjectDocument] = relationship(back_populates="chunks")


class ProjectDocumentExtraction(Base, TimestampMixin):
    """Records LLM extraction artifacts from an uploaded document."""

    __tablename__ = "project_document_extractions"
    __table_args__ = (sa.Index("ix_doc_extractions_project", "project_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.ForeignKey("project_documents.id", ondelete="CASCADE")
    )
    extracted_tasks: Mapped[dict] = mapped_column(sa.JSON, default=dict, nullable=False)
    extraction_notes: Mapped[str | None] = mapped_column(sa.Text)


class ProjectPlanApproval(Base, TimestampMixin):
    """Audit log of user approvals/rejections/edits for a project plan."""

    __tablename__ = "project_plan_approvals"
    __table_args__ = (sa.Index("ix_plan_approvals_project", "project_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    approved_by_user_id: Mapped[str | None] = mapped_column(sa.String(64))
    approved_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    action: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    feedback: Mapped[str | None] = mapped_column(sa.Text)

