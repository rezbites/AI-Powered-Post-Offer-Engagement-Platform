"""Provider selection.

One place decides whether the system runs in Live Mode or Demo Mode, so the
answer cannot differ between the readiness probe, the analysis pipeline and the
UI badge - which is exactly how a mock result would end up presented as genuine
model output.
"""

from __future__ import annotations

from functools import lru_cache

from app.ai.mock import MockProvider
from app.ai.provider import AIProvider
from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


@lru_cache
def get_provider() -> AIProvider:
    """Return the configured provider.

    A missing or broken Gemini configuration falls back to the mock rather than
    raising. Booting into a clearly-labelled Demo Mode is strictly better than
    refusing to start: the operator can still reach the app, and
    `/health/ready` tells them exactly which mode they are in.

    Cached because constructing the Gemini client is not free and the choice
    cannot change without a restart (it is driven by environment variables).
    """
    settings = get_settings()

    resolved = settings.resolved_provider
    if resolved in ("gemini", "claude"):
        live = (_gemini_provider if resolved == "gemini" else _claude_provider)()
        if live is not None:
            logger.info("provider_selected", provider=resolved, model=settings.active_model)
            return live
        logger.error("live_provider_init_failed_falling_back_to_mock", provider=resolved)

    logger.info(
        "provider_selected",
        provider="mock",
        mode="demo",
        note="No live LLM calls. Analyses are deterministic and labelled as mock.",
    )
    return MockProvider()


@lru_cache
def _gemini_provider() -> AIProvider | None:
    """Build the Gemini provider once, or None if it cannot be constructed."""
    settings = get_settings()
    if not settings.gemini_api_key.strip():
        return None
    try:
        from app.ai.gemini import GeminiProvider

        return GeminiProvider()
    except Exception as exc:  # noqa: BLE001
        logger.error("gemini_init_failed", error=str(exc))
        return None


@lru_cache
def _claude_provider() -> AIProvider | None:
    settings = get_settings()
    if not settings.anthropic_api_key.strip():
        return None
    try:
        from app.ai.claude import ClaudeProvider

        return ClaudeProvider()
    except Exception as exc:  # noqa: BLE001
        logger.error("claude_init_failed", error=str(exc))
        return None


def available_providers() -> dict[str, bool]:
    """Which providers this deployment can actually use.

    The UI reads this to decide whether to offer a Gemini option at all -
    presenting a choice that will silently fall back to the mock would be worse
    than not offering it.
    """
    return {
        "mock": True,
        "gemini": _gemini_provider() is not None,
        "claude": _claude_provider() is not None,
    }


def get_provider_by_name(name: str | None) -> tuple[AIProvider, bool]:
    """Resolve an explicitly requested provider.

    Returns `(provider, honoured)`. `honoured` is False when the caller asked
    for Gemini and no key is configured, so the caller can *tell the user* the
    request was downgraded rather than quietly serving mock output labelled as
    a live analysis. Silent substitution is exactly the dishonesty the Demo
    Mode labelling exists to prevent.
    """
    if name is None:
        return get_provider(), True

    if name == "mock":
        return MockProvider(), True

    if name in ("gemini", "claude"):
        builder = _gemini_provider if name == "gemini" else _claude_provider
        live = builder()
        if live is None:
            logger.warning("provider_requested_but_unavailable", requested=name)
            return MockProvider(), False
        return live, True

    return get_provider(), True


def reset_provider_cache() -> None:
    """Clear the cached provider. Used by tests that swap configuration."""
    get_provider.cache_clear()
    _gemini_provider.cache_clear()
    _claude_provider.cache_clear()
