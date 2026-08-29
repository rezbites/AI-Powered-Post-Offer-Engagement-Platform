"""Authentication endpoints.

Deliberately small: login, and "who am I". There is no registration endpoint -
recruiters are provisioned by an administrator, and a self-service signup on an
internal HR tool holding candidate PII would be a liability, not a feature.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select

from app.core.config import get_settings
from app.core.deps import ActorDep, SessionDep
from app.core.errors import UnauthorizedError
from app.core.logging import get_logger
from app.db.models import Recruiter
from app.domain.enums import UserRole
from app.modules.auth.security import create_access_token, hash_password, verify_password

# A genuine argon2 hash, computed once at import, of a value nobody knows.
# Verifying against it on the account-not-found path makes that path do the
# same expensive work as a real check, so response time does not reveal
# whether an account exists. A hand-written literal would not parse and
# would return early, silently defeating the point.
_DUMMY_HASH = hash_password("timing-parity-placeholder-not-a-credential")

router = APIRouter(prefix="/auth", tags=["auth"])
settings = get_settings()
logger = get_logger(__name__)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in_minutes: int
    recruiter_id: str
    name: str
    role: UserRole


class MeResponse(BaseModel):
    recruiter_id: str | None
    name: str | None
    email: str | None
    role: str
    authenticated: bool


@router.post("/login", response_model=LoginResponse, summary="Exchange credentials for a token")
async def login(session: SessionDep, payload: LoginRequest) -> LoginResponse:
    """Issue an access token.

    Every failure path returns the same message and the same status. Telling a
    caller whether the *email* was wrong versus the *password* hands them a
    free user-enumeration oracle, which is how credential-stuffing lists get
    refined.

    The password is verified even when no user is found, so the response time
    does not leak account existence through a timing difference.
    """
    stmt = select(Recruiter).where(Recruiter.email == str(payload.email).lower())
    recruiter = (await session.execute(stmt)).scalar_one_or_none()

    # Constant-ish work regardless of whether the account exists.
    stored_hash = recruiter.password_hash if recruiter else _DUMMY_HASH
    password_ok = verify_password(payload.password, stored_hash)

    if recruiter is None or not password_ok or not recruiter.is_active:
        logger.warning(
            "login_failed",
            # The email is PII and the redaction filter masks it; the reason is
            # recorded server-side only, never returned to the caller.
            reason="invalid_credentials" if recruiter else "unknown_account",
        )
        raise UnauthorizedError("Invalid email or password.")

    token = create_access_token(subject=recruiter.id, role=recruiter.role)
    logger.info("login_succeeded", recruiter_id=recruiter.id, role=recruiter.role)

    return LoginResponse(
        access_token=token,
        expires_in_minutes=settings.jwt_expire_minutes,
        recruiter_id=recruiter.id,
        name=recruiter.name,
        role=UserRole(recruiter.role),
    )


@router.get("/me", response_model=MeResponse, summary="Current caller")
async def me(session: SessionDep, actor: ActorDep) -> MeResponse:
    """Who the current token belongs to.

    Returns `authenticated: false` rather than 401 for anonymous callers, so
    the frontend can render a signed-out state without treating it as an error.
    """
    if not actor.is_authenticated:
        return MeResponse(
            recruiter_id=None, name=None, email=None, role="anonymous", authenticated=False
        )

    recruiter = await session.get(Recruiter, actor.id)
    if recruiter is None:
        # Valid signature, but the account has since been deleted.
        return MeResponse(
            recruiter_id=None, name=None, email=None, role="anonymous", authenticated=False
        )

    return MeResponse(
        recruiter_id=recruiter.id,
        name=recruiter.name,
        email=recruiter.email,
        role=recruiter.role,
        authenticated=True,
    )
