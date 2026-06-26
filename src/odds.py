"""Fetch 2026 FIFA World Cup odds from Kalshi.

Market types (all CONFIRMED live on Kalshi as of 2026-06-24):

1. **Per-game markets** (``KXWCGAME`` series)
   For each match Kalshi lists three binary contracts on a single event:
     KXWCGAME-{YY}{MMM}{DD}{T1}{T2}-{T1 | T2 | TIE}
   Example: ``KXWCGAME-26JUN27JORARG-ARG`` is the YES-Argentina-wins
   contract on the Jordan vs Argentina match.  The last segment is a FIFA
   code or "TIE".  Survivor pool only cares about outright wins, so the
   "TIE" markets are skipped.

2. **Per-round advancement futures** (``KXWCROUND`` series)
   Per-team "reach round X" markets — e.g. "Will USA reach the Round of
   16?".  Event suffix → round_probs key:
     26RO16  → "R16"
     26QUAR  → "QF"
     26SEMI  → "SF"
     26FINAL → "Final" (reach the final)
   There is no R32 suffix in this series — see the qualify series below.

3. **Group-qualification futures** (``KXWCGROUPQUAL`` series)
   "Will <team> qualify from its group?" = reach the Round of 32.  Every
   market in this series maps to round_probs key "R32".
   Example: ``KXWCGROUPQUAL-26L-ENG``.

4. **Champion** (event ``KXMENWORLDCUP-26``)
   Per-team "win the tournament" markets — e.g. ``KXMENWORLDCUP-26-US``.
   Stored under round_probs key "Champion".  NOTE: this event uses MIXED
   ISO codes (mostly alpha-2) that must be normalised to our FIFA codes
   (see ``_KALSHI_CODE_ALIASES`` / ``_norm_code``).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

import structlog

from .fixtures import build_group_matches, opponent_for
from .kalshi_client import get_client
from .teams import (
    FIFA_CODE_MAP,
    GROUP_ROUNDS,
    KO_ROUNDS,
    Nation,
    get_all_nations,
)

logger = structlog.get_logger()


# ── Market configuration ───────────────────────────────────────────────────────

# Per-game W/D/L markets.
SERIES_TICKER = "KXWCGAME"

# Per-round advancement futures (reach R16/QF/SF/Final).
FUTURES_SERIES: str | None = "KXWCROUND"
# Group qualification = reach R32.
GROUPQUAL_SERIES: str | None = "KXWCGROUPQUAL"
# Championship winner event.
CHAMP_EVENT: str | None = "KXMENWORLDCUP-26"

# Month-name → 2-digit month for converting Kalshi ticker dates ("JUN11")
# into ISO dates ("2026-06-11").
_MONTH_MAP = {
    "JAN": "01", "FEB": "02", "MAR": "03", "APR": "04",
    "MAY": "05", "JUN": "06", "JUL": "07", "AUG": "08",
    "SEP": "09", "OCT": "10", "NOV": "11", "DEC": "12",
}

# Kalshi KXWCROUND event suffix → our cumulative round_probs key.
_FUTURES_EVENT_TO_ROUND: dict[str, str] = {
    "26RO16":  "R16",
    "26QUAR":  "QF",
    "26SEMI":  "SF",
    "26FINAL": "Final",
}

# Kalshi team codes that differ from our data/groups.json FIFA codes.
# Applied via _norm_code() before any FIFA_CODE_MAP lookup.
#   - KXWCGAME/KXWCROUND/KXWCGROUPQUAL use FIFA codes that mostly match,
#     except DZA->ALG, HTI->HAI, IRI->IRN.
#   - KXMENWORLDCUP uses MIXED ISO codes (mostly alpha-2).
_KALSHI_CODE_ALIASES: dict[str, str] = {
    # FIFA-code mismatches (3-letter).
    "DZA": "ALG", "HTI": "HAI", "IRI": "IRN",
    # Champion-event ISO (alpha-2) → FIFA.
    "AR": "ARG", "AT": "AUT", "AU": "AUS", "BE": "BEL", "BR": "BRA",
    "CA": "CAN", "CH": "SUI", "CO": "COL", "DE": "GER", "EC": "ECU",
    "ES": "ESP", "FR": "FRA", "GB": "ENG", "GH": "GHA", "HR": "CRO",
    "IR": "IRN", "JP": "JPN", "KR": "KOR", "MA": "MAR", "MX": "MEX",
    "NL": "NED", "NO": "NOR", "PT": "POR", "PY": "PAR", "SA": "KSA",
    "SC": "SCO", "SE": "SWE", "SN": "SEN", "TN": "TUN", "TR": "TUR",
    "US": "USA", "UY": "URU",
}


def _norm_code(code: str) -> str:
    """Normalise a Kalshi team code to our canonical FIFA code."""
    return _KALSHI_CODE_ALIASES.get(code, code)


_KALSHI_GAME_URL_BASE = "https://kalshi.com/markets/kxwcgame"
_KALSHI_ROUND_URL_BASE = "https://kalshi.com/markets/kxwcround"
_KALSHI_GROUPQUAL_URL_BASE = "https://kalshi.com/markets/kxwcgroupqual"
_KALSHI_CHAMP_URL_BASE = "https://kalshi.com/markets/kxmenworldcup"


# ── Data structures ────────────────────────────────────────────────────────────

@dataclass
class NationOdds:
    """Odds for a single nation across group-stage matchdays + KO rounds."""

    nation: Nation

    # Group-stage per-match win probabilities (3-way market — team win only).
    matchday_probs: dict[str, float | None] = field(default_factory=dict)
    matchday_urls: dict[str, str] = field(default_factory=dict)
    matchday_dates: dict[str, str] = field(default_factory=dict)
    matchday_opponents: dict[str, str] = field(default_factory=dict)

    # Knockout cumulative advancement probabilities.
    # Keys: "R32", "R16", "QF", "SF", "Final".  "Final" = P(win the trophy).
    # We also store "ReachFinal" separately for conditional math.
    round_probs: dict[str, float | None] = field(default_factory=dict)
    round_urls: dict[str, str] = field(default_factory=dict)

    # Minimum game-win probability to consider a pick "safe" for survivor.
    # Lower than MM (0.70) because draws compress the per-game win range.
    SAFE_THRESHOLD: float = 0.65

    # ── Conditional KO probabilities ───────────────────────────────────────

    def conditional_ko_probs(self) -> dict[str, float | None]:
        """Conditional win probability for each KO game.

        P(win R32 game) = P(reach R16) / P(reach R32)
        P(win R16 game) = P(reach QF) / P(reach R16)
        … etc.  Returns None when monotonicity violated or upstream missing.
        """
        result: dict[str, float | None] = {}
        for i, rnd in enumerate(KO_ROUNDS):
            curr = self.round_probs.get(rnd)
            next_rnd = KO_ROUNDS[i + 1] if i + 1 < len(KO_ROUNDS) else None
            next_prob = self.round_probs.get(next_rnd) if next_rnd else None

            if rnd == "Final":
                # round_probs["Final"] = P(reach Final); round_probs["Champion"]
                # = P(win trophy).  Conditional final-game win = champ / reach.
                champ = self.round_probs.get("Champion")
                reach_final = curr
                if champ is not None and reach_final and reach_final > 0:
                    result[rnd] = min(1.0, champ / reach_final)
                else:
                    result[rnd] = None
                continue

            if curr is not None and curr > 0 and next_prob is not None:
                ratio = next_prob / curr
                if 0 < ratio < 1.0:
                    result[rnd] = ratio
                else:
                    result[rnd] = None
            else:
                result[rnd] = None
        return result

    # ── Best-pick helpers ──────────────────────────────────────────────────

    @property
    def best_matchday(self) -> tuple[str | None, float | None]:
        """Group-stage matchday with the highest per-game win probability."""
        best_md: str | None = None
        best_prob: float | None = None
        for md in ("MD1", "MD2", "MD3"):
            p = self.matchday_probs.get(md)
            if p is None:
                continue
            if best_prob is None or p > best_prob:
                best_prob = p
                best_md = md
        return best_md, best_prob

    @property
    def best_ko_round(self) -> tuple[str | None, float | None]:
        """Latest KO round where conditional game-win probability ≥ SAFE_THRESHOLD."""
        conds = self.conditional_ko_probs()
        for rnd in reversed(KO_ROUNDS):
            p = conds.get(rnd)
            if p is not None and p >= self.SAFE_THRESHOLD:
                return rnd, p
        # Fallback: highest conditional prob across KO
        best_rnd, best_p = None, -1.0
        for rnd, p in conds.items():
            if p is not None and p > best_p:
                best_p, best_rnd = p, rnd
        return (best_rnd, best_p) if best_rnd else (None, None)

    @property
    def best_pick(self) -> tuple[str | None, float | None]:
        """Overall best pick.

        Strategy: prefer the *latest* round where the conditional game-win
        probability is still safe.  Group-stage matchdays are evaluated by
        their direct per-game win probability; KO rounds by the conditional
        ratio.  Whichever round has the highest "safe" probability wins,
        with later rounds breaking ties (don't burn a strong team early).
        """
        md, md_p = self.best_matchday
        ko, ko_p = self.best_ko_round

        if md_p is None and ko_p is None:
            return None, None
        if md_p is None:
            return ko, ko_p
        if ko_p is None:
            return md, md_p
        # Prefer KO when comparable — later round = more value
        if ko_p >= self.SAFE_THRESHOLD:
            return ko, ko_p
        return md, md_p


# ── Price → probability ────────────────────────────────────────────────────────

def _normalize_price(val) -> float | None:
    """Convert a price value (dollars or cents) to a 0.0–1.0 probability."""
    if val is None:
        return None
    v = float(val)
    if v <= 0:
        return None
    return v / 100 if v > 1 else v


def price_to_prob(market: dict) -> float:
    """Convert Kalshi market prices to implied probability.

    Strategy mirrors MM: prefer last_price (most recent trade), fall back
    to yes_bid, then yes_ask.  Kalshi prices are 0.00–1.00 in dollars;
    cent-denominated values (>1) are auto-normalised.
    """
    last_price_raw = market.get("last_price_dollars") or market.get("last_price")
    yes_bid_raw = market.get("yes_bid_dollars") or market.get("yes_bid")
    yes_ask_raw = market.get("yes_ask_dollars") or market.get("yes_ask")

    last = _normalize_price(last_price_raw)
    bid = _normalize_price(yes_bid_raw)
    ask = _normalize_price(yes_ask_raw)

    if last is not None:
        return last
    if bid is not None:
        return bid
    if ask is not None:
        return ask
    return 0.0


def _orderbook_midpoint(client, ticker: str) -> float | None:
    """Implied YES probability from a market's resting order book.

    Used only as a GAP-FILLER when ``price_to_prob`` finds no summary price
    (no last trade / no top-of-book bid/ask).  Kalshi returns the book as
    ``orderbook_fp`` with ascending ``yes_dollars`` / ``no_dollars`` arrays of
    ``[price, size]``.  Best YES bid is the highest yes price; the YES ask is
    ``1 − best NO bid``.  The midpoint of those is the implied probability.
    Returns None if the book is empty or the request fails.
    """
    try:
        resp = client.get(f"/markets/{ticker}/orderbook")
    except Exception:
        logger.debug("Orderbook fetch failed", ticker=ticker, exc_info=True)
        return None

    book = resp.get("orderbook_fp") or resp.get("orderbook") or resp
    if isinstance(book, dict) and "orderbook_fp" in book:
        book = book["orderbook_fp"]
    if not isinstance(book, dict):
        return None

    yes_levels = book.get("yes_dollars") or book.get("yes") or []
    no_levels = book.get("no_dollars") or book.get("no") or []

    def _best(levels) -> float | None:
        prices = [_normalize_price(p) for p, _ in levels]
        prices = [p for p in prices if p is not None]
        return max(prices) if prices else None

    best_yes = _best(yes_levels)
    best_no = _best(no_levels)

    if best_yes is not None and best_no is not None:
        yes_ask = 1.0 - best_no
        return (best_yes + yes_ask) / 2
    if best_yes is not None:
        return best_yes
    if best_no is not None:
        return 1.0 - best_no
    return None


# ── Market parsing ─────────────────────────────────────────────────────────────

# Per-game ticker shape (CONFIRMED via Kalshi qualifiers):
#   KXFIFAGAME-26JUN11MEXKOR-MEX
#              ^^^^^^^ ^^^^^^  ^^^
#              date+matchup    team FIFA code | "TIE"


def _kalshi_date_to_iso(date_str: str) -> str | None:
    """Convert a Kalshi-embedded date like 'JUN11' → '2026-06-11'."""
    if len(date_str) != 5:
        return None
    mon, dd = date_str[:3], date_str[3:]
    m = _MONTH_MAP.get(mon)
    if m is None or not dd.isdigit():
        return None
    return f"2026-{m}-{dd}"


def _build_team_date_to_round_map() -> dict[tuple[str, str], str]:
    """Build a {(fifa_code, ISO date) → matchday code} lookup at import time.

    Resolves the date-overlap problem where the same calendar day (e.g.
    Jun 16) is MD2 for Group A and MD1 for Group K.  By keying on the
    team's FIFA code, the matchday is unambiguous.
    """
    from .fixtures import build_group_matches
    lookup: dict[tuple[str, str], str] = {}
    for m in build_group_matches():
        if m.team_a:
            lookup[(m.team_a.fifa_code, m.date)] = m.round
        if m.team_b:
            lookup[(m.team_b.fifa_code, m.date)] = m.round
    return lookup


_TEAM_DATE_TO_ROUND: dict[tuple[str, str], str] | None = None


def _team_date_to_round(team_code: str, date_str: str) -> str | None:
    """Resolve (team_code, Kalshi date prefix) → round code.

    Order of resolution:
      1. Exact group-stage match from matches.json (team + ISO date).
      2. Group-stage tolerant fallback — if the team has 3 group-stage
         dates configured and the requested date is within the group
         stage window (Jun 11–27), classify by ordinal position
         (earliest = MD1, middle = MD2, latest = MD3).  This handles
         the case where my matches.json date guesses don't quite match
         the FIFA-published per-group schedule.
      3. KO date-window match from matches.json.
    Returns None if nothing matches.
    """
    global _TEAM_DATE_TO_ROUND
    if _TEAM_DATE_TO_ROUND is None:
        _TEAM_DATE_TO_ROUND = _build_team_date_to_round_map()

    iso = _kalshi_date_to_iso(date_str)
    if iso is None:
        return None

    # 1. Exact (team, date) hit
    md = _TEAM_DATE_TO_ROUND.get((team_code, iso))
    if md:
        return md

    # 2. Tolerant group-stage fallback by ordinal position
    if "2026-06-11" <= iso <= "2026-06-27":
        team_dates = sorted(
            d for (code, d) in _TEAM_DATE_TO_ROUND.keys() if code == team_code
        )
        if len(team_dates) == 3:
            ordered = ["MD1", "MD2", "MD3"]
            # Insert iso into the sorted dates and see where it falls.
            for idx, d in enumerate(team_dates):
                if iso <= d:
                    return ordered[idx]
            return "MD3"  # later than all configured group dates

    # 3. KO date windows
    from .fixtures import knockout_windows
    windows = knockout_windows()
    for rnd in ("R32", "R16", "QF", "SF", "Final"):
        w = windows.get(rnd, {}) or {}
        start = w.get("start") or w.get("date")
        end = w.get("end") or w.get("date")
        if start and end and start <= iso <= end:
            return rnd

    return None


def _parse_game_ticker(ticker: str) -> tuple[str | None, str | None, str | None]:
    """Extract (team_fifa_code, round_code, date_str) from a game-market ticker.

    Returns (None, None, None) if the ticker is malformed, the team code
    is "TIE" (draw market — irrelevant for survivor), or the round can't
    be resolved.
    """
    parts = ticker.split("-")
    if len(parts) < 3 or parts[0] != SERIES_TICKER:
        return None, None, None

    team_code = parts[-1]
    if team_code == "TIE":
        return None, None, None
    team_code = _norm_code(team_code)

    game_part = parts[1]  # e.g. "26JUN11MEXKOR"
    if len(game_part) < 7:
        return None, None, None
    date_str = game_part[2:7]  # e.g. "JUN11"

    round_code = _team_date_to_round(team_code, date_str)
    return team_code, round_code, date_str


def _market_to_dict(market) -> dict:
    """Normalise a pykalshi market to a plain dict."""
    if isinstance(market, dict):
        return market
    if hasattr(market, "data") and hasattr(market.data, "model_dump"):
        return market.data.model_dump()
    return market.__dict__


_CLOSED_STATUSES = {"finalized", "settled", "closed", "determined"}


def _is_closed(mdict: dict) -> bool:
    status = mdict.get("status", "")
    status_str = status.value if hasattr(status, "value") else str(status)
    return status_str.lower() in _CLOSED_STATUSES


def _fetch_all_markets(client, **kwargs) -> list[dict]:
    """Fetch markets as RAW dicts via the paginated REST path.

    Uses ``client.paginated_get('/markets', 'markets', params,
    fetch_all=True)`` rather than the typed ``get_markets`` helper.  The
    typed pydantic model populates only the integer price fields
    (last_price, yes_bid, yes_ask) which are all None on these markets;
    the real prices live in the ``*_dollars`` STRING fields that exist
    only in the raw REST JSON.  Returning raw dicts lets ``price_to_prob``
    read those.  ``kwargs`` carries ``series_ticker`` OR ``event_ticker``.
    """
    params = {**kwargs, "limit": 1000}
    return client.paginated_get("/markets", "markets", params, fetch_all=True)


# ── Game markets ───────────────────────────────────────────────────────────────

def _fetch_game_markets() -> tuple[
    dict[str, dict[str, float]],   # nation_code → {round → prob}
    dict[str, dict[str, str]],     # nation_code → {round → url}
    dict[str, dict[str, str]],     # nation_code → {round → date_str}
]:
    """Fetch per-game win probabilities keyed by FIFA code and matchday round."""
    try:
        client = get_client()
        markets = _fetch_all_markets(client, series_ticker=SERIES_TICKER)
    except Exception:
        logger.warning("Could not fetch Kalshi per-game markets", exc_info=True)
        return {}, {}, {}

    probs: dict[str, dict[str, float]] = {}
    urls: dict[str, dict[str, str]] = {}
    dates: dict[str, dict[str, str]] = {}
    skipped = 0

    for mdict in markets:
        if _is_closed(mdict):
            skipped += 1
            continue

        ticker = mdict.get("ticker", "")
        event_ticker = mdict.get("event_ticker", "")
        team_code, round_code, date_str = _parse_game_ticker(ticker)
        if not team_code or not round_code:
            continue
        if team_code not in FIFA_CODE_MAP:
            continue

        prob = price_to_prob(mdict)
        if prob <= 0:
            # Gap-fill from the order book only when there's no summary price.
            prob = _orderbook_midpoint(client, ticker) or 0.0
        if prob <= 0:
            continue

        probs.setdefault(team_code, {})[round_code] = prob
        if event_ticker:
            urls.setdefault(team_code, {})[round_code] = (
                f"{_KALSHI_GAME_URL_BASE}/{event_ticker.lower()}"
            )
        if date_str:
            dates.setdefault(team_code, {})[round_code] = date_str

    logger.info("Kalshi per-game markets fetched", parsed=len(probs), skipped_closed=skipped)
    return probs, urls, dates


# ── Futures markets ────────────────────────────────────────────────────────────

def _fetch_futures() -> tuple[dict[str, dict[str, float]], dict[str, dict[str, str]]]:
    """Fetch Kalshi tournament futures.

    Returns:
        probs: nation_code → {round_code → cumulative probability}
        urls:  nation_code → {round_code → market URL}

    Returns empty dicts when none of the futures series / champion event
    are configured.
    """
    if FUTURES_SERIES is None and GROUPQUAL_SERIES is None and CHAMP_EVENT is None:
        logger.info("Kalshi tournament futures not configured — skipping")
        return {}, {}

    try:
        client = get_client()
    except Exception:
        logger.warning("Could not initialise Kalshi client for futures", exc_info=True)
        return {}, {}

    probs: dict[str, dict[str, float]] = {}
    urls: dict[str, dict[str, str]] = {}
    unresolved: set[str] = set()

    # 1. Per-round advancement (KXWCROUND → R16/QF/SF/Final)
    round_markets: list[dict] = []
    if FUTURES_SERIES:
        try:
            round_markets = _fetch_all_markets(client, series_ticker=FUTURES_SERIES)
        except Exception:
            logger.exception("Failed to fetch round futures markets")

    for mdict in round_markets:
        if _is_closed(mdict):
            continue
        ticker = mdict.get("ticker", "")
        event_ticker = mdict.get("event_ticker", "")
        # KXWCROUND-26RO16-USA
        parts = ticker.split("-")
        if len(parts) < 3:
            continue
        raw_code = parts[-1]
        event_suffix = parts[1]
        round_code = _FUTURES_EVENT_TO_ROUND.get(event_suffix)
        if round_code is None:
            continue
        team_code = _norm_code(raw_code)
        if team_code not in FIFA_CODE_MAP:
            unresolved.add(raw_code)
            continue

        prob = price_to_prob(mdict)
        if prob <= 0:
            prob = _orderbook_midpoint(client, ticker) or 0.0
        if prob > 0:
            probs.setdefault(team_code, {})[round_code] = prob
            if event_ticker:
                urls.setdefault(team_code, {})[round_code] = (
                    f"{_KALSHI_ROUND_URL_BASE}/{event_ticker.lower()}"
                )

    # 2. Group qualification (KXWCGROUPQUAL → reach R32)
    groupqual_markets: list[dict] = []
    if GROUPQUAL_SERIES:
        try:
            groupqual_markets = _fetch_all_markets(client, series_ticker=GROUPQUAL_SERIES)
        except Exception:
            logger.exception("Failed to fetch group-qualification markets")

    for mdict in groupqual_markets:
        if _is_closed(mdict):
            continue
        ticker = mdict.get("ticker", "")
        event_ticker = mdict.get("event_ticker", "")
        # KXWCGROUPQUAL-26L-ENG
        parts = ticker.split("-")
        if len(parts) < 3:
            continue
        raw_code = parts[-1]
        team_code = _norm_code(raw_code)
        if team_code not in FIFA_CODE_MAP:
            unresolved.add(raw_code)
            continue

        prob = price_to_prob(mdict)
        if prob <= 0:
            prob = _orderbook_midpoint(client, ticker) or 0.0
        if prob > 0:
            probs.setdefault(team_code, {})["R32"] = prob
            if event_ticker:
                urls.setdefault(team_code, {})["R32"] = (
                    f"{_KALSHI_GROUPQUAL_URL_BASE}/{event_ticker.lower()}"
                )

    # 3. Championship winner (KXMENWORLDCUP-26)
    champ_markets: list[dict] = []
    if CHAMP_EVENT:
        try:
            champ_markets = _fetch_all_markets(client, event_ticker=CHAMP_EVENT)
        except Exception:
            logger.exception("Failed to fetch championship futures markets")

    for mdict in champ_markets:
        if _is_closed(mdict):
            continue
        ticker = mdict.get("ticker", "")
        parts = ticker.split("-")
        if len(parts) < 3:
            continue
        raw_code = parts[-1]
        team_code = _norm_code(raw_code)
        if team_code not in FIFA_CODE_MAP:
            unresolved.add(raw_code)
            continue
        prob = price_to_prob(mdict)
        if prob <= 0:
            prob = _orderbook_midpoint(client, ticker) or 0.0
        if prob > 0:
            # Store under "Champion" to disambiguate from "reach Final".
            probs.setdefault(team_code, {})["Champion"] = prob
            event_ticker = mdict.get("event_ticker", "")
            if event_ticker:
                urls.setdefault(team_code, {})["Champion"] = (
                    f"{_KALSHI_CHAMP_URL_BASE}/{event_ticker.lower()}"
                )

    if unresolved:
        logger.warning(
            "Kalshi futures codes not resolved to a nation (out of scope)",
            codes=sorted(unresolved),
        )

    logger.info(
        "Kalshi futures fetched",
        teams=len(probs),
        rounds=sorted({r for d in probs.values() for r in d}),
        unresolved=sorted(unresolved),
    )
    return probs, urls


# ── Main fetch ─────────────────────────────────────────────────────────────────

def fetch_odds() -> tuple[list[NationOdds], bool]:
    """Fetch odds entirely from Kalshi prediction markets and assemble
    a NationOdds record for every nation in the tournament.

    Per-game markets (KXWCGAME) and tournament futures are fetched
    independently; either one alone is enough to populate the table.
    Falls back to sample odds only when BOTH are empty (e.g. before
    Kalshi has listed any WC markets).

    Returns:
        (odds, is_sample) — ``is_sample`` is True when no live Kalshi data
        was available and the returned odds are placeholder values.  Callers
        MUST surface this to users (loud banner) so fake numbers are never
        mistaken for real market prices.
    """
    futures_probs, futures_urls = _fetch_futures()
    game_probs, game_urls, game_dates = _fetch_game_markets()

    if not futures_probs and not game_probs:
        logger.warning("No Kalshi WC data yet — falling back to sample odds")
        return _generate_sample_odds(), True

    result: list[NationOdds] = []
    for nation in get_all_nations():
        code = nation.fifa_code
        round_probs = dict(futures_probs.get(code, {}))
        round_urls = dict(futures_urls.get(code, {}))

        # Per-game markets may surface both group-stage AND knockout matches.
        # Route only the group-stage ones into matchday_probs; the KO per-game
        # signal is left out until we have a use for it (e.g. a fallback when
        # futures aren't listed).
        team_games = game_probs.get(code, {})
        md_probs = {r: p for r, p in team_games.items() if r in {"MD1", "MD2", "MD3"}}
        md_urls = {
            r: u for r, u in game_urls.get(code, {}).items() if r in {"MD1", "MD2", "MD3"}
        }
        md_dates = {
            r: d for r, d in game_dates.get(code, {}).items() if r in {"MD1", "MD2", "MD3"}
        }

        # Lookup opponents for each matchday for display.
        md_opps: dict[str, str] = {}
        for md in ("MD1", "MD2", "MD3"):
            opp = opponent_for(nation, md)
            md_opps[md] = opp.fifa_code if opp else "?"

        result.append(NationOdds(
            nation=nation,
            matchday_probs=md_probs,
            matchday_urls=md_urls,
            matchday_dates=md_dates,
            matchday_opponents=md_opps,
            round_probs=round_probs,
            round_urls=round_urls,
        ))

    teams_with_futures = sum(1 for o in result if o.round_probs)
    teams_with_games = sum(1 for o in result if o.matchday_probs)
    logger.info(
        "Odds assembled",
        total_nations=len(result),
        with_futures=teams_with_futures,
        with_game_markets=teams_with_games,
    )
    return result, False


# ── Sample data generation (for dev / template testing) ────────────────────────

# Base per-game win probabilities by pot (rough soccer-survivor benchmarks).
_POT_BASE_GAME_WIN: dict[int, float] = {
    1: 0.55,   # top seeds usually 50–65% to win any group game
    2: 0.42,
    3: 0.32,
    4: 0.22,
}

# Base cumulative probability of reaching each KO round by pot.
_POT_KO_CUM: dict[int, dict[str, float]] = {
    1: {"R32": 0.92, "R16": 0.70, "QF": 0.45, "SF": 0.25, "Final": 0.13, "Champion": 0.07},
    2: {"R32": 0.75, "R16": 0.45, "QF": 0.22, "SF": 0.10, "Final": 0.04, "Champion": 0.02},
    3: {"R32": 0.55, "R16": 0.25, "QF": 0.10, "SF": 0.03, "Final": 0.01, "Champion": 0.005},
    4: {"R32": 0.30, "R16": 0.10, "QF": 0.03, "SF": 0.01, "Final": 0.002, "Champion": 0.001},
}


def _generate_sample_odds() -> list[NationOdds]:
    """Generate plausible sample odds based on pot for every nation."""
    random.seed(2026)
    out: list[NationOdds] = []
    for nation in get_all_nations():
        base = _POT_BASE_GAME_WIN.get(nation.pot, 0.20)
        md_probs: dict[str, float] = {}
        for md in ("MD1", "MD2", "MD3"):
            md_probs[md] = round(
                max(0.05, min(0.95, base + random.uniform(-0.08, 0.08))), 3
            )

        ko_template = _POT_KO_CUM.get(nation.pot, _POT_KO_CUM[4])
        round_probs: dict[str, float] = {}
        for rnd, p in ko_template.items():
            # Light noise so the table isn't perfectly uniform per pot
            jitter = random.uniform(-0.05, 0.05) * p
            round_probs[rnd] = round(max(0.0, min(1.0, p + jitter)), 4)

        md_opps = {
            md: (opponent_for(nation, md).fifa_code if opponent_for(nation, md) else "?")
            for md in ("MD1", "MD2", "MD3")
        }

        out.append(NationOdds(
            nation=nation,
            matchday_probs=md_probs,
            matchday_opponents=md_opps,
            round_probs=round_probs,
        ))
    logger.info("Generated sample odds", nations=len(out))
    return out


# ── Snapshot serialisation ─────────────────────────────────────────────────────

def odds_to_snapshot(odds: list[NationOdds]) -> list[dict]:
    """Convert NationOdds list to a JSON-serialisable snapshot."""
    snapshot = []
    for o in odds:
        best_rnd, best_prob = o.best_pick
        snapshot.append({
            "nation": o.nation.name,
            "fifa_code": o.nation.fifa_code,
            "group": o.nation.group,
            "pot": o.nation.pot,
            "confederation": o.nation.confederation,
            "eliminated": o.nation.eliminated,
            "matchday_probs": dict(o.matchday_probs),
            "matchday_urls": dict(o.matchday_urls),
            "matchday_dates": dict(o.matchday_dates),
            "matchday_opponents": dict(o.matchday_opponents),
            "round_probs": dict(o.round_probs),
            "round_urls": dict(o.round_urls),
            "best_pick_round": best_rnd,
            "best_pick_prob": best_prob,
        })
    return snapshot
