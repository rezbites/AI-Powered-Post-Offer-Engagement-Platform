"""Engagement journey and interaction management.

Two responsibilities that belong together because both mutate the signals risk
scoring depends on: journey stage transitions, and the conversation history.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.deps import Actor
from app.core.errors import NotFoundError, ValidationError
from app.db.base import utcnow
from app.db.models import Candidate, CandidateStage, Interaction, JourneyStage, JourneyTemplate
from app.domain.enums import (
    AuditAction,
    InteractionChannel,
    InteractionDirection,
    StageStatus,
)
from app.modules.audit import service as audit


async def get_default_template(session: AsyncSession) -> JourneyTemplate | None:
    stmt = (
        select(JourneyTemplate)
        .where(JourneyTemplate.is_default.is_(True))
        .options(selectinload(JourneyTemplate.stages))
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def assign_default_journey(session: AsyncSession, candidate: Candidate) -> int:
    """Create a CandidateStage row for every stage in the default journey.

    Due dates are computed from the offer date plus each stage's SLA and then
    frozen onto the row. Storing rather than deriving means a later change to
    the template does not silently move historical deadlines - and overdue
    reporting stays reproducible.
    """
    template = await get_default_template(session)
    if template is None:
        # A database with no journey template is a deployment problem, not a
        # request problem: fail loudly rather than creating a candidate with no
        # journey whose progress can never be tracked.
        raise ValidationError(
            "No default journey template is configured. Run the seed or create one."
        )

    candidate.journey_template_id = template.id

    for stage in sorted(template.stages, key=lambda s: s.sequence):
        session.add(
            CandidateStage(
                candidate_id=candidate.id,
                stage_id=stage.id,
                status=StageStatus.PENDING.value,
                due_date=candidate.offer_date + timedelta(days=stage.sla_days),
            )
        )

    await session.flush()
    return len(template.stages)


async def set_stage_status(
    session: AsyncSession,
    *,
    candidate_id: str,
    stage_key: str,
    status: StageStatus,
    actor: Actor,
) -> CandidateStage:
    """Mark a journey step complete or return it to pending.

    Reset exists because completion is a human judgement that can be wrong;
    without it, a mis-click is permanent and the drop-off analytics inherit
    the error.
    """
    stmt = (
        select(CandidateStage)
        .join(JourneyStage, JourneyStage.id == CandidateStage.stage_id)
        .where(CandidateStage.candidate_id == candidate_id, JourneyStage.key == stage_key)
        .options(selectinload(CandidateStage.stage))
    )
    candidate_stage = (await session.execute(stmt)).scalar_one_or_none()

    if candidate_stage is None:
        raise NotFoundError(
            "That engagement step does not exist for this candidate.",
            details={"candidate_id": candidate_id, "stage_key": stage_key},
        )

    before = audit.snapshot_of(candidate_stage, ["status", "completed_at", "completed_by"])

    candidate_stage.status = status.value
    if status is StageStatus.COMPLETED:
        candidate_stage.completed_at = utcnow()
        candidate_stage.completed_by = actor.id
    else:
        candidate_stage.completed_at = None
        candidate_stage.completed_by = None

    await audit.record_change(
        session,
        actor=actor,
        entity=candidate_stage,
        entity_type="candidate_stage",
        action=AuditAction.STAGE_COMPLETE if status is StageStatus.COMPLETED else AuditAction.STAGE_RESET,
        tracked_fields=["status", "completed_at", "completed_by"],
        before_snapshot=before,
    )

    await session.commit()
    await session.refresh(candidate_stage)
    return candidate_stage


async def list_stages(session: AsyncSession, candidate_id: str) -> list[CandidateStage]:
    stmt = (
        select(CandidateStage)
        .join(JourneyStage, JourneyStage.id == CandidateStage.stage_id)
        .where(CandidateStage.candidate_id == candidate_id)
        .options(selectinload(CandidateStage.stage))
        .order_by(JourneyStage.sequence.asc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def add_interaction(
    session: AsyncSession,
    *,
    candidate_id: str,
    channel: InteractionChannel,
    direction: InteractionDirection,
    content: str,
    occurred_at=None,
    actor: Actor,
) -> Interaction:
    """Record a communication event and refresh the candidate's recency marker.

    `last_interaction_at` is updated here rather than derived on read because
    the silent-candidate rule and the attention queue both filter on it across
    the entire population - a per-row aggregate would be the single most
    expensive query in the system.

    The guard against moving the timestamp backwards matters when back-filling
    historical messages: importing an old email should not make a candidate
    look freshly contacted.
    """
    candidate = await session.get(Candidate, candidate_id)
    if candidate is None:
        raise NotFoundError("Candidate not found.", details={"candidate_id": candidate_id})

    if not content.strip():
        raise ValidationError("Interaction content cannot be empty.")

    when = occurred_at or utcnow()

    interaction = Interaction(
        candidate_id=candidate_id,
        channel=channel.value,
        direction=direction.value,
        content=content.strip(),
        occurred_at=when,
        created_by=actor.id,
    )
    session.add(interaction)

    if candidate.last_interaction_at is None or when > candidate.last_interaction_at:
        candidate.last_interaction_at = when

    await session.commit()
    await session.refresh(interaction)
    return interaction


async def list_interactions(
    session: AsyncSession, candidate_id: str, *, limit: int = 100
) -> list[Interaction]:
    stmt = (
        select(Interaction)
        .where(Interaction.candidate_id == candidate_id)
        .order_by(Interaction.occurred_at.desc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())
