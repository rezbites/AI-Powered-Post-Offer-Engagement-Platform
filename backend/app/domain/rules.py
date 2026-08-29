"""Automation rule predicates.

Rules are pure predicates over a `CandidateContext` plus the action they
propose. Keeping the *decision* separate from the *effect* means the conditions
can be exhaustively tested without a database, a scheduler, or a clock - and
the scheduler in `modules/automation` becomes a thin loop that evaluates
predicates and writes rows.

Each rule declares a `dedupe_window_days`, which the persistence layer turns
into the idempotency key. That is what makes an hourly job safe to re-run and
safe to trigger by hand during a demo without burying the queue in duplicates.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable

from app.domain.context import CandidateContext
from app.domain.enums import NextAction, RiskLevel, SignalType
from app.domain.risk import (
    JOINING_IMMINENT_DAYS,
    SILENCE_THRESHOLD_DAYS,
    classify,
    score,
)


@dataclass(frozen=True)
class RuleOutcome:
    """What a rule wants done when it fires."""

    title: str
    reason: str
    action: NextAction
    # Days from today by which a recruiter should have acted.
    due_in_days: int = 1


@dataclass(frozen=True)
class Rule:
    """A named automation rule.

    `predicate` decides whether the rule applies; `build` describes the
    follow-up to create. Both are pure.
    """

    key: str
    description: str
    predicate: Callable[[CandidateContext, date], bool]
    build: Callable[[CandidateContext, date], RuleOutcome]
    # One action per candidate per rule per this many days.
    dedupe_window_days: int = 1


# --------------------------------------------------------------------------
# Rule 1 - the brief's worked example.
# "If a candidate joins in 7 days and has had no interaction in the last 5
#  days, flag the candidate, generate a personalized message, and create a
#  follow-up action for HR."
# --------------------------------------------------------------------------
def _joining_soon_no_contact(ctx: CandidateContext, today: date) -> bool:
    if ctx.is_terminal:
        return False

    days_out = ctx.days_to_joining(today)
    if not (0 <= days_out <= JOINING_IMMINENT_DAYS):
        return False

    days_quiet = ctx.days_since_interaction(today)
    # Never contacted counts as silent. Treating None as "not silent" would
    # skip exactly the candidates in the worst state.
    return days_quiet is None or days_quiet >= SILENCE_THRESHOLD_DAYS


def _build_joining_soon(ctx: CandidateContext, today: date) -> RuleOutcome:
    days_out = ctx.days_to_joining(today)
    days_quiet = ctx.days_since_interaction(today)
    quiet_text = "no interaction on record" if days_quiet is None else f"no interaction for {days_quiet} days"

    return RuleOutcome(
        title=f"Contact {ctx.name} before joining",
        reason=f"Joining in {days_out} day{'s' if days_out != 1 else ''} with {quiet_text}.",
        action=NextAction.CALL_CANDIDATE,
        due_in_days=1,
    )


# --------------------------------------------------------------------------
# Rule 2 - engagement steps past their SLA.
# --------------------------------------------------------------------------
def _stage_overdue(ctx: CandidateContext, today: date) -> bool:
    return not ctx.is_terminal and ctx.stages_overdue > 0


def _build_stage_overdue(ctx: CandidateContext, today: date) -> RuleOutcome:
    plural = "s" if ctx.stages_overdue != 1 else ""
    return RuleOutcome(
        title=f"Unblock {ctx.name}'s engagement journey",
        reason=f"{ctx.stages_overdue} engagement step{plural} past the agreed SLA.",
        action=NextAction.SEND_REMINDER,
        due_in_days=2,
    )


# --------------------------------------------------------------------------
# Rule 3 - high risk with nobody working it.
# --------------------------------------------------------------------------
# Only follow-ups that actually address joining risk suppress an escalation.
# Checking "any open follow-up" was a real bug: a routine paperwork reminder
# from `stage_overdue` would silence the escalation for a candidate about to
# walk, and because that rule fires for most candidates the escalation became
# effectively dead code.
SUPPRESSES_ESCALATION = frozenset(
    {"joining_soon_no_contact", "high_risk_unattended", "manual"}
)


def _high_risk_unattended(ctx: CandidateContext, today: date) -> bool:
    if ctx.is_terminal or ctx.has_open_follow_up_from(SUPPRESSES_ESCALATION):
        return False
    return classify(score(ctx, today=today)) is RiskLevel.HIGH


def _build_high_risk(ctx: CandidateContext, today: date) -> RuleOutcome:
    concerns = [s.type.value.replace("_", " ") for s in ctx.negative_signals]
    detail = f" Signals: {', '.join(concerns)}." if concerns else ""
    return RuleOutcome(
        title=f"Escalate {ctx.name} - high joining risk",
        reason=f"Assessed high risk with no follow-up in progress.{detail}",
        action=NextAction.ESCALATE,
        due_in_days=1,
    )


# --------------------------------------------------------------------------
# Rule 4 - a solvable, specific concern deserves a specific response.
# Distinct from the generic high-risk escalation because relocation support is
# a concrete offer a recruiter can make today.
# --------------------------------------------------------------------------
# A relocation offer is specific enough that only a prior relocation offer
# (or a human already handling it) should suppress it.
SUPPRESSES_RELOCATION = frozenset({"relocation_support", "manual"})


def _relocation_support_needed(ctx: CandidateContext, today: date) -> bool:
    if ctx.is_terminal or ctx.has_open_follow_up_from(SUPPRESSES_RELOCATION):
        return False
    return any(s.type is SignalType.RELOCATION_CONCERN for s in ctx.signals)


def _build_relocation(ctx: CandidateContext, today: date) -> RuleOutcome:
    return RuleOutcome(
        title=f"Send relocation support to {ctx.name}",
        reason="Candidate raised a relocation or accommodation concern.",
        action=NextAction.SEND_RELOCATION_SUPPORT,
        due_in_days=2,
    )


RULES: list[Rule] = [
    Rule(
        key="joining_soon_no_contact",
        description="Joining within 7 days with no interaction in the last 5 days.",
        predicate=_joining_soon_no_contact,
        build=_build_joining_soon,
    ),
    Rule(
        key="stage_overdue",
        description="One or more engagement steps past their SLA.",
        predicate=_stage_overdue,
        build=_build_stage_overdue,
        # Wider window: paperwork does not need a fresh nag every day.
        dedupe_window_days=3,
    ),
    Rule(
        key="high_risk_unattended",
        description="Assessed HIGH risk with no open follow-up.",
        predicate=_high_risk_unattended,
        build=_build_high_risk,
    ),
    Rule(
        key="relocation_support",
        description="Relocation concern detected with no open follow-up.",
        predicate=_relocation_support_needed,
        build=_build_relocation,
        dedupe_window_days=7,
    ),
]

RULES_BY_KEY: dict[str, Rule] = {rule.key: rule for rule in RULES}


def evaluate(ctx: CandidateContext, *, today: date) -> list[tuple[Rule, RuleOutcome]]:
    """Every rule that fires for this candidate, in declaration order."""
    return [(rule, rule.build(ctx, today)) for rule in RULES if rule.predicate(ctx, today)]
