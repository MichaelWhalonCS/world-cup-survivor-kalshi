"""Tests for the per-(team, date) Kalshi ticker → matchday resolver.

The critical case: Jun 16 is MD2 for Group A teams but MD1 for Group K
teams (per data/matches.json).  A purely date-based map mis-classified
half of those games before this fix.
"""

from src.odds import (
    _kalshi_date_to_iso,
    _parse_game_ticker,
    _team_date_to_round,
)
from src.teams import FIFA_CODE_MAP


def test_kalshi_date_to_iso():
    assert _kalshi_date_to_iso("JUN11") == "2026-06-11"
    assert _kalshi_date_to_iso("JUL19") == "2026-07-19"
    assert _kalshi_date_to_iso("XXX99") is None
    assert _kalshi_date_to_iso("") is None
    assert _kalshi_date_to_iso("JUN1") is None


def test_jun16_disambiguation_group_a_vs_group_k():
    """Jun 16 is MD2 for Mexico (Group A) but MD1 for Belgium (Group K).

    Wait — actually Belgium is Group G in the verified draw. Let me use
    the verified groups: Group A has Mexico (Jun 16 = MD2 per matches.json),
    Group K has Portugal (Jun 16 = MD1 per matches.json).
    """
    # Group A — Jun 16 should be MD2
    assert _team_date_to_round("MEX", "JUN16") == "MD2"
    # Group K — Jun 16 should be MD1
    assert _team_date_to_round("POR", "JUN16") == "MD1"


def test_md1_dates_per_group():
    # Each Pot 1 team's Jun-11..Jun-16 opener is MD1
    md1_pairs = [
        ("MEX", "JUN11"),  # Group A
        ("CAN", "JUN12"),  # Group B
        ("BRA", "JUN12"),  # Group C
        ("USA", "JUN13"),  # Group D
        ("GER", "JUN13"),  # Group E
        ("NED", "JUN14"),  # Group F
        ("BEL", "JUN14"),  # Group G
        ("ESP", "JUN15"),  # Group H
        ("FRA", "JUN15"),  # Group I
        ("ARG", "JUN15"),  # Group J
        ("POR", "JUN16"),  # Group K
        ("ENG", "JUN16"),  # Group L
    ]
    for code, date in md1_pairs:
        assert _team_date_to_round(code, date) == "MD1", (
            f"{code} {date} should be MD1"
        )


def test_ko_window_falls_through_to_round():
    # R32 window: Jun 29 – Jul 3
    assert _team_date_to_round("USA", "JUN29") == "R32"
    assert _team_date_to_round("BRA", "JUL03") == "R32"
    # R16: Jul 4 – 7
    assert _team_date_to_round("ESP", "JUL05") == "R16"
    # Final: Jul 19
    assert _team_date_to_round("FRA", "JUL19") == "Final"


def test_tie_market_skipped():
    """The 'TIE' contract under each event is irrelevant for survivor."""
    team, rnd, date = _parse_game_ticker("KXFIFAGAME-26JUN11MEXKOR-TIE")
    assert team is None
    assert rnd is None
    assert date is None


def test_parse_game_ticker_happy_path():
    team, rnd, date = _parse_game_ticker("KXFIFAGAME-26JUN11MEXKOR-MEX")
    assert team == "MEX"
    assert rnd == "MD1"
    assert date == "JUN11"
    assert team in FIFA_CODE_MAP


def test_parse_game_ticker_malformed():
    # Wrong series
    assert _parse_game_ticker("KXNCAAMBGAME-26MAR19-DUKE") == (None, None, None)
    # Too short
    assert _parse_game_ticker("KXFIFAGAME-X-Y") == (None, None, None)
    # Unknown team code resolves no round → still returns code + date
    team, rnd, date = _parse_game_ticker("KXFIFAGAME-26JUN11ZZZQQQ-ZZZ")
    assert team == "ZZZ"
    assert rnd is None  # unresolved
    assert date == "JUN11"
