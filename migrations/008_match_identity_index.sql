-- Migration 008: Add unique index on match identity key
--
-- upsert_match deduped with ON CONFLICT(api_football_id). That column is UNIQUE
-- but nullable, and SQLite treats every NULL as distinct, so CSV and
-- football-data.org rows never fired the conflict clause and every re-fetch
-- inserted a fresh copy.
--
-- Migration 007 collapsed the existing duplicates; this index makes a repeat
-- structurally impossible. The key includes the calendar date (not the full
-- timestamp) because split-format leagues — Scottish Premiership, Belgian Pro
-- League — legitimately repeat a home/away pairing within one season, so keying
-- on the pairing alone would reject real fixtures. Only the time differs when
-- CSV's midnight placeholder meets football-data.org's true kickoff.

CREATE UNIQUE INDEX IF NOT EXISTS idx_matches_identity
    ON matches(league_id, season, home_team_id, away_team_id, date(date));
