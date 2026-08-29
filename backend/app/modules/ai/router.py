"""AI endpoints."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Query, status
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.deps import ActorDep, SessionDep
from app.core.ratelimit import ai_rate_limit
from app.domain.enums import (
    AnalysisStatus,
    InteractionChannel,
    MessageStatus,
    NextAction,
    RiskLevel,
)
from app.modules.ai import service
from app.modules.candidates.schemas import SignalOut

router = APIRouter(tags=["ai"])
settings = get_settings()


class AnalysisResponse(BaseModel):
    """A stored analysis, with its provenance made explicit.

    `provider` and `mode` are returned on every response so the UI can label
    Demo Mode output as a mock fixture. An interface that presents deterministic
    mock output indistinguishably from model output is misleading, and a
    reviewer who spots it is right to distrust everything else.
    """

    id: str
    candidate_id: str
    summary: str
    risk_level: RiskLevel
    risk_confidence: float = Field(
        description="Derived from evidence quality. An uncalibrated heuristic, not a probability."
    )
    model_confidence: float | None = Field(
        default=None,
        description="The model's own self-reported confidence. Telemetry only; known to be poorly calibrated.",
    )
    model_risk_level: RiskLevel | None = Field(
        default=None,
        description=(
            "The band the model proposed. `risk_level` above is the authoritative "
            "blended band from the hybrid engine; the model does not pick the band."
        ),
    )
    engine_agreed_with_model: bool | None = Field(
        default=None,
        description="Whether the model's proposed band matched the blended band.",
    )
    risk_rationale: str
    signals: list[SignalOut]
    next_action: NextAction
    next_action_label: str
    recommended_follow_up: str

    provider: str = Field(description="'gemini' for live model output, 'mock' for Demo Mode.")
    mode: str = Field(description="'live' or 'demo'.")
    model: str | None = None
    prompt_version: str
    analysis_status: AnalysisStatus = Field(
        description="valid | repaired | failed. 'failed' means a deterministic fallback was stored."
    )
    dropped_signals: int = Field(
        description="Signals discarded because their quote was not found in the candidate's messages."
    )
    latency_ms: int | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    from_cache: bool = False
    created_at: datetime


class MessageResponse(BaseModel):
    id: str
    candidate_id: str
    channel: str
    subject: str | None
    body: str
    tone: str
    status: MessageStatus
    provider: str
    mode: str
    model: str | None = None
    warnings: list[str] = Field(
        default_factory=list,
        description="Safety warnings, e.g. commitment language the recruiter should review.",
    )
    created_at: datetime


def _to_analysis_response(record, *, from_cache: bool) -> AnalysisResponse:
    action = NextAction(record.next_action)
    return AnalysisResponse(
        id=record.id,
        candidate_id=record.candidate_id,
        summary=record.summary,
        risk_level=RiskLevel(record.risk_level),
        risk_confidence=record.risk_confidence,
        model_confidence=record.model_confidence,
        model_risk_level=RiskLevel(record.model_risk_level) if record.model_risk_level else None,
        engine_agreed_with_model=(
            record.model_risk_level == record.risk_level
            if record.model_risk_level
            else None
        ),
        risk_rationale=record.risk_rationale,
        signals=[SignalOut(**s) for s in (record.signals or [])],
        next_action=action,
        next_action_label=action.label,
        recommended_follow_up=record.recommended_follow_up,
        provider=record.provider,
        mode="demo" if record.provider == "mock" else "live",
        model=record.model,
        prompt_version=record.prompt_version,
        analysis_status=AnalysisStatus(record.status),
        dropped_signals=record.dropped_signals,
        latency_ms=record.latency_ms,
        tokens_in=record.tokens_in,
        tokens_out=record.tokens_out,
        from_cache=from_cache,
        created_at=record.created_at,
    )


@router.post(
    "/candidates/{candidate_id}/ai/analyze",
    response_model=AnalysisResponse,
    summary="Analyse a candidate and store the result",
    # Rate limited: each call can cost a real LLM request.
    dependencies=[Depends(ai_rate_limit)],
)
async def analyze_candidate(
    session: SessionDep,
    actor: ActorDep,
    candidate_id: str,
    force: Annotated[bool, Query(description="Bypass the cache and re-analyse.")] = False,
) -> AnalysisResponse:
    """Runs snapshot -> cache -> generate -> validate -> repair -> fallback.

    Always returns an analysis. A provider outage produces a deterministic
    fallback with `analysis_status = failed`, never an error - a dashboard that
    breaks when the model is down would be worse than one that degrades.
    """
    record, from_cache = await service.analyse_candidate(
        session, candidate_id, actor=actor, force=force
    )
    return _to_analysis_response(record, from_cache=from_cache)


@router.post(
    "/ai/analyze-batch",
    summary="Analyse many candidates",
    dependencies=[Depends(ai_rate_limit)],
)
async def analyze_batch(
    session: SessionDep,
    actor: ActorDep,
    candidate_ids: Annotated[list[str], Body(embed=True)],
    force: Annotated[bool, Query()] = False,
) -> dict[str, int]:
    """Batch analysis for demos and backfills.

    Runs sequentially: provider rate limits bind before CPU does, and a burst
    of parallel calls would trip them. In production this becomes a queue with
    a bounded worker pool.
    """
    return await service.analyse_many(
        session, candidate_ids[:200], actor=actor, force=force
    )


@router.post(
    "/candidates/{candidate_id}/ai/message",
    response_model=MessageResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Draft a personalised message",
    dependencies=[Depends(ai_rate_limit)],
)
async def draft_message(
    session: SessionDep,
    actor: ActorDep,
    candidate_id: str,
    channel: Annotated[InteractionChannel, Query()] = InteractionChannel.EMAIL,
) -> MessageResponse:
    """Generates a draft. Nothing is sent until a recruiter approves it."""
    message, warnings = await service.generate_message(
        session, candidate_id, channel=channel, actor=actor
    )
    return MessageResponse(
        id=message.id,
        candidate_id=message.candidate_id,
        channel=message.channel,
        subject=message.subject,
        body=message.body,
        tone=message.tone,
        status=MessageStatus(message.status),
        provider=message.provider,
        mode="demo" if message.provider == "mock" else "live",
        model=message.model,
        warnings=warnings,
        created_at=message.created_at,
    )


@router.get(
    "/candidates/{candidate_id}/ai/messages",
    response_model=list[MessageResponse],
    summary="List drafted messages",
)
async def list_messages(session: SessionDep, candidate_id: str) -> list[MessageResponse]:
    rows = await service.list_messages(session, candidate_id)
    return [
        MessageResponse(
            id=m.id,
            candidate_id=m.candidate_id,
            channel=m.channel,
            subject=m.subject,
            body=m.body,
            tone=m.tone,
            status=MessageStatus(m.status),
            provider=m.provider,
            mode="demo" if m.provider == "mock" else "live",
            model=m.model,
            created_at=m.created_at,
        )
        for m in rows
    ]


class MessageEdit(BaseModel):
    subject: str | None = Field(default=None, max_length=200)
    body: str = Field(min_length=1, max_length=5000)


@router.patch(
    "/ai/messages/{message_id}",
    response_model=MessageResponse,
    summary="Edit a draft before approving it",
)
async def edit_message(
    session: SessionDep, actor: ActorDep, message_id: str, payload: MessageEdit
) -> MessageResponse:
    """Drafts are editable; approved messages are not.

    A recruiter who cannot adjust the wording will paste it into their own mail
    client instead, and the approval trail disappears.
    """
    message = await service.update_message(
        session, message_id, subject=payload.subject, body=payload.body, actor=actor
    )
    return MessageResponse(
        id=message.id,
        candidate_id=message.candidate_id,
        channel=message.channel,
        subject=message.subject,
        body=message.body,
        tone=message.tone,
        status=MessageStatus(message.status),
        provider=message.provider,
        mode="demo" if message.provider == "mock" else "live",
        model=message.model,
        created_at=message.created_at,
    )


@router.post(
    "/ai/messages/{message_id}/approve",
    response_model=MessageResponse,
    summary="Approve a draft and simulate sending it",
)
async def approve_message(
    session: SessionDep,
    actor: ActorDep,
    message_id: str,
    simulate_send: Annotated[bool, Query()] = True,
) -> MessageResponse:
    """The human gate.

    Actual delivery is simulated, as the brief permits. The approval itself is
    real: recorded, audited, and required before the status can advance past
    draft.
    """
    message = await service.approve_message(
        session, message_id, actor=actor, simulate_send=simulate_send
    )
    return MessageResponse(
        id=message.id,
        candidate_id=message.candidate_id,
        channel=message.channel,
        subject=message.subject,
        body=message.body,
        tone=message.tone,
        status=MessageStatus(message.status),
        provider=message.provider,
        mode="demo" if message.provider == "mock" else "live",
        model=message.model,
        created_at=message.created_at,
    )


@router.get("/ai/status", summary="Which provider is serving analyses")
async def ai_status() -> dict[str, object]:
    """Surfaces Demo Mode unambiguously.

    One of several places that report this, all reading the same resolver, so
    the badge in the UI cannot disagree with what actually produced a result.
    """
    demo = settings.is_demo_mode
    return {
        "provider": settings.resolved_provider,
        "mode": "demo" if demo else "live",
        "model": None if demo else settings.gemini_model,
        "prompt_version": service.pipeline.PROMPT_VERSION,
        "description": (
            "Demo Mode - analyses are produced by a deterministic mock provider. "
            "No LLM calls are made. Set GEMINI_API_KEY to enable Live Mode."
            if demo
            else f"Live Mode - analyses are produced by {settings.gemini_model}."
        ),
    }
