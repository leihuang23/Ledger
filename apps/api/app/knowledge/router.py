from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.bootstrap import bootstrap_lock
from app.core.access import require_demo_data_access
from app.core.config import get_settings
from app.db.session import get_db

from .ingestion import ingest_builtin_knowledge_documents
from .schemas import (
    KnowledgeIngestionResponse,
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
    KnowledgeSearchResult,
)
from .search import search_knowledge

router = APIRouter(
    prefix="/documents",
    tags=["documents"],
    dependencies=[Depends(require_demo_data_access)],
)


def require_document_ingest_access(
    ingest_token: str | None = Header(default=None, alias="X-Document-Ingest-Token"),
) -> None:
    settings = get_settings()
    if settings.document_ingest_token is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Document ingestion API is disabled. Use the CLI/bootstrap path or "
                "set DOCUMENT_INGEST_TOKEN for explicit operator refreshes."
            ),
        )
    if ingest_token is None or not secrets.compare_digest(
        ingest_token, settings.document_ingest_token
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid document ingestion token.",
        )


@router.post("/ingest", dependencies=[Depends(require_document_ingest_access)])
def ingest_documents(db: Session = Depends(get_db)) -> KnowledgeIngestionResponse:
    with bootstrap_lock(db.get_bind()):
        result = ingest_builtin_knowledge_documents(db, force=False)
    return KnowledgeIngestionResponse(
        document_count=result.document_count,
        chunk_count=result.chunk_count,
        source_ids=result.source_ids,
    )


@router.post("/search")
def search_documents(
    payload: KnowledgeSearchRequest, db: Session = Depends(get_db)
) -> KnowledgeSearchResponse:
    results = search_knowledge(db, payload.query, limit=payload.limit)
    return KnowledgeSearchResponse(
        query=payload.query,
        results=[
            KnowledgeSearchResult(
                source_id=result.source_id,
                title=result.title,
                snippet=result.snippet,
                score=result.score,
                citation=result.citation,
            )
            for result in results
        ],
    )
