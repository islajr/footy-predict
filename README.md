# ⚽ Footy Predict

> Local-first football analysis engine & decision-support system for building weekend parlays across 12 European leagues.

**Footy Predict** is a **decision-support system**, not an automated bettor. It performs rigorous, data-driven analysis of upcoming football fixtures and surfaces ranked picks across 11 betting markets with stated probabilities, implied odds, and plain-English rationale. You do your own research. You build your own parlay. The system removes cognitive complexity — it does not remove your judgement.

Runs entirely on your laptop with SQLite. No hosting required.

---

## ✨ What It Does

| Capability | Detail |
|---|---|
| **16-Factor Analysis Engine** | Form, xG quality, head-to-head, rest days, rivalry, venue bias, opponent strength, attacking matchup, scoring patterns, goal timing, and more |
| **11 Betting Markets** | 1X2, Double Chance, Draw No Bet, Over/Under (0.5–3.5 goals), GG/NG, Win to Nil, Win Either Half, Asian Handicap, Team O/U (0.5/1.5/2.5), **1X2-1UP** (bet wins when team ever leads by 1+), **1X2-2UP** (bet wins on outright win or going 2 ahead at any point) |
| **Value Zone Filtering** | `footy report --min-odds 1.32` — filters picks to HIGH confidence + ≥1.20 real odds (adjusted for bookmaker margin) |
| **Weekly Decision-Support Report** | `footy report` — per-fixture top pick, rationale, risk flags, and 4 alternatives. Designed to be read, not blindly followed |
| **Quota-Aware Fetching** | `footy fetch-upcoming` stays within the 100 req/day free API tier |
| **Accuracy Tracking** | `footy record` + `footy accuracy` + `footy calibrate` — a self-improving loop across the season |
| **134 Tests** | Pytest suite covering factors, markets, settlement logic, calibration |

---

## 📊 Current Accuracy (July 2026, 8,307 settled predictions)

| Tier | Hit Rate | Count | When to Use |
|---|---|---|---|
| **HIGH** (≥80% prob) | **79.9%** | 1,973 picks | Primary parlay picks |
| **MEDIUM** (60-80%) | **62.7%** | 1,466 picks | Secondary / fill legs |
| LOW (<60%) | 34.6% | 4,868 picks | Informational only |

**Brier score: 0.2064** (vs 0.25 for random — significantly better calibration)

> Accuracy measured via retroactive simulation on 200 finished matches (2025/26 season). Historical CSVs + API-Football data. xG factor not yet active (starts August 2026).

---

## 🎯 Market Guide

### Understanding 1UP and 2UP

These are early-exit insurance picks — the most valuable markets in the system:

- **Home 1UP**: Bet wins the moment the home team goes ahead on the scoreline (1-0, 2-1, etc.), regardless of the final result. Even if they concede later and lose, the bet is already won.
- **Home 2UP**: Bet wins on outright home win, OR the moment the home team leads by 2 goals at any point. If they go 2-0 up and then the score ends 2-2, the bet still wins.
- **Away 1UP / Away 2UP**: Same logic for the away team.

These pay slightly lower odds than the outright result but provide significantly better insurance. Historical hit rates: **1UP: ~85%, 2UP: ~87%**.

### Markets to Use at HIGH Confidence

| Market | HIGH Hit Rate | Model Odds Range | Notes |
|---|---|---|---|
| `1x2-1up` (1UP picks) | ~85% | 1.17–1.30 | Best risk/reward balance |
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
footy report [-o 1.32] [-c HIGH] [--days N] [--output FILE]   # weekly report ⭐
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
- ✅ 11 markets including 1UP/2UP
- ✅ Probability calibration (shrinkage, Dixon-Coles, DNB fix)
- ✅ `footy report` weekly decision-support command
- ✅ Accuracy tracking and calibration loop
- 🔄 xG quality factor activates from August 2026

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
python brain/scratch/simulate_historical.py
```

---

## 📄 Documentation

- **[CLI Guide](brain/cli_guide.md)** — comprehensive local workflow guide, all commands, scenarios, how to win consistently
- **[Walkthrough](brain/walkthrough.md)** — development history and what was built
- **[Analytics Roadmap](brain/analytics_roadmap.md)** — full capability roadmap with status
- **[Simulation Report](brain/simulation_report_phase_a.md)** — accuracy measurements and calibration history

---

*Local-first · Decision-support · No hosting required · Python 3.11+*
