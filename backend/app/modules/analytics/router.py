"""Analytics endpoints."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter

from app.core.deps import SessionDep
from app.modules.analytics import service
from app.modules.analytics.schemas import AnalyticsOverview

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/overview", response_model=AnalyticsOverview, summary="All dashboard metrics")
async def overview(session: SessionDep) -> AnalyticsOverview:
    """Every metric the brief requires, in one response.

    One endpoint rather than eight: the dashboard renders these together, so
    separate round trips would be slower *and* would let tiles disagree with
    each other if the data changed mid-load.

    Metric definitions the brief leaves open are documented on the response
    schema - notably that offer-to-join conversion uses resolved candidates as
    its denominator, and that engagement frequency is normalised per week.
    """
    return await service.build_overview(session, today=date.today())
