"""Kalshi API service using pykalshi — singleton client."""

import structlog
from pykalshi import KalshiClient

from .config import settings

logger = structlog.get_logger()


def _create_client() -> KalshiClient:
    """Create and return a Kalshi client configured from settings."""
    logger.info(
        "Connecting to Kalshi API",
        api_base=settings.kalshi_base_url,
        api_key_id=(
            settings.kalshi_api_key_id[:8] + "..." if settings.kalshi_api_key_id else "(empty)"
        ),
    )
    return KalshiClient(
        api_key_id=settings.kalshi_api_key_id,
        private_key_path=str(settings.kalshi_private_key_path),
        api_base=settings.kalshi_base_url,
    )


_client: KalshiClient | None = None


def get_client() -> KalshiClient:
    """Get or create the singleton Kalshi client."""
    global _client
    if _client is None:
        _client = _create_client()
    return _client
