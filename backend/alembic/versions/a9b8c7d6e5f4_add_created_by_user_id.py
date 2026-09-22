"""add created_by_user_id to projects

Revision ID: a9b8c7d6e5f4
Revises: 8f76a94512b0
Create Date: 2026-09-22 09:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a9b8c7d6e5f4'
down_revision: Union[str, None] = '8f76a94512b0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE projects ADD COLUMN IF NOT EXISTS created_by_user_id VARCHAR(64)")

    # Backfill created_by_user_id from the first project member's discord_user_id
    op.execute("""
        UPDATE projects
        SET created_by_user_id = (
            SELECT u.discord_user_id
            FROM project_members pm
            JOIN member_profiles mp ON pm.member_profile_id = mp.id
            JOIN users u ON mp.user_id = u.id
            WHERE pm.project_id = projects.id
            ORDER BY pm.created_at ASC
            LIMIT 1
        );
    """)
    # Set sentinel '<legacy>' for any remaining projects with no members
    op.execute("""
        UPDATE projects
        SET created_by_user_id = '<legacy>'
        WHERE created_by_user_id IS NULL;
    """)


def downgrade() -> None:
    op.drop_column('projects', 'created_by_user_id')
