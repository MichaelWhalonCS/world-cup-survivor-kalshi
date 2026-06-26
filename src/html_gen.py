"""Generate a self-contained index.html from NationOdds data."""

from __future__ import annotations

from pathlib import Path

import jinja2
import structlog

from .config import now_in_app_tz, settings
from .odds import NationOdds
from .teams import GROUP_ROUNDS, KO_ROUNDS, ROUND_LABELS

logger = structlog.get_logger()

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"


def _prob_display(prob: float | None) -> str:
    if prob is None:
        return "—"
    if prob < 0.005:
        return "<0.1%"
    return f"{prob:.1%}"


def _prob_color(prob: float | None) -> str:
    """Background color: dark red (0%) → yellow (50%) → dark green (100%)."""
    if prob is None:
        return "#1e2228"
    p = max(0.0, min(1.0, prob))
    if p < 0.5:
        ratio = p / 0.5
        r = int(220 - ratio * 40)
        g = int(60 + ratio * 160)
        b = int(60 - ratio * 10)
    else:
        ratio = (p - 0.5) / 0.5
        r = int(180 - ratio * 140)
        g = int(220 - ratio * 40)
        b = int(50 + ratio * 30)
    return f"rgb({r},{g},{b})"


def _prob_text_color(prob: float | None) -> str:
    if prob is None:
        return "#6c757d"
    if prob < 0.15 or prob > 0.90:
        return "#ffffff"
    return "#1a1a1a"


def _best_pick_display(odds: NationOdds) -> str:
    rnd, prob = odds.best_pick
    if rnd is None or prob is None:
        return "—"
    if rnd in GROUP_ROUNDS:
        opp = odds.matchday_opponents.get(rnd, "?")
        return f"{rnd} vs {opp} ({prob:.1%})"
    return f"{rnd} ({prob:.1%})"


def _best_pick_is_safe(odds: NationOdds) -> bool:
    _, prob = odds.best_pick
    return prob is not None and prob >= odds.SAFE_THRESHOLD


_WINDOW_ORDER = ["MD1", "MD2", "MD3", "R32", "R16", "QF", "SF", "Final"]


def _best_pick_sort_value(odds: NationOdds) -> float:
    rnd, prob = odds.best_pick
    if rnd is None or prob is None:
        return -1.0
    idx = _WINDOW_ORDER.index(rnd) if rnd in _WINDOW_ORDER else 0
    return idx * 10 + prob


# Knockout "reach stage" sequence.  round_probs[X] = P(reach stage X);
# "Champion" = P(win the trophy).  A game is played at each of R32..Final.
_KO_REACH = ["R32", "R16", "QF", "SF", "Final", "Champion"]
_KO_GAME_STAGES = ["R32", "R16", "QF", "SF", "Final"]


def _win_and_out(odds: NationOdds, anchor: str) -> float | None:
    """P(win the ``anchor`` knockout game but lose the following one).

    ``anchor`` is the stage whose game is being evaluated (R32..Final).
    Winning it means reaching the next stage; losing the round after means
    not reaching the stage beyond that.  So:

        win & out = P(reach next stage) − P(reach stage after that)

    For the Final there is no "following round", so this collapses to
    P(win the trophy).  Returns None when the underlying futures are missing.
    A high value flags a likely "one-and-done" — best spent in this round
    rather than saved.
    """
    if anchor not in _KO_GAME_STAGES:
        return None
    i = _KO_REACH.index(anchor)
    win_stage = _KO_REACH[i + 1] if i + 1 < len(_KO_REACH) else None
    after_stage = _KO_REACH[i + 2] if i + 2 < len(_KO_REACH) else None

    win_p = odds.round_probs.get(win_stage) if win_stage else None
    if win_p is None:
        return None
    after_p = odds.round_probs.get(after_stage) if after_stage else None
    if after_p is None:
        return win_p  # terminal (Final → just the trophy-win probability)
    return max(0.0, win_p - after_p)


def generate_html(
    odds: list[NationOdds], output_path: Path, is_sample: bool = False
) -> None:
    """Render templates/table.html with NationOdds and write to output_path.

    When ``is_sample`` is True the page renders a loud banner warning that the
    numbers are placeholder data, not live Kalshi market prices.
    """
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=True,
    )
    template = env.get_template("table.html")

    sorted_odds = sorted(
        odds,
        key=lambda o: (o.nation.eliminated, o.nation.group, o.nation.slot),
    )

    now = now_in_app_tz()
    current_round = settings.current_round

    # "Win This Round & Out" anchor: the knockout game whose one-and-done risk
    # we score.  In the knockout stage that's the current round; during the
    # group stage (no single-game elimination) we anchor to R32, the first
    # knockout game.
    win_out_anchor = current_round if current_round in _KO_GAME_STAGES else "R32"
    win_out_label = f"Win {win_out_anchor} & Out"

    # Visible KO rounds: from current_round forward when in KO, else all KO.
    if current_round in KO_ROUNDS:
        ko_idx = KO_ROUNDS.index(current_round)
        visible_ko = KO_ROUNDS[ko_idx:]
    else:
        visible_ko = list(KO_ROUNDS)

    # Group-stage columns: show all 3 matchdays during group stage; hide
    # past matchdays once we move to knockout.
    if current_round in GROUP_ROUNDS:
        md_idx = ["MD1", "MD2", "MD3"].index(current_round)
        visible_md = ["MD1", "MD2", "MD3"][md_idx:]
    else:
        visible_md = []

    rows = []
    for o in sorted_odds:
        conds = o.conditional_ko_probs()

        md_cells = []
        for md in visible_md:
            p = o.matchday_probs.get(md)
            opp = o.matchday_opponents.get(md, "?")
            md_cells.append({
                "window": md,
                "prob": p,
                "display": _prob_display(p),
                "bg_color": _prob_color(p),
                "text_color": _prob_text_color(p),
                "opponent": opp,
                "url": o.matchday_urls.get(md),
            })

        ko_cells = []
        for rnd in visible_ko:
            cum = o.round_probs.get(rnd)
            cond = conds.get(rnd)
            ko_cells.append({
                "round": rnd,
                "prob": cum,
                "cond_prob": cond,
                "display": _prob_display(cum),
                "cond_display": _prob_display(cond),
                "bg_color": _prob_color(cum),
                "text_color": _prob_text_color(cum),
                "url": o.round_urls.get(rnd),
            })

        # Champion column (P(win trophy))
        champ_prob = o.round_probs.get("Champion")

        # "Win This Round & Out" — one-and-done indicator for the anchor round.
        win_out_prob = _win_and_out(o, win_out_anchor)

        rows.append({
            "nation": o.nation.name,
            "fifa_code": o.nation.fifa_code,
            "group": o.nation.group,
            "pot": o.nation.pot,
            "confederation": o.nation.confederation,
            "eliminated": o.nation.eliminated,
            "md_cells": md_cells,
            "ko_cells": ko_cells,
            "champ_prob": champ_prob,
            "champ_display": _prob_display(champ_prob),
            "champ_bg": _prob_color(champ_prob),
            "champ_text": _prob_text_color(champ_prob),
            "champ_url": o.round_urls.get("Champion"),
            "win_out_prob": win_out_prob,
            "win_out_display": _prob_display(win_out_prob),
            "win_out_bg": _prob_color(win_out_prob),
            "win_out_text": _prob_text_color(win_out_prob),
            "best_pick": _best_pick_display(o),
            "best_pick_is_safe": _best_pick_is_safe(o),
            "best_pick_sort": _best_pick_sort_value(o),
        })

    html = template.render(
        rows=rows,
        round_labels=ROUND_LABELS,
        visible_md=visible_md,
        visible_ko=visible_ko,
        current_round=current_round,
        win_out_label=win_out_label,
        updated_at=now.strftime("%Y-%m-%d %I:%M %p %Z"),
        nation_count=len([r for r in rows if not r["eliminated"]]),
        groups=sorted({r["group"] for r in rows}),
        confederations=sorted({r["confederation"] for r in rows}),
        is_sample=is_sample,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    logger.info("HTML generated", path=str(output_path), rows=len(rows))
