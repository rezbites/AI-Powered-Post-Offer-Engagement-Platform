"""Automation engine: evaluate rules, create follow-ups, record the run.

The rule *decisions* live in `domain/rules.py` as pure predicates. This module
is the effectful half - it loads candidates, applies those predicates, writes
follow-up rows, and logs what happened.

## Idempotency

The scheduler runs hourly, a recruiter can trigger it manually during a demo,
and a container restart mid-run leaves partial work. All three must be safe.

Two mechanisms, layered:

1. A **deterministic dedupe bucket** derived from the rule's window, stored on
   the row and covered by `uq_follow_up_idempotency`. This is the real
   guarantee - it lives in the database, not in application logic that a race
   could slip past.
2. A **savepoint per insert**, so a constraint violation rolls back one row
   rather than the whole run. Two replicas firing simultaneously both attempt
   the insert; one wins, the other continues cleanly.

Without this an hourly job would bury the attention queue in duplicates within
a day, which is the fastest way to make recruiters stop reading it.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.deps import ANONYMOUS, Actor
from app.core.logging import get_logger
from app.db.base import utcnow
from app.db.models import AutomationRun, Candidate, FollowUpAction
from app.domain.context import CandidateContext
from app.domain.enums import (
    AutomationRunStatus,
    FollowUpStatus,
    InteractionChannel,
    NextAction,
)
from app.domain.rules import RULES, Rule, RuleOutcome
from app.modules.attention import service as attention_service

logger = get_logger(__name__)
settings = get_settings()

# Arbitrary but fixed key identifying this job class to Postgres advisory
# locking. Must stay stable across deploys or the lock stops working.
_ADVISORY_LOCK_KEY = 8472_1163


def dedupe_bucket(today: date, window_days: int) -> date:
    """Deterministic bucket for the idempotency key.

    A rule with a 1-day window buckets to today, so it may fire once per day.
    A 3-day window buckets into fixed 3-day periods, so paperwork nags do not
    arrive daily.

    Bucketing (rather than "any row in the last N days") is what lets the
    database enforce this with a unique constraint instead of a read-then-write
    that two concurrent runs could both pass.
    """
    if window_days <= 1:
        return today
    ordinal = today.toordinal()
    return date.fromordinal(ordinal - (ordinal % window_days))


async def try_acquire_lock(session: AsyncSession) -> bool:
    """Best-effort single-runner guard across API replicas.

    The scheduler is in-process, so every replica would otherwise fire the same
    job at the same time. A Postgres advisory lock makes exactly one replica
    win; the others skip the run and try again next tick.

    This is a mitigation, not a real distributed scheduler. It is held only for
    the session, so a replica that dies mid-run releases the lock and the work
    is picked up on the next tick - acceptable because every action is
    idempotent. The production answer is an external scheduler dispatching to a
    worker queue, which is written up in the README.

    Returns True on SQLite, which has no advisory locks and no replicas.
    """
    if settings.is_sqlite:
        return True

    try:
        result = await session.execute(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": _ADVISORY_LOCK_KEY}
        )
        return bool(result.scalar())
    except Exception as exc:  # noqa: BLE001 - never let locking break the run
        logger.warning("advisory_lock_unavailable", error=str(exc))
        return True


async def release_lock(session: AsyncSession) -> None:
    if settings.is_sqlite:
        return
    try:
        await session.execute(
            text("SELECT pg_advisory_unlock(:key)"), {"key": _ADVISORY_LOCK_KEY}
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("advisory_unlock_failed", error=str(exc))


async def _create_follow_up(
    session: AsyncSession,
    *,
    candidate: Candidate,
    rule: Rule,
    outcome: RuleOutcome,
    today: date,
) -> bool:
    """Insert one follow-up, or skip if the idempotency key already exists.

    Returns True when a row was created. The savepoint is what confines a
    constraint violation to this single insert.
    """
    bucket = dedupe_bucket(today, rule.dedupe_window_days)

    try:
        async with session.begin_nested():
            session.add(
                FollowUpAction(
                    candidate_id=candidate.id,
                    rule_key=rule.key,
                    dedupe_date=bucket,
                    title=outcome.title,
                    reason=outcome.reason,
                    recommended_action=outcome.action.value,
                    due_date=today + timedelta(days=outcome.due_in_days),
                    status=FollowUpStatus.OPEN.value,
                )
            )
        return True
    except IntegrityError:
        # Already raised for this candidate/rule/bucket. Expected on every run
        # after the first, so this is debug rather than a warning.
        logger.debug(
            "follow_up_deduplicated", candidate_id=candidate.id, rule=rule.key, bucket=str(bucket)
        )
        return False


async def _draft_message_for(session: AsyncSession, candidate: Candidate) -> bool:
    """Draft a personalised message alongside the follow-up.

    The brief asks the automation to "generate a personalized message". It is
    best-effort: a failure here must not prevent the follow-up task being
    created, because the task is what actually gets the candidate contacted.
    """
    from app.modules.ai import service as ai_service

    try:
        await ai_service.generate_message(
            session,
            candidate.id,
            channel=InteractionChannel.WHATSAPP,
            actor=ANONYMOUS,
        )
        return True
    except Exception as exc:  # noqa: BLE001 - the follow-up matters more
        logger.warning("automation_message_draft_failed", candidate_id=candidate.id, error=str(exc))
        return False


async def run_rule(
    session: AsyncSession,
    rule: Rule,
    *,
    today: date,
    trigger: str = "scheduled",
    draft_messages: bool = True,
) -> AutomationRun:
    """Evaluate one rule across all active candidates and record the run."""
    run = AutomationRun(
        rule_key=rule.key,
        trigger=trigger,
        started_at=utcnow(),
        status=AutomationRunStatus.SUCCESS.value,
    )
    session.add(run)
    await session.flush()

    created = 0
    skipped = 0
    scanned = 0

    try:
        candidates = await attention_service.active_candidates(session, today=today)
        contexts = await attention_service.contexts_for(session, candidates, today=today)

        for candidate in candidates:
            ctx: CandidateContext | None = contexts.get(candidate.id)
            if ctx is None:
                continue
            scanned += 1

            if not rule.predicate(ctx, today):
                continue

            outcome = rule.build(ctx, today)
            if await _create_follow_up(
                session, candidate=candidate, rule=rule, outcome=outcome, today=today
            ):
                created += 1
                # Only the urgent-contact rule drafts a message; a paperwork
                # nag does not need bespoke prose, and generating one per
                # overdue stage would burn tokens for no benefit.
                if draft_messages and outcome.action in {
                    NextAction.CALL_CANDIDATE,
                    NextAction.SEND_RELOCATION_SUPPORT,
                }:
                    await _draft_message_for(session, candidate)
            else:
                skipped += 1

    except Exception as exc:  # noqa: BLE001 - the run log must record failures
        run.status = AutomationRunStatus.FAILED.value
        run.error = str(exc)[:1000]
        logger.error("automation_rule_failed", rule=rule.key, error=str(exc), exc_info=True)

    run.finished_at = utcnow()
    run.candidates_scanned = scanned
    run.actions_created = created
    run.actions_skipped = skipped

    await session.commit()

    logger.info(
        "automation_rule_completed",
        rule=rule.key,
        trigger=trigger,
        scanned=scanned,
        created=created,
        skipped=skipped,
        status=run.status,
    )
    return run


async def run_all_rules(
    session: AsyncSession,
    *,
    today: date | None = None,
    trigger: str = "scheduled",
    rule_keys: list[str] | None = None,
    draft_messages: bool = True,
    use_lock: bool = True,
) -> list[AutomationRun]:
    """Run every rule (or a named subset).

    Acquires the advisory lock once for the whole sweep so replicas do not
    interleave rules against a shifting candidate set.
    """
    today = today or date.today()
    selected = [r for r in RULES if not rule_keys or r.key in rule_keys]

    if use_lock and not await try_acquire_lock(session):
        logger.info("automation_skipped_lock_held")
        return []

    try:
        return [
            await run_rule(
                session, rule, today=today, trigger=trigger, draft_messages=draft_messages
            )
            for rule in selected
        ]
    finally:
        if use_lock:
            await release_lock(session)


# --------------------------------------------------------------------------
# Follow-up queries
# --------------------------------------------------------------------------
async def list_follow_ups(
    session: AsyncSession,
    *,
    candidate_id: str | None = None,
    status: FollowUpStatus | None = FollowUpStatus.OPEN,
    limit: int = 100,
) -> list[FollowUpAction]:
    stmt = select(FollowUpAction)
    if candidate_id:
        stmt = stmt.where(FollowUpAction.candidate_id == candidate_id)
    if status:
        stmt = stmt.where(FollowUpAction.status == status.value)
    stmt = stmt.order_by(FollowUpAction.due_date.asc().nulls_last()).limit(limit)
    return list((await session.execute(stmt)).scalars().all())


async def resolve_follow_up(
    session: AsyncSession, follow_up_id: str, *, status: FollowUpStatus, actor: Actor
) -> FollowUpAction:
    from app.core.errors import NotFoundError

    follow_up = await session.get(FollowUpAction, follow_up_id)
    if follow_up is None:
        raise NotFoundError("Follow-up not found.", details={"follow_up_id": follow_up_id})

    follow_up.status = status.value
    follow_up.resolved_by = actor.id
    follow_up.resolved_at = utcnow()
    await session.commit()
    await session.refresh(follow_up)
    return follow_up


async def recent_runs(session: AsyncSession, *, limit: int = 20) -> list[AutomationRun]:
    stmt = select(AutomationRun).order_by(AutomationRun.started_at.desc()).limit(limit)
    return list((await session.execute(stmt)).scalars().all())
