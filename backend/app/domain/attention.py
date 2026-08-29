"""Attention queue ranking - "who needs me today?"

This is the first thing a recruiter sees, and it is deliberately **not** an LLM
call. Ordering a work queue must be deterministic, instant, and identical on
every refresh; a model that reshuffles the list between page loads, costs money
per render, and cannot explain its ordering would be strictly worse than
arithmetic. The LLM's contribution is upstream - it supplies the signals that
feed risk - and stops there.

Ranking blends four things a recruiter actually weighs:

* **risk band** - the headline judgement;
* **urgency** - how soon the candidate is due to start;
* **silence** - how long since anyone spoke to them;
* **stalled journey** - overdue engagement steps.

and then subtracts for work already in flight, so the queue surfaces neglected
candidates rather than repeating ones a colleague is already handling.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.domain.context import CandidateContext
from app.domain.enums import RiskLevel
from app.domain.risk import (
    JOINING_CRITICAL_DAYS,
    JOINING_IMMINENT_DAYS,
    JOINING_SOON_DAYS,
    SILENCE_SEVERE_DAYS,
    SILENCE_THRESHOLD_DAYS,
)

# Risk dominates the ordering; the remaining terms break ties between
# candidates in the same band.
RISK_MULTIPLIER = 2.0

URGENCY_CRITICAL = 3.0
URGENCY_IMMINENT = 2.0
URGENCY_SOON = 1.0

SILENCE_SEVERE_WEIGHT = 1.5
SILENCE_WEIGHT = 0.8
NEVER_CONTACTED_WEIGHT = 2.0

OVERDUE_PER_STAGE = 0.5
OVERDUE_CAP = 1.5

# Someone is already on it. Not zero - an open task on a HIGH-risk candidate
# still deserves visibility - but enough to let neglected candidates rise.
OPEN_FOLLOW_UP_DISCOUNT = 1.0


@dataclass(frozen=True)
class AttentionItem:
    """One ranked entry, carrying the reasons that put it there."""

    candidate_id: str
    name: str
    priority: float
    risk_level: RiskLevel
    days_to_joining: int
    reasons: list[str]


def _urgency(days_out: int) -> float:
    # Past-due with no recorded outcome ranks alongside the most urgent: it
    # means someone was due to start and nobody closed the loop.
    if days_out < 0:
        return URGENCY_CRITICAL
    if days_out <= JOINING_CRITICAL_DAYS:
        return URGENCY_CRITICAL
    if days_out <= JOINING_IMMINENT_DAYS:
        return URGENCY_IMMINENT
    if days_out <= JOINING_SOON_DAYS:
        return URGENCY_SOON
    return 0.0


def _silence_weight(days_quiet: int | None) -> float:
    if days_quiet is None:
        return NEVER_CONTACTED_WEIGHT
    if days_quiet >= SILENCE_SEVERE_DAYS:
        return SILENCE_SEVERE_WEIGHT
    if days_quiet >= SILENCE_THRESHOLD_DAYS:
        return SILENCE_WEIGHT
    return 0.0


def priority(ctx: CandidateContext, *, level: RiskLevel, today: date) -> float:
    """Ranking score. Higher means more urgent.

    Terminal candidates score zero and are filtered out by `build_queue`.
    """
    if ctx.is_terminal:
        return 0.0

    days_out = ctx.days_to_joining(today)
    days_quiet = ctx.days_since_interaction(today)

    total = (level.rank + 1) * RISK_MULTIPLIER
    total += _urgency(days_out)
    total += _silence_weight(days_quiet)
    total += min(ctx.stages_overdue * OVERDUE_PER_STAGE, OVERDUE_CAP)

    if ctx.has_open_follow_up:
        total -= OPEN_FOLLOW_UP_DISCOUNT

    return round(max(0.0, total), 2)


def reasons(ctx: CandidateContext, *, today: date) -> list[str]:
    """Short phrases explaining the placement.

    Intentionally terser than the risk "Why?" panel - a queue entry needs a
    glanceable line, not a full breakdown.
    """
    out: list[str] = []
    days_out = ctx.days_to_joining(today)
    days_quiet = ctx.days_since_interaction(today)

    if days_out < 0:
        out.append(f"Joining date passed {abs(days_out)} days ago")
    elif days_out <= JOINING_IMMINENT_DAYS:
        out.append(f"Joining in {days_out} day{'s' if days_out != 1 else ''}")

    if days_quiet is None:
        out.append("Never contacted")
    elif days_quiet >= SILENCE_THRESHOLD_DAYS:
        out.append(f"No response for {days_quiet} days")

    for signal in ctx.negative_signals:
        out.append(signal.type.value.replace("_", " ").capitalize())

    if ctx.stages_overdue:
        plural = "s" if ctx.stages_overdue != 1 else ""
        out.append(f"{ctx.stages_overdue} step{plural} overdue")

    return out


def build_queue(
    entries: list[tuple[CandidateContext, RiskLevel]],
    *,
    today: date,
    limit: int | None = None,
    min_priority: float = 0.0,
) -> list[AttentionItem]:
    """Rank candidates into the attention queue.

    Terminal candidates are excluded outright. Ties break on the sooner joining
    date, so the ordering is total and stable across refreshes - a queue whose
    order wobbles between loads is one recruiters stop trusting.
    """
    items: list[tuple[AttentionItem, int]] = []

    for ctx, level in entries:
        if ctx.is_terminal:
            continue
        value = priority(ctx, level=level, today=today)
        if value < min_priority:
            continue
        days_out = ctx.days_to_joining(today)
        items.append(
            (
                AttentionItem(
                    candidate_id=ctx.candidate_id,
                    name=ctx.name,
                    priority=value,
                    risk_level=level,
                    days_to_joining=days_out,
                    reasons=reasons(ctx, today=today),
                ),
                days_out,
            )
        )

    items.sort(key=lambda pair: (-pair[0].priority, pair[1], pair[0].candidate_id))
    ranked = [item for item, _ in items]
    return ranked[:limit] if limit else ranked
