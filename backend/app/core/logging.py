"""Structured JSON logging with request correlation and PII redaction.

Two things matter here beyond "we have logs":

1. Every line carries the request_id, so a recruiter reporting "it broke at
   10:42" can be traced through API, DB and LLM calls in one query.
2. Candidate interaction text and secrets are redacted. This system handles
   PII (names, emails, phones) and API keys; logs are the most common place
   both leak from.
"""

from __future__ import annotations

import logging
import re
import sys
from contextvars import ContextVar
from typing import Any

import structlog

# Populated by RequestContextMiddleware; read by the log processor below.
# A ContextVar (not a global) is what makes this safe under async concurrency —
# each request gets its own value even while tasks interleave.
request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")

# Keys whose values must never reach a log sink, at any nesting depth.
SENSITIVE_KEYS = frozenset(
    {
        "password",
        "password_hash",
        "token",
        "access_token",
        "authorization",
        "jwt_secret",
        "gemini_api_key",
        "api_key",
        "secret",
        "content",  # interaction bodies — candidate-authored PII
        "raw_response",  # may echo interaction text back
        "notes",
    }
)

REDACTED = "[redacted]"

# Structural keys that are never user data. Excluded from scrubbing because
# an ISO-8601 timestamp is otherwise indistinguishable from a phone number
# to a permissive digit-run pattern.
NON_PII_KEYS = frozenset({"timestamp", "level", "event", "logger", "request_id", "exception"})

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
# Deliberately strict: an optional country code followed by exactly ten
# digits. The lookarounds reject digits or hyphens on either side, so a date
# such as 2026-08-29 cannot match a looser digit run.
_PHONE_RE = re.compile(r"(?<![\d-])(?:\+\d{1,3}[\s-]?)?\d{10}(?![\d-])")


def _scrub_value(value: Any) -> Any:
    if isinstance(value, str):
        value = _EMAIL_RE.sub("[email]", value)
        return _PHONE_RE.sub("[phone]", value)
    if isinstance(value, dict):
        return {k: (REDACTED if k.lower() in SENSITIVE_KEYS else _scrub_value(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub_value(v) for v in value]
    return value


def redact_processor(_logger: Any, _name: str, event_dict: dict) -> dict:
    """Drop sensitive keys and mask email/phone patterns anywhere in the event."""
    for key in list(event_dict.keys()):
        lowered = key.lower()
        if lowered in SENSITIVE_KEYS:
            event_dict[key] = REDACTED
        elif lowered not in NON_PII_KEYS:
            event_dict[key] = _scrub_value(event_dict[key])
    return event_dict


def request_id_processor(_logger: Any, _name: str, event_dict: dict) -> dict:
    event_dict["request_id"] = request_id_ctx.get()
    return event_dict


def configure_logging(level: str = "INFO", *, json_output: bool = True) -> None:
    """Idempotent logging setup. Console renderer locally for readability,
    JSON in containers so logs are machine-parseable by any aggregator."""
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level.upper())

    # Uvicorn ships its own handlers; let them propagate into structlog instead
    # of double-printing every access line.
    for noisy in ("uvicorn.access", "uvicorn.error"):
        logging.getLogger(noisy).handlers.clear()
        logging.getLogger(noisy).propagate = True

    renderer = structlog.processors.JSONRenderer() if json_output else structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            request_id_processor,
            redact_processor,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level.upper())),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
