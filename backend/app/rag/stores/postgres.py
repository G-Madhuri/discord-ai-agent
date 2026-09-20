"""PostgreSQL-backed vector store.

Vectors live in `document_chunks.embedding` as a JSON float array and
similarity is computed in Python over the project's chunks. That is honest
about what it is: correct and dependency-free, suitable for the per-project
corpus sizes this bot deals with, and *not* an ANN index.

When the corpus outgrows it, swap this class for a pgvector or Vertex AI
Vector Search implementation — the VectorStore protocol is the only contract
the rest of the system depends on.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import DocumentChunk, ProjectDocument
from app.rag.interfaces import Chunk, KnowledgeScope, ScoredChunk, StoredChunk, cosine_similarity


class PostgresVectorStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(
        self,
        scope: KnowledgeScope,
        document_id: uuid.UUID,
        chunks: list[Chunk],
        embeddings: list[list[float]],
        *,
        model_name: str,
    ) -> int:
        # Re-ingesting a document replaces its chunks wholesale; partial updates
        # would leave stale text retrievable.
        await self._session.execute(
            sa.delete(DocumentChunk).where(
                DocumentChunk.document_id == document_id,
                DocumentChunk.server_id == scope.server_id,
            )
        )
        for chunk, vector in zip(chunks, embeddings, strict=True):
            self._session.add(
                DocumentChunk(
                    server_id=scope.server_id,
                    project_id=scope.project_id,
                    document_id=document_id,
                    chunk_index=chunk.index,
                    content=chunk.content,
                    token_estimate=chunk.token_estimate,
                    embedding=list(vector),
                    embedding_model=model_name,
                    metadata_json=chunk.metadata,
                )
            )
        await self._session.flush()
        return len(chunks)

    async def search(
        self,
        scope: KnowledgeScope,
        query_embedding: list[float],
        *,
        top_k: int = 5,
        min_score: float = 0.0,
    ) -> list[ScoredChunk]:
        stmt = (
            sa.select(DocumentChunk, ProjectDocument.title, ProjectDocument.source_type)
            .join(ProjectDocument, ProjectDocument.id == DocumentChunk.document_id)
            # Both scope columns are filtered explicitly: server isolation must
            # not rely on the project id alone.
            .where(
                DocumentChunk.server_id == scope.server_id,
                DocumentChunk.project_id == scope.project_id,
                DocumentChunk.embedding.is_not(None),
            )
        )
        rows = (await self._session.execute(stmt)).all()

        scored: list[ScoredChunk] = []
        for chunk, title, source_type in rows:
            score = cosine_similarity(query_embedding, chunk.embedding or [])
            if score <= min_score:
                continue
            scored.append(
                ScoredChunk(
                    chunk=StoredChunk(
                        chunk_id=chunk.id,
                        document_id=chunk.document_id,
                        document_title=title,
                        source_type=str(source_type),
                        index=chunk.chunk_index,
                        content=chunk.content,
                    ),
                    score=score,
                )
            )
        scored.sort(key=lambda s: (-s.score, s.chunk.index))
        return scored[:top_k]

    async def delete_document(self, scope: KnowledgeScope, document_id: uuid.UUID) -> int:
        result = await self._session.execute(
            sa.delete(DocumentChunk).where(
                DocumentChunk.document_id == document_id,
                DocumentChunk.server_id == scope.server_id,
            )
        )
        return result.rowcount or 0
