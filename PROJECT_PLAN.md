# World Cup Survivor Tool — Project Plan

> Sibling of `../march_madness_kalshi`. Reuses the same static-site + GitHub
> Pages + Kalshi-driven architecture, retargeted at the **2026 FIFA World Cup**
> (USA / Canada / Mexico, **June 11 – July 19, 2026**).

## Overview

A static site published via **GitHub Pages** that shows 2026 World Cup odds
from Kalshi prediction markets in a table optimized for survivor / pick'em
contest strategy. A Python script pulls odds from the Kalshi API, generates a
self-contained `docs/index.html`, and pushes it. No backend server.

**Target ship date:** **Tue/Wed June 9-10, 2026** — before the opener on
**Thursday June 11, 2026** (Mexico vs. opener at Estadio Azteca). Tighter
window than March Madness was.

---

## What's Different vs. March Madness

| Concern              | March Madness                       | World Cup                                                       |
|----------------------|-------------------------------------|-----------------------------------------------------------------|
| **Team count**       | 68 (incl. First Four)               | **48** (new expanded format)                                    |
| **Grouping**         | 4 regions of ~17                    | **12 groups of 4** (A–L)                                        |
| **Format**           | Single-elim only                    | **Group stage + knockout**                                      |
| **Game outcomes**    | Binary (win/loss)                   | **Ternary in group: W / D / L** — draws break the math          |
| **"Rounds"**         | R64, R32, S16, E8, F4, Champ        | MD1, MD2, MD3, R32, R16, QF, SF, Final, Champ                   |
| **Survivor pick**    | One team per round                  | One team per **matchday** (3 MDs in group stage) + per KO round |
| **Team naming**      | Custom abbr (DUKE, CONN…)           | **FIFA 3-letter codes** (USA, MEX, BRA, FRA…)                   |
| **Calendar**         | 3 weeks                             | ~5.5 weeks (Jun 11 – Jul 19)                                    |
| **Eliminations**     | Lose once = out                     | Group: 3 games before elim; KO: lose once = out                 |

The two biggest mechanical changes are **draws** (a team that draws is not
eliminated and the binary "win prob" no longer matches survivor semantics
1:1) and the **group-stage matchday picks** (the survivor problem in group
stage is "which of my team's 3 group games is the softest pick" — a new
"Best Matchday" column).

---

## What It Does

```
Team   | Grp | Pot | MD1 W% | MD2 W% | MD3 W% | →R32 | →R16 | →QF | →SF | →Final | Champ | Best Pick
-------|-----|-----|--------|--------|--------|------|------|-----|-----|--------|-------|------------
USA    | D   |  1  |  72%   |  58%   |  45%   |  82% |  44% | 22% | 11% |   5%   |  2%   | MD1 vs ??? (72%)
MEX    | A   |  1  |  68%   |  52%   |  40%   |  78% |  40% | 18% |  9% |   4%   |  2%   | MD1 vs ??? (68%)
BRA    | C   |  1  |  78%   |  62%   |  55%   |  90% |  62% | 38% | 22% |  12%   |  6%   | MD1 vs ??? (78%)
…
```

- Python script pulls **per-game** + **per-round-advancement** markets from Kalshi
- Maps each market → `(team, matchday|round)` → implied probability
- Generates **static HTML** with sortable, color-coded, filterable table
- Published to GitHub Pages — pick partners just open a URL
- Auto-refreshes daily at **12:05 AM CT** via GitHub Actions
- Historical snapshots saved as JSON in `data/snapshots/`

---

## Architecture

```
world_cup_survivor_kalshi/        # GitHub repo: MichaelWhalonCS/world-cup-survivor-kalshi
├── .github/workflows/refresh.yml # Daily 12:05 AM CT → run script → commit → deploy
├── .env.example
├── .gitignore
├── pyproject.toml
├── requirements.txt
├── README.md
├── PROJECT_PLAN.md               # this file
│
├── docs/index.html               # GitHub Pages source — THE deliverable
│
├── data/
│   ├── snapshots/                # JSON odds snapshots, committed
│   └── fixtures.json             # Group-stage schedule (team A vs team B on date X)
│
├── scripts/
│   ├── discover_tickers.py       # One-off: explore Kalshi for World Cup tickers
│   └── refresh.py                # Main entry: pull odds → render HTML → snapshot
│
├── src/
│   ├── __init__.py
│   ├── config.py                 # Pydantic settings (Kalshi creds, paths)  ← copy from MM
│   ├── kalshi_client.py          # Kalshi singleton                          ← copy from MM
│   ├── teams.py                  # 48 nations, group, pot, FIFA code, schedule refs
│   ├── fixtures.py               # Group-stage match calendar + KO bracket positions
│   ├── odds.py                   # Fetch + parse markets → TeamOdds
│   ├── survivor.py               # Best-matchday / best-round logic (new vs MM)
│   └── html_gen.py               # Jinja2 render → docs/index.html
│
├── templates/
│   └── table.html                # Jinja2 template (inline CSS+JS, mobile-friendly)
│
└── tests/
    ├── test_odds.py
    ├── test_survivor.py
    └── test_html_gen.py
```

**Reuse from `../march_madness_kalshi/`** (mostly straight copies + minor edits):
- `src/config.py`, `src/kalshi_client.py` — identical pattern, just rename settings prefix.
- `templates/table.html` skeleton — same sortable, color-coded, mobile CSS+JS pattern.
- `scripts/refresh.py` shape — load config → fetch → render → snapshot.
- `.github/workflows/refresh.yml` — same cron + secrets pattern.

**New from scratch:**
- `src/teams.py` — 48 nations × group × pot × FIFA code.
- `src/fixtures.py` — group-stage match schedule (driven from a static JSON).
- `src/odds.py` — handles **draws** (Yes/No on each side of a soccer market;
  draw = `1 − P(homeWin) − P(awayWin)`) and the different Kalshi series ticker.
- `src/survivor.py` — group-stage "best matchday" picker (new concept).

---

## Implementation Plan (Ordered Steps)

### Phase 0 — Pre-Work (Day 0, today: Jun 2)

| # | Task | Details |
|---|------|---------|
| 0a | **Confirm Kalshi has WC markets live** | Open kalshi.com, search "World Cup" / "FIFA". Confirm per-game and futures markets exist. If only futures exist as of Jun 2, plan a Phase 5 to add per-game once those list. |
| 0b | **Snapshot the official bracket / groups** | FIFA draw was Dec 5, 2025. Pull 12 groups × 4 teams + pots into `data/groups.json` (manual one-time). |
| 0c | **Snapshot the fixtures list** | 104 total matches (72 group, 32 KO). Pull into `data/fixtures.json`. FIFA.com or Wikipedia. |

### Phase 1 — Scaffold & Kalshi Client (Day 1, Jun 3)

| # | Task | Details |
|---|------|---------|
| 1 | **Create GitHub repo** | `world-cup-survivor-kalshi` under **MichaelWhalonCS**. Enable Pages from `docs/` on `main`. |
| 2 | **Project config** | `pyproject.toml`, `.gitignore`, `.env.example`, `requirements.txt` — copy from MM repo, change package name. |
| 3 | **Config + Kalshi client** | Direct copy of `src/config.py` + `src/kalshi_client.py` from MM repo. Only string change: `KALSHI_*` env vars stay the same so the same key works. |
| 4 | **Discover tickers** | `scripts/discover_tickers.py` — search Kalshi for series tickers containing "WORLDCUP" / "FIFA" / "WC26". Print all matching events and market titles. Hardcode the relevant series tickers after inspection. |

### Phase 2 — Teams, Groups, Fixtures (Day 1-2)

| # | Task | Details |
|---|------|---------|
| 5 | **48 nations** | `src/teams.py` — `Nation` dataclass: `name`, `fifa_code` (3-letter ISO), `group` (A-L), `pot` (1-4), `confederation` (UEFA/CONMEBOL/CONCACAF/AFC/CAF/OFC), `eliminated: bool = False`. Load from `data/groups.json`. |
| 6 | **Fixture schedule** | `src/fixtures.py` — load `data/fixtures.json` into `Match` records (`match_id`, `date`, `kickoff_local`, `host_city`, `group`, `team_a`, `team_b`, `round` ∈ {MD1, MD2, MD3, R32, R16, QF, SF, Final}). |
| 7 | **Name normalization** | Fuzzy + alias map for Kalshi titles → FIFA code. e.g. "United States" → USA, "South Korea" → KOR. Reuse the MM `KALSHI_ABBR_MAP` pattern. |

### Phase 3 — Odds Logic (Day 2-3)

| # | Task | Details |
|---|------|---------|
| 8 | **Per-game markets** | `src/odds.py` — fetch the WC per-game series (whatever the discovered ticker is, e.g. `KXFIFAWC26GAME`). For each game, soccer is **3-way (W/D/L)**: parse the team-win market for each side; draw probability is implicit. Implied prob = midpoint of yes_bid/yes_ask. |
| 9 | **Per-round advancement futures** | Fetch tournament futures: "Will USA make Round of 16?", "Will Brazil reach QF?", etc. Map Kalshi event → our `round_probs` key: `→R32`, `→R16`, `→QF`, `→SF`, `→Final`, `Champion`. |
| 10 | **Group-stage matchday probs** | For each (team, matchday), pull the team-win price from the per-game market on that date. Store `md1_win`, `md2_win`, `md3_win` per team. |
| 11 | **Conditional advancement (fallback)** | If a futures market is missing, derive from per-game markets. (Hard for group stage — leave gaps blank rather than guess.) |

### Phase 4 — Survivor Logic & HTML (Day 3)

| # | Task | Details |
|---|------|---------|
| 12 | **Best-pick selector** | `src/survivor.py` — `best_pick(team) → (label, prob)`. Group stage: pick the matchday with the highest single-game win prob. KO: pick the earliest round where prob ≥ threshold. Surface as a "Best Pick" column. |
| 13 | **Jinja2 template** | `templates/table.html` — copy MM template; rename columns; add **Group** + **Pot** filters; keep sortable headers, green→red probability gradient, mobile-friendly layout, "Last updated" timestamp. |
| 14 | **HTML generator** | `src/html_gen.py` — render template with the list of `TeamOdds`, write `docs/index.html`. Self-contained (inline CSS+JS). |
| 15 | **Snapshot saving** | `scripts/refresh.py` writes `data/snapshots/{ISO-timestamp}.json` per run, same as MM. |

### Phase 5 — Automation & Polish (Day 4)

| # | Task | Details |
|---|------|---------|
| 16 | **`scripts/refresh.py`** | Main entry. Load config → fetch odds → run survivor logic → render HTML → snapshot. |
| 17 | **GitHub Actions** | `.github/workflows/refresh.yml` — daily at **12:05 AM CT** (`5 6 * * *`). Reuse MM workflow verbatim, swap repo name. Manual `workflow_dispatch` trigger. |
| 18 | **Eliminated teams** | After group stage, mark teams that don't advance as `eliminated=True`. Auto-detect from settled futures (`→R32 = 0%`) or settled per-game markets. Grey out in table. |
| 19 | **README** | Setup, GH Pages URL, how Kalshi creds are reused from MM `.env`. |

### Phase 6 — Tournament-Time Maintenance (Jun 11 – Jul 19)

| # | Task | Details |
|---|------|---------|
| 20 | **Watch for renamed/relisted markets** | Kalshi sometimes relists markets at round boundaries. Check daily that ticker assumptions still hold. |
| 21 | **Patch fixtures.json if KO bracket fills in** | Group winners/runners-up populate the R32 slots dynamically. Once known, update fixtures.json so the KO matchday columns show actual opponents. |

---

## Key Design Decisions

1. **Static GitHub Pages, like MM.** No server.
2. **No database.** JSON snapshots.
3. **Kalshi only.** Prices = implied probabilities. No scraping FiveThirtyEight / Opta.
4. **Midpoint pricing**, fallback to last_price. Same as MM.
5. **Self-contained HTML.** Inline CSS+JS+data.
6. **Daily 12:05 AM CT refresh.** Markets are stale during matches anyway.
7. **Reuse Kalshi auth from MM project** — same key ID + private key. Just point the new repo's GH Actions secrets at the same values.
8. **Draws are not modeled as a separate column.** They show up implicitly via lower team-win probs. The user picks survivors based on win prob; draws are bad for survivor either way, so no extra UI cost.
9. **Group-stage best-pick is per-matchday, not per-round.** This is the main UX divergence from MM.
10. **FIFA 3-letter codes** as the canonical identifier (USA, MEX, BRA, ENG…). Display name in the table, code internally and as Kalshi-mapping key.

---

## Environment Variables

Identical to MM — reuse the same `.env`:

```env
KALSHI_API_KEY_ID=your-api-key-id
KALSHI_PRIVATE_KEY_PATH=/path/to/your/kalshi_private_key.pem
KALSHI_BASE_URL=https://api.kalshi.co
```

**GitHub Actions secrets** in the new repo:
- `KALSHI_API_KEY_ID`
- `KALSHI_PRIVATE_KEY` (full PEM content)

---

## Dependencies

Same set as MM:

```
pykalshi[dataframe]>=0.1.0
pydantic>=2.6.0
pydantic-settings>=2.1.0
jinja2>=3.1.0
structlog>=24.1.0
```

Dev:
```
pytest>=8.0.0
ruff>=0.2.0
```

---

## GitHub Pages Setup

1. Repo **Settings → Pages**
2. Source: **Deploy from a branch**
3. Branch: `main`, folder: `/docs`
4. URL: `https://michaelwhaloncs.github.io/world-cup-survivor-kalshi/`

---

## GitHub Actions Cron

```yaml
on:
  schedule:
    - cron: '5 6 * * *'   # 6:05 AM UTC = 12:05 AM CT (UTC-6)
  workflow_dispatch:
```

---

## Milestone Checklist

- [ ] Phase 0: Groups + fixtures snapshotted to `data/`
- [ ] Repo created on MichaelWhalonCS, Pages enabled, secrets set
- [ ] `discover_tickers.py` confirms WC market tickers
- [ ] `src/teams.py` + `src/fixtures.py` loaded from JSON
- [ ] Odds parsing maps markets to (team, matchday|round)
- [ ] `refresh.py` generates `docs/index.html` with full table
- [ ] Group/pot filters, sortable columns, color gradient working
- [ ] Best Pick column populated (matchday-aware for group stage)
- [ ] GH Pages live at the canonical URL
- [ ] GH Actions daily refresh at 12:05 AM CT verified
- [ ] README complete
- [ ] **Jun 11 — kickoff, table is live**

---

## Open Questions (Resolve in Phase 0-1)

1. **What are Kalshi's exact World Cup ticker patterns?** Likely something like `KXFIFAWC26GAME-…` for games, `KXFIFAWC26ROUND-…` for advancement, `KXFIFAWC26-26` for champion. Confirm with `discover_tickers.py`.
2. **Does Kalshi have per-round advancement futures for every team?** MM had them for nearly every team; WC probably has them for top ~24 nations only. If missing for a team, leave the column blank rather than synthesize.
3. **Does Kalshi list "Group X Winner" / "Group X Runner-Up" markets?** If yes, surface those directly as additional columns. If no, derive from per-game + advancement futures.
4. **Are there draw-explicit markets?** If yes, we get cleaner numbers. If no, derive: `P(draw) = 1 − P(team_A win) − P(team_B win)`.
5. **Three host countries / 16 venues — timezones span UTC-7 to UTC-4.** Confirm 12:05 AM CT refresh catches all settled markets for the prior day.

---

## Timeline

| Day            | Date          | Target                                                |
|----------------|---------------|-------------------------------------------------------|
| Day 0          | Tue Jun 2     | Phase 0 — groups + fixtures snapshot, confirm Kalshi  |
| Day 1          | Wed Jun 3     | Phase 1 — scaffold, client, discover tickers          |
| Day 2-3        | Thu-Fri Jun 4-5 | Phase 2-3 — teams, fixtures, odds parsing           |
| Day 4-5        | Sat-Sun Jun 6-7 | Phase 4 — survivor logic, HTML, template            |
| Day 6-7        | Mon-Tue Jun 8-9 | Phase 5 — Actions, README, polish                   |
| Buffer         | Wed Jun 10    | Final QA, fix anything                                |
| **Kickoff**    | **Thu Jun 11**| **Site is live before the opener**                    |
| Group stage    | Jun 11-27     | Daily auto-refresh; manual tuning on issues           |
| Knockout       | Jun 29-Jul 18 | Mark eliminations; fill KO bracket from results       |
| Final          | Sun Jul 19    | Wrap up; archive final snapshot                       |

---

## Cross-References

- Reference implementation: `../march_madness_kalshi/` (same architecture, simpler bracket).
- Key files to copy/adapt:
  - `../march_madness_kalshi/src/config.py`
  - `../march_madness_kalshi/src/kalshi_client.py`
  - `../march_madness_kalshi/templates/table.html`
  - `../march_madness_kalshi/scripts/refresh.py` (entry-point shape)
  - `../march_madness_kalshi/.github/workflows/refresh.yml`
