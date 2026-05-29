"""Structured logging via structlog. JSON output prod, console dev."""
import logging
import sys
from typing import Any

import structlog
from structlog.types import EventDict, Processor

from src.core.config import settings

SENSITIVE_KEYS = frozenset({
    "password", "access_token", "refresh_token", "partner_key", "partner_secret",
    "credentials", "x-shopee-signature", "signature", "api_key", "secret_key",
    "admin_secret_token", "authorization", "cookie", "set-cookie",
})


def redact_processor(_logger: Any, _method: str, event_dict: EventDict) -> EventDict:
    for k in list(event_dict.keys()):
        if k.lower() in SENSITIVE_KEYS:
            event_dict[k] = "***REDACTED***"
    return event_dict


def configure_logging() -> None:
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        timestamper,
        redact_processor,
        structlog.processors.StackInfoRenderer(),
    ]

    if settings.ENVIRONMENT == "development":
        renderer: Processor = structlog.dev.ConsoleRenderer(colors=True)
    else:
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.processors.dict_tracebacks,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, settings.LOG_LEVEL)
        ),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, settings.LOG_LEVEL),
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
