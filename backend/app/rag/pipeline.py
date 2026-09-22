"""Ingestion and retrieval pipeline wiring chunker + embeddings + vector store + hybrid RRF."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.rag.embeddings import get_embedding_provider
from app.rag.interfaces import EmbeddingProvider, KnowledgeScope, ScoredChunk, VectorStore
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
    """Turns a document into retrievable chunks and answers scoped queries using hybrid RRF search."""

    def __init__(
        self,
        vector_store: VectorStore,
        embedder: EmbeddingProvider | None = None,
        session: AsyncSession | None = None,
    ) -> None:
        self.vector_store = vector_store
        self.embedder = embedder or get_embedding_provider()
        self.session = session

    async def ingest(
        self,
        scope: KnowledgeScope,
        document_id: uuid.UUID,
        text: str,
        *,
        metadata: dict | None = None,
    ) -> IngestionResult:
        from app.rag.chunking import chunk_document_async

        chunks = await chunk_document_async(text, metadata=metadata)
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
        if self.session is not None:
            from app.rag.retrievers.hybrid import search_hybrid

            return await search_hybrid(
                self.session, scope, query, final_top_k=top_k, embedding_provider=self.embedder
            )
        vector = await self.embedder.embed_query(query)
        return await self.vector_store.search(scope, vector, top_k=top_k or settings.rag_top_k)

    @staticmethod
    def ingested_at() -> datetime:
        return datetime.now(tz=UTC)


def build_pipeline(session: AsyncSession | None = None) -> KnowledgePipeline:
    """Pick the configured vector store."""
    if settings.vector_store == "memory" or session is None:
        return KnowledgePipeline(InMemoryVectorStore(), session=session)
    return KnowledgePipeline(PostgresVectorStore(session), session=session)


async def ingest_document(
    session: AsyncSession,
    server_id: uuid.UUID,
    project_id: uuid.UUID,
    document_id: uuid.UUID,
    raw_text: str,
    filename: str | None = None,
    mime_type: str | None = None,
) -> int:
    """Ingest raw document text using KnowledgePipeline into document_chunks table."""
    from app.models.project import ProjectDocument
    from app.models.enums import DocumentSourceType

    # Ensure parent ProjectDocument exists
    doc = await session.get(ProjectDocument, document_id)
    if not doc:
        doc = ProjectDocument(
            id=document_id,
            server_id=server_id,
            project_id=project_id,
            title=filename or "Attached Document",
            source_type=DocumentSourceType.UPLOAD,
            content_hash=content_hash(raw_text),
        )
        session.add(doc)
        await session.flush()

    pipeline = build_pipeline(session)
    scope = KnowledgeScope(server_id=server_id, project_id=project_id)
    metadata = {"filename": filename, "mime_type": mime_type} if filename else None
    res = await pipeline.ingest(scope, document_id, raw_text, metadata=metadata)
    
    doc.chunk_count = res.chunk_count
    doc.embedding_model = res.embedding_model
    doc.ingested_at = datetime.now(tz=UTC)
    await session.flush()
    return res.chunk_count


