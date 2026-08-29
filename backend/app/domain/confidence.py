"""Confidence derivation.

Confidence answers a different question from risk. Risk asks *how likely is
this candidate not to join*; confidence asks *how much should you trust that
answer*. A candidate can legitimately be HIGH risk at 45% confidence - one
worrying sentence and nothing else to go on - and a recruiter deciding where to
spend an afternoon needs to see both numbers.

## Why the model does not report its own confidence

The obvious implementation is to add a `confidence` field to the LLM schema and
use whatever it returns. That was rejected. Self-reported LLM confidence is
poorly calibrated: models are fluent and consistent in expressing certainty
regardless of whether they are right, and the number tracks phrasing more than
evidence. Worse, it is unfalsifiable - there is nothing to check it against.

So confidence is computed from properties that are actually observable, and
every contribution is returned alongside the number so the arithmetic can be
inspected in the product rather than taken on faith.

## Calibration

The components are sized so that a realistic strong case lands near 0.85 and
the ceiling is genuinely hard to reach.

An earlier version summed to 1.05 for any candidate with six messages, quoted
signals and rule/model agreement - so it always clamped to the 0.95 ceiling.
That made 0.95 the *default* for well-evidenced candidates rather than an
exceptional result, which overstated what this system can know. The components
below deliberately do not saturate.

## What this number is not

It is an **uncalibrated ordinal heuristic**, not a probability. 0.85 does not
mean "correct 85% of the time"; it means "better supported than 0.60". Genuine
calibration needs historical joined/dropped outcomes to fit against, and this
system has none.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.domain.context import CandidateContext
from app.domain.enums import RiskLevel
from app.domain.risk import EVIDENCE_STALE_DAYS, rule_only_band

# Base confidence by volume of evidence. Coarse on purpose: pretending to
# distinguish seven messages from eight would be false precision.
_VOLUME_BASE: list[tuple[int, float]] = [
    (0, 0.15),  # nothing but structural facts (dates, stages)
    (2, 0.35),
    (5, 0.55),
]
_VOLUME_BASE_MAX = 0.70

# Multipliers for evidence-quality problems.
_NO_INBOUND_PENALTY = 0.75
_STALE_EVIDENCE_PENALTY = 0.85

# Corroboration between the deterministic and signal-informed views.
_AGREEMENT_BONUS = 0.07
_DISAGREEMENT_PENALTY = 0.15

# Each quoted signal adds a little, capped - ten quotes are not ten times as
# convincing as one.
_QUOTE_BONUS_EACH = 0.04
_QUOTE_BONUS_CAP = 0.08

CONFIDENCE_FLOOR = 0.05
# Reserved rather than routine. The best realistic case (0.70 + 0.08 + 0.07)
# reaches 0.85, so the ceiling is not something ordinary evidence hits.
CONFIDENCE_CEILING = 0.90


@dataclass(frozen=True)
class ConfidenceBreakdown:
    """The value plus the arithmetic that produced it.

    Returned so the UI can answer "why that number?" without anyone reading
    this file. A confidence figure a recruiter cannot interrogate is one they
    are right to ignore.
    """

    value: float
    factors: list[str]


def _volume_base(total_interactions: int) -> float:
    for threshold, value in _VOLUME_BASE:
        if total_interactions <= threshold:
            return value
    return _VOLUME_BASE_MAX


def explain_confidence(
    ctx: CandidateContext, *, level: RiskLevel, today: date
) -> ConfidenceBreakdown:
    """Confidence in `level`, with a human-readable derivation."""
    if ctx.is_terminal:
        return ConfidenceBreakdown(
            CONFIDENCE_FLOOR, ["Candidate has reached a final outcome"]
        )

    factors: list[str] = []

    confidence = _volume_base(ctx.total_interactions)
    factors.append(
        f"{ctx.total_interactions} message{'s' if ctx.total_interactions != 1 else ''} "
        f"on record (+{confidence:.2f})"
    )

    # We have talked at them but never heard back: intent is unknowable, so any
    # reading of it is weakly supported.
    if ctx.inbound_interactions == 0:
        before = confidence
        confidence *= _NO_INBOUND_PENALTY
        factors.append(f"candidate has never replied ({before:.2f} -> {confidence:.2f})")

    days_quiet = ctx.days_since_interaction(today)
    if days_quiet is None or days_quiet > EVIDENCE_STALE_DAYS:
        before = confidence
        confidence *= _STALE_EVIDENCE_PENALTY
        factors.append(f"evidence is stale ({before:.2f} -> {confidence:.2f})")

    # Signals backed by a verbatim quote are checkable against the transcript;
    # signals without one are unfalsifiable and earn nothing.
    quoted = sum(1 for s in ctx.signals if s.evidence.strip())
    if quoted:
        bonus = min(quoted * _QUOTE_BONUS_EACH, _QUOTE_BONUS_CAP)
        confidence += bonus
        factors.append(f"{quoted} signal{'s' if quoted != 1 else ''} quoted verbatim (+{bonus:.2f})")

    if ctx.signals:
        rules_band = rule_only_band(ctx, today=today)
        if rules_band is level:
            confidence += _AGREEMENT_BONUS
            factors.append(f"rules and model agree on {level.value} (+{_AGREEMENT_BONUS:.2f})")
        elif abs(rules_band.rank - level.rank) >= 1:
            confidence -= _DISAGREEMENT_PENALTY
            factors.append(
                f"rules say {rules_band.value}, model says {level.value} "
                f"(-{_DISAGREEMENT_PENALTY:.2f})"
            )

    raw = confidence
    final = round(max(CONFIDENCE_FLOOR, min(CONFIDENCE_CEILING, raw)), 2)
    if raw > CONFIDENCE_CEILING:
        factors.append(f"capped at {CONFIDENCE_CEILING:.2f}")

    return ConfidenceBreakdown(final, factors)


def derive_confidence(ctx: CandidateContext, *, level: RiskLevel, today: date) -> float:
    """Confidence only. Thin wrapper for callers that do not need the workings."""
    return explain_confidence(ctx, level=level, today=today).value
