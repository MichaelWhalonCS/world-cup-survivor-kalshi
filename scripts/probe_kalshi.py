#!/usr/bin/env python3
"""Pull every KXFIFAGAME event + probe for WC-specific futures series.

Run repeatedly as we approach kickoff (Jun 11, 2026) to detect when
Kalshi lists the World Cup slate.
"""

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.kalshi_client import get_client


def _to_dict(obj) -> dict:
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "data") and hasattr(obj.data, "model_dump"):
        return obj.data.model_dump()
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    return getattr(obj, "__dict__", {}) or {}


def _to_list(result) -> list[dict]:
    if hasattr(result, "to_dicts"):
        return result.to_dicts()
    return [_to_dict(x) for x in (result or [])]


client = get_client()

# ── 1. KXFIFAGAME: pull all events, bucket by year/month prefix ──────
print("== KXFIFAGAME: events bucketed by month ==")
ev = client.get_events(series_ticker="KXFIFAGAME", limit=200)
events = _to_list(ev)
print(f"  fetched: {len(events)}\n")

buckets: dict[str, list[dict]] = {}
for e in events:
    et = e.get("event_ticker", "")
    if et.startswith("KXFIFAGAME-"):
        rest = et[len("KXFIFAGAME-"):]
        key = rest[:5]  # e.g. "26JUN" or "25DEC"
        buckets.setdefault(key, []).append(e)

for key in sorted(buckets.keys()):
    print(f"  {key}: {len(buckets[key])} events")

# ── 2. WC-window events (Jun/Jul 2026) ──────────────────────────────
wc_events = [
    e for e in events
    if any(
        e.get("event_ticker", "").startswith(p)
        for p in ("KXFIFAGAME-26JUN", "KXFIFAGAME-26JUL")
    )
]
print(f"\n== WC-window events: {len(wc_events)} ==")
for e in wc_events[:40]:
    et = e.get("event_ticker", "?")
    title = e.get("title", "?")
    st = e.get("sub_title", "")
    print(f"  {et:45s} | {title} | {st}")

# ── 3. Probe WC-specific futures series ──────────────────────────────
print("\n== Probing WC-specific futures series ==")
candidates = [
    "KXFIFAWCWINNER", "KXFIFAWCWINNER26", "KXFIFAWC26WINNER",
    "KXFIFAMENSWORLDCUPWINNER", "KXFIFAWCCHAMP", "KXWCWINNER",
    "KXFIFAWORLDCUPWINNER", "KXWORLDCUPWINNER",
    "KXFIFAWC", "KXFIFAWC26", "KXFIFAWC26ROUND", "KXFIFAQUALIFY",
    "KXFIFAGROUP", "KXFIFAROUND", "KXFIFAWCROUND",
    "KXFIFAGOAL", "KXSOCCERFUTURES",
    # Possible NCAA-Madness-style patterns
    "KXFIFAWCWIN", "KXWCFINAL", "KXFIFAWCFINAL",
    "KXFIFAMENWC", "KXFIFAMENWC26",
    # Wild guesses
    "KXFIFAWORLDCUP2026", "KXWORLDCUP2026", "KXWC2026WINNER",
]
found_series = []
for ticker in candidates:
    try:
        s = client.get_series(series_ticker=ticker)
        d = _to_dict(s)
        print(f"  EXISTS  {ticker:35s} | {d.get('title', '?')} [{d.get('category', '?')}]")
        found_series.append(ticker)
    except Exception as exc:
        msg = str(exc)
        if "not_found" not in msg.lower() and "404" not in msg:
            print(f"  ERR     {ticker:35s} | {msg[:60]}")

# ── 4. For any newly-found series, dump events ──────────────────────
for ticker in found_series:
    print(f"\n== Events under {ticker} ==")
    try:
        ev2 = client.get_events(series_ticker=ticker, limit=200)
        e_list = _to_list(ev2)
        print(f"  {len(e_list)} events")
        for e in e_list[:30]:
            et = e.get("event_ticker", "?")
            title = e.get("title", "?")
            st = e.get("sub_title", "")
            print(f"  {et:45s} | {title} | {st}")
    except Exception as exc:
        print(f"  failed: {exc}")
