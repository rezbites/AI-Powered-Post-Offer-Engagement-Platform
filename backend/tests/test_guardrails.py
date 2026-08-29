"""Guardrail tests.

Grounding is the guardrail that matters: schema validation proves output is
well-formed, not that it is true to the input. These pin the tolerance between
"the model normalised punctuation" and "the model invented a quote".
"""

from __future__ import annotations

import pytest

from app.ai.guardrails import check_message_safety, is_grounded
from app.ai.schemas import GeneratedMessageDraft

SOURCE = (
    "I am still figuring out relocation and accommodation. "
    "Thank you for the offer, really looking forward to getting started."
)


class TestGrounding:
    def test_exact_quote_is_grounded(self):
        assert is_grounded("I am still figuring out relocation and accommodation.", SOURCE)

    def test_partial_but_faithful_quote_is_grounded(self):
        assert is_grounded("still figuring out relocation", SOURCE)

    def test_cosmetic_normalisation_is_tolerated(self):
        """Models legitimately fix curly quotes, casing and trailing
        punctuation. Requiring an exact substring would reject honest quotes."""
        assert is_grounded("I AM STILL FIGURING OUT RELOCATION AND ACCOMMODATION", SOURCE)

    def test_invented_quote_is_rejected(self):
        assert not is_grounded("I have accepted an offer from another company.", SOURCE)

    def test_short_quote_must_match_completely(self):
        """With two words, partial overlap carries no information."""
        assert is_grounded("relocation and", SOURCE)
        assert not is_grounded("another offer", SOURCE)

    def test_empty_quote_is_not_grounded(self):
        assert not is_grounded("", SOURCE)
        assert not is_grounded("   ", SOURCE)

    def test_no_source_means_nothing_is_grounded(self):
        """A candidate who has never written to us cannot be quoted."""
        assert not is_grounded("anything at all", "")

    def test_mostly_invented_quote_is_rejected(self):
        """Half-real quotes are the dangerous case: plausible enough to pass a
        skim, wrong enough to mislead."""
        assert not is_grounded(
            "I am still figuring out whether to accept the competing offer instead", SOURCE
        )


class TestMessageSafety:
    def _draft(self, body: str) -> GeneratedMessageDraft:
        return GeneratedMessageDraft(subject="Hello", body=body, tone="warm_professional")

    def test_clean_message_produces_no_warnings(self):
        draft = self._draft("Hi, checking in on how your joining preparations are going.")
        assert check_message_safety(draft) == []

    @pytest.mark.parametrize(
        "body",
        [
            "We will increase your compensation to match.",
            "I guarantee your start date will not move.",
            "I promise we can sort the relocation costs.",
            "Attached is your revised offer.",
        ],
    )
    def test_commitment_language_is_flagged(self, body):
        """A recruiter skim-reading before hitting approve is exactly how an
        accidental promise reaches a candidate."""
        assert check_message_safety(self._draft(body))

    def test_warnings_do_not_block_the_draft(self):
        """The recruiter is the decision-maker. The system makes risk visible;
        it does not silently rewrite someone's communication."""
        draft = self._draft("We guarantee this will work out.")
        warnings = check_message_safety(draft)
        assert warnings
        assert draft.body  # unchanged
