"""Chunking, embeddings and the scoped vector store."""

from __future__ import annotations

import uuid

from app.rag.chunking import ParagraphChunker
from app.rag.embeddings import HashingEmbeddingProvider
from app.rag.interfaces import KnowledgeScope
from app.rag.pipeline import KnowledgePipeline
from app.rag.stores.memory import InMemoryVectorStore


def test_chunker_keeps_paragraphs_together():
    text = "First paragraph about React.\n\nSecond paragraph about PostgreSQL."
    chunks = ParagraphChunker(chunk_size=500, overlap=0).split(text)

    assert len(chunks) == 1
    assert "React" in chunks[0].content and "PostgreSQL" in chunks[0].content


def test_chunker_splits_when_over_the_size_limit():
    text = "\n\n".join(f"Paragraph {i} " + "x" * 80 for i in range(10))
    chunks = ParagraphChunker(chunk_size=200, overlap=20).split(text)

    assert len(chunks) > 1
    assert all(c.token_estimate > 0 for c in chunks)
    assert [c.index for c in chunks] == list(range(len(chunks)))


def test_chunker_ignores_empty_input():
    assert ParagraphChunker().split("   \n\n  ") == []


async def test_hashing_embeddings_are_deterministic_and_normalised():
    provider = HashingEmbeddingProvider(dimension=64)
    first = await provider.embed_query("React dashboard")
    second = await provider.embed_query("React dashboard")

    assert first == second
    assert len(first) == 64
    assert abs(sum(v * v for v in first) ** 0.5 - 1.0) < 1e-9


async def test_retrieval_finds_the_relevant_chunk():
    store = InMemoryVectorStore()
    scope = KnowledgeScope(server_id=uuid.uuid4(), project_id=uuid.uuid4())
    document_id = uuid.uuid4()
    store.register_document(document_id, "Architecture", "architecture")

    pipeline = KnowledgePipeline(store, HashingEmbeddingProvider(dimension=256))
    await pipeline.ingest(
        scope,
        document_id,
        "The frontend uses React and Recharts.\n\nThe database layer is PostgreSQL.",
    )

    hits = await pipeline.search(scope, "React charting library", top_k=1)

    assert hits
    assert "React" in hits[0].chunk.content


async def test_retrieval_is_scoped_to_one_server():
    store = InMemoryVectorStore()
    project_id = uuid.uuid4()
    scope_a = KnowledgeScope(server_id=uuid.uuid4(), project_id=project_id)
    # Same project id, different server: must not leak.
    scope_b = KnowledgeScope(server_id=uuid.uuid4(), project_id=project_id)

    document_id = uuid.uuid4()
    store.register_document(document_id, "Architecture", "architecture")
    pipeline = KnowledgePipeline(store, HashingEmbeddingProvider(dimension=128))
    await pipeline.ingest(scope_a, document_id, "The frontend uses React and Recharts.")

    assert await pipeline.search(scope_a, "React")
    assert await pipeline.search(scope_b, "React") == []
