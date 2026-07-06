from __future__ import annotations

import json
import logging

from fastapi.testclient import TestClient

from app.logging_config import JsonFormatter, configure_logging
from app.main import app


def test_health_response_includes_request_id() -> None:
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    request_id = response.headers.get("X-Request-ID")
    assert request_id
    assert len(request_id) >= 16


def test_health_response_preserves_inbound_request_id() -> None:
    client = TestClient(app)
    response = client.get("/health", headers={"X-Request-ID": "inbound-request-123"})
    assert response.status_code == 200
    assert response.headers.get("X-Request-ID") == "inbound-request-123"


def test_json_formatter_includes_extra_fields() -> None:
    configure_logging()
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="hello",
        args=(),
        exc_info=None,
    )
    record.request_id = "req-123"
    record.run_id = "run-456"
    record.incident_id = "inc-789"
    output = formatter.format(record)
    parsed = json.loads(output)
    assert parsed["message"] == "hello"
    assert parsed["request_id"] == "req-123"
    assert parsed["run_id"] == "run-456"
    assert parsed["incident_id"] == "inc-789"


def test_request_id_filter_copies_context_var_onto_record() -> None:
    """RequestIdFilter must copy request_id_context onto every log record so
    JSON logs carry the request id without callers passing it manually
    (audit P1 #4)."""
    from app.logging_config import RequestIdFilter, request_id_context

    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="hello",
        args=(),
        exc_info=None,
    )
    rid_filter = RequestIdFilter()
    token = request_id_context.set("req-filter-test")
    try:
        rid_filter.filter(record)
        assert record.request_id == "req-filter-test"
    finally:
        request_id_context.reset(token)


def test_request_id_filter_defaults_to_dash_when_no_context() -> None:
    """When no request is in scope, the filter should still set a sentinel
    so JSON logs always carry the field (never missing)."""
    from app.logging_config import RequestIdFilter, request_id_context

    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="background",
        args=(),
        exc_info=None,
    )
    # Ensure no context is set for this record.
    assert request_id_context.get(None) is None
    RequestIdFilter().filter(record)
    assert record.request_id == "-"


def test_configure_logging_attaches_request_id_filter() -> None:
    """configure_logging must attach RequestIdFilter to the handler so every
    log record going through the pipeline picks up the request id."""
    from app.logging_config import RequestIdFilter

    configure_logging()
    root = logging.getLogger()
    assert any(isinstance(f, RequestIdFilter) for h in root.handlers for f in h.filters)
