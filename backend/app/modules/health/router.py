"""Liveness, readiness and metrics endpoints.

The split matters for orchestration: /health answers "is the process alive"
(restart me if not), /health/ready answers "can I serve traffic" (route to me
only if yes). Conflating them causes a container to be killed for a transient
database blip it would have recovered from.

/health/ready also reports which LLM provider is live. That is deliberate —
it is one of the surfaces that makes Demo Mode unmistakable.
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.core.config import get_settings
from app.db.session import ping_database

router = APIRouter(tags=["health"])
settings = get_settings()


@router.get("/health", summary="Liveness probe")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready", summary="Readiness probe")
async def health_ready(response: Response) -> dict[str, object]:
    """Reports dependency status. Returns 503 when the database is down so
    load balancers stop routing here, rather than serving broken responses."""
    db_ok = await ping_database()

    # Provider health is reported but does NOT gate readiness: an LLM outage
    # degrades analyses to a deterministic fallback, it does not stop the
    # service answering requests.
    from app.ai.factory import get_provider

    provider = get_provider()
    provider_ok = await provider.healthy()

    if not db_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {
        "status": "ready" if db_ok else "degraded",
        "checks": {
            "database": "ok" if db_ok else "unavailable",
            "llm_provider": "ok" if provider_ok else "degraded",
        },
        # Named explicitly so nobody has to guess whether analyses are real.
        "provider": settings.resolved_provider,
        "mode": "demo" if settings.is_demo_mode else "live",
        "model": settings.active_model,
    }


@router.get("/metrics", summary="Prometheus metrics", include_in_schema=False)
async def metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
