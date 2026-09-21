"""add_phase6_agentic_planning_schema

Revision ID: 8f76a94512b0
Revises: 7e65b867044d
Create Date: 2026-09-22 00:35:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "8f76a94512b0"
down_revision: Union[str, None] = "7e65b867044d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add columns to projects table
    op.add_column("projects", sa.Column("constraints", sa.Text(), nullable=True))
    op.add_column("projects", sa.Column("deadline", sa.Date(), nullable=True))
    op.add_column("projects", sa.Column("plan_source", sa.String(length=50), nullable=True))
    op.add_column("projects", sa.Column("draft_assignments", sa.JSON(), nullable=True))

    # Backfill existing manually-typed projects to 'user_provided' (Item 4)
    op.execute("UPDATE projects SET plan_source = 'user_provided' WHERE plan_source IS NULL")

    # Create project_document_extractions table
    op.create_table(
        "project_document_extractions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("extracted_tasks", sa.JSON(), nullable=False),
        sa.Column("extraction_notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["project_documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_doc_extractions_project", "project_document_extractions", ["project_id"], unique=False)

    # Create project_plan_approvals table
    op.create_table(
        "project_plan_approvals",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("approved_by_user_id", sa.String(length=64), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("action", sa.String(length=50), nullable=False),
        sa.Column("feedback", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_plan_approvals_project", "project_plan_approvals", ["project_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_plan_approvals_project", table_name="project_plan_approvals")
    op.drop_table("project_plan_approvals")
    op.drop_index("ix_doc_extractions_project", table_name="project_document_extractions")
    op.drop_table("project_document_extractions")
    op.drop_column("projects", "draft_assignments")
    op.drop_column("projects", "plan_source")
    op.drop_column("projects", "deadline")
    op.drop_column("projects", "constraints")
