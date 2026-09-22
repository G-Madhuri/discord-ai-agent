from __future__ import annotations

import json
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.project import DocumentChunk, ProjectDocument
from app.rag.embeddings import get_embedding_provider
from app.rag.interfaces import KnowledgeScope, ScoredChunk, StoredChunk, cosine_similarity

logger = get_logger(__name__)


async def search_semantic(
    session: AsyncSession,
    scope: KnowledgeScope,
    query: str,
    top_k: int = 20,
    embedding_provider: Any = None,
) -> list[ScoredChunk]:
    """Semantic vector search using pgvector cosine distance operator <=>, with SQLite fallback for tests."""
    if not query or not query.strip():
        return []

    provider = embedding_provider or get_embedding_provider()
    query_vec = await provider.embed_query(query)

    dialect = session.bind.dialect.name if session.bind else "postgresql"
    if dialect != "postgresql":
        stmt = (
            sa.select(DocumentChunk, ProjectDocument.title, ProjectDocument.source_type)
            .join(ProjectDocument, ProjectDocument.id == DocumentChunk.document_id)
            .where(
                DocumentChunk.server_id == scope.server_id,
                DocumentChunk.project_id == scope.project_id,
                DocumentChunk.embedding.is_not(None),
            )
        )
        rows = (await session.execute(stmt)).all()
        scored = []
        for chunk, title, source_type in rows:
            emb = chunk.embedding
            if isinstance(emb, str):
                try:
                    emb = json.loads(emb)
                except Exception:
                    emb = []
            score = cosine_similarity(query_vec, emb or [])
            if score > 0.0:
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
                        score=float(score),
                    )
                )
        scored.sort(key=lambda s: (-s.score, s.chunk.index))
        return scored[:top_k]

    vec_str = f"[{','.join(str(v) for v in query_vec)}]"

    stmt = sa.text("""
        SELECT dc.id, dc.document_id, dc.chunk_index, dc.content,
               pd.title AS document_title, pd.source_type,
               1 - (dc.embedding <=> CAST(:query_vec AS vector)) AS score
        FROM document_chunks dc
        JOIN project_documents pd ON pd.id = dc.document_id
        WHERE dc.server_id = :server_id
          AND dc.project_id = :project_id
          AND dc.embedding IS NOT NULL
        ORDER BY dc.embedding <=> CAST(:query_vec AS vector)
        LIMIT :top_k
    """)
    res = await session.execute(
        stmt,
        {
            "query_vec": vec_str,
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
                score=float(r.score),
            )
        )
    return results
