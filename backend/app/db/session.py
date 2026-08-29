"""Async engine, session factory, and the FastAPI session dependency."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

settings = get_settings()


def _engine_kwargs() -> dict[str, Any]:
    """SQLite ignores pool sizing (and errors on some pool args), so the
    connection-pool tuning only applies to the Postgres path."""
    if settings.is_sqlite:
        return {"echo": settings.db_echo}
    return {
        "echo": settings.db_echo,
        "pool_size": settings.db_pool_size,
        "max_overflow": settings.db_max_overflow,
        # Recycle before typical cloud idle-timeouts silently kill sockets,
        # and verify liveness on checkout so a stale connection surfaces as a
        # retry rather than a user-visible 500.
        "pool_recycle": 1800,
        "pool_pre_ping": True,
    }


engine = create_async_engine(settings.database_url, **_engine_kwargs())

SessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,  # keep ORM objects usable after commit in handlers
    autoflush=False,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Request-scoped session.

    Commit is left to the service layer so a single HTTP request can span
    several repository calls inside one transaction. Anything that escapes as
    an exception rolls the whole thing back.
    """
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def ping_database() -> bool:
    """Cheap liveness probe used by /health/ready."""
    from sqlalchemy import text

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001 - probe reports status, never raises
        return False
