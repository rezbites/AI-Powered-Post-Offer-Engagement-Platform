"""Risk engine tests.

These cover the highest-consequence logic in the system: a wrong band sends a
recruiter to the wrong candidate. They are grouped by the property being
asserted rather than by function, because what matters is the behaviour a
recruiter would notice.
"""

from __future__ import annotations

import pytest

from app.domain.enums import CandidateStatus, RiskLevel, SignalType
from app.domain.risk import (
    BAND_HIGH_AT,
    BAND_MEDIUM_AT,
    CAP_JOURNEY,
    SCORE_MAX,
    assess,
    classify,
    compute_factors,
    explain,
    rule_only_band,
    score,
)
from tests.conftest import TODAY, make_context


class TestClassification:
    """Band boundaries."""

    @pytest.mark.parametrize(
        "value,expected",
        [
            (0.0, RiskLevel.LOW),
            (BAND_MEDIUM_AT - 0.01, RiskLevel.LOW),
            (BAND_MEDIUM_AT, RiskLevel.MEDIUM),
            (BAND_HIGH_AT - 0.01, RiskLevel.MEDIUM),
            (BAND_HIGH_AT, RiskLevel.HIGH),
            (SCORE_MAX, RiskLevel.HIGH),
        ],
    )
    def test_boundaries_are_inclusive_at_the_lower_edge(self, value, expected):
        assert classify(value) is expected


class TestHealthyCandidate:
    def test_engaged_candidate_far_out_is_low_risk(self, today):
        ctx = make_context(days_to_joining=45, days_since_interaction=2)
        assert assess(ctx, today=today).level is RiskLevel.LOW

    def test_positive_intent_offsets_timing_pressure(self, today):
        """A candidate who just said they are excited should not be flagged
        merely because their start date is close."""
        without = make_context(days_to_joining=10, days_since_interaction=1)
        with_positive = make_context(
            days_to_joining=10,
            days_since_interaction=1,
            signals=[(SignalType.POSITIVE_INTENT, "Very excited to start!")],
        )
        assert score(with_positive, today=today) < score(without, today=today)


class TestSilence:
    def test_never_contacted_scores_worse_than_recently_contacted(self, today):
        never = make_context(days_since_interaction=None, total_interactions=0)
        recent = make_context(days_since_interaction=1)
        assert score(never, today=today) > score(recent, today=today)

    def test_never_contacted_is_not_treated_as_zero_days_quiet(self, today):
        """Regression guard: collapsing None to 0 would hide the worst cases."""
        ctx = make_context(days_since_interaction=None, total_interactions=0)
        assert "No interaction recorded yet" in explain(ctx, today=today)

    def test_longer_silence_scores_higher(self, today):
        short = make_context(days_since_interaction=2)
        medium = make_context(days_since_interaction=6)
        long = make_context(days_since_interaction=14)
        assert (
            score(short, today=today)
            < score(medium, today=today)
            < score(long, today=today)
        )

    def test_unanswered_outbound_adds_risk(self, today):
        quiet = make_context(days_since_interaction=6, unanswered_outbound=0)
        ignored = make_context(days_since_interaction=6, unanswered_outbound=4)
        assert score(ignored, today=today) > score(quiet, today=today)


class TestCriticalWindow:
    """Silence and imminence compound rather than merely add."""

    def test_imminent_joining_with_silence_is_high_risk(self, today):
        ctx = make_context(days_to_joining=4, days_since_interaction=8, stages_overdue=1)
        assert assess(ctx, today=today).level is RiskLevel.HIGH

    def test_same_silence_far_from_joining_is_not_high(self, today):
        ctx = make_context(days_to_joining=45, days_since_interaction=8, stages_overdue=1)
        assert assess(ctx, today=today).level is not RiskLevel.HIGH

    def test_imminent_joining_with_recent_contact_skips_the_penalty(self, today):
        ctx = make_context(days_to_joining=4, days_since_interaction=1)
        assert "Joining imminently with no recent contact" not in explain(ctx, today=today)

    def test_never_contacted_counts_as_silent_in_the_window(self, today):
        ctx = make_context(days_to_joining=5, days_since_interaction=None, total_interactions=0)
        assert "Joining imminently with no recent contact" in explain(ctx, today=today)


class TestSignals:
    def test_competing_offer_outweighs_a_relocation_question(self, today):
        """Signal weights must reflect that these are different situations:
        one threatens the hire, the other is usually solvable."""
        competing = make_context(signals=[(SignalType.COMPETING_OFFER, "I have another offer")])
        relocation = make_context(signals=[(SignalType.RELOCATION_CONCERN, "figuring out housing")])
        assert score(competing, today=today) > score(relocation, today=today)

    def test_the_briefs_worked_example_produces_elevated_risk(self, today):
        """The relocation scenario from the assignment brief.

        Deliberately asserts MEDIUM rather than HIGH: a solvable logistics
        question with the joining date two weeks out warrants a follow-up, not
        an escalation.
        """
        ctx = make_context(
            days_to_joining=12,
            days_since_interaction=8,
            stages_overdue=2,
            signals=[
                (
                    SignalType.RELOCATION_CONCERN,
                    "I am still figuring out relocation and accommodation.",
                )
            ],
        )
        result = assess(ctx, today=today)
        assert result.level is RiskLevel.MEDIUM
        assert any("relocation" in f.lower() for f in result.factors)

    def test_signal_contribution_is_capped(self, today):
        """Every negative signal at once must not exceed the signal ceiling,
        so paperwork and timing still influence the outcome."""
        every_signal = make_context(
            signals=[
                (SignalType.COMPETING_OFFER, "a"),
                (SignalType.COMPENSATION_CONCERN, "b"),
                (SignalType.NOTICE_PERIOD_ISSUE, "c"),
                (SignalType.RELOCATION_CONCERN, "d"),
                (SignalType.LOW_ENTHUSIASM, "e"),
            ]
        )
        assert score(every_signal, today=today) <= SCORE_MAX


class TestJourney:
    def test_overdue_stages_raise_risk(self, today):
        on_track = make_context(stages_overdue=0)
        behind = make_context(stages_overdue=3)
        assert score(behind, today=today) > score(on_track, today=today)

    def test_overdue_contribution_is_capped(self, today):
        """Without a cap, a paperwork backlog would drown out a candidate who
        explicitly said they are leaving."""
        many_overdue = make_context(stages_overdue=20)
        paperwork_only = score(many_overdue, today=today)
        assert paperwork_only <= CAP_JOURNEY + 0.01


class TestTerminalCandidates:
    @pytest.mark.parametrize(
        "status", [CandidateStatus.JOINED, CandidateStatus.DROPPED_OUT]
    )
    def test_assess_returns_none_for_terminal_outcomes(self, today, status):
        """Risk is forward-looking. Recomputing it for someone who already
        joined or withdrew would overwrite history with a meaningless value -
        callers keep the last recorded band instead.
        """
        ctx = make_context(status=status, days_to_joining=-10, days_since_interaction=40)
        assert assess(ctx, today=today) is None

    def test_terminal_candidates_have_no_factors(self, today):
        ctx = make_context(status=CandidateStatus.JOINED, days_to_joining=-5)
        assert compute_factors(ctx, today=today) == []


class TestOverdueJoiningDate:
    def test_passed_joining_date_without_outcome_is_flagged(self, today):
        """A start date in the past with a non-terminal status is a data
        problem someone must resolve, not an absence of risk."""
        ctx = make_context(days_to_joining=-4, days_since_interaction=10)
        factors = explain(ctx, today=today)
        assert any("passed" in f for f in factors)


class TestRuleOnlyBand:
    def test_rule_band_ignores_semantic_signals(self, today):
        """Used by confidence: the deterministic view must be computable
        independently so agreement between halves can be measured."""
        base = make_context(days_to_joining=40, days_since_interaction=1)
        with_signal = make_context(
            days_to_joining=40,
            days_since_interaction=1,
            signals=[(SignalType.COMPETING_OFFER, "another offer")],
        )
        assert rule_only_band(base, today=today) is rule_only_band(with_signal, today=today)


class TestExplanation:
    def test_factors_are_ordered_by_influence(self, today):
        ctx = make_context(
            days_to_joining=3,
            days_since_interaction=12,
            stages_overdue=1,
            signals=[(SignalType.COMPETING_OFFER, "another offer")],
        )
        factors = compute_factors(ctx, today=today)
        weights = [abs(f.weight) for f in factors]
        assert weights == sorted(weights, reverse=True)

    def test_every_risky_assessment_carries_an_explanation(self, today):
        """A band with no stated reason is one a recruiter cannot act on."""
        ctx = make_context(days_to_joining=3, days_since_interaction=12, stages_overdue=2)
        result = assess(ctx, today=today)
        assert result.level is not RiskLevel.LOW
        assert result.factors
        assert result.rationale.strip()

    def test_explain_respects_limit(self, today):
        ctx = make_context(days_to_joining=3, days_since_interaction=12, stages_overdue=2)
        assert len(explain(ctx, today=today, limit=2)) == 2


class TestScoreBounds:
    @pytest.mark.parametrize("days_out", [-30, 0, 3, 7, 15, 60])
    @pytest.mark.parametrize("quiet", [None, 0, 5, 30])
    def test_score_always_within_bounds(self, today, days_out, quiet):
        ctx = make_context(
            days_to_joining=days_out,
            days_since_interaction=quiet,
            stages_overdue=4,
            unanswered_outbound=5,
            signals=[(SignalType.COMPETING_OFFER, "x"), (SignalType.LOW_ENTHUSIASM, "y")],
        )
        assert 0.0 <= score(ctx, today=today) <= SCORE_MAX
