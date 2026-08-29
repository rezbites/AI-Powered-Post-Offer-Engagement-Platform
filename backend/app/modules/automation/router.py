"""Automation and follow-up endpoints."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Body, Query
from pydantic import BaseModel, Field

from app.core.deps import ActorDep, SessionDep
from app.domain.enums import AutomationRunStatus, FollowUpStatus, NextAction
from app.domain.rules import RULES
from app.modules.automation import scheduler, service

router = APIRouter(tags=["automation"])


class RuleInfo(BaseModel):
    key: str
    description: str
    dedupe_window_days: int = Field(
        description="One action per candidate per rule per this many days."
    )


class RunSummary(BaseModel):
    id: str
    rule_key: str
    trigger: str
    started_at: datetime
    finished_at: datetime | None
    candidates_scanned: int
    actions_created: int
    actions_skipped: int = Field(
        description="Fired but deduplicated - an action already existed for this window."
    )
    status: AutomationRunStatus
    error: str | None = None


class FollowUpOut(BaseModel):
    id: str
    candidate_id: str
    rule_key: str | None
    title: str
    reason: str
    recommended_action: NextAction
    recommended_action_label: str
    due_date: date | None
    status: FollowUpStatus
    created_at: datetime


class FollowUpUpdate(BaseModel):
    status: FollowUpStatus


def _to_follow_up(row) -> FollowUpOut:
    action = NextAction(row.recommended_action)
    return FollowUpOut(
        id=row.id,
        candidate_id=row.candidate_id,
        rule_key=row.rule_key,
        title=row.title,
        reason=row.reason,
        recommended_action=action,
        recommended_action_label=action.label,
        due_date=row.due_date,
        status=FollowUpStatus(row.status),
        created_at=row.created_at,
    )


@router.get("/automation/rules", response_model=list[RuleInfo], summary="Configured rules")
async def list_rules() -> list[RuleInfo]:
    return [
        RuleInfo(key=r.key, description=r.description, dedupe_window_days=r.dedupe_window_days)
        for r in RULES
    ]


@router.get("/automation/status", summary="Scheduler status")
async def automation_status() -> dict[str, object]:
    """Confirms the job is actually scheduled, rather than inferring it from
    configuration - those two disagree more often than anyone expects."""
    return scheduler.scheduler_status()


@router.post(
    "/automation/run",
    response_model=list[RunSummary],
    summary="Run the engagement rules now",
)
async def run_automation(
    session: SessionDep,
    rule_keys: Annotated[list[str] | None, Body(embed=True)] = None,
    draft_messages: Annotated[bool, Query(description="Also draft candidate messages.")] = True,
) -> list[RunSummary]:
    """Manual trigger, used for demos and for forcing a sweep after a change.

    Safe to call repeatedly: the idempotency key means a second run within the
    same window creates nothing and reports the attempts as `actions_skipped`.
    """
    runs = await service.run_all_rules(
        session,
        trigger="manual",
        rule_keys=rule_keys,
        draft_messages=draft_messages,
        # The manual trigger bypasses the advisory lock: an operator asking for
        # a run should get one, not a silent no-op because a scheduled tick
        # happens to hold the lock.
        use_lock=False,
    )
    return [RunSummary.model_validate(r, from_attributes=True) for r in runs]


@router.get("/automation/runs", response_model=list[RunSummary], summary="Run history")
async def list_runs(
    session: SessionDep, limit: Annotated[int, Query(ge=1, le=100)] = 20
) -> list[RunSummary]:
    """Execution history.

    A background job with no visible run log is unobservable - "did the rule
    fire last night?" must be answerable without grepping container logs.
    """
    rows = await service.recent_runs(session, limit=limit)
    return [RunSummary.model_validate(r, from_attributes=True) for r in rows]


@router.get("/follow-ups", response_model=list[FollowUpOut], summary="Open follow-up actions")
async def list_follow_ups(
    session: SessionDep,
    candidate_id: Annotated[str | None, Query()] = None,
    status_filter: Annotated[FollowUpStatus | None, Query(alias="status")] = FollowUpStatus.OPEN,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> list[FollowUpOut]:
    rows = await service.list_follow_ups(
        session, candidate_id=candidate_id, status=status_filter, limit=limit
    )
    return [_to_follow_up(r) for r in rows]


@router.patch(
    "/follow-ups/{follow_up_id}",
    response_model=FollowUpOut,
    summary="Resolve or dismiss a follow-up",
)
async def update_follow_up(
    session: SessionDep, actor: ActorDep, follow_up_id: str, payload: FollowUpUpdate
) -> FollowUpOut:
    """Closing a follow-up also lifts the attention-queue discount on that
    candidate, so a genuinely neglected candidate resurfaces rather than being
    permanently suppressed by a stale task."""
    row = await service.resolve_follow_up(
        session, follow_up_id, status=payload.status, actor=actor
    )
    return _to_follow_up(row)
