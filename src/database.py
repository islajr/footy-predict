"""
Footy Predict — Database Layer

SQLite schema, connection management, and data access methods.
All database operations go through the Database class.

Tables:
    leagues          — League metadata and API identifiers
    teams            — Team info with cross-source ID mappings
    matches          — Historical + upcoming fixtures
    match_stats      — Per-team match statistics (shots, corners, etc.)
    team_season_stats — Cached seasonal aggregates from API
    head_to_head     — H2H records between team pairs
    standings        — Current league tables
    predictions      — System's market recommendations with rationale
    prediction_results — Actual outcomes for accuracy tracking
    api_cache        — Response cache with TTL expiration
    api_usage        — Daily API quota tracking
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Generator

from src.config import DB_PATH
from src.team_names import canonical_key


# ═══════════════════════════════════════════════════════════════
#  Schema
# ═══════════════════════════════════════════════════════════════

SCHEMA = """
-- League metadata
CREATE TABLE IF NOT EXISTS leagues (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL,
    country         TEXT    NOT NULL,
    slug            TEXT    NOT NULL UNIQUE,
    api_football_id INTEGER UNIQUE,
    fdo_code        TEXT,
    csv_code        TEXT,
    tier            TEXT    NOT NULL DEFAULT 'extended'
);

-- Teams with cross-source ID mappings
CREATE TABLE IF NOT EXISTS teams (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL,
    api_football_id INTEGER UNIQUE,
    fdo_id          INTEGER,
    league_id       INTEGER REFERENCES leagues(id),
    short_name      TEXT,
    aliases         TEXT    DEFAULT '[]',  -- JSON array of alternative names
    logo_url        TEXT,
    -- Source-independent identity: "Man United" and "Manchester United FC"
    -- share a key, so a club cannot split across rows. See team_names.py.
    canonical_key   TEXT,
    UNIQUE(name, league_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_teams_canonical
    ON teams(league_id, canonical_key);

-- Match fixtures (historical + upcoming)
CREATE TABLE IF NOT EXISTS matches (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    api_football_id INTEGER UNIQUE,
    league_id       INTEGER NOT NULL REFERENCES leagues(id),
    season          TEXT    NOT NULL,
    matchday        INTEGER,
    date            TEXT    NOT NULL,  -- ISO 8601
    status          TEXT    NOT NULL DEFAULT 'SCHEDULED',
    home_team_id    INTEGER NOT NULL REFERENCES teams(id),
    away_team_id    INTEGER NOT NULL REFERENCES teams(id),
    home_score      INTEGER,
    away_score      INTEGER,
    ht_home_score   INTEGER,
    ht_away_score   INTEGER,
    referee         TEXT,
    venue           TEXT,
    source          TEXT    NOT NULL DEFAULT 'api_football',
    created_at      TEXT    DEFAULT (datetime('now')),
    updated_at      TEXT    DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_matches_date ON matches(date);
CREATE INDEX IF NOT EXISTS idx_matches_league ON matches(league_id, season);
CREATE INDEX IF NOT EXISTS idx_matches_teams ON matches(home_team_id, away_team_id);

-- Fixture identity. Keyed on the calendar date rather than the full timestamp:
-- CSV rows carry midnight where football-data.org carries the true kickoff, and
-- those are the same fixture. The date must be in the key all the same, since
-- split-format leagues (Scottish Premiership, Belgian Pro League) legitimately
-- repeat a home/away pairing within one season.
CREATE UNIQUE INDEX IF NOT EXISTS idx_matches_identity
    ON matches(league_id, season, home_team_id, away_team_id, date(date));

-- Fixture identity for rows that carry a matchday. A date is not stable —
-- football-data.org publishes a provisional kickoff for an unplayed fixture and
-- revises it later, and the revised row lands under a different date key, so
-- the index above sees a new fixture. A pairing occurs at most once per
-- matchday, including in the split-format leagues that put the date in that
-- key, so this one survives a reschedule.
--
-- Partial because CSV loads carry no matchday and would all collide on NULL.
-- Those rows keep the date-keyed identity, which also still catches a
-- matchday-bearing row meeting a matchday-less one on the same date.
CREATE UNIQUE INDEX IF NOT EXISTS idx_matches_identity_matchday
    ON matches(league_id, season, home_team_id, away_team_id, matchday)
    WHERE matchday IS NOT NULL;

-- Per-team match statistics
CREATE TABLE IF NOT EXISTS match_stats (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id        INTEGER NOT NULL REFERENCES matches(id),
    team_id         INTEGER NOT NULL REFERENCES teams(id),
    shots           INTEGER,
    shots_on_target INTEGER,
    possession      REAL,
    corners         INTEGER,
    fouls           INTEGER,
    yellow_cards    INTEGER,
    red_cards       INTEGER,
    offsides        INTEGER,
    passes          INTEGER,
    pass_accuracy   REAL,
    expected_goals  REAL,
    UNIQUE(match_id, team_id)
);

-- Cached seasonal team statistics from API
CREATE TABLE IF NOT EXISTS team_season_stats (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id             INTEGER NOT NULL REFERENCES teams(id),
    league_id           INTEGER NOT NULL REFERENCES leagues(id),
    season              TEXT    NOT NULL,
    matches_played      INTEGER DEFAULT 0,
    wins                INTEGER DEFAULT 0,
    draws               INTEGER DEFAULT 0,
    losses              INTEGER DEFAULT 0,
    goals_scored        INTEGER DEFAULT 0,
    goals_conceded      INTEGER DEFAULT 0,
    home_played         INTEGER DEFAULT 0,
    home_wins           INTEGER DEFAULT 0,
    home_draws          INTEGER DEFAULT 0,
    home_losses         INTEGER DEFAULT 0,
    home_goals_scored   INTEGER DEFAULT 0,
    home_goals_conceded INTEGER DEFAULT 0,
    away_played         INTEGER DEFAULT 0,
    away_wins           INTEGER DEFAULT 0,
    away_draws          INTEGER DEFAULT 0,
    away_losses         INTEGER DEFAULT 0,
    away_goals_scored   INTEGER DEFAULT 0,
    away_goals_conceded INTEGER DEFAULT 0,
    form                TEXT,       -- e.g. "WWDLW" (most recent last)
    clean_sheets        INTEGER DEFAULT 0,
    failed_to_score     INTEGER DEFAULT 0,
    avg_goals_scored    REAL,
    avg_goals_conceded  REAL,
    btts_percentage     REAL,      -- Both Teams To Score %
    over_2_5_percentage REAL,
    under_2_5_percentage REAL,
    penalty_scored      INTEGER DEFAULT 0,
    penalty_missed      INTEGER DEFAULT 0,
    fetched_at          TEXT    DEFAULT (datetime('now')),
    UNIQUE(team_id, league_id, season)
);

-- Head-to-head records between team pairs
CREATE TABLE IF NOT EXISTS head_to_head (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    team_a_id       INTEGER NOT NULL REFERENCES teams(id),
    team_b_id       INTEGER NOT NULL REFERENCES teams(id),
    total_matches   INTEGER DEFAULT 0,
    team_a_wins     INTEGER DEFAULT 0,
    draws           INTEGER DEFAULT 0,
    team_b_wins     INTEGER DEFAULT 0,
    team_a_goals    INTEGER DEFAULT 0,
    team_b_goals    INTEGER DEFAULT 0,
    last_matches    TEXT    DEFAULT '[]',  -- JSON: recent H2H match summaries
    fetched_at      TEXT    DEFAULT (datetime('now')),
    UNIQUE(team_a_id, team_b_id)
);

-- League standings
CREATE TABLE IF NOT EXISTS standings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    league_id       INTEGER NOT NULL REFERENCES leagues(id),
    season          TEXT    NOT NULL,
    team_id         INTEGER NOT NULL REFERENCES teams(id),
    position        INTEGER NOT NULL,
    played          INTEGER DEFAULT 0,
    won             INTEGER DEFAULT 0,
    drawn           INTEGER DEFAULT 0,
    lost            INTEGER DEFAULT 0,
    goals_for       INTEGER DEFAULT 0,
    goals_against   INTEGER DEFAULT 0,
    goal_difference INTEGER DEFAULT 0,
    points          INTEGER DEFAULT 0,
    form            TEXT,
    fetched_at      TEXT    DEFAULT (datetime('now')),
    UNIQUE(league_id, season, team_id)
);

-- System predictions with full rationale
CREATE TABLE IF NOT EXISTS predictions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id        INTEGER NOT NULL REFERENCES matches(id),
    market          TEXT    NOT NULL,       -- Market code (e.g. "over-under-2.5")
    pick            TEXT    NOT NULL,       -- The recommendation (e.g. "Over")
    probability     REAL    NOT NULL,       -- 0.0 – 1.0
    confidence      TEXT    NOT NULL,       -- "HIGH", "MEDIUM", "LOW"
    implied_odds    REAL,                   -- 1.0 / probability
    rationale       TEXT    NOT NULL,       -- Human-readable explanation
    risk_factors    TEXT    DEFAULT '[]',   -- JSON array of risk strings
    supporting_stats TEXT   DEFAULT '{}',   -- JSON dict of key stats
    factor_scores   TEXT    DEFAULT '{}',   -- JSON dict: factor name → score
    created_at      TEXT    DEFAULT (datetime('now')),
    UNIQUE(match_id, market, pick)   -- Broadened: one row per (match, market, pick direction)
);

-- Actual outcomes for accuracy tracking
CREATE TABLE IF NOT EXISTS prediction_results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    prediction_id   INTEGER NOT NULL UNIQUE REFERENCES predictions(id),
    actual_outcome  TEXT    NOT NULL,
    is_correct      INTEGER NOT NULL,       -- 0 or 1
    recorded_at     TEXT    DEFAULT (datetime('now'))
);

-- Calibration weight history
CREATE TABLE IF NOT EXISTS weight_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    factor_name     TEXT    NOT NULL,
    old_weight      REAL    NOT NULL,
    new_weight      REAL    NOT NULL,
    reason          TEXT,
    calibrated_at   TEXT    DEFAULT (datetime('now'))
);

-- API response cache
CREATE TABLE IF NOT EXISTS api_cache (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    cache_key       TEXT    NOT NULL UNIQUE,  -- deterministic hash of url + params
    url             TEXT    NOT NULL,
    source          TEXT    NOT NULL,         -- 'api_football' or 'football_data_org'
    response_data   TEXT    NOT NULL,         -- JSON string
    cached_at       TEXT    DEFAULT (datetime('now')),
    expires_at      TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cache_key ON api_cache(cache_key);
CREATE INDEX IF NOT EXISTS idx_cache_expires ON api_cache(expires_at);

-- Daily API usage tracking
CREATE TABLE IF NOT EXISTS api_usage (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT    NOT NULL,
    date            TEXT    NOT NULL,
    requests_made   INTEGER DEFAULT 0,
    requests_limit  INTEGER,
    UNIQUE(source, date)
);

-- User settings (preference storage)
CREATE TABLE IF NOT EXISTS user_settings (
    key   TEXT PRIMARY KEY,
    value TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- User active/disabled leagues
CREATE TABLE IF NOT EXISTS user_leagues (
    slug          TEXT PRIMARY KEY,
    enabled       INTEGER DEFAULT 1,
    display_order INTEGER DEFAULT 0
);

-- User active/disabled markets and floor
CREATE TABLE IF NOT EXISTS user_markets (
    market_code     TEXT PRIMARY KEY,
    display_name    TEXT,
    enabled         INTEGER DEFAULT 1,
    min_probability REAL DEFAULT 0.55,
    display_order   INTEGER DEFAULT 0
);

-- Saved parlays history
CREATE TABLE IF NOT EXISTS parlays (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL,
    created_at      TEXT    DEFAULT (datetime('now')),
    submitted_at    TEXT,
    size            INTEGER NOT NULL,
    picks           TEXT    NOT NULL,  -- JSON string of items
    status          TEXT    NOT NULL DEFAULT 'draft',
    correct_count   INTEGER,
    notes           TEXT
);
"""


# ═══════════════════════════════════════════════════════════════
#  Database Class
# ═══════════════════════════════════════════════════════════════

class Database:
    """
    SQLite data access layer for Footy Predict.

    Usage:
        db = Database()
        teams = db.get_teams_by_league(league_id=1)
    """

    def __init__(self, db_path: Path | str = DB_PATH):
        self.db_path = Path(db_path)
        self._init_db()

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager yielding a connection with WAL mode and FK enforcement."""
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

    def _init_db(self) -> None:
        """Create all tables and apply pending SQL migrations."""
        with self._connect() as conn:
            conn.executescript(SCHEMA)
        self._apply_migrations()


    def _apply_migrations(self) -> None:
        """Apply any SQL migration files in the migrations/ directory in order."""
        migrations_dir = Path(__file__).parent.parent / "migrations"
        if not migrations_dir.is_dir():
            return
        migration_files = sorted(migrations_dir.glob("*.sql"))
        with self._connect() as conn:
            for migration_path in migration_files:
                sql = migration_path.read_text()
                try:
                    conn.executescript(sql)
                except Exception:
                    # Columns added by ALTER TABLE already exist — safe to ignore
                    pass

    # ── Helpers ─────────────────────────────────────────────

    @staticmethod
    def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
        """Convert a sqlite3.Row to a plain dict, or None."""
        if row is None:
            return None
        return dict(row)

    @staticmethod
    def _rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
        """Convert a list of sqlite3.Rows to a list of dicts."""
        return [dict(r) for r in rows]

    def _now(self) -> str:
        """Current UTC timestamp in ISO format."""
        return datetime.now(timezone.utc).isoformat()

    # ═══════════════════════════════════════════════════════
    #  Leagues
    # ═══════════════════════════════════════════════════════

    def upsert_league(
        self,
        name: str,
        country: str,
        slug: str,
        api_football_id: int | None = None,
        fdo_code: str | None = None,
        csv_code: str | None = None,
        tier: str = "extended",
    ) -> int:
        """Insert or update a league. Returns the league ID."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO leagues (name, country, slug, api_football_id, fdo_code, csv_code, tier)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(slug) DO UPDATE SET
                    name=excluded.name,
                    country=excluded.country,
                    api_football_id=excluded.api_football_id,
                    fdo_code=excluded.fdo_code,
                    csv_code=excluded.csv_code,
                    tier=excluded.tier
                """,
                (name, country, slug, api_football_id, fdo_code, csv_code, tier),
            )
            row = conn.execute(
                "SELECT id FROM leagues WHERE slug = ?", (slug,)
            ).fetchone()
            return row["id"]

    def get_league(self, slug: str) -> dict[str, Any] | None:
        """Get a league by slug."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM leagues WHERE slug = ?", (slug,)
            ).fetchone()
            return self._row_to_dict(row)

    def get_league_by_api_football_id(self, api_id: int) -> dict[str, Any] | None:
        """Get a league by its API-Football ID."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM leagues WHERE api_football_id = ?", (api_id,)
            ).fetchone()
            return self._row_to_dict(row)

    def get_all_leagues(self) -> list[dict[str, Any]]:
        """Get all leagues ordered by tier then name."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM leagues ORDER BY tier, name"
            ).fetchall()
            return self._rows_to_dicts(rows)

    def seed_leagues(self) -> None:
        """Populate the leagues table from config.LEAGUES."""
        from src.config import LEAGUES as LEAGUE_CONFIGS

        for cfg in LEAGUE_CONFIGS.values():
            self.upsert_league(
                name=cfg.name,
                country=cfg.country,
                slug=cfg.slug,
                api_football_id=cfg.api_football_id,
                fdo_code=cfg.football_data_org_code,
                csv_code=cfg.csv_code,
                tier=cfg.tier,
            )

        self.seed_user_defaults()

    # ═══════════════════════════════════════════════════════
    #  Teams
    # ═══════════════════════════════════════════════════════

    def upsert_team(
        self,
        name: str,
        league_id: int,
        api_football_id: int | None = None,
        fdo_id: int | None = None,
        short_name: str | None = None,
        aliases: list[str] | None = None,
        logo_url: str | None = None,
    ) -> int:
        """Insert or update a team. Returns the team ID.

        Identity resolves on the canonical key, not the raw name, so the same
        club arriving as "Man United" (CSV) and "Manchester United FC" (FDO)
        lands on one row instead of two. The longer spelling wins the display
        name and the shorter one is kept as an alias.
        """
        key = canonical_key(name)

        with self._connect() as conn:
            # Source id is the strongest signal when present.
            if api_football_id:
                existing = conn.execute(
                    "SELECT id FROM teams WHERE api_football_id = ?",
                    (api_football_id,),
                ).fetchone()
                if existing:
                    self._merge_team_row(
                        conn, existing["id"], name, key, league_id,
                        fdo_id, short_name, aliases, logo_url,
                    )
                    return existing["id"]

            existing = conn.execute(
                "SELECT id FROM teams WHERE league_id = ? AND canonical_key = ?",
                (league_id, key),
            ).fetchone()
            if existing:
                self._merge_team_row(
                    conn, existing["id"], name, key, league_id,
                    fdo_id, short_name, aliases, logo_url,
                    api_football_id=api_football_id,
                )
                return existing["id"]

            cur = conn.execute(
                """
                INSERT INTO teams (name, api_football_id, fdo_id, league_id,
                                   short_name, aliases, logo_url, canonical_key)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(name, league_id) DO UPDATE SET
                    api_football_id=COALESCE(excluded.api_football_id, teams.api_football_id),
                    fdo_id=COALESCE(excluded.fdo_id, teams.fdo_id),
                    short_name=COALESCE(excluded.short_name, teams.short_name),
                    aliases=excluded.aliases,
                    logo_url=COALESCE(excluded.logo_url, teams.logo_url),
                    canonical_key=excluded.canonical_key
                """,
                (name, api_football_id, fdo_id, league_id,
                 short_name, json.dumps(aliases or []), logo_url, key),
            )
            if cur.lastrowid:
                return cur.lastrowid

            row = conn.execute(
                "SELECT id FROM teams WHERE name = ? AND league_id = ?",
                (name, league_id),
            ).fetchone()
            return row["id"]

    def _merge_team_row(
        self,
        conn: sqlite3.Connection,
        team_id: int,
        name: str,
        key: str,
        league_id: int,
        fdo_id: int | None,
        short_name: str | None,
        aliases: list[str] | None,
        logo_url: str | None,
        api_football_id: int | None = None,
    ) -> None:
        """Fold an incoming spelling into an existing team row.

        The most complete spelling becomes the display name; every other
        observed spelling is retained as an alias so lookups by any source's
        name still resolve.
        """
        row = conn.execute(
            "SELECT name, aliases FROM teams WHERE id = ?", (team_id,)
        ).fetchone()

        try:
            known = set(json.loads(row["aliases"] or "[]"))
        except (json.JSONDecodeError, TypeError):
            known = set()
        known.update(aliases or [])
        known.add(row["name"])
        known.add(name)

        display = max((row["name"], name), key=len)
        known.discard(display)

        conn.execute(
            """
            UPDATE teams SET
                name            = ?,
                canonical_key   = ?,
                league_id       = COALESCE(?, league_id),
                api_football_id = COALESCE(api_football_id, ?),
                fdo_id          = COALESCE(?, fdo_id),
                short_name      = COALESCE(?, short_name),
                logo_url        = COALESCE(?, logo_url),
                aliases         = ?
            WHERE id = ?
            """,
            (display, key, league_id, api_football_id, fdo_id, short_name,
             logo_url, json.dumps(sorted(known)), team_id),
        )

    def get_team(self, team_id: int) -> dict[str, Any] | None:
        """Get a team by internal ID."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM teams WHERE id = ?", (team_id,)
            ).fetchone()
            return self._row_to_dict(row)

    def get_team_by_api_football_id(self, api_id: int) -> dict[str, Any] | None:
        """Get a team by its API-Football ID."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM teams WHERE api_football_id = ?", (api_id,)
            ).fetchone()
            return self._row_to_dict(row)

    def find_team_by_name(
        self, name: str, league_id: int | None = None
    ) -> dict[str, Any] | None:
        """
        Find a team by name or alias. Optionally scoped to a league.
        Useful for matching team names from different data sources.
        """
        with self._connect() as conn:
            # Exact match first
            if league_id:
                row = conn.execute(
                    "SELECT * FROM teams WHERE name = ? AND league_id = ?",
                    (name, league_id),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM teams WHERE name = ?", (name,)
                ).fetchone()

            if row:
                return self._row_to_dict(row)

            # Search aliases
            query = "SELECT * FROM teams WHERE aliases LIKE ?"
            params: list[Any] = [f'%"{name}"%']
            if league_id:
                query += " AND league_id = ?"
                params.append(league_id)

            row = conn.execute(query, params).fetchone()
            return self._row_to_dict(row)

    def get_teams_by_league(self, league_id: int) -> list[dict[str, Any]]:
        """Get all teams in a league."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM teams WHERE league_id = ? ORDER BY name",
                (league_id,),
            ).fetchall()
            return self._rows_to_dicts(rows)

    # ═══════════════════════════════════════════════════════
    #  Matches
    # ═══════════════════════════════════════════════════════

    def upsert_match(
        self,
        league_id: int,
        season: str,
        date: str,
        home_team_id: int,
        away_team_id: int,
        api_football_id: int | None = None,
        matchday: int | None = None,
        status: str = "SCHEDULED",
        home_score: int | None = None,
        away_score: int | None = None,
        ht_home_score: int | None = None,
        ht_away_score: int | None = None,
        referee: str | None = None,
        venue: str | None = None,
        source: str = "api_football",
    ) -> int:
        """Insert or update a match. Returns the match ID.

        Identity resolves on two keys. A row carrying a matchday is identified
        by (league, season, both teams, matchday); everything else by (league,
        season, both teams, calendar date).

        Both are needed. The date key alone splits a fixture whose kickoff gets
        revised — football-data.org publishes provisionally, then moves it a day
        — which put 87 duplicate 2026/27 fixtures in the database. The matchday
        key alone cannot cover CSV loads, which carry no matchday at all. The
        date key also still catches a matchday-bearing row meeting a
        matchday-less one on the same date.

        The original ON CONFLICT(api_football_id) clause fired for neither:
        that column is nullable and SQLite treats every NULL as distinct, so
        each re-fetch inserted a duplicate.

        A late score or status only ever overwrites a NULL: a FINISHED result
        already on the row is never reverted by a later SCHEDULED fetch.
        """
        with self._connect() as conn:
            row = conn.execute(
                """
                INSERT INTO matches (
                    api_football_id, league_id, season, matchday, date, status,
                    home_team_id, away_team_id, home_score, away_score,
                    ht_home_score, ht_away_score, referee, venue, source, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(league_id, season, home_team_id, away_team_id, matchday)
                    WHERE matchday IS NOT NULL
                DO UPDATE SET
                    api_football_id=COALESCE(matches.api_football_id, excluded.api_football_id),
                    status=CASE
                        WHEN matches.status = 'FINISHED' AND excluded.status != 'FINISHED'
                            THEN matches.status
                        ELSE excluded.status
                    END,
                    -- A revised kickoff is the point of this key, so the new
                    -- date wins — unless it is the CSV loader's midnight
                    -- placeholder, which is not a reschedule.
                    date=CASE
                        WHEN excluded.date LIKE '%T00:00:00%' THEN matches.date
                        ELSE excluded.date
                    END,
                    home_score=COALESCE(excluded.home_score, matches.home_score),
                    away_score=COALESCE(excluded.away_score, matches.away_score),
                    ht_home_score=COALESCE(excluded.ht_home_score, matches.ht_home_score),
                    ht_away_score=COALESCE(excluded.ht_away_score, matches.ht_away_score),
                    referee=COALESCE(excluded.referee, matches.referee),
                    venue=COALESCE(excluded.venue, matches.venue),
                    updated_at=datetime('now')
                ON CONFLICT(league_id, season, home_team_id, away_team_id, date(date))
                DO UPDATE SET
                    api_football_id=COALESCE(matches.api_football_id, excluded.api_football_id),
                    -- A stale SCHEDULED row from a re-fetch must not un-finish
                    -- a match whose result is already recorded.
                    status=CASE
                        WHEN matches.status = 'FINISHED' AND excluded.status != 'FINISHED'
                            THEN matches.status
                        ELSE excluded.status
                    END,
                    -- Prefer the more precise timestamp: the CSV loader stores
                    -- midnight, so it must not clobber a real kickoff time.
                    date=CASE
                        WHEN excluded.date LIKE '%T00:00:00%' THEN matches.date
                        ELSE excluded.date
                    END,
                    home_score=COALESCE(excluded.home_score, matches.home_score),
                    away_score=COALESCE(excluded.away_score, matches.away_score),
                    ht_home_score=COALESCE(excluded.ht_home_score, matches.ht_home_score),
                    ht_away_score=COALESCE(excluded.ht_away_score, matches.ht_away_score),
                    referee=COALESCE(excluded.referee, matches.referee),
                    venue=COALESCE(excluded.venue, matches.venue),
                    matchday=COALESCE(excluded.matchday, matches.matchday),
                    updated_at=datetime('now')
                RETURNING id
                """,
                (api_football_id, league_id, season, matchday, date, status,
                 home_team_id, away_team_id, home_score, away_score,
                 ht_home_score, ht_away_score, referee, venue, source),
            ).fetchone()
            # RETURNING rather than a follow-up SELECT: once a reschedule can
            # move the stored date, looking the row back up by date finds
            # nothing whenever the incoming date lost the CASE above.
            return row["id"]

    def get_upcoming_matches(
        self,
        league_id: int | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """
        Get upcoming (SCHEDULED/TIMED) matches, optionally filtered.

        If no date range given, defaults to next 14 days.

        The limit is a runaway guard, not a page size — a busy 14-day window
        across all 12 leagues is ~250 fixtures, so it must sit well clear of
        realistic volumes or matchweek analysis silently loses fixtures.
        """
        if from_date is None:
            from_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if to_date is None:
            to_date = (
                datetime.now(timezone.utc) + timedelta(days=14)
            ).strftime("%Y-%m-%d")

        with self._connect() as conn:
            query = """
                SELECT m.*, ht.name AS home_team_name, at.name AS away_team_name,
                       l.name AS league_name, l.slug AS league_slug
                FROM matches m
                JOIN teams ht ON m.home_team_id = ht.id
                JOIN teams at ON m.away_team_id = at.id
                JOIN leagues l ON m.league_id = l.id
                WHERE m.status IN ('SCHEDULED', 'TIMED')
                  AND m.date >= ? AND m.date <= ?
            """
            params: list[Any] = [from_date, to_date + "T23:59:59"]

            if league_id:
                query += " AND m.league_id = ?"
                params.append(league_id)

            query += " ORDER BY m.date, l.name LIMIT ?"
            params.append(limit)

            rows = conn.execute(query, params).fetchall()
            return self._rows_to_dicts(rows)

    def get_recent_matches(
        self,
        team_id: int,
        limit: int = 10,
        home_only: bool = False,
        away_only: bool = False,
    ) -> list[dict[str, Any]]:
        """Get a team's most recent completed matches."""
        with self._connect() as conn:
            query = """
                SELECT m.*, ht.name AS home_team_name, at.name AS away_team_name
                FROM matches m
                JOIN teams ht ON m.home_team_id = ht.id
                JOIN teams at ON m.away_team_id = at.id
                WHERE m.status = 'FINISHED'
            """
            params: list[Any] = []

            if home_only:
                query += " AND m.home_team_id = ?"
                params.append(team_id)
            elif away_only:
                query += " AND m.away_team_id = ?"
                params.append(team_id)
            else:
                query += " AND (m.home_team_id = ? OR m.away_team_id = ?)"
                params.extend([team_id, team_id])

            query += " ORDER BY m.date DESC LIMIT ?"
            params.append(limit)

            rows = conn.execute(query, params).fetchall()
            return self._rows_to_dicts(rows)

    def get_match(self, match_id: int) -> dict[str, Any] | None:
        """Get a single match with team and league names."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT m.*, ht.name AS home_team_name, at.name AS away_team_name,
                       l.name AS league_name, l.slug AS league_slug
                FROM matches m
                JOIN teams ht ON m.home_team_id = ht.id
                JOIN teams at ON m.away_team_id = at.id
                JOIN leagues l ON m.league_id = l.id
                WHERE m.id = ?
                """,
                (match_id,),
            ).fetchone()
            return self._row_to_dict(row)

    def get_finished_matches_without_results(self) -> list[dict[str, Any]]:
        """Get matches that are FINISHED but have no prediction results recorded."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT m.*, ht.name AS home_team_name,
                       at.name AS away_team_name, l.name AS league_name
                FROM matches m
                JOIN teams ht ON m.home_team_id = ht.id
                JOIN teams at ON m.away_team_id = at.id
                JOIN leagues l ON m.league_id = l.id
                JOIN predictions p ON p.match_id = m.id
                LEFT JOIN prediction_results pr ON pr.prediction_id = p.id
                WHERE m.status = 'FINISHED'
                  AND pr.id IS NULL
                ORDER BY m.date
                """
            ).fetchall()
            return self._rows_to_dicts(rows)

    # ═══════════════════════════════════════════════════════
    #  Match Stats
    # ═══════════════════════════════════════════════════════

    def upsert_match_stats(
        self,
        match_id: int,
        team_id: int,
        **stats: Any,
    ) -> None:
        """Insert or update match statistics for a team."""
        valid_cols = {
            "shots", "shots_on_target", "possession", "corners", "fouls",
            "yellow_cards", "red_cards", "offsides", "passes",
            "pass_accuracy", "expected_goals",
        }
        filtered = {k: v for k, v in stats.items() if k in valid_cols}
        if not filtered:
            return

        cols = ", ".join(filtered.keys())
        placeholders = ", ".join(["?"] * len(filtered))
        updates = ", ".join(f"{k}=excluded.{k}" for k in filtered)

        with self._connect() as conn:
            conn.execute(
                f"""
                INSERT INTO match_stats (match_id, team_id, {cols})
                VALUES (?, ?, {placeholders})
                ON CONFLICT(match_id, team_id) DO UPDATE SET {updates}
                """,
                (match_id, team_id, *filtered.values()),
            )

    def get_match_stats(self, match_id: int) -> list[dict[str, Any]]:
        """Get stats for both teams in a match."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT ms.*, t.name AS team_name
                   FROM match_stats ms
                   JOIN teams t ON ms.team_id = t.id
                   WHERE ms.match_id = ?""",
                (match_id,),
            ).fetchall()
            return self._rows_to_dicts(rows)

    # ═══════════════════════════════════════════════════════
    #  Team Season Stats
    # ═══════════════════════════════════════════════════════

    def upsert_team_season_stats(
        self,
        team_id: int,
        league_id: int,
        season: str,
        **stats: Any,
    ) -> None:
        """Insert or update seasonal team statistics."""
        valid_cols = {
            "matches_played", "wins", "draws", "losses",
            "goals_scored", "goals_conceded",
            "home_played", "home_wins", "home_draws", "home_losses",
            "home_goals_scored", "home_goals_conceded",
            "away_played", "away_wins", "away_draws", "away_losses",
            "away_goals_scored", "away_goals_conceded",
            "form", "clean_sheets", "failed_to_score",
            "avg_goals_scored", "avg_goals_conceded",
            "btts_percentage", "over_2_5_percentage", "under_2_5_percentage",
            "penalty_scored", "penalty_missed",
        }
        filtered = {k: v for k, v in stats.items() if k in valid_cols}
        filtered["fetched_at"] = self._now()

        cols = ", ".join(filtered.keys())
        placeholders = ", ".join(["?"] * len(filtered))
        updates = ", ".join(f"{k}=excluded.{k}" for k in filtered)

        with self._connect() as conn:
            conn.execute(
                f"""
                INSERT INTO team_season_stats (team_id, league_id, season, {cols})
                VALUES (?, ?, ?, {placeholders})
                ON CONFLICT(team_id, league_id, season) DO UPDATE SET {updates}
                """,
                (team_id, league_id, season, *filtered.values()),
            )

    def get_team_season_stats(
        self, team_id: int, league_id: int, season: str
    ) -> dict[str, Any] | None:
        """Get cached seasonal stats for a team."""
        with self._connect() as conn:
            row = conn.execute(
                """SELECT * FROM team_season_stats
                   WHERE team_id=? AND league_id=? AND season=?""",
                (team_id, league_id, season),
            ).fetchone()
            return self._row_to_dict(row)

    # ═══════════════════════════════════════════════════════
    #  Head-to-Head
    # ═══════════════════════════════════════════════════════

    def upsert_h2h(
        self,
        team_a_id: int,
        team_b_id: int,
        total_matches: int,
        team_a_wins: int,
        draws: int,
        team_b_wins: int,
        team_a_goals: int,
        team_b_goals: int,
        last_matches: list[dict] | None = None,
    ) -> None:
        """Insert or update head-to-head record. team_a is always the lower ID."""
        # Normalize order so (A, B) and (B, A) map to the same row
        if team_a_id > team_b_id:
            team_a_id, team_b_id = team_b_id, team_a_id
            team_a_wins, team_b_wins = team_b_wins, team_a_wins
            team_a_goals, team_b_goals = team_b_goals, team_a_goals

        last_json = json.dumps(last_matches or [])

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO head_to_head
                    (team_a_id, team_b_id, total_matches,
                     team_a_wins, draws, team_b_wins,
                     team_a_goals, team_b_goals, last_matches, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(team_a_id, team_b_id) DO UPDATE SET
                    total_matches=excluded.total_matches,
                    team_a_wins=excluded.team_a_wins,
                    draws=excluded.draws,
                    team_b_wins=excluded.team_b_wins,
                    team_a_goals=excluded.team_a_goals,
                    team_b_goals=excluded.team_b_goals,
                    last_matches=excluded.last_matches,
                    fetched_at=excluded.fetched_at
                """,
                (team_a_id, team_b_id, total_matches,
                 team_a_wins, draws, team_b_wins,
                 team_a_goals, team_b_goals, last_json),
            )

    def get_h2h(
        self, team_a_id: int, team_b_id: int
    ) -> dict[str, Any] | None:
        """Get H2H record between two teams (order-independent)."""
        a, b = min(team_a_id, team_b_id), max(team_a_id, team_b_id)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM head_to_head WHERE team_a_id=? AND team_b_id=?",
                (a, b),
            ).fetchone()
            result = self._row_to_dict(row)
            if result and result.get("last_matches"):
                result["last_matches"] = json.loads(result["last_matches"])
            return result

    # ═══════════════════════════════════════════════════════
    #  Standings
    # ═══════════════════════════════════════════════════════

    def upsert_standing(
        self,
        league_id: int,
        season: str,
        team_id: int,
        position: int,
        played: int = 0,
        won: int = 0,
        drawn: int = 0,
        lost: int = 0,
        goals_for: int = 0,
        goals_against: int = 0,
        goal_difference: int = 0,
        points: int = 0,
        form: str | None = None,
        # Home split (populated from API-Football standings response)
        home_played: int = 0,
        home_won: int = 0,
        home_drawn: int = 0,
        home_lost: int = 0,
        home_gf: int = 0,
        home_ga: int = 0,
        # Away split (populated from API-Football standings response)
        away_played: int = 0,
        away_won: int = 0,
        away_drawn: int = 0,
        away_lost: int = 0,
        away_gf: int = 0,
        away_ga: int = 0,
    ) -> None:
        """Insert or update a team's league standing.

        Home/away split kwargs are optional — they default to 0 for sources
        that do not provide venue-specific standings (e.g. football-data.org).
        They are populated from the API-Football standings response which
        includes ``entry["home"]`` and ``entry["away"]`` sub-dicts.

        The conflict clause refuses a downgrade: an incoming row with
        ``played=0`` leaves a stored row that has real data untouched. Before
        this guard existed the overwrite was unconditional and a blank
        pre-season table destroyed 53 complete rows — see ``HANDOFF.md`` for
        the evidence. A caller writing a partial table is therefore safe, but
        the write is also silent, so it cannot be used to zero a row
        deliberately.
        """
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO standings
                    (league_id, season, team_id, position, played, won, drawn,
                     lost, goals_for, goals_against, goal_difference, points,
                     form, fetched_at,
                     home_played, home_won, home_drawn, home_lost, home_gf, home_ga,
                     away_played, away_won, away_drawn, away_lost, away_gf, away_ga)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'),
                        ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?)
                ON CONFLICT(league_id, season, team_id) DO UPDATE SET
                    position=excluded.position,
                    played=excluded.played,
                    won=excluded.won,
                    drawn=excluded.drawn,
                    lost=excluded.lost,
                    goals_for=excluded.goals_for,
                    goals_against=excluded.goals_against,
                    goal_difference=excluded.goal_difference,
                    points=excluded.points,
                    form=excluded.form,
                    fetched_at=excluded.fetched_at,
                    home_played=excluded.home_played,
                    home_won=excluded.home_won,
                    home_drawn=excluded.home_drawn,
                    home_lost=excluded.home_lost,
                    home_gf=excluded.home_gf,
                    home_ga=excluded.home_ga,
                    away_played=excluded.away_played,
                    away_won=excluded.away_won,
                    away_drawn=excluded.away_drawn,
                    away_lost=excluded.away_lost,
                    away_gf=excluded.away_gf,
                    away_ga=excluded.away_ga
                WHERE excluded.played > 0 OR standings.played = 0
                """,
                (league_id, season, team_id, position, played, won, drawn,
                 lost, goals_for, goals_against, goal_difference, points, form,
                 home_played, home_won, home_drawn, home_lost, home_gf, home_ga,
                 away_played, away_won, away_drawn, away_lost, away_gf, away_ga),
            )

    def get_standings(
        self, league_id: int, season: str
    ) -> list[dict[str, Any]]:
        """Get league table ordered by position."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT s.*, t.name AS team_name
                   FROM standings s
                   JOIN teams t ON s.team_id = t.id
                   WHERE s.league_id=? AND s.season=?
                   ORDER BY s.position""",
                (league_id, season),
            ).fetchall()
            return self._rows_to_dicts(rows)

    def get_matches_without_stats(self, league_id: int, limit: int = 20) -> list[dict[str, Any]]:
        """Return FINISHED matches that have no match_stats rows yet, newest first.

        Used by ``fetch_and_store_match_stats`` to identify which fixtures need
        their per-team statistics backfilled from ``/fixtures/statistics``.

        Args:
            league_id: Internal league DB ID to filter by.
            limit:     Maximum number of matches to return per call. Keep this
                       conservative (default 20) to avoid burning daily API quota
                       in a single run.

        Returns:
            List of dicts with keys: ``id``, ``api_football_id``,
            ``home_team_id``, ``away_team_id``.
        """
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT m.id, m.api_football_id, m.home_team_id, m.away_team_id
                FROM matches m
                WHERE m.league_id = ?
                  AND m.status = 'FINISHED'
                  AND m.api_football_id IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM match_stats ms WHERE ms.match_id = m.id
                  )
                ORDER BY m.date DESC
                LIMIT ?
                """,
                (league_id, limit),
            ).fetchall()
            return self._rows_to_dicts(rows)


    # ═══════════════════════════════════════════════════════
    #  Predictions
    # ═══════════════════════════════════════════════════════

    def insert_prediction(
        self,
        match_id: int,
        market: str,
        pick: str,
        probability: float,
        confidence: str,
        rationale: str,
        implied_odds: float | None = None,
        risk_factors: list[str] | None = None,
        supporting_stats: dict | None = None,
        factor_scores: dict | None = None,
    ) -> int:
        """Record a prediction for a (match, market, pick) combination.

        The unique constraint is now UNIQUE(match_id, market, pick), which
        allows multiple picks per market per match (e.g. all three 1X2
        directions, or both Over and Under for each O/U line).

        Args:
            match_id: Database ID of the match.
            market: Market code, e.g. ``"over-under-2.5"``.
            pick: The specific pick direction, e.g. ``"Over 2.5"``.
            probability: Estimated probability in [0, 1].
            confidence: Classification string — ``"HIGH"``, ``"MEDIUM"``, or ``"LOW"``.
            rationale: Human-readable explanation of the recommendation.
            implied_odds: Decimal odds implied by probability (auto-computed if omitted).
            risk_factors: Optional list of risk flag strings.
            supporting_stats: Optional dict of key supporting statistics.
            factor_scores: Optional dict mapping factor names to their scores.

        Returns:
            The database ``id`` of the inserted or updated prediction row.
        """
        if implied_odds is None and probability > 0:
            implied_odds = round(1.0 / probability, 2)

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO predictions
                    (match_id, market, pick, probability, confidence,
                     implied_odds, rationale, risk_factors,
                     supporting_stats, factor_scores)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(match_id, market, pick) DO UPDATE SET
                    probability=excluded.probability,
                    confidence=excluded.confidence,
                    implied_odds=excluded.implied_odds,
                    rationale=excluded.rationale,
                    risk_factors=excluded.risk_factors,
                    supporting_stats=excluded.supporting_stats,
                    factor_scores=excluded.factor_scores,
                    created_at=datetime('now')
                """,
                (
                    match_id, market, pick, probability, confidence,
                    implied_odds,
                    rationale,
                    json.dumps(risk_factors or []),
                    json.dumps(supporting_stats or {}),
                    json.dumps(factor_scores or {}),
                ),
            )
            row = conn.execute(
                "SELECT id FROM predictions WHERE match_id=? AND market=? AND pick=?",
                (match_id, market, pick),
            ).fetchone()
            return row["id"]

    def get_team_xg_history(
        self, team_id: int, limit: int = 10
    ) -> list[dict[str, Any]]:
        """Return recent match xG statistics for a team from match_stats.

        Joins the ``matches`` table with ``match_stats`` to retrieve expected
        goals (xG) for each match where data is available.  Results are
        ordered most-recent first.

        Args:
            team_id: Database ID of the team.
            limit: Maximum number of matches to return.

        Returns:
            List of dicts with keys: ``id``, ``date``, ``home_team_id``,
            ``away_team_id``, ``home_score``, ``away_score``,
            ``expected_goals``.
        """
        sql = """
            SELECT m.id,
                   m.date,
                   m.home_team_id,
                   m.away_team_id,
                   m.home_score,
                   m.away_score,
                   ms.expected_goals
              FROM matches m
              JOIN match_stats ms
                ON ms.match_id = m.id
               AND ms.team_id = ?
             WHERE m.status = 'FINISHED'
               AND ms.expected_goals IS NOT NULL
             ORDER BY m.date DESC
             LIMIT ?
        """
        with self._connect() as conn:
            rows = conn.execute(sql, (team_id, limit)).fetchall()
            return [dict(r) for r in rows]


    def get_predictions_for_match(
        self, match_id: int
    ) -> list[dict[str, Any]]:
        """Get all predictions for a match."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM predictions WHERE match_id=? ORDER BY probability DESC",
                (match_id,),
            ).fetchall()
            results = self._rows_to_dicts(rows)
            for r in results:
                r["risk_factors"] = json.loads(r.get("risk_factors", "[]"))
                r["supporting_stats"] = json.loads(r.get("supporting_stats", "{}"))
                r["factor_scores"] = json.loads(r.get("factor_scores", "{}"))
            return results

    def get_high_confidence_predictions(
        self,
        min_confidence: str = "HIGH",
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get predictions at or above a confidence level, with match info."""
        confidence_levels = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}
        min_level = confidence_levels.get(min_confidence, 1)
        valid_confidences = [
            k for k, v in confidence_levels.items() if v >= min_level
        ]
        placeholders = ", ".join(["?"] * len(valid_confidences))

        with self._connect() as conn:
            query = f"""
                SELECT p.*, m.date, m.status,
                       ht.name AS home_team_name, at.name AS away_team_name,
                       l.name AS league_name
                FROM predictions p
                JOIN matches m ON p.match_id = m.id
                JOIN teams ht ON m.home_team_id = ht.id
                JOIN teams at ON m.away_team_id = at.id
                JOIN leagues l ON m.league_id = l.id
                WHERE p.confidence IN ({placeholders})
            """
            params: list[Any] = list(valid_confidences)

            if from_date:
                query += " AND m.date >= ?"
                params.append(from_date)
            if to_date:
                query += " AND m.date <= ?"
                params.append(to_date + "T23:59:59")

            query += " ORDER BY p.probability DESC"
            rows = conn.execute(query, params).fetchall()
            return self._rows_to_dicts(rows)

    # ═══════════════════════════════════════════════════════
    #  Prediction Results & Accuracy
    # ═══════════════════════════════════════════════════════

    def record_result(
        self, prediction_id: int, actual_outcome: str, is_correct: bool
    ) -> None:
        """Record the actual outcome for a prediction."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO prediction_results (prediction_id, actual_outcome, is_correct)
                VALUES (?, ?, ?)
                ON CONFLICT(prediction_id) DO UPDATE SET
                    actual_outcome=excluded.actual_outcome,
                    is_correct=excluded.is_correct,
                    recorded_at=datetime('now')
                """,
                (prediction_id, actual_outcome, int(is_correct)),
            )

    def get_accuracy_stats(
        self,
        market: str | None = None,
        league_slug: str | None = None,
        confidence: str | None = None,
    ) -> dict[str, Any]:
        """
        Calculate accuracy statistics with optional filters.

        Returns dict with: total, correct, incorrect, accuracy,
        by_confidence, by_market breakdowns.
        """
        with self._connect() as conn:
            base_query = """
                FROM prediction_results pr
                JOIN predictions p ON pr.prediction_id = p.id
                JOIN matches m ON p.match_id = m.id
                JOIN leagues l ON m.league_id = l.id
            """
            conditions: list[str] = []
            params: list[Any] = []

            if market:
                conditions.append("p.market = ?")
                params.append(market)
            if league_slug:
                conditions.append("l.slug = ?")
                params.append(league_slug)
            if confidence:
                conditions.append("p.confidence = ?")
                params.append(confidence)

            where = " WHERE " + " AND ".join(conditions) if conditions else ""

            # Overall stats
            row = conn.execute(
                f"""SELECT
                        COUNT(*) AS total,
                        SUM(pr.is_correct) AS correct,
                        SUM(1 - pr.is_correct) AS incorrect,
                        ROUND(AVG(pr.is_correct) * 100, 1) AS accuracy_pct
                    {base_query}{where}""",
                params,
            ).fetchone()

            stats = dict(row) if row else {}

            # By confidence breakdown
            rows = conn.execute(
                f"""SELECT p.confidence,
                        COUNT(*) AS total,
                        SUM(pr.is_correct) AS correct,
                        ROUND(AVG(pr.is_correct) * 100, 1) AS accuracy_pct
                    {base_query}{where}
                    GROUP BY p.confidence
                    ORDER BY accuracy_pct DESC""",
                params,
            ).fetchall()
            stats["by_confidence"] = self._rows_to_dicts(rows)

            # By market breakdown
            rows = conn.execute(
                f"""SELECT p.market,
                        COUNT(*) AS total,
                        SUM(pr.is_correct) AS correct,
                        ROUND(AVG(pr.is_correct) * 100, 1) AS accuracy_pct
                    {base_query}{where}
                    GROUP BY p.market
                    ORDER BY accuracy_pct DESC""",
                params,
            ).fetchall()
            stats["by_market"] = self._rows_to_dicts(rows)

            return stats

    def get_total_predictions(self) -> int:
        """Count total recorded predictions (with results)."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM prediction_results"
            ).fetchone()
            return row["cnt"] if row else 0

    # ═══════════════════════════════════════════════════════
    #  Weight History (Calibration)
    # ═══════════════════════════════════════════════════════

    def record_weight_change(
        self,
        factor_name: str,
        old_weight: float,
        new_weight: float,
        reason: str,
    ) -> None:
        """Log a calibration weight adjustment."""
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO weight_history
                   (factor_name, old_weight, new_weight, reason)
                   VALUES (?, ?, ?, ?)""",
                (factor_name, old_weight, new_weight, reason),
            )

    def get_weight_history(
        self, factor_name: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Get calibration history, optionally for a specific factor."""
        with self._connect() as conn:
            if factor_name:
                rows = conn.execute(
                    """SELECT * FROM weight_history
                       WHERE factor_name=?
                       ORDER BY calibrated_at DESC LIMIT ?""",
                    (factor_name, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM weight_history
                       ORDER BY calibrated_at DESC LIMIT ?""",
                    (limit,),
                ).fetchall()
            return self._rows_to_dicts(rows)

    # ═══════════════════════════════════════════════════════
    #  API Cache
    # ═══════════════════════════════════════════════════════

    def cache_response(
        self,
        cache_key: str,
        url: str,
        source: str,
        response_data: dict | list,
        ttl_seconds: int,
    ) -> None:
        """Store an API response in the cache."""
        expires = (
            datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
        ).isoformat()

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO api_cache (cache_key, url, source, response_data, expires_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    response_data=excluded.response_data,
                    cached_at=datetime('now'),
                    expires_at=excluded.expires_at
                """,
                (cache_key, url, source, json.dumps(response_data), expires),
            )

    def get_cached_response(self, cache_key: str) -> dict | list | None:
        """Retrieve a cached response if it hasn't expired."""
        with self._connect() as conn:
            row = conn.execute(
                """SELECT response_data FROM api_cache
                   WHERE cache_key=? AND expires_at > datetime('now')""",
                (cache_key,),
            ).fetchone()
            if row:
                return json.loads(row["response_data"])
            return None

    def clear_expired_cache(self) -> int:
        """Remove expired cache entries. Returns count of deleted rows."""
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM api_cache WHERE expires_at <= datetime('now')"
            )
            return cursor.rowcount

    # ═══════════════════════════════════════════════════════
    #  API Usage Tracking
    # ═══════════════════════════════════════════════════════

    def increment_api_usage(self, source: str, limit: int | None = None) -> int:
        """
        Increment today's API usage counter for a source.
        Returns the new count.
        """
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO api_usage (source, date, requests_made, requests_limit)
                VALUES (?, ?, 1, ?)
                ON CONFLICT(source, date) DO UPDATE SET
                    requests_made = api_usage.requests_made + 1
                """,
                (source, today, limit),
            )
            row = conn.execute(
                "SELECT requests_made FROM api_usage WHERE source=? AND date=?",
                (source, today),
            ).fetchone()
            return row["requests_made"]

    def get_api_usage_today(self, source: str) -> dict[str, Any]:
        """Get today's API usage for a source."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM api_usage WHERE source=? AND date=?",
                (source, today),
            ).fetchone()
            if row:
                return dict(row)
            return {
                "source": source,
                "date": today,
                "requests_made": 0,
                "requests_limit": None,
            }

    def get_remaining_quota(self, source: str, daily_limit: int) -> int:
        """Get remaining API calls for today."""
        usage = self.get_api_usage_today(source)
        return max(0, daily_limit - usage["requests_made"])

    # ═══════════════════════════════════════════════════════
    #  User Preferences & Settings
    # ═══════════════════════════════════════════════════════

    def get_setting(self, key: str) -> str | None:
        """Retrieve a key's value from user_settings."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM user_settings WHERE key=?",
                (key,)
            ).fetchone()
            return row["value"] if row else None

    def get_all_settings(self) -> dict[str, str]:
        """Retrieve all settings from user_settings as a dictionary."""
        with self._connect() as conn:
            rows = conn.execute("SELECT key, value FROM user_settings").fetchall()
            return {row["key"]: row["value"] for row in rows}

    def set_setting(self, key: str, value: str) -> None:
        """Insert or update a key-value pair in user_settings."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO user_settings (key, value, updated_at)
                VALUES (?, ?, datetime('now'))
                ON CONFLICT(key) DO UPDATE SET
                    value=excluded.value,
                    updated_at=datetime('now')
                """,
                (key, str(value))
            )

    def get_enabled_leagues(self) -> list[str]:
        """Return the slugs of all enabled user leagues."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT slug FROM user_leagues WHERE enabled = 1 ORDER BY display_order ASC, slug ASC"
            ).fetchall()
            return [r["slug"] for r in rows]

    def get_league_preferences(self) -> list[dict[str, Any]]:
        """Get all user leagues with their enabled and order preferences."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT l.name, l.country, l.slug, 
                       COALESCE(ul.enabled, 1) AS enabled, 
                       COALESCE(ul.display_order, 0) AS display_order
                FROM leagues l
                LEFT JOIN user_leagues ul ON l.slug = ul.slug
                ORDER BY display_order ASC, name ASC
                """
            ).fetchall()
            return self._rows_to_dicts(rows)

    def set_league_preference(self, slug: str, enabled: int, display_order: int) -> None:
        """Save a user's active status and sorting order for a league."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO user_leagues (slug, enabled, display_order)
                VALUES (?, ?, ?)
                ON CONFLICT(slug) DO UPDATE SET
                    enabled=excluded.enabled,
                    display_order=excluded.display_order
                """,
                (slug, enabled, display_order)
            )

    def get_enabled_markets(self) -> list[dict[str, Any]]:
        """Return all enabled user markets with their code and floor."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT market_code, min_probability FROM user_markets WHERE enabled = 1 ORDER BY display_order ASC"
            ).fetchall()
            return self._rows_to_dicts(rows)

    def get_market_preferences(self) -> list[dict[str, Any]]:
        """Get all user markets with active states and sliders."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM user_markets ORDER BY display_order ASC, market_code ASC"
            ).fetchall()
            return self._rows_to_dicts(rows)

    def set_market_preference(self, market_code: str, enabled: int, min_probability: float, display_order: int) -> None:
        """Save active states, custom name, or confidence floors for a market."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO user_markets (market_code, enabled, min_probability, display_order)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(market_code) DO UPDATE SET
                    enabled=excluded.enabled,
                    min_probability=excluded.min_probability,
                    display_order=excluded.display_order
                """,
                (market_code, enabled, min_probability, display_order)
            )

    def seed_user_defaults(self) -> None:
        """Seed default user settings, leagues, and markets preferences."""
        from src.config import LEAGUES as LEAGUE_CONFIGS, MARKETS as MARKET_CONFIGS

        # Seed onboarding defaults
        if self.get_setting("onboarding_complete") is None:
            self.set_setting("onboarding_complete", "0")
        if self.get_setting("theme") is None:
            self.set_setting("theme", "dark")
        if self.get_setting("fetch_strategy") is None:
            self.set_setting("fetch_strategy", "full")

        # Seed API key defaults from environment if present
        import os
        if self.get_setting("api_football_key") is None:
            self.set_setting("api_football_key", os.getenv("API_FOOTBALL_KEY", ""))
        if self.get_setting("football_data_org_key") is None:
            self.set_setting("football_data_org_key", os.getenv("FOOTBALL_DATA_ORG_KEY", ""))

        # Seed leagues as enabled by default
        for i, slug in enumerate(LEAGUE_CONFIGS.keys()):
            with self._connect() as conn:
                exists = conn.execute("SELECT 1 FROM user_leagues WHERE slug=?", (slug,)).fetchone()
                if not exists:
                    conn.execute(
                        "INSERT INTO user_leagues (slug, enabled, display_order) VALUES (?, 1, ?)",
                        (slug, i)
                    )

        # Seed markets as enabled by default with 0.55 threshold
        for i, (market_code, market_def) in enumerate(MARKET_CONFIGS.items()):
            with self._connect() as conn:
                exists = conn.execute("SELECT 1 FROM user_markets WHERE market_code=?", (market_code,)).fetchone()
                if not exists:
                    conn.execute(
                        """
                        INSERT INTO user_markets (market_code, display_name, enabled, min_probability, display_order)
                        VALUES (?, ?, 1, 0.55, ?)
                        """,
                        (market_code, market_def.name, i)
                    )

    # ═══════════════════════════════════════════════════════
    #  Parlays History CRUD
    # ═══════════════════════════════════════════════════════

    def insert_parlay(self, name: str, size: int, picks: list[dict[str, Any]], status: str = 'draft', notes: str | None = None) -> int:
        """Insert a new parlay slip into the database."""
        picks_json = json.dumps(picks)
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO parlays (name, size, picks, status, notes, created_at)
                VALUES (?, ?, ?, ?, ?, datetime('now'))
                """,
                (name, size, picks_json, status, notes)
            )
            return cursor.lastrowid

    def update_parlay_status(self, parlay_id: int, status: str, correct_count: int | None = None) -> None:
        """Update a parlay slip status (e.g. submitted, won, lost) and correct picks count."""
        with self._connect() as conn:
            if status == "submitted":
                conn.execute(
                    "UPDATE parlays SET status=?, submitted_at=datetime('now') WHERE id=?",
                    (status, parlay_id)
                )
            else:
                conn.execute(
                    "UPDATE parlays SET status=?, correct_count=? WHERE id=?",
                    (status, correct_count, parlay_id)
                )

    def get_parlay(self, parlay_id: int) -> dict[str, Any] | None:
        """Retrieve a parlay slip by its ID."""
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM parlays WHERE id=?", (parlay_id,)).fetchone()
            if row:
                res = dict(row)
                res["picks"] = json.loads(res["picks"])
                return res
            return None

    def get_all_parlays(self) -> list[dict[str, Any]]:
        """Retrieve all parlay slips ordered by created_at desc."""
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM parlays ORDER BY created_at DESC").fetchall()
            res_list = []
            for r in rows:
                d = dict(r)
                d["picks"] = json.loads(d["picks"])
                res_list.append(d)
            return res_list

    def delete_parlay(self, parlay_id: int) -> None:
        """Remove a parlay slip from the database."""
        with self._connect() as conn:
            conn.execute("DELETE FROM parlays WHERE id=?", (parlay_id,))


