"""Embedding providers.

VertexEmbeddingProvider uses Google Vertex AI (text-embedding-004, 768 dimensions)
with batching (up to 50 texts per call) and exponential backoff retry.
HashingEmbeddingProvider remains available as a deterministic offline fallback for local tests.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import re
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_WORD = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return _WORD.findall(text.lower())


class HashingEmbeddingProvider:
    """Deterministic hashed bag-of-words vectors, L2-normalised."""

    def __init__(self, dimension: int | None = None) -> None:
        self._dimension = dimension or settings.embedding_dimension

    @property
    def model_name(self) -> str:
        return f"hashing-{self._dimension}"

    @property
    def dimension(self) -> int:
        return self._dimension

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self._dimension
        tokens = _tokenize(text)
        if not tokens:
            return vector
        for token in tokens:
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "big") % self._dimension
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[bucket] += sign
        norm = math.sqrt(sum(v * v for v in vector))
        return [v / norm for v in vector] if norm else vector

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


class VertexEmbeddingProvider:
    """Vertex AI text-embedding-004 embeddings (768 dimensions) using google.genai client.

    Batches up to 50 chunks per API call and retries up to 3 times with exponential backoff.
    Fails loud on repeated failure without fallback to hashing.
    """

    def __init__(self, model_name: str | None = None, dimension: int | None = None) -> None:
        self._model_name = model_name or settings.rag_embedding_model
        self._dimension = dimension or settings.embedding_dimension
        self._client: Any = None

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dimension(self) -> int:
        return self._dimension

    def _get_client(self) -> Any:
        if self._client is None:
            from google import genai

            if not settings.google_cloud_project:
                raise RuntimeError("GOOGLE_CLOUD_PROJECT must be set to use Vertex embeddings")
            self._client = genai.Client(
                vertexai=settings.google_genai_use_vertexai,
                project=settings.google_cloud_project,
                location=settings.google_cloud_location,
            )
        return self._client

    async def _embed_batch_with_retry(self, texts: list[str]) -> list[list[float]]:
        client = self._get_client()

        def _call_embed():
            resp = client.models.embed_content(
                model=self._model_name,
                contents=texts,
            )
            if not resp or not resp.embeddings:
                raise RuntimeError(
                    f"Vertex AI embed_content returned empty response for model {self._model_name}"
                )
            return [list(emb.values) for emb in resp.embeddings]

        max_attempts = 3
        backoff = 1.0
        last_exc: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                loop = asyncio.get_running_loop()
                return await loop.run_in_executor(None, _call_embed)
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "Vertex AI embed_content attempt %d/%d failed: %s", attempt, max_attempts, exc
                )
                if attempt < max_attempts:
                    await asyncio.sleep(backoff)
                    backoff *= 2.0

        raise RuntimeError(
            f"Vertex AI embedding failed after {max_attempts} attempts: {last_exc}"
        ) from last_exc

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        batch_size = 50
        all_embeddings = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            batch_vecs = await self._embed_batch_with_retry(batch)
            all_embeddings.extend(batch_vecs)
        return all_embeddings

    async def embed_query(self, text: str) -> list[float]:
        results = await self._embed_batch_with_retry([text])
        return results[0]


def get_embedding_provider():
    if settings.embedding_provider == "vertex" or settings.rag_vector_store == "postgres":
        if settings.google_cloud_project:
            return VertexEmbeddingProvider()
    return HashingEmbeddingProvider()
