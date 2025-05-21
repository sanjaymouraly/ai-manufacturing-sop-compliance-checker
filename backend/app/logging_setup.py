"""Structured logging with credential redaction."""

from __future__ import annotations

import json
import logging
import re
import sys
from typing import Any

from app.config import get_settings

_SECRET_PATTERNS = [
    re.compile(r"(sk-[A-Za-z0-9_\-]{6})[A-Za-z0-9_\-]{6,}"),
    re.compile(r"(sk-or-v1-[A-Za-z0-9]{4})[A-Za-z0-9]{8,}"),
    re.compile(r"(Bearer\s+[A-Za-z0-9_\-]{4})[A-Za-z0-9_\-.]{8,}"),
    re.compile(r"(\"api_key\"\s*:\s*\")[^\"]+"),
    re.compile(r"(LLM_API_KEY=)\S+"),
]


def redact(message: str) -> str:
    out = message
    for pat in _SECRET_PATTERNS:
        out = pat.sub(r"\1***REDACTED***", out)
    return out


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("request_id", "path", "method", "status_code", "duration_ms", "actor"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return redact(json.dumps(payload, default=str))


def configure_logging() -> None:
    settings = get_settings()
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stdout)
    if settings.log_json:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            RedactingFormatter("%(asctime)s | %(levelname)-7s | %(name)-28s | %(message)s", "%H:%M:%S")
        )
    root.addHandler(handler)
    root.setLevel(settings.log_level.upper())
    logging.getLogger("uvicorn.access").setLevel("WARNING")
    logging.getLogger("httpx").setLevel("WARNING")


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
