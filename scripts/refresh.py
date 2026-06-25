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


def main():
    logger.info("Starting refresh", base_url=settings.kalshi_base_url, current_round=settings.current_round)

    # 1. Fetch odds.  is_sample is True only when NO live Kalshi data was
    #    available (sample odds populate all 48 nations, so a row count can't
    #    tell sample from real — the flag is the reliable signal).
    odds, is_sample = fetch_odds()
    nations_with_futures = sum(1 for o in odds if o.round_probs)
    nations_with_games = sum(1 for o in odds if o.matchday_probs)
    logger.info(
        "Odds fetched",
        nations=len(odds),
        with_futures=nations_with_futures,
        with_games=nations_with_games,
        is_sample=is_sample,
    )

    # 2. Save snapshot (always)
    snapshot = odds_to_snapshot(odds)
    save_snapshot(snapshot, Path(settings.snapshot_dir))

    # 3. Guard: never overwrite the live page with placeholder data.
    html_path = Path(settings.html_output_path)
    if is_sample:
        logger.warning(
            "No live Kalshi WC data — skipping HTML overwrite to preserve the "
            "last real page. Snapshot still saved for diagnostics."
        )
        return

    # 4. Generate HTML
    generate_html(odds, html_path, is_sample=is_sample)
    logger.info("Refresh complete", html=str(html_path))


if __name__ == "__main__":
    main()
