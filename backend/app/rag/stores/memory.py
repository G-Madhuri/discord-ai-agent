"""In-process vector store for tests and offline development."""

from __future__ import annotations

import uuid
from collections import defaultdict

from app.rag.interfaces import Chunk, KnowledgeScope, ScoredChunk, StoredChunk, cosine_similarity


class InMemoryVectorStore:
    def __init__(self) -> None:
        self._rows: dict[tuple[uuid.UUID, uuid.UUID], list[tuple[StoredChunk, list[float]]]] = (
            defaultdict(list)
        )
        self._titles: dict[uuid.UUID, tuple[str, str]] = {}

    def register_document(self, document_id: uuid.UUID, title: str, source_type: str) -> None:
        self._titles[document_id] = (title, source_type)

    async def upsert(
        self,
        scope: KnowledgeScope,
        document_id: uuid.UUID,
        chunks: list[Chunk],
        embeddings: list[list[float]],
        *,
        model_name: str,
    ) -> int:
        key = (scope.server_id, scope.project_id)
        self._rows[key] = [row for row in self._rows[key] if row[0].document_id != document_id]
        title, source_type = self._titles.get(document_id, ("untitled", "other"))
        for chunk, vector in zip(chunks, embeddings, strict=True):
            self._rows[key].append(
                (
                    StoredChunk(
                        chunk_id=uuid.uuid4(),
                        document_id=document_id,
                        document_title=title,
                        source_type=source_type,
                        index=chunk.index,
                        content=chunk.content,
                    ),
                    vector,
                )
            )
        return len(chunks)

    async def search(
        self,
        scope: KnowledgeScope,
        query_embedding: list[float],
        *,
        top_k: int = 5,
        min_score: float = 0.0,
    ) -> list[ScoredChunk]:
        rows = self._rows.get((scope.server_id, scope.project_id), [])
        scored = [
            ScoredChunk(chunk=chunk, score=cosine_similarity(query_embedding, vector))
            for chunk, vector in rows
        ]
        scored = [s for s in scored if s.score > min_score]
        scored.sort(key=lambda s: (-s.score, s.chunk.index))
        return scored[:top_k]

    async def delete_document(self, scope: KnowledgeScope, document_id: uuid.UUID) -> int:
        key = (scope.server_id, scope.project_id)
        before = len(self._rows.get(key, []))
        self._rows[key] = [r for r in self._rows.get(key, []) if r[0].document_id != document_id]
        return before - len(self._rows[key])
