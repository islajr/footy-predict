-- Migration 003: Broaden predictions unique constraint to (match_id, market, pick)
--
-- Why: market models now emit multiple picks per market per match:
--   - 1x2:            3 picks  (Home Win, Draw, Away Win)
--   - double-chance:  3 picks  (1X, X2, 12)
--   - over-under-*:   2 picks  (Over, Under) × 4 lines = 8 picks
--   - gg-ng:          2 picks  (GG, NG)
--   - draw-no-bet:    2 picks  (DNB Home, DNB Away)
--   - win-to-nil:     4 picks  (Home Yes, Home No, Away Yes, Away No)
--   - win-either-half:2 picks  (Home WEH, Away WEH)
--   - team-over-under:4 picks  (Home Over/Under, Away Over/Under) × 2 lines = 8 picks
--   - handicap:       up to 3 picks per match
--
-- The previous UNIQUE(match_id, market) constraint only allowed one row
-- per market per match. The new constraint allows one row per (match, market, pick).
--
-- This migration is idempotent: if the new table already exists it will fail
-- gracefully on the CREATE TABLE step. Run via: python -m footy.db_migrate 003

-- Step 1: Create the replacement predictions table with the broadened constraint
CREATE TABLE IF NOT EXISTS predictions_v3 (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id         INTEGER NOT NULL REFERENCES matches(id),
    market           TEXT    NOT NULL,       -- Market code (e.g. "over-under-2.5")
    pick             TEXT    NOT NULL,       -- The recommendation (e.g. "Over 2.5")
    probability      REAL    NOT NULL,       -- 0.0 – 1.0
    confidence       TEXT    NOT NULL,       -- "HIGH", "MEDIUM", "LOW"
    implied_odds     REAL,                   -- 1.0 / probability
    rationale        TEXT    NOT NULL,       -- Human-readable explanation
    risk_factors     TEXT    DEFAULT '[]',   -- JSON array of risk strings
    supporting_stats TEXT    DEFAULT '{}',   -- JSON dict of key stats
    factor_scores    TEXT    DEFAULT '{}',   -- JSON dict: factor name → score
    created_at       TEXT    DEFAULT (datetime('now')),
    UNIQUE(match_id, market, pick)           -- Broadened from UNIQUE(match_id, market)
);

-- Step 2: Migrate all existing data (ignore duplicates that would now violate the new key)
INSERT OR IGNORE INTO predictions_v3 (
    id, match_id, market, pick, probability, confidence,
    implied_odds, rationale, risk_factors, supporting_stats,
    factor_scores, created_at
)
SELECT
    id, match_id, market, pick, probability, confidence,
    implied_odds, rationale, risk_factors, supporting_stats,
    factor_scores, created_at
FROM predictions;

-- Step 3: Swap — drop old table and rename replacement
DROP TABLE predictions;
ALTER TABLE predictions_v3 RENAME TO predictions;

-- Step 4: Ensure user_markets has Yes/No WtN picks labelled correctly
-- (These are framing labels, not separate market codes)
UPDATE user_markets
   SET display_name = 'Win to Nil (Yes/No)'
 WHERE market_code = 'win-to-nil';
