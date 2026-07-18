# ⚽ Footy Predict

> Local-first football analysis engine & interactive parlay builder — built for 50-game SportyBet weekend parlays across 12 European leagues.

**Footy Predict** is a **decision support system**, not an automated bettor. It performs rigorous, transparent analysis of upcoming football fixtures and recommends picks across 11 betting markets. You make the final call; the system gives you the data, rationale, confidence scores, and an interactive parlay builder to construct your slip.

Runs entirely on your laptop with SQLite. Cloud-deployable to Railway with a single Volume mount.

---

## ✨ Feature Highlights

| Area | Detail |
|------|--------|
| **Analysis Engine** | 16-factor weighted model: form, xG quality, head-to-head, rest days, rivalry, venue bias, opponent strength, and more |
| **11 Betting Markets** | 1X2, Double Chance, DNB, Over/Under (0.5–3.5), GG/NG, Win to Nil, Win Either Half, Asian Handicap (half-line + whole-line void model), 1X2-1UP (wins when team goes 1 ahead at any point), 1X2-2UP (wins on outright win or going 2 ahead), Team O/U (0.5/1.5/2.5) |
| **Parlay Builder** | Real-time combined odds & win probability, pick-swap with amber downgrade badges, history, outcome settling, simulated ROI |
| **Quota-Aware Fetching** | `fetch-upcoming` pre-computes request budget and fetches only fixtures relevant to the coming weekend — perfect for 100 req/day free tier |
| **Mobile-First UI** | Bottom nav, card picks, parlay bottom sheet, fixture cards — fully responsive at ≤ 768 px |
| **SSE Log Stream** | Real-time console overlay during background fetch tasks |
| **Cloud Ready** | `DATABASE_URL` env var, `railway.toml`, scheduled crons — deploy to Railway in minutes |
| **115 Tests** | Pytest suite covering factors, markets, handicap void model, WEH blend, momentum, calibration |

---

## 🚀 Quick Start

### Prerequisites

- Python 3.11+
- Free API keys (no card required):
  - **[API-Football](https://dashboard.api-football.com/register)** — primary source, 100 req/day free tier
  - **[Football-Data.org](https://www.football-data.org/client/register)** — secondary, 7 leagues, offloads fixture/standings quota

### Installation

```bash
# 1. Clone
git clone <repo-url>
cd footy-predict

# 2. Virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 3. Install (editable, with dev extras)
pip install -e ".[dev]"
```

### First-Run Onboarding

```bash
# Start the web server
footy serve
# → http://localhost:8000
```

Open your browser and complete the **Onboarding Wizard** (one-time):

1. **API Keys** — paste your API-Football and Football-Data.org keys
2. **Leagues** — toggle the 12 leagues you want to track
3. **Markets** — enable/disable the betting markets you care about
4. **Bootstrap data** — the wizard triggers an initial sync automatically if the database is empty

To bulk-import historical CSV statistics (recommended before first analysis):

```bash
footy load-history --seasons 3   # last 3 completed seasons
```

---

## 📅 Weekly Workflow

Footy Predict is designed around a Thursday-to-Monday rhythm for weekend parlay construction and result recording.

### Thursday Evening — Build Your Slip

```bash
# Quota-aware weekend sync (fetches only upcoming fixtures, stays within budget)
footy fetch-upcoming

# Review high-confidence picks
footy picks

# Build a 50-game parlay (adjust --size as needed)
footy parlay --size 50
```

Then open `http://localhost:8000/parlay` to review, swap picks, and lock your slip.

### Monday Morning — Settle Results

```bash
# Full stats sync including per-match xG (consumes ~30 API calls)
footy fetch --stats --stats-limit 30

# Evaluate finished matches and update accuracy/ROI stats
footy record

# Review updated accuracy dashboard
footy accuracy
```

### Season Start (August 2026)

```bash
# One-time full historical sync (high quota usage — run once per season)
footy fetch --stats

# Bulk-import last 3 seasons of CSV data
footy load-history --seasons 3

# Recalibrate factor weights against new season data
footy calibrate
```

---

## 🖥️ Web UI

Start the server with `footy serve` and navigate to `http://localhost:8000`.

| Route | Description |
|-------|-------------|
| `/dashboard` | Overview: upcoming fixtures, recent picks summary, quota meter |
| `/picks` | Filterable picks table (confidence, market, league) |
| `/parlay` | Interactive parlay builder — real-time odds, pick-swap, lock & save |
| `/analysis` | Per-league fixture cards with factor bar charts |
| `/statistics` | Accuracy dashboard, ROI curve, per-market breakdown |
| `/onboarding` | API keys, league/market toggles, initial sync wizard |
| `/settings` | Edit preferences, manage active leagues/markets |

### Mobile-First Layout (≤ 768 px)

- **Bottom navigation bar** replaces the sidebar
- **Card-based picks** with swipeable confidence badges
- **Parlay bottom sheet** — slides up from the bottom for slip review
- **Fixture cards** — compact view with key stats inline

### Real-Time Features

- **SSE log stream** — a console overlay appears during `fetch` operations, streaming live progress
- **Results auto-recording** — accuracy dashboards refresh automatically after new results arrive

---

## 🔧 Full CLI Reference

```
footy serve
    Start the FastAPI + Uvicorn web server on http://localhost:8000

footy fetch [--league SLUG] [--stats] [--stats-limit N]
    Fetch fixtures, standings, and (optionally) per-match xG stats
    --league SLUG       Restrict to a single league (e.g. premier-league)
    --stats             Include per-match statistics (xG, shots, etc.)
    --stats-limit N     Max matches to fetch stats for (default: all)
    Example: footy fetch --stats --stats-limit 30

footy fetch-upcoming [--days N] [--budget N]
    Pre-weekend quota-aware sync — fetches only fixtures in the next N days,
    distributing API calls within the remaining daily budget.
    --days N            Lookahead window (default: 4)
    --budget N          Max API calls to spend (default: auto from quota)
    Example: footy fetch-upcoming --days 10 --budget 40

footy load-history [--league SLUG] [--seasons N]
    Bulk-import historical match CSVs from football-data.co.uk
    --league SLUG       Restrict import to one league
    --seasons N         Number of past seasons to import (default: 1)
    Example: footy load-history --seasons 3

footy analyze [--league SLUG]
    Run the analysis engine and print upcoming matchweek factor panels

footy picks [--confidence LEVEL] [--market MARKET] [--league SLUG]
    Print high-confidence picks table
    --confidence LEVEL  Filter by HIGH / MEDIUM / LOW (default: HIGH)
    --market MARKET     Filter by market name (e.g. 1x2, over_under_2.5)
    Examples:
      footy picks
      footy picks --confidence MEDIUM
      footy picks --market 1x2

footy parlay --size N [--confidence LEVEL]
    Suggest an N-pick parlay via CLI, optimised for combined probability
    --size N            Number of picks in the parlay
    --confidence LEVEL  Minimum confidence tier (default: HIGH)
    Example: footy parlay --size 10

footy record [--league SLUG] [--date YYYY-MM-DD]
    Settle predictions for finished matches and update accuracy records

footy accuracy [--market MARKET] [--league SLUG]
    Print accuracy analytics dashboard and simulated flat-unit ROI

footy status
    Show database record counts, API quota usage, and cache state

footy calibrate [--reset]
    Adjust factor weights based on historical prediction accuracy
    --reset             Restore default weights before calibrating
```

---

## 📊 Markets Reference

| Market | Key | Description |
|--------|-----|-------------|
| 1X2 | `1x2` | Home Win / Draw / Away Win |
| Double Chance | `double_chance` | 1X / X2 / 12 |
| Draw No Bet | `draw_no_bet` | Home or Away; void on draw |
| Over/Under 0.5 | `over_under_0.5` | Total goals threshold |
| Over/Under 1.5 | `over_under_1.5` | Total goals threshold |
| Over/Under 2.5 | `over_under_2.5` | Total goals threshold (most popular) |
| Over/Under 3.5 | `over_under_3.5` | Total goals threshold |
| GG / NG | `gg_ng` | Both Teams to Score / No Goal |
| Win to Nil | `win_to_nil` | Win without conceding |
| Win Either Half | `win_either_half` | Win at least one half (WEH blend model) |
| Asian Handicap | `asian_handicap` | Half-line & whole-line with void model |
| 1X2-1UP | `1x2_1up` | Wins if team goes ≥1 goal ahead at any point, regardless of final result |
| 1X2-2UP | `1x2_2up` | Wins if team wins outright **OR** goes ≥2 goals ahead at any point (early-exit safeguard) |
| Team O/U 0.5 | `team_ou_0.5` | Individual team goal threshold |
| Team O/U 1.5 | `team_ou_1.5` | Individual team goal threshold |
| Team O/U 2.5 | `team_ou_2.5` | Individual team goal threshold |

---

## 🔬 Analysis Factors

All 16 factors are active. Weights are calibrated and stored in `data/weights.json`.

| # | Factor | Default Weight | Measures |
|---|--------|---------------|----------|
| 1 | `recent_form` | 18% | Last 5–10 results, trend direction |
| 2 | `home_form` | 15% | Home team's home-ground record |
| 3 | `away_form` | 15% | Away team's away-ground record |
| 4 | `scoring_frequency` | 12% | Goals per game, scoring consistency |
| 5 | `defensive_record` | 12% | Goals conceded, clean sheet rate |
| 6 | `head_to_head` | 10% | Historical meeting outcomes |
| 7 | `league_position` | 8% | Table standing, points gap |
| 8 | `goal_timing` | 4% | First-half vs second-half scoring splits |
| 9 | `momentum` | 3% | Last 3 vs previous 3 results delta |
| 10 | `scoring_patterns` | 2% | BTTS rate, O/U rates, draw tendency |
| 11 | `rest_days` | 0.5% | Fixture congestion / fatigue proxy |
| 12 | `rivalry` | 0.5% | Derby detection, historical upset factor |
| 13 | `venue_advantage` | — | Venue-bias split (separate from home/away form) |
| 14 | `attacking_matchup` | — | Attack vs defence quality matchup |
| 15 | `xg_quality` | — | Expected goals quality differential |
| 16 | `opponent_strength` | — | Adjusted for quality of opposition faced |

**Score formula:**

```
overall_score = Σ(factor_score × weight × confidence) / Σ(weight × confidence)
```

Missing or low-reliability factor dimensions are automatically downweighted. Market probabilities blend historical rates with the model estimate:

```
probability = 0.6 × historical_rate + 0.4 × model_estimate
```

---

## 🏗️ Architecture

| Layer | Technology |
|-------|-----------|
| Web Framework | **FastAPI** + Uvicorn (ASGI) |
| Templates | **Jinja2** (server-side rendered HTML) |
| Database | **SQLite** via built-in `sqlite3` + raw SQL DAL |
| HTTP Client | **httpx** (async, with caching wrapper) |
| CLI | **Click** |
| Testing | **pytest** (115 tests) |
| Styling | Vanilla CSS — glassmorphism, CSS custom properties, mobile-first |

### Project Layout

```
footy-predict/
├── pyproject.toml              # Package config & dependencies
├── railway.toml                # Railway deployment config & crons
├── README.md
├── footy.db                    # SQLite database (gitignored)
├── data/
│   ├── historical/             # CSV caches (gitignored)
│   └── weights.json            # Calibrated factor weights
└── src/
    ├── config.py               # Configuration & defaults
    ├── database.py             # Schema & data access layer
    ├── main.py                 # Server entry point
    ├── cli.py                  # Click CLI commands
    ├── fetchers/
    │   ├── cache.py            # API response cache wrapper
    │   ├── api_football.py     # API-Football client (quota tracking)
    │   ├── football_data_org.py# Football-Data.org client
    │   └── csv_loader.py       # Historical CSV parser
    ├── analysis/
    │   ├── factors.py          # 16-factor score calculations
    │   ├── engine.py           # Orchestrator & parlay suggester
    │   └── markets.py          # 11-market recommendation models
    ├── reporting/
    │   └── formatter.py        # CLI rich-text output
    ├── tracking/
    │   ├── recorder.py         # Match outcome evaluation
    │   ├── accuracy.py         # Accuracy logs & ROI calculator
    │   └── calibrator.py       # Weight calibration loop
    ├── api/
    │   ├── app.py              # FastAPI app setup & lifespan
    │   ├── deps.py             # Dependency injection
    │   ├── schemas.py          # Pydantic request/response schemas
    │   └── routes/
    │       ├── analysis.py     # Matchweek / match detail API
    │       ├── picks.py        # Filtered recommendations API
    │       ├── fetch.py        # Sync scheduler & SSE stream
    │       ├── settings.py     # Settings CRUD API
    │       ├── parlay.py       # Parlay slip CRUD API
    │       └── views.py        # Jinja2 HTML view renderer
    ├── static/css/
    │   └── style.css           # Global styles (glassmorphism, tokens)
    └── templates/
        ├── base.html           # Sidebar/bottom-nav, SSE modal, theme toggle
        ├── onboarding.html     # Setup wizard
        ├── dashboard.html      # Overview dashboard
        ├── analysis.html       # Fixture card grid
        ├── match_detail.html   # Factor bars deep-dive
        ├── picks.html          # Picks table
        ├── parlay.html         # Parlay builder / bottom sheet
        ├── parlay_history.html # Saved slips & ROI tracker
        ├── settings.html       # Preferences panels
        └── statistics.html     # Accuracy & ROI charts
```

---

## 🌐 Data Sources

| Source | Role | Quota |
|--------|------|-------|
| [API-Football](https://www.api-football.com/) | Primary — fixtures, standings, match stats, xG | 100 req/day (free) |
| [Football-Data.org](https://www.football-data.org/) | Secondary — 7 leagues, fixtures & standings | 10 req/min (free) |
| [football-data.co.uk](https://www.football-data.co.uk/) | Bulk historical CSVs (no quota) | Unlimited |

Configure via environment variables or the Onboarding Wizard:

```bash
export API_FOOTBALL_KEY=your_key_here
export FOOTBALL_DATA_ORG_KEY=your_key_here
export DATABASE_URL=/data/footy.db      # optional; defaults to ./footy.db
```

---

## ☁️ Railway Deployment

### One-Time Setup

1. **Create a Volume** named `footy-db`, mount at `/data`
2. Set environment variables in the Railway dashboard:
   ```
   DATABASE_URL=/data/footy.db
   API_FOOTBALL_KEY=<your key>
   FOOTBALL_DATA_ORG_KEY=<your key>
   ```
3. Deploy — Railway will detect `railway.toml` automatically

### Scheduled Crons (configured in `railway.toml`)

| Schedule | Command | Purpose |
|----------|---------|---------|
| **Mon 06:00 UTC** | `footy fetch --stats --stats-limit 30` | Post-weekend stats sync & result recording |
| **Thu 17:00 UTC** | `footy fetch-upcoming` | Pre-weekend quota-aware fixture sync |

### Manual Operations (via Railway CLI or dashboard shell)

```bash
# Bootstrap history on first deploy
footy load-history --seasons 3

# Force a full sync if cron missed
footy fetch --stats
```

---

## 🏆 Supported Leagues

| League | Country | Primary Source |
|--------|---------|---------------|
| Premier League | England | API-Football + FDO |
| La Liga | Spain | API-Football + FDO |
| Serie A | Italy | API-Football + FDO |
| Bundesliga | Germany | API-Football + FDO |
| Ligue 1 | France | API-Football + FDO |
| Liga Portugal | Portugal | API-Football |
| Eredivisie | Netherlands | API-Football |
| Scottish Premiership | Scotland | API-Football |
| Süper Lig | Turkey | API-Football |
| Saudi Pro League | Saudi Arabia | API-Football |
| Eliteserien | Norway | API-Football |
| Belgian Pro League | Belgium | API-Football |

---

## 🗺️ Roadmap

### ✅ Phase A — Foundation (Complete)
- Bug fixes, venue home/away splits, xG integration
- `attacking_matchup` factor, market reform (DNB, WtN, WEH)
- Parlay builder with history & outcome settling

### ✅ Phase B — Depth (Complete)
- Standings splits (home/away table), per-match statistics ingestion
- `opponent_strength` factor (quality-adjusted records)
- 101 pytest tests

### ✅ Phase C — Refinement (Complete)
- Venue-bias fix (clean separation from form factors)
- WEH blend model (first half + second half probability weighting)
- Momentum weight tuning
- Asian Handicap: half-line & whole-line with void model
- 1X2-1UP / 1X2-2UP lead-trigger markets
- Team O/U 2.5 market
- 115 pytest tests

### ✅ Phase C.5 — Mobile-First UI Overhaul (Complete)
- Bottom navigation bar (≤ 768 px)
- Card-based pick grid with confidence badges
- Parlay bottom sheet (slide-up on mobile)
- Fixture cards with inline key stats

### ✅ Cloud Readiness (Complete)
- `DATABASE_URL` environment variable support
- `footy fetch-upcoming` with quota-aware budget logic
- `railway.toml` with service config and cron schedule

### 🔲 Phase D — Player Intelligence (Upcoming)
- **Player stats** — top scorers, assisters, key player influence
- **Injury & suspension tracking** — missing players factored into picks
- **Lineup integration** — confirmed starting XI weighting
- **Manager bounce** — new manager appointment detection
- **Squad depth** — rotation risk and bench quality scoring
- **Fatigue modelling** — cumulative minutes, fixture density
- **Referee bias** — card rates, penalty tendencies per official

---

## 🧪 Testing

```bash
# Run the full test suite
.venv/bin/pytest tests/ -v

# Run a specific module
.venv/bin/pytest tests/test_markets.py -v

# Run with coverage report
.venv/bin/pytest tests/ --cov=src --cov-report=term-missing
```

Current status: **115 tests passing** (factors, markets, handicap void model, WEH blend, momentum, calibration, API quota logic).

---

## 📝 Notes

- **Not affiliated** with SportyBet, API-Football, Football-Data.org, or any data provider.
- **Personal use only** — this is a decision support tool, not financial advice.
- API keys are stored in SQLite (`user_settings` table) and never committed to version control.
- The database file (`footy.db`) and CSV cache (`data/historical/`) are gitignored.
