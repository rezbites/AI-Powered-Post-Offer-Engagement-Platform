"""Analytics assembly."""

from __future__ import annotations

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.modules.analytics import repository as repo
from app.modules.analytics.schemas import (
    AIOperations,
    AnalyticsOverview,
    ConversionMetrics,
    EngagementMetrics,
    JoiningWindow,
    PipelineTotals,
    RecruiterMetrics,
    RiskBreakdown,
    StageMetrics,
)

settings = get_settings()


async def build_overview(session: AsyncSession, *, today: date | None = None) -> AnalyticsOverview:
    """Compute every dashboard metric.

    The queries are independent and could run concurrently, but they are
    executed sequentially on purpose: a single AsyncSession is not safe for
    concurrent use, and opening several sessions to save a few milliseconds on
    a page that loads once would trade correctness for nothing.
    """
    today = today or date.today()

    totals = await repo.pipeline_totals(session, today=today)
    windows = await repo.joining_windows(session, today=today)
    risk = await repo.risk_breakdown(session, today=today)
    engagement = await repo.engagement_metrics(session, today=today)
    staleness = await repo.engagement_staleness(session, today=today)
    stages = await repo.stage_funnel(session, today=today)
    recruiters = await repo.recruiter_performance(session, today=today)
    ai_ops = await repo.ai_operations(session)

    joined = totals["joined"]
    dropped = totals["dropped"]
    resolved = joined + dropped

    return AnalyticsOverview(
        generated_for=today,
        totals=PipelineTotals(
            total_offered=totals["total"],
            active=totals["active"],
            joined=joined,
            dropped_out=dropped,
            pending_outcome=totals["pending"],
        ),
        conversion=ConversionMetrics(
            joined=joined,
            dropped_out=dropped,
            resolved=resolved,
            # None rather than 0.0 when nothing has resolved - see
            # repository.conversion_rate for why the distinction matters.
            resolved_rate=repo.conversion_rate(joined, resolved),
            pending_outcome=totals["pending"],
        ),
        joining_windows=JoiningWindow(**windows),
        risk=RiskBreakdown(**risk),
        engagement=EngagementMetrics(
            avg_interactions_per_candidate=engagement["avg_interactions_per_candidate"],
            avg_interactions_per_week=engagement["avg_interactions_per_week"],
            total_interactions=int(engagement["total_interactions"]),
            **staleness,
        ),
        stages=[StageMetrics(**s) for s in stages],
        recruiters=[RecruiterMetrics(**r) for r in recruiters],
        ai_operations=AIOperations(
            provider=settings.resolved_provider,
            mode="demo" if settings.is_demo_mode else "live",
            **ai_ops,
        ),
    )
