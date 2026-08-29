"""Shared FastAPI dependencies.

The actor resolution here is deliberately permissive: routes accept an
authenticated caller when one is present, and fall back to an anonymous actor
otherwise. Full enforcement (login, required roles) is layered on top in the
auth module rather than baked into every route signature, so tightening it is
a one-line change per route instead of a rewrite.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.schemas import PaginationParams
from app.db.session import get_session

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@dataclass(frozen=True)
class Actor:
    """Whoever is performing the current request.

    Recorded on audit rows and on stage completions, so "who marked this
    done?" and "who overrode the AI?" are always answerable.
    """

    id: str | None
    role: str = "recruiter"

    @property
    def is_authenticated(self) -> bool:
        return self.id is not None

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


ANONYMOUS = Actor(id=None, role="recruiter")


async def get_current_actor(
    authorization: Annotated[str | None, Header()] = None,
) -> Actor:
    """Resolve the caller from a Bearer token when present.

    Returns ANONYMOUS rather than raising when no credentials are supplied.
    Routes that must not be anonymous depend on `require_actor` instead - that
    way an unauthenticated read is a product decision, not an oversight.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        return ANONYMOUS

    # Imported lazily: the auth module pulls in JWT machinery that plain
    # read endpoints should not have to load.
    from app.modules.auth.security import decode_token

    token = authorization.split(" ", 1)[1].strip()
    claims = decode_token(token)
    if claims is None:
        return ANONYMOUS
    return Actor(id=claims.get("sub"), role=claims.get("role", "recruiter"))


ActorDep = Annotated[Actor, Depends(get_current_actor)]


def pagination(
    limit: Annotated[int, Query(ge=1, le=200, description="Maximum rows to return.")] = 50,
    offset: Annotated[int, Query(ge=0, description="Rows to skip.")] = 0,
) -> PaginationParams:
    return PaginationParams(limit=limit, offset=offset)


PaginationDep = Annotated[PaginationParams, Depends(pagination)]
