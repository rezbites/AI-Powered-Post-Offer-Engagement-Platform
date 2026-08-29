"""Hybrid joining-risk engine.

Pure: no database, no clock, no network. Everything arrives as a
`CandidateContext` plus an explicit `today`. That constraint is what makes the
highest-consequence logic in the system exhaustively testable in milliseconds -
a risk rule you cannot test in isolation is a rule nobody should trust.

## Why hybrid rather than pure-LLM

A language model asked to output "HIGH" for a candidate cannot be audited,
drifts between model versions, and gives a recruiter nothing to disagree with.
Pure rules, conversely, cannot read *"I am still figuring out relocation and
accommodation"* and understand it as a concern.

So the two are split by what each is actually good at:

* **Rules** own everything countable - days to joining, days of silence,
  overdue stages, unanswered messages. Deterministic and reproducible.
* **The LLM** owns only semantic extraction: turning free text into a closed
  set of typed signals with supporting quotes. It never picks the band.

The band is then computed here, from both. Every contributing factor carries
its own weight, so the number and the explanation are produced by the same
statement and cannot disagree.

## Component caps

Each category is capped so no single dimension can dominate. Without caps a
candidate with six overdue stages would score HIGH on paperwork alone, drowning
out someone who explicitly said they are considering another offer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.domain.context import CandidateContext, RiskAssessment
from app.domain.enums import RiskLevel, SignalType

# --------------------------------------------------------------------------
# Thresholds. Named constants so the reasoning is documented once, and so the
# automation rules can reuse the same numbers rather than re-deriving them.
# --------------------------------------------------------------------------

# Inside this window, silence stops being normal and becomes actionable.
JOINING_CRITICAL_DAYS = 3
JOINING_IMMINENT_DAYS = 7
JOINING_SOON_DAYS = 15

# Matches the brief's worked example: no contact for five days.
SILENCE_THRESHOLD_DAYS = 5
SILENCE_SEVERE_DAYS = 10
# Evidence older than this is stale enough to reduce confidence.
EVIDENCE_STALE_DAYS = 21

# Two unanswered messages could be timing; three is a pattern.
UNANSWERED_OUTBOUND_THRESHOLD = 3

# Weight of the silence-and-imminence interaction term. This is the single
# heaviest contribution in the model, deliberately: it is the exact condition
# the brief singles out for automation, and a candidate starting in days with
# nobody talking to them is the clearest escalation the system can detect.
CRITICAL_WINDOW_WEIGHT = 3.0

# Per-category ceilings.
CAP_TIMING = 2.0
CAP_SILENCE = 2.5
CAP_JOURNEY = 3.0
CAP_SIGNALS = 4.0
FLOOR_SIGNALS = -2.0

# Band boundaries on a 0-10 scale.
BAND_MEDIUM_AT = 3.5
BAND_HIGH_AT = 6.5
SCORE_MAX = 10.0

# Signal weights differ because the underlying situations differ in kind. A
# competing offer is a live threat to the hire; a relocation question is
# usually solvable with support, and treating them alike would be wrong.
SIGNAL_WEIGHTS: dict[SignalType, float] = {
    SignalType.COMPETING_OFFER: 4.0,
    SignalType.COMPENSATION_CONCERN: 2.0,
    SignalType.NOTICE_PERIOD_ISSUE: 1.5,
    SignalType.RELOCATION_CONCERN: 1.5,
    SignalType.LOW_ENTHUSIASM: 1.5,
    # Negative weight: without it, a visibly enthusiastic candidate still
    # accumulates risk from timing alone and clutters the attention queue.
    SignalType.POSITIVE_INTENT: -1.5,
}

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
    """One contributing reason and the weight it adds.

    Text and weight travel together so the "Why?" panel and the numeric score
    are always derived from the same decision.
    """

    text: str
    weight: float


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _timing_factors(ctx: CandidateContext, today: date) -> list[Factor]:
    """Proximity to the joining date."""
    days_out = ctx.days_to_joining(today)

    # Already past the joining date but not marked joined/dropped: this is a
    # data-quality problem the recruiter needs to resolve, not zero risk.
    if days_out < 0:
        return [Factor(f"Joining date passed {abs(days_out)} days ago without an outcome", 2.0)]

    if days_out <= JOINING_CRITICAL_DAYS:
        return [Factor(f"Joining in {days_out} day{'s' if days_out != 1 else ''}", 2.0)]
    if days_out <= JOINING_IMMINENT_DAYS:
        return [Factor(f"Joining in {days_out} days", 1.5)]
    if days_out <= JOINING_SOON_DAYS:
        return [Factor(f"Joining in {days_out} days", 0.5)]
    return []


def _silence_factors(ctx: CandidateContext, today: date) -> list[Factor]:
    """Lack of contact.

    Never-contacted is treated as strictly worse than contacted-recently rather
    than folded into a zero-days default - it is precisely the candidates
    nobody has spoken to who go quiet and disappear.
    """
    factors: list[Factor] = []
    days_quiet = ctx.days_since_interaction(today)

    if days_quiet is None:
        factors.append(Factor("No interaction recorded yet", 2.0))
    elif days_quiet >= SILENCE_SEVERE_DAYS:
        factors.append(Factor(f"No interaction for {days_quiet} days", 2.0))
    elif days_quiet >= SILENCE_THRESHOLD_DAYS:
        factors.append(Factor(f"No interaction for {days_quiet} days", 1.2))

    # Unanswered outbound is a sharper signal than raw silence: it separates
    # "we have not tried" from "we tried and heard nothing back".
    if ctx.unanswered_outbound >= UNANSWERED_OUTBOUND_THRESHOLD:
        factors.append(Factor(f"{ctx.unanswered_outbound} outbound messages with no reply", 1.5))
    elif ctx.unanswered_outbound == 2:
        factors.append(Factor("Two outbound messages with no reply", 0.7))

    return factors


def _journey_factors(ctx: CandidateContext) -> list[Factor]:
    """Stalled progress through the engagement journey."""
    factors: list[Factor] = []

    if ctx.stages_overdue > 0:
        plural = "s" if ctx.stages_overdue != 1 else ""
        factors.append(
            Factor(f"{ctx.stages_overdue} engagement step{plural} overdue", 0.9 * ctx.stages_overdue)
        )

    # Barely started is a different failure from being late on one step.
    if ctx.stages_total and ctx.stages_completed <= 1:
        factors.append(Factor("Engagement journey has barely started", 0.6))

    return factors


def _signal_factors(ctx: CandidateContext) -> list[Factor]:
    """What the candidate actually said, as extracted by the LLM."""
    return [
        Factor(SIGNAL_PHRASES[signal.type], SIGNAL_WEIGHTS[signal.type])
        for signal in ctx.signals
        if signal.type in SIGNAL_WEIGHTS
    ]


def _critical_window_factor(ctx: CandidateContext, today: date) -> list[Factor]:
    """Interaction term: silence *and* an imminent joining date.

    These compound rather than add. Five days of quiet is unremarkable a month
    out and alarming four days before someone is due to start, and a purely
    additive model cannot express that difference. This is also exactly the
    condition the brief's automation rule describes.
    """
    days_out = ctx.days_to_joining(today)
    if not (0 <= days_out <= JOINING_IMMINENT_DAYS):
        return []

    days_quiet = ctx.days_since_interaction(today)
    if days_quiet is None or days_quiet >= SILENCE_THRESHOLD_DAYS:
        return [Factor("Joining imminently with no recent contact", CRITICAL_WINDOW_WEIGHT)]
    return []


def compute_factors(ctx: CandidateContext, *, today: date) -> list[Factor]:
    """All contributing factors, strongest influence first.

    Terminal candidates return nothing: someone who has already joined or
    withdrawn has no forward-looking risk, and surfacing them in an attention
    queue would be pure noise.
    """
    if ctx.is_terminal:
        return []

    factors = [
        *_timing_factors(ctx, today),
        *_silence_factors(ctx, today),
        *_journey_factors(ctx),
        *_signal_factors(ctx),
        *_critical_window_factor(ctx, today),
    ]
    # Absolute weight, so the strongest influence leads whether it raises or
    # lowers risk.
    return sorted(factors, key=lambda f: abs(f.weight), reverse=True)


def score(ctx: CandidateContext, *, today: date) -> float:
    """Weighted risk score on a 0-10 scale.

    Categories are summed independently and each is capped before contributing,
    so no single dimension can saturate the result.
    """
    if ctx.is_terminal:
        return 0.0

    timing = min(sum(f.weight for f in _timing_factors(ctx, today)), CAP_TIMING)
    silence = min(sum(f.weight for f in _silence_factors(ctx, today)), CAP_SILENCE)
    journey = min(sum(f.weight for f in _journey_factors(ctx)), CAP_JOURNEY)
    signals = _clamp(sum(f.weight for f in _signal_factors(ctx)), FLOOR_SIGNALS, CAP_SIGNALS)
    critical = sum(f.weight for f in _critical_window_factor(ctx, today))

    return _clamp(timing + silence + journey + signals + critical, 0.0, SCORE_MAX)


def classify(value: float) -> RiskLevel:
    """Map a score onto a band.

    Boundaries are hand-tuned against realistic scenarios, not learned - there
    are no historical joined/dropped outcomes to calibrate against. This is
    stated plainly in the README as a limitation rather than dressed up.
    """
    if value >= BAND_HIGH_AT:
        return RiskLevel.HIGH
    if value >= BAND_MEDIUM_AT:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def rule_only_band(ctx: CandidateContext, *, today: date) -> RiskLevel:
    """Band from deterministic factors alone, ignoring LLM signals.

    Used by confidence derivation: when the rules and the signal-informed
    assessment agree, the result deserves more confidence than when the whole
    verdict rests on one model-extracted sentence.
    """
    if ctx.is_terminal:
        return RiskLevel.LOW

    timing = min(sum(f.weight for f in _timing_factors(ctx, today)), CAP_TIMING)
    silence = min(sum(f.weight for f in _silence_factors(ctx, today)), CAP_SILENCE)
    journey = min(sum(f.weight for f in _journey_factors(ctx)), CAP_JOURNEY)
    critical = sum(f.weight for f in _critical_window_factor(ctx, today))
    return classify(_clamp(timing + silence + journey + critical, 0.0, SCORE_MAX))


def _rationale(level: RiskLevel, factors: list[Factor]) -> str:
    """One-sentence summary for the UI, built from the top factors."""
    if not factors:
        return "No risk factors detected."

    leading = [f.text.lower() for f in factors if f.weight > 0][:2]
    if not leading:
        return "Positive engagement signals outweigh any risk factors."

    joined = " and ".join(leading)
    prefix = {
        RiskLevel.HIGH: "High risk driven by",
        RiskLevel.MEDIUM: "Moderate risk driven by",
        RiskLevel.LOW: "Low risk, with minor factors:",
    }[level]
    return f"{prefix} {joined}."


def assess(ctx: CandidateContext, *, today: date) -> RiskAssessment | None:
    """Full assessment, or None when risk does not apply.

    None is returned for terminal candidates (joined or dropped out). Callers
    keep whatever risk was last recorded rather than overwriting history with a
    meaningless LOW - the fact that someone who withdrew was previously flagged
    HIGH is exactly the record worth preserving.
    """
    if ctx.is_terminal:
        return None

    # Imported here rather than at module scope to keep the dependency one-way:
    # confidence reads risk, never the reverse.
    from app.domain.confidence import derive_confidence

    factors = compute_factors(ctx, today=today)
    value = score(ctx, today=today)
    level = classify(value)

    return RiskAssessment(
        level=level,
        confidence=derive_confidence(ctx, level=level, today=today),
        score=round(value, 2),
        factors=[f.text for f in factors],
        rationale=_rationale(level, factors),
    )


def explain(ctx: CandidateContext, *, today: date, limit: int | None = None) -> list[str]:
    """Human-readable "Why?" list for the UI."""
    texts = [f.text for f in compute_factors(ctx, today=today)]
    return texts[:limit] if limit else texts
