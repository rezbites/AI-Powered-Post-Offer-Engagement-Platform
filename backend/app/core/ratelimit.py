"""In-process rate limiting for expensive endpoints.

Applied only where a request costs real money or real latency - the AI analysis
and message-generation routes. Rate limiting cheap reads would add contention
for no benefit.

## The honest limitation

The counters live in this process's memory. With more than one API replica each
gets its own budget, so the effective limit is `limit x replicas`. It also
resets on restart.

That is acceptable here because the purpose is to stop one enthusiastic user
(or a runaway script) burning the LLM budget - not to defend against a
distributed attacker. A real deployment puts this in Redis with a sliding
window, or at the edge in an API gateway, which is written up in the README.

The alternative - pulling in Redis solely for this - would add a service to run
and explain in exchange for correctness at a replica count the system does not
have yet.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Annotated

from fastapi import Depends, Request

from app.core.config import get_settings
from app.core.deps import Actor, get_current_actor
from app.core.errors import RateLimitedError
from app.core.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()

# Timestamps of recent requests, keyed by caller identity.
_HITS: dict[str, deque[float]] = defaultdict(deque)

# Sliding window rather than fixed buckets. A fixed 60-second bucket lets a
# caller spend the whole budget at 0:59 and the whole budget again at 1:01 -
# double the intended rate at the boundary.
WINDOW_SECONDS = 60

# Sized for a recruiter working through a queue by hand: comfortably above
# normal use, far below what a loop could generate.
AI_REQUESTS_PER_MINUTE = 20


def _identity(request: Request, actor: Actor) -> str:
    """Who to bill this request to.

    Prefers the authenticated recruiter id so a shared office IP is not
    collectively throttled. Falls back to client host for anonymous callers.
    """
    if actor.is_authenticated and actor.id:
        return f"user:{actor.id}"
    client = request.client.host if request.client else "unknown"
    return f"ip:{client}"


def _check(key: str, limit: int) -> None:
    now = time.monotonic()
    hits = _HITS[key]

    # Drop everything that has aged out of the window.
    cutoff = now - WINDOW_SECONDS
    while hits and hits[0] < cutoff:
        hits.popleft()

    if len(hits) >= limit:
        retry_after = int(WINDOW_SECONDS - (now - hits[0])) + 1
        logger.warning("rate_limited", key=key, limit=limit, window=WINDOW_SECONDS)
        raise RateLimitedError(
            f"Too many requests. Try again in {retry_after} seconds.",
            details={"limit": limit, "window_seconds": WINDOW_SECONDS, "retry_after": retry_after},
        )

    hits.append(now)


async def ai_rate_limit(
    request: Request,
    actor: Annotated[Actor, Depends(get_current_actor)],
) -> None:
    """Guard the LLM-backed endpoints.

    In Demo Mode there is no provider cost, but the limit still applies: a
    demo that behaves differently from production hides exactly the problems a
    demo should surface.
    """
    _check(_identity(request, actor), AI_REQUESTS_PER_MINUTE)


def reset_limits() -> None:
    """Clear all counters. Used by tests, which must not inherit each other's
    request history."""
    _HITS.clear()
