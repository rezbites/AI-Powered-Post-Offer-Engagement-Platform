"""Anthropic Claude adapter.

The second real provider, and the reason the `AIProvider` port earns its place:
adding it touched no pipeline code, no validation, no guardrails and no UI
logic. Everything downstream already worked because it depends on the port,
not on Gemini.

## Forcing structured output

Claude has no `response_schema` parameter. The equivalent is **forced tool
use**: declare a single tool whose `input_schema` is the analysis schema, then
set `tool_choice` to that tool. The model must emit a tool call conforming to
the schema, which is a stronger constraint than asking for JSON in the prompt
and hoping — the same property `response_schema` gives on Gemini, reached by a
different mechanism.

The response is therefore read from the tool-use block, not from a text block.

Uses httpx directly rather than the `anthropic` SDK: the Messages API surface
needed here is one POST, and a new dependency for that would be an unfair trade
against image size and supply-chain surface.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx

from app.ai.provider import AIProvider, LLMResult, ProviderUnavailable
from app.core.config import get_settings
from app.core.logging import get_logger
from app.domain.enums import ProviderName

logger = get_logger(__name__)
settings = get_settings()

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"

MAX_ATTEMPTS = 3
BASE_BACKOFF_SECONDS = 0.5

# The tool the model is forced to call. Naming it after the task rather than
# something generic gives the model useful signal about what is wanted.
TOOL_NAME = "record_analysis"

# Status codes worth retrying. 429 and 5xx are transient; 401 and 400 are not,
# and retrying them just spends the caller's latency to reach the same answer.
RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504, 529}


class ClaudeProvider(AIProvider):
    """Live provider backed by the Anthropic Messages API."""

    name = ProviderName.CLAUDE

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self._api_key = api_key or settings.anthropic_api_key
        self._model = model or settings.anthropic_model

        if not self._api_key:
            raise ProviderUnavailable("ANTHROPIC_API_KEY is not configured.")

    async def generate_structured(
        self,
        *,
        prompt: str,
        schema: dict[str, Any],
        max_output_tokens: int | None = None,
    ) -> LLMResult:
        payload = {
            "model": self._model,
            "max_tokens": max_output_tokens or settings.llm_max_output_tokens,
            # Near-deterministic: this is extraction, not composition. Sampling
            # variance would show up as risk bands flickering between refreshes.
            "temperature": 0.0,
            "tools": [
                {
                    "name": TOOL_NAME,
                    "description": "Record the structured analysis of this candidate.",
                    "input_schema": schema,
                }
            ],
            # The forcing mechanism. Without this the model may reply in prose.
            "tool_choice": {"type": "tool", "name": TOOL_NAME},
            "messages": [{"role": "user", "content": prompt}],
        }

        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": API_VERSION,
            "content-type": "application/json",
        }

        last_error: Exception | None = None

        for attempt in range(1, MAX_ATTEMPTS + 1):
            started = time.perf_counter()
            try:
                async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
                    response = await client.post(API_URL, headers=headers, json=payload)

                latency_ms = max(1, round((time.perf_counter() - started) * 1000))

                if response.status_code in RETRYABLE_STATUS:
                    last_error = ProviderUnavailable(
                        f"Claude returned {response.status_code}"
                    )
                    logger.warning(
                        "llm_call_retryable",
                        provider="claude",
                        attempt=attempt,
                        status=response.status_code,
                    )
                elif response.status_code != 200:
                    # Authentication or malformed request: deterministic, so
                    # fail immediately rather than burning the retry budget.
                    detail = _error_message(response)
                    logger.error(
                        "llm_call_failed",
                        provider="claude",
                        status=response.status_code,
                        error=detail,
                        retryable=False,
                    )
                    raise ProviderUnavailable(f"Claude request failed: {detail}")
                else:
                    body = response.json()
                    text = _extract_tool_input(body)
                    usage = body.get("usage", {})

                    logger.info(
                        "llm_call_succeeded",
                        provider="claude",
                        model=self._model,
                        attempt=attempt,
                        latency_ms=latency_ms,
                        tokens_in=usage.get("input_tokens"),
                        tokens_out=usage.get("output_tokens"),
                    )
                    return LLMResult(
                        text=text,
                        provider=ProviderName.CLAUDE,
                        model=self._model,
                        latency_ms=latency_ms,
                        tokens_in=usage.get("input_tokens"),
                        tokens_out=usage.get("output_tokens"),
                    )

            except ProviderUnavailable:
                raise
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
                logger.warning("llm_transport_error", provider="claude", attempt=attempt, error=str(exc))

            if attempt < MAX_ATTEMPTS:
                await asyncio.sleep(BASE_BACKOFF_SECONDS * (2 ** (attempt - 1)))

        raise ProviderUnavailable(
            f"Claude unavailable after {MAX_ATTEMPTS} attempts: {last_error}"
        ) from last_error

    async def healthy(self) -> bool:
        """Reports configuration validity, not reachability.

        A readiness probe must be fast and must not bill. Real reachability
        surfaces on the call path, where failure degrades to the deterministic
        fallback rather than taking the service down.
        """
        return bool(self._api_key)


def _extract_tool_input(body: dict[str, Any]) -> str:
    """Pull the forced tool call's arguments out of the response.

    Returns JSON text so the pipeline's parse/validate/repair path is identical
    across providers - the whole point of the port is that downstream code
    cannot tell which provider produced a response.
    """
    for block in body.get("content", []):
        if block.get("type") == "tool_use":
            return json.dumps(block.get("input", {}))

    # Forced tool_choice should make this unreachable. If the model somehow
    # replied in prose, surface it as a failure rather than silently returning
    # text that will fail validation with a confusing error.
    raise ProviderUnavailable("Claude returned no tool_use block despite forced tool choice.")


def _error_message(response: httpx.Response) -> str:
    try:
        return response.json().get("error", {}).get("message", response.text[:200])
    except Exception:  # noqa: BLE001 - error reporting must not itself fail
        return response.text[:200]
