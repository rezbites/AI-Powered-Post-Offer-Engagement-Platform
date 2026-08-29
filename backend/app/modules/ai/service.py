"""AI orchestration against the database.

Bridges the pure pipeline and the persistence layer: builds the snapshot,
consults the cache, runs the pipeline, stores the ledger row, and propagates
the resulting risk onto the candidate.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.ai import pipeline
from app.ai.factory import get_provider
from app.ai.provider import ProviderUnavailable
from app.ai.snapshot import CandidateSnapshot, build_snapshot
from app.core.deps import Actor
from app.core.errors import NotFoundError, ProviderError
from app.core.logging import get_logger
from app.db.base import utcnow
from app.db.models import AIAnalysisRecord, Candidate, GeneratedMessage
from app.domain import risk
from app.domain.context import CandidateContext, SignalView
from app.domain.enums import (
    AnalysisStatus,
    AuditAction,
    InteractionChannel,
    MessageStatus,
    RiskSource,
)
from app.modules.audit import service as audit
from app.modules.candidates import repository as repo
from app.modules.candidates import service as candidate_service

logger = get_logger(__name__)


async def _load_context_and_snapshot(
    session: AsyncSession, candidate_id: str, *, today: date
) -> tuple[Candidate, CandidateContext, CandidateSnapshot]:
    """Gather everything both the pipeline and the risk engine need."""
    candidate = await repo.get_candidate_with_detail(session, candidate_id)
    if candidate is None:
        raise NotFoundError("Candidate not found.", details={"candidate_id": candidate_id})

    ids = [candidate_id]
    stage_stats = await repo.stage_progress_for(session, ids, today=today)
    next_labels = await repo.next_pending_stage_labels(session, ids)
    unanswered = await repo.unanswered_outbound_counts(session, ids)
    totals = await repo.interaction_counts(session, ids)
    open_follow_ups = await repo.open_follow_up_ids(session, ids)
    stats = stage_stats.get(candidate_id, {})

    context = candidate_service.build_context(
        candidate,
        stage_stats=stats,
        unanswered_outbound=unanswered.get(candidate_id, 0),
        interaction_totals=totals.get(candidate_id, (0, 0)),
        analysis=None,  # signals come from this run, not a previous one
        has_open_follow_up=candidate_id in open_follow_ups,
    )

    snapshot = build_snapshot(
        candidate,
        list(candidate.interactions),
        today=today,
        stages_completed=int(stats.get("completed", 0) or 0),
        stages_total=int(stats.get("total", 0) or 0),
        stages_overdue=int(stats.get("overdue", 0) or 0),
        pending_stage=next_labels.get(candidate_id),
    )

    return candidate, context, snapshot


async def _cached_analysis(
    session: AsyncSession, candidate_id: str, input_hash: str
) -> AIAnalysisRecord | None:
    """Look for an existing analysis of identical candidate state.

    Only VALID and REPAIRED rows are reusable. A cached FAILED row would pin a
    candidate to a degraded fallback until their state happened to change,
    which could be days - so failures are always retried.
    """
    stmt = (
        select(AIAnalysisRecord)
        .where(
            AIAnalysisRecord.candidate_id == candidate_id,
            AIAnalysisRecord.input_hash == input_hash,
            AIAnalysisRecord.status.in_(
                [AnalysisStatus.VALID.value, AnalysisStatus.REPAIRED.value]
            ),
        )
        .order_by(AIAnalysisRecord.created_at.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


def blend_assessment(
    context: CandidateContext, outcome: pipeline.AnalysisOutcome, *, today: date
):
    """Combine LLM-extracted signals with the deterministic engine.

    This is where the hybrid model actually happens. The LLM contributes typed
    signals with quotes; it does **not** choose the band. The band comes from
    `risk.assess`, which weighs those signals alongside timing, silence and
    journey progress.

    Deferring to the model's own band would give up the properties that make
    this design defensible: reproducibility, an auditable score, and an
    explanation a recruiter can argue with. The model's proposal is kept as
    telemetry so disagreement can be measured.
    """
    enriched = replace(
        context,
        signals=[SignalView(type=s.type, evidence=s.evidence) for s in outcome.analysis.signals],
    )
    return enriched, risk.assess(enriched, today=today)


def _apply_risk(
    candidate: Candidate,
    outcome: pipeline.AnalysisOutcome,
    context: CandidateContext,
    *,
    today: date,
):
    """Write the blended assessment onto the candidate row.

    A human override is never overwritten - that is the entire purpose of
    `risk_source`, and silently reverting a recruiter's decision on the next
    analysis would make the override feature a lie.
    """
    enriched, assessment = blend_assessment(context, outcome, today=today)

    if candidate.risk_source == RiskSource.HUMAN.value:
        candidate.last_analyzed_at = utcnow()
        return assessment

    if assessment is None:
        # Terminal candidate: risk no longer applies, so the last recorded
        # band is preserved rather than being overwritten with a meaningless
        # value. The fact that someone who withdrew was flagged HIGH is
        # exactly the history worth keeping.
        candidate.last_analyzed_at = utcnow()
        return None

    candidate.risk_level = assessment.level.value
    candidate.risk_confidence = assessment.confidence
    # A failed run produced a rules-only fallback with no signals, so its
    # provenance is RULE. Labelling a fallback as AI output would misrepresent it.
    candidate.risk_source = (
        RiskSource.RULE if outcome.status is AnalysisStatus.FAILED else RiskSource.AI
    ).value
    candidate.last_analyzed_at = utcnow()
    return assessment


async def analyse_candidate(
    session: AsyncSession,
    candidate_id: str,
    *,
    actor: Actor,
    today: date | None = None,
    force: bool = False,
) -> tuple[AIAnalysisRecord, bool]:
    """Analyse one candidate. Returns the record and whether it came from cache.

    `force=True` bypasses the cache - used by the eval harness and by a
    recruiter explicitly asking for a re-analysis.
    """
    today = today or date.today()
    candidate, context, snapshot = await _load_context_and_snapshot(
        session, candidate_id, today=today
    )
    input_hash = snapshot.input_hash()

    if not force:
        cached = await _cached_analysis(session, candidate_id, input_hash)
        if cached is not None:
            logger.info("analysis_cache_hit", candidate_id=candidate_id)
            return cached, True

    provider = get_provider()
    outcome = await pipeline.analyse(provider, snapshot, context, today=today)

    # Blend first: the row must store the band the product will display,
    # not the model's unreviewed proposal.
    assessment = _apply_risk(candidate, outcome, context, today=today)
    blended_level = (
        assessment.level.value if assessment else candidate.risk_level
    )

    record = AIAnalysisRecord(
        candidate_id=candidate_id,
        input_hash=input_hash,
        summary=outcome.analysis.summary,
        risk_level=blended_level,
        # The engine's explanation when it produced the band; the model's
        # prose is kept only when the two agree on the reasoning shape.
        risk_rationale=(assessment.rationale if assessment else outcome.analysis.risk_rationale),
        signals=[{"type": s.type.value, "evidence": s.evidence} for s in outcome.analysis.signals],
        next_action=outcome.analysis.next_action.value,
        recommended_follow_up=outcome.analysis.recommended_follow_up,
        provider=outcome.provider.value,
        model=outcome.model,
        prompt_version=pipeline.PROMPT_VERSION,
        status=outcome.status.value,
        latency_ms=outcome.latency_ms,
        tokens_in=outcome.tokens_in,
        tokens_out=outcome.tokens_out,
        raw_response=outcome.raw_response,
        error=outcome.error,
        model_confidence=outcome.analysis.risk_confidence,
        model_risk_level=outcome.analysis.risk_level.value,
        dropped_signals=len(outcome.dropped_signals),
    )
    session.add(record)
    # The stored confidence is the derived one; the row records the model's
    # self-report too, so the calibration gap stays measurable.
    record.risk_confidence = candidate.risk_confidence

    await session.commit()
    await session.refresh(record)

    logger.info(
        "analysis_stored",
        candidate_id=candidate_id,
        provider=outcome.provider.value,
        status=outcome.status.value,
        model_risk=outcome.analysis.risk_level.value,
        blended_risk=blended_level,
        agreed=outcome.analysis.risk_level.value == blended_level,
        signals=len(outcome.analysis.signals),
        dropped=len(outcome.dropped_signals),
        latency_ms=outcome.latency_ms,
    )
    return record, False


async def analyse_many(
    session: AsyncSession,
    candidate_ids: list[str],
    *,
    actor: Actor,
    today: date | None = None,
    force: bool = False,
) -> dict[str, int]:
    """Analyse a batch, tolerating individual failures.

    Sequential rather than concurrent: provider rate limits are the binding
    constraint, and a burst of parallel calls would trip them and make the
    whole batch slower. At production scale this becomes a queue with a
    controlled worker pool rather than a request-scoped loop.
    """
    today = today or date.today()
    stats = {"analysed": 0, "cached": 0, "failed": 0}

    for candidate_id in candidate_ids:
        try:
            record, from_cache = await analyse_candidate(
                session, candidate_id, actor=actor, today=today, force=force
            )
            if from_cache:
                stats["cached"] += 1
            elif record.status == AnalysisStatus.FAILED.value:
                stats["failed"] += 1
            else:
                stats["analysed"] += 1
        except Exception as exc:  # noqa: BLE001 - one bad row must not stop the batch
            logger.error("batch_analysis_failed", candidate_id=candidate_id, error=str(exc))
            stats["failed"] += 1
            await session.rollback()

    return stats


async def generate_message(
    session: AsyncSession,
    candidate_id: str,
    *,
    channel: InteractionChannel,
    actor: Actor,
    today: date | None = None,
) -> tuple[GeneratedMessage, list[str]]:
    """Draft a message for recruiter review.

    Saved as DRAFT. Nothing reaches the candidate without an explicit approval
    step - the human gate is the real defence against prompt injection, since
    injected text cannot approve itself.
    """
    today = today or date.today()
    _candidate, _context, snapshot = await _load_context_and_snapshot(
        session, candidate_id, today=today
    )

    provider = get_provider()
    try:
        draft, warnings, provider_name, model, latency_ms = await pipeline.draft_message(
            provider, snapshot, channel=channel.value
        )
    except ProviderUnavailable as exc:
        # No fallback here, deliberately. A templated message masquerading as a
        # personalised one is worse than telling the recruiter to write it.
        raise ProviderError(
            "Message generation is unavailable. Please write this message manually."
        ) from exc

    message = GeneratedMessage(
        candidate_id=candidate_id,
        channel=channel.value,
        subject=draft.subject,
        body=draft.body,
        tone=draft.tone,
        status=MessageStatus.DRAFT.value,
        provider=provider_name.value,
        model=model,
    )
    session.add(message)
    await session.commit()
    await session.refresh(message)

    logger.info(
        "message_drafted",
        candidate_id=candidate_id,
        channel=channel.value,
        provider=provider_name.value,
        latency_ms=latency_ms,
        warnings=len(warnings),
    )
    return message, warnings


async def approve_message(
    session: AsyncSession, message_id: str, *, actor: Actor, simulate_send: bool = True
) -> GeneratedMessage:
    """Approve a draft and optionally mark it sent.

    Sending is simulated (the brief permits this), but the approval gate is
    real: it is recorded, audited, and required before the status can advance.
    """
    message = await session.get(GeneratedMessage, message_id)
    if message is None:
        raise NotFoundError("Message not found.", details={"message_id": message_id})

    before = audit.snapshot_of(message, ["status", "approved_by", "sent_at"])

    message.status = (
        MessageStatus.SENT_SIMULATED if simulate_send else MessageStatus.APPROVED
    ).value
    message.approved_by = actor.id
    message.approved_at = utcnow()
    if simulate_send:
        message.sent_at = utcnow()

    await audit.record_change(
        session,
        actor=actor,
        entity=message,
        entity_type="generated_message",
        action=AuditAction.MESSAGE_SEND if simulate_send else AuditAction.MESSAGE_APPROVE,
        tracked_fields=["status", "approved_by", "sent_at"],
        before_snapshot=before,
    )
    await session.commit()
    await session.refresh(message)
    return message


async def list_messages(
    session: AsyncSession, candidate_id: str
) -> list[GeneratedMessage]:
    stmt = (
        select(GeneratedMessage)
        .where(GeneratedMessage.candidate_id == candidate_id)
        .order_by(GeneratedMessage.created_at.desc())
    )
    return list((await session.execute(stmt)).scalars().all())
