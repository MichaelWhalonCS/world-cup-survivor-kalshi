"""Survivor-pool optimisation: pick a unique team per pick window that
maximises the probability of surviving the whole tournament.

For a World Cup survivor pool we treat each matchday (MD1, MD2, MD3) and
each knockout round (R32, R16, QF, SF, Final) as a separate pick window.
A valid series assigns exactly one team to each visible window, with no
team used more than once.  The "score" is the product of conditional
game-win probabilities — the probability of surviving every window.

Uses a beam search (width 200) over the 48 nations to stay fast.
"""

from __future__ import annotations

import math

import structlog

from .odds import NationOdds
from .teams import GROUP_ROUNDS, KO_ROUNDS

logger = structlog.get_logger()

BEAM_WIDTH = 200

ALL_WINDOWS = ["MD1", "MD2", "MD3"] + KO_ROUNDS


def _window_prob(odds: NationOdds, window: str) -> float | None:
    """Return the relevant per-window probability for a nation.

    Group-stage windows → direct per-game win probability.
    Knockout windows → conditional game-win probability for that round.
    """
    if window in GROUP_ROUNDS:
        return odds.matchday_probs.get(window)
    conds = odds.conditional_ko_probs()
    return conds.get(window)


def best_survivor_series(
    odds: list[NationOdds],
    windows: list[str],
    top_n: int = 3,
) -> list[list[dict]]:
    """Find the top-N pick series for the given pick windows.

    Returns a list of series.  Each series is a list of dicts:
        [{"window": "MD1", "nation": "USA", "fifa_code": "USA",
          "group": "C", "pot": 1, "cond_prob": 0.72}, ...]
    sorted by descending overall survival probability.
    """
    if not windows or not odds:
        return []

    # Pre-compute conditional probs for every active nation
    candidates: list[tuple[NationOdds, dict[str, float | None]]] = []
    for o in odds:
        if o.nation.eliminated:
            continue
        per_window = {w: _window_prob(o, w) for w in windows}
        candidates.append((o, per_window))

    # Beam state: (cumulative_neg_log, picks_so_far, used_fifa_codes)
    beam: list[tuple[float, list[dict], frozenset[str]]] = [(0.0, [], frozenset())]

    for window in windows:
        next_beam: list[tuple[float, list[dict], frozenset[str]]] = []
        for neg_log, picks, used in beam:
            for o, per_window in candidates:
                code = o.nation.fifa_code
                if code in used:
                    continue
                cp = per_window.get(window)
                if cp is None or cp <= 0:
                    continue
                pick = {
                    "window": window,
                    "nation": o.nation.name,
                    "fifa_code": code,
                    "group": o.nation.group,
                    "pot": o.nation.pot,
                    "cond_prob": cp,
                    "url": (
                        o.matchday_urls.get(window)
                        if window in GROUP_ROUNDS
                        else o.round_urls.get(window)
                    ),
                }
                next_beam.append((
                    neg_log - math.log(cp),
                    picks + [pick],
                    used | {code},
                ))
        if not next_beam:
            break
        next_beam.sort(key=lambda x: x[0])
        beam = next_beam[:BEAM_WIDTH]

    seen: set[frozenset[str]] = set()
    results: list[tuple[float, list[dict]]] = []
    for neg_log, picks, used in beam:
        if used in seen:
            continue
        seen.add(used)
        survival = math.exp(-neg_log)
        picks_copy = [dict(p) for p in picks]
        for p in picks_copy:
            p["survival"] = survival
        results.append((survival, picks_copy))
        if len(results) >= top_n:
            break

    results.sort(key=lambda x: -x[0])
    return [series for _, series in results]
