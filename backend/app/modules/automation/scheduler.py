"""In-process job scheduler.

## The honest limitation

APScheduler runs inside the API process. With more than one API replica, every
replica fires the same job at the same tick. That is a real flaw, and it is
named here rather than buried.

Two things make it survivable rather than fatal:

* **Every action is idempotent.** The follow-up unique constraint means a
  duplicate run creates nothing, so the worst case is wasted work.
* **A Postgres advisory lock** (see `service.try_acquire_lock`) lets exactly
  one replica win each tick.

That is a mitigation, not a distributed scheduler. The production shape is an
external trigger - Cloud Scheduler, Kubernetes CronJob, or Celery beat -
dispatching to a worker pool that does not share a process with the request
path. It is written up in the README, because "why is your scheduler in the web
server?" is the first question anyone will ask.

## Why in-process anyway

On a one-day budget, Redis plus a broker plus a worker deployment is three more
things to run, explain and debug, in exchange for correctness at a replica
count this system does not yet have. The trade is deliberate and reversible:
`run_all_rules` is a plain function, so moving it behind a worker is a
scheduling change, not a rewrite.
"""

from __future__ import annotations

from datetime import date

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import SessionLocal
from app.modules.automation import service

logger = get_logger(__name__)
settings = get_settings()

_scheduler: AsyncIOScheduler | None = None

JOB_ID = "engagement_rules"


async def _tick() -> None:
    """One scheduled sweep.

    Opens its own session: the scheduler runs outside any request, so there is
    no request-scoped session to borrow. Exceptions are swallowed and logged -
    an unhandled error would remove the job from the scheduler entirely, so a
    single bad tick would silently disable automation forever.
    """
    try:
        async with SessionLocal() as session:
            runs = await service.run_all_rules(
                session, today=date.today(), trigger="scheduled"
            )
        if runs:
            logger.info(
                "automation_tick_completed",
                rules=len(runs),
                created=sum(r.actions_created for r in runs),
            )
    except Exception as exc:  # noqa: BLE001 - a bad tick must not kill the job
        logger.error("automation_tick_failed", error=str(exc), exc_info=True)


def start_scheduler() -> AsyncIOScheduler | None:
    """Start the scheduler if automation is enabled."""
    global _scheduler

    if not settings.automation_enabled:
        logger.info("automation_disabled", note="Use POST /automation/run to trigger manually.")
        return None

    if _scheduler is not None:
        return _scheduler

    _scheduler = AsyncIOScheduler(timezone="UTC")
    _scheduler.add_job(
        _tick,
        trigger=IntervalTrigger(minutes=settings.automation_interval_minutes),
        id=JOB_ID,
        # If a tick is missed (restart, long GC pause) run once on recovery
        # rather than firing every missed interval in a burst.
        coalesce=True,
        max_instances=1,
        # Grace period so a tick delayed by a slow startup still runs instead
        # of being silently dropped.
        misfire_grace_time=300,
    )
    _scheduler.start()

    logger.info(
        "automation_scheduler_started",
        interval_minutes=settings.automation_interval_minutes,
    )
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("automation_scheduler_stopped")


def scheduler_status() -> dict[str, object]:
    """Reported by the automation endpoints so an operator can confirm the job
    is actually scheduled rather than assuming it from config."""
    if _scheduler is None:
        return {"running": False, "enabled": settings.automation_enabled, "next_run": None}

    job = _scheduler.get_job(JOB_ID)
    return {
        "running": _scheduler.running,
        "enabled": settings.automation_enabled,
        "interval_minutes": settings.automation_interval_minutes,
        "next_run": job.next_run_time.isoformat() if job and job.next_run_time else None,
    }
