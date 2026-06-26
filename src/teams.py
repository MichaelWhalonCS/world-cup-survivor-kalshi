"""Tournament nations, groups, pots, and name normalization.

This module defines the 48 nations for the 2026 FIFA World Cup.  Data is
loaded from ``data/groups.json`` at import time so the bracket can be
updated without code changes.

The ``fifa_code`` field on each Nation is the canonical identifier we
use to map Kalshi market tickers (e.g. USA, MEX, BRA) → display names.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import structlog

from .config import settings

logger = structlog.get_logger()


# ── Round definitions ──────────────────────────────────────────────────────────
# Group-stage matchdays first, then knockout rounds.

ROUNDS = ["MD1", "MD2", "MD3", "R32", "R16", "QF", "SF", "Final"]

ROUND_LABELS = {
    "MD1":   "Matchday 1",
    "MD2":   "Matchday 2",
    "MD3":   "Matchday 3",
    # Each KO label names the SAME stage its data represents.
    # round_probs[X] = P(reach stage X):
    #   R32   = P(qualify from group / reach Round of 32)   [KXWCGROUPQUAL]
    #   R16   = P(reach Round of 16)                        [KXWCROUND 26RO16]
    #   QF    = P(reach Quarter-finals)                     [KXWCROUND 26QUAR]
    #   SF    = P(reach Semi-finals)                        [KXWCROUND 26SEMI]
    #   Final = P(reach the Final)                          [KXWCROUND 26FINAL]
    # The separate "Win Cup" column carries P(win the trophy) [KXMENWORLDCUP].
    "R32":   "Make R32",
    "R16":   "Make R16",
    "QF":    "Make QF",
    "SF":    "Make SF",
    "Final": "Make Final",
}

# Rounds that live in the group stage — survivor logic is per-matchday rather
# than per-round here.
GROUP_ROUNDS = {"MD1", "MD2", "MD3"}

# Cumulative-advancement rounds (knockout) — these map to Kalshi futures.
KO_ROUNDS = ["R32", "R16", "QF", "SF", "Final"]


# ── Nation dataclass ───────────────────────────────────────────────────────────

@dataclass
class Nation:
    name: str             # Display name, e.g. "United States"
    fifa_code: str        # 3-letter FIFA code, e.g. "USA"
    group: str            # "A".."L"
    slot: int             # 1..4 — position within group (1 = top seed by pot)
    pot: int              # 1..4
    confederation: str    # "UEFA" | "CONMEBOL" | "CONCACAF" | "AFC" | "CAF" | "OFC"
    eliminated: bool = False


# ── Load nations from JSON ─────────────────────────────────────────────────────

def _load_nations() -> list[Nation]:
    """Load the 48 nations from data/groups.json."""
    try:
        raw = json.loads(settings.groups_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.warning("groups.json not found", path=str(settings.groups_path))
        return []
    except json.JSONDecodeError as exc:
        logger.error("groups.json is not valid JSON", error=str(exc))
        return []

    meta = raw.get("_meta", {})
    if not meta.get("verified", False):
        logger.warning(
            "Using unverified groups.json — verify against fifa.com before publishing",
            source_note=meta.get("source_note", ""),
        )

    nations: list[Nation] = []
    for group_letter, slots in raw.get("groups", {}).items():
        for slot in slots:
            nations.append(Nation(
                name=slot["name"],
                fifa_code=slot["fifa_code"],
                group=group_letter,
                slot=int(slot["slot"]),
                pot=int(slot["pot"]),
                confederation=slot["confederation"],
            ))
    logger.info("Nations loaded", count=len(nations), groups=len(raw.get("groups", {})))
    return nations


NATIONS: list[Nation] = _load_nations()


# ── FIFA-code → Nation lookup ──────────────────────────────────────────────────

FIFA_CODE_MAP: dict[str, Nation] = {n.fifa_code: n for n in NATIONS}


# ── Name aliases ───────────────────────────────────────────────────────────────
# Maps alternative names (lowercase) → canonical FIFA code for flexible lookups.
# Used when Kalshi market titles contain a full country name rather than a code.

ALIASES: dict[str, str] = {
    "united states": "USA",
    "u.s.a.": "USA",
    "u.s.": "USA",
    "us": "USA",
    "team usa": "USA",
    "south korea": "KOR",
    "republic of korea": "KOR",
    "korea": "KOR",
    "ivory coast": "CIV",
    "cote d'ivoire": "CIV",
    "côte d'ivoire": "CIV",
    "iran": "IRN",
    "i.r. iran": "IRN",
    "czech republic": "CZE",
    "czechia": "CZE",
    "cape verde": "CPV",
    "cabo verde": "CPV",
    "saudi arabia": "KSA",
    "ksa": "KSA",
    "new zealand": "NZL",
    "the netherlands": "NED",
    "holland": "NED",
    "england": "ENG",
    "great britain": "ENG",
    "wales": "WAL",
    "scotland": "SCO",
    "germany": "GER",
    "deutschland": "GER",
    "spain": "ESP",
    "españa": "ESP",
    "france": "FRA",
    "portugal": "POR",
    "brazil": "BRA",
    "brasil": "BRA",
    "argentina": "ARG",
    "uruguay": "URU",
    "colombia": "COL",
    "ecuador": "ECU",
    "mexico": "MEX",
    "méxico": "MEX",
    "canada": "CAN",
    "japan": "JPN",
    "australia": "AUS",
    "socceroos": "AUS",
    "morocco": "MAR",
    "senegal": "SEN",
    "egypt": "EGY",
    "tunisia": "TUN",
    "algeria": "ALG",
    "nigeria": "NGA",
    "super eagles": "NGA",
    "italy": "ITA",
    "croatia": "CRO",
    "switzerland": "SUI",
    "schweiz": "SUI",
    "belgium": "BEL",
    "denmark": "DEN",
    "netherlands": "NED",
    "poland": "POL",
    "serbia": "SRB",
    "ukraine": "UKR",
    "sweden": "SWE",
    "norway": "NOR",
    "austria": "AUT",
    "hungary": "HUN",
    "turkey": "TUR",
    "türkiye": "TUR",
    "finland": "FIN",
    "iraq": "IRQ",
    "jordan": "JOR",
    "uzbekistan": "UZB",
    "panama": "PAN",
    "haiti": "HAI",
    "curacao": "CUW",
    "curaçao": "CUW",
    "wales (cymru)": "WAL",
}


# ── Lookup helpers ─────────────────────────────────────────────────────────────

def _build_lookup() -> dict[str, Nation]:
    """Build a case-insensitive lookup dict: lowercase name/alias/code → Nation."""
    lookup: dict[str, Nation] = {}
    for nation in NATIONS:
        lookup[nation.name.lower()] = nation
        lookup[nation.fifa_code.lower()] = nation
    for alias, code in ALIASES.items():
        if code in FIFA_CODE_MAP:
            lookup[alias.lower()] = FIFA_CODE_MAP[code]
    return lookup


_nation_lookup: dict[str, Nation] | None = None


def find_nation(name_or_code: str) -> Nation | None:
    """Look up a nation by display name, FIFA code, or alias (case-insensitive)."""
    global _nation_lookup
    if _nation_lookup is None:
        _nation_lookup = _build_lookup()
    return _nation_lookup.get(name_or_code.strip().lower())


def get_all_nations() -> list[Nation]:
    """Return all 48 nations (including eliminated)."""
    return list(NATIONS)


def get_active_nations() -> list[Nation]:
    """Return only nations that have not been eliminated."""
    return [n for n in NATIONS if not n.eliminated]


def get_group(letter: str) -> list[Nation]:
    """Return the 4 nations in a given group letter (A-L), ordered by slot."""
    return sorted([n for n in NATIONS if n.group == letter.upper()], key=lambda n: n.slot)
