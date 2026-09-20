"""Knowledge-layer abstractions.

The domain only ever talks to these three protocols, so the concrete pieces
(chunker, embedding provider, vector store) can be swapped for a GCP-native
implementation later without touching assignment logic.

Every operation is scoped by `KnowledgeScope`. There is no un-scoped read path:
a server's knowledge cannot be retrieved while acting for another server.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class KnowledgeScope:
    server_id: uuid.UUID
    project_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class Chunk:
    """A slice of a document, before or after embedding."""

    index: int
    content: str
    token_estimate: int
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StoredChunk:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_title: str
    source_type: str
    index: int
    content: str


@dataclass(frozen=True, slots=True)
class ScoredChunk:
    chunk: StoredChunk
    score: float


@runtime_checkable
class Chunker(Protocol):
    def split(self, text: str, *, metadata: dict | None = None) -> list[Chunk]: ...


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Turns text into vectors. Implementations must be deterministic for the
    same input/model so stored vectors stay comparable."""

    @property
    def model_name(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...


@runtime_checkable
class VectorStore(Protocol):
    async def upsert(
        self,
        scope: KnowledgeScope,
        document_id: uuid.UUID,
        chunks: list[Chunk],
        embeddings: list[list[float]],
        *,
        model_name: str,
    ) -> int: ...

    async def search(
        self,
        scope: KnowledgeScope,
        query_embedding: list[float],
        *,
        top_k: int = 5,
        min_score: float = 0.0,
    ) -> list[ScoredChunk]: ...

    async def delete_document(self, scope: KnowledgeScope, document_id: uuid.UUID) -> int: ...


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = sum(a * a for a in left) ** 0.5
    right_norm = sum(b * b for b in right) ** 0.5
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)
