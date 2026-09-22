"""Project knowledge: ingest documents, retrieve scoped excerpts.

Only unstructured knowledge lives here. Team, task and assignment facts stay in
PostgreSQL tables and are read through their own services — never inferred from
retrieved text.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.models.project import DocumentChunk, Project, ProjectDocument
from app.rag.interfaces import KnowledgeScope, ScoredChunk
from app.rag.pipeline import KnowledgePipeline, build_pipeline, content_hash
from app.schemas.project import DocumentCreate, DocumentRead, KnowledgeHit, KnowledgeSearchResult

logger = get_logger(__name__)


def scope_for(project: Project) -> KnowledgeScope:
    return KnowledgeScope(server_id=project.server_id, project_id=project.id)


async def ingest_document(
    session: AsyncSession,
    project: Project,
    payload: DocumentCreate,
    pipeline: KnowledgePipeline | None = None,
) -> DocumentRead:
    pipeline = pipeline or build_pipeline(session)
    digest = content_hash(payload.content)

    existing = (
        await session.execute(
            sa.select(ProjectDocument).where(
                ProjectDocument.project_id == project.id,
                ProjectDocument.title == payload.title,
            )
        )
    ).scalar_one_or_none()

    document = existing or ProjectDocument(
        server_id=project.server_id,
        project_id=project.id,
        title=payload.title,
        source_type=payload.source_type,
    )
    document.source_type = payload.source_type
    document.source_uri = payload.source_uri
    document.metadata_json = payload.metadata
    if existing is None:
        session.add(document)
        await session.flush()

    result = await pipeline.ingest(
        scope_for(project),
        document.id,
        payload.content,
        metadata={"title": payload.title, "source_type": str(payload.source_type)},
    )
    document.content_hash = digest
    document.chunk_count = result.chunk_count
    document.embedding_model = result.embedding_model
    document.ingested_at = pipeline.ingested_at()
    await session.flush()

    return DocumentRead(
        id=document.id,
        title=document.title,
        source_type=document.source_type,
        chunk_count=document.chunk_count,
        ingested_at=document.ingested_at,
    )


async def list_documents(
    session: AsyncSession, server_id: uuid.UUID, project_id: uuid.UUID
) -> list[DocumentRead]:
    rows = (
        await session.execute(
            sa.select(ProjectDocument)
            .where(
                ProjectDocument.server_id == server_id,
                ProjectDocument.project_id == project_id,
            )
            .order_by(ProjectDocument.created_at.desc())
        )
    ).scalars()
    return [
        DocumentRead(
            id=d.id,
            title=d.title,
            source_type=d.source_type,
            chunk_count=d.chunk_count,
            ingested_at=d.ingested_at,
        )
        for d in rows
    ]


async def search_knowledge(
    session: AsyncSession,
    project: Project,
    query: str,
    *,
    top_k: int | None = None,
    pipeline: KnowledgePipeline | None = None,
) -> list[ScoredChunk]:
    # Skip retrieval entirely if the project has no ingested document_chunks
    stmt = sa.select(sa.func.count()).select_from(DocumentChunk).where(
        DocumentChunk.server_id == project.server_id,
        DocumentChunk.project_id == project.id,
    )
    chunk_count = (await session.execute(stmt)).scalar_one()
    if chunk_count == 0:
        logger.info("RAG SKIPPED | no documents for project %s", project.id)
        return []

    pipeline = pipeline or build_pipeline(session)
    return await pipeline.search(scope_for(project), query, top_k=top_k or settings.rag_top_k)


async def search_knowledge_result(
    session: AsyncSession,
    project: Project,
    query: str,
    *,
    top_k: int | None = None,
) -> KnowledgeSearchResult:
    hits = await search_knowledge(session, project, query, top_k=top_k)
    return KnowledgeSearchResult(
        query=query,
        project_key=project.key,
        hits=[
            KnowledgeHit(
                document_id=hit.chunk.document_id,
                document_title=hit.chunk.document_title,
                source_type=hit.chunk.source_type,
                chunk_id=hit.chunk.chunk_id,
                chunk_index=hit.chunk.index,
                content=hit.chunk.content,
                score=round(hit.score, 4),
            )
            for hit in hits
        ],
    )


def build_task_query(title: str, description: str | None, skills: list[str]) -> str:
    """The retrieval query used during assignment.

    Combining the task title, its description and its declared skills keeps
    retrieval anchored to what the task actually needs.
    """
    parts = [title]
    if description:
        parts.append(description)
    if skills:
        parts.append(" ".join(skills))
    return " ".join(parts)
