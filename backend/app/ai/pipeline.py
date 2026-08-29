"""The AI analysis pipeline.

    snapshot -> hash -> cache -> generate -> validate -> repair -> fallback
             -> guardrails -> persist -> recruiter

Every stage exists because of a specific failure it prevents:

* **hash + cache** - dashboards re-render constantly; without this, every page
  load re-analyses every visible candidate and bills for it.
* **schema-forced generation** - constrain the model at decode time rather than
  asking politely for JSON and hoping.
* **validate** - well-formed output is not guaranteed output; Pydantic is the
  boundary between "the model said something" and "the system believes it".
* **repair** - most invalid generations are one field away from correct. Feeding
  the error back recovers them for the price of one extra call.
* **fallback** - after two failures the answer is *still* a working dashboard.
  A model outage must degrade the product, not break it.
* **guardrails** - schema-valid output can still be a hallucinated quote.
* **persist** - the row is simultaneously the cache, the audit record, and the
  cost/latency ledger.

The pipeline never raises for provider failure. A recruiter opening a candidate
page during a Gemini outage sees a deterministic assessment labelled as such,
not a 502.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from pydantic import ValidationError as PydanticValidationError

from app.ai import guardrails
from app.ai.provider import AIProvider, ProviderUnavailable
from app.ai.schemas import (
    ANALYSIS_SCHEMA,
    MESSAGE_SCHEMA,
    PROMPT_VERSION,
    AIAnalysis,
    GeneratedMessageDraft,
)
from app.ai.snapshot import CandidateSnapshot
from app.core.config import get_settings
from app.core.logging import get_logger
from app.domain.context import CandidateContext
from app.domain.enums import AnalysisStatus, NextAction, ProviderName, RiskLevel, SignalType
from app.domain.risk import assess

logger = get_logger(__name__)
settings = get_settings()

PROMPTS_DIR = Path(__file__).parent / "prompts"


@dataclass
class AnalysisOutcome:
    """Result of one pipeline run, including everything the ledger records."""

    analysis: AIAnalysis
    status: AnalysisStatus
    provider: ProviderName
    model: str | None
    latency_ms: int
    tokens_in: int | None
    tokens_out: int | None
    raw_response: str | None
    error: str | None
    dropped_signals: list[str]
    from_cache: bool = False


def _load_prompt(name: str) -> str:
    """Read a versioned prompt from disk.

    Prompts live in files rather than string literals so they are reviewable in
    a diff, and so `PROMPT_VERSION` can attribute a regression to a specific
    revision.
    """
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def build_analysis_prompt(snapshot: CandidateSnapshot) -> str:
    template = _load_prompt(f"analysis_{PROMPT_VERSION}.md")
    payload = json.dumps(snapshot.to_dict(), indent=2, default=str)
    return template.replace("{snapshot_json}", payload)


def build_message_prompt(snapshot: CandidateSnapshot, *, channel: str) -> str:
    template = _load_prompt(f"message_{PROMPT_VERSION}.md")
    payload = json.dumps(snapshot.to_dict(), indent=2, default=str)
    return template.replace("{snapshot_json}", payload).replace("{channel}", channel)


def _strip_fences(text: str) -> str:
    """Remove markdown fences a model may add despite instructions.

    Cheap and worth doing before declaring a response invalid: burning a repair
    call on three backticks would be wasteful.
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    return cleaned


def parse_analysis(text: str) -> AIAnalysis:
    """Parse and validate. Raises on malformed or non-conforming output."""
    return AIAnalysis.model_validate_json(_strip_fences(text))


def _repair_prompt(original: str, bad_output: str, error: str) -> str:
    """Ask the model to fix its own output.

    The invalid output and the exact validator error are both included: without
    the error the model has no idea what was wrong, and a blind retry mostly
    reproduces the same mistake.
    """
    return (
        f"{original}\n\n"
        "---\n"
        "Your previous response was rejected by schema validation.\n\n"
        f"Previous response:\n{bad_output[:2000]}\n\n"
        f"Validation error:\n{error[:1000]}\n\n"
        "Return a corrected JSON object matching the schema exactly. "
        "Use only the permitted enum values. No prose, no markdown fences."
    )


def deterministic_fallback(
    ctx: CandidateContext, snapshot: CandidateSnapshot, *, today: date
) -> AIAnalysis:
    """A usable analysis with no model involved.

    Built from the deterministic risk engine, so the dashboard stays populated
    and honest during a provider outage. It carries no `signals`: without the
    model there is no semantic extraction, and inventing signals here would be
    precisely the dishonesty the grounding guardrail exists to prevent.
    """
    assessment = assess(ctx, today=today)

    if assessment is None:  # terminal candidate
        return AIAnalysis(
            summary=f"{snapshot.name} has reached a final outcome ({snapshot.status}).",
            risk_level=RiskLevel.LOW,
            risk_confidence=0.0,
            signals=[],
            risk_rationale="Candidate has reached a terminal status; joining risk no longer applies.",
            next_action=NextAction.NO_ACTION,
            recommended_follow_up="No further engagement action is required.",
        )

    days_out = snapshot.days_to_joining
    quiet = snapshot.days_since_interaction

    # Action chosen from countable facts alone - the same information the rules
    # engine used to produce the band.
    if assessment.level is RiskLevel.HIGH:
        action = NextAction.CALL_CANDIDATE
    elif quiet is None or quiet >= 5:
        action = NextAction.SEND_REMINDER
    elif snapshot.stages_overdue:
        action = NextAction.SEND_REMINDER
    else:
        action = NextAction.NO_ACTION

    contact = "never contacted" if quiet is None else f"last contacted {quiet} days ago"
    return AIAnalysis(
        summary=(
            f"{snapshot.name} joins in {days_out} days and was {contact}. "
            f"Engagement journey is {snapshot.stages_completed} of {snapshot.stages_total} steps complete. "
            "Automated language analysis was unavailable, so this assessment uses "
            "engagement history and timing only."
        ),
        risk_level=assessment.level,
        risk_confidence=assessment.confidence,
        signals=[],
        risk_rationale=assessment.rationale,
        next_action=action,
        recommended_follow_up=(
            f"Review {snapshot.name} manually - automated analysis was unavailable. "
            f"Contributing factors: {'; '.join(assessment.factors[:3]) or 'none detected'}."
        ),
    )


async def analyse(
    provider: AIProvider,
    snapshot: CandidateSnapshot,
    ctx: CandidateContext,
    *,
    today: date,
) -> AnalysisOutcome:
    """Run one analysis end to end.

    Never raises for provider or validation failure - the worst case is a
    deterministic fallback with `status=failed`, which is a working product.
    """
    prompt = build_analysis_prompt(snapshot)
    started = time.perf_counter()

    raw_text: str | None = None
    total_tokens_in = 0
    total_tokens_out = 0

    # --- Attempt 1: schema-forced generation ------------------------------
    try:
        result = await provider.generate_structured(
            prompt=prompt, schema=ANALYSIS_SCHEMA
        )
        raw_text = result.text
        total_tokens_in += result.tokens_in or 0
        total_tokens_out += result.tokens_out or 0

        analysis = parse_analysis(raw_text)
        analysis, dropped = guardrails.enforce_grounding(
            analysis, candidate_text=snapshot.candidate_text()
        )
        return AnalysisOutcome(
            analysis=analysis,
            status=AnalysisStatus.VALID,
            provider=result.provider,
            model=result.model,
            latency_ms=int((time.perf_counter() - started) * 1000),
            tokens_in=total_tokens_in or None,
            tokens_out=total_tokens_out or None,
            raw_response=raw_text,
            error=None,
            dropped_signals=dropped,
        )

    except PydanticValidationError as exc:
        validation_error = str(exc)
        logger.warning("analysis_invalid", candidate_id=snapshot.candidate_id, attempt=1)

    except ProviderUnavailable as exc:
        # No point repairing: the provider itself is unreachable.
        logger.error("analysis_provider_unavailable", candidate_id=snapshot.candidate_id, error=str(exc))
        return AnalysisOutcome(
            analysis=deterministic_fallback(ctx, snapshot, today=today),
            status=AnalysisStatus.FAILED,
            provider=provider.name,
            model=None,
            latency_ms=int((time.perf_counter() - started) * 1000),
            tokens_in=total_tokens_in or None,
            tokens_out=total_tokens_out or None,
            raw_response=None,
            error=f"provider_unavailable: {exc}",
            dropped_signals=[],
        )

    except (json.JSONDecodeError, ValueError) as exc:
        validation_error = f"Response was not valid JSON: {exc}"
        logger.warning("analysis_unparseable", candidate_id=snapshot.candidate_id, attempt=1)

    # --- Attempt 2: repair ------------------------------------------------
    try:
        repair = await provider.generate_structured(
            prompt=_repair_prompt(prompt, raw_text or "", validation_error),
            schema=ANALYSIS_SCHEMA,
        )
        total_tokens_in += repair.tokens_in or 0
        total_tokens_out += repair.tokens_out or 0

        analysis = parse_analysis(repair.text)
        analysis, dropped = guardrails.enforce_grounding(
            analysis, candidate_text=snapshot.candidate_text()
        )
        logger.info("analysis_repaired", candidate_id=snapshot.candidate_id)
        return AnalysisOutcome(
            analysis=analysis,
            status=AnalysisStatus.REPAIRED,
            provider=repair.provider,
            model=repair.model,
            latency_ms=int((time.perf_counter() - started) * 1000),
            tokens_in=total_tokens_in or None,
            tokens_out=total_tokens_out or None,
            raw_response=repair.text,
            error=f"repaired_after: {validation_error[:500]}",
            dropped_signals=dropped,
        )

    except Exception as exc:  # noqa: BLE001 - every failure lands on the fallback
        logger.error(
            "analysis_failed_after_repair",
            candidate_id=snapshot.candidate_id,
            error=str(exc),
        )

    # --- Fallback ---------------------------------------------------------
    return AnalysisOutcome(
        analysis=deterministic_fallback(ctx, snapshot, today=today),
        status=AnalysisStatus.FAILED,
        provider=provider.name,
        model=None,
        latency_ms=int((time.perf_counter() - started) * 1000),
        tokens_in=total_tokens_in or None,
        tokens_out=total_tokens_out or None,
        raw_response=raw_text,
        error=f"validation_failed: {validation_error[:500]}",
        dropped_signals=[],
    )


async def draft_message(
    provider: AIProvider,
    snapshot: CandidateSnapshot,
    *,
    channel: str,
) -> tuple[GeneratedMessageDraft, list[str], ProviderName, str | None, int]:
    """Draft a candidate message.

    Returns the draft, any safety warnings, and telemetry. Unlike analysis
    there is no deterministic fallback: if the model cannot draft a message,
    the honest answer is to tell the recruiter to write it themselves rather
    than to send them a template pretending to be personalised.
    """
    prompt = build_message_prompt(snapshot, channel=channel)
    started = time.perf_counter()

    result = await provider.generate_structured(prompt=prompt, schema=MESSAGE_SCHEMA)
    draft = GeneratedMessageDraft.model_validate_json(_strip_fences(result.text))
    warnings = guardrails.check_message_safety(draft)

    if warnings:
        logger.warning(
            "message_safety_warning",
            candidate_id=snapshot.candidate_id,
            warnings=warnings,
        )

    latency_ms = int((time.perf_counter() - started) * 1000)
    return draft, warnings, result.provider, result.model, latency_ms


def signals_to_views(analysis: AIAnalysis) -> list[tuple[SignalType, str]]:
    """Flatten validated signals for the domain layer."""
    return [(s.type, s.evidence) for s in analysis.signals]
