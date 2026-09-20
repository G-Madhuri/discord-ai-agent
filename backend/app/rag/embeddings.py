"""Embedding providers.

`hashing` is a dependency-free, deterministic bag-of-words projection used for
local development and tests. It is lexical, not semantic — it will match
'React' to 'React', not to 'frontend library'. It exists so the whole pipeline
is runnable offline; production sets EMBEDDING_PROVIDER=vertex.
"""

from __future__ import annotations

import hashlib
import math
import re

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
    """Vertex AI text embeddings.

    The SDK is imported lazily so the application starts without Google
    credentials when EMBEDDING_PROVIDER is not 'vertex'.
    """

    def __init__(self, model_name: str | None = None, dimension: int | None = None) -> None:
        self._model_name = model_name or settings.embedding_model
        self._dimension = dimension or settings.embedding_dimension
        self._model = None

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dimension(self) -> int:
        return self._dimension

    def _get_model(self):
        if self._model is None:
            import vertexai
            from vertexai.language_models import TextEmbeddingModel

            if not settings.google_cloud_project:
                raise RuntimeError("GOOGLE_CLOUD_PROJECT must be set to use Vertex embeddings")
            vertexai.init(
                project=settings.google_cloud_project, location=settings.google_cloud_location
            )
            self._model = TextEmbeddingModel.from_pretrained(self._model_name)
        return self._model

    async def _embed_batch(self, texts: list[str], task_type: str) -> list[list[float]]:
        import asyncio

        from vertexai.language_models import TextEmbeddingInput

        model = self._get_model()
        inputs = [TextEmbeddingInput(text=t, task_type=task_type) for t in texts]
        # The Vertex SDK call is synchronous; keep the event loop free.
        result = await asyncio.to_thread(model.get_embeddings, inputs)
        return [list(item.values) for item in result]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return await self._embed_batch(texts, "RETRIEVAL_DOCUMENT")

    async def embed_query(self, text: str) -> list[float]:
        vectors = await self._embed_batch([text], "RETRIEVAL_QUERY")
        return vectors[0]


def get_embedding_provider():
    if settings.embedding_provider == "vertex":
        return VertexEmbeddingProvider()
    return HashingEmbeddingProvider()
