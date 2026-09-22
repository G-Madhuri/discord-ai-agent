"""Chunking algorithms for documents.

Supports:
1. `SemanticChunker`: Uses NLTK sentence tokenization + Vertex embedding distance boundaries,
   enforcing min/max token constraints and sentence overlap.
2. `ParagraphChunker`: Fixed-window paragraph-aware splitter.
"""

from __future__ import annotations

import math
import re
from typing import Any

import nltk

from app.core.config import settings
from app.core.logging import get_logger
from app.rag.embeddings import get_embedding_provider
from app.rag.interfaces import Chunk

logger = get_logger(__name__)

_PARAGRAPH = re.compile(r"\n\s*\n")


def estimate_tokens(text: str) -> int:
    """Rough 4-chars-per-token heuristic."""
    return max(1, len(text) // 4)


def _cosine_distance(v1: list[float], v2: list[float]) -> float:
    dot = sum(a * b for a, b in zip(v1, v2))
    norm1 = math.sqrt(sum(a * a for a in v1))
    norm2 = math.sqrt(sum(b * b for b in v2))
    if norm1 == 0.0 or norm2 == 0.0:
        return 1.0
    similarity = dot / (norm1 * norm2)
    similarity = max(-1.0, min(1.0, similarity))
    return 1.0 - similarity


class ParagraphChunker:
    """Splits on blank lines and packs paragraphs up to `chunk_size` characters."""

    def __init__(self, chunk_size: int | None = None, overlap: int | None = None) -> None:
        self.chunk_size = chunk_size or settings.rag_chunk_size
        self.overlap = overlap if overlap is not None else settings.rag_chunk_overlap
        if self.overlap >= self.chunk_size:
            raise ValueError("chunk overlap must be smaller than chunk size")

    def split(self, text: str, *, metadata: dict | None = None) -> list[Chunk]:
        cleaned = text.strip()
        if not cleaned:
            return []

        pieces = [p.strip() for p in _PARAGRAPH.split(cleaned) if p.strip()]
        packed: list[str] = []
        buffer = ""
        for piece in pieces:
            for part in self._hard_split(piece):
                if not buffer:
                    buffer = part
                elif len(buffer) + len(part) + 2 <= self.chunk_size:
                    buffer = f"{buffer}\n\n{part}"
                else:
                    packed.append(buffer)
                    buffer = self._carry_over(buffer, part)
        if buffer:
            packed.append(buffer)

        return [
            Chunk(
                index=i,
                content=content,
                token_estimate=estimate_tokens(content),
                metadata=dict(metadata or {}),
            )
            for i, content in enumerate(packed)
        ]

    def _hard_split(self, piece: str) -> list[str]:
        if len(piece) <= self.chunk_size:
            return [piece]
        step = self.chunk_size - self.overlap
        return [piece[i : i + self.chunk_size] for i in range(0, len(piece), step)]

    def _carry_over(self, previous: str, nxt: str) -> str:
        if not self.overlap:
            return nxt
        tail = previous[-self.overlap :].lstrip()
        return f"{tail}\n\n{nxt}" if tail else nxt


class SemanticChunker:
    """Semantic chunker using NLTK sentence tokenization + embedding distance boundaries."""

    def __init__(
        self,
        min_tokens: int | None = None,
        max_tokens: int | None = None,
        threshold: float | None = None,
        overlap_sentences: int | None = None,
        embedding_provider: Any = None,
    ) -> None:
        self.min_tokens = min_tokens or settings.rag_chunk_min_tokens
        self.max_tokens = max_tokens or settings.rag_chunk_max_tokens
        self.threshold = threshold or settings.rag_semantic_chunk_threshold
        self.overlap_sentences = (
            overlap_sentences
            if overlap_sentences is not None
            else settings.rag_chunk_overlap_sentences
        )
        self._provider = embedding_provider

    async def split_async(self, text: str, *, metadata: dict | None = None) -> list[Chunk]:
        cleaned = text.strip()
        if not cleaned:
            return []

        # Step 1: Split into sentences using NLTK
        try:
            sentences = [s.strip() for s in nltk.sent_tokenize(cleaned) if s.strip()]
        except Exception:
            sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", cleaned) if s.strip()]

        if not sentences:
            return []

        if len(sentences) == 1:
            return [
                Chunk(
                    index=0,
                    content=sentences[0],
                    token_estimate=estimate_tokens(sentences[0]),
                    metadata=dict(metadata or {}),
                )
            ]

        # Step 2: Embed sentences
        provider = self._provider or get_embedding_provider()
        embeddings = await provider.embed_documents(sentences)

        # Step 3: Compute consecutive pairwise cosine distances
        distances: list[float] = []
        for i in range(len(embeddings) - 1):
            dist = _cosine_distance(embeddings[i], embeddings[i + 1])
            distances.append(dist)

        # Step 4: Adaptive or configured threshold
        if self.threshold is not None:
            cutoff = self.threshold
        elif distances:
            sorted_dists = sorted(distances)
            p95_idx = int(0.95 * (len(sorted_dists) - 1))
            cutoff = sorted_dists[p95_idx]
        else:
            cutoff = 0.5

        # Group sentences into initial semantic clusters based on cutoff
        clusters: list[list[str]] = []
        current_cluster = [sentences[0]]
        for i, dist in enumerate(distances):
            if dist > cutoff:
                clusters.append(current_cluster)
                current_cluster = [sentences[i + 1]]
            else:
                current_cluster.append(sentences[i + 1])
        if current_cluster:
            clusters.append(current_cluster)

        # Step 5: Enforce min/max tokens
        merged_clusters: list[list[str]] = []
        buf_cluster: list[str] = []
        for cl in clusters:
            buf_cluster.extend(cl)
            cluster_text = " ".join(buf_cluster)
            if estimate_tokens(cluster_text) >= self.min_tokens:
                merged_clusters.append(buf_cluster)
                buf_cluster = []
        if buf_cluster:
            if merged_clusters:
                merged_clusters[-1].extend(buf_cluster)
            else:
                merged_clusters.append(buf_cluster)

        # Apply overlap & build final chunk contents
        final_texts: list[str] = []
        for i, cl_sents in enumerate(merged_clusters):
            if i > 0 and self.overlap_sentences > 0 and merged_clusters[i - 1]:
                overlap_sents = merged_clusters[i - 1][-self.overlap_sentences :]
                chunk_sents = overlap_sents + cl_sents
            else:
                chunk_sents = cl_sents

            chunk_text = " ".join(chunk_sents)

            if estimate_tokens(chunk_text) > self.max_tokens:
                sub_chunks = self._split_large_text(chunk_text)
                final_texts.extend(sub_chunks)
            else:
                final_texts.append(chunk_text)

        return [
            Chunk(
                index=idx,
                content=txt,
                token_estimate=estimate_tokens(txt),
                metadata=dict(metadata or {}),
            )
            for idx, txt in enumerate(final_texts)
        ]

    def _split_large_text(self, text: str) -> list[str]:
        words = text.split()
        chunks = []
        current: list[str] = []
        for w in words:
            current.append(w)
            if estimate_tokens(" ".join(current)) >= self.max_tokens:
                chunks.append(" ".join(current))
                current = []
        if current:
            chunks.append(" ".join(current))
        return chunks


async def chunk_document_async(text: str, *, metadata: dict | None = None) -> list[Chunk]:
    if settings.rag_chunk_method == "fixed":
        return ParagraphChunker().split(text, metadata=metadata)
    return await SemanticChunker().split_async(text, metadata=metadata)
