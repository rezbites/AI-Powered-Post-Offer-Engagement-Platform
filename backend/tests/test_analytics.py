"""Analytics definition tests.

The aggregate SQL needs a database, but the *definitions* - which are where the
judgement lives - are pure and testable here. Getting a denominator wrong is
the kind of bug that makes a dashboard confidently lie, and nobody notices
because the number still looks plausible.
"""

from __future__ import annotations

import pytest

from app.modules.analytics.repository import conversion_rate


class TestConversionRate:
    def test_normal_case(self):
        assert conversion_rate(joined=8, resolved=12) == 66.7

    def test_perfect_conversion(self):
        assert conversion_rate(joined=3, resolved=3) == 100.0

    def test_total_loss_is_zero_not_none(self):
        """A recruiter who genuinely lost every resolved candidate must show
        0%, and that is a real, different fact from having no data."""
        assert conversion_rate(joined=0, resolved=4) == 0.0

    def test_no_resolved_candidates_returns_none(self):
        """The bug this guards: returning 0.0 here rendered as "0% conversion"
        for a recruiter whose candidates simply have not joined *yet*. In an HR
        tool where these numbers shape how people are judged, "no data" and
        "lost everyone" must not look identical.
        """
        assert conversion_rate(joined=0, resolved=0) is None

    def test_none_and_zero_are_distinguishable(self):
        """Stated as a property, because the two are easy to conflate in a
        truthiness check and the failure is silent."""
        no_data = conversion_rate(joined=0, resolved=0)
        total_loss = conversion_rate(joined=0, resolved=5)

        assert no_data is None
        assert total_loss == 0.0
        assert no_data != total_loss

    @pytest.mark.parametrize("resolved", [0, -1])
    def test_non_positive_denominator_never_divides(self, resolved):
        """Defensive: a negative count would indicate corrupt data, and
        crashing the whole dashboard over it helps nobody."""
        assert conversion_rate(joined=0, resolved=resolved) is None

    def test_rate_is_rounded_to_one_decimal(self):
        """False precision invites over-reading a noisy sample."""
        assert conversion_rate(joined=1, resolved=3) == 33.3
