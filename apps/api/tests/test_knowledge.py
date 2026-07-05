from __future__ import annotations

import json
import math
from collections.abc import Callable, Generator

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401
from app.core.config import get_settings
from app.db.base import Base
from app.db.session import get_db
from app.knowledge.embeddings import (
    LocalHashingEmbeddings,
    OpenAIEmbeddings,
    get_embedding_provider,
)
from app.knowledge.ingestion import (
    KNOWLEDGE_INGESTED_AT,
    builtin_documents,
    chunk_markdown,
    ingest_builtin_knowledge_documents,
)
from app.knowledge.search import search_knowledge
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


def test_local_hashing_embeddings_return_configured_dimensions() -> None:
    provider = LocalHashingEmbeddings()
    vectors = provider.embed(["billing retry webhook regression", "MRR drop"])

    assert provider.dimensions == 96
    assert len(vectors) == 2
    assert all(len(vector) == provider.dimensions for vector in vectors)


def _mock_openai_embedding_response(input_texts: list[str], dimensions: int) -> dict:
    return {
        "object": "list",
        "data": [
            {
                "object": "embedding",
                "index": index,
                "embedding": [float((index + 1) * (dim + 1)) for dim in range(dimensions)],
            }
            for index, _ in enumerate(input_texts)
        ],
        "model": "text-embedding-3-small",
        "usage": {"prompt_tokens": 10, "total_tokens": 10},
    }


def test_openai_embeddings_project_to_configured_dimensions() -> None:
    input_texts = ["billing retry webhook regression", "MRR drop"]
    raw_dimensions = 1536

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["model"] == "text-embedding-3-small"
        assert payload["input"] == input_texts
        return httpx.Response(
            200,
            json=_mock_openai_embedding_response(input_texts, raw_dimensions),
        )

    transport = httpx.MockTransport(handler)
    provider = OpenAIEmbeddings(
        api_key="sk-test", model="text-embedding-3-small", transport=transport
    )
    vectors = provider.embed(input_texts)

    assert provider.dimensions == 96
    assert len(vectors) == 2
    assert all(len(vector) == 96 for vector in vectors)
    assert all(math.sqrt(sum(v * v for v in vector)) == pytest.approx(1.0) for vector in vectors)


def test_openai_embeddings_request_native_dimension_reduction() -> None:
    input_texts = ["test"]
    requested_dimensions: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requested_dimensions.append(int(payload.get("dimensions", 0)))
        return httpx.Response(
            200,
            json=_mock_openai_embedding_response(input_texts, 512),
        )

    transport = httpx.MockTransport(handler)
    provider = OpenAIEmbeddings(
        api_key="sk-test",
        model="text-embedding-3-small",
        output_dimensions=96,
        transport=transport,
    )
    provider.embed(input_texts)

    assert requested_dimensions == [512]


def test_get_embedding_provider_defaults_to_local() -> None:
    get_settings.cache_clear()
    try:
        provider = get_embedding_provider()
        assert isinstance(provider, LocalHashingEmbeddings)
        assert provider.dimensions == 96
    finally:
        get_settings.cache_clear()


def test_get_embedding_provider_uses_openai_when_configured(monkeypatch) -> None:
    monkeypatch.setenv("EMBEDDING_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
    get_settings.cache_clear()
    try:
        provider = get_embedding_provider()
        assert provider.provider == "openai"
        assert provider.model == "text-embedding-3-small"
        assert provider.dimensions == 96
    finally:
        monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_EMBEDDING_MODEL", raising=False)
        get_settings.cache_clear()


def test_get_embedding_provider_falls_back_to_local_without_openai_key(
    monkeypatch,
) -> None:
    monkeypatch.setenv("EMBEDDING_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    get_settings.cache_clear()
    try:
        provider = get_embedding_provider()
        assert isinstance(provider, LocalHashingEmbeddings)
    finally:
        monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)
        get_settings.cache_clear()


def test_ingest_and_search_with_openai_embeddings(tmp_path, monkeypatch) -> None:
    for make_session in session_factory(tmp_path):
        with make_session() as session:

            def handler(request: httpx.Request) -> httpx.Response:
                payload = json.loads(request.content)
                return httpx.Response(
                    200,
                    json=_mock_openai_embedding_response(
                        payload["input"], 1536
                    ),
                )

            provider = OpenAIEmbeddings(
                api_key="sk-test", model="text-embedding-3-small", transport=httpx.MockTransport(handler)
            )
            result = ingest_builtin_knowledge_documents(session, provider=provider)

            assert result.chunk_count >= result.document_count
            chunk = session.scalar(
                select(KnowledgeDocumentChunk)
                .where(
                    KnowledgeDocumentChunk.document_id
                    == "kb-runbook-billing-retry-regression"
                )
                .order_by(KnowledgeDocumentChunk.chunk_index)
            )
            assert chunk is not None
            assert len(chunk.embedding) == 96

            results = search_knowledge(
                session, "retry webhook failed renewal", provider=provider, limit=3
            )
            assert results
            assert all(len(result.citation) == 9 for result in results)
