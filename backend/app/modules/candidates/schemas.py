"""Pydantic contracts for the candidates API.

Request models validate at the boundary; response models control exactly what
leaves the process. They are separate classes on purpose - reusing one model
for both is how internal fields (password hashes, raw LLM responses) end up
serialised to clients by accident.
"""

from __future__ import annotations

import re
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.domain.enums import (
    CandidateStatus,
    NextAction,
    RiskLevel,
    RiskSource,
    SignalType,
    StageStatus,
)

_JOINING_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


# --------------------------------------------------------------------------
# Requests
# --------------------------------------------------------------------------
class CandidateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    phone: str | None = Field(default=None, max_length=32)
    role_title: str = Field(min_length=1, max_length=120)
    location: str = Field(min_length=1, max_length=120)
    offer_date: date
    joining_date: date
    recruiter_id: str
    status: CandidateStatus = CandidateStatus.OFFER_ACCEPTED
    notes: str | None = None

    @model_validator(mode="after")
    def _joining_after_offer(self) -> "CandidateCreate":
        # A joining date before the offer date is not a typo we should silently
        # accept: every risk and analytics calculation derives from this window.
        if self.joining_date < self.offer_date:
            raise ValueError("joining_date must be on or after offer_date")
        return self


class CandidateUpdate(BaseModel):
    """Partial update. Every field optional; only what is sent is changed.

    Risk fields are absent by design - risk moves only through the AI pipeline
    or the explicit override endpoint, both of which write an audit trail.
    """

    name: str | None = Field(default=None, min_length=1, max_length=120)
    phone: str | None = Field(default=None, max_length=32)
    role_title: str | None = Field(default=None, min_length=1, max_length=120)
    location: str | None = Field(default=None, min_length=1, max_length=120)
    joining_date: date | None = None
    recruiter_id: str | None = None
    status: CandidateStatus | None = None
    notes: str | None = None


class CandidateFilters(BaseModel):
    """Dashboard filter set, mirroring the filters the brief requires."""

    joining_month: str | None = Field(
        default=None, description="Calendar month of the joining date, as YYYY-MM."
    )
    recruiter_id: str | None = None
    role_title: str | None = None
    risk_level: RiskLevel | None = None
    status: CandidateStatus | None = None
    search: str | None = Field(default=None, description="Case-insensitive match on name or email.")
    joining_within_days: int | None = Field(
        default=None, ge=1, le=365, description="Only candidates joining within N days from today."
    )

    @field_validator("joining_month")
    @classmethod
    def _validate_month(cls, value: str | None) -> str | None:
        if value is not None and not _JOINING_MONTH_RE.match(value):
            raise ValueError("joining_month must be formatted as YYYY-MM")
        return value


# --------------------------------------------------------------------------
# Responses
# --------------------------------------------------------------------------
class SignalOut(BaseModel):
    """A detected concern together with the quote that evidences it.

    Evidence is non-optional: a signal without a supporting quote is exactly
    the kind of unfalsifiable claim that makes AI output untrustworthy.
    """

    type: SignalType
    evidence: str


class JourneyProgress(BaseModel):
    completed: int
    total: int
    current_stage: str | None = Field(default=None, description="Label of the next pending stage.")
    overdue_stages: int = 0


class RiskView(BaseModel):
    """Risk as the UI needs it: never a bare label.

    `factors` is what populates the "Why?" panel. `confidence` is reported
    separately from `level` and is a derived heuristic, not a calibrated
    probability - see docs/decisions.md.
    """

    level: RiskLevel
    confidence: float = Field(ge=0.0, le=1.0)
    source: RiskSource
    rationale: str = ""
    factors: list[str] = Field(default_factory=list)
    signals: list[SignalOut] = Field(default_factory=list)
    override_reason: str | None = None
    overridden_by: str | None = None
    overridden_at: datetime | None = None
    last_analyzed_at: datetime | None = None


class CandidateSummary(BaseModel):
    """Row shape for the dashboard table.

    Carries risk, the reason for it, and the recommended action inline, because
    the brief requires all three visible on the candidate list rather than one
    click away.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    email: EmailStr
    role_title: str
    location: str
    joining_date: date
    days_to_joining: int
    status: CandidateStatus
    recruiter_id: str
    recruiter_name: str | None = None
    last_interaction_at: datetime | None = None
    days_since_interaction: int | None = None
    risk: RiskView
    next_action: NextAction = NextAction.NO_ACTION
    next_action_label: str = ""
    why: list[str] = Field(default_factory=list)
    journey: JourneyProgress


class StageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str
    label: str
    sequence: int
    status: StageStatus
    due_date: date | None = None
    completed_at: datetime | None = None
    is_overdue: bool = False


class InteractionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    channel: str
    direction: str
    content: str
    occurred_at: datetime


class CandidateDetail(CandidateSummary):
    """Everything the candidate detail page renders."""

    phone: str | None = None
    offer_date: date
    notes: str | None = None
    ai_summary: str | None = None
    recommended_follow_up: str | None = None
    analysis_provider: str | None = Field(
        default=None,
        description="Which provider produced the current analysis: 'gemini' or 'mock'.",
    )
    analysis_model: str | None = None
    stages: list[StageOut] = Field(default_factory=list)
    interactions: list[InteractionOut] = Field(default_factory=list)
