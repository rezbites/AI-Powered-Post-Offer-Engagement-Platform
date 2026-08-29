"""Shared test fixtures.

The domain layer takes `today` as an explicit parameter rather than reading the
clock, so every test below runs against a frozen date. Tests that depend on the
real clock fail at midnight, fail in other timezones, and fail differently in
CI than on a laptop.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.domain.context import CandidateContext, SignalView
from app.domain.enums import CandidateStatus, SignalType

# Fixed reference date for all domain tests.
TODAY = date(2026, 8, 29)


def days_ago(n: int) -> datetime:
    """A timezone-aware timestamp n days before TODAY."""
    return datetime.combine(TODAY - timedelta(days=n), datetime.min.time(), tzinfo=timezone.utc)


def make_context(
    *,
    candidate_id: str = "cand-1",
    name: str = "Test Candidate",
    status: CandidateStatus = CandidateStatus.ENGAGED,
    days_to_joining: int = 30,
    days_since_interaction: int | None = 1,
    unanswered_outbound: int = 0,
    total_interactions: int = 6,
    inbound_interactions: int = 3,
    stages_total: int = 6,
    stages_completed: int = 3,
    stages_overdue: int = 0,
    signals: list[tuple[SignalType, str]] | None = None,
    has_open_follow_up: bool = False,
) -> CandidateContext:
    """Build a context from human-meaningful parameters.

    Defaults describe a healthy, unremarkable candidate, so each test varies
    only the dimension it is actually about.
    """
    return CandidateContext(
        candidate_id=candidate_id,
        name=name,
        status=status,
        joining_date=TODAY + timedelta(days=days_to_joining),
        offer_date=TODAY - timedelta(days=30),
        last_interaction_at=(
            None if days_since_interaction is None else days_ago(days_since_interaction)
        ),
        unanswered_outbound=unanswered_outbound,
        total_interactions=total_interactions,
        inbound_interactions=inbound_interactions,
        stages_total=stages_total,
        stages_completed=stages_completed,
        stages_overdue=stages_overdue,
        signals=[SignalView(type=t, evidence=e) for t, e in (signals or [])],
        has_open_follow_up=has_open_follow_up,
    )


@pytest.fixture
def today() -> date:
    return TODAY
