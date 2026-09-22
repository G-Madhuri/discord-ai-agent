from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.project import DocumentChunk, ProjectDocument
from app.rag.interfaces import KnowledgeScope, ScoredChunk, StoredChunk

logger = get_logger(__name__)


async def search_keyword(
    session: AsyncSession,
    scope: KnowledgeScope,
    query: str,
    top_k: int = 20,
) -> list[ScoredChunk]:
    """Keyword search using PostgreSQL full-text tsvector and ts_rank, with SQLite fallback for tests."""
    if not query or not query.strip():
        return []

    dialect = session.bind.dialect.name if session.bind else "postgresql"
    if dialect != "postgresql":
        # SQLite fallback for unit tests
        stmt = (
            sa.select(DocumentChunk, ProjectDocument.title, ProjectDocument.source_type)
            .join(ProjectDocument, ProjectDocument.id == DocumentChunk.document_id)
            .where(
                DocumentChunk.server_id == scope.server_id,
                DocumentChunk.project_id == scope.project_id,
            )
        )
        rows = (await session.execute(stmt)).all()
        q_words = [w.lower() for w in query.split()]
        scored = []
        for chunk, title, source_type in rows:
            content_lower = chunk.content.lower()
            matches = sum(1 for w in q_words if w in content_lower)
            if matches > 0:
                scored.append(
                    ScoredChunk(
                        chunk=StoredChunk(
                            chunk_id=chunk.id,
                            document_id=chunk.document_id,
                            document_title=title or "Untitled Document",
                            source_type=str(source_type),
                            index=chunk.chunk_index,
                            content=chunk.content,
                        ),
                        score=float(matches),
                    )
                )
        scored.sort(key=lambda s: (-s.score, s.chunk.index))
        return scored[:top_k]

    stmt = sa.text("""
        SELECT dc.id, dc.document_id, dc.chunk_index, dc.content,
               pd.title AS document_title, pd.source_type,
               ts_rank(dc.text_search, plainto_tsquery('english', :query)) AS rank
        FROM document_chunks dc
        JOIN project_documents pd ON pd.id = dc.document_id
        WHERE dc.server_id = :server_id
          AND dc.project_id = :project_id
          AND dc.text_search @@ plainto_tsquery('english', :query)
        ORDER BY rank DESC
        LIMIT :top_k
    """)
    res = await session.execute(
        stmt,
        {
            "query": query,
            "server_id": scope.server_id,
            "project_id": scope.project_id,
            "top_k": top_k,
        },
    )
    rows = res.all()
    results = []
    for r in rows:
        results.append(
            ScoredChunk(
                chunk=StoredChunk(
                    chunk_id=r.id,
                    document_id=r.document_id,
                    document_title=r.document_title or "Untitled Document",
                    source_type=str(r.source_type),
                    index=r.chunk_index,
                    content=r.content,
                ),
                score=float(r.rank),
            )
        )
    return results
