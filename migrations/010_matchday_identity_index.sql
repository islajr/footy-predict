-- Migration 010: Fixture identity for rows that carry a matchday
--
-- 008 keyed fixture identity on the calendar date. A date is not stable:
-- football-data.org publishes a provisional kickoff and revises it later, and
-- the revised row lands under a different key, so the upsert inserts a second
-- copy instead of updating the first. That produced 87 duplicate 2026/27
-- fixtures across six leagues, collapsed by migration 009.
--
-- A home/away pairing occurs at most once per matchday within a season —
-- including in the split-format leagues that make the date necessary in 008's
-- key, where the repeat meeting falls in a later round. The key holds no date,
-- so a revision updates the fixture it belongs to.
--
-- The index is partial because CSV loads carry no matchday: every csv row would
-- collide on (league, season, home, away, NULL) otherwise. Those rows keep the
-- date-keyed identity from 008, which still covers all rows and still catches
-- the case where a matchday-bearing row meets a matchday-less one on the same
-- date.

CREATE UNIQUE INDEX IF NOT EXISTS idx_matches_identity_matchday
    ON matches(league_id, season, home_team_id, away_team_id, matchday)
    WHERE matchday IS NOT NULL;
