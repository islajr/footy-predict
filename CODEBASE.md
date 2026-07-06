# Footy Predict — Codebase Specification Document

This document provides a detailed walkthrough of the Footy Predict codebase. It describes the file structure, database tables, modules, prediction models, and interface layouts.

---

## 1. Directory Structure

```
footy-predict/
├── pyproject.toml              # Project dependencies & build rules
├── README.md                   # Installation & getting started guide
├── architecture.md             # High-level architecture & pipeline designs
├── footy.db                    # SQLite database file (generated at runtime)
├── data/
│   ├── historical/             # Cached CSV files from football-data.co.uk
│   └── weights.json            # Calibrated factor weights
├── src/
│   ├── config.py               # Registry of leagues, markets, and default weights
│   ├── database.py             # Schema definitions and database operations (DAL)
│   ├── main.py                 # Core entry point (FastAPI server bootstrap)
│   ├── cli.py                  # Click command-line interface commands
│   ├── fetchers/
│   │   ├── cache.py            # Deterministic SHA-256 API caching
│   │   ├── api_football.py     # Client for API-Football (primary data source)
│   │   ├── football_data_org.py # Client for Football-Data.org (secondary source)
│   │   └── csv_loader.py       # Parser for football-data.co.uk CSV data
│   ├── analysis/
│   │   ├── factors.py          # Implementations for the 12 analysis factors
│   │   ├── engine.py           # AnalysisEngine orchestrator
│   │   └── markets.py          # Prediction models for the 9 betting markets
│   ├── tracking/
│   │   ├── recorder.py         # Prediction outcomes evaluator
│   │   ├── accuracy.py         # Dashboard metrics (Brier score & flat ROI)
│   │   └── calibrator.py       # Feedback loop for factor weight tuning
│   ├── reporting/
│   │   ├── formatter.py        # Terminal layout builder and Telegram message compiler
│   │   └── telegram_bot.py     # Telegram command handlers and event loop
│   ├── api/
│   │   ├── app.py              # FastAPI server setup and routes mount
│   │   ├── deps.py             # Database and engine dependency injections
│   │   ├── schemas.py          # Pydantic schema validators
│   │   └── routes/
│   │       ├── views.py        # HTML Jinja2 template view routes
│   │       ├── fetch.py        # SSE streams and data fetchers routing
│   │       ├── analysis.py     # JSON analysis feed endpoints
│   │       ├── picks.py        # High-confidence recommendations API
│   │       ├── settings.py     # App settings and API keys test routing
│   │       ├── parlay.py       # Parlay slips database CRUD API
│   │       └── accuracy.py     # Metrics endpoint
│   ├── templates/              # HTML templates for the browser interface
│   └── static/
│       └── css/
│           └── style.css       # Stylesheets for dark/light themes
└── tests/                      # Pytest unit and integration test suite
```

---

## 2. Configuration (`src/config.py`)

The configuration module serves as the static registry for the application. It maps leagues and markets, sets default weights, and manages path locations.

### Key Data Structures

*   `APIFootballConfig` / `FootballDataOrgConfig` / `TelegramConfig`: Dataclasses that load API keys, base URLs, rate limits, and chat IDs from environment variables or the SQLite database.
*   `CacheTTL`: Defines expiration times (in seconds) for cached API responses:
    *   `standings`: 24 hours (`86,400`s)
    *   `fixtures`: 6 hours (`21,600`s)
    *   `team_stats`: 7 days (`604,800`s)
    *   `h2h`: 30 days (`2,592,000`s)
    *   `match_stats`: 30 days (`2,592,000`s)
    *   `predictions`: 3 days (`259,200`s)
*   `LeagueConfig`: A configuration registry for the 12 supported leagues. It contains references for mapping teams and fixtures across different APIs:
    *   `slug`: The unique, URL-friendly ID.
    *   `api_football_id`: The ID used for API-Football requests.
    *   `football_data_org_code`: The league code for Football-Data.org (if supported).
    *   `csv_code`: The division code for historical CSV downloads.
    *   `season_format`: `"european"` (August to May) or `"calendar"` (January to December).
*   `MarketDef`: Defines the code, display name, SportyBet-equivalent label, and category ("result", "goals", "team_performance") for each betting market.
*   `FACTOR_WEIGHTS`: Initial baseline weights for the 12 factors (must sum to exactly 1.0).
*   `CONFIDENCE_THRESHOLDS`: Maps probability ranges to confidence labels (HIGH: $\ge 0.75$, MEDIUM: $\ge 0.60$, LOW: $< 0.60$).

---

## 3. Database Layer (`src/database.py`)

The database layer handles SQLite storage, schema validation, and SQL queries. It uses a context manager (`_connect()`) to ensure connections are properly closed and database transactions are processed safely.

### SQLite Schema Summary

```sql
-- Leagues metadata
CREATE TABLE leagues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    country TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    api_football_id INTEGER UNIQUE,
    fdo_code TEXT,
    csv_code TEXT,
    tier TEXT NOT NULL DEFAULT 'extended'
);

-- Teams registry with external source ID mapping
CREATE TABLE teams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    api_football_id INTEGER UNIQUE,
    fdo_id INTEGER,
    league_id INTEGER REFERENCES leagues(id),
    short_name TEXT,
    aliases TEXT DEFAULT '[]', -- JSON list of alternate names
    logo_url TEXT,
    UNIQUE(name, league_id)
);

-- Matches registry (fixtures and history)
CREATE TABLE matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    api_football_id INTEGER UNIQUE,
    league_id INTEGER NOT NULL REFERENCES leagues(id),
    season TEXT NOT NULL,
    matchday INTEGER,
    date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'SCHEDULED',
    home_team_id INTEGER NOT NULL REFERENCES teams(id),
    away_team_id INTEGER NOT NULL REFERENCES teams(id),
    home_score INTEGER,
    away_score INTEGER,
    ht_home_score INTEGER,
    ht_away_score INTEGER,
    referee TEXT,
    venue TEXT,
    source TEXT NOT NULL DEFAULT 'api_football',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

-- Match statistical records
CREATE TABLE match_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id INTEGER NOT NULL REFERENCES matches(id),
    team_id INTEGER NOT NULL REFERENCES teams(id),
    shots INTEGER,
    shots_on_target INTEGER,
    possession REAL,
    corners INTEGER,
    fouls INTEGER,
    yellow_cards INTEGER,
    red_cards INTEGER,
    offsides INTEGER,
    passes INTEGER,
    pass_accuracy REAL,
    expected_goals REAL,
    UNIQUE(match_id, team_id)
);

-- Predictions registry
CREATE TABLE predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id INTEGER NOT NULL REFERENCES matches(id),
    market TEXT NOT NULL,
    pick TEXT NOT NULL,
    probability REAL NOT NULL,
    confidence TEXT NOT NULL,
    implied_odds REAL,
    rationale TEXT NOT NULL,
    risk_factors TEXT DEFAULT '[]', -- JSON array
    supporting_stats TEXT DEFAULT '{}', -- JSON object
    factor_scores TEXT DEFAULT '{}', -- JSON object (factor_name -> score)
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(match_id, market)
);

-- Prediction Outcomes (Correct/Incorrect evaluation flags)
CREATE TABLE prediction_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prediction_id INTEGER NOT NULL UNIQUE REFERENCES predictions(id),
    actual_outcome TEXT NOT NULL,
    is_correct INTEGER NOT NULL,
    recorded_at TEXT DEFAULT (datetime('now'))
);
```

### Core Database Methods

#### `Database.upsert_league(self, name: str, country: str, slug: str, api_football_id: int | None = None, fdo_code: str | None = None, csv_code: str | None = None, tier: str = 'extended') -> int`
Inserts a new league metadata record or updates an existing one if the `slug` conflicts.
*   **Parameters**:
    *   `name` (str): Display name of the league (e.g., "Premier League").
    *   `country` (str): Associated country.
    *   `slug` (str): Unique, URL-safe identifier.
    *   `api_football_id` (int, optional): ID assigned by API-Football.
    *   `fdo_code` (str, optional): League code assigned by Football-Data.org.
    *   `csv_code` (str, optional): Division identifier for football-data.co.uk CSVs.
    *   `tier` (str): Tier rating, either `"core"` or `"extended"`.
*   **Returns**: The database integer primary key (`id`) of the upserted league.
*   **Internal Operations**: Executes an `INSERT INTO leagues ... ON CONFLICT(slug) DO UPDATE` query, enforcing constraints, and then performs a quick select to retrieve the resulting primary key.

#### `Database.upsert_team(self, name: str, league_id: int, api_football_id: int | None = None, fdo_id: int | None = None, short_name: str | None = None, aliases: list[str] | None = None, logo_url: str | None = None) -> int`
Saves team details to the database, mapping IDs from different API sources to a single internal record.
*   **Parameters**:
    *   `name` (str): Primary display name of the team.
    *   `league_id` (int): Internal league database primary key reference.
    *   `api_football_id` (int, optional): Team ID from API-Football.
    *   `fdo_id` (int, optional): Team ID from Football-Data.org.
    *   `short_name` (str, optional): Abbreviated team name.
    *   `aliases` (list[str], optional): Alternative names used by other sources.
    *   `logo_url` (str, optional): Logo URL.
*   **Returns**: The team's database primary key (`id`).
*   **Internal Operations**: It first searches for a match using `api_football_id`. If one is found, it updates the record. If not, it executes an `INSERT ... ON CONFLICT(name, league_id) DO UPDATE` query. It serializes the alias list to a JSON string before inserting it.

#### `Database.upsert_match(self, api_football_id: int | None, league_id: int, season: str, date: str, status: str, home_team_id: int, away_team_id: int, matchday: int | None = None, home_score: int | None = None, away_score: int | None = None, ht_home_score: int | None = None, ht_away_score: int | None = None, referee: str | None = None, venue: str | None = None, source: str = 'api_football') -> int`
Inserts a new match record or updates an existing one.
*   **Parameters**:
    *   `api_football_id` (int, optional): ID from API-Football.
    *   `league_id` (int): Internal league ID.
    *   `season` (str): Season year (e.g., `"2025"`).
    *   `date` (str): ISO 8601 timestamp string.
    *   `status` (str): Current status (e.g., `"SCHEDULED"`, `"FINISHED"`).
    *   `home_team_id` / `away_team_id` (int): Database references for the teams.
    *   `home_score` / `away_score` (int, optional): Full-time scores.
    *   `ht_home_score` / `ht_away_score` (int, optional): Half-time scores.
    *   `referee` / `venue` (str, optional): Name of referee and stadium.
    *   `source` (str): Data source identifier.
*   **Returns**: The database primary key (`id`) of the match.

#### `Database.get_upcoming_matches(self, league_id: int | None = None, from_date: str | None = None, to_date: str | None = None) -> list[dict[str, Any]]`
Queries the database for scheduled or in-progress matches within a date range.
*   **Parameters**:
    *   `league_id` (int, optional): Filter by a specific league.
    *   `from_date` (str, optional): Start date string (UTC).
    *   `to_date` (str, optional): End date string (UTC).
*   **Returns**: A list of match dictionaries, containing team names, IDs, dates, statuses, and scores.
*   **Internal Operations**: If `from_date` and `to_date` are omitted, the method defaults to a window from 1 day ago to 7 days in the future. It joins the `matches` table with the `teams` table to include the home and away team display names.

#### `Database.get_accuracy_stats(self, market: str | None = None, league_id: int | None = None) -> dict[str, Any]`
Computes the historical prediction accuracy.
*   **Parameters**:
    *   `market` (str, optional): Filter statistics to a specific market (e.g., `"1x2"`).
    *   `league_id` (int, optional): Filter statistics to a specific league.
*   **Returns**: A dictionary containing accuracy metrics:
    *   `total_predictions` (int): Total number of predictions evaluated.
    *   `correct_predictions` (int): Number of correct predictions.
    *   `accuracy_pct` (float): Percent of predictions that were correct.
    *   `brier_score` (float): Brier score measuring probability calibration.
    *   `by_confidence` (dict): Accuracy breakdown grouped by confidence levels (HIGH, MEDIUM, LOW).

---

## 4. Ingestion Module (`src/fetchers/`)

The ingestion module handles retrieving and normalizing data from external API providers and CSV files.

### A. Cache Manager (`cache.py`)

#### `class CacheManager`
Implements the caching layer for API responses, storing them in the `api_cache` table.
*   **Methods**:
    *   `get(self, url: str, params: dict | None = None, source: str = 'api_football') -> dict | list | None`: Calculates the cache key by calling `make_cache_key(url, params)` and queries the database for a valid, unexpired entry. Returns the parsed JSON payload, or `None` if the cache missed or expired.
    *   `set(self, url: str, params: dict | None, source: str, data: dict | list, ttl: int) -> None`: Computes the cache key and stores the JSON payload in the database. The expiration date is set to `now + ttl`.
    *   `clear_expired(self) -> int`: Deletes all expired cache entries and returns the number of deleted records.

---

### B. API-Football Client (`api_football.py`)

#### `class APIFootballClient`
An asynchronous HTTP client for the API-Football v3 service.
*   **Constructor**: `APIFootballClient(db: Database)`
*   **Context Manager**:
    *   `async def __aenter__(self) -> APIFootballClient`: Retrieves the API key from the settings table and instantiates an `httpx.AsyncClient` with the correct authentication headers.
    *   `async def __aexit__(self, *exc) -> None`: Closes the underlying HTTP client.
*   **Core Methods**:
    *   `async def _request(self, endpoint: str, params: dict | None = None, ttl: int | None = None) -> dict`: Coordinates cache lookups, checks daily API quotas, enforces the sliding window rate limit (10 requests per minute), executes the HTTP GET request, caches successful responses, increments the daily usage counter, and returns the response.
    *   `async def fetch_and_store_fixtures(self, league_slug: str) -> int`: Fetches upcoming and recent matches for the current season, registers participating teams, resolves name differences using the aliases array, and upserts the match records. Returns the number of matches saved.
    *   `async def fetch_and_store_standings(self, league_slug: str) -> int`: Syncs the league standings table.
    *   `async def fetch_and_store_team_stats(self, league_slug: str) -> int`: Fetches seasonal statistics (such as goal averages and BTTS rates) for all teams in the league and saves them to the database.

---

### C. Football-Data.org Client (`football_data_org.py`)

#### `class FootballDataOrgClient`
An asynchronous HTTP client for Football-Data.org. It functions similarly to `APIFootballClient` but uses different headers (`X-Auth-Token`) and endpoints to sync fixtures and standings, helping offload requests from the main API-Football quota.

---

### D. Historical CSV Loader (`csv_loader.py`)

#### `class CSVLoader`
A utility for downloading and importing historical match data from football-data.co.uk CSV files.
*   **Methods**:
    *   `download_season_csv(self, league_slug: str, season_year: int) -> Path`: Downloads the CSV file for a given league and season to the local directory `data/historical/`.
    *   `parse_csv(self, csv_path: Path, league_slug: str) -> list[dict]`: Reads the CSV using pandas and maps the column names (e.g., `FTHG` to goals scored, `HST` to shots on target, `B365H` to betting odds) to a normalized dictionary format.
    *   `load_season(self, league_slug: str, season_year: int) -> int`: Downloads the CSV, parses the records, maps participating team names to database IDs, and upserts the matches and match statistics. Returns the count of imported matches.

---

## 5. Analysis Engine (`src/analysis/`)

This module evaluates the database records to generate match analyses and betting predictions.

### A. The 12 Analysis Factors (`factors.py`)

Every factor function shares a common signature:
`factor(db: Database, home_team_id: int, away_team_id: int, league_id: int, season: str) -> FactorResult`

*   **Inputs**: The database instance, the primary keys of the home and away teams, the league ID, and the current season string.
*   **Returns**: A `FactorResult` containing a score ($s \in [-1.0, 1.0]$), a confidence rating ($c \in [0.0, 1.0]$), a text explanation, and a dictionary of raw backing statistics.

1.  **Recent Form (`recent_form`)**: Queries the last 10 games for both teams using `db.get_recent_matches`. It calculates points per game (PPG) and applies an exponential decay weight to favor recent results.
2.  **Home Form (`home_form`)**: Analyzes the home team's performance record specifically in home matches.
3.  **Away Form (`away_form`)**: Analyzes the away team's performance record specifically in away matches.
4.  **Scoring Frequency (`scoring_frequency`)**: Compares the average goals scored per game by both teams.
5.  **Defensive Record (`defensive_record`)**: Compares the clean sheet rates and average goals conceded by both teams.
6.  **Head-to-Head (`head_to_head`)**: Evaluates the historical results of matches played directly between the two teams.
7.  **League Position (`league_position`)**: Compares the teams' current standings and points gap in the league table.
8.  **Goal Timing (`goal_timing`)**: Checks for patterns in when teams score (first half vs. second half) to identify potential scoring trends.
9.  **Momentum (`momentum`)**: Compares each team's average points from their last 3 matches against their average from the preceding 3 matches to detect shifts in form.
10. **Scoring Patterns (`scoring_patterns`)**: Analyzes historical frequencies of Both Teams to Score (BTTS) and Over/Under goals.
11. **Rest Days (`rest_days`)**: Compares the number of rest days each team has had since their last match to identify potential physical fatigue.
12. **Rivalry (`rivalry`)**: Detects local derbies (teams from the same city or region) and increases the likelihood of an upset in the prediction.

---

### B. Analysis Engine (`engine.py`)

#### `class AnalysisEngine`
The central orchestrator that coordinates the match analysis process.
*   **Methods**:
    *   `analyze_match(self, match_id: int) -> MatchAnalysis`: Coordinates the analysis process for a match:
        1.  Loads match details and participating team profiles.
        2.  Evaluates the 12 factors, returning neutral scores if any throw an exception.
        3.  Computes the overall score and confidence rating using a weighted average.
        4.  Gathers team season statistics.
        5.  Calls the market prediction models in `markets.py`.
        6.  Generates a natural language summary and records the predictions.
        7.  Returns a `MatchAnalysis` object.
    *   `analyze_matchweek(self, league_slug: str | None = None, from_date: str | None = None, to_date: str | None = None) -> list[MatchAnalysis]`: Runs batch analyses for a collection of upcoming fixtures.

---

### C. Market Models (`markets.py`)

Contains the mathematical models for the 9 supported betting markets. These models convert factor scores and team statistics into probability ratings. Each model uses the formula:
$$P_{\text{final}} = 0.6 \cdot P_{\text{historical}} + 0.4 \cdot P_{\text{model}}$$

*   **Models**:
    *   `model_1x2(...) -> list[MarketRecommendation]`: Calculates the probabilities for a Home Win ($P_H$), Draw ($P_D$), and Away Win ($P_A$) by passing the overall score through a logistic function:
        $$P_{\text{model, H}} = \frac{1}{1 + e^{-3 \cdot S}}$$
        The raw model outputs are normalized to sum to 1.0 and blended with the teams' season win and draw frequencies.
    *   `model_double_chance(...) -> list[MarketRecommendation]`: Combines the probabilities from the `1X2` model:
        $$P(1X) = P_H + P_D, \quad P(X2) = P_D + P_A, \quad P(12) = P_H + P_A$$
    *   `model_over_under(..., line: float) -> list[MarketRecommendation]`: Estimates total goals using a Poisson-like distribution, calculated from average goals scored and conceded. The result is blended with the historical rate of matches that exceeded or fell below the goal line.
    *   `model_btts(...) -> MarketRecommendation`: Estimates the probability of both teams scoring by analyzing scoring frequency and clean sheet rates, blending the output with seasonal BTTS percentages.
    *   `model_draw_no_bet(...) -> MarketRecommendation`: Calculates draw-no-bet probabilities by removing the draw outcome and scaling the remaining probabilities to sum to 1.0:
        $$P_{\text{DNB, H}} = \frac{P_H}{P_H + P_A}$$
    *   `model_win_to_nil(...) -> list[MarketRecommendation]`: Calculates the probability of a team winning without conceding a goal by combining the win probability with the clean sheet rate.
    *   `model_win_either_half(...) -> list[MarketRecommendation]`: Predicts the probability of a team winning at least one half of the match.
    *   `model_team_over_under(..., line: float) -> list[MarketRecommendation]`: Predicts whether a specific team's individual goal count will exceed a set limit.
    *   `model_handicap(...) -> list[MarketRecommendation]`: Estimates the probability of a team winning after applying a virtual goal handicap (e.g., $+1$ or $-1$).

---

## 6. Tracking & Performance Calibration (`src/tracking/`)

This module evaluates settled predictions, displays accuracy metrics, and manages the self-calibration feedback loop.

### A. Result Recorder (`recorder.py`)

#### `class ResultRecorder`
Evaluates prediction accuracy when new match results are imported.
*   **Methods**:
    *   `record_match_results(self, match_id: int) -> dict[str, Any]`: Pulls the final score, gathers all predictions for that match, evaluates each prediction using `_evaluate_prediction()`, and saves the result in `prediction_results`. Returns a summary of predictions evaluated.
    *   `auto_record_all(self) -> dict[str, int]`: Finds completed matches that have predictions but no recorded outcomes and processes them.
    *   `_evaluate_prediction(self, prediction: dict, match: dict) -> tuple[str, bool]`: Compares the prediction pick with the final match scores:
        *   `1x2`: Checks if the pick matches the winning team or a draw.
        *   `double-chance`: Checks if the outcome is covered by the selected options (e.g. 1X).
        *   `over-under-X`: Checks if the sum of goals is greater than or less than the goal line.
        *   `gg-ng`: Checks if both teams scored (GG) or at least one team kept a clean sheet (NG).
        *   `draw-no-bet`: Returns `VOID` if the match ended in a draw. Otherwise, checks if the selected team won.
        *   `win-to-nil`: Checks if the selected team won and the opposing team scored 0 goals.

---

### B. Accuracy Tracker (`accuracy.py`)

#### `class AccuracyTracker`
Provides accuracy metrics and simulation reports.
*   **Methods**:
    *   `get_dashboard(self, market: str | None = None, league: str | None = None) -> dict`: Compiles accuracy metrics, including hit rates, Brier scores, calibration curves, and win/loss streaks.
    *   `get_roi_simulation(self, stake: float = 1.0) -> dict`: Simulates the returns on investment (ROI) if a flat-stake strategy had been followed for all predictions that met the confidence thresholds.

---

### C. Weight Calibrator (`calibrator.py`)

#### `class Calibrator`
Adjusts factor weights based on historical accuracy.
*   **Methods**:
    *   `load_weights(self) -> dict[str, float]`: Loads weights from `data/weights.json`, falling back to `config.FACTOR_WEIGHTS` if the file does not exist.
    *   `save_weights(self, weights: dict[str, float]) -> None`: Saves updated weights to `data/weights.json`.
    *   `can_calibrate(self) -> bool`: Checks if the database contains at least 100 predictions with settled outcomes.
    *   `calibrate(self) -> dict[str, Any]`: Nudges factor weights based on variance between factor signal accuracy and baseline performance, then re-normalizes them.
        1. Evaluates overall baseline prediction accuracy ($A_{\text{base}}$).
        2. Identifies predictions where a factor had a strong signal ($|s_f| > 0.3$, $c_f > 0.4$).
        3. Computes the factor's accuracy ($A_f$) within this subset.
        4. Calculates the weight adjustment: $w_f = w_f + 0.1 \cdot (A_f - A_{\text{base}})$.
        5. Normalizes all weights to sum to 1.0, ensuring no weight falls below `MIN_WEIGHT` ($0.005$).
        6. Logs weight adjustments to the `weight_history` table and saves them to `data/weights.json`.

---

## 7. Web Server Layer (`src/api/`)

The web server layer handles routing, business logic, template rendering, and JSON endpoints.

### API Routes Mapping

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| **GET** | `/` | Renders the onboarding screen or redirects to the dashboard. |
| **GET** | `/dashboard` | Displays the main overview page, including active match counts, quota status, and upcoming fixtures. |
| **GET** | `/analysis` | Lists upcoming fixtures with filter controls for leagues and date ranges. |
| **GET** | `/analysis/match/{id}` | Displays a deep-dive view of a match, including charts for the 12 factors and market recommendations. |
| **GET** | `/picks` | Lists recommendations that meet the minimum confidence threshold. |
| **GET** | `/parlay` | Opens the interactive parlay builder tool. |
| **POST** | `/api/v1/parlay` | Saves a parlay draft to the database. |
| **POST** | `/api/v1/parlay/{id}/submit` | Saves the parlay slip and marks it as active. |
| **POST** | `/api/v1/parlay/{id}/settle` | Compares the parlay selections with match results and marks the slip as won or lost. |
| **POST** | `/api/v1/fetch` | Starts a background synchronization task. |
| **GET** | `/api/v1/fetch/stream/{id}` | Streams synchronization logs in real-time using Server-Sent Events (SSE). |

---

## 8. Frontend Structure

The user interface uses Jinja2 templates and a CSS variables-based layout stylesheet.

### User Interface Assets

*   `src/templates/base.html`: The parent layout file. It includes the sidebar navigation menu, the dark/light theme toggle, and the modal progress overlay for monitoring live data synchronization.
*   `src/templates/onboarding.html`: A step-by-step setup screen that guides users through inputting API keys, selecting leagues, and enabling betting markets.
*   `src/templates/parlay.html`: An interactive parlay builder. It displays active selections and dynamically updates combined decimal odds and win probabilities as picks are added or removed.
*   `src/static/css/style.css`: Contains CSS variables and style definitions.
    *   **Theme Management**: Uses custom CSS properties (e.g., `--bg-primary`, `--text-primary`) mapped to the `[data-theme="dark"]` and `[data-theme="light"]` selectors.
    *   **Layout & Styling**: Uses a CSS flexbox layout with custom-styled elements, including Apple-inspired scrollbars, custom chevrons for dropdown menus, and color-coded badges for quota limits.

---

## 9. Click CLI Interface (`src/cli.py`)

The Command Line Interface (CLI) is built using Click, providing command-line access to the system's administration and reporting tools.

### Available CLI Commands

*   `serve`: Starts the local FastAPI web server using Uvicorn.
*   `fetch`: Synchronizes current data for enabled leagues.
*   `load-history`: Imports historical statistics from downloaded CSV files.
*   `status`: Displays database statistics, cache efficiency, and API quota usage.
*   `analyze matchweek`: Generates prediction reports for upcoming matches.
*   `analyze match "Home vs Away"`: Generates a factor analysis report for a specific fixture.
*   `picks`: Displays a table of recommendations that meet the minimum confidence threshold.
*   `parlay`: Suggests an optimized parlay slip based on selected parameters.
*   `record`: Evaluates settled predictions against completed match results.
*   `accuracy`: Displays accuracy statistics and historical ROI metrics.
*   `calibrate`: Runs the self-calibration loop to update factor weights.
*   `bot start`: Launches the Telegram bot listener.

---

## 10. Quirks, Gotchas, and Core Implementations

This section highlights key behaviors, data normalization rules, and design choices within the codebase.

### SQLite Connection Management
SQLite connections are not thread-safe across asynchronous event loops. To avoid database locking issues, the `_connect()` context manager creates, configures, and closes a connection on demand for each transaction:
```python
@contextmanager
def _connect(self) -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(str(self.db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
```

### Team Name Normalization and Aliasing
External API providers often use slightly different names for the same team (e.g., "Manchester United" vs. "Man United"). To prevent duplicate team records, the database schema uses a `teams.aliases` column stored as a JSON array. When importing matches or statistics, the system queries the database using name matching and alias arrays to map teams to a single internal ID:
```python
def find_team_by_name(self, name: str, league_id: int) -> dict[str, Any] | None:
    # Checks exact name, short name, and items in the aliases JSON array
    # using SQL LIKE or JSON_EACH functions where applicable.
```

### API Envelope Structures
API-Football wraps all response payloads in a standard envelope structure:
```json
{
  "get": "fixtures",
  "parameters": { "league": "39", "season": "2025" },
  "errors": [],
  "results": 1,
  "response": [ ... actual data ... ]
}
```
The client validates this envelope and checks for API-level errors before returning the `response` array to the calling method.

### UTC Datetime Normalization
Python 3.14 deprecates the `datetime.utcnow()` method. The codebase is transitioning to using timezone-aware objects:
```python
# Transitioning from:
today = datetime.utcnow()
# To:
today = datetime.now(timezone.utc)
```
When parsing dates from external APIs or CSV files, timestamps are normalized to ISO 8601 strings in UTC format before being stored in the database.
