"""add pgvector, tsvector, and HNSW index to document_chunks

Revision ID: b1c2d3e4f5a6
Revises: a9b8c7d6e5f4
Create Date: 2026-09-22 10:15:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b1c2d3e4f5a6'
down_revision: Union[str, None] = 'a9b8c7d6e5f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("ALTER TABLE document_chunks DROP COLUMN IF EXISTS embedding")
    op.execute("ALTER TABLE document_chunks ADD COLUMN embedding vector(768)")
    op.execute("""
        ALTER TABLE document_chunks
        ADD COLUMN IF NOT EXISTS text_search tsvector
        GENERATED ALWAYS AS (to_tsvector('english', content)) STORED
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_document_chunks_embedding_hnsw
        ON document_chunks
        USING hnsw (embedding vector_cosine_ops)
        WITH (m=16, ef_construction=64)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_document_chunks_text_search
        ON document_chunks USING gin(text_search)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_document_chunks_text_search")
    op.execute("DROP INDEX IF EXISTS idx_document_chunks_embedding_hnsw")
    op.execute("ALTER TABLE document_chunks DROP COLUMN IF EXISTS text_search")
    op.execute("ALTER TABLE document_chunks DROP COLUMN IF EXISTS embedding")
    op.execute("ALTER TABLE document_chunks ADD COLUMN embedding JSON")
