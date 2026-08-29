"""Attention queue tests.

The queue is the product. If its ordering is wrong, a recruiter spends their
morning on the wrong person - so the ordering properties are pinned here.
"""

from __future__ import annotations

from app.domain.attention import build_queue, priority, reasons
from app.domain.enums import CandidateStatus, RiskLevel, SignalType
from tests.conftest import make_context


class TestPriorityOrdering:
    def test_higher_risk_outranks_lower_risk(self, today):
        ctx = make_context()
        high = priority(ctx, level=RiskLevel.HIGH, today=today)
        medium = priority(ctx, level=RiskLevel.MEDIUM, today=today)
        low = priority(ctx, level=RiskLevel.LOW, today=today)
        assert high > medium > low

    def test_sooner_joining_outranks_later_within_a_band(self, today):
        soon = make_context(days_to_joining=2)
        later = make_context(days_to_joining=40)
        assert priority(soon, level=RiskLevel.MEDIUM, today=today) > priority(
            later, level=RiskLevel.MEDIUM, today=today
        )

    def test_longer_silence_outranks_recent_contact(self, today):
        quiet = make_context(days_since_interaction=14)
        recent = make_context(days_since_interaction=1)
        assert priority(quiet, level=RiskLevel.MEDIUM, today=today) > priority(
            recent, level=RiskLevel.MEDIUM, today=today
        )

    def test_never_contacted_ranks_above_merely_quiet(self, today):
        never = make_context(days_since_interaction=None)
        quiet = make_context(days_since_interaction=6)
        assert priority(never, level=RiskLevel.MEDIUM, today=today) > priority(
            quiet, level=RiskLevel.MEDIUM, today=today
        )


class TestOpenFollowUpDiscount:
    def test_candidate_already_being_worked_ranks_lower(self, today):
        """The queue should surface neglected candidates, not repeat ones a
        colleague already has an open task for."""
        unattended = make_context(has_open_follow_up=False)
        attended = make_context(has_open_follow_up=True)
        assert priority(attended, level=RiskLevel.HIGH, today=today) < priority(
            unattended, level=RiskLevel.HIGH, today=today
        )

    def test_discount_does_not_bury_a_high_risk_candidate(self, today):
        """An open task lowers priority but must not push a HIGH-risk
        candidate below a healthy LOW-risk one."""
        attended_high = make_context(has_open_follow_up=True, days_to_joining=5)
        healthy_low = make_context(days_to_joining=60, days_since_interaction=1)
        assert priority(attended_high, level=RiskLevel.HIGH, today=today) > priority(
            healthy_low, level=RiskLevel.LOW, today=today
        )


class TestQueueConstruction:
    def test_terminal_candidates_are_excluded(self, today):
        entries = [
            (make_context(candidate_id="joined", status=CandidateStatus.JOINED), RiskLevel.HIGH),
            (
                make_context(candidate_id="dropped", status=CandidateStatus.DROPPED_OUT),
                RiskLevel.HIGH,
            ),
            (make_context(candidate_id="active"), RiskLevel.MEDIUM),
        ]
        queue = build_queue(entries, today=today)
        assert [item.candidate_id for item in queue] == ["active"]

    def test_queue_is_sorted_by_descending_priority(self, today):
        entries = [
            (make_context(candidate_id="calm", days_to_joining=60), RiskLevel.LOW),
            (
                make_context(candidate_id="urgent", days_to_joining=2, days_since_interaction=9),
                RiskLevel.HIGH,
            ),
            (make_context(candidate_id="middling", days_to_joining=20), RiskLevel.MEDIUM),
        ]
        queue = build_queue(entries, today=today)
        assert [item.candidate_id for item in queue] == ["urgent", "middling", "calm"]
        assert queue[0].priority >= queue[1].priority >= queue[2].priority

    def test_ties_break_deterministically(self, today):
        """A queue whose order wobbles between refreshes is one recruiters
        stop trusting, so ordering must be total."""
        entries = [
            (make_context(candidate_id=f"c{i}", days_to_joining=10), RiskLevel.MEDIUM)
            for i in range(5)
        ]
        first = [i.candidate_id for i in build_queue(entries, today=today)]
        second = [i.candidate_id for i in build_queue(list(reversed(entries)), today=today)]
        assert first == second

    def test_limit_truncates_the_queue(self, today):
        entries = [
            (make_context(candidate_id=f"c{i}", days_to_joining=i), RiskLevel.MEDIUM)
            for i in range(10)
        ]
        assert len(build_queue(entries, today=today, limit=3)) == 3

    def test_min_priority_filters_calm_candidates(self, today):
        entries = [
            (make_context(candidate_id="calm", days_to_joining=90, days_since_interaction=1), RiskLevel.LOW),
            (make_context(candidate_id="urgent", days_to_joining=1, days_since_interaction=10), RiskLevel.HIGH),
        ]
        queue = build_queue(entries, today=today, min_priority=5.0)
        assert [i.candidate_id for i in queue] == ["urgent"]


class TestReasons:
    def test_every_queued_item_states_why(self, today):
        """A queue entry a recruiter cannot justify is one they will ignore."""
        ctx = make_context(
            days_to_joining=4,
            days_since_interaction=7,
            stages_overdue=2,
            signals=[(SignalType.RELOCATION_CONCERN, "housing")],
        )
        queue = build_queue([(ctx, RiskLevel.HIGH)], today=today)
        assert queue[0].reasons

    def test_reasons_mention_imminent_joining_and_silence(self, today):
        ctx = make_context(days_to_joining=3, days_since_interaction=8)
        text = " ".join(reasons(ctx, today=today)).lower()
        assert "joining in 3 days" in text
        assert "no response for 8 days" in text

    def test_negative_signals_appear_but_positive_ones_do_not(self, today):
        ctx = make_context(
            signals=[
                (SignalType.COMPETING_OFFER, "another offer"),
                (SignalType.POSITIVE_INTENT, "excited"),
            ]
        )
        text = " ".join(reasons(ctx, today=today)).lower()
        assert "competing offer" in text
        assert "positive intent" not in text
