"""Request tracing, logging, body-size limit middleware."""
from __future__ import annotations

import time
from typing import TYPE_CHECKING
from uuid import uuid4

import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from src.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = get_logger(__name__)

MAX_BODY_SIZE = 1_048_576  # 1MB


class TraceMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        trace_id = request.headers.get("X-Trace-Id") or str(uuid4())
        structlog.contextvars.bind_contextvars(trace_id=trace_id, path=request.url.path)
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception("request_exception")
            raise
        finally:
            duration = time.perf_counter() - start
            structlog.contextvars.unbind_contextvars("trace_id", "path")
        response.headers["X-Trace-Id"] = trace_id
        logger.info("request", method=request.method, path=request.url.path,
                    status=response.status_code, duration_ms=duration * 1000)
        return response


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        cl = request.headers.get("content-length")
        if cl and int(cl) > MAX_BODY_SIZE:
            return Response(status_code=413, content="Payload too large")
        return await call_next(request)
