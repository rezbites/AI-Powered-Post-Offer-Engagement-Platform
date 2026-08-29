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

    if settings.resolved_provider == "gemini":
        try:
            from app.ai.gemini import GeminiProvider

            provider = GeminiProvider()
            logger.info("provider_selected", provider="gemini", model=settings.gemini_model)
            return provider
        except Exception as exc:  # noqa: BLE001 - degrade rather than fail to boot
            logger.error(
                "gemini_init_failed_falling_back_to_mock",
                error=str(exc),
            )

    logger.info(
        "provider_selected",
        provider="mock",
        mode="demo",
        note="No live LLM calls. Analyses are deterministic and labelled as mock.",
    )
    return MockProvider()


def reset_provider_cache() -> None:
    """Clear the cached provider. Used by tests that swap configuration."""
    get_provider.cache_clear()
