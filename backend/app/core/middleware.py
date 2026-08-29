"""Request correlation and access logging."""

from __future__ import annotations

import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.logging import get_logger, request_id_ctx

logger = get_logger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assign every request an id, expose it on the response, and log timing.

    An inbound X-Request-ID is honoured so a trace started at the frontend (or
    a load balancer) stays continuous across the hop. Otherwise we mint one.
    The id is stashed in a ContextVar, which is what lets the error envelope
    and every log line pick it up without threading it through call signatures.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        token = request_id_ctx.set(request_id)
        start = time.perf_counter()

        # All logging happens inside the try/finally, because resetting the
        # ContextVar before the access-log call would strip request_id from the
        # very line that most needs it.
        try:
            response = await call_next(request)

            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            response.headers[REQUEST_ID_HEADER] = request_id

            # Health probes fire constantly; logging them buries real traffic.
            if not request.url.path.endswith(("/health", "/health/ready", "/metrics")):
                logger.info(
                    "request_completed",
                    method=request.method,
                    path=request.url.path,
                    status_code=response.status_code,
                    duration_ms=duration_ms,
                )
            return response

        except Exception:
            # Timing is still useful for failures - often the slowest requests
            # are the ones that fail. The exception handlers do the reporting.
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            logger.exception(
                "request_failed",
                method=request.method,
                path=request.url.path,
                duration_ms=duration_ms,
            )
            raise
        finally:
            request_id_ctx.reset(token)
