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
