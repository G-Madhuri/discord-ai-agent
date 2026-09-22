from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.rag.interfaces import KnowledgeScope, ScoredChunk, StoredChunk
from app.rag.retrievers.keyword import search_keyword
from app.rag.retrievers.semantic import search_semantic

logger = get_logger(__name__)


async def search_hybrid(
    session: AsyncSession,
    scope: KnowledgeScope,
    query: str,
    *,
    retrieval_mode: str | None = None,
    semantic_top_k: int | None = None,
    keyword_top_k: int | None = None,
    final_top_k: int | None = None,
    rrf_k: int | None = None,
    embedding_provider: Any = None,
) -> list[ScoredChunk]:
    """Hybrid retrieval fusing pgvector semantic search and tsvector keyword search via RRF."""
    if not query or not query.strip():
        return []

    mode = retrieval_mode or settings.rag_retrieval_mode
    s_k = semantic_top_k or settings.rag_semantic_top_k
    k_k = keyword_top_k or settings.rag_keyword_top_k
    f_k = final_top_k or settings.rag_final_top_k
    r_k = rrf_k or settings.rag_rrf_k

    if mode == "keyword":
        return (await search_keyword(session, scope, query, top_k=f_k))[:f_k]

    if mode == "semantic":
        return (
            await search_semantic(
                session, scope, query, top_k=f_k, embedding_provider=embedding_provider
            )
        )[:f_k]

    # Hybrid mode (parallel search + RRF fusion)
    sem_res: list[ScoredChunk] = []
    kw_res: list[ScoredChunk] = []

    async def _do_sem():
        nonlocal sem_res
        try:
            sem_res = await search_semantic(
                session, scope, query, top_k=s_k, embedding_provider=embedding_provider
            )
        except Exception as exc:
            logger.warning("Semantic retrieval failed: %s. Falling back to keyword-only.", exc)

    async def _do_kw():
        nonlocal kw_res
        try:
            kw_res = await search_keyword(session, scope, query, top_k=k_k)
        except Exception as exc:
            logger.warning("Keyword retrieval failed: %s. Falling back to semantic-only.", exc)

    await asyncio.gather(_do_sem(), _do_kw())

    if not sem_res and not kw_res:
        return []

    # Reciprocal Rank Fusion (RRF): score(chunk) = sum( 1 / (rrf_k + rank_i) )
    scores: dict[str, float] = {}
    chunks_map: dict[str, StoredChunk] = {}

    for rank, item in enumerate(sem_res, start=1):
        cid = str(item.chunk.chunk_id)
        scores[cid] = scores.get(cid, 0.0) + (1.0 / (r_k + rank))
        chunks_map[cid] = item.chunk

    for rank, item in enumerate(kw_res, start=1):
        cid = str(item.chunk.chunk_id)
        scores[cid] = scores.get(cid, 0.0) + (1.0 / (r_k + rank))
        chunks_map[cid] = item.chunk

    sorted_ids = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)
    fused_results = [
        ScoredChunk(chunk=chunks_map[cid], score=scores[cid]) for cid in sorted_ids[:f_k]
    ]

    return fused_results
