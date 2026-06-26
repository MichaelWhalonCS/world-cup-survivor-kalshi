"""Tests for the KXWCGAME-derived group schedule (Phase A) and the
groupqual settlement → elimination / Make-R32 backfill (Phases B & C).

All offline: synthetic market dicts, no live Kalshi.
"""

from src.odds import (
    _build_group_schedule,
    _parse_game_pairing,
    _settled_yes,
)


def _game(ticker, **extra):
    return {"ticker": ticker, "event_ticker": ticker.rsplit("-", 1)[0], **extra}


# ── Pairing parse ─────────────────────────────────────────────────────────────

def test_parse_game_pairing_basic():
    iso, a, b = _parse_game_pairing("KXWCGAME-26JUN22FRAIRQ-FRA")
    assert iso == "2026-06-22"
    assert {a, b} == {"FRA", "IRQ"}


def test_parse_game_pairing_normalizes_codes():
    # DZA → ALG via _norm_code, regardless of side.
    iso, a, b = _parse_game_pairing("KXWCGAME-26JUN20ALGDZA-TIE")
    assert iso == "2026-06-20"
    # second team DZA normalises to ALG
    assert "ALG" in {a, b}


def test_parse_game_pairing_malformed():
    assert _parse_game_pairing("KXFIFAGAME-26MAR31SWEPOL-SWE") == (None, None, None)
    assert _parse_game_pairing("KXWCGAME-X-Y") == (None, None, None)


# ── Schedule builder ──────────────────────────────────────────────────────────

def test_build_group_schedule_france():
    """France: MD1 vs SEN (06-16), MD2 vs IRQ (06-22), MD3 vs NOR (06-26).

    Note MD3 ticker lists France SECOND (NORFRA) — both codes must be read
    from the game part, and a TIE contract alone must still encode it.
    """
    markets = [
        _game("KXWCGAME-26JUN16FRASEN-FRA"),
        _game("KXWCGAME-26JUN16FRASEN-SEN"),
        _game("KXWCGAME-26JUN16FRASEN-TIE"),
        _game("KXWCGAME-26JUN22FRAIRQ-FRA"),
        _game("KXWCGAME-26JUN26NORFRA-TIE"),  # France is team_b here
    ]
    sched = _build_group_schedule(markets)
    assert sched["FRA"]["MD1"] == {"date": "2026-06-16", "opponent": "SEN"}
    assert sched["FRA"]["MD2"] == {"date": "2026-06-22", "opponent": "IRQ"}
    assert sched["FRA"]["MD3"] == {"date": "2026-06-26", "opponent": "NOR"}
    # Opponent's perspective is symmetric (NOR sees FRA on the same date).
    assert sched["NOR"]["MD1"] == {"date": "2026-06-26", "opponent": "FRA"}


def test_build_group_schedule_ordinal_assignment():
    """Three sorted dates map to MD1/MD2/MD3 in chronological order."""
    markets = [
        _game("KXWCGAME-26JUN22FRAIRQ-FRA"),  # middle
        _game("KXWCGAME-26JUN26NORFRA-FRA"),  # latest
        _game("KXWCGAME-26JUN16FRASEN-FRA"),  # earliest
    ]
    sched = _build_group_schedule(markets)
    assert [sched["FRA"][md]["date"] for md in ("MD1", "MD2", "MD3")] == [
        "2026-06-16", "2026-06-22", "2026-06-26",
    ]


def test_build_group_schedule_excludes_knockout_dates():
    """Games on/after the R32 window (2026-06-29) are not matchdays."""
    markets = [
        _game("KXWCGAME-26JUN16FRASEN-FRA"),
        _game("KXWCGAME-26JUL01FRAXYZ-FRA"),  # knockout — must be ignored
    ]
    sched = _build_group_schedule(markets)
    assert list(sched["FRA"].keys()) == ["MD1"]


def test_build_group_schedule_tolerates_short_count(caplog):
    """A team with <3 group games doesn't crash; fills what exists."""
    markets = [_game("KXWCGAME-26JUN16FRASEN-FRA")]
    sched = _build_group_schedule(markets)  # must not raise
    assert sched["FRA"]["MD1"]["opponent"] == "SEN"
    assert "MD2" not in sched["FRA"]


# ── Settlement reader ─────────────────────────────────────────────────────────

def test_settled_yes_reads_result_field():
    assert _settled_yes({"result": "yes"}) is True
    assert _settled_yes({"result": "no"}) is False


def test_settled_yes_price_fallback():
    # No result field → fall back to implied price.
    assert _settled_yes({"last_price_dollars": "0.99"}) is True
    assert _settled_yes({"last_price_dollars": "0.01"}) is False
    assert _settled_yes({}) is None


# ── _fetch_futures settlement → elimination + R32 backfill (Phases B & C) ──────

def test_fetch_futures_settlement_backfill(monkeypatch):
    """finalized NO ⇒ eliminated + R32=0.0; finalized YES ⇒ R32=1.0, alive;
    active ⇒ price kept, not eliminated."""
    import src.odds as odds

    groupqual = [
        {"ticker": "KXWCGROUPQUAL-26A-MEX", "event_ticker": "KXWCGROUPQUAL-26A",
         "status": "finalized", "result": "yes"},
        {"ticker": "KXWCGROUPQUAL-26A-CZE", "event_ticker": "KXWCGROUPQUAL-26A",
         "status": "finalized", "result": "no"},
        {"ticker": "KXWCGROUPQUAL-26L-ENG", "event_ticker": "KXWCGROUPQUAL-26L",
         "status": "active", "last_price_dollars": "0.85"},
        # Unknown finalized code (playoff loser) must NOT become "unresolved".
        {"ticker": "KXWCGROUPQUAL-26K-JAM", "event_ticker": "KXWCGROUPQUAL-26K",
         "status": "finalized", "result": "no"},
    ]

    def fake_fetch_all(client, **kwargs):
        if kwargs.get("series_ticker") == odds.GROUPQUAL_SERIES:
            return groupqual
        return []  # no round / champion markets

    monkeypatch.setattr(odds, "_fetch_all_markets", fake_fetch_all)
    monkeypatch.setattr(odds, "get_client", lambda: object())

    probs, urls, eliminated = odds._fetch_futures()

    assert probs["MEX"]["R32"] == 1.0          # clinched
    assert probs["CZE"]["R32"] == 0.0          # out
    assert "CZE" in eliminated
    assert "MEX" not in eliminated
    assert abs(probs["ENG"]["R32"] - 0.85) < 1e-9  # active price preserved
    assert "ENG" not in eliminated
    assert "JAM" not in eliminated             # not in field, ignored


def test_fetch_futures_stale_round_market_does_not_rescue(monkeypatch):
    """A finalized-NO groupqual is a definitive group elimination: a stale,
    still-open downstream KXWCROUND market (Kalshi lag) must NOT keep the team
    alive — a non-qualifier cannot reach a later round."""
    import src.odds as odds

    groupqual = [
        {"ticker": "KXWCGROUPQUAL-26L-PAN", "event_ticker": "KXWCGROUPQUAL-26L",
         "status": "finalized", "result": "no"},
    ]
    rounds = [
        {"ticker": "KXWCROUND-26FINAL-PAN", "event_ticker": "KXWCROUND-26FINAL",
         "status": "active", "last_price_dollars": "0.03"},
    ]

    def fake_fetch_all(client, **kwargs):
        if kwargs.get("series_ticker") == odds.GROUPQUAL_SERIES:
            return groupqual
        if kwargs.get("series_ticker") == odds.FUTURES_SERIES:
            return rounds
        return []

    monkeypatch.setattr(odds, "_fetch_all_markets", fake_fetch_all)
    monkeypatch.setattr(odds, "get_client", lambda: object())

    _, _, eliminated = odds._fetch_futures()
    assert "PAN" in eliminated  # stale Final market doesn't rescue a non-qualifier


def test_fetch_futures_active_groupqual_blocks_elimination(monkeypatch):
    """Conservative guard: never eliminate while QUALIFICATION is genuinely
    undecided. A team with a settled-NO record but a still-active groupqual
    market (qualification not yet decided) is not eliminated."""
    import src.odds as odds

    groupqual = [
        # Active groupqual → qualification still open.
        {"ticker": "KXWCGROUPQUAL-26A-MEX", "event_ticker": "KXWCGROUPQUAL-26A",
         "status": "active", "last_price_dollars": "0.30"},
    ]

    def fake_fetch_all(client, **kwargs):
        if kwargs.get("series_ticker") == odds.GROUPQUAL_SERIES:
            return groupqual
        return []

    monkeypatch.setattr(odds, "_fetch_all_markets", fake_fetch_all)
    monkeypatch.setattr(odds, "get_client", lambda: object())

    _, _, eliminated = odds._fetch_futures()
    assert "MEX" not in eliminated
