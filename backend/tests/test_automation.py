"""Automation idempotency tests.

The rule predicates are covered in `test_rules.py`. What is tested here is the
dedupe bucketing that turns those predicates into database rows exactly once
per window - the property that decides whether an hourly job is useful or
whether it floods the attention queue by lunchtime.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.domain.rules import RULES_BY_KEY
from app.modules.automation.service import dedupe_bucket


class TestDedupeBucket:
    def test_daily_window_buckets_to_today(self):
        today = date(2026, 8, 29)
        assert dedupe_bucket(today, 1) == today

    def test_zero_or_negative_window_is_treated_as_daily(self):
        """Defensive: a misconfigured window must not produce a bucket that
        never repeats, which would silence a rule permanently."""
        today = date(2026, 8, 29)
        assert dedupe_bucket(today, 0) == today
        assert dedupe_bucket(today, -5) == today

    def test_multi_day_window_is_stable_within_the_window(self):
        """Every day inside one bucket must map to the same key, otherwise the
        unique constraint would not suppress anything."""
        start = date(2026, 8, 29)
        buckets = {dedupe_bucket(start + timedelta(days=i), 3) for i in range(3)}
        # Three consecutive days can straddle at most two buckets.
        assert len(buckets) <= 2

    def test_multi_day_window_eventually_advances(self):
        """A rule must be able to fire again once its window elapses."""
        start = date(2026, 8, 29)
        first = dedupe_bucket(start, 3)
        later = dedupe_bucket(start + timedelta(days=4), 3)
        assert later > first

    def test_bucket_is_deterministic(self):
        """Two replicas computing the key independently must agree, or the
        constraint cannot deduplicate across processes."""
        today = date(2026, 8, 29)
        assert dedupe_bucket(today, 3) == dedupe_bucket(today, 3)

    @pytest.mark.parametrize("window", [1, 2, 3, 7, 14])
    def test_bucket_never_exceeds_today(self, window):
        """A bucket in the future would let a rule fire twice today."""
        today = date(2026, 8, 29)
        assert dedupe_bucket(today, window) <= today

    @pytest.mark.parametrize("window", [1, 2, 3, 7, 14])
    def test_bucket_stays_within_one_window_of_today(self, window):
        today = date(2026, 8, 29)
        assert (today - dedupe_bucket(today, window)).days < window


class TestRuleWindows:
    def test_urgent_rules_use_a_daily_window(self):
        """A candidate joining in days should be resurfaced every day until
        someone acts."""
        assert RULES_BY_KEY["joining_soon_no_contact"].dedupe_window_days == 1
        assert RULES_BY_KEY["high_risk_unattended"].dedupe_window_days == 1

    def test_low_urgency_rules_use_wider_windows(self):
        """Paperwork reminders every single day are how a queue becomes noise
        that recruiters learn to ignore."""
        assert RULES_BY_KEY["stage_overdue"].dedupe_window_days > 1
        assert RULES_BY_KEY["relocation_support"].dedupe_window_days > 1
