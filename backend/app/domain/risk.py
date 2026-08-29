"""Deterministic risk factors.

This module is pure: no database, no clock, no network. Everything it needs
arrives as a `CandidateContext` plus an explicit `today`. That is what makes it
exhaustively unit-testable and what keeps risk logic honest - a rule you cannot
test in isolation is a rule nobody will trust.

Scope note: this file currently provides the *explainable factor* half of the
hybrid risk model - the part that answers "Why?" on the candidate page. Scoring,
band classification and confidence derivation build on these same thresholds
and land alongside them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.domain.context import CandidateContext
from app.domain.enums import SignalType

# Thresholds are named constants rather than inline numbers so the rationale
# for each is documented once and the automation rules can reuse them.

# Inside this window, silence stops being normal and starts being a problem.
JOINING_IMMINENT_DAYS = 7
JOINING_SOON_DAYS = 15
# Matches the brief's worked example: no contact for five days.
SILENCE_THRESHOLD_DAYS = 5
SILENCE_SEVERE_DAYS = 10
# Two unanswered messages could be timing; three is a pattern.
UNANSWERED_OUTBOUND_THRESHOLD = 3

# Human-readable phrasing for each semantic signal, used in the "Why?" list.
SIGNAL_PHRASES: dict[SignalType, str] = {
    SignalType.RELOCATION_CONCERN: "Candidate raised a relocation or accommodation concern",
    SignalType.COMPETING_OFFER: "Candidate mentioned a competing offer",
    SignalType.COMPENSATION_CONCERN: "Candidate raised a compensation concern",
    SignalType.NOTICE_PERIOD_ISSUE: "Candidate flagged a notice-period problem",
    SignalType.LOW_ENTHUSIASM: "Candidate responses show low enthusiasm",
    SignalType.POSITIVE_INTENT: "Candidate expressed positive intent",
}


@dataclass(frozen=True)
class Factor:
    """One contributing reason, with the weight it adds to the risk score.

    Keeping weight and text together means the UI explanation and the numeric
    score can never disagree - they are produced by the same statement.
    """

    text: str
    weight: float


def _timing_factors(ctx: CandidateContext, today: date) -> list[Factor]:
    """Risk arising from proximity to the joining date."""
    factors: list[Factor] = []
    days_out = ctx.days_to_joining(today)

    if 0 <= days_out <= JOINING_IMMINENT_DAYS:
        factors.append(Factor(f"Joining in {days_out} day{'s' if days_out != 1 else ''}", 1.5))
    elif JOINING_IMMINENT_DAYS < days_out <= JOINING_SOON_DAYS:
        factors.append(Factor(f"Joining in {days_out} days", 0.5))

    return factors


def _silence_factors(ctx: CandidateContext, today: date) -> list[Factor]:
    """Risk arising from lack of contact.

    Never contacted at all is treated as strictly worse than contacted-recently,
    rather than being folded into a zero-days default.
    """
    factors: list[Factor] = []
    days_quiet = ctx.days_since_interaction(today)

    if days_quiet is None:
        factors.append(Factor("No interaction recorded yet", 2.0))
        return factors

    if days_quiet >= SILENCE_SEVERE_DAYS:
        factors.append(Factor(f"No interaction for {days_quiet} days", 2.0))
    elif days_quiet >= SILENCE_THRESHOLD_DAYS:
        factors.append(Factor(f"No interaction for {days_quiet} days", 1.2))

    if ctx.unanswered_outbound >= UNANSWERED_OUTBOUND_THRESHOLD:
        factors.append(
            Factor(f"{ctx.unanswered_outbound} outbound messages with no reply", 1.5)
        )
    elif ctx.unanswered_outbound == 2:
        factors.append(Factor("Two outbound messages with no reply", 0.7))

    return factors


def _journey_factors(ctx: CandidateContext) -> list[Factor]:
    """Risk arising from stalled progress through the engagement journey."""
    factors: list[Factor] = []

    if ctx.stages_overdue > 0:
        plural = "s" if ctx.stages_overdue != 1 else ""
        factors.append(
            Factor(f"{ctx.stages_overdue} engagement step{plural} overdue", 0.8 * ctx.stages_overdue)
        )

    # Barely started with the joining date approaching is a distinct problem
    # from being behind on one step.
    if ctx.stages_total and ctx.stages_completed <= 1:
        factors.append(Factor("Engagement journey has barely started", 0.6))

    return factors


def _signal_factors(ctx: CandidateContext) -> list[Factor]:
    """Risk arising from what the candidate actually said.

    Weights differ by signal because the underlying situations differ: a
    competing offer is a live threat to the hire, while a relocation question
    is usually solvable with support.
    """
    weights: dict[SignalType, float] = {
        SignalType.COMPETING_OFFER: 3.0,
        SignalType.COMPENSATION_CONCERN: 2.0,
        SignalType.NOTICE_PERIOD_ISSUE: 1.5,
        SignalType.RELOCATION_CONCERN: 1.5,
        SignalType.LOW_ENTHUSIASM: 1.5,
        # Positive intent reduces risk. Without this, a candidate who is
        # visibly enthusiastic still accumulates risk from timing alone.
        SignalType.POSITIVE_INTENT: -1.5,
    }

    return [
        Factor(SIGNAL_PHRASES[signal.type], weights.get(signal.type, 0.0))
        for signal in ctx.signals
        if signal.type in weights
    ]


def compute_factors(ctx: CandidateContext, *, today: date) -> list[Factor]:
    """All contributing factors, highest-weight first.

    Terminal candidates return nothing: someone who has already joined or
    dropped out has no forward-looking risk, and showing them in an attention
    queue would be noise.
    """
    if ctx.is_terminal:
        return []

    factors = [
        *_timing_factors(ctx, today),
        *_silence_factors(ctx, today),
        *_journey_factors(ctx),
        *_signal_factors(ctx),
    ]
    # Sort by absolute weight so the strongest influence leads, whether it
    # raises or lowers risk.
    return sorted(factors, key=lambda f: abs(f.weight), reverse=True)


def explain(ctx: CandidateContext, *, today: date, limit: int | None = None) -> list[str]:
    """Human-readable "Why?" list for the UI."""
    texts = [f.text for f in compute_factors(ctx, today=today)]
    return texts[:limit] if limit else texts
