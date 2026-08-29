"""Uniform error envelope and the exception hierarchy behind it.

Every failure — validation, missing row, LLM outage, unhandled crash — leaves
the API in exactly one shape:

    {"error": {"code": "...", "message": "...", "details": {...},
               "request_id": "..."}}

A single shape means the frontend has one error path instead of five, and the
request_id lets a user-reported failure be found in logs immediately.

Unhandled exceptions never leak a stack trace or driver message to the client;
those go to the log, and the caller gets an opaque 500 with a correlation id.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger, request_id_ctx

logger = get_logger(__name__)


class AppError(Exception):
    """Base class for errors this application raises deliberately.

    `status_code` and `code` travel with the exception so handlers stay thin
    and each error type defines its own HTTP semantics in one place.
    """

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    code: str = "internal_error"
    message: str = "An unexpected error occurred."

    def __init__(self, message: str | None = None, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message or self.message)
        if message:
            self.message = message
        self.details = details or {}


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"
    message = "The requested resource does not exist."


class ValidationError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    code = "validation_error"
    message = "The request payload failed validation."


class ConflictError(AppError):
    """Used where a uniqueness constraint expresses a business rule — e.g. the
    automation idempotency key that prevents duplicate follow-ups."""

    status_code = status.HTTP_409_CONFLICT
    code = "conflict"
    message = "The request conflicts with the current state of the resource."


class UnauthorizedError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "unauthorized"
    message = "Authentication is required."


class ForbiddenError(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "forbidden"
    message = "You do not have permission to perform this action."


class RateLimitedError(AppError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "rate_limited"
    message = "Too many requests. Please retry shortly."


class ProviderError(AppError):
    """An upstream LLM call failed irrecoverably.

    Note that most LLM failures never surface as this: the AI pipeline degrades
    to a deterministic fallback instead. This is reserved for cases where the
    caller explicitly demanded a live analysis.
    """

    status_code = status.HTTP_502_BAD_GATEWAY
    code = "provider_error"
    message = "The AI provider could not be reached."


class ServiceUnavailableError(AppError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    code = "service_unavailable"
    message = "A required dependency is unavailable."


def error_body(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
            "request_id": request_id_ctx.get(),
        }
    }


def register_exception_handlers(app: FastAPI) -> None:
    """Wire every failure mode to the single envelope above."""

    @app.exception_handler(AppError)
    async def _app_error(_request: Request, exc: AppError) -> JSONResponse:
        # Expected, business-level failures: logged at warning, no stack trace.
        logger.warning("app_error", code=exc.code, message=exc.message, details=exc.details)
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(exc.code, exc.message, exc.details),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        # Reshape FastAPI's default 422 into our envelope. Field-level errors
        # are preserved so the frontend can highlight the offending inputs.
        details = {
            "fields": [
                {"loc": ".".join(str(p) for p in err.get("loc", [])), "msg": err.get("msg", "")}
                for err in exc.errors()
            ]
        }
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=error_body("validation_error", "The request payload failed validation.", details),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {401: "unauthorized", 403: "forbidden", 404: "not_found", 405: "method_not_allowed"}.get(
            exc.status_code, "http_error"
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(code, str(exc.detail)),
        )

    @app.exception_handler(SQLAlchemyError)
    async def _db_error(_request: Request, exc: SQLAlchemyError) -> JSONResponse:
        """Database failures.

        A genuine connection or driver failure (DBAPIError) is a dependency
        outage and must be a 503 so load balancers stop routing here. Anything
        else from SQLAlchemy - a bad query, a lazy load attempted in async
        context - is *our* bug, and reporting it as 503 would send whoever is
        on call to investigate a perfectly healthy database.

        Either way the driver message stays server-side: it can disclose schema
        and connection details.
        """
        is_outage = isinstance(exc, DBAPIError) and exc.connection_invalidated

        logger.error(
            "database_error",
            error=str(exc),
            error_type=type(exc).__name__,
            outage=is_outage,
            exc_info=True,
        )

        if is_outage:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content=error_body("service_unavailable", "The database is currently unavailable."),
            )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=error_body("internal_error", "An unexpected error occurred."),
        )

    @app.exception_handler(Exception)
    async def _unhandled(_request: Request, exc: Exception) -> JSONResponse:
        logger.error("unhandled_exception", error=str(exc), exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=error_body("internal_error", "An unexpected error occurred."),
        )
