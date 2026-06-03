#!/usr/bin/env python3
"""Main entry point: fetch Kalshi odds → generate HTML → save snapshot.

Usage:
    python scripts/refresh.py
"""

import json
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import structlog

from src.config import now_in_app_tz, settings
from src.html_gen import generate_html
from src.odds import fetch_odds, odds_to_snapshot

structlog.configure(
    processors=[structlog.dev.ConsoleRenderer()],
    wrapper_class=structlog.BoundLogger,
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)

logger = structlog.get_logger()


def save_snapshot(snapshot: list[dict], snapshot_dir: Path) -> Path:
    """Save odds snapshot as JSON file with timestamp."""
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    now = now_in_app_tz()
    filename = now.strftime("%Y-%m-%dT%H-%M") + ".json"
    path = snapshot_dir / filename
    path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    logger.info("Snapshot saved", path=str(path))
    return path


# Below this number of nations with ANY Kalshi data (futures OR per-game)
# → treat as empty/sample and skip the HTML overwrite to preserve whatever
# was last live.  Generous threshold of 1 — show whatever we have.
MIN_NATIONS_WITH_DATA = 1


def main():
    logger.info("Starting refresh", base_url=settings.kalshi_base_url, current_round=settings.current_round)

    # 1. Fetch odds
    odds = fetch_odds()
    nations_with_futures = sum(1 for o in odds if o.round_probs)
    nations_with_games = sum(1 for o in odds if o.matchday_probs)
    nations_with_data = sum(1 for o in odds if o.round_probs or o.matchday_probs)
    logger.info(
        "Odds fetched",
        nations=len(odds),
        with_futures=nations_with_futures,
        with_games=nations_with_games,
    )

    # 2. Save snapshot (always)
    snapshot = odds_to_snapshot(odds)
    save_snapshot(snapshot, Path(settings.snapshot_dir))

    # 3. Guard: don't overwrite live HTML with empty/sample data
    html_path = Path(settings.html_output_path)
    if nations_with_data < MIN_NATIONS_WITH_DATA:
        logger.warning(
            "No Kalshi WC data yet — skipping HTML overwrite. "
            "Snapshot still saved for diagnostics.",
            nations_with_data=nations_with_data,
        )
        return

    # 4. Generate HTML
    generate_html(odds, html_path)
    logger.info("Refresh complete", html=str(html_path))


if __name__ == "__main__":
    main()
