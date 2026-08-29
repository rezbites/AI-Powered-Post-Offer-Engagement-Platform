"""FastAPI application factory and lifespan wiring."""

from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.middleware import RequestContextMiddleware
from app.db.session import engine
from app.modules.ai.router import router as ai_router
from app.modules.analytics.router import router as analytics_router
from app.modules.attention.router import router as attention_router
from app.modules.automation.router import router as automation_router
from app.modules.automation.scheduler import start_scheduler, stop_scheduler
from app.modules.candidates.router import router as candidates_router
from app.modules.engagement.router import router as engagement_router
from app.modules.health.router import router as health_router

settings = get_settings()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Startup/shutdown. Kept deliberately thin — the app must boot even when
    dependencies are unhealthy, so operators can reach /health/ready and see
    *why* rather than staring at a crash loop."""
    configure_logging(settings.log_level, json_output=settings.app_env != "development")

    for problem in settings.validate_production_safety():
        logger.error("unsafe_production_config", problem=problem)

    logger.info(
        "application_started",
        env=settings.app_env,
        provider=settings.resolved_provider,
        mode="demo" if settings.is_demo_mode else "live",
        database="sqlite" if settings.is_sqlite else "postgres",
    )

    # Started after logging so scheduler output is formatted consistently.
    # Failure here must not prevent the app serving requests: automation is
    # important, but a recruiter still needs the dashboard without it.
    try:
        start_scheduler()
    except Exception as exc:  # noqa: BLE001
        logger.error("scheduler_start_failed", error=str(exc), exc_info=True)

    yield

    stop_scheduler()
    await engine.dispose()
    logger.info("application_stopped")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Post-Offer Engagement Platform",
        description=(
            "Helps recruiters engage candidates between offer acceptance and joining: "
            "tracks the engagement journey, detects joining risk, and recommends next actions."
        ),
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )

    # Order matters: request context is outermost so the request_id exists
    # before any downstream middleware or handler tries to log.
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )

    register_exception_handlers(app)

    app.include_router(health_router, prefix=settings.api_prefix)
    app.include_router(ai_router, prefix=settings.api_prefix)
    app.include_router(analytics_router, prefix=settings.api_prefix)
    app.include_router(attention_router, prefix=settings.api_prefix)
    app.include_router(automation_router, prefix=settings.api_prefix)
    app.include_router(candidates_router, prefix=settings.api_prefix)
    # Registered after candidates so the more specific /candidates/{id}/...
    # routes do not shadow the candidate detail route.
    app.include_router(engagement_router, prefix=settings.api_prefix)

    return app


app = create_app()
