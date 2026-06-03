"""Fetch 2026 FIFA World Cup odds from Kalshi.

Two market types:

1. **Per-game markets** (``KXFIFAGAME`` series — CONFIRMED live on Kalshi)
   For each match Kalshi lists three binary contracts on a single event:
     KXFIFAGAME-{YY}{MMM}{DD}{T1}{T2}-{T1 | T2 | TIE}
   Example: ``KXFIFAGAME-26MAR31SWEPOL-SWE`` is the YES-Sweden-wins
   contract on the Sweden vs Poland match on Mar 31, 2026.
   Survivor pool only cares about outright wins, so the "TIE" markets
   are skipped.

2. **Tournament futures** (``FUTURES_SERIES`` / ``CHAMP_EVENT``)
   Per-team, per-round advancement markets — e.g. "Will USA reach the
   Round of 16?"  As of 2026-06-02, Kalshi has NOT yet listed any WC
   tournament futures.  We expect them to appear close to the kickoff
   on Jun 11.  When they do, plug the discovered tickers into
   ``FUTURES_SERIES`` / ``CHAMP_EVENT`` below and the rest of the
   pipeline will pick them up automatically.

   Conjectured event suffix → round_probs key mapping (verify when listed):
     R32 → "R32"  (reaching R32 = surviving the group stage)
     R16 → "R16"
     QF  → "QF"
     SF  → "SF"
     FNL → "Final" (reaching the final)
     winner-of-tournament event → "Champion"
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

# Per-game W/D/L markets — confirmed live on Kalshi as of 2026-06-02.
SERIES_TICKER = "KXFIFAGAME"

# Tournament futures — NOT YET LISTED by Kalshi as of 2026-06-02.  Set to None
# until they appear; the fetch logic will skip futures cleanly when None.
FUTURES_SERIES: str | None = None       # e.g. "KXFIFAWC26ROUND" once listed
CHAMP_EVENT: str | None = None          # e.g. "KXFIFAWC-26" once listed

# Map of Kalshi date strings (embedded in tickers) → matchday round code.
# Group-stage dates pulled from data/matches.json — keep this in sync if the
# schedule there changes.
_DATE_TO_ROUND: dict[str, str] = {
    # MD1 — Jun 11–16
    "JUN11": "MD1", "JUN12": "MD1", "JUN13": "MD1",
    "JUN14": "MD1", "JUN15": "MD1", "JUN16": "MD1",
    # MD2 — Jun 16–22
    "JUN17": "MD2", "JUN18": "MD2", "JUN19": "MD2",
    "JUN20": "MD2", "JUN21": "MD2", "JUN22": "MD2",
    # MD3 — Jun 25–27 (all final-group matches simultaneous within group)
    "JUN25": "MD3", "JUN26": "MD3", "JUN27": "MD3",
    # KO windows — exact dates vary
    "JUN29": "R32", "JUN30": "R32",
    "JUL01": "R32", "JUL02": "R32", "JUL03": "R32",
    "JUL04": "R16", "JUL05": "R16", "JUL06": "R16", "JUL07": "R16",
    "JUL09": "QF",  "JUL10": "QF",  "JUL11": "QF",
    "JUL14": "SF",  "JUL15": "SF",
    "JUL18": "3rd",
    "JUL19": "Final",
}

# Kalshi futures event suffix → our cumulative round_probs key.
_FUTURES_EVENT_TO_ROUND: dict[str, str] = {
    "26R32": "R32",
    "26R16": "R16",
    "26QF":  "QF",
    "26SF":  "SF",
    "26FNL": "Final",
    "26F":   "Final",  # tolerant fallback
}

_KALSHI_GAME_URL_BASE = "https://kalshi.com/markets/kxfifagame"
_KALSHI_ROUND_URL_BASE = "https://kalshi.com/markets/kxfifawc26round"
_KALSHI_CHAMP_URL_BASE = "https://kalshi.com/markets/kxfifawc"


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
                # Conditional final win = P(win trophy) / P(reach Final).
                # We store P(reach Final) as round_probs["Final"] — wait, this
                # conflicts with P(win trophy).  Treat round_probs["Final"]
                # as P(reach Final) and self.round_probs.get("Champion") as
                # P(win).  Fall back to None when missing.
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


# ── Market parsing ─────────────────────────────────────────────────────────────

# Per-game ticker shape — PLACEHOLDER format (verify via discover_tickers.py):
#   KXFIFAWC26GAME-26JUN11MEXNOR-MEX
#                  ^^^^^^^ ^^^^^^  ^^^
#                  date+matchup    team FIFA code
#
# We extract:
#   - team_code (last segment)
#   - round_code (from JUNxx / JULxx prefix)

def _parse_game_ticker(ticker: str) -> tuple[str | None, str | None, str | None]:
    """Extract (team_fifa_code, round_code, date_str) from a game-market ticker.

    Returns (None, None, None) if it doesn't match the expected shape.
    """
    parts = ticker.split("-")
    if len(parts) < 3 or parts[0] != SERIES_TICKER:
        return None, None, None

    team_code = parts[-1]
    game_part = parts[1]  # e.g. "26JUN11MEXNOR"
    if len(game_part) < 7:
        return None, None, None
    date_str = game_part[2:7]  # e.g. "JUN11"

    round_code = _DATE_TO_ROUND.get(date_str)
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
    """Fetch markets via pykalshi and convert to plain dicts."""
    result = client.get_markets(**kwargs, limit=1000)
    if isinstance(result, dict):
        raw = result.get("markets", [])
    elif hasattr(result, "to_dicts"):
        raw = result.to_dicts()
    else:
        raw = list(result)
    return [_market_to_dict(m) for m in raw]


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

    Returns empty dicts when ``FUTURES_SERIES`` / ``CHAMP_EVENT`` haven't
    been set yet (Kalshi hasn't listed them).
    """
    if FUTURES_SERIES is None and CHAMP_EVENT is None:
        logger.info("Kalshi tournament futures not yet listed — skipping")
        return {}, {}

    try:
        client = get_client()
    except Exception:
        logger.warning("Could not initialise Kalshi client for futures", exc_info=True)
        return {}, {}

    # 1. Per-round advancement
    round_markets: list[dict] = []
    if FUTURES_SERIES:
        try:
            round_markets = _fetch_all_markets(client, series_ticker=FUTURES_SERIES)
        except Exception:
            logger.exception("Failed to fetch round futures markets")

    probs: dict[str, dict[str, float]] = {}
    urls: dict[str, dict[str, str]] = {}

    for mdict in round_markets:
        if _is_closed(mdict):
            continue
        ticker = mdict.get("ticker", "")
        event_ticker = mdict.get("event_ticker", "")
        # KXFIFAWC26ROUND-26R16-USA
        parts = ticker.split("-")
        if len(parts) < 3:
            continue
        team_code = parts[-1]
        event_suffix = parts[1]
        round_code = _FUTURES_EVENT_TO_ROUND.get(event_suffix)
        if round_code is None or team_code not in FIFA_CODE_MAP:
            continue

        prob = price_to_prob(mdict)
        if prob > 0:
            probs.setdefault(team_code, {})[round_code] = prob
            if event_ticker:
                urls.setdefault(team_code, {})[round_code] = (
                    f"{_KALSHI_ROUND_URL_BASE}/{event_ticker.lower()}"
                )

    # 2. Championship winner
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
        team_code = parts[-1]
        if team_code not in FIFA_CODE_MAP:
            continue
        prob = price_to_prob(mdict)
        if prob > 0:
            # Store under "Champion" to disambiguate from "reach Final".
            probs.setdefault(team_code, {})["Champion"] = prob
            event_ticker = mdict.get("event_ticker", "")
            if event_ticker:
                urls.setdefault(team_code, {})["Champion"] = (
                    f"{_KALSHI_CHAMP_URL_BASE}/{event_ticker.lower()}"
                )

    logger.info(
        "Kalshi futures fetched",
        teams=len(probs),
        rounds=sorted({r for d in probs.values() for r in d}),
    )
    return probs, urls


# ── Main fetch ─────────────────────────────────────────────────────────────────

def fetch_odds() -> list[NationOdds]:
    """Fetch odds entirely from Kalshi prediction markets and assemble
    a NationOdds record for every nation in the tournament.

    Per-game markets (KXFIFAGAME) and tournament futures are fetched
    independently; either one alone is enough to populate the table.
    Falls back to sample odds only when BOTH are empty (e.g. before
    Kalshi has listed any WC markets).
    """
    futures_probs, futures_urls = _fetch_futures()
    game_probs, game_urls, game_dates = _fetch_game_markets()

    if not futures_probs and not game_probs:
        logger.warning("No Kalshi WC data yet — falling back to sample odds")
        return _generate_sample_odds()

    result: list[NationOdds] = []
    for nation in get_all_nations():
        code = nation.fifa_code
        round_probs = dict(futures_probs.get(code, {}))
        round_urls = dict(futures_urls.get(code, {}))

        md_probs = dict(game_probs.get(code, {}))
        md_urls = dict(game_urls.get(code, {}))
        md_dates = dict(game_dates.get(code, {}))

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
    return result


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
