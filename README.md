# 2026 World Cup Survivor Tool

A static site that shows 2026 FIFA World Cup odds from [Kalshi](https://kalshi.com) prediction markets in a table optimized for survivor / pick'em contest strategy.

**Sibling of [`../march_madness_kalshi`](../march_madness_kalshi)** — same architecture, retargeted at the 48-team / 12-group / group-stage-plus-knockout World Cup format.

## How It Works

1. A Python script pulls World Cup market odds from the Kalshi API
2. It generates a self-contained `docs/index.html` with a sortable, color-coded table
3. GitHub Pages serves it as a static site
4. GitHub Actions auto-refreshes hourly

No backend server needed.

## Table Features

- **Group-stage matchday columns** (MD1, MD2, MD3) — per-game win probability for each matchday, with opponent FIFA code shown below the percentage
- **Knockout cumulative columns** (Make R16, Make QF, Make SF, Make Final, Win Cup) — from Kalshi advancement futures
- **Best Pick column** — the most strategic round to spend each nation in your survivor pool
- **Sortable columns** — click any header to sort
- **Color-coded cells** — green (high probability) → red (low probability)
- **Group / Pot / Confederation filters** — narrow the view to e.g. "Pot 1 from CONMEBOL"
- **Mobile-friendly**

## What's Different vs. March Madness

| Concern              | March Madness                       | World Cup                                                       |
|----------------------|-------------------------------------|-----------------------------------------------------------------|
| Team count           | 68 (incl. First Four)               | 48 (expanded format)                                            |
| Grouping             | 4 regions of ~17                    | 12 groups of 4 (A–L)                                            |
| Format               | Single-elim                         | Group stage + knockout                                          |
| Game outcomes        | Binary (W/L)                        | Ternary in group (W/D/L) — draws break the math                 |
| Rounds               | R64…Champ                           | MD1, MD2, MD3, R32, R16, QF, SF, Final                          |
| Survivor pick        | One per round                       | One per matchday in group, then one per KO round                |
| Team naming          | Custom abbr                         | FIFA 3-letter codes                                             |

## Local Setup

```powershell
# From the project directory
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Add your Kalshi credentials
Copy-Item .env.example .env
# Edit .env with your API key ID and private key path
# (You can re-use the same key as march_madness_kalshi)

# Run discovery once to find the real Kalshi WC ticker patterns,
# then update SERIES_TICKER / FUTURES_SERIES / CHAMP_EVENT in src/odds.py
python scripts/discover_tickers.py

# Run the refresh
python scripts/refresh.py

# Open the generated page
start docs/index.html
```

## ⚠ Before Going Live

This scaffold was built with **PLACEHOLDER values** that must be verified:

1. **`data/groups.json`** — the 48 teams and their group assignments are based on the 2025-12-05 draw but at the edge of the model's knowledge. **Verify every group against [fifa.com](https://www.fifa.com/en/tournaments/mens/worldcup/canadamexicousa2026) and set `_meta.verified` to `true`.**

2. **`data/matches.json`** — the group-stage matchday dates follow the FIFA-published schedule template but specific kickoff dates per group should be verified.

3. **Ticker constants in `src/odds.py`** — `SERIES_TICKER`, `FUTURES_SERIES`, `CHAMP_EVENT`, and `_DATE_TO_ROUND` are placeholders. Run `python scripts/discover_tickers.py` once Kalshi has WC markets listed and update to the real values.

## Discovering Kalshi Markets

```powershell
python scripts/discover_tickers.py
```

Then update `src/odds.py` with the relevant series tickers and event suffixes.

## Project Structure

```
├── .github/workflows/refresh.yml   # Hourly auto-refresh via GitHub Actions
├── docs/index.html                  # Generated static page (GitHub Pages source)
├── data/
│   ├── groups.json                  # 48 nations × 12 groups × pots (verify before go-live)
│   ├── matches.json                 # Group-stage schedule + KO date windows
│   └── snapshots/                   # Historical odds snapshots
├── scripts/
│   ├── refresh.py                   # Main entry: pull odds → generate HTML
│   └── discover_tickers.py          # One-off: explore Kalshi for WC tickers
├── src/
│   ├── config.py                    # Pydantic settings
│   ├── kalshi_client.py             # Kalshi singleton (shared shape with MM)
│   ├── teams.py                     # 48 nations, FIFA codes, alias map
│   ├── fixtures.py                  # Schedule loader, opponent resolver
│   ├── odds.py                      # Kalshi market fetch + parse → NationOdds
│   ├── survivor.py                  # Beam-search optimal pick series
│   └── html_gen.py                  # Self-contained HTML generator (Jinja2)
└── templates/table.html             # Jinja2 template (inline CSS + JS)
```

## GitHub Actions Secrets

In the repo's Settings → Secrets → Actions:

- `KALSHI_API_KEY_ID` — Kalshi API key ID
- `KALSHI_PRIVATE_KEY` — full content of the private key PEM file

These are the same credentials as the March Madness project — point them at the same key pair.

## Timeline

- **Thu, June 11, 2026** — Tournament opener (Mexico, Estadio Azteca)
- **Jun 11 – Jun 27** — Group stage (3 matchdays per team)
- **Jun 29 – Jul 3** — Round of 32
- **Jul 4 – 7** — Round of 16
- **Jul 9 – 11** — Quarter-finals
- **Jul 14 – 15** — Semi-finals
- **Jul 18** — Third-place play-off
- **Sun, Jul 19, 2026** — Final at MetLife Stadium
