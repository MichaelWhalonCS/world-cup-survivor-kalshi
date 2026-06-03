#!/usr/bin/env python3
"""Discover Kalshi tickers for 2026 FIFA World Cup markets.

Strategy:
  1. Probe a list of plausible series tickers directly. The cheapest signal
     is "this series ticker returns markets" — if it does, dump a sample.
  2. Page through events filtered by sports categories and grep titles
     for soccer / World Cup keywords.
  3. Page through all currently open markets and grep titles / subtitles.

Run:
    python scripts/discover_tickers.py
"""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import structlog

structlog.configure(
    processors=[structlog.dev.ConsoleRenderer()],
    wrapper_class=structlog.BoundLogger,
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)

logger = structlog.get_logger()

from src.kalshi_client import get_client  # noqa: E402


# ── Coerce pykalshi models / DataFrameList → list[dict] ────────────────────────

def _to_dict(obj) -> dict:
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "data") and hasattr(obj.data, "model_dump"):
        return obj.data.model_dump()
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    return getattr(obj, "__dict__", {}) or {}


def _to_list(result) -> list[dict]:
    if isinstance(result, dict):
        for key in ("markets", "events", "series"):
            if key in result:
                result = result[key]
                break
    if hasattr(result, "to_dicts"):
        return result.to_dicts()
    if isinstance(result, list):
        return [_to_dict(r) for r in result]
    return []


# Plausible series-ticker guesses for the 2026 WC.
CANDIDATE_SERIES = [
    "KXFIFAWC26GAME",
    "KXFIFAWC26ROUND",
    "KXFIFAWC26",
    "KXFIFAWC",
    "KXFIFAWORLDCUP",
    "KXWORLDCUP26",
    "KXWC26",
    "KXWC2026",
    "KXSOCCERWC",
    "KXSOCCERWC26",
    "KXSOCCER",
    "KXFIFA",
    "KXFIFAMWC",
    "KXFIFAMWC26",
    "KXWCMENS26",
    "KXFIFAMENWORLDCUP",
]

KEYWORDS = (
    "world cup", "fifa", "wc26", "wc 2026", "soccer", "football",
    "round of 16", "round of 32", "quarter", "semi", "group",
)


def probe_series(client, ticker: str) -> list[dict]:
    """Try fetching markets for a specific series_ticker. Return the markets list or []."""
    try:
        result = client.get_markets(series_ticker=ticker, limit=50)
        markets = _to_list(result)
        return markets
    except Exception as exc:
        msg = str(exc)
        if "not found" in msg.lower() or "404" in msg:
            return []
        logger.debug("probe error", series=ticker, error=msg)
        return []


def crawl_events(client) -> list[dict]:
    """Page through events without filtering — return all dicts."""
    all_events: list[dict] = []
    cursor = None
    pages = 0
    while pages < 20:  # safety cap
        kwargs = {"limit": 200}
        if cursor:
            kwargs["cursor"] = cursor
        try:
            result = client.get_events(**kwargs)
        except Exception as exc:
            logger.warning("get_events failed", error=str(exc), page=pages)
            break
        raw = _to_list(result)
        if not raw:
            break
        all_events.extend(raw)
        # cursor extraction — pykalshi may surface it on the wrapper
        cursor = getattr(result, "cursor", None) or (
            result.get("cursor") if isinstance(result, dict) else None
        )
        if not cursor:
            break
        pages += 1
    logger.info("Events crawled", count=len(all_events), pages=pages + 1)
    return all_events


def crawl_markets(client) -> list[dict]:
    """Page through markets — return all dicts."""
    all_markets: list[dict] = []
    cursor = None
    pages = 0
    while pages < 20:
        kwargs = {"limit": 500}
        if cursor:
            kwargs["cursor"] = cursor
        try:
            result = client.get_markets(**kwargs)
        except Exception as exc:
            logger.warning("get_markets failed", error=str(exc), page=pages)
            break
        raw = _to_list(result)
        if not raw:
            break
        all_markets.extend(raw)
        cursor = getattr(result, "cursor", None) or (
            result.get("cursor") if isinstance(result, dict) else None
        )
        if not cursor:
            break
        pages += 1
    logger.info("Markets crawled", count=len(all_markets), pages=pages + 1)
    return all_markets


def filter_by_keywords(items: list[dict], fields: tuple[str, ...]) -> list[dict]:
    matches: list[dict] = []
    for item in items:
        blob = " ".join(str(item.get(f, "") or "") for f in fields).lower()
        if any(k in blob for k in KEYWORDS):
            matches.append(item)
    return matches


def main():
    print("\n" + "=" * 80)
    print("KALSHI 2026 FIFA WORLD CUP MARKET DISCOVERY")
    print("=" * 80)

    client = get_client()

    # ── Pass 1: probe candidate series tickers ─────────────────────────
    print("\n--- Probing candidate series tickers ---")
    hits = {}
    for ticker in CANDIDATE_SERIES:
        markets = probe_series(client, ticker)
        if markets:
            hits[ticker] = markets
            print(f"  ✓ {ticker}: {len(markets)} markets")
        else:
            print(f"  · {ticker}: 0")

    if hits:
        print("\n  Sample markets per series:")
        for ticker, markets in hits.items():
            print(f"\n  [{ticker}]")
            for m in markets[:5]:
                t = m.get("ticker", "?")
                title = m.get("title", "?")
                event = m.get("event_ticker", "?")
                series = m.get("series_ticker", "?")
                yes_bid = m.get("yes_bid", "?")
                yes_ask = m.get("yes_ask", "?")
                last = m.get("last_price", "?")
                print(f"    {t:45s} | {title}")
                print(f"      event={event}  series={series}  bid/ask={yes_bid}/{yes_ask}  last={last}")

    # ── Pass 2: crawl events + filter by keyword ───────────────────────
    print("\n--- Crawling events ---")
    events = crawl_events(client)
    event_hits = filter_by_keywords(events, ("title", "sub_title", "ticker", "category"))
    print(f"  Total events: {len(events)} | matching WC/soccer keywords: {len(event_hits)}")
    for e in event_hits[:30]:
        ticker = e.get("ticker", "?")
        title = e.get("title", "?")
        series = e.get("series_ticker", "")
        category = e.get("category", "")
        print(f"  EVENT {ticker:45s} | {title} ({category}) [series={series}]")

    # ── Pass 3: crawl markets + filter by keyword ──────────────────────
    print("\n--- Crawling markets ---")
    markets = crawl_markets(client)
    market_hits = filter_by_keywords(markets, ("title", "subtitle", "ticker", "category", "event_ticker"))
    print(f"  Total markets: {len(markets)} | matching WC/soccer keywords: {len(market_hits)}")
    for m in market_hits[:40]:
        t = m.get("ticker", "?")
        title = m.get("title", "?")
        subtitle = m.get("subtitle", "")
        series = m.get("series_ticker", "?")
        event = m.get("event_ticker", "?")
        print(f"  MARKET {t:45s} | {title}")
        if subtitle:
            print(f"         subtitle: {subtitle}")
        print(f"         event={event}  series={series}")

    # ── Summary ────────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)

    series_seen: set[str] = set(hits.keys())
    for e in event_hits + market_hits:
        s = e.get("series_ticker") or ""
        if s:
            series_seen.add(s)

    print(f"  Series tickers with WC/soccer signal: {sorted(series_seen) or '(none)'}")
    print(f"  Total events scanned: {len(events)}")
    print(f"  Total markets scanned: {len(markets)}")
    print("\nNext step: update SERIES_TICKER / FUTURES_SERIES / CHAMP_EVENT in src/odds.py.")


if __name__ == "__main__":
    main()
