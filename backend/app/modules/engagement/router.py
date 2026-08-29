"""Engagement journey and interaction endpoints."""

from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, status
from pydantic import BaseModel, ConfigDict, Field

from app.core.deps import ActorDep, SessionDep
from app.domain.enums import InteractionChannel, InteractionDirection, StageStatus
from app.modules.engagement import service

router = APIRouter(prefix="/candidates/{candidate_id}", tags=["engagement"])


class InteractionCreate(BaseModel):
    channel: InteractionChannel = InteractionChannel.EMAIL
    direction: InteractionDirection = InteractionDirection.OUTBOUND
    content: str = Field(min_length=1, max_length=10_000)
    occurred_at: datetime | None = Field(
        default=None,
        description="Defaults to now. Supply a past timestamp when back-filling history.",
    )


class InteractionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    candidate_id: str
    channel: str
    direction: str
    content: str
    occurred_at: datetime


class StageUpdate(BaseModel):
    status: StageStatus


class StageResponse(BaseModel):
    key: str
    label: str
    sequence: int
    status: StageStatus
    due_date: date | None = None
    completed_at: datetime | None = None
    is_overdue: bool = False


@router.get("/interactions", response_model=list[InteractionResponse], summary="Conversation history")
async def list_interactions(session: SessionDep, candidate_id: str) -> list[InteractionResponse]:
    rows = await service.list_interactions(session, candidate_id)
    return [InteractionResponse.model_validate(row) for row in rows]


@router.post(
    "/interactions",
    response_model=InteractionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Record an interaction",
)
async def create_interaction(
    session: SessionDep, actor: ActorDep, candidate_id: str, payload: InteractionCreate
) -> InteractionResponse:
    """Recording an interaction also refreshes the candidate's recency marker,
    which is what clears them from the silent-candidate automation rule."""
    interaction = await service.add_interaction(
        session,
        candidate_id=candidate_id,
        channel=payload.channel,
        direction=payload.direction,
        content=payload.content,
        occurred_at=payload.occurred_at,
        actor=actor,
    )
    return InteractionResponse.model_validate(interaction)


@router.get("/stages", response_model=list[StageResponse], summary="Engagement journey progress")
async def list_stages(session: SessionDep, candidate_id: str) -> list[StageResponse]:
    """Returns completed and pending steps together, as the brief requires."""
    today = date.today()
    rows = await service.list_stages(session, candidate_id)
    return [
        StageResponse(
            key=row.stage.key,
            label=row.stage.label,
            sequence=row.stage.sequence,
            status=StageStatus(row.status),
            due_date=row.due_date,
            completed_at=row.completed_at,
            is_overdue=(
                row.status == StageStatus.PENDING.value
                and row.due_date is not None
                and row.due_date < today
            ),
        )
        for row in rows
    ]


@router.patch(
    "/stages/{stage_key}",
    response_model=StageResponse,
    summary="Complete or reset an engagement step",
)
async def update_stage(
    session: SessionDep,
    actor: ActorDep,
    candidate_id: str,
    stage_key: str,
    payload: StageUpdate,
) -> StageResponse:
    today = date.today()
    row = await service.set_stage_status(
        session,
        candidate_id=candidate_id,
        stage_key=stage_key,
        status=payload.status,
        actor=actor,
    )
    return StageResponse(
        key=row.stage.key,
        label=row.stage.label,
        sequence=row.stage.sequence,
        status=StageStatus(row.status),
        due_date=row.due_date,
        completed_at=row.completed_at,
        is_overdue=(
            row.status == StageStatus.PENDING.value
            and row.due_date is not None
            and row.due_date < today
        ),
    )
