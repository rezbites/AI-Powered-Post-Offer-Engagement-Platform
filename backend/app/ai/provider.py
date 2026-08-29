"""The LLM provider port.

An explicit interface with two implementations - Gemini and a deterministic
mock - buys three concrete things:

* the whole system runs and demos with no API key (Demo Mode);
* tests exercise the full pipeline, including failure paths, without cost,
  network flakiness or non-determinism;
* swapping providers is one adapter, not a refactor.

Providers return raw text plus telemetry. They deliberately do **not** parse or
validate: that belongs in the pipeline, so validation, repair and fallback
behave identically no matter which provider produced the text.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Any

from app.domain.enums import ProviderName


@dataclass(frozen=True)
class LLMResult:
    """One provider response plus the telemetry the ledger records."""

    text: str
    provider: ProviderName
    model: str | None
    latency_ms: int
    tokens_in: int | None = None
    tokens_out: int | None = None

    @property
    def is_mock(self) -> bool:
        return self.provider is ProviderName.MOCK


class ProviderUnavailable(Exception):
    """The provider could not be reached, or refused the request.

    Raised rather than returned so the pipeline's fallback path is explicit.
    Callers never see this: an unavailable provider degrades to a deterministic
    analysis, it does not fail a dashboard load.
    """


class AIProvider(abc.ABC):
    """Port implemented by every backend."""

    name: ProviderName

    @abc.abstractmethod
    async def generate_structured(
        self,
        *,
        prompt: str,
        schema: dict[str, Any],
        max_output_tokens: int | None = None,
    ) -> LLMResult:
        """Return JSON text conforming to `schema`.

        Implementations should use native schema-forced generation where the
        provider supports it. Asking politely in the prompt and hoping for JSON
        is measurably worse, and the repair path exists for the residue - not
        as the primary strategy.
        """

    @abc.abstractmethod
    async def healthy(self) -> bool:
        """Cheap liveness check for the readiness probe."""
