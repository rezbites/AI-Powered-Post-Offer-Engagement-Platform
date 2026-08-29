"""SQLAlchemy ORM models.

Schema notes that are easy to miss:

* Enums are VARCHAR + a Python-side enum, not native database enum types.
  Adding a value to a native PG enum requires a migration and a table lock;
  here it is a code change validated by Pydantic at the boundary. It also
  keeps the SQLite fallback usable.

* Journey progress lives in `candidate_stages` rows, not a `current_stage`
  column on the candidate. Stage drop-off analytics and "which steps are
  pending" both need per-stage timestamps, which a single column cannot give.

* AI output is stored, not recomputed per request. Dashboards read it on every
  render, recruiters override it, and analytics aggregate it - all three need
  a durable, attributable row.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.domain.enums import (
    AnalysisStatus,
    AutomationRunStatus,
    CandidateStatus,
    FollowUpStatus,
    InteractionChannel,
    InteractionDirection,
    MessageStatus,
    NextAction,
    ProviderName,
    RiskLevel,
    RiskSource,
    StageStatus,
    UserRole,
)


class Recruiter(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A recruiter or HR admin. Also the actor referenced by the audit log."""

    __tablename__ = "recruiters"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default=UserRole.RECRUITER.value)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    candidates: Mapped[list["Candidate"]] = relationship(
        back_populates="recruiter", foreign_keys="Candidate.recruiter_id"
    )


class JourneyTemplate(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A named engagement journey.

    Stages are rows rather than a hardcoded list so a journey can be
    reconfigured without a deployment - different roles and locations
    legitimately need different pre-joining steps.
    """

    __tablename__ = "journey_templates"

    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    stages: Mapped[list["JourneyStage"]] = relationship(
        back_populates="template",
        cascade="all, delete-orphan",
        order_by="JourneyStage.sequence",
    )


class JourneyStage(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One step in a journey, e.g. Documentation.

    `sla_days` is days-from-offer by which the stage should be complete. It is
    what turns a passive checklist into something that can raise an alert, and
    it drives both the `stage_overdue` automation rule and the deterministic
    half of risk scoring.
    """

    __tablename__ = "journey_stages"
    __table_args__ = (
        UniqueConstraint("template_id", "key", name="uq_journey_stage_template_key"),
        UniqueConstraint("template_id", "sequence", name="uq_journey_stage_template_sequence"),
    )

    template_id: Mapped[str] = mapped_column(
        ForeignKey("journey_templates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    key: Mapped[str] = mapped_column(String(60), nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    sla_days: Mapped[int] = mapped_column(Integer, nullable=False, default=7)

    template: Mapped[JourneyTemplate] = relationship(back_populates="stages")


class Candidate(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A candidate between offer acceptance and joining - the central entity.

    Risk fields are denormalised onto this row (rather than always joined from
    `ai_analyses`) because the dashboard filters and sorts by them across the
    whole population. Keeping them here turns the primary list query into a
    single indexed scan instead of a correlated subquery per row.
    """

    __tablename__ = "candidates"
    __table_args__ = (
        # Composite index for the dominant dashboard access pattern:
        # a recruiter's own candidates, soonest joiners first.
        Index("ix_candidates_recruiter_joining", "recruiter_id", "joining_date"),
        Index("ix_candidates_status_risk", "status", "risk_level"),
    )

    # --- Identity -----------------------------------------------------------
    name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # --- Offer --------------------------------------------------------------
    role_title: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    location: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    offer_date: Mapped[date] = mapped_column(Date, nullable=False)
    joining_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    recruiter_id: Mapped[str] = mapped_column(
        ForeignKey("recruiters.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    # --- Engagement ---------------------------------------------------------
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=CandidateStatus.OFFER_ACCEPTED.value, index=True
    )
    journey_template_id: Mapped[str | None] = mapped_column(
        ForeignKey("journey_templates.id", ondelete="SET NULL"), nullable=True
    )
    # Maintained on every interaction write so the silent-candidate rule and
    # the attention queue never have to aggregate the interactions table.
    last_interaction_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    # --- Risk ---------------------------------------------------------------
    risk_level: Mapped[str] = mapped_column(String(10), nullable=False, default=RiskLevel.LOW.value, index=True)
    # Deliberately separate from risk_level: a HIGH band held with weak evidence
    # is a different thing to a HIGH band held with strong evidence, and a
    # recruiter should be able to tell them apart.
    risk_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    risk_source: Mapped[str] = mapped_column(String(10), nullable=False, default=RiskSource.RULE.value)
    risk_override_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    risk_overridden_by: Mapped[str | None] = mapped_column(
        ForeignKey("recruiters.id", ondelete="SET NULL"), nullable=True
    )
    risk_overridden_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- Relationships ------------------------------------------------------
    recruiter: Mapped[Recruiter] = relationship(back_populates="candidates", foreign_keys=[recruiter_id])
    interactions: Mapped[list["Interaction"]] = relationship(
        back_populates="candidate", cascade="all, delete-orphan", order_by="Interaction.occurred_at.desc()"
    )
    stages: Mapped[list["CandidateStage"]] = relationship(
        back_populates="candidate", cascade="all, delete-orphan"
    )
    analyses: Mapped[list["AIAnalysisRecord"]] = relationship(
        back_populates="candidate", cascade="all, delete-orphan", order_by="AIAnalysisRecord.created_at.desc()"
    )
    messages: Mapped[list["GeneratedMessage"]] = relationship(
        back_populates="candidate", cascade="all, delete-orphan"
    )
    follow_ups: Mapped[list["FollowUpAction"]] = relationship(
        back_populates="candidate", cascade="all, delete-orphan"
    )


class CandidateStage(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Per-candidate progress through one journey stage.

    A row exists for every stage from the moment the candidate is created, so
    pending is a first-class state rather than an absence of data. That is what
    makes stage drop-off measurable.
    """

    __tablename__ = "candidate_stages"
    __table_args__ = (
        UniqueConstraint("candidate_id", "stage_id", name="uq_candidate_stage"),
        Index("ix_candidate_stages_candidate_status", "candidate_id", "status"),
    )

    candidate_id: Mapped[str] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stage_id: Mapped[str] = mapped_column(ForeignKey("journey_stages.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=StageStatus.PENDING.value)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_by: Mapped[str | None] = mapped_column(
        ForeignKey("recruiters.id", ondelete="SET NULL"), nullable=True
    )
    # Denormalised from the stage SLA at assignment time so historical rows keep
    # their original deadline even if the template is later reconfigured.
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    candidate: Mapped[Candidate] = relationship(back_populates="stages")
    stage: Mapped[JourneyStage] = relationship()


class Interaction(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One communication event.

    Inbound rows are the raw material for risk detection; outbound rows
    establish whether the recruiter is engaging at all.
    """

    __tablename__ = "interactions"
    __table_args__ = (Index("ix_interactions_candidate_occurred", "candidate_id", "occurred_at"),)

    candidate_id: Mapped[str] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    channel: Mapped[str] = mapped_column(String(20), nullable=False, default=InteractionChannel.EMAIL.value)
    direction: Mapped[str] = mapped_column(
        String(10), nullable=False, default=InteractionDirection.OUTBOUND.value
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    created_by: Mapped[str | None] = mapped_column(
        ForeignKey("recruiters.id", ondelete="SET NULL"), nullable=True
    )

    candidate: Mapped[Candidate] = relationship(back_populates="interactions")


class AIAnalysisRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A stored AI analysis - and simultaneously the LLM observability ledger.

    Columns mirror the `AIAnalysis` Pydantic schema field-for-field, so what the
    model was asked to produce and what the database holds cannot drift.

    The telemetry columns (provider, model, prompt_version, tokens, latency,
    status) make cost, latency and failure rate answerable with plain SQL
    instead of requiring a separate observability stack.
    """

    __tablename__ = "ai_analyses"
    __table_args__ = (
        # The cache lookup: identical candidate state reuses the existing
        # analysis instead of paying for a model call on every render.
        Index("ix_ai_analyses_candidate_hash", "candidate_id", "input_hash"),
        Index("ix_ai_analyses_created", "created_at"),
    )

    candidate_id: Mapped[str] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # SHA-256 over the canonical input snapshot. Any change to candidate facts
    # or interactions produces a new hash and therefore a fresh analysis.
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # --- Mirrors the AIAnalysis schema -------------------------------------
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(10), nullable=False, default=RiskLevel.LOW.value)
    risk_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    risk_rationale: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # List of {"type": SignalType, "evidence": "verbatim quote"}.
    signals: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    next_action: Mapped[str] = mapped_column(String(40), nullable=False, default=NextAction.NO_ACTION.value)
    recommended_follow_up: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # --- Telemetry / provenance --------------------------------------------
    provider: Mapped[str] = mapped_column(String(20), nullable=False, default=ProviderName.MOCK.value)
    model: Mapped[str | None] = mapped_column(String(80), nullable=True)
    prompt_version: Mapped[str] = mapped_column(String(20), nullable=False, default="v1")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=AnalysisStatus.VALID.value)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_in: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_out: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Retained for debugging a bad generation. Redacted from logs, never
    # returned by the API.
    raw_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    candidate: Mapped[Candidate] = relationship(back_populates="analyses")


class GeneratedMessage(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An AI-drafted candidate message.

    Starts as DRAFT and requires explicit human approval before it can be
    marked sent. Sending is simulated (the brief permits this), but the
    approval gate is real and is the main reason prompt injection cannot turn
    into outbound communication.
    """

    __tablename__ = "generated_messages"

    candidate_id: Mapped[str] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    channel: Mapped[str] = mapped_column(String(20), nullable=False, default=InteractionChannel.EMAIL.value)
    subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    tone: Mapped[str] = mapped_column(String(40), nullable=False, default="warm_professional")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=MessageStatus.DRAFT.value)
    approved_by: Mapped[str | None] = mapped_column(
        ForeignKey("recruiters.id", ondelete="SET NULL"), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    provider: Mapped[str] = mapped_column(String(20), nullable=False, default=ProviderName.MOCK.value)
    model: Mapped[str | None] = mapped_column(String(80), nullable=True)

    candidate: Mapped[Candidate] = relationship(back_populates="messages")


class FollowUpAction(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A task for a recruiter, raised by an automation rule or by hand.

    The unique constraint is the idempotency mechanism: the scheduler may run
    every hour, restart mid-run, or be triggered manually during a demo, and a
    given rule can still only raise one action per candidate per day. Without
    it, an hourly job would bury the attention queue in duplicates.
    """

    __tablename__ = "follow_up_actions"
    __table_args__ = (
        UniqueConstraint("candidate_id", "rule_key", "dedupe_date", name="uq_follow_up_idempotency"),
        Index("ix_follow_ups_status_due", "status", "due_date"),
    )

    candidate_id: Mapped[str] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # A NULL rule_key means created by a human, which the constraint treats as
    # always-distinct (NULLs do not collide in a unique index).
    rule_key: Mapped[str | None] = mapped_column(String(60), nullable=True)
    dedupe_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    recommended_action: Mapped[str] = mapped_column(
        String(40), nullable=False, default=NextAction.NO_ACTION.value
    )
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=FollowUpStatus.OPEN.value)
    resolved_by: Mapped[str | None] = mapped_column(
        ForeignKey("recruiters.id", ondelete="SET NULL"), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    candidate: Mapped[Candidate] = relationship(back_populates="follow_ups")


class AutomationRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Execution history for the rule engine.

    A background job with no run log is unobservable: "did the rule fire?" has
    to be answerable without grepping application logs.
    """

    __tablename__ = "automation_runs"

    rule_key: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    trigger: Mapped[str] = mapped_column(String(20), nullable=False, default="scheduled")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    candidates_scanned: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    actions_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    actions_skipped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=AutomationRunStatus.SUCCESS.value)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class AuditLog(UUIDPrimaryKeyMixin, Base):
    """Append-only record of state changes.

    Deliberately not TimestampMixin: audit rows are never updated, so an
    `updated_at` column would be misleading. Before/after snapshots make an AI
    override reconstructable long after the fact - which is what turns
    human-in-the-loop from a claim into evidence.
    """

    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_entity", "entity_type", "entity_id", "created_at"),)

    actor_id: Mapped[str | None] = mapped_column(
        ForeignKey("recruiters.id", ondelete="SET NULL"), nullable=True
    )
    entity_type: Mapped[str] = mapped_column(String(60), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    before: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
