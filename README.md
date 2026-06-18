# ⚽ Footy Predict

> Local-first football analysis engine & interactive parlay builder with a premium Apple-inspired browser UI.

Footy Predict is a **decision support system** — not an automated bettor. It performs rigorous, transparent analysis of football matches across 12 leagues and recommends picks for 9+ betting markets. You make the final decisions; the system provides the data, rationale, confidence levels, and interactive slip building tools.

---

## ✨ Features

- **Premium Web UI** — macOS-inspired dark/light theme, glassmorphism card grids, responsive layouts, and pure CSS visualization bars.
- **Interactive Parlay Builder** — dynamically computes combined decimal odds and win probabilities in real-time. Allows swapping individual picks from alternative match recommendations with amber warning badges (`↓ Replacing HIGH with MEDIUM`) for manual changes.
- **Parlay History & Outcome Settling** — saves slip drafts, locks active parlays, settles outcomes (recording correct pick counts), and measures simulated flat unit ROI.
- **Smart Fetch strategy** — spreads API-Football's daily request quotas (100 calls/day free tier) across a weekly cycle:
  - *Mon–Wed*: Syncs only leagues with midweek fixtures (next 3 days).
  - *Thu–Fri*: Full sync of all active leagues for weekend preparation.
  - *Sat–Sun*: Syncs active leagues with matches in the next 24 hours.
  - *Initial Bootstrap*: Automatically executes a full active sync if the database is empty of matches.
- **Server-Sent Events (SSE) log stream** — overlays a console box displaying synchronization logs streaming in real-time as background fetch tasks execute.
- **Results Auto-Recording** — automatically triggers outcomes evaluation immediately upon new fetch arrivals, updating statistics dashboards instantly.
- **Local-first SQLite settings** — stores credentials, keys, active leagues, and active markets in SQLite (`user_settings`, `user_leagues`, `user_markets`) with a step-by-step Onboarding Setup Wizard in the browser.
- **12 analysis factors** — recent form, home/away form, scoring patterns, defensive records, head-to-head, standings gap, goal timing, momentum, and more.
- **9+ betting markets** — 1X2, double chance, over/under (0.5–3.5), GG/NG, draw no bet, win to nil, win either half, handicaps, team O/U.
- **12 leagues** — Premier League, La Liga, Serie A, Bundesliga, Ligue 1, Liga Portugal, Eredivisie, Scottish Premiership, Süper Lig, Saudi Pro League, Eliteserien, Belgian Pro League.

---

## 🚀 Quick Start

### Prerequisites

- Python 3.11+
- Free API keys (no payment details required):
  - [API-Football](https://dashboard.api-football.com/register) — primary data source (100 requests/day free tier)
  - [Football-Data.org](https://www.football-data.org/client/register) — secondary data source (offloads fixtures/standings query quotas)

### Installation

```bash
# Clone the repository
git clone <repo-url>
cd footy-predict

# Create and activate a Python virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install the package and development dependencies
pip install -e ".[dev]"
```

### First Run & Onboarding

1. Start the local server:
   ```bash
   footy serve
   ```
2. Open your browser and navigate to `http://localhost:8000`.
3. Complete the **Onboarding Wizard** to configure your API keys, select active leagues, and toggle active betting markets.
4. (Optional) Bootstrap historical CSV statistics (downloads football-data.co.uk CSVs to local cache):
   ```bash
   footy load-history
   ```

---

## 📋 Usage

### CLI Commands

The CLI is a Click-based entry point providing administrative control and batch scripts:

```
footy
├── serve                            Launch local FastAPI + Uvicorn server on :8000
├── fetch [--league SLUG]            Refresh active data from APIs
├── load-history [--league SLUG]     Load historical CSV files
├── status                           Check database records size, cache, and API usage
├── analyze
│   ├── matchweek [--league SLUG]    Print CLI analysis panels for upcoming matchweek
│   └── match "Team A vs Team B"     Print deep-dive factor charts for specific fixture
├── picks [--min-confidence LVL]     Print high-confidence picks table
├── parlay --size N                  Suggest N-pick parlay via CLI
├── record                           Evaluate results for finished matches
├── accuracy [--market M] [--lg L]   Print accuracy analytics dashboard
└── calibrate [--reset]              Run weight calibration adjusting factor scores
```

---

## 🔬 Analysis Methodology

### Aggregates Calculation

Aggregated score biases are computed by scaling factor confidence levels dynamically (unreliable or missing dimensions are automatically downweighted):

```
overall_score = Σ(factor_score × weight × confidence) / Σ(weight × confidence)
```

For betting markets, actual probabilities blend team historical trends with model-derived contextual parameters:

```
probability = 0.6 × historical_rate + 0.4 × model_estimate
```

### 12-Factor Weights

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

---

## 🏗️ File Tree

```
footy-predict/
├── pyproject.toml          # Package configurations & dependencies
├── README.md               # Documentation
├── footy.db                # SQLite database (gitignored)
├── data/
│   ├── historical/         # Local CSV caches (gitignored)
│   └── weights.json        # Calibrated weights output
├── src/
│   ├── config.py           # Configuration specifications fallback
│   ├── database.py         # SQLite schema & database DAL
│   ├── main.py             # Server bootstrapper
│   ├── cli.py              # Click command-line interface commands
│   ├── fetchers/
│   │   ├── cache.py        # API responses caching wrapper
│   │   ├── api_football.py # AP-Football Client (caching + quotas)
│   │   ├── football_data_org.py # Football-Data.org Client
│   │   └── csv_loader.py   # Historical CSV loader parser
│   ├── analysis/
│   │   ├── factors.py      # 12 factor score calculations
│   │   ├── engine.py       # Orchestrator & parlay suggestor
│   │   └── markets.py      # 9 market recommendation models
│   ├── reporting/
│   │   └── formatter.py    # CLI rich text printing
│   ├── tracking/
│   │   ├── recorder.py     # Matches outcomes evaluation
│   │   ├── accuracy.py     # Accuracy logs & ROI calculator
│   │   └── calibrator.py   # Weight adjustments loop
│   ├── api/
│   │   ├── app.py          # FastAPI application server setup
│   │   ├── deps.py         # Dependencies injector
│   │   ├── schemas.py      # Pydantic validation schemas
│   │   └── routes/
│   │       ├── analysis.py # Matchweek/match detail JSON API
│   │       ├── picks.py    # Filtered recommendations JSON API
│   │       ├── fetch.py    # Sync scheduler and SSE streams API
│   │       ├── settings.py # SQLite settings controls JSON API
│   │       ├── parlay.py   # Parlay slips CRUD JSON API
│   │       └── views.py    # FastAPI template renderer HTML views
│   ├── static/
│   │   └── css/
│   │       └── style.css   # Main Apple-inspired styles rules
│   └── templates/
│       ├── base.html       # Sidebar menu, theme toggle, SSE progress modal
│       ├── onboarding.html # Onboarding wizard screens
│       ├── dashboard.html  # Main overview dashboard
│       ├── analysis.html   # Matches list cards
│       ├── match_detail.html # Deep-dive factor bars page
│       ├── picks.html      # Picks table list
│       ├── parlay.html     # Slips constructor
│       ├── parlay_history.html # Saved slips settled tracker
│       ├── settings.html   # Preferences config panels
│       └── statistics.html # Analytics dashboard charts
└── tests/                  # Pytest test suite (63 passing tests)
```

---

## 🧪 Development & Testing

Run all unit and integration tests:

```bash
# Run pytest
.venv/bin/pytest tests/ -v
```

---

## 📝 License

This project is for personal use. Not affiliated with SportyBet or any API provider.
