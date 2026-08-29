"""Candidate HTTP endpoints.

Routers stay thin: parse and validate input, delegate to the service, shape the
response. No SQL and no business rules live here.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field

from app.core.deps import ActorDep, PaginationDep, SessionDep
from app.core.errors import NotFoundError
from app.core.schemas import Page
from app.domain.enums import CandidateStatus, RiskLevel
from app.modules.candidates import repository as repo
from app.modules.candidates import service
from app.modules.candidates.schemas import (
    CandidateCreate,
    CandidateDetail,
    CandidateFilters,
    CandidateSummary,
    CandidateUpdate,
)

router = APIRouter(prefix="/candidates", tags=["candidates"])


def candidate_filters(
    joining_month: Annotated[str | None, Query(description="Joining month as YYYY-MM.")] = None,
    recruiter_id: Annotated[str | None, Query()] = None,
    role_title: Annotated[str | None, Query()] = None,
    risk_level: Annotated[RiskLevel | None, Query()] = None,
    status_filter: Annotated[CandidateStatus | None, Query(alias="status")] = None,
    search: Annotated[str | None, Query(description="Match on name or email.")] = None,
    joining_within_days: Annotated[int | None, Query(ge=1, le=365)] = None,
) -> CandidateFilters:
    """Filter set as a dependency so the same shape is reusable by analytics."""
    return CandidateFilters(
        joining_month=joining_month,
        recruiter_id=recruiter_id,
        role_title=role_title,
        risk_level=risk_level,
        status=status_filter,
        search=search,
        joining_within_days=joining_within_days,
    )


FiltersDep = Annotated[CandidateFilters, Depends(candidate_filters)]


class RiskOverrideRequest(BaseModel):
    """A recruiter replacing the model's judgement.

    `reason` is required and non-trivial: an unexplained override is
    indistinguishable from a mis-click when reviewed weeks later.
    """

    risk_level: RiskLevel
    reason: str = Field(min_length=3, max_length=500)


@router.get("", response_model=Page[CandidateSummary], summary="List and filter candidates")
async def list_candidates(
    session: SessionDep,
    filters: FiltersDep,
    page: PaginationDep,
) -> Page[CandidateSummary]:
    """The dashboard's main query.

    Returns risk, the reasons behind it, and the recommended action inline on
    every row, because the brief requires all three visible on the list itself.
    """
    today = date.today()
    candidates, total = await repo.list_candidates(
        session, filters, limit=page.limit, offset=page.offset, today=today
    )
    items = await service.assemble_summaries(session, candidates, today=today)
    return Page(items=items, total=total, limit=page.limit, offset=page.offset)


@router.get("/roles", response_model=list[str], summary="Distinct role titles for filters")
async def list_roles(session: SessionDep) -> list[str]:
    return await repo.distinct_roles(session)


@router.get("/{candidate_id}", response_model=CandidateDetail, summary="Candidate detail")
async def get_candidate(session: SessionDep, candidate_id: str) -> CandidateDetail:
    candidate = await repo.get_candidate_with_detail(session, candidate_id)
    if candidate is None:
        raise NotFoundError("Candidate not found.", details={"candidate_id": candidate_id})
    return await service.assemble_detail(session, candidate, today=date.today())


@router.post(
    "",
    response_model=CandidateDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Create a candidate",
)
async def create_candidate(
    session: SessionDep, actor: ActorDep, payload: CandidateCreate
) -> CandidateDetail:
    candidate = await service.create_candidate(session, payload, actor=actor)
    detailed = await repo.get_candidate_with_detail(session, candidate.id)
    return await service.assemble_detail(session, detailed, today=date.today())


@router.patch("/{candidate_id}", response_model=CandidateDetail, summary="Update a candidate")
async def update_candidate(
    session: SessionDep, actor: ActorDep, candidate_id: str, payload: CandidateUpdate
) -> CandidateDetail:
    await service.update_candidate(session, candidate_id, payload, actor=actor)
    detailed = await repo.get_candidate_with_detail(session, candidate_id)
    return await service.assemble_detail(session, detailed, today=date.today())


@router.post(
    "/{candidate_id}/risk/override",
    response_model=CandidateDetail,
    summary="Override the AI risk classification",
)
async def override_risk(
    session: SessionDep, actor: ActorDep, candidate_id: str, payload: RiskOverrideRequest
) -> CandidateDetail:
    """Human-in-the-loop control required by the brief.

    The override is recorded with actor, timestamp and reason, and the response
    reports `risk.source = human` so the UI can show provenance rather than
    presenting a recruiter's decision as a model output.
    """
    await service.override_risk(
        session,
        candidate_id,
        risk_level=payload.risk_level,
        reason=payload.reason,
        actor=actor,
    )
    detailed = await repo.get_candidate_with_detail(session, candidate_id)
    return await service.assemble_detail(session, detailed, today=date.today())


@router.post(
    "/{candidate_id}/risk/revert",
    response_model=CandidateDetail,
    summary="Discard a human override and restore the AI classification",
)
async def revert_risk(session: SessionDep, actor: ActorDep, candidate_id: str) -> CandidateDetail:
    await service.revert_risk_to_ai(session, candidate_id, actor=actor)
    detailed = await repo.get_candidate_with_detail(session, candidate_id)
    return await service.assemble_detail(session, detailed, today=date.today())
