"""Attention queue endpoints.

This backs the first thing a recruiter sees each morning: a ranked list
answering "who needs my attention today, and why?".
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.core.deps import SessionDep
from app.domain.enums import NextAction, RiskLevel
from app.modules.attention import service

router = APIRouter(tags=["attention"])


class AttentionEntry(BaseModel):
    """One queue row.

    Deliberately self-contained: the recruiter must be able to decide whether
    to act without opening the candidate. That means the reasons and the
    recommended action travel with the row, not one click away.
    """

    candidate_id: str
    name: str
    role_title: str
    recruiter_name: str | None = None
    joining_date: date
    days_to_joining: int
    risk_level: RiskLevel
    risk_confidence: float
    priority: float = Field(description="Ranking score; higher is more urgent.")
    reasons: list[str]
    recommended_action: NextAction
    recommended_action_label: str


class AttentionQueueResponse(BaseModel):
    items: list[AttentionEntry]
    total_active: int = Field(description="Active candidates considered for ranking.")
    generated_for: date


@router.get(
    "/attention-queue",
    response_model=AttentionQueueResponse,
    summary="Ranked list of candidates needing attention today",
)
async def attention_queue(
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    recruiter_id: Annotated[str | None, Query(description="Restrict to one recruiter.")] = None,
) -> AttentionQueueResponse:
    """Deterministic ranking - no LLM call.

    Ordering a work queue must be instant, identical on every refresh, and
    explainable. The model's contribution happens upstream, where it turns free
    text into typed signals that feed the risk band; it plays no part in the
    sort itself.
    """
    today = date.today()
    ranked = await service.build_attention_queue(session, today=today, limit=limit, recruiter_id=recruiter_id)

    items = []
    for item, candidate, analysis in ranked:
        # Falls back to NO_ACTION when the candidate has not been analysed
        # yet - the queue still ranks them on deterministic factors alone.
        action = NextAction(analysis.next_action) if analysis else NextAction.NO_ACTION

        items.append(
            AttentionEntry(
                candidate_id=candidate.id,
                name=candidate.name,
                role_title=candidate.role_title,
                recruiter_name=candidate.recruiter.name if candidate.recruiter else None,
                joining_date=candidate.joining_date,
                days_to_joining=item.days_to_joining,
                risk_level=item.risk_level,
                risk_confidence=candidate.risk_confidence,
                priority=item.priority,
                reasons=item.reasons,
                recommended_action=action,
                recommended_action_label=action.label,
            )
        )

    return AttentionQueueResponse(
        items=items,
        total_active=len(ranked),
        generated_for=today,
    )


@router.post(
    "/attention-queue/recompute-risk",
    summary="Refresh rule-derived risk for all active candidates",
)
async def recompute_risk(session: SessionDep) -> dict[str, int]:
    """Operational endpoint.

    Stored risk drifts as days pass even when nothing else changes - a
    candidate silently moves into the critical window. In production this is a
    scheduled job; exposing it manually keeps the demo honest and gives an
    operator a way to force consistency.

    Human overrides and AI-sourced risk are left untouched.
    """
    return await service.recompute_risk(session, today=date.today())
