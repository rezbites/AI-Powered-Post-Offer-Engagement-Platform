"""Candidate orchestration: assembling views, and mutating with an audit trail.

This layer owns transactions. Repositories return rows; this turns them into
the shapes the recruiter UI needs, including the "Why?" factors that must
accompany every risk band.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import Actor
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.db.base import utcnow
from app.db.models import AIAnalysisRecord, Candidate
from app.domain import risk
from app.domain.context import CandidateContext, SignalView
from app.domain.enums import (
    AuditAction,
    CandidateStatus,
    NextAction,
    RiskLevel,
    RiskSource,
    SignalType,
    StageStatus,
)
from app.modules.audit import service as audit
from app.modules.candidates import repository as repo
from app.modules.candidates.schemas import (
    CandidateCreate,
    CandidateDetail,
    CandidateSummary,
    CandidateUpdate,
    InteractionOut,
    JourneyProgress,
    RiskView,
    SignalOut,
    StageOut,
)

# Fields captured in audit snapshots. Explicit, so adding a column does not
# silently start leaking it into the audit table.
AUDITED_FIELDS = [
    "name",
    "phone",
    "role_title",
    "location",
    "joining_date",
    "recruiter_id",
    "status",
    "notes",
    "risk_level",
    "risk_confidence",
    "risk_source",
    "risk_override_reason",
]


def _signals_from_analysis(analysis: AIAnalysisRecord | None) -> list[SignalView]:
    """Parse the stored signals JSON defensively.

    The column is written by the AI pipeline after validation, but it is still
    JSON in a database that migrations and manual fixes can touch. A malformed
    entry degrades to "no signals" rather than breaking the dashboard.
    """
    if analysis is None or not analysis.signals:
        return []

    parsed: list[SignalView] = []
    for raw in analysis.signals:
        if not isinstance(raw, dict):
            continue
        try:
            parsed.append(SignalView(type=SignalType(raw["type"]), evidence=str(raw.get("evidence", ""))))
        except (KeyError, ValueError):
            continue
    return parsed


def build_context(
    candidate: Candidate,
    *,
    stage_stats: dict[str, object],
    unanswered_outbound: int,
    interaction_totals: tuple[int, int],
    analysis: AIAnalysisRecord | None,
    open_follow_up_rules: frozenset[str] = frozenset(),
) -> CandidateContext:
    """Assemble the pure snapshot every decision function consumes."""
    total, inbound = interaction_totals
    return CandidateContext(
        candidate_id=candidate.id,
        name=candidate.name,
        status=CandidateStatus(candidate.status),
        joining_date=candidate.joining_date,
        offer_date=candidate.offer_date,
        last_interaction_at=candidate.last_interaction_at,
        unanswered_outbound=unanswered_outbound,
        total_interactions=total,
        inbound_interactions=inbound,
        stages_total=int(stage_stats.get("total", 0) or 0),
        stages_completed=int(stage_stats.get("completed", 0) or 0),
        stages_overdue=int(stage_stats.get("overdue", 0) or 0),
        signals=_signals_from_analysis(analysis),
        open_follow_up_rules=open_follow_up_rules,
    )


def _risk_view(
    candidate: Candidate,
    analysis: AIAnalysisRecord | None,
    factors: list[str],
) -> RiskView:
    """Risk as the UI renders it, with provenance always attached.

    When a human has overridden, the AI's signals are still shown - the
    recruiter's judgement replaces the band, not the evidence that prompted it.
    """
    source = RiskSource(candidate.risk_source)
    return RiskView(
        level=RiskLevel(candidate.risk_level),
        confidence=candidate.risk_confidence,
        source=source,
        rationale=(analysis.risk_rationale if analysis else "") or "",
        factors=factors,
        signals=[SignalOut(type=s.type, evidence=s.evidence) for s in _signals_from_analysis(analysis)],
        override_reason=candidate.risk_override_reason,
        overridden_by=candidate.risk_overridden_by,
        overridden_at=candidate.risk_overridden_at,
        last_analyzed_at=candidate.last_analyzed_at,
    )


def to_summary(
    candidate: Candidate,
    *,
    context: CandidateContext,
    analysis: AIAnalysisRecord | None,
    factors: list[str],
    next_stage_label: str | None,
    today: date,
) -> CandidateSummary:
    next_action = NextAction(analysis.next_action) if analysis else NextAction.NO_ACTION
    return CandidateSummary(
        id=candidate.id,
        name=candidate.name,
        email=candidate.email,
        role_title=candidate.role_title,
        location=candidate.location,
        joining_date=candidate.joining_date,
        days_to_joining=context.days_to_joining(today),
        status=CandidateStatus(candidate.status),
        recruiter_id=candidate.recruiter_id,
        recruiter_name=candidate.recruiter.name if candidate.recruiter else None,
        last_interaction_at=candidate.last_interaction_at,
        days_since_interaction=context.days_since_interaction(today),
        risk=_risk_view(candidate, analysis, factors),
        next_action=next_action,
        next_action_label=next_action.label,
        # The dashboard shows the top few reasons inline; the detail page shows
        # all of them. Truncating here keeps table rows scannable.
        why=factors[:3],
        journey=JourneyProgress(
            completed=context.stages_completed,
            total=context.stages_total,
            current_stage=next_stage_label,
            overdue_stages=context.stages_overdue,
        ),
    )


def to_detail(
    candidate: Candidate,
    *,
    context: CandidateContext,
    analysis: AIAnalysisRecord | None,
    factors: list[str],
    next_stage_label: str | None,
    today: date,
) -> CandidateDetail:
    summary = to_summary(
        candidate,
        context=context,
        analysis=analysis,
        factors=factors,
        next_stage_label=next_stage_label,
        today=today,
    )

    stages = [
        StageOut(
            key=cs.stage.key,
            label=cs.stage.label,
            sequence=cs.stage.sequence,
            status=StageStatus(cs.status),
            due_date=cs.due_date,
            completed_at=cs.completed_at,
            is_overdue=(
                cs.status == StageStatus.PENDING.value
                and cs.due_date is not None
                and cs.due_date < today
            ),
        )
        for cs in sorted(candidate.stages, key=lambda c: c.stage.sequence)
    ]

    interactions = [
        InteractionOut(
            id=i.id,
            channel=i.channel,
            direction=i.direction,
            content=i.content,
            occurred_at=i.occurred_at,
        )
        for i in sorted(candidate.interactions, key=lambda i: i.occurred_at, reverse=True)
    ]

    return CandidateDetail(
        **summary.model_dump(),
        phone=candidate.phone,
        offer_date=candidate.offer_date,
        notes=candidate.notes,
        ai_summary=analysis.summary if analysis else None,
        recommended_follow_up=analysis.recommended_follow_up if analysis else None,
        # Provenance surfaced to the client so the UI can label Demo Mode
        # output as a mock fixture rather than presenting it as model output.
        analysis_provider=analysis.provider if analysis else None,
        analysis_model=analysis.model if analysis else None,
        stages=stages,
        interactions=interactions,
    )


# --------------------------------------------------------------------------
# Mutations
# --------------------------------------------------------------------------
async def create_candidate(
    session: AsyncSession, payload: CandidateCreate, *, actor: Actor
) -> Candidate:
    """Create a candidate and materialise their full journey.

    Stage rows are created immediately for every stage in the default journey,
    so "pending" is real data from the first moment rather than an absence.
    That is what makes stage drop-off measurable later.
    """
    if await repo.email_exists(session, payload.email):
        raise ConflictError("A candidate with this email already exists.", details={"email": payload.email})

    if not await repo.recruiter_exists(session, payload.recruiter_id):
        raise ValidationError(
            "The assigned recruiter does not exist.", details={"recruiter_id": payload.recruiter_id}
        )

    from app.modules.engagement import service as engagement

    candidate = Candidate(
        name=payload.name,
        email=str(payload.email),
        phone=payload.phone,
        role_title=payload.role_title,
        location=payload.location,
        offer_date=payload.offer_date,
        joining_date=payload.joining_date,
        recruiter_id=payload.recruiter_id,
        status=payload.status.value,
        notes=payload.notes,
        risk_level=RiskLevel.LOW.value,
        risk_source=RiskSource.RULE.value,
        risk_confidence=0.0,
    )
    session.add(candidate)
    await session.flush()

    await engagement.assign_default_journey(session, candidate)

    # Run the deterministic engine immediately. Without this a new candidate
    # sits at confidence 0.0, which the UI would render as "0% confident" -
    # implying a assessed judgement rather than the truth, which is that
    # nothing has assessed them yet.
    stage_count = len(
        (await engagement.get_default_template(session)).stages
    )
    initial_ctx = CandidateContext(
        candidate_id=candidate.id,
        name=candidate.name,
        status=CandidateStatus(candidate.status),
        joining_date=candidate.joining_date,
        offer_date=candidate.offer_date,
        last_interaction_at=None,
        unanswered_outbound=0,
        total_interactions=0,
        inbound_interactions=0,
        stages_total=stage_count,
        stages_completed=0,
        stages_overdue=0,
    )
    assessment = risk.assess(initial_ctx, today=date.today())
    if assessment is not None:
        candidate.risk_level = assessment.level.value
        candidate.risk_confidence = assessment.confidence

    await audit.record(
        session,
        actor=actor,
        entity_type="candidate",
        entity_id=candidate.id,
        action=AuditAction.CREATE,
        after=audit.snapshot_of(candidate, AUDITED_FIELDS),
    )
    await session.commit()
    await session.refresh(candidate)
    return candidate


async def update_candidate(
    session: AsyncSession, candidate_id: str, payload: CandidateUpdate, *, actor: Actor
) -> Candidate:
    candidate = await repo.get_candidate(session, candidate_id)
    if candidate is None:
        raise NotFoundError("Candidate not found.", details={"candidate_id": candidate_id})

    before = audit.snapshot_of(candidate, AUDITED_FIELDS)

    changes = payload.model_dump(exclude_unset=True)
    if "recruiter_id" in changes and not await repo.recruiter_exists(session, changes["recruiter_id"]):
        raise ValidationError(
            "The assigned recruiter does not exist.", details={"recruiter_id": changes["recruiter_id"]}
        )

    if "joining_date" in changes and changes["joining_date"] < candidate.offer_date:
        raise ValidationError("joining_date must be on or after offer_date.")

    for field_name, value in changes.items():
        setattr(candidate, field_name, value.value if hasattr(value, "value") else value)

    await audit.record_change(
        session,
        actor=actor,
        entity=candidate,
        entity_type="candidate",
        action=AuditAction.UPDATE,
        tracked_fields=AUDITED_FIELDS,
        before_snapshot=before,
    )
    await session.commit()
    await session.refresh(candidate)
    return candidate


async def override_risk(
    session: AsyncSession,
    candidate_id: str,
    *,
    risk_level: RiskLevel,
    reason: str,
    confidence: float,
    actor: Actor,
) -> Candidate:
    """Replace the AI's risk band with a human judgement.

    The reason is mandatory. An override without a stated reason is
    indistinguishable from a mis-click three weeks later, and it is precisely
    the disagreements between recruiter and model that are worth reviewing.
    """
    candidate = await repo.get_candidate(session, candidate_id)
    if candidate is None:
        raise NotFoundError("Candidate not found.", details={"candidate_id": candidate_id})

    if not reason.strip():
        raise ValidationError("An override reason is required.")

    before = audit.snapshot_of(candidate, AUDITED_FIELDS)

    candidate.risk_level = risk_level.value
    candidate.risk_source = RiskSource.HUMAN.value
    candidate.risk_override_reason = reason.strip()
    candidate.risk_overridden_by = actor.id
    candidate.risk_overridden_at = utcnow()
    # The recruiter states their own certainty. Forcing every override to 1.0
    # was wrong: a recruiter who thinks someone is probably fine but is not
    # sure has said something different from one who has just spoken to the
    # candidate and knows. Flattening both to "certain" throws away the more
    # useful half of the signal.
    candidate.risk_confidence = max(0.0, min(1.0, confidence))

    await audit.record_change(
        session,
        actor=actor,
        entity=candidate,
        entity_type="candidate",
        action=AuditAction.RISK_OVERRIDE,
        tracked_fields=AUDITED_FIELDS,
        before_snapshot=before,
    )
    await session.commit()
    await session.refresh(candidate)
    return candidate


async def revert_risk_to_ai(session: AsyncSession, candidate_id: str, *, actor: Actor) -> Candidate:
    """Discard a human override and fall back to the latest stored analysis.

    If no analysis exists yet the candidate reverts to a rule-sourced LOW
    rather than staying pinned to the override.
    """
    candidate = await repo.get_candidate(session, candidate_id)
    if candidate is None:
        raise NotFoundError("Candidate not found.", details={"candidate_id": candidate_id})

    before = audit.snapshot_of(candidate, AUDITED_FIELDS)
    analyses = await repo.latest_analyses_for(session, [candidate_id])
    analysis = analyses.get(candidate_id)

    candidate.risk_level = analysis.risk_level if analysis else RiskLevel.LOW.value
    candidate.risk_confidence = analysis.risk_confidence if analysis else 0.0
    candidate.risk_source = (RiskSource.AI if analysis else RiskSource.RULE).value
    candidate.risk_override_reason = None
    candidate.risk_overridden_by = None
    candidate.risk_overridden_at = None

    await audit.record_change(
        session,
        actor=actor,
        entity=candidate,
        entity_type="candidate",
        action=AuditAction.RISK_REVERT,
        tracked_fields=AUDITED_FIELDS,
        before_snapshot=before,
    )
    await session.commit()
    await session.refresh(candidate)
    return candidate


# --------------------------------------------------------------------------
# View assembly
# --------------------------------------------------------------------------
async def assemble_summaries(
    session: AsyncSession, candidates: list[Candidate], *, today: date
) -> list[CandidateSummary]:
    """Turn a page of candidate rows into dashboard rows.

    Every auxiliary lookup is batched across the whole page, so the cost is a
    fixed handful of queries regardless of page size. Building these views one
    candidate at a time would be the classic N+1 that makes a list endpoint
    slow only once real data arrives.
    """
    if not candidates:
        return []

    ids = [c.id for c in candidates]

    stage_stats = await repo.stage_progress_for(session, ids, today=today)
    next_labels = await repo.next_pending_stage_labels(session, ids)
    analyses = await repo.latest_analyses_for(session, ids)
    unanswered = await repo.unanswered_outbound_counts(session, ids)
    totals = await repo.interaction_counts(session, ids)
    open_follow_ups = await repo.open_follow_up_rules(session, ids)

    summaries: list[CandidateSummary] = []
    for candidate in candidates:
        analysis = analyses.get(candidate.id)
        context = build_context(
            candidate,
            stage_stats=stage_stats.get(candidate.id, {}),
            unanswered_outbound=unanswered.get(candidate.id, 0),
            interaction_totals=totals.get(candidate.id, (0, 0)),
            analysis=analysis,
            open_follow_up_rules=open_follow_ups.get(candidate.id, frozenset()),
        )
        summaries.append(
            to_summary(
                candidate,
                context=context,
                analysis=analysis,
                factors=risk.explain(context, today=today),
                next_stage_label=next_labels.get(candidate.id),
                today=today,
            )
        )
    return summaries


async def assemble_detail(
    session: AsyncSession, candidate: Candidate, *, today: date
) -> CandidateDetail:
    """Full detail view for one candidate."""
    ids = [candidate.id]

    stage_stats = await repo.stage_progress_for(session, ids, today=today)
    next_labels = await repo.next_pending_stage_labels(session, ids)
    analyses = await repo.latest_analyses_for(session, ids)
    unanswered = await repo.unanswered_outbound_counts(session, ids)
    totals = await repo.interaction_counts(session, ids)
    open_follow_ups = await repo.open_follow_up_rules(session, ids)

    analysis = analyses.get(candidate.id)
    context = build_context(
        candidate,
        stage_stats=stage_stats.get(candidate.id, {}),
        unanswered_outbound=unanswered.get(candidate.id, 0),
        interaction_totals=totals.get(candidate.id, (0, 0)),
        analysis=analysis,
        open_follow_up_rules=open_follow_ups.get(candidate.id, frozenset()),
    )

    return to_detail(
        candidate,
        context=context,
        analysis=analysis,
        # The detail page shows every factor, not just the top three.
        factors=risk.explain(context, today=today),
        next_stage_label=next_labels.get(candidate.id),
        today=today,
    )
