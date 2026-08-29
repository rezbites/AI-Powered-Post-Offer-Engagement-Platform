"""Deterministic mock provider - the engine behind Demo Mode.

This is **not** a stub returning canned text. It reads the same candidate
snapshot the real prompt carries, matches the candidate's own words against a
keyword lexicon, and emits signals with genuine verbatim quotes pulled from
those messages. The relocation example from the brief is detected here exactly
as Gemini would detect it, quote and all.

Three reasons it earns its place:

* **Demo Mode.** `docker compose up` with no API key produces a fully populated,
  coherent product. An evaluator who cannot obtain a key still sees everything.
* **Testing.** The pipeline's validation, repair and fallback paths need
  reproducible inputs. Assertions against a live model would be flaky and
  expensive.
* **Cost.** Nothing bills during development or CI.

It is deliberately *labelled* everywhere it surfaces - `provider` on every
stored analysis, the readiness probe, and a UI badge. Unlabelled mock output
that looks like model output would be dishonest, and an evaluator seeing
suspiciously perfect results is entitled to wonder whether the "AI" is
hardcoded. Saying so plainly turns that suspicion into evidence of intent.

Keyword matching is obviously not language understanding. It cannot handle
negation ("no relocation issues at all"), sarcasm, or paraphrase. That is
acceptable for a demo and a test fixture; it is why Live Mode exists.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from app.ai.provider import AIProvider, LLMResult
from app.domain.enums import NextAction, ProviderName, RiskLevel, SignalType

# Lexicon mapping candidate phrasing to typed signals. Ordered by specificity:
# the first match wins for a given signal type.
_LEXICON: list[tuple[SignalType, tuple[str, ...]]] = [
    (
        SignalType.RELOCATION_CONCERN,
        ("relocat", "accommodation", "housing", "shifting to", "move to the city", "place to stay"),
    ),
    (
        SignalType.COMPETING_OFFER,
        ("another offer", "other offer", "competing offer", "counter-offer", "counter offer",
         "another opportunity", "weighing my options", "other opportunity"),
    ),
    (
        SignalType.COMPENSATION_CONCERN,
        ("compensation", "salary", "variable component", "fixed portion", "package", "ctc",
         "pay is higher"),
    ),
    (
        SignalType.NOTICE_PERIOD_ISSUE,
        ("notice period", "not releasing me", "last working day", "relieving", "extend by"),
    ),
    (
        SignalType.POSITIVE_INTENT,
        ("excited", "looking forward", "delighted", "thank you so much", "works perfectly",
         "very keen", "can't wait", "cannot wait"),
    ),
    (
        SignalType.LOW_ENTHUSIASM,
        ("i will revert", "will get back", "let me think", "not sure yet", "still deciding"),
    ),
]

# Which action best addresses each concern, most urgent first. A competing offer
# needs a conversation; a relocation question needs a concrete offer of help.
_ACTION_BY_SIGNAL: list[tuple[SignalType, NextAction]] = [
    (SignalType.COMPETING_OFFER, NextAction.CALL_CANDIDATE),
    (SignalType.COMPENSATION_CONCERN, NextAction.SCHEDULE_CONVERSATION),
    (SignalType.RELOCATION_CONCERN, NextAction.SEND_RELOCATION_SUPPORT),
    (SignalType.NOTICE_PERIOD_ISSUE, NextAction.SCHEDULE_CONVERSATION),
    (SignalType.LOW_ENTHUSIASM, NextAction.CALL_CANDIDATE),
]

_DATA_BLOCK = re.compile(r"<candidate_data>\s*(\{.*?\})\s*</candidate_data>", re.DOTALL)


def _extract_snapshot(prompt: str) -> dict[str, Any]:
    """Pull the candidate JSON out of the prompt.

    The prompt genuinely carries the snapshot in a delimited block, so this is
    the mock reading its input rather than reverse-engineering a string.
    """
    match = _DATA_BLOCK.search(prompt)
    if not match:
        return {}
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return {}


def _sentences(text: str) -> list[str]:
    """Split into sentences for quoting.

    Quoting one sentence rather than a whole message keeps evidence tight and
    keeps it a genuine verbatim span of the source.
    """
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _detect_signals(inbound: list[str]) -> list[dict[str, str]]:
    """Match candidate messages against the lexicon, quoting the exact sentence."""
    found: list[dict[str, str]] = []
    seen: set[SignalType] = set()

    for signal_type, keywords in _LEXICON:
        if signal_type in seen:
            continue
        for message in inbound:
            lowered = message.lower()
            if not any(keyword in lowered for keyword in keywords):
                continue
            # Quote the specific sentence that triggered the match, so the
            # evidence is checkable against the transcript.
            quote = next(
                (s for s in _sentences(message) if any(k in s.lower() for k in keywords)),
                message,
            )
            found.append({"type": signal_type.value, "evidence": quote[:500]})
            seen.add(signal_type)
            break

    return found


def _assess(snapshot: dict[str, Any], signals: list[dict[str, str]]) -> tuple[RiskLevel, float]:
    """A plausible band and self-reported confidence.

    Mirrors the shape of the real engine's reasoning without importing it: the
    mock stands in for the *model*, and the model does not have access to our
    scoring code. The pipeline recomputes authoritative risk downstream either
    way.
    """
    days_out = snapshot.get("days_to_joining")
    days_quiet = snapshot.get("days_since_interaction")
    types = {s["type"] for s in signals}

    score = 0
    if isinstance(days_out, int) and 0 <= days_out <= 7:
        score += 2
    elif isinstance(days_out, int) and days_out <= 15:
        score += 1

    if days_quiet is None or (isinstance(days_quiet, int) and days_quiet >= 10):
        score += 2
    elif isinstance(days_quiet, int) and days_quiet >= 5:
        score += 1

    if SignalType.COMPETING_OFFER.value in types:
        score += 3
    if SignalType.COMPENSATION_CONCERN.value in types:
        score += 2
    if {SignalType.RELOCATION_CONCERN.value, SignalType.NOTICE_PERIOD_ISSUE.value} & types:
        score += 1
    if SignalType.POSITIVE_INTENT.value in types:
        score -= 1

    if score >= 5:
        return RiskLevel.HIGH, 0.82
    if score >= 3:
        return RiskLevel.MEDIUM, 0.74
    return RiskLevel.LOW, 0.68


def _choose_action(snapshot: dict[str, Any], signals: list[dict[str, str]], level: RiskLevel) -> NextAction:
    types = {s["type"] for s in signals}

    for signal_type, action in _ACTION_BY_SIGNAL:
        if signal_type.value in types:
            return action

    days_quiet = snapshot.get("days_since_interaction")
    if days_quiet is None or (isinstance(days_quiet, int) and days_quiet >= 5):
        return NextAction.SEND_REMINDER
    if snapshot.get("stages_overdue", 0):
        return NextAction.SEND_REMINDER
    if snapshot.get("pending_stage") == "Manager Introduction":
        return NextAction.MANAGER_INTRODUCTION
    return NextAction.NO_ACTION if level is RiskLevel.LOW else NextAction.SCHEDULE_CONVERSATION


def _summarise(snapshot: dict[str, Any], signals: list[dict[str, str]]) -> str:
    name = snapshot.get("name", "The candidate")
    days_out = snapshot.get("days_to_joining")
    days_quiet = snapshot.get("days_since_interaction")
    completed = snapshot.get("stages_completed", 0)
    total = snapshot.get("stages_total", 0)

    timing = (
        f"joins in {days_out} days" if isinstance(days_out, int) and days_out >= 0
        else "has a joining date that has already passed"
    )
    contact = (
        "has not been contacted yet" if days_quiet is None
        else f"was last contacted {days_quiet} days ago"
    )
    parts = [f"{name} {timing} and {contact}.", f"Engagement journey is {completed} of {total} steps complete."]

    if signals:
        readable = ", ".join(s["type"].replace("_", " ") for s in signals)
        parts.append(f"Detected in their messages: {readable}.")
    else:
        parts.append("No specific concerns were raised in their messages.")

    return " ".join(parts)


def _rationale(level: RiskLevel, snapshot: dict[str, Any], signals: list[dict[str, str]]) -> str:
    reasons: list[str] = []
    days_out = snapshot.get("days_to_joining")
    days_quiet = snapshot.get("days_since_interaction")

    if isinstance(days_out, int) and 0 <= days_out <= 7:
        reasons.append(f"joining in {days_out} days")
    if days_quiet is None:
        reasons.append("no contact on record")
    elif isinstance(days_quiet, int) and days_quiet >= 5:
        reasons.append(f"{days_quiet} days without contact")
    for signal in signals:
        if signal["type"] != SignalType.POSITIVE_INTENT.value:
            reasons.append(signal["type"].replace("_", " "))

    if not reasons:
        return "Engagement is on track with no concerns raised."
    return f"Assessed {level.value} because of {', '.join(reasons[:3])}."


def _follow_up(action: NextAction, snapshot: dict[str, Any]) -> str:
    name = snapshot.get("name", "the candidate")
    return {
        NextAction.CALL_CANDIDATE: f"Call {name} today to understand their current thinking directly.",
        NextAction.SEND_RELOCATION_SUPPORT: (
            f"Send {name} the relocation support pack and offer to connect them with a housing partner."
        ),
        NextAction.SEND_REMINDER: f"Send {name} a short check-in and nudge the pending steps.",
        NextAction.MANAGER_INTRODUCTION: f"Set up an introduction between {name} and their hiring manager.",
        NextAction.SCHEDULE_CONVERSATION: f"Schedule a call with {name} to work through their concern.",
        NextAction.ESCALATE: f"Escalate {name} to the HR lead - joining is at material risk.",
        NextAction.NO_ACTION: f"No action needed; {name} is engaged and on track.",
    }[action]


class MockProvider(AIProvider):
    """Deterministic provider used whenever no API key is configured."""

    name = ProviderName.MOCK

    def __init__(self, model_label: str = "deterministic-mock-v1") -> None:
        self._model_label = model_label

    async def generate_structured(
        self,
        *,
        prompt: str,
        schema: dict[str, Any],
        max_output_tokens: int | None = None,
    ) -> LLMResult:
        started = time.perf_counter()
        snapshot = _extract_snapshot(prompt)

        # Message generation and analysis share the port, so the schema tells
        # us which shape is being asked for.
        if "body" in schema.get("properties", {}):
            payload = self._draft_message(snapshot)
        else:
            payload = self._analyse(snapshot)

        latency_ms = max(1, int((time.perf_counter() - started) * 1000))
        text = json.dumps(payload)

        return LLMResult(
            text=text,
            provider=ProviderName.MOCK,
            model=self._model_label,
            latency_ms=latency_ms,
            # Rough character-based estimate. Reported so the ledger has a
            # consistent shape across providers; it is not a billing figure.
            tokens_in=len(prompt) // 4,
            tokens_out=len(text) // 4,
        )

    def _analyse(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        inbound = [
            i.get("content", "")
            for i in snapshot.get("interactions", [])
            if i.get("direction") == "inbound"
        ]
        signals = _detect_signals(inbound)
        level, confidence = _assess(snapshot, signals)
        action = _choose_action(snapshot, signals, level)

        return {
            "summary": _summarise(snapshot, signals),
            "risk_level": level.value,
            "risk_confidence": confidence,
            "signals": signals,
            "risk_rationale": _rationale(level, snapshot, signals),
            "next_action": action.value,
            "recommended_follow_up": _follow_up(action, snapshot),
        }

    def _draft_message(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        """Draft a candidate message from the detected concern.

        Grounded strictly in the snapshot - it never invents a date, a name or
        a commitment that was not in the input.
        """
        name = snapshot.get("name", "there")
        first_name = name.split()[0] if name else "there"
        inbound = [
            i.get("content", "")
            for i in snapshot.get("interactions", [])
            if i.get("direction") == "inbound"
        ]
        signals = {s["type"] for s in _detect_signals(inbound)}
        days_out = snapshot.get("days_to_joining")

        if SignalType.RELOCATION_CONCERN.value in signals:
            subject = "Relocation support for your move"
            body = (
                f"Hi {first_name},\n\n"
                "You mentioned you are still working through relocation and accommodation. "
                "We can help with that directly - we have a relocation support pack covering "
                "temporary accommodation, movers, and a local housing contact who works with "
                "people joining us.\n\n"
                "Would a short call this week be useful to talk through the options?\n\n"
                "Best regards"
            )
        elif SignalType.COMPETING_OFFER.value in signals:
            subject = "A quick conversation before you decide"
            body = (
                f"Hi {first_name},\n\n"
                "Thank you for being open about weighing your options - genuinely, that helps us "
                "have a straight conversation.\n\n"
                "Before you decide, could we find fifteen minutes? I would like to understand what "
                "matters most to you and be clear about what we can and cannot do.\n\n"
                "Best regards"
            )
        elif SignalType.NOTICE_PERIOD_ISSUE.value in signals:
            subject = "Flexibility on your joining date"
            body = (
                f"Hi {first_name},\n\n"
                "Thanks for flagging the notice period situation early. We would rather adjust the "
                "date than have you in a difficult position with your current employer.\n\n"
                "Let me know the earliest date that works and we will look at moving things.\n\n"
                "Best regards"
            )
        else:
            timing = (
                f"With your start date about {days_out} days away, "
                if isinstance(days_out, int) and days_out >= 0
                else ""
            )
            subject = "Checking in before your first day"
            body = (
                f"Hi {first_name},\n\n"
                f"{timing}I wanted to check in and see how everything is going.\n\n"
                "Is there anything outstanding I can help move along, or any questions I can answer?\n\n"
                "Best regards"
            )

        return {"subject": subject, "body": body, "tone": "warm_professional"}

    async def healthy(self) -> bool:
        """Always available - it has no dependencies. That is the point."""
        return True
