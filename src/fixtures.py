"""Group-stage and knockout schedule loader.

Loads ``data/matches.json`` and resolves group-stage matches against
``data/groups.json`` (via teams.py) into concrete ``Match`` records.

Group-stage pairing convention (FIFA standard):
    MD1: slot1 vs slot2, slot3 vs slot4
    MD2: slot1 vs slot3, slot4 vs slot2
    MD3: slot4 vs slot1, slot2 vs slot3   (all final-group matches simultaneous)

Knockout matches are not pre-resolved here — Kalshi market tickers
disambiguate which team plays which knockout match once advancement is
determined.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import structlog

from .config import settings
from .teams import GROUP_ROUNDS, KO_ROUNDS, Nation, get_group

logger = structlog.get_logger()


@dataclass
class Match:
    match_id: str         # e.g. "A-MD1-1v2"
    date: str             # ISO date "2026-06-11"
    round: str            # "MD1" | "MD2" | "MD3" | "R32" | ...
    group: str | None     # "A".."L" for group stage, None for KO
    team_a: Nation | None
    team_b: Nation | None
    venue: str | None = None


# Group-stage matchday pairing template (slot indices within a group).
_MD_PAIRINGS: dict[str, list[tuple[int, int]]] = {
    "MD1": [(1, 2), (3, 4)],
    "MD2": [(1, 3), (4, 2)],
    "MD3": [(4, 1), (2, 3)],
}


def _load_schedule() -> dict:
    try:
        return json.loads(settings.matches_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.warning("matches.json not found", path=str(settings.matches_path))
        return {}
    except json.JSONDecodeError as exc:
        logger.error("matches.json is not valid JSON", error=str(exc))
        return {}


_cached_matches: list[Match] | None = None


def build_group_matches() -> list[Match]:
    """Resolve every group-stage match into a Match record (cached)."""
    global _cached_matches
    if _cached_matches is not None:
        return _cached_matches

    raw = _load_schedule()
    schedule = raw.get("group_stage", {}).get("schedule", {})

    matches: list[Match] = []
    for group_letter, md_dates in schedule.items():
        group_nations = get_group(group_letter)
        by_slot: dict[int, Nation] = {n.slot: n for n in group_nations}

        for md, pairings in _MD_PAIRINGS.items():
            date = md_dates.get(md, "")
            for slot_a, slot_b in pairings:
                team_a = by_slot.get(slot_a)
                team_b = by_slot.get(slot_b)
                matches.append(Match(
                    match_id=f"{group_letter}-{md}-{slot_a}v{slot_b}",
                    date=date,
                    round=md,
                    group=group_letter,
                    team_a=team_a,
                    team_b=team_b,
                ))
    logger.info("Group-stage matches resolved", count=len(matches))
    _cached_matches = matches
    return matches


def group_matches_for(nation: Nation) -> dict[str, Match | None]:
    """Return {MD1, MD2, MD3 → Match} for a given nation."""
    out: dict[str, Match | None] = {"MD1": None, "MD2": None, "MD3": None}
    for m in build_group_matches():
        if m.group != nation.group:
            continue
        if m.team_a == nation or m.team_b == nation:
            out[m.round] = m
    return out


def opponent_for(nation: Nation, md: str) -> Nation | None:
    """Return the opponent for a nation in a given matchday."""
    m = group_matches_for(nation).get(md)
    if m is None:
        return None
    return m.team_b if m.team_a == nation else m.team_a


def knockout_windows() -> dict[str, dict[str, str]]:
    """Return the knockout-round date windows from matches.json."""
    raw = _load_schedule()
    return {
        rnd: raw.get("knockout", {}).get(rnd, {})
        for rnd in KO_ROUNDS + ["3rd"]
    }


def all_group_dates() -> list[str]:
    """Return sorted list of all distinct group-stage dates."""
    raw = _load_schedule()
    schedule = raw.get("group_stage", {}).get("schedule", {})
    dates: set[str] = set()
    for md_dates in schedule.values():
        dates.update(md_dates.values())
    return sorted(dates)


__all__ = [
    "GROUP_ROUNDS",
    "KO_ROUNDS",
    "Match",
    "build_group_matches",
    "group_matches_for",
    "opponent_for",
    "knockout_windows",
    "all_group_dates",
]
