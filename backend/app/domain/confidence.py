"""Confidence derivation.

Confidence answers a different question from risk. Risk asks *how likely is
this candidate not to join*; confidence asks *how much should you trust that
answer*. A candidate can legitimately be HIGH risk at 55% confidence - one
worrying sentence and nothing else to go on - and a recruiter deciding where to
spend an afternoon needs to see both numbers.

## Why the model does not report its own confidence

The obvious implementation is to add a `confidence` field to the LLM schema and
use whatever it returns. That was rejected. Self-reported LLM confidence is
poorly calibrated: models are fluent and consistent in expressing certainty
regardless of whether they are right, and the number tracks phrasing more than
evidence. Worse, it is unfalsifiable - there is nothing to check it against.

So confidence is computed here from properties that are actually observable:

* **how much evidence exists** - two messages support less than twelve;
* **whether the candidate ever replied** - we cannot read intent from someone
  who has never written to us;
* **how fresh that evidence is** - a concern from five weeks ago may be resolved;
* **whether extracted signals carry supporting quotes** - a signal with a
  verbatim quote is checkable, one without is an assertion;
* **whether the rule layer and the signal-informed verdict agree** - when the
  countable facts and the model's reading point the same way, the conclusion is
  more robust than when either carries it alone.

## What this number is not

It is an **uncalibrated ordinal heuristic**, not a probability. 0.8 does not
mean "correct 80% of the time"; it means "better supported than 0.6". Genuine
calibration needs historical joined/dropped outcomes to fit against, and this
system has none. The UI therefore labels it "heuristic" and the README says so
outright, because a fake probability is worse than an honest ordering.
"""

from __future__ import annotations

from datetime import date

from app.domain.context import CandidateContext
from app.domain.enums import RiskLevel
from app.domain.risk import EVIDENCE_STALE_DAYS, rule_only_band

# Base confidence by volume of evidence. The jumps are deliberately coarse:
# pretending to distinguish 7 messages from 8 would be false precision.
_VOLUME_BASE: list[tuple[int, float]] = [
    (0, 0.20),  # nothing but structural facts (dates, stages)
    (2, 0.45),
    (5, 0.70),
]
_VOLUME_BASE_MAX = 0.85

# Multipliers for evidence-quality problems.
_NO_INBOUND_PENALTY = 0.75
_STALE_EVIDENCE_PENALTY = 0.85

# Agreement between the deterministic and signal-informed views.
_AGREEMENT_BONUS = 0.10
_DISAGREEMENT_PENALTY = 0.15

# Each quoted signal adds a little, capped - ten quotes are not ten times as
# convincing as one.
_QUOTE_BONUS_EACH = 0.05
_QUOTE_BONUS_CAP = 0.10

# Reserved: 1.0 is used exclusively for human overrides, so a recruiter's
# stated judgement is always visibly more certain than any derived number.
CONFIDENCE_FLOOR = 0.05
CONFIDENCE_CEILING = 0.95


def _volume_base(total_interactions: int) -> float:
    for threshold, value in _VOLUME_BASE:
        if total_interactions <= threshold:
            return value
    return _VOLUME_BASE_MAX


def derive_confidence(
    ctx: CandidateContext, *, level: RiskLevel, today: date
) -> float:
    """Confidence in `level`, on a 0.05-0.95 ordinal scale.

    `level` is the blended (signal-informed) band. It is compared against the
    rules-only band to detect disagreement.
    """
    # A terminal outcome is observed fact, not inference - but risk itself does
    # not apply, so callers should not be asking. Return the floor rather than
    # implying a confident prediction.
    if ctx.is_terminal:
        return CONFIDENCE_FLOOR

    confidence = _volume_base(ctx.total_interactions)

    # We have talked at them but never heard back: intent is unknowable, so
    # any reading of it is weakly supported.
    if ctx.inbound_interactions == 0:
        confidence *= _NO_INBOUND_PENALTY

    days_quiet = ctx.days_since_interaction(today)
    if days_quiet is None or days_quiet > EVIDENCE_STALE_DAYS:
        confidence *= _STALE_EVIDENCE_PENALTY

    # Signals backed by a verbatim quote are checkable against the transcript;
    # signals without one are unfalsifiable and earn nothing.
    quoted = sum(1 for s in ctx.signals if s.evidence.strip())
    confidence += min(quoted * _QUOTE_BONUS_EACH, _QUOTE_BONUS_CAP)

    # Corroboration between the two halves of the hybrid model.
    if ctx.signals:
        rules_band = rule_only_band(ctx, today=today)
        if rules_band is level:
            confidence += _AGREEMENT_BONUS
        elif abs(rules_band.rank - level.rank) >= 1:
            confidence -= _DISAGREEMENT_PENALTY

    return round(max(CONFIDENCE_FLOOR, min(CONFIDENCE_CEILING, confidence)), 2)
