"""Attention queue assembly, and deterministic risk recomputation.

Two closely related jobs:

* build the ranked "who needs me today?" queue that the dashboard leads with;
* recompute stored risk from the deterministic engine.

Recomputation exists because the `candidates.risk_level` column is what the
dashboard filters and sorts on, so it must be indexed and therefore stored -
but a stored value drifts as time passes and interactions arrive. This keeps it
honest without making every list query recompute risk for the whole table.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.logging import get_logger
from app.db.models import AIAnalysisRecord, Candidate
from app.domain import attention, risk
from app.domain.context import CandidateContext
from app.domain.enums import CandidateStatus, RiskLevel, RiskSource
from app.modules.candidates import repository as repo
from app.modules.candidates import service as candidate_service

logger = get_logger(__name__)

# The queue only ever concerns candidates who have not yet started. Bounding
# the scan by joining date keeps it proportional to *active* pipeline rather
# than total history - at a million candidates the alternative is a full scan
# on every dashboard load.
QUEUE_HORIZON_DAYS = 120


async def _active_candidates(session: AsyncSession, *, today: date) -> list[Candidate]:
    """Non-terminal candidates within the planning horizon."""
    horizon = today + timedelta(days=QUEUE_HORIZON_DAYS)
    stmt = (
        select(Candidate)
        .where(
            Candidate.status.notin_(
                [CandidateStatus.JOINED.value, CandidateStatus.DROPPED_OUT.value]
            ),
            Candidate.joining_date <= horizon,
        )
        .options(selectinload(Candidate.recruiter))
        .order_by(Candidate.joining_date.asc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def _contexts_for(
    session: AsyncSession, candidates: list[Candidate], *, today: date
) -> dict[str, CandidateContext]:
    """Build pure contexts for a set of candidates, batching every lookup."""
    if not candidates:
        return {}

    ids = [c.id for c in candidates]
    stage_stats = await repo.stage_progress_for(session, ids, today=today)
    analyses = await repo.latest_analyses_for(session, ids)
    unanswered = await repo.unanswered_outbound_counts(session, ids)
    totals = await repo.interaction_counts(session, ids)
    open_follow_ups = await repo.open_follow_up_ids(session, ids)

    return {
        c.id: candidate_service.build_context(
            c,
            stage_stats=stage_stats.get(c.id, {}),
            unanswered_outbound=unanswered.get(c.id, 0),
            interaction_totals=totals.get(c.id, (0, 0)),
            analysis=analyses.get(c.id),
            has_open_follow_up=c.id in open_follow_ups,
        )
        for c in candidates
    }


async def build_attention_queue(
    session: AsyncSession, *, today: date, limit: int = 10, recruiter_id: str | None = None
) -> list[tuple[attention.AttentionItem, Candidate, AIAnalysisRecord | None]]:
    """The ranked queue, paired with each candidate row and its latest analysis.

    The analysis is returned explicitly rather than left for the caller to
    read off `candidate.analyses`: that relationship is lazy, and touching it
    from async code raises MissingGreenlet. Passing it through also reuses the
    batched lookup instead of issuing a query per row.
    """
    candidates = await _active_candidates(session, today=today)
    if recruiter_id:
        candidates = [c for c in candidates if c.recruiter_id == recruiter_id]

    contexts = await _contexts_for(session, candidates, today=today)
    analyses = await repo.latest_analyses_for(session, [c.id for c in candidates])
    by_id = {c.id: c for c in candidates}

    # Rank against the *stored* risk level, so a recruiter's override moves the
    # candidate in the queue. Ignoring overrides here would mean the queue kept
    # arguing with a decision a human already made.
    entries = [
        (contexts[c.id], RiskLevel(c.risk_level)) for c in candidates if c.id in contexts
    ]
    ranked = attention.build_queue(entries, today=today, limit=limit)
    return [
        (item, by_id[item.candidate_id], analyses.get(item.candidate_id))
        for item in ranked
    ]


async def recompute_risk(
    session: AsyncSession, *, today: date, commit: bool = True
) -> dict[str, int]:
    """Refresh stored risk for every active candidate from the rules engine.

    Human overrides are never touched - that is the whole point of recording
    `risk_source`. Candidates whose risk came from the AI pipeline are also left
    alone here, because this deterministic pass has no access to the semantic
    signals that informed them; overwriting would silently discard evidence.

    So this only (re)writes rows whose risk is rule-derived, which is exactly
    the set that goes stale purely through the passage of time.
    """
    candidates = await _active_candidates(session, today=today)
    contexts = await _contexts_for(session, candidates, today=today)

    updated = 0
    skipped = 0

    for candidate in candidates:
        if candidate.risk_source != RiskSource.RULE.value:
            skipped += 1
            continue

        ctx = contexts.get(candidate.id)
        if ctx is None:
            continue

        assessment = risk.assess(ctx, today=today)
        if assessment is None:  # terminal; excluded by the query, belt and braces
            continue

        candidate.risk_level = assessment.level.value
        candidate.risk_confidence = assessment.confidence
        candidate.risk_source = RiskSource.RULE.value
        updated += 1

    if commit:
        await session.commit()

    logger.info("risk_recomputed", updated=updated, skipped=skipped, total=len(candidates))
    return {"updated": updated, "skipped": skipped, "total": len(candidates)}
