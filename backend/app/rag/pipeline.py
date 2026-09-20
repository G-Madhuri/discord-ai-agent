"""Ingestion and retrieval pipeline wiring chunker + embeddings + vector store."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.rag.chunking import ParagraphChunker
from app.rag.embeddings import get_embedding_provider
from app.rag.interfaces import Chunker, EmbeddingProvider, KnowledgeScope, ScoredChunk, VectorStore
from app.rag.stores.memory import InMemoryVectorStore
from app.rag.stores.postgres import PostgresVectorStore

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class IngestionResult:
    document_id: uuid.UUID
    chunk_count: int
    embedding_model: str
    content_hash: str


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class KnowledgePipeline:
    """Turns a document into retrievable chunks and answers scoped queries."""

    def __init__(
        self,
        vector_store: VectorStore,
        embedder: EmbeddingProvider | None = None,
        chunker: Chunker | None = None,
    ) -> None:
        self.vector_store = vector_store
        self.embedder = embedder or get_embedding_provider()
        self.chunker = chunker or ParagraphChunker()

    async def ingest(
        self,
        scope: KnowledgeScope,
        document_id: uuid.UUID,
        text: str,
        *,
        metadata: dict | None = None,
    ) -> IngestionResult:
        chunks = self.chunker.split(text, metadata=metadata)
        if not chunks:
            return IngestionResult(document_id, 0, self.embedder.model_name, content_hash(text))

        vectors = await self.embedder.embed_documents([c.content for c in chunks])
        count = await self.vector_store.upsert(
            scope, document_id, chunks, vectors, model_name=self.embedder.model_name
        )
        logger.info(
            "ingested document %s (%s chunks, model=%s)",
            document_id,
            count,
            self.embedder.model_name,
        )
        return IngestionResult(document_id, count, self.embedder.model_name, content_hash(text))

    async def search(
        self, scope: KnowledgeScope, query: str, *, top_k: int | None = None
    ) -> list[ScoredChunk]:
        if not query.strip():
            return []
        vector = await self.embedder.embed_query(query)
        return await self.vector_store.search(scope, vector, top_k=top_k or settings.rag_top_k)

    @staticmethod
    def ingested_at() -> datetime:
        return datetime.now(tz=UTC)


def build_pipeline(session: AsyncSession | None = None) -> KnowledgePipeline:
    """Pick the configured vector store. `memory` never touches the database."""
    if settings.vector_store == "memory" or session is None:
        return KnowledgePipeline(InMemoryVectorStore())
    return KnowledgePipeline(PostgresVectorStore(session))
