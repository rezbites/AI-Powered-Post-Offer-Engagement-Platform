"""Password hashing and JWT encode/decode.

Kept separate from the auth router so that dependency resolution (which every
request touches) does not import route handlers.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import get_settings
from app.core.logging import get_logger

settings = get_settings()
logger = get_logger(__name__)

# argon2id: memory-hard, so a leaked hash is expensive to attack offline, and
# unlike bcrypt there is no silent 72-byte password truncation.
pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")


def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Constant-time verification via passlib.

    Returns False on malformed hashes rather than raising, so a corrupted row
    fails closed as a login rejection instead of a 500.
    """
    try:
        return pwd_context.verify(plain, hashed)
    except ValueError:
        return False


def create_access_token(*, subject: str, role: str, expires_minutes: int | None = None) -> str:
    """Mint a short-lived access token.

    No refresh-token flow: this is an internal HR tool with an 8-hour working
    session, so a single access token whose lifetime matches a shift is the
    simpler and smaller attack surface. A public-facing deployment would want
    refresh rotation - noted in docs/decisions.md.
    """
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=expires_minutes or settings.jwt_expire_minutes
    )
    claims: dict[str, Any] = {"sub": subject, "role": role, "exp": expire}
    return jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict[str, Any] | None:
    """Verify and decode. Returns None on any failure.

    Signature errors, expiry and malformed tokens are all treated identically
    and logged at debug: telling a caller *why* their token failed is free
    reconnaissance.
    """
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError as exc:
        logger.debug("token_rejected", reason=type(exc).__name__)
        return None
