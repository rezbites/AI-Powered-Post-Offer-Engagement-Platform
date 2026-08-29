"""Declarative base, id/timestamp mixins, and portable column types."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    """Timezone-aware UTC now.

    Everything is stored in UTC and rendered in IST at the edge. Storing local
    time is the classic source of off-by-a-day errors in exactly the kind of
    date arithmetic this system depends on ("joining in 7 days").
    """
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class UUIDPrimaryKeyMixin:
    """String UUID primary keys.

    Chosen over auto-increment integers so ids are non-enumerable (a sequential
    /candidates/1 invites scraping) and safe to generate client-side or across
    shards without coordination.

    Stored as CHAR(36) rather than a native UUID column to keep the SQLite
    fallback working. The cost is index size and — because uuid4 is random —
    poor B-tree insert locality. At the scale where that bites, the fix is
    UUIDv7 or ULID, which keep the non-enumerable property while sorting by
    creation time. Noted in docs/decisions.md.
    """

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)


class TimestampMixin:
    """Created/updated audit columns.

    Defaults are applied server-side (`func.now()`) as well as in Python so
    rows written by migrations or raw SQL are still stamped correctly.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        server_default=func.now(),
        onupdate=utcnow,
        nullable=False,
    )
