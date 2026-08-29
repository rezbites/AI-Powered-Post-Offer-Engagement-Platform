"""Google Gemini adapter.

Uses schema-forced generation: `response_mime_type=application/json` plus an
explicit `response_schema`, so the model is constrained at decode time rather
than merely asked nicely for JSON. That is what makes the validation layer a
safety net for the residue instead of the primary parsing strategy.

Retries are narrow on purpose. Transient failures (timeouts, 429s, 5xx) are
retried with exponential backoff; anything else - a bad API key, a malformed
request - fails immediately, because retrying a deterministic rejection just
burns the user's latency budget to arrive at the same answer.

The SDK is imported lazily so a deployment with no API key never loads it, and
an SDK version change cannot prevent the application from booting into Demo
Mode.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from app.ai.provider import AIProvider, LLMResult, ProviderUnavailable
from app.core.config import get_settings
from app.core.logging import get_logger
from app.domain.enums import ProviderName

logger = get_logger(__name__)
settings = get_settings()

MAX_ATTEMPTS = 3
BASE_BACKOFF_SECONDS = 0.5

# Substrings identifying failures that are worth retrying. Matched against the
# exception text because the SDK's exception hierarchy varies across versions,
# and a brittle isinstance chain would silently stop retrying after an upgrade.
_RETRYABLE_MARKERS = (
    "deadline",
    "timeout",
    "unavailable",
    "429",
    "resource_exhausted",
    "rate limit",
    "500",
    "502",
    "503",
    "504",
    "internal error",
)


def _is_retryable(exc: Exception) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    return any(marker in text for marker in _RETRYABLE_MARKERS)


class GeminiProvider(AIProvider):
    """Live provider. Selected when GEMINI_API_KEY is present."""

    name = ProviderName.GEMINI

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self._api_key = api_key or settings.gemini_api_key
        self._model = model or settings.gemini_model
        self._client: Any = None

        if not self._api_key:
            # Should be unreachable: the factory only builds this when a key
            # exists. Explicit anyway, so a misconfiguration fails loudly here
            # rather than as a confusing auth error on the first request.
            raise ProviderUnavailable("GEMINI_API_KEY is not configured.")

    def _get_client(self) -> Any:
        """Lazily construct the SDK client.

        Deferred so that importing this module - which the provider factory
        does unconditionally - never requires the SDK to be installed or the
        network to be reachable.
        """
        if self._client is None:
            try:
                from google import genai
            except ImportError as exc:  # pragma: no cover - dependency is pinned
                raise ProviderUnavailable("google-genai is not installed.") from exc
            self._client = genai.Client(api_key=self._api_key)
        return self._client

    async def generate_structured(
        self,
        *,
        prompt: str,
        schema: dict[str, Any],
        max_output_tokens: int | None = None,
    ) -> LLMResult:
        from google.genai import types

        client = self._get_client()
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=schema,
            max_output_tokens=max_output_tokens or settings.llm_max_output_tokens,
            # Near-deterministic. This is an extraction task, not a creative
            # one: the same transcript should yield the same signals, and
            # sampling variance would show up as risk bands flickering between
            # refreshes.
            temperature=0.1,
        )

        last_error: Exception | None = None

        for attempt in range(1, MAX_ATTEMPTS + 1):
            started = time.perf_counter()
            try:
                response = await asyncio.wait_for(
                    client.aio.models.generate_content(
                        model=self._model,
                        contents=prompt,
                        config=config,
                    ),
                    timeout=settings.llm_timeout_seconds,
                )
                latency_ms = max(1, int((time.perf_counter() - started) * 1000))

                text = getattr(response, "text", None)
                if not text or not text.strip():
                    # A blocked or empty completion is a real outcome, not an
                    # exception. Treated as retryable: it is often a transient
                    # safety-filter trip on one sampling path.
                    raise ProviderUnavailable("Gemini returned an empty response.")

                tokens_in, tokens_out = _usage(response)
                logger.info(
                    "llm_call_succeeded",
                    provider="gemini",
                    model=self._model,
                    attempt=attempt,
                    latency_ms=latency_ms,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                )
                return LLMResult(
                    text=text,
                    provider=ProviderName.GEMINI,
                    model=self._model,
                    latency_ms=latency_ms,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                )

            except asyncio.TimeoutError as exc:
                last_error = exc
                logger.warning("llm_timeout", attempt=attempt, timeout=settings.llm_timeout_seconds)
            except Exception as exc:  # noqa: BLE001 - classified immediately below
                last_error = exc
                if not _is_retryable(exc):
                    logger.error("llm_call_failed", attempt=attempt, error=str(exc), retryable=False)
                    raise ProviderUnavailable(f"Gemini request failed: {exc}") from exc
                logger.warning("llm_call_retryable", attempt=attempt, error=str(exc))

            if attempt < MAX_ATTEMPTS:
                # Exponential backoff. No jitter is added because concurrency
                # here is bounded by the request rate of a small HR team; at
                # fleet scale jitter would be needed to avoid synchronised
                # retry storms.
                await asyncio.sleep(BASE_BACKOFF_SECONDS * (2 ** (attempt - 1)))

        raise ProviderUnavailable(
            f"Gemini unavailable after {MAX_ATTEMPTS} attempts: {last_error}"
        ) from last_error

    async def healthy(self) -> bool:
        """Reports configuration validity, not live reachability.

        A readiness probe must be fast and must not bill. Actual reachability
        surfaces through the call path, where failure degrades to the
        deterministic fallback rather than taking the service down.
        """
        return bool(self._api_key)


def _usage(response: Any) -> tuple[int | None, int | None]:
    """Extract token counts, tolerating SDK field renames.

    Telemetry must never be the reason a successful analysis is discarded, so
    every failure here degrades to None.
    """
    try:
        usage = getattr(response, "usage_metadata", None)
        if usage is None:
            return None, None
        return (
            getattr(usage, "prompt_token_count", None),
            getattr(usage, "candidates_token_count", None),
        )
    except Exception:  # noqa: BLE001 - telemetry is strictly best-effort
        return None, None
