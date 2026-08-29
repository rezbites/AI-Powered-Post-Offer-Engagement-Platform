"""Append-only audit logging.

Every state change that a human could later be asked to justify goes through
here: status edits, note edits, stage completions, message approvals, and above
all AI risk overrides.

The before/after snapshots are what make the human-in-the-loop story
verifiable rather than merely claimed - given an audit row you can reconstruct
exactly what the model said and exactly what the recruiter changed it to.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import Actor
from app.db.base import utcnow
from app.db.models import AuditLog
from app.domain.enums import AuditAction

# Values that must never be copied into an audit snapshot. Audit rows are
# widely readable (admins, exports), so they get the same redaction discipline
# as logs.
_EXCLUDED_FIELDS = frozenset({"password_hash", "raw_response"})


def _snapshot(entity: Any, fields: list[str]) -> dict[str, Any]:
    """Extract a JSON-safe subset of an ORM object.

    Only the named fields are captured: a full row dump would bloat the table
    and drag in PII that the audit trail does not need.
    """
    result: dict[str, Any] = {}
    for name in fields:
        if name in _EXCLUDED_FIELDS:
            continue
        value = getattr(entity, name, None)
        # Dates/datetimes are not JSON-serialisable by the driver's JSON type.
        result[name] = value.isoformat() if hasattr(value, "isoformat") else value
    return result


async def record(
    session: AsyncSession,
    *,
    actor: Actor,
    entity_type: str,
    entity_id: str,
    action: AuditAction,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> AuditLog:
    """Write one audit row.

    Deliberately joins the caller's transaction rather than committing on its
    own: an audit entry for a change that was later rolled back would be a
    lie. If the business write fails, the audit row disappears with it.
    """
    entry = AuditLog(
        actor_id=actor.id,
        entity_type=entity_type,
        entity_id=entity_id,
        action=action.value,
        before=before,
        after=after,
        created_at=utcnow(),
    )
    session.add(entry)
    return entry


async def record_change(
    session: AsyncSession,
    *,
    actor: Actor,
    entity: Any,
    entity_type: str,
    action: AuditAction,
    tracked_fields: list[str],
    before_snapshot: dict[str, Any],
) -> AuditLog:
    """Convenience wrapper: diff an entity against a snapshot taken earlier.

    Call `snapshot_of(entity, fields)` before mutating, then pass the result
    here afterwards.
    """
    return await record(
        session,
        actor=actor,
        entity_type=entity_type,
        entity_id=entity.id,
        action=action,
        before=before_snapshot,
        after=_snapshot(entity, tracked_fields),
    )


def snapshot_of(entity: Any, fields: list[str]) -> dict[str, Any]:
    return _snapshot(entity, fields)


async def list_for_entity(
    session: AsyncSession, *, entity_type: str, entity_id: str, limit: int = 50
) -> list[AuditLog]:
    stmt = (
        select(AuditLog)
        .where(AuditLog.entity_type == entity_type, AuditLog.entity_id == entity_id)
        .order_by(AuditLog.created_at.desc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())
