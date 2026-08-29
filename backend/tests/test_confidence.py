"""Confidence derivation tests.

The central property under test is that confidence tracks *evidence quality*,
independently of how alarming the risk band is. Conflating the two is the
failure mode this whole design exists to avoid.
"""

from __future__ import annotations

from app.domain.confidence import (
    CONFIDENCE_CEILING,
    CONFIDENCE_FLOOR,
    derive_confidence,
)
from app.domain.enums import CandidateStatus, RiskLevel, SignalType
from app.domain.risk import assess
from tests.conftest import make_context


class TestEvidenceVolume:
    def test_more_interactions_yield_more_confidence(self, today):
        sparse = make_context(total_interactions=1, inbound_interactions=1)
        rich = make_context(total_interactions=12, inbound_interactions=6)
        assert derive_confidence(rich, level=RiskLevel.LOW, today=today) > derive_confidence(
            sparse, level=RiskLevel.LOW, today=today
        )

    def test_no_interactions_gives_very_low_confidence(self, today):
        ctx = make_context(
            total_interactions=0, inbound_interactions=0, days_since_interaction=None
        )
        assert derive_confidence(ctx, level=RiskLevel.HIGH, today=today) < 0.3


class TestInboundEvidence:
    def test_never_hearing_back_reduces_confidence(self, today):
        """We can count our own messages, but we cannot read intent from
        someone who has never replied."""
        silent = make_context(total_interactions=6, inbound_interactions=0)
        conversational = make_context(total_interactions=6, inbound_interactions=3)
        assert derive_confidence(silent, level=RiskLevel.MEDIUM, today=today) < derive_confidence(
            conversational, level=RiskLevel.MEDIUM, today=today
        )


class TestStaleness:
    def test_stale_evidence_reduces_confidence(self, today):
        fresh = make_context(days_since_interaction=2)
        stale = make_context(days_since_interaction=40)
        assert derive_confidence(stale, level=RiskLevel.MEDIUM, today=today) < derive_confidence(
            fresh, level=RiskLevel.MEDIUM, today=today
        )


class TestQuotedEvidence:
    def test_signals_with_quotes_raise_confidence(self, today):
        """A signal backed by a verbatim quote is checkable against the
        transcript; one without is an unfalsifiable assertion."""
        quoted = make_context(
            signals=[(SignalType.RELOCATION_CONCERN, "I am still figuring out relocation")]
        )
        unquoted = make_context(signals=[(SignalType.RELOCATION_CONCERN, "   ")])
        assert derive_confidence(quoted, level=RiskLevel.MEDIUM, today=today) > derive_confidence(
            unquoted, level=RiskLevel.MEDIUM, today=today
        )


class TestAgreement:
    def test_disagreement_between_halves_lowers_confidence(self, today):
        """When the countable facts look calm but a single extracted sentence
        drives the verdict, the verdict is less certain - and says so."""
        # Healthy structural picture; the band is carried entirely by a signal.
        ctx = make_context(
            days_to_joining=50,
            days_since_interaction=1,
            stages_overdue=0,
            signals=[(SignalType.COMPETING_OFFER, "I have another offer")],
        )
        disagreeing = derive_confidence(ctx, level=RiskLevel.HIGH, today=today)

        # Same evidence, band matching the rules-only view.
        agreeing = derive_confidence(ctx, level=RiskLevel.LOW, today=today)
        assert disagreeing < agreeing


class TestBounds:
    def test_confidence_never_reaches_one(self, today):
        """1.0 is reserved for human overrides, so a recruiter's stated
        judgement always outranks a derived number."""
        ctx = make_context(
            total_interactions=50,
            inbound_interactions=25,
            days_since_interaction=0,
            signals=[(SignalType.POSITIVE_INTENT, "excited")],
        )
        assert derive_confidence(ctx, level=RiskLevel.LOW, today=today) <= CONFIDENCE_CEILING
        assert derive_confidence(ctx, level=RiskLevel.LOW, today=today) < 1.0

    def test_confidence_never_hits_zero(self, today):
        ctx = make_context(
            total_interactions=0, inbound_interactions=0, days_since_interaction=None
        )
        assert derive_confidence(ctx, level=RiskLevel.HIGH, today=today) >= CONFIDENCE_FLOOR

    def test_terminal_candidates_return_the_floor(self, today):
        ctx = make_context(status=CandidateStatus.JOINED)
        assert derive_confidence(ctx, level=RiskLevel.LOW, today=today) == CONFIDENCE_FLOOR


class TestIndependenceFromRisk:
    def test_high_risk_can_hold_low_confidence(self, today):
        """The design claim, asserted directly: risk and confidence are
        orthogonal. One worrying sentence and nothing else must be able to
        produce HIGH risk that the UI can honestly label as weakly supported.
        """
        ctx = make_context(
            days_to_joining=5,
            days_since_interaction=None,
            total_interactions=0,
            inbound_interactions=0,
        )
        result = assess(ctx, today=today)
        assert result.level is RiskLevel.HIGH
        assert result.confidence < 0.5
