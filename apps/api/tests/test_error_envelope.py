from __future__ import annotations

from collections.abc import Generator
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from app.db.session import get_db
from app.main import app


def _override_db() -> Generator[MagicMock, None, None]:
    yield MagicMock()


def test_unhandled_exception_returns_structured_500() -> None:
    """Unhandled exceptions must return a structured JSON envelope with error
    code, message, and request_id — not a bare 500 (audit P1 #5)."""

    @app.get("/__test-raise__")
    def _raise() -> None:
        raise RuntimeError("boom from test route")

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/__test-raise__")

    assert response.status_code == 500
    body = response.json()
    assert "error" in body
    assert body["error"]["code"] == "internal_error"
    assert "boom from test route" in body["error"]["message"]
    assert body["error"]["request_id"]


def test_unhandled_exception_withholds_detail_in_production(monkeypatch) -> None:
    """In non-dev envs the error envelope must not leak exception details."""

    from app.core.config import get_settings

    monkeypatch.setenv("APP_ENV", "production")
    get_settings.cache_clear()
    try:

        @app.get("/__test-raise-prod__")
        def _raise() -> None:
            raise RuntimeError("secret internal detail")

        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/__test-raise-prod__")

        assert response.status_code == 500
        body = response.json()
        assert body["error"]["code"] == "internal_error"
        assert "secret" not in body["error"]["message"]
        assert body["error"]["request_id"]
    finally:
        get_settings.cache_clear()


def test_evals_run_maps_integrity_error_to_409(monkeypatch) -> None:
    """When run_eval_suite raises IntegrityError, the route should map it to
    HTTP 409 with the structured envelope, not a 500 (audit §3 #5)."""
    from sqlalchemy.exc import IntegrityError

    from app.core.config import get_settings
    from app.evals import router as evals_router

    def raise_integrity(db) -> None:
        raise IntegrityError("stmt", {}, Exception("dup"))

    monkeypatch.setattr(evals_router, "run_eval_suite", raise_integrity)
    monkeypatch.setenv("EVAL_RUN_TOKEN", "eval-token")
    get_settings.cache_clear()
    app.dependency_overrides[get_db] = _override_db
    try:
        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(
            "/evals/run", headers={"X-Eval-Run-Token": "eval-token"}
        )
        assert response.status_code == 409
        body = response.json()
        assert body["error"]["code"] == "conflict"
        assert body["error"]["request_id"]
    finally:
        app.dependency_overrides.pop(get_db, None)
        get_settings.cache_clear()


def test_evals_run_maps_lookup_error_to_404(monkeypatch) -> None:
    """When run_eval_suite raises LookupError (e.g. missing eval case), the
    route should map it to HTTP 404 with the structured envelope."""
    from app.core.config import get_settings
    from app.evals import router as evals_router

    def raise_lookup(db) -> None:
        raise LookupError("eval case not found")

    monkeypatch.setattr(evals_router, "run_eval_suite", raise_lookup)
    monkeypatch.setenv("EVAL_RUN_TOKEN", "eval-token")
    get_settings.cache_clear()
    app.dependency_overrides[get_db] = _override_db
    try:
        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(
            "/evals/run", headers={"X-Eval-Run-Token": "eval-token"}
        )
        assert response.status_code == 404
        body = response.json()
        assert body["error"]["code"] == "not_found"
        assert body["error"]["request_id"]
    finally:
        app.dependency_overrides.pop(get_db, None)
        get_settings.cache_clear()


def test_documents_ingest_maps_integrity_error_to_409(monkeypatch) -> None:
    """When document ingestion raises IntegrityError, the route should map it
    to HTTP 409 with the structured envelope (audit §3 #5)."""
    from sqlalchemy.exc import IntegrityError

    from app.core.config import get_settings
    from app.knowledge import router as knowledge_router

    def raise_integrity(db, *, force: bool = False) -> None:
        raise IntegrityError("stmt", {}, Exception("dup"))

    monkeypatch.setattr(
        knowledge_router, "ingest_builtin_knowledge_documents", raise_integrity
    )
    monkeypatch.setenv("DOCUMENT_INGEST_TOKEN", "ingest-token")
    get_settings.cache_clear()
    app.dependency_overrides[get_db] = _override_db
    try:
        client = TestClient(app, raise_server_exceptions=False)
        response = client.post(
            "/documents/ingest", headers={"X-Document-Ingest-Token": "ingest-token"}
        )
        assert response.status_code == 409
        body = response.json()
        assert body["error"]["code"] == "conflict"
        assert body["error"]["request_id"]
    finally:
        app.dependency_overrides.pop(get_db, None)
        get_settings.cache_clear()
