"""Analytics response contracts.

Every metric the brief names, plus the definitions used to compute them.
Several are genuinely ambiguous - "offer-to-join conversion" has at least three
defensible denominators - so each is documented on the field rather than left
for a reader to reverse-engineer from SQL.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from app.domain.enums import RiskLevel


class PipelineTotals(BaseModel):
    total_offered: int = Field(description="Every candidate ever recorded, including terminal ones.")
    active: int = Field(description="Offer accepted but not yet joined or dropped out.")
    joined: int
    dropped_out: int
    pending_outcome: int = Field(
        description="Active candidates whose joining date has not yet passed."
    )


class ConversionMetrics(BaseModel):
    """Offer-to-join conversion.

    The denominator is *resolved* candidates only - those who have actually
    joined or dropped out. Including still-pending candidates would understate
    conversion badly, since someone joining next month is not a failure yet.

    `resolved_rate` is therefore the honest headline number, and
    `pending_outcome` is reported alongside so the sample size is visible.
    """

    joined: int
    dropped_out: int
    resolved: int = Field(description="joined + dropped_out; the conversion denominator.")
    resolved_rate: float | None = Field(
        description=(
            "joined / resolved as a percentage, or null when nothing has resolved. "
            "Null and 0.0 mean very different things and must render differently."
        )
    )
    pending_outcome: int = Field(description="Not yet resolved; excluded from the rate.")


class JoiningWindow(BaseModel):
    """Counts for the 7/15/30-day windows the brief asks for.

    Cumulative and forward-looking: the 30-day figure includes the 7-day one,
    and none of them count joining dates already in the past.
    """

    next_7_days: int
    next_15_days: int
    next_30_days: int
    overdue: int = Field(
        description="Joining date has passed but no outcome recorded. A data-quality signal."
    )


class RiskBreakdown(BaseModel):
    high: int
    medium: int
    low: int
    high_risk_joining_within_7_days: int = Field(
        description="The intersection that matters most operationally."
    )
    human_overridden: int = Field(
        description="Candidates whose risk a recruiter has overridden."
    )
    ai_assessed: int = Field(description="Risk currently sourced from the AI pipeline.")


class EngagementMetrics(BaseModel):
    """Engagement frequency.

    The brief asks for "average engagement frequency" without defining it. The
    definition used here: interactions per candidate per week, measured over
    each candidate's own offer-to-now window, then averaged across active
    candidates. Per-week normalisation matters because a candidate offered
    three months ago would otherwise look far more engaged than one offered
    last week.
    """

    avg_interactions_per_candidate: float
    avg_interactions_per_week: float
    candidates_never_contacted: int
    candidates_silent_over_7_days: int
    total_interactions: int


class StageMetrics(BaseModel):
    key: str
    label: str
    sequence: int
    completed: int
    pending: int
    overdue: int = Field(description="Pending and past the stage SLA.")
    completion_rate: float
    not_yet_reached: int = Field(
        description=(
            "Completed the previous stage but not this one. Mostly candidates "
            "still in progress - NOT people who withdrew. Actual withdrawals "
            "are the dropped_out status, counted in conversion."
        )
    )


class RecruiterMetrics(BaseModel):
    """Per-recruiter figures.

    Worth stating wherever these are displayed: with a handful of resolved
    candidates each, the rates are extremely noisy - one dropout swings a
    percentage by tens of points. A conversation starter, not a performance
    metric.
    """

    recruiter_id: str
    recruiter_name: str
    total_candidates: int
    joined: int
    dropped_out: int
    resolved: int
    conversion_rate: float | None = Field(
        description="joined / resolved as a percentage, or null when nothing has resolved."
    )
    high_risk_active: int
    avg_days_since_interaction: float | None = Field(
        default=None, description="How stale their engagement is on average."
    )


class AIOperations(BaseModel):
    """LLM observability, read straight from the analyses ledger.

    Cost, latency and failure rate are answerable in SQL because every analysis
    stores its own telemetry - no separate monitoring stack required at this
    scale.
    """

    total_analyses: int
    provider: str = Field(description="'mock' in Demo Mode, 'gemini' in Live Mode.")
    mode: str
    valid: int
    repaired: int = Field(description="Recovered by the repair pass after failing validation.")
    failed: int = Field(description="Fell back to a deterministic assessment.")
    dropped_signals: int = Field(
        description="Signals discarded because the quote was absent from the candidate's messages."
    )
    model_engine_disagreements: int = Field(
        description="Times the model's proposed band differed from the blended band."
    )
    avg_latency_ms: float | None = None
    total_tokens_in: int = 0
    total_tokens_out: int = 0


class AnalyticsOverview(BaseModel):
    """Everything the analytics dashboard renders, in one response.

    Deliberately one endpoint rather than eight: the dashboard shows these
    together, and eight round trips would make it slower and allow the tiles to
    disagree with each other if data changed mid-load.
    """

    generated_for: date
    totals: PipelineTotals
    conversion: ConversionMetrics
    joining_windows: JoiningWindow
    risk: RiskBreakdown
    engagement: EngagementMetrics
    stages: list[StageMetrics]
    recruiters: list[RecruiterMetrics]
    ai_operations: AIOperations
