from __future__ import annotations

import json
import logging
import sys
from typing import Any

from app.core.config import Settings


class JsonFormatter(logging.Formatter):
    """Emit log records as single-line JSON."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if hasattr(record, "request_id"):
            payload["request_id"] = record.request_id
        if hasattr(record, "run_id"):
            payload["run_id"] = record.run_id
        if hasattr(record, "incident_id"):
            payload["incident_id"] = record.incident_id
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(settings: Settings | None = None) -> None:
    if settings is None:
        from app.core.config import get_settings

        settings = get_settings()

    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    if settings.log_format == "json":
        formatter: logging.Formatter = JsonFormatter()
    else:
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers = [handler]


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
