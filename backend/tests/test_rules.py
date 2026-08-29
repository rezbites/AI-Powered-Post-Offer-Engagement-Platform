"""Automation rule tests.

The first rule is the brief's worked example, so its boundaries are pinned
exactly. Off-by-one errors here mean either missing a candidate on their last
actionable day, or nagging one who was contacted yesterday.
"""

from __future__ import annotations

import pytest

from app.domain.enums import CandidateStatus, NextAction, SignalType
from app.domain.rules import RULES, RULES_BY_KEY, evaluate
from tests.conftest import make_context


class TestJoiningSoonNoContact:
    """The brief: joining within 7 days AND no interaction in the last 5."""

    rule = RULES_BY_KEY["joining_soon_no_contact"]

    @pytest.mark.parametrize("days_out,quiet,expected", [
        (7, 5, True),    # both conditions exactly at the boundary
        (7, 4, False),   # contacted one day inside the window
        (8, 10, False),  # joining one day outside the window
        (0, 5, True),    # joining today
        (3, 30, True),
        (-1, 30, False), # already past; a different rule's problem
        (2, 0, False),   # spoke to them today
    ])
    def test_boundaries(self, today, days_out, quiet, expected):
        ctx = make_context(days_to_joining=days_out, days_since_interaction=quiet)
        assert self.rule.predicate(ctx, today) is expected

    def test_never_contacted_counts_as_silent(self, today):
        """Treating None as 'not silent' would skip exactly the candidates in
        the worst state."""
        ctx = make_context(
            days_to_joining=3, days_since_interaction=None, total_interactions=0
        )
        assert self.rule.predicate(ctx, today) is True

    @pytest.mark.parametrize("status", [CandidateStatus.JOINED, CandidateStatus.DROPPED_OUT])
    def test_terminal_candidates_never_fire(self, today, status):
        ctx = make_context(status=status, days_to_joining=3, days_since_interaction=30)
        assert self.rule.predicate(ctx, today) is False

    def test_outcome_explains_itself(self, today):
        ctx = make_context(days_to_joining=4, days_since_interaction=9, name="Rahul Sharma")
        outcome = self.rule.build(ctx, today)
        assert "Rahul Sharma" in outcome.title
        assert "4 days" in outcome.reason
        assert "9 days" in outcome.reason
        assert outcome.action is NextAction.CALL_CANDIDATE


class TestStageOverdue:
    rule = RULES_BY_KEY["stage_overdue"]

    def test_fires_when_a_step_is_late(self, today):
        assert self.rule.predicate(make_context(stages_overdue=1), today) is True

    def test_does_not_fire_when_on_track(self, today):
        assert self.rule.predicate(make_context(stages_overdue=0), today) is False

    def test_uses_a_wider_dedupe_window_than_urgent_rules(self):
        """Paperwork does not warrant a fresh nag every single day."""
        assert self.rule.dedupe_window_days > RULES_BY_KEY["joining_soon_no_contact"].dedupe_window_days


class TestHighRiskUnattended:
    rule = RULES_BY_KEY["high_risk_unattended"]

    def test_fires_for_high_risk_with_no_open_task(self, today):
        ctx = make_context(
            days_to_joining=3,
            days_since_interaction=12,
            stages_overdue=2,
            has_open_follow_up=False,
        )
        assert self.rule.predicate(ctx, today) is True

    def test_suppressed_when_someone_is_already_on_it(self, today):
        """Prevents the queue filling with escalations for candidates a
        recruiter is already actively working."""
        ctx = make_context(
            days_to_joining=3,
            days_since_interaction=12,
            stages_overdue=2,
            has_open_follow_up=True,
        )
        assert self.rule.predicate(ctx, today) is False

    def test_does_not_fire_for_healthy_candidates(self, today):
        ctx = make_context(days_to_joining=60, days_since_interaction=1)
        assert self.rule.predicate(ctx, today) is False

    def test_a_paperwork_reminder_does_not_silence_an_escalation(self, today):
        """Regression. Suppressing on *any* open follow-up made this rule dead
        code: `stage_overdue` fires for most candidates, so a routine document
        nag would silence the escalation for someone about to walk.
        """
        ctx = make_context(
            days_to_joining=3,
            days_since_interaction=12,
            stages_overdue=2,
            open_follow_up_rules=frozenset({"stage_overdue"}),
        )
        assert self.rule.predicate(ctx, today) is True

    def test_an_urgent_contact_task_does_silence_an_escalation(self, today):
        """The converse: if someone is already tasked with calling this
        candidate today, a second escalation adds noise, not value."""
        ctx = make_context(
            days_to_joining=3,
            days_since_interaction=12,
            stages_overdue=2,
            open_follow_up_rules=frozenset({"joining_soon_no_contact"}),
        )
        assert self.rule.predicate(ctx, today) is False


class TestRelocationSupport:
    rule = RULES_BY_KEY["relocation_support"]

    def test_fires_on_the_briefs_example_signal(self, today):
        ctx = make_context(
            signals=[
                (
                    SignalType.RELOCATION_CONCERN,
                    "I am still figuring out relocation and accommodation.",
                )
            ]
        )
        assert self.rule.predicate(ctx, today) is True

    def test_proposes_relocation_support_specifically(self, today):
        """A specific, solvable concern deserves a specific response rather
        than a generic escalation."""
        ctx = make_context(signals=[(SignalType.RELOCATION_CONCERN, "housing")])
        assert self.rule.build(ctx, today).action is NextAction.SEND_RELOCATION_SUPPORT

    def test_does_not_fire_without_the_signal(self, today):
        ctx = make_context(signals=[(SignalType.COMPETING_OFFER, "another offer")])
        assert self.rule.predicate(ctx, today) is False


class TestEvaluate:
    def test_multiple_rules_can_fire_together(self, today):
        ctx = make_context(
            days_to_joining=3,
            days_since_interaction=10,
            stages_overdue=2,
            signals=[(SignalType.RELOCATION_CONCERN, "housing")],
        )
        fired = {rule.key for rule, _ in evaluate(ctx, today=today)}
        assert "joining_soon_no_contact" in fired
        assert "stage_overdue" in fired

    def test_healthy_candidate_triggers_nothing(self, today):
        ctx = make_context(days_to_joining=60, days_since_interaction=1, stages_overdue=0)
        assert evaluate(ctx, today=today) == []

    @pytest.mark.parametrize("status", [CandidateStatus.JOINED, CandidateStatus.DROPPED_OUT])
    def test_no_rule_fires_for_terminal_candidates(self, today, status):
        """A blanket guarantee: nobody who has already joined or withdrawn
        should ever generate a follow-up task."""
        ctx = make_context(
            status=status,
            days_to_joining=-5,
            days_since_interaction=40,
            stages_overdue=3,
            signals=[(SignalType.COMPETING_OFFER, "x")],
        )
        assert evaluate(ctx, today=today) == []


class TestRuleRegistry:
    def test_rule_keys_are_unique(self):
        keys = [r.key for r in RULES]
        assert len(keys) == len(set(keys))

    def test_every_rule_declares_a_dedupe_window(self):
        """The dedupe window becomes the idempotency key; a zero or missing
        window would let an hourly job create duplicate follow-ups."""
        assert all(r.dedupe_window_days >= 1 for r in RULES)
