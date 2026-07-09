-- Migration 004: Add home/away split columns to standings table
-- These are present in the API-Football /standings response (entry["home"] / entry["away"])
-- but were previously discarded. This migration adds the columns so the next standings
-- sync will populate them, enabling the league_position factor to use venue-specific
-- table data and eventually the home/away league table split Tier-3 feature.

ALTER TABLE standings ADD COLUMN home_played INTEGER DEFAULT 0;
ALTER TABLE standings ADD COLUMN home_won    INTEGER DEFAULT 0;
ALTER TABLE standings ADD COLUMN home_drawn  INTEGER DEFAULT 0;
ALTER TABLE standings ADD COLUMN home_lost   INTEGER DEFAULT 0;
ALTER TABLE standings ADD COLUMN home_gf     INTEGER DEFAULT 0;
ALTER TABLE standings ADD COLUMN home_ga     INTEGER DEFAULT 0;

ALTER TABLE standings ADD COLUMN away_played INTEGER DEFAULT 0;
ALTER TABLE standings ADD COLUMN away_won    INTEGER DEFAULT 0;
ALTER TABLE standings ADD COLUMN away_drawn  INTEGER DEFAULT 0;
ALTER TABLE standings ADD COLUMN away_lost   INTEGER DEFAULT 0;
ALTER TABLE standings ADD COLUMN away_gf     INTEGER DEFAULT 0;
ALTER TABLE standings ADD COLUMN away_ga     INTEGER DEFAULT 0;
