# ⚽ Footy Predict

> Local-first football analysis engine & decision-support system for building weekend parlays across 12 European leagues.

**Footy Predict** is a **decision-support system**, not an automated bettor. It performs rigorous, data-driven analysis of upcoming football fixtures and surfaces ranked picks across 11 betting markets with stated probabilities, implied odds, and plain-English rationale. You do your own research. You build your own parlay. The system removes cognitive complexity — it does not remove your judgement.

Runs entirely on your laptop with SQLite. No hosting required.

---

## ✨ What It Does

| Capability | Detail |
|---|---|
| **16-Factor Analysis Engine** | Form, xG quality, head-to-head, rest days, rivalry, venue bias, opponent strength, attacking matchup, scoring patterns, goal timing, and more |
| **15 Betting Market Families** | 1X2, Double Chance, Draw No Bet, Over/Under (0.5–3.5), GG/NG, Win to Nil, Win Either Half, Asian Handicap, Team O/U, **1X2-1UP/2UP**, **1st Half O/U** (0.5/1.5), **2nd Half O/U** (0.5/1.5), **Home Team 1st/2nd Half O/U**, **Away Team 1st/2nd Half O/U** — ~65 picks per fixture |
| **Value Zone Filtering** | `footy report` defaults to **MEDIUM** confidence + ≥1.32 model odds — the correct value zone for 64,000-odds accumulator construction |
| **Weekly Decision-Support Report** | `footy report` — per-fixture top pick, rationale, risk flags, and 4 alternatives. Designed to be read, not blindly followed |
| **Quota-Aware Fetching** | `footy fetch-upcoming` stays within the 100 req/day free API tier |
| **Accuracy Tracking** | `footy record` + `footy accuracy` + `footy calibrate` — a self-improving loop across the season |
| **194 Tests** | Pytest suite covering factors, markets, settlement logic, calibration |

---

## 📊 Current Accuracy (August 2026, 12,982 settled predictions)

| Tier | Hit Rate | Count | When to Use |
|---|---|---|---|
| **HIGH** (≥80% prob) | **80.3%** | 2,427 picks | Primary parlay picks |
| **MEDIUM** (60-80%) | **67.0%** | 2,284 picks | Secondary / fill legs |
| LOW (<60%) | 35.5% | 8,271 picks | Informational only |

**Brier score: 0.2037** (vs 0.25 for random — significantly better calibration)

> Accuracy measured via retroactive simulation on 200 finished matches (2025/26 season). Historical CSVs + API-Football data.
>
> **Read tiers, not per-market rates.** The engine stores *both* sides of every binary market with complementary probabilities, so per-market hit rates mechanically converge on 50% and "overall accuracy" (~49.4%) is meaningless. Only confidence tiers and Brier score measure quality.
>
> The `xg_quality` factor is at 0% coverage and **blocked, not merely pending** — historical rows sourced from football-data.org carry no `api_football_id`, which `footy fetch --stats` requires. See `cli_guide.md` §10.

---

## 🎯 Market Guide

### Understanding 1UP and 2UP

These are early-exit insurance picks — the most valuable markets in the system:

- **Home 1UP**: Bet wins the moment the home team goes ahead on the scoreline (1-0, 2-1, etc.), regardless of the final result. Even if they concede later and lose, the bet is already won.
- **Home 2UP**: Bet wins on outright home win, OR the moment the home team leads by 2 goals at any point. If they go 2-0 up and then the score ends 2-2, the bet still wins.
- **Away 1UP / Away 2UP**: Same logic for the away team.

These pay slightly lower odds than the outright result but provide significantly better insurance.

> [!NOTE]
> **Measured rates, and why they're a floor.** In the Aug 2026 simulation, 1UP hit **88.5%** at HIGH (n=26) and 53.4% at MEDIUM (n=204); 2UP hit 66.3% at MEDIUM (n=101) with only one HIGH pick. Settlement is a *conservative approximation* — without minute-level goal events, a lead is only detected via an outright win or a half-time lead. A team that led 1-0 in the 60th minute and drew 1-1 is scored as a miss. True rates are therefore higher than these figures, but by an unmeasured margin until `/fixtures/events` data lands (Phase E).

### Understanding the New Half-Time Markets

Added July 2026 — these derive from the `goal_timing` factor's per-team H1/H2 goal percentages applied to the Poisson lambda. All use historical HTHG/HTAG data already in the database.

| Market | Description | Typical Model Odds |
|---|---|---|
| **1st Half Over 0.5** | At least 1 goal before half-time | 1.25–1.60 |
| **1st Half Under 0.5** | No goals in first 45 mins | 1.60–2.50 |
| **1st Half Over 1.5** | 2+ goals before half-time | 1.70–3.00 |
| **2nd Half Over 0.5** | At least 1 goal after half-time | 1.20–1.45 |
| **2nd Half Over 1.5** | 2+ goals in second half | 1.60–2.50 |
| **Home Team 1st Half Over 0.5** | Home team scores in H1 | 1.45–2.00 |
| **Away Team 1st Half Over 0.5** | Away team scores in H1 | 1.60–2.40 |
| **Home Team 2nd Half Over 0.5** | Home team scores in H2 | 1.35–1.80 |
| **Away Team 2nd Half Over 0.5** | Away team scores in H2 | 1.40–1.90 |

> **Settlement note:** These all void if HTHG/HTAG data is unavailable for a match. The CSV data from football-data.co.uk stores half-time scores for all leagues going back to 2018, so in practice virtually all historical results will settle correctly.

### Markets to Use at HIGH Confidence

| Market | HIGH Hit Rate | Model Odds Range | Notes |
|---|---|---|---|
| `1x2` — picks `…1X2 - 1UP` | 88.5% (n=26) | 1.17–1.30 | Best risk/reward balance. Rate is a floor — see settlement note above |
| `double-chance` | 80.0% | 1.13–1.30 | Short odds — borderline value zone |
| `over-under-1.5` | 83.2% | 1.14–1.40 | Reliable at HIGH |
| `away-under-2.5` | 85.9% | 1.08–1.25 | Very reliable |

### Markets to Be Cautious With at HIGH Confidence

| Market | HIGH Hit Rate | Issue |
|---|---|---|
| `win-either-half` | ~65% | Model overstates. Use at MEDIUM only. |
| `team-over-under-1.5` | ~68% | Poisson λ still slightly inflated |
| `over-under-2.5` | ~62% | Near-threshold uncertainty |
| `win-to-nil Yes` | 78.4% | Compound probability, +16pp gap |

---

## 🚀 Quick Start

### Prerequisites

- Python 3.11+
- Free API keys (no card, no trial period):
  - **[API-Football](https://dashboard.api-football.com/register)** — 100 requests/day free
  - **[Football-Data.org](https://www.football-data.org/client/register)** — optional, 7 leagues

### Install

```bash
git clone https://github.com/islajr/footy-predict.git
cd footy-predict
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

### Configure

```bash
cp .env.example .env
# Edit .env — add API_FOOTBALL_KEY and optionally FOOTBALL_DATA_ORG_KEY
```

### First-Time Bootstrap

```bash
footy load-history              # bulk-load historical CSVs (no API quota used)
footy fetch --league premier-league   # fetch current standings/stats (~10 API calls)
footy status                    # verify data loaded
```

---

## 📅 Weekly Workflow

| Day | Command | Time |
|---|---|---|
| **Thursday evening** | `footy fetch-upcoming` then `footy report --output weekend.txt` | 5 min |
| **Friday** | `footy analyze match "Team A vs Team B"` for uncertain fixtures | 10 min |
| **Saturday morning** | `footy parlay --size 50 -c HIGH --min-odds 1.32` | 5 min |
| **Monday morning** | `footy record` then `footy accuracy` | 5 min |
| **Monthly** | `footy calibrate` | 2 min |

---

## 🛠️ All CLI Commands

```
footy status                                # API quota, DB stats, cache info
footy fetch [--league SLUG] [--stats]       # fetch fixtures, standings, stats
footy fetch-upcoming [--days N]             # quota-aware weekend fetch
footy load-history [--league SLUG] [--seasons N]   # bulk-load historical CSVs
footy analyze matchweek [--league SLUG]    # full analysis, all upcoming fixtures
footy analyze match "Team A vs Team B"     # single-match deep dive
footy picks [-c HIGH] [-m MARKET] [-o 1.32]        # all picks, filterable
footy report [-o 1.32] [-c MEDIUM] [--days N] [--output FILE]  # weekly report ⭐
footy parlay --size 50 [-c HIGH] [-o 1.32]         # parlay builder
footy record [--league SLUG]               # record finished match results
footy accuracy [--market M] [--league L]   # accuracy dashboard
footy calibrate                            # update factor weights
footy bot start                            # start Telegram bot
```

**See the full CLI Guide for detailed options, examples, and scenario-based recipes.**

---

## 📋 Supported Leagues

Premier League · La Liga · Bundesliga · Serie A · Ligue 1 · Eredivisie · Liga Portugal · Championship · Belgian Pro League · Turkish Süper Lig · Scottish Premiership · Brasileirão

---

## 🗺️ Roadmap

### Active (Season 2026/27)
- ✅ 16-factor analysis engine
- ✅ 15 market families including 1UP/2UP and 6 new half-time markets (~65 picks/fixture)
- ✅ Probability calibration (shrinkage, Dixon-Coles, DNB fix)
- ✅ `footy report` defaults to MEDIUM confidence + 1.32 odds — correct value zone
- ✅ Accuracy tracking and calibration loop
- ✅ Data integrity: team canonicalisation (325 clubs), 11,609 duplicate rows removed, fixture-identity unique index
- ✅ 196 tests passing
- ⚠️ xG quality factor blocked at 0% — FDO-sourced rows lack `api_football_id`; resolves as 2026/27 fixtures arrive

### Phase E (After Season Start)
- 📋 Bookmaker odds integration (true edge detection)
- 📋 Injury reports (`/injuries` endpoint)
- 📋 Confirmed lineups (60min pre-KO)
- 📋 Manager change detection + new manager bounce
- 📋 Referee bias modelling
- 📋 Platt scaling (global probability calibration)

---

## 🧪 Development

```bash
# Run tests
pytest

# Run tests with coverage
pytest --cov=src

# Run simulation (200 historical matches)
# Note: simulate_historical.py lives outside the repo, in the agent workspace:
#   ~/.gemini/antigravity-cli/brain/<session-uuid>/scratch/simulate_historical.py
python /path/to/simulate_historical.py
```

---

## 📄 Documentation

- **[CLI Guide](cli_guide.md)** — comprehensive workflow guide: all commands, use scenarios, data-integrity notes, troubleshooting, parlay construction
- **[Architecture](architecture.md)** — system design and module layout
- **[Codebase Map](CODEBASE.md)** — file-by-file reference
- **[Simulation Report](simulation_report_phase_a.md)** — accuracy measurements, calibration history, factor coverage

---

*Local-first · Decision-support · No hosting required · Python 3.11+ · 15 markets · 196 tests*
