from __future__ import annotations

from collections.abc import Callable, Generator

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401
from app.core.config import get_settings
from app.db.base import Base
from app.db.session import get_db
from app.knowledge.ingestion import (
    KNOWLEDGE_INGESTED_AT,
    builtin_documents,
    chunk_markdown,
    ingest_builtin_knowledge_documents,
)
from app.main import app
from app.models import KnowledgeDocument, KnowledgeDocumentChunk


def session_factory(tmp_path) -> Generator[Callable[[], Session], None, None]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'knowledge_test.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    yield TestingSessionLocal

    Base.metadata.drop_all(engine)
    engine.dispose()


def test_chunk_markdown_preserves_heading_context_and_metadata() -> None:
    metadata, chunks = chunk_markdown(
        """---
source_id: kb-test
title: Retry Runbook
document_type: runbook
owner: Revenue Operations
tags: billing, retry, mrr
---
# Retry Runbook

Use this when paid MRR drops after failed renewals.

## Diagnosis

- Check retry webhook delivery.
- Compare failed renewal invoices with billing tickets.
""",
        max_chars=180,
    )

    assert metadata["source_id"] == "kb-test"
    assert metadata["title"] == "Retry Runbook"
    assert metadata["tags"] == ["billing", "retry", "mrr"]
    assert [chunk.heading_path for chunk in chunks] == [
        "Retry Runbook",
        "Retry Runbook > Diagnosis",
    ]
    assert chunks[1].token_count > 0


def test_builtin_knowledge_ingestion_stores_documents_chunks_and_citations(
    tmp_path,
) -> None:
    for make_session in session_factory(tmp_path):
        with make_session() as session:
            result = ingest_builtin_knowledge_documents(session)

            assert result.document_count >= 20
            assert result.chunk_count >= result.document_count
            assert "kb-runbook-billing-retry-regression" in result.source_ids
            assert (
                session.scalar(select(func.count()).select_from(KnowledgeDocument))
                == result.document_count
            )

            chunk = session.scalar(
                select(KnowledgeDocumentChunk)
                .where(
                    KnowledgeDocumentChunk.document_id
                    == "kb-runbook-billing-retry-regression"
                )
                .order_by(KnowledgeDocumentChunk.chunk_index)
            )

            assert chunk is not None
            assert chunk.embedding
            assert chunk.citation_metadata["source_id"] == chunk.document_id
            assert chunk.citation_metadata["title"] == "Billing Retry Regression Runbook"
            assert chunk.citation_metadata["chunk_id"] == chunk.id


def test_builtin_knowledge_ingestion_refreshes_stale_docs_and_prunes_removed_sources(
    tmp_path,
) -> None:
    for make_session in session_factory(tmp_path):
        with make_session() as session:
            ingest_builtin_knowledge_documents(session)
            manifest = {
                document.source_id: document for document in builtin_documents()
            }

            document = session.get(
                KnowledgeDocument, "kb-runbook-billing-retry-regression"
            )
            assert document is not None
            document.title = "Stale Billing Runbook"
            document.checksum = "stale-checksum"
            document.content = "stale content"

            removed_document = KnowledgeDocument(
                id="kb-removed-built-in",
                title="Removed Built-in",
                document_type="runbook",
                owner="Test",
                source_path="app/knowledge/docs/removed.md",
                source_uri=None,
                checksum="removed",
                document_metadata={"source_id": "kb-removed-built-in"},
                content="removed",
                created_at=KNOWLEDGE_INGESTED_AT,
                updated_at=KNOWLEDGE_INGESTED_AT,
            )
            removed_chunk = KnowledgeDocumentChunk(
                id="kb-removed-built-in#chunk-000",
                document_id="kb-removed-built-in",
                chunk_index=0,
                heading_path="Removed Built-in",
                content="removed",
                token_count=1,
                embedding=[0.0] * 96,
                citation_metadata={
                    "source_id": "kb-removed-built-in",
                    "chunk_id": "kb-removed-built-in#chunk-000",
                    "title": "Removed Built-in",
                    "document_type": "runbook",
                    "heading_path": "Removed Built-in",
                    "source_path": "app/knowledge/docs/removed.md",
                    "source_uri": None,
                    "chunk_index": 0,
                    "tags": [],
                },
                created_at=KNOWLEDGE_INGESTED_AT,
            )
            session.add_all([removed_document, removed_chunk])
            session.commit()

            ingest_builtin_knowledge_documents(session, force=False)

            refreshed = session.get(
                KnowledgeDocument, "kb-runbook-billing-retry-regression"
            )
            assert refreshed is not None
            assert refreshed.title == "Billing Retry Regression Runbook"
            assert (
                refreshed.checksum
                == manifest["kb-runbook-billing-retry-regression"].checksum
            )
            assert refreshed.updated_at == KNOWLEDGE_INGESTED_AT
            assert session.get(KnowledgeDocument, "kb-removed-built-in") is None
            assert (
                session.get(
                    KnowledgeDocumentChunk, "kb-removed-built-in#chunk-000"
                )
                is None
            )


def test_knowledge_search_endpoint_returns_source_ids_and_citation_shape(
    tmp_path,
) -> None:
    for make_session in session_factory(tmp_path):
        with make_session() as session:
            ingest_builtin_knowledge_documents(session)

        def override_get_db() -> Generator[Session, None, None]:
            with make_session() as db:
                yield db

        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)
        try:
            response = client.post(
                "/documents/search",
                json={"query": "retry webhook failed renewal MRR drop", "limit": 5},
            )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        payload = response.json()
        assert payload["query"] == "retry webhook failed renewal MRR drop"
        assert payload["results"]

        first_result = payload["results"][0]
        assert first_result["source_id"]
        assert first_result["title"]
        assert "retry" in first_result["snippet"].lower()
        assert first_result["citation"].keys() == {
            "source_id",
            "chunk_id",
            "title",
            "document_type",
            "heading_path",
            "source_path",
            "source_uri",
            "chunk_index",
            "tags",
        }
        assert isinstance(first_result["citation"]["tags"], list)


def test_document_ingest_endpoint_requires_explicit_operator_token(
    tmp_path,
    monkeypatch,
) -> None:
    for make_session in session_factory(tmp_path):
        def override_get_db() -> Generator[Session, None, None]:
            with make_session() as db:
                yield db

        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)
        try:
            get_settings.cache_clear()
            disabled_response = client.post("/documents/ingest")
            assert disabled_response.status_code == 403

            monkeypatch.setenv("DOCUMENT_INGEST_TOKEN", "refresh-token")
            get_settings.cache_clear()
            missing_token_response = client.post("/documents/ingest")
            assert missing_token_response.status_code == 403

            response = client.post(
                "/documents/ingest",
                headers={"X-Document-Ingest-Token": "refresh-token"},
            )
        finally:
            app.dependency_overrides.clear()
            monkeypatch.delenv("DOCUMENT_INGEST_TOKEN", raising=False)
            get_settings.cache_clear()

        assert response.status_code == 200
        assert response.json()["document_count"] >= 20


def test_embedding_settings_reject_unsupported_provider(monkeypatch) -> None:
    monkeypatch.setenv("EMBEDDING_PROVIDER", "external")
    get_settings.cache_clear()
    try:
        with pytest.raises(ValidationError):
            get_settings()
    finally:
        monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)
        get_settings.cache_clear()
