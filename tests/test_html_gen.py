"""Tests for the 'Win This Round & Out' one-and-done column and the
order-book price gap-filler."""

from src.html_gen import _win_and_out
from src.odds import NationOdds, _orderbook_midpoint
from src.teams import KO_ROUNDS, ROUND_LABELS, Nation


# ── KO column labels must name the SAME stage their data represents ───────────
# round_probs[X] = P(reach stage X), so the header for column X must read
# "Make <X>" — never the next stage (regression for the off-by-one bug where
# R32's P(reach R32) was displayed under a "Make R16" header).

def test_ko_labels_name_their_own_stage():
    expected = {
        "R32": "Make R32",
        "R16": "Make R16",
        "QF": "Make QF",
        "SF": "Make SF",
        "Final": "Make Final",
    }
    for rnd in KO_ROUNDS:
        assert ROUND_LABELS[rnd] == expected[rnd]


def test_ko_labels_are_not_off_by_one():
    # No KO label may name the stage that comes AFTER it.
    nxt = {"R32": "R16", "R16": "QF", "QF": "SF", "SF": "Final"}
    for rnd, after in nxt.items():
        assert ROUND_LABELS[after] not in ROUND_LABELS[rnd]


def _nation(code="FRA"):
    return Nation(
        name="France", fifa_code=code, group="I", slot=1, pot=1,
        confederation="UEFA",
    )


def _odds(round_probs):
    return NationOdds(nation=_nation(), round_probs=round_probs)


# ── Win & Out ────────────────────────────────────────────────────────────────

def test_win_and_out_basic():
    # Win R32 game (reach R16) but lose R16 game (don't reach QF).
    o = _odds({"R16": 0.80, "QF": 0.50})
    assert abs(_win_and_out(o, "R32") - 0.30) < 1e-9


def test_win_and_out_final_is_terminal():
    # The Final has no following round → collapses to P(win the trophy).
    o = _odds({"Final": 0.30, "Champion": 0.12})
    assert _win_and_out(o, "Final") == 0.12


def test_win_and_out_missing_futures_returns_none():
    o = _odds({"R16": 0.80})  # QF missing
    # win stage present, following stage missing → terminal value (win prob)
    assert _win_and_out(o, "R32") == 0.80
    # win stage itself missing → None
    assert _win_and_out(_odds({}), "R32") is None


def test_win_and_out_rejects_non_game_stage():
    o = _odds({"R16": 0.8, "QF": 0.5})
    assert _win_and_out(o, "Champion") is None
    assert _win_and_out(o, "MD1") is None


def test_win_and_out_clamps_non_monotonic_to_zero():
    # Independent markets can be non-monotonic; never show a negative.
    o = _odds({"R16": 0.40, "QF": 0.50})
    assert _win_and_out(o, "R32") == 0.0


# ── Order-book midpoint gap-filler ─────────────────────────────────────────────

class _FakeClient:
    def __init__(self, book):
        self._book = book

    def get(self, endpoint):
        return {"orderbook_fp": self._book}


def test_orderbook_midpoint_from_both_sides():
    # best yes bid 0.0610; best no bid 0.9380 → yes ask 0.0620; mid ≈ 0.0615
    book = {
        "yes_dollars": [["0.0010", "5"], ["0.0610", "8"]],
        "no_dollars": [["0.0010", "5"], ["0.9380", "3"]],
    }
    mid = _orderbook_midpoint(_FakeClient(book), "X")
    assert mid is not None and abs(mid - 0.0615) < 1e-6


def test_orderbook_midpoint_empty_book_is_none():
    assert _orderbook_midpoint(_FakeClient({"yes_dollars": [], "no_dollars": []}), "X") is None


def test_orderbook_midpoint_request_failure_is_none():
    class Boom:
        def get(self, endpoint):
            raise RuntimeError("network")
    assert _orderbook_midpoint(Boom(), "X") is None
