"""Audit trail endpoints.

Admin-only, and that restriction is not decorative. Audit rows carry
before/after snapshots of candidate records, which means they contain PII and a
complete history of every judgement a recruiter has made. Exposing that to all
recruiters would turn an accountability mechanism into a surveillance one.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select

from app.core.deps import SessionDep, require_role
from app.db.models import AuditLog
from app.domain.enums import UserRole

router = APIRouter(prefix="/audit", tags=["audit"])


class AuditEntry(BaseModel):
    id: str
    actor_id: str | None
    entity_type: str
    entity_id: str
    action: str
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    created_at: datetime


@router.get(
    "",
    response_model=list[AuditEntry],
    summary="Audit trail (admin only)",
    # RBAC applied here rather than checked inside the handler, so the
    # restriction is visible in the route definition and in the OpenAPI schema.
    dependencies=[Depends(require_role(UserRole.ADMIN.value))],
)
async def list_audit(
    session: SessionDep,
    entity_type: Annotated[str | None, Query()] = None,
    entity_id: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[AuditEntry]:
    """Recent state changes, newest first.

    The interesting query is `entity_type=candidate&entity_id=...`, which
    reconstructs exactly what the model said and exactly what a recruiter
    changed it to - the evidence that makes human-in-the-loop a fact rather
    than a claim.
    """
    stmt = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)

    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    if entity_id:
        stmt = stmt.where(AuditLog.entity_id == entity_id)

    rows = (await session.execute(stmt)).scalars().all()
    return [AuditEntry.model_validate(r, from_attributes=True) for r in rows]
