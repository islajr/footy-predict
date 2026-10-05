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
│   ├── standings.py            # League tables derived from stored match results
│   ├── main.py                 # Core entry point (FastAPI server bootstrap)
│   ├── cli.py                  # Click command-line interface commands
│   ├── fetchers/
│   │   ├── cache.py            # Deterministic SHA-256 API caching
│   │   ├── api_football.py     # Client for API-Football (primary data source)
│   │   ├── football_data_org.py # Client for Football-Data.org (secondary source)
│   │   └── csv_loader.py       # Parser for football-data.co.uk CSV data
│   ├── analysis/
│   │   ├── factors.py          # 16 analysis factors with venue-split data and Poisson stats
│   │   ├── engine.py           # AnalysisEngine orchestrator; produces decision-support cards & narrative prose
│   │   ├── markets.py          # Multi-direction market models (~61 picks per match)
│   │   └── market_narrator.py  # Multi-factor market ranking & per-pick narrative commentary generator
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
*   `FACTOR_WEIGHTS`: Baseline weights for all 15 factors (must sum to exactly 1.0). Keys include the three new Phase A factors: `venue_advantage`, `attacking_matchup`, `xg_quality`.
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

-- Predictions registry (one row per match × market × pick direction)
CREATE TABLE predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id INTEGER NOT NULL REFERENCES matches(id),
    market TEXT NOT NULL,
    pick TEXT NOT NULL,       -- Direction e.g. "Over 2.5", "1 (Home Win)", "Home Win to Nil: No"
    probability REAL NOT NULL,
    confidence TEXT NOT NULL,
    implied_odds REAL,
    rationale TEXT NOT NULL,
    risk_factors TEXT DEFAULT '[]', -- JSON array
    supporting_stats TEXT DEFAULT '{}', -- JSON object
    factor_scores TEXT DEFAULT '{}', -- JSON object (factor_name -> score)
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(match_id, market, pick)  -- Broadened in migration 003 to allow multi-direction
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

#### `Database.upsert_standing(self, league_id: int, season: str, team_id: int, position: int, played: int = 0, ...) -> None`
Inserts or updates one row of a league table.
*   **Internal Operations**: The conflict clause ends in `WHERE excluded.played > 0 OR standings.played = 0`, so a blank incoming row cannot overwrite a complete stored one. Without that guard a single pre-season fetch destroyed 53 real rows; the trailing `WHERE` turns the update into a no-op instead.

#### `Database.delete_standings_except(self, league_id: int, season: str, keep_team_ids: list[int]) -> int`
Drops standings rows for teams outside `keep_team_ids`, returning the number removed. Returns 0 without touching the table when `keep_team_ids` is empty.
*   **Why it exists**: rewriting a table has to replace rather than merge. Premier League 2025-26 held 23 rows for a 20-team competition — three relegated clubs left by an earlier fetch — and writing 20 correct rows over that would leave three stragglers at position 1, so the repaired table would still fail `standings_are_ranked` and the rebuild would accomplish nothing.

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

### E. Derived League Tables (`src/standings.py`)

A fifth data source that touches no network: it reconstructs league tables from the `matches` rows already stored. Two jobs — coverage, because standings exist only for the league-seasons some API was asked for while the rest have complete results and no table, and repair, because a mislabelled pre-season fetch overwrote real tables whose source matches all survived.

#### `compute_standings(db: Database, league_id: int, season: str) -> list[dict[str, Any]]`
Aggregates every FINISHED match with non-NULL scores into a ranked table.
*   **Returns**: one row per team appearing in at least one finished fixture, ordered by position, carrying points, W/D/L, goals for/against, goal difference, the home and away splits, and the last-five `form` string oldest-first. A team with no finished match is **absent** rather than present on zero — the table describes what was played.
*   **Ranking**: points, then goal difference, then goals scored, then name. Several leagues break ties on head-to-head instead, so two level teams can sit a place either way of their official order; the factors reading this compare position gaps and points gaps, where that is immaterial.

#### `incoherent_reason(db: Database, league_id: int, season: str, table: list[dict]) -> str | None`
Returns why a derived table cannot be trusted, or `None` if it can.
*   **The check**: once a season has no fixtures left to play, every pair of teams in it must have met at least once. A club stored under two `teams` rows is precisely the pair that never can.
*   **Why not played counts**: unequal totals are the more obvious symptom but the wrong test. Split-format leagues (the Belgian Pro League's championship and relegation playoffs) legitimately finish a season with teams on different totals — 35 to 40 in 2025-26. The pairing test clears those and still catches every identity split.
*   Mid-season nothing is checked; pairs that have not met yet are the normal state of an incomplete fixture list.

#### `store_derived_standings(db: Database, league_id: int, season: str, force: bool = False) -> int`
Derives a table and writes it, returning rows written or 0 if declined. Three refusals, all logged:
*   The derivation fails `standings_are_ranked` — writing it recreates the every-team-at-position-1 condition the repair exists to remove. **Not overridable**; there is no reading under which that write is wanted.
*   The match set is incoherent per `incoherent_reason`. Overridable by `force`.
*   A table is already stored **and** already ranks. That is API data carrying a form string and splits the derivation only approximates, and it may cover teams whose matches were never stored, so replacing it is a downgrade and must be asked for. Overridable by `force`.

The write replaces rather than merges: `delete_standings_except` prunes rows for teams outside the derived table first.

#### `derivable_league_seasons(db: Database) -> list[dict[str, Any]]`
Every league-season with finished matches, newest first, each with `league_id`, `slug`, `season`, `finished`, `stored`, and `needs_table` (`not standings_are_ranked(stored)`). This is what `footy derive-standings` iterates.

> The season a table belongs to is the season its **matches** carry. Callers must not take it from an existing standings row — that label is the thing under suspicion.

---

## 5. Analysis Engine (`src/analysis/`)

This module evaluates the database records to generate match analyses and betting predictions.

### A. The 16 Analysis Factors (`factors.py`)

Every factor function shares a common signature:
`factor(db: Database, home_team_id: int, away_team_id: int, league_id: int, season: str) -> FactorResult`

*   **Inputs**: The database instance, the primary keys of the home and away teams, the league ID, and the current season string.
*   **Returns**: A `FactorResult` containing a score ($s \in [-1.0, 1.0]$), a confidence rating ($c \in [0.0, 1.0]$), a text explanation, and a dictionary of raw backing statistics (`data_points`).

Key design principles:
- **Venue-split data**: home team's home record vs. away team's away record is preferred over combined season stats
- **Poisson probabilities**: pre-computed by `scoring_patterns` and consumed by market models
- **Explicit data keys**: fixed top-level keys (`home_gpg`, `away_gpg`, `home_cs_rate_home`, `away_cs_rate_away`, `min_rest`, `btts_avg_pct`, etc.) guarantee correct cross-factor data exchange

1.  **Recent Form (`recent_form`)**: Queries the last 10 games for both teams. Calculates PPG, win rate, draw rate, form string (W/D/L), and applies exponential decay weighting to favour recent results.
2.  **Home Form (`home_form`)**: Home team's home-only record: win rate, GPG at home, clean sheet rate at home, failed-to-score rate, current winning/losing streak.
3.  **Away Form (`away_form`)**: Away team's away-only record: same metrics as home form, applied to away fixtures.
4.  **Scoring Frequency (`scoring_frequency`)**: Venue-split GPG (home at home, away from home), scored-in percentage, blank-game count. Exposes `home_gpg`, `away_gpg`, `home_home_gpg`, `away_away_gpg`, `home_scored_in_pct`, `away_scored_in_pct`.
5.  **Defensive Record (`defensive_record`)**: Venue-split clean sheet rates and CPG. Exposes `home_cs_rate`, `away_cs_rate`, `home_cs_rate_home`, `away_cs_rate_away`, `home_cpg`, `away_cpg`.
6.  **Head-to-Head (`head_to_head`)**: H2H win/draw/loss record, venue-split H2H, last 5 scorelines, average goals, BTTS rate in H2H fixtures.
7.  **League Position (`league_position`)**: Table position, points gap, goals-for/against per game, form string from league table, win/draw percentage.
8.  **Goal Timing (`goal_timing`)**: First-half vs second-half goal distribution. Exposes `home_1h_win_rate`, `away_1h_win_rate` for use by WEH market model.
9.  **Momentum (`momentum`)**: Last 3 vs previous 3 results comparison for improving/declining trajectory.
10. **Scoring Patterns (`scoring_patterns`)**: Historical BTTS rates, O/U rates, and pre-computed Poisson probabilities (`poisson_over_0_5` through `poisson_over_3_5`, `poisson_btts`, `poisson_home_over_0_5`, `poisson_away_over_0_5`).
11. **Rest Days (`rest_days`)**: Days since last match. Exposes `min_rest` key for fixture congestion risk detection. Flags games where either team has < 3 days rest.
12. **Rivalry (`rivalry`)**: Derby detection for same-city/region teams; adjusts expected upset frequency.
13. **Venue Advantage (`venue_advantage`)**: Compares home team's home-only performance vs their away performance and vice versa for the away team. Produces a pure ground-bias signal.
14. **Attacking Matchup (`attacking_matchup`)**: Direct attack-vs-defence blending. Home attack GPG minus away defensive CPG and vice versa. Produces `expected_home_goals`, `expected_away_goals`, `expected_total`, `expected_goal_diff`.
15. **xG Quality (`xg_quality`)**: Shot quality analysis via expected goals data from `match_stats`. Detects over- and underperformance relative to xG; flags keepers on hot form.
16. **Opponent Strength (`opponent_strength`)**: Adjusts recent PPG performance by opponent strength derived from league standings rankings.

---

### B. Analysis Engine (`engine.py`)

#### `class AnalysisEngine`
The central orchestrator that coordinates the match analysis process.
*   **Methods**:
    *   `analyze_match(self, match_id: int) -> MatchAnalysis`: Coordinates the analysis process for a match:
        1.  Loads match details and participating team profiles.
        2.  Evaluates all 16 factors, returning neutral scores if any throw an exception.
        3.  Computes the overall score and confidence rating using a weighted average.
        4.  Gathers team season statistics.
        5.  Calls the market prediction models in `markets.py`.
        6.  Invokes `_generate_summary()` to synthesize a natural language narrative paragraph from underlying factor `data_points` (form strings, PPG, H2H scorelines, league positions, momentum, and defensive matchups).
        7.  Calls `rank_and_narrate()` in `market_narrator.py` to rank all recommendations by mathematical backing score, generate per-pick commentary, and identify shared statistical foundations across market options.
        8.  Returns a `MatchAnalysis` object containing the verdict, summary narrative, recommendations, and market assessments.
    *   `analyze_matchweek(self, league_slug: str | None = None, from_date: str | None = None, to_date: str | None = None) -> list[MatchAnalysis]`: Runs batch analyses for a collection of upcoming fixtures.

---

### C. Market Models (`markets.py`)

Contains the mathematical models for the 15 supported betting market families. Each model emits **all meaningful pick directions** (~61 picks per match total) — no filtering occurs at this layer.

Probability blending: $P_{\text{final}} = 0.6 \cdot P_{\text{historical}} + 0.4 \cdot P_{\text{model}}$

When Poisson probabilities are available from `scoring_patterns`, they are blended 55/45 with the logistic fallback.

---

### D. Ranked Market Assessment & Narrative Generator (`market_narrator.py`)

Produces ranked, human-readable market assessments alongside per-pick statistical narratives.

*   **`AssessedMarket` Dataclass**:
    *   `market_code` (str): Unique market code (e.g. `"1x2"`, `"double-chance"`, `"win-to-nil"`).
    *   `pick` (str): Pick direction (e.g. `"Home Win"`, `"Over 2.5"`, `"Home Win to Nil: No"`).
    *   `tier` (str): Confidence tier (`"HIGH"`, `"MEDIUM"`, `"LOW"`).
    *   `probability` (float): Blended model probability.
    *   `confidence` (float): Factor confidence rating.
    *   `implied_odds` (float): Fair model implied odds ($1 / \text{probability}$).
    *   `backing_score` (float): Combined quality score used for market ranking:
        $$\text{backing\_score} = P \times (1 + 0.15 \times \text{aligned\_factors})$$
        Where $\text{aligned\_factors}$ counts independent factors aligned with the pick direction.
    *   `shared_thesis_with` (str | None): Code of a higher-ranked pick sharing the same statistical basis.
    *   `shared_thesis_note` (str | None): Decision advice for comparing bookmaker odds across connected picks.
    *   `narrative` (str): Per-pick narrative explaining the statistical rationale.
    *   `supporting_stats` (dict): Underlying data points supporting the pick.
    *   `risk_factors` (list[str]): Key risk factors flagged for the pick.

*   **Core Functions**:
    *   `rank_and_narrate(recommendations: list[MarketRecommendation], factor_results: list[FactorResult]) -> list[AssessedMarket]`: Sorts all recommendations by `backing_score` descending without dropping picks, detects shared statistical foundations (e.g. Home Win vs DNB Home vs Double Chance 1X), attaches odds decision advice, and generates specific narrative prose for every option.


*   **`model_1x2`**: Emits 3 picks — `1 (Home Win)`, `X (Draw)`, `2 (Away Win)`. Probabilities normalised to sum to 1. Rationales include form strings, streak detection, table position, and H2H record.
*   **`model_double_chance`**: Emits 3 picks — `1X`, `X2`, `12`. Each rationale explains the risk profile of that sub-variant.
*   **`model_over_under`**: Emits 2 picks per line (`Over N` and `Under N`) for lines 0.5, 1.5, 2.5, 3.5 (8 total). Poisson probabilities from `scoring_patterns` used where available; venue-split GPG from `attacking_matchup` preferred over season totals.
*   **`model_gg_ng`**: Emits 2 picks — `GG (Both Teams Score)` and `NG (Clean Sheet Expected)`. Uses Poisson BTTS probability and venue-split CS rates. NG rationale includes blank-game counts.
*   **`model_draw_no_bet`**: Emits 2 picks — `DNB Home` and `DNB Away`. Draw probability surfaced explicitly in rationale.
*   **`model_win_to_nil`**: Emits 4 picks using **Yes/No binary framing**:
    - `Home Win to Nil: Yes` — home wins to nil (compound: win × CS)
    - `Home Win to Nil: No` — away scores ≥ 1 goal regardless of result (primary value direction)
    - `Away Win to Nil: Yes` — away wins to nil
    - `Away Win to Nil: No` — home scores ≥ 1 goal regardless of result
    The `No` picks are positioned as the primary value-bet direction in the rationale.
*   **`model_win_either_half`**: Emits 2 picks — `Home Win Either Half` and `Away Win Either Half`. Uses `home_1h_win_rate`/`away_1h_win_rate` from `goal_timing`.
*   **`model_team_over_under`**: Emits 4 picks per line (`Home Over`, `Home Under`, `Away Over`, `Away Under`) for lines 0.5 and 1.5 (8 total). Market code: `team-over-under-{line}`.
*   **`model_handicap`**: Emits up to 3 picks — primary, conservative, and aggressive lines — using `expected_goal_diff` from `attacking_matchup`.

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
        *   `win-to-nil`: Evaluates the new Yes/No binary framing:
            - **Yes picks**: team must win AND keep a clean sheet.
            - **No picks**: the opposing team must score ≥ 1 goal (regardless of result). This is the primary value direction.
            - Legacy `"Home"` / `"Away"` format also supported for backwards compatibility.

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
| **GET** | `/analysis/match/{id}` | Displays a deep-dive view of a match, including charts for the 16 factors and market recommendations. |
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
*   `derive-standings`: Rebuilds league tables by aggregating stored match results. No API cost.
*   `status`: Displays database statistics, cache efficiency, and API quota usage.
*   `analyze matchweek`: Generates prediction reports for upcoming matches.
*   `analyze match "Home vs Away"`: Generates a factor analysis report for a specific fixture.
*   `picks`: Displays a table of recommendations that meet the minimum confidence threshold.
*   `report`: Displays weekly decision-support pick report with VERDICT line, match-level summary narrative, and ranked market assessment.
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
