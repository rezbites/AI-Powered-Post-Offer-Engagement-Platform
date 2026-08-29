"""Output guardrails applied after schema validation.

Schema validation proves the output is *well-formed*. It says nothing about
whether the content is *true to the input*. These checks close that gap.

The important one is grounding: a model can produce a perfectly valid
`relocation_concern` signal quoting a sentence the candidate never wrote. That
is a hallucination which passes every type check, and it is exactly the failure
that would destroy a recruiter's trust in the tool - they open the transcript,
cannot find the quote, and stop believing any of it.

So ungrounded signals are dropped rather than rejected wholesale. Discarding an
entire otherwise-useful analysis because one of four quotes was paraphrased
would trade a small inaccuracy for a total loss of information.
"""

from __future__ import annotations

import re

from app.ai.schemas import AIAnalysis, GeneratedMessageDraft, Signal
from app.core.logging import get_logger

logger = get_logger(__name__)

# Fraction of a quote's words that must appear in the source for it to count as
# grounded. Not 1.0: models legitimately normalise curly quotes, fix obvious
# typos, and trim trailing punctuation. Requiring an exact substring match would
# reject honest quotes; this tolerates cosmetic drift but not invention.
GROUNDING_THRESHOLD = 0.75
MIN_QUOTE_WORDS = 3

_WORD_RE = re.compile(r"[a-z0-9']+")


def _words(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower())


def is_grounded(quote: str, source: str) -> bool:
    """Whether `quote` is plausibly a verbatim span of `source`.

    Compares on a bag-of-words basis after normalisation. A short quote must
    match completely - with two words, partial overlap is meaningless.
    """
    quote_words = _words(quote)
    if not quote_words:
        return False

    source_words = set(_words(source))
    if not source_words:
        return False

    overlap = sum(1 for word in quote_words if word in source_words)
    ratio = overlap / len(quote_words)

    if len(quote_words) < MIN_QUOTE_WORDS:
        return ratio == 1.0
    return ratio >= GROUNDING_THRESHOLD


def enforce_grounding(analysis: AIAnalysis, *, candidate_text: str) -> tuple[AIAnalysis, list[str]]:
    """Drop signals whose evidence cannot be found in the candidate's messages.

    Returns the filtered analysis and a list of dropped signal types, which the
    caller records as telemetry - a rising drop rate is an early warning that a
    prompt or model change has started hallucinating.
    """
    if not analysis.signals:
        return analysis, []

    kept: list[Signal] = []
    dropped: list[str] = []

    for signal in analysis.signals:
        if is_grounded(signal.evidence, candidate_text):
            kept.append(signal)
        else:
            dropped.append(signal.type.value)
            logger.warning(
                "signal_dropped_ungrounded",
                signal_type=signal.type.value,
                # The quote itself is candidate PII and is deliberately not logged.
                quote_length=len(signal.evidence),
            )

    if not dropped:
        return analysis, []

    return analysis.model_copy(update={"signals": kept}), dropped


# Phrases a draft must never contain: commitments the system has no authority
# to make on the company's behalf. A recruiter skim-reading an AI draft before
# hitting approve is exactly how an accidental promise reaches a candidate.
_FORBIDDEN_PATTERNS = (
    re.compile(r"\bwe (?:will|can) (?:increase|revise|match|raise)\b", re.I),
    re.compile(r"\bguarantee(?:d|s)?\b", re.I),
    re.compile(r"\bpromise\b", re.I),
    re.compile(r"\b(?:revised|new) (?:offer|package|ctc)\b", re.I),
)


def check_message_safety(draft: GeneratedMessageDraft) -> list[str]:
    """Flag commitment language in a generated message.

    Returns warnings rather than blocking. The recruiter is the decision-maker;
    the system's job is to make the risk visible before they approve, not to
    silently rewrite their communication.
    """
    warnings: list[str] = []
    text = f"{draft.subject or ''} {draft.body}"

    for pattern in _FORBIDDEN_PATTERNS:
        match = pattern.search(text)
        if match:
            warnings.append(f"Draft contains commitment language: '{match.group(0)}'")

    return warnings
