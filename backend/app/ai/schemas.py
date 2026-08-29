"""The structured contract the LLM is forced to fill.

This module is the single source of truth for AI output: it generates the
provider's response schema, validates the reply, and types the API response.
Defining the shape once is what stops the three from drifting apart.

## Why the enums are closed

`risk_level`, `signals[].type` and `next_action` are closed enumerations. That
is the load-bearing guardrail of the whole design: candidate-authored text
flows into the prompt, so a free-text action field would let injected content
propose something the application does not know how to render or perform. With
closed enums the worst an injection can achieve is picking a different *valid*
action - which a recruiter then reviews before anything happens.

Free text is confined to `summary`, `risk_rationale`, `evidence` and
`recommended_follow_up`. None of those drive control flow.

## Why `risk_confidence` is requested but not trusted

The model is asked for a confidence value, and it is stored - but as
*telemetry*, not as the number the product shows. Self-reported LLM confidence
is poorly calibrated: it tracks fluency more than evidence, and there is
nothing to check it against. The authoritative confidence is derived in
`domain/confidence.py` from observable properties. Keeping both lets us measure
the gap between what the model claims and what the evidence supports, which is
a genuine calibration signal rather than a vibe.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.domain.enums import NextAction, RiskLevel, SignalType

# Bumped whenever a prompt changes in a way that could alter output. Stamped on
# every stored analysis so a regression can be attributed to a specific prompt.
PROMPT_VERSION = "v1"


class Signal(BaseModel):
    """One detected concern, with the quote that evidences it.

    `evidence` is mandatory and must be a verbatim span from the candidate's
    messages. A signal without a checkable quote is an unfalsifiable assertion,
    and the grounding guardrail drops any whose quote cannot be located in the
    source transcript.
    """

    type: SignalType
    evidence: str = Field(
        min_length=1,
        max_length=500,
        description="Verbatim quote from an interaction that supports this signal.",
    )


class AIAnalysis(BaseModel):
    """Complete analysis of one candidate."""

    summary: str = Field(
        min_length=1,
        max_length=1200,
        description="Two or three sentences summarising the engagement so far.",
    )
    risk_level: RiskLevel
    risk_confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="The model's own confidence. Stored as telemetry only; the "
        "displayed confidence is derived independently.",
    )
    signals: list[Signal] = Field(
        default_factory=list,
        max_length=10,
        description="Concerns or positive indicators detected in the candidate's messages.",
    )
    risk_rationale: str = Field(
        min_length=1, max_length=600, description="One or two sentences explaining the risk level."
    )
    next_action: NextAction
    recommended_follow_up: str = Field(
        min_length=1,
        max_length=600,
        description="Concrete next step for the recruiter, in plain language.",
    )

    @field_validator("signals")
    @classmethod
    def _dedupe_signals(cls, signals: list[Signal]) -> list[Signal]:
        """Keep the first occurrence of each signal type.

        Models sometimes emit the same concern twice with different quotes.
        Duplicates would double-count in risk scoring, so they are collapsed
        here rather than being handled at every call site.
        """
        seen: set[SignalType] = set()
        unique: list[Signal] = []
        for signal in signals:
            if signal.type not in seen:
                seen.add(signal.type)
                unique.append(signal)
        return unique


class GeneratedMessageDraft(BaseModel):
    """An AI-drafted message to a candidate.

    Always a draft. Nothing here reaches a candidate without a recruiter
    approving it first - the human gate, not the prompt, is what makes
    injected instructions harmless.
    """

    subject: str | None = Field(default=None, max_length=200)
    body: str = Field(min_length=1, max_length=3000)
    tone: str = Field(default="warm_professional", max_length=40)


# --------------------------------------------------------------------------
# Provider schema generation
# --------------------------------------------------------------------------
# Gemini accepts a subset of JSON Schema: no `$defs`, no `$ref`, no `anyOf`.
# Pydantic's `model_json_schema()` emits all three for nested models, so the
# schemas below are written explicitly rather than derived. They are covered by
# a test asserting they stay aligned with the Pydantic models, which is what
# stops the duplication silently rotting.

_SIGNAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "type": {
            "type": "string",
            "enum": [s.value for s in SignalType],
            "description": "The kind of concern or indicator detected.",
        },
        "evidence": {
            "type": "string",
            "description": "Verbatim quote from a candidate message supporting this signal.",
        },
    },
    "required": ["type", "evidence"],
}

ANALYSIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {
            "type": "string",
            "description": "Two or three sentences summarising engagement so far.",
        },
        "risk_level": {
            "type": "string",
            "enum": [r.value for r in RiskLevel],
            "description": "Likelihood the candidate does not join.",
        },
        "risk_confidence": {
            "type": "number",
            "description": "Your confidence in the risk level, between 0 and 1.",
        },
        "signals": {
            "type": "array",
            "items": _SIGNAL_SCHEMA,
            "description": "Concerns or positive indicators, each with a supporting quote.",
        },
        "risk_rationale": {
            "type": "string",
            "description": "One or two sentences explaining the risk level.",
        },
        "next_action": {
            "type": "string",
            "enum": [a.value for a in NextAction],
            "description": "The single best next action for the recruiter.",
        },
        "recommended_follow_up": {
            "type": "string",
            "description": "Concrete next step in plain language.",
        },
    },
    "required": [
        "summary",
        "risk_level",
        "risk_confidence",
        "signals",
        "risk_rationale",
        "next_action",
        "recommended_follow_up",
    ],
}

MESSAGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "subject": {"type": "string", "description": "Subject line. Empty for WhatsApp."},
        "body": {"type": "string", "description": "The message body."},
        "tone": {"type": "string", "description": "Tone used, e.g. warm_professional."},
    },
    "required": ["body", "tone"],
}
