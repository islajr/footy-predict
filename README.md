# ⚽ Footy Predict

> Data-backed football analysis engine for building winning SportyBet parlays.

Footy Predict is a **decision support system** — not an automated bettor. It performs rigorous, transparent analysis of football matches across 12 leagues and recommends picks for 9+ betting markets. You make the final calls; the system provides the data, rationale, and confidence levels.

---

## ✨ Features

- **12 analysis factors** — recent form, home/away form, scoring patterns, defensive record, H2H, league position, goal timing, momentum, and more
- **9+ betting markets** — 1X2, double chance, over/under (0.5–3.5), GG/NG, draw no bet, win to nil, win either half, handicaps, team O/U
- **12 leagues** — PL, La Liga, Serie A, Bundesliga, Ligue 1, Liga Portugal, Eredivisie, Scottish Premiership, Süper Lig, Saudi Pro League, Eliteserien, Belgian Pro League
- **50-game parlay builder** — ranks picks by probability and diversifies across leagues
- **Self-improving** — tracks prediction accuracy and calibrates factor weights over time
- **Dual interface** — Rich CLI for desktop, Telegram bot for mobile
- **Free data sources** — API-Football (100/day), Football-Data.org (unlimited), football-data.co.uk CSVs

---

## 🚀 Quick Start

### Prerequisites

- Python 3.11+
- Free API keys (no payment required):
  - [API-Football](https://dashboard.api-football.com/register) — primary data source
  - [Football-Data.org](https://www.football-data.org/client/register) — secondary source
  - [Telegram Bot](https://t.me/BotFather) *(optional)* — for mobile alerts

### Installation

```bash
# Clone
git clone <repo-url>
cd footy-predict

# Virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install
pip install -e ".[dev]"

# Configure
cp .env.example .env
nano .env  # Add your API keys
```

### First Run

```bash
# Load historical data (no API calls — downloads CSVs)
footy load-history

# Fetch current season data
footy fetch

# Check everything is working
footy status
```

---

## 📋 Usage

### Weekly Parlay Workflow

```bash
# 1. Refresh data
footy fetch

# 2. Analyse the matchweek
footy analyze matchweek

# 3. See high-confidence picks
footy picks

# 4. Deep-dive on a specific match
footy analyze match "Arsenal vs Chelsea"

# 5. Get a parlay suggestion
footy parlay --size 50

# 6. Build your parlay on SportyBet

# 7. After matches finish, record results
footy record

# 8. Check accuracy
footy accuracy

# 9. After 100+ predictions, calibrate
footy calibrate
```

### CLI Commands

```
footy
├── fetch [--league SLUG]            Refresh data from APIs
├── load-history [--league SLUG]     Load historical CSVs
├── status                           API quota, cache, DB stats
├── analyze
│   ├── matchweek [--league SLUG]    Analyse upcoming matchweek
│   └── match "Team A vs Team B"     Deep-dive on a specific fixture
├── picks [--min-confidence LVL]     Filtered recommendations
├── parlay --size N                  Parlay suggestion
├── record                           Record results for finished matches
├── accuracy [--market M] [--lg L]   Accuracy dashboard
├── calibrate [--reset]              Run weight calibration
└── bot start                        Launch Telegram bot
```

### Telegram Bot Commands

```
/mw         — Analyse next matchweek
/league PL  — Analyse one league
/match Arsenal vs Chelsea  — Deep-dive
/picks      — High-confidence picks
/accuracy   — Prediction stats
/quota      — API usage
/fetch      — Refresh data
```

---

## 🏗️ Architecture

```
footy-predict/
├── src/
│   ├── config.py           Central configuration (leagues, markets, weights)
│   ├── database.py         SQLite schema + data access layer
│   ├── main.py             App bootstrap
│   ├── cli.py              Click CLI interface
│   ├── fetchers/
│   │   ├── cache.py        SQLite-backed response cache
│   │   ├── api_football.py API-Football async client
│   │   ├── football_data_org.py  FDO async client
│   │   └── csv_loader.py   Historical CSV loader
│   ├── analysis/
│   │   ├── factors.py      12 analysis factor functions
│   │   ├── engine.py       Orchestrator + aggregation
│   │   └── markets.py      9 market model functions
│   ├── reporting/
│   │   ├── formatter.py    Rich CLI + Telegram formatters
│   │   └── telegram_bot.py Bot implementation
│   └── tracking/
│       ├── recorder.py     Result recording + evaluation
│       ├── accuracy.py     Accuracy metrics + dashboard
│       └── calibrator.py   Weight calibration engine
├── data/
│   ├── historical/         Downloaded CSV files
│   └── weights.json        Calibrated factor weights
└── footy.db                SQLite database
```

### Data Flow

```
APIs / CSVs → Fetchers → Database → Analysis Engine → Market Models → Reports
                                                                         ↓
                                                              User picks on SportyBet
                                                                         ↓
                                                              Record results → Accuracy → Calibrator
                                                                                              ↓
                                                                                     Adjusted weights
```

---

## 🔬 Analysis Methodology

### Factor Weights

| Factor | Weight | Measures |
|--------|--------|----------|
| Recent Form | 18% | Last 5-10 match results, trend direction |
| Home Form | 15% | Home team's home record |
| Away Form | 15% | Away team's away record |
| Scoring Frequency | 12% | Goals per game, consistency |
| Defensive Record | 12% | Goals conceded, clean sheet rate |
| Head to Head | 10% | Historical meetings |
| League Position | 8% | Table standing, points gap |
| Goal Timing | 4% | First half vs second half scoring |
| Momentum | 3% | Last 3 vs previous 3 results |
| Scoring Patterns | 2% | BTTS rate, O/U rates |
| Rest Days | 0.5% | Fixture congestion |
| Rivalry | 0.5% | Derby detection, upset history |

### Confidence-Weighted Aggregation

```
overall_score = Σ(factor_score × weight × confidence) / Σ(weight × confidence)
```

Factors with low confidence (sparse data) are automatically down-weighted.

### Probability Blending

```
probability = 0.6 × historical_rate + 0.4 × model_estimate
```

Anchored to real data, adjusted for match-specific context.

---

## ⚙️ Data Sources

| Source | Role | Limits | Leagues |
|--------|------|--------|---------|
| API-Football | Primary (stats, H2H, predictions) | 100 req/day, 10/min | All 12 |
| Football-Data.org | Secondary (fixtures, standings) | 10 req/min, no daily cap | 7 of 12 |
| football-data.co.uk | Historical CSVs | Unlimited | 11 of 12 |

The system aggressively caches responses to minimise API usage:
- Standings: 24 hours
- Fixtures: 6 hours
- Team stats: 7 days
- H2H data: 30 days

---

## 📊 Supported Markets

| Market | SportyBet Name | How It Works |
|--------|---------------|--------------|
| 1X2 | 1X2 | Outright match result |
| Double Chance | DC | Two outcomes covered |
| Over/Under | O/U 0.5–3.5 | Total goals vs line |
| GG/NG | GG/NG | Both teams score? |
| Draw No Bet | DNB | Void on draw |
| Win to Nil | Win to Nil | Win + clean sheet |
| Win Either Half | Win Either Half | Win at least one half |
| Handicap | Handicap | Goal advantage applied |
| Team O/U | Home/Away O/U | Individual team goals |

---

## 🧪 Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
python -m pytest tests/ -v

# Run with coverage
python -m pytest tests/ --cov=src --cov-report=term-missing
```

---

## 📝 License

This project is for personal use. Not affiliated with SportyBet.
