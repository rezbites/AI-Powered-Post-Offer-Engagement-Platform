"""Data access for candidates.

All SQL lives here. Services orchestrate and hold transactions; routers do HTTP.
Keeping the layers separate is what lets the risk logic be tested without a
database and the SQL be optimised without touching business rules.

The list query is deliberately three bounded statements rather than one big
join: fetch the page of candidates, then aggregate stages and latest analyses
for exactly those ids. A single join would multiply rows by stage count and
force de-duplication in Python; N+1 per-candidate queries would be worse still.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import Select, and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import (
    AIAnalysisRecord,
    Candidate,
    CandidateStage,
    FollowUpAction,
    Interaction,
    JourneyStage,
    Recruiter,
)
from app.domain.enums import FollowUpStatus, InteractionDirection, StageStatus
from app.modules.candidates.schemas import CandidateFilters


def _month_bounds(joining_month: str) -> tuple[date, date]:
    """Half-open [start, next_month_start) range for a YYYY-MM string.

    Comparing against a range keeps the query sargable so the index on
    joining_date is used. Wrapping the column in EXTRACT(MONTH ...) would
    force a full scan.
    """
    year, month = (int(part) for part in joining_month.split("-"))
    start = date(year, month, 1)
    end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return start, end


def apply_filters(stmt: Select, filters: CandidateFilters, *, today: date) -> Select:
    """Translate dashboard filters into WHERE clauses."""
    conditions = []

    if filters.joining_month:
        start, end = _month_bounds(filters.joining_month)
        conditions.append(and_(Candidate.joining_date >= start, Candidate.joining_date < end))

    if filters.joining_within_days is not None:
        # "Joining in the next N days" excludes dates already past, otherwise
        # the 7/15/30-day analytics tiles would count historical joiners.
        conditions.append(
            and_(
                Candidate.joining_date >= today,
                Candidate.joining_date <= today + timedelta(days=filters.joining_within_days),
            )
        )

    if filters.recruiter_id:
        conditions.append(Candidate.recruiter_id == filters.recruiter_id)
    if filters.role_title:
        conditions.append(Candidate.role_title == filters.role_title)
    if filters.risk_level:
        conditions.append(Candidate.risk_level == filters.risk_level.value)
    if filters.status:
        conditions.append(Candidate.status == filters.status.value)

    if filters.search:
        # ILIKE-equivalent that also works on SQLite, where LIKE is already
        # case-insensitive for ASCII. Leading wildcard means no index use; at
        # scale this becomes a trigram index or a search service.
        pattern = f"%{filters.search.strip()}%"
        conditions.append(or_(Candidate.name.ilike(pattern), Candidate.email.ilike(pattern)))

    return stmt.where(*conditions) if conditions else stmt


async def list_candidates(
    session: AsyncSession,
    filters: CandidateFilters,
    *,
    limit: int,
    offset: int,
    today: date,
) -> tuple[list[Candidate], int]:
    """Return one page plus the total count matching the filter."""
    base = select(Candidate).options(selectinload(Candidate.recruiter))
    base = apply_filters(base, filters, today=today)

    # Count over the same predicates, without ORDER BY or eager loads.
    count_stmt = apply_filters(select(func.count(Candidate.id)), filters, today=today)
    total = (await session.execute(count_stmt)).scalar_one()

    # Soonest joiners first: the dashboard's default question is "who needs me
    # now". id is a tiebreaker so pagination is stable across identical dates.
    page_stmt = base.order_by(Candidate.joining_date.asc(), Candidate.id.asc()).limit(limit).offset(offset)
    rows = list((await session.execute(page_stmt)).scalars().all())
    return rows, total


async def get_candidate(session: AsyncSession, candidate_id: str) -> Candidate | None:
    stmt = (
        select(Candidate)
        .where(Candidate.id == candidate_id)
        .options(selectinload(Candidate.recruiter))
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_candidate_with_detail(session: AsyncSession, candidate_id: str) -> Candidate | None:
    """Detail-page load: candidate plus interactions and stages in one round trip.

    selectinload issues one additional SELECT per collection (not per row), so
    this is three statements total regardless of how many interactions exist.
    """
    stmt = (
        select(Candidate)
        .where(Candidate.id == candidate_id)
        .options(
            selectinload(Candidate.recruiter),
            selectinload(Candidate.interactions),
            selectinload(Candidate.stages).selectinload(CandidateStage.stage),
        )
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def stage_progress_for(
    session: AsyncSession, candidate_ids: list[str], *, today: date
) -> dict[str, dict[str, object]]:
    """Completed/total/overdue stage counts for a set of candidates.

    One grouped query for the whole page. Overdue is counted in SQL rather
    than Python so the database does the filtering it is good at.
    """
    if not candidate_ids:
        return {}

    completed_case = func.sum(
        case((CandidateStage.status == StageStatus.COMPLETED.value, 1), else_=0)
    )
    overdue_case = func.sum(
        case(
            (
                and_(
                    CandidateStage.status == StageStatus.PENDING.value,
                    CandidateStage.due_date.is_not(None),
                    CandidateStage.due_date < today,
                ),
                1,
            ),
            else_=0,
        )
    )

    stmt = (
        select(
            CandidateStage.candidate_id,
            func.count(CandidateStage.id).label("total"),
            completed_case.label("completed"),
            overdue_case.label("overdue"),
        )
        .where(CandidateStage.candidate_id.in_(candidate_ids))
        .group_by(CandidateStage.candidate_id)
    )

    result: dict[str, dict[str, object]] = {}
    for row in (await session.execute(stmt)).all():
        result[row.candidate_id] = {
            "total": int(row.total or 0),
            "completed": int(row.completed or 0),
            "overdue": int(row.overdue or 0),
        }
    return result


async def next_pending_stage_labels(
    session: AsyncSession, candidate_ids: list[str]
) -> dict[str, str]:
    """Label of the earliest pending stage per candidate.

    Answers "where are they now?" on the dashboard without loading every stage
    row for every candidate.
    """
    if not candidate_ids:
        return {}

    stmt = (
        select(CandidateStage.candidate_id, JourneyStage.label, JourneyStage.sequence)
        .join(JourneyStage, JourneyStage.id == CandidateStage.stage_id)
        .where(
            CandidateStage.candidate_id.in_(candidate_ids),
            CandidateStage.status == StageStatus.PENDING.value,
        )
        .order_by(CandidateStage.candidate_id, JourneyStage.sequence.asc())
    )

    labels: dict[str, str] = {}
    for candidate_id, label, _sequence in (await session.execute(stmt)).all():
        # Ordered by sequence, so the first row seen per candidate is the next
        # pending stage; later rows for the same candidate are ignored.
        labels.setdefault(candidate_id, label)
    return labels


async def latest_analyses_for(
    session: AsyncSession, candidate_ids: list[str]
) -> dict[str, AIAnalysisRecord]:
    """Most recent analysis per candidate, in a single query.

    Uses a ROW_NUMBER window rather than DISTINCT ON so the same statement runs
    on both Postgres and the SQLite fallback.
    """
    if not candidate_ids:
        return {}

    # Rank ids only, then join the full row back. Selecting bare ids keeps the
    # window subquery narrow, and the join avoids ORM-entity aliasing issues.
    ranked = (
        select(
            AIAnalysisRecord.id.label("analysis_id"),
            func.row_number()
            .over(
                partition_by=AIAnalysisRecord.candidate_id,
                order_by=AIAnalysisRecord.created_at.desc(),
            )
            .label("rank"),
        )
        .where(AIAnalysisRecord.candidate_id.in_(candidate_ids))
        .subquery()
    )

    stmt = select(AIAnalysisRecord).join(
        ranked,
        and_(AIAnalysisRecord.id == ranked.c.analysis_id, ranked.c.rank == 1),
    )
    rows = (await session.execute(stmt)).scalars().all()
    return {row.candidate_id: row for row in rows}


async def unanswered_outbound_counts(
    session: AsyncSession, candidate_ids: list[str]
) -> dict[str, int]:
    """How many outbound messages have gone unanswered since the last inbound.

    A run of unanswered outbound messages is one of the strongest non-semantic
    risk signals available: it needs no model, and unlike raw silence it
    distinguishes "we have not tried" from "we tried and got nothing back".
    """
    if not candidate_ids:
        return {}

    # Timestamp of the last inbound message per candidate.
    last_inbound = (
        select(
            Interaction.candidate_id.label("cid"),
            func.max(Interaction.occurred_at).label("last_inbound_at"),
        )
        .where(
            Interaction.candidate_id.in_(candidate_ids),
            Interaction.direction == InteractionDirection.INBOUND.value,
        )
        .group_by(Interaction.candidate_id)
        .subquery()
    )

    stmt = (
        select(Interaction.candidate_id, func.count(Interaction.id))
        .outerjoin(last_inbound, last_inbound.c.cid == Interaction.candidate_id)
        .where(
            Interaction.candidate_id.in_(candidate_ids),
            Interaction.direction == InteractionDirection.OUTBOUND.value,
            or_(
                last_inbound.c.last_inbound_at.is_(None),
                Interaction.occurred_at > last_inbound.c.last_inbound_at,
            ),
        )
        .group_by(Interaction.candidate_id)
    )

    return {cid: int(count) for cid, count in (await session.execute(stmt)).all()}


async def interaction_counts(session: AsyncSession, candidate_ids: list[str]) -> dict[str, tuple[int, int]]:
    """(total, inbound) interaction counts per candidate.

    Feeds confidence derivation: an analysis built on two messages deserves
    less confidence than one built on twelve.
    """
    if not candidate_ids:
        return {}

    inbound_case = func.sum(
        case((Interaction.direction == InteractionDirection.INBOUND.value, 1), else_=0)
    )
    stmt = (
        select(Interaction.candidate_id, func.count(Interaction.id), inbound_case)
        .where(Interaction.candidate_id.in_(candidate_ids))
        .group_by(Interaction.candidate_id)
    )
    return {cid: (int(total or 0), int(inbound or 0)) for cid, total, inbound in (await session.execute(stmt)).all()}


async def open_follow_up_rules(
    session: AsyncSession, candidate_ids: list[str]
) -> dict[str, frozenset[str]]:
    """Which rules have an unresolved follow-up, per candidate.

    Returns rule keys rather than a bare "has any" flag so predicates can be
    specific: a stage-overdue reminder should not suppress a high-risk
    escalation, and a boolean cannot express that distinction.

    Human-created follow-ups have a NULL rule_key; they map to the sentinel
    "manual" so they still count as work in progress.
    """
    if not candidate_ids:
        return {}

    stmt = (
        select(FollowUpAction.candidate_id, FollowUpAction.rule_key)
        .where(
            FollowUpAction.candidate_id.in_(candidate_ids),
            FollowUpAction.status == FollowUpStatus.OPEN.value,
        )
        .distinct()
    )

    grouped: dict[str, set[str]] = {}
    for candidate_id, rule_key in (await session.execute(stmt)).all():
        grouped.setdefault(candidate_id, set()).add(rule_key or "manual")
    return {cid: frozenset(keys) for cid, keys in grouped.items()}


async def email_exists(session: AsyncSession, email: str, *, exclude_id: str | None = None) -> bool:
    stmt = select(func.count(Candidate.id)).where(Candidate.email == email)
    if exclude_id:
        stmt = stmt.where(Candidate.id != exclude_id)
    return bool((await session.execute(stmt)).scalar_one())


async def recruiter_exists(session: AsyncSession, recruiter_id: str) -> bool:
    stmt = select(func.count(Recruiter.id)).where(Recruiter.id == recruiter_id)
    return bool((await session.execute(stmt)).scalar_one())


async def distinct_roles(session: AsyncSession) -> list[str]:
    """Filter dropdown options, taken from live data rather than a hardcoded
    list so a new role title appears without a deployment."""
    stmt = select(Candidate.role_title).distinct().order_by(Candidate.role_title)
    return [row[0] for row in (await session.execute(stmt)).all()]
