"""Analytics aggregate queries.

Every metric is a grouped aggregate pushed into the database rather than
computed by loading rows into Python. That matters even at 60 candidates,
because it is the difference between a dashboard that stays fast at 60,000 and
one that has to be rewritten.

## What changes at a million candidates

These queries scan the candidate table. Indexed, but still a scan. At that
scale the shape changes rather than the SQL being tuned:

* nightly rollup tables (or materialised views) holding pre-aggregated counts
  per day, per recruiter, per stage;
* the dashboard reads the rollup, so its cost is independent of history size;
* only the current-day slice is computed live.

That is written up in the README rather than built, because building it now
would add refresh scheduling and staleness handling for a dataset that fits in
a single page of results.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import Date, Float, and_, case, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AIAnalysisRecord,
    Candidate,
    CandidateStage,
    Interaction,
    JourneyStage,
    Recruiter,
)
from app.domain.enums import (
    AnalysisStatus,
    CandidateStatus,
    RiskLevel,
    RiskSource,
    StageStatus,
)

TERMINAL = [CandidateStatus.JOINED.value, CandidateStatus.DROPPED_OUT.value]


def conversion_rate(joined: int, resolved: int) -> float | None:
    """Offer-to-join percentage, or None when nothing has resolved yet.

    Returning 0.0 for a recruiter with no resolved candidates would read as
    "lost every one of them", which is the opposite of the truth. In an HR
    tool where these figures shape how people are judged, "no data yet" and
    "zero percent" must not render identically.
    """
    if resolved <= 0:
        return None
    return round(joined / resolved * 100, 1)


def _count_if(condition) -> object:
    """SUM(CASE WHEN ... THEN 1 ELSE 0 END).

    Used in preference to FILTER (WHERE ...) which, while cleaner, is not
    supported by SQLite and would break the no-Docker fallback path.
    """
    return func.coalesce(func.sum(case((condition, 1), else_=0)), 0)


async def pipeline_totals(session: AsyncSession, *, today: date) -> dict[str, int]:
    """Headline counts in a single pass over the candidate table."""
    stmt = select(
        func.count(Candidate.id).label("total"),
        _count_if(Candidate.status == CandidateStatus.JOINED.value).label("joined"),
        _count_if(Candidate.status == CandidateStatus.DROPPED_OUT.value).label("dropped"),
        _count_if(Candidate.status.notin_(TERMINAL)).label("active"),
        _count_if(
            and_(Candidate.status.notin_(TERMINAL), Candidate.joining_date >= today)
        ).label("pending"),
    )
    row = (await session.execute(stmt)).one()
    return {
        "total": int(row.total or 0),
        "joined": int(row.joined or 0),
        "dropped": int(row.dropped or 0),
        "active": int(row.active or 0),
        "pending": int(row.pending or 0),
    }


async def joining_windows(session: AsyncSession, *, today: date) -> dict[str, int]:
    """Forward-looking 7/15/30-day counts.

    Every window requires `joining_date >= today`: without that, candidates who
    joined last week would inflate "joining in the next 7 days", which is the
    single easiest way to make this dashboard lie.
    """

    def window(days: int):
        return and_(
            Candidate.status.notin_(TERMINAL),
            Candidate.joining_date >= today,
            Candidate.joining_date <= today + timedelta(days=days),
        )

    stmt = select(
        _count_if(window(7)).label("d7"),
        _count_if(window(15)).label("d15"),
        _count_if(window(30)).label("d30"),
        _count_if(
            and_(Candidate.status.notin_(TERMINAL), Candidate.joining_date < today)
        ).label("overdue"),
    )
    row = (await session.execute(stmt)).one()
    return {
        "next_7_days": int(row.d7 or 0),
        "next_15_days": int(row.d15 or 0),
        "next_30_days": int(row.d30 or 0),
        "overdue": int(row.overdue or 0),
    }


async def risk_breakdown(session: AsyncSession, *, today: date) -> dict[str, int]:
    """Risk distribution across active candidates only.

    Terminal candidates keep their last recorded band as history, but counting
    them here would mix "at risk of not joining" with "already joined", which
    are not the same population.
    """
    active = Candidate.status.notin_(TERMINAL)

    stmt = select(
        _count_if(and_(active, Candidate.risk_level == RiskLevel.HIGH.value)).label("high"),
        _count_if(and_(active, Candidate.risk_level == RiskLevel.MEDIUM.value)).label("medium"),
        _count_if(and_(active, Candidate.risk_level == RiskLevel.LOW.value)).label("low"),
        _count_if(
            and_(
                active,
                Candidate.risk_level == RiskLevel.HIGH.value,
                Candidate.joining_date >= today,
                Candidate.joining_date <= today + timedelta(days=7),
            )
        ).label("high_soon"),
        _count_if(and_(active, Candidate.risk_source == RiskSource.HUMAN.value)).label("human"),
        _count_if(and_(active, Candidate.risk_source == RiskSource.AI.value)).label("ai"),
    )
    row = (await session.execute(stmt)).one()
    return {
        "high": int(row.high or 0),
        "medium": int(row.medium or 0),
        "low": int(row.low or 0),
        "high_risk_joining_within_7_days": int(row.high_soon or 0),
        "human_overridden": int(row.human or 0),
        "ai_assessed": int(row.ai or 0),
    }


async def engagement_metrics(session: AsyncSession, *, today: date) -> dict[str, float]:
    """Interaction volume and staleness.

    Frequency is normalised per week over each candidate's own offer-to-now
    window. Without that normalisation, someone offered three months ago looks
    far more engaged than someone offered last week purely because they have
    had more time to accumulate messages.
    """
    active = Candidate.status.notin_(TERMINAL)

    # Per-candidate interaction counts, left-joined so never-contacted
    # candidates appear with zero rather than vanishing from the average.
    per_candidate = (
        select(
            Candidate.id.label("cid"),
            Candidate.offer_date.label("offer_date"),
            func.count(Interaction.id).label("n"),
        )
        .select_from(Candidate)
        .outerjoin(Interaction, Interaction.candidate_id == Candidate.id)
        .where(active)
        .group_by(Candidate.id, Candidate.offer_date)
        .subquery()
    )

    counts = (await session.execute(select(per_candidate.c.n, per_candidate.c.offer_date))).all()

    if not counts:
        return {
            "avg_interactions_per_candidate": 0.0,
            "avg_interactions_per_week": 0.0,
            "total_interactions": 0,
        }

    total = sum(int(n) for n, _ in counts)
    per_week: list[float] = []
    for n, offer_date in counts:
        # Floor of one week: a candidate offered yesterday must not produce a
        # divide-by-zero or an absurd per-week rate.
        weeks = max((today - offer_date).days / 7.0, 1.0)
        per_week.append(int(n) / weeks)

    return {
        "avg_interactions_per_candidate": round(total / len(counts), 2),
        "avg_interactions_per_week": round(sum(per_week) / len(per_week), 2),
        "total_interactions": total,
    }


async def engagement_staleness(session: AsyncSession, *, today: date) -> dict[str, int]:
    """How many active candidates have gone quiet."""
    active = Candidate.status.notin_(TERMINAL)
    cutoff = today - timedelta(days=7)

    stmt = select(
        _count_if(and_(active, Candidate.last_interaction_at.is_(None))).label("never"),
        _count_if(
            and_(
                active,
                Candidate.last_interaction_at.is_not(None),
                cast(Candidate.last_interaction_at, Date) < cutoff,
            )
        ).label("silent"),
    )
    row = (await session.execute(stmt)).one()
    return {
        "candidates_never_contacted": int(row.never or 0),
        "candidates_silent_over_7_days": int(row.silent or 0),
    }


async def stage_funnel(session: AsyncSession, *, today: date) -> list[dict[str, object]]:
    """Completion and drop-off per journey stage.

    This is the query that justifies modelling journey progress as rows rather
    than a `current_stage` column: drop-off needs per-stage completion counts,
    which a single column cannot provide.

    Drop-off between consecutive stages is computed in Python from the ordered
    results - expressing a window function over the sequence would be less
    readable for no measurable gain at this cardinality (one row per stage).
    """
    stmt = (
        select(
            JourneyStage.key,
            JourneyStage.label,
            JourneyStage.sequence,
            func.count(CandidateStage.id).label("total"),
            _count_if(CandidateStage.status == StageStatus.COMPLETED.value).label("completed"),
            _count_if(CandidateStage.status == StageStatus.PENDING.value).label("pending"),
            _count_if(
                and_(
                    CandidateStage.status == StageStatus.PENDING.value,
                    CandidateStage.due_date.is_not(None),
                    CandidateStage.due_date < today,
                )
            ).label("overdue"),
        )
        .select_from(JourneyStage)
        .outerjoin(CandidateStage, CandidateStage.stage_id == JourneyStage.id)
        .group_by(JourneyStage.key, JourneyStage.label, JourneyStage.sequence)
        .order_by(JourneyStage.sequence)
    )

    rows = (await session.execute(stmt)).all()

    results: list[dict[str, object]] = []
    previous_completed: int | None = None

    for row in rows:
        total = int(row.total or 0)
        completed = int(row.completed or 0)
        drop_off = 0 if previous_completed is None else max(0, previous_completed - completed)

        results.append(
            {
                "key": row.key,
                "label": row.label,
                "sequence": int(row.sequence),
                "completed": completed,
                "pending": int(row.pending or 0),
                "overdue": int(row.overdue or 0),
                "completion_rate": round(completed / total * 100, 1) if total else 0.0,
                "drop_off_from_previous": drop_off,
            }
        )
        previous_completed = completed

    return results


async def recruiter_performance(session: AsyncSession, *, today: date) -> list[dict[str, object]]:
    """Per-recruiter conversion and workload.

    A caveat worth stating wherever this is displayed: with a handful of
    resolved candidates each, these rates are extremely noisy. One dropout
    moves a recruiter's percentage by double digits. Useful as a conversation
    starter, not as a performance metric.
    """
    stmt = (
        select(
            Recruiter.id,
            Recruiter.name,
            func.count(Candidate.id).label("total"),
            _count_if(Candidate.status == CandidateStatus.JOINED.value).label("joined"),
            _count_if(Candidate.status == CandidateStatus.DROPPED_OUT.value).label("dropped"),
            _count_if(
                and_(
                    Candidate.status.notin_(TERMINAL),
                    Candidate.risk_level == RiskLevel.HIGH.value,
                )
            ).label("high_risk"),
        )
        .select_from(Recruiter)
        .outerjoin(Candidate, Candidate.recruiter_id == Recruiter.id)
        .group_by(Recruiter.id, Recruiter.name)
        .order_by(Recruiter.name)
    )

    staleness = await recruiter_staleness(session, today=today)

    results: list[dict[str, object]] = []
    for row in (await session.execute(stmt)).all():
        joined = int(row.joined or 0)
        dropped = int(row.dropped or 0)
        resolved = joined + dropped

        results.append(
            {
                "recruiter_id": row.id,
                "recruiter_name": row.name,
                "total_candidates": int(row.total or 0),
                "joined": joined,
                "dropped_out": dropped,
                "resolved": resolved,
                "conversion_rate": conversion_rate(joined, resolved),
                "high_risk_active": int(row.high_risk or 0),
                "avg_days_since_interaction": staleness.get(row.id),
            }
        )
    return results


async def recruiter_staleness(
    session: AsyncSession, *, today: date
) -> dict[str, float | None]:
    """Average days since last contact, per recruiter, over active candidates.

    Computed in Python rather than SQL because date subtraction differs between
    Postgres and SQLite, and keeping the query dialect-neutral preserves the
    no-Docker fallback. The result set is one row per active candidate, so the
    transfer cost is trivial.

    Never-contacted candidates are excluded rather than counted as zero: they
    are a separate, worse problem, already reported by `engagement_staleness`.
    Folding them in as "0 days since contact" would make a neglectful recruiter
    look attentive.
    """
    stmt = select(Candidate.recruiter_id, Candidate.last_interaction_at).where(
        Candidate.status.notin_(TERMINAL)
    )

    buckets: dict[str, list[int]] = {}
    for recruiter_id, last_at in (await session.execute(stmt)).all():
        if last_at is None:
            continue
        buckets.setdefault(recruiter_id, []).append((today - last_at.date()).days)

    return {
        recruiter_id: round(sum(days) / len(days), 1)
        for recruiter_id, days in buckets.items()
        if days
    }


async def ai_operations(session: AsyncSession) -> dict[str, object]:
    """LLM cost, latency and failure rate from the analyses ledger."""
    stmt = select(
        func.count(AIAnalysisRecord.id).label("total"),
        _count_if(AIAnalysisRecord.status == AnalysisStatus.VALID.value).label("valid"),
        _count_if(AIAnalysisRecord.status == AnalysisStatus.REPAIRED.value).label("repaired"),
        _count_if(AIAnalysisRecord.status == AnalysisStatus.FAILED.value).label("failed"),
        func.coalesce(func.sum(AIAnalysisRecord.dropped_signals), 0).label("dropped"),
        _count_if(
            and_(
                AIAnalysisRecord.model_risk_level.is_not(None),
                AIAnalysisRecord.model_risk_level != AIAnalysisRecord.risk_level,
            )
        ).label("disagreements"),
        func.avg(cast(AIAnalysisRecord.latency_ms, Float)).label("avg_latency"),
        func.coalesce(func.sum(AIAnalysisRecord.tokens_in), 0).label("tokens_in"),
        func.coalesce(func.sum(AIAnalysisRecord.tokens_out), 0).label("tokens_out"),
    )
    row = (await session.execute(stmt)).one()

    return {
        "total_analyses": int(row.total or 0),
        "valid": int(row.valid or 0),
        "repaired": int(row.repaired or 0),
        "failed": int(row.failed or 0),
        "dropped_signals": int(row.dropped or 0),
        "model_engine_disagreements": int(row.disagreements or 0),
        # `is not None`, not a truthiness check: a genuine average of 0.0
        # (the mock provider returns in under a millisecond) is a real
        # measurement, and reporting it as null would read as "no data".
        "avg_latency_ms": (
            round(float(row.avg_latency), 2) if row.avg_latency is not None else None
        ),
        "total_tokens_in": int(row.tokens_in or 0),
        "total_tokens_out": int(row.tokens_out or 0),
    }
