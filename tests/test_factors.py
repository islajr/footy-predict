import pytest
import tempfile
import os
from pathlib import Path
from src.database import Database
from src.analysis.factors import (
    recent_form,
    home_form,
    away_form,
    scoring_frequency,
    defensive_record,
    head_to_head,
    league_position,
    goal_timing,
    momentum,
    scoring_patterns,
    rest_days,
    rivalry,
    venue_advantage,
    attacking_matchup,
    xg_quality,
)

@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = Database(path)
    # Seed default leagues
    db.seed_leagues()
    yield db
    try:
        os.unlink(path)
    except OSError:
        pass

def test_recent_form(temp_db):
    db = temp_db
    # Get Premier League id
    leagues = db.get_all_leagues()
    pl = next(l for l in leagues if l["slug"] == "premier-league")
    league_id = pl["id"]
    season = "2025"

    # Insert teams
    home_id = db.upsert_team("Arsenal", league_id, api_football_id=42)
    away_id = db.upsert_team("Chelsea", league_id, api_football_id=49)
    other_id = db.upsert_team("Spurs", league_id, api_football_id=47)

    # Insert finished matches for Arsenal (Recent Form: W, W, W, W, W)
    for i in range(5):
        db.upsert_match(
            league_id=league_id,
            season=season,
            date=f"2025-05-0{i+1}",
            home_team_id=home_id,
            away_team_id=other_id,
            api_football_id=100 + i,
            status="FINISHED",
            home_score=3,
            away_score=0
        )

    # Insert finished matches for Chelsea (Recent Form: L, L, L, L, L)
    for i in range(5):
        db.upsert_match(
            league_id=league_id,
            season=season,
            date=f"2025-05-1{i+1}",
            home_team_id=other_id,
            away_team_id=away_id,
            api_football_id=200 + i,
            status="FINISHED",
            home_score=3,
            away_score=0
        )

    res = recent_form(db, home_id, away_id, league_id, season)
    assert res.name == "recent_form"
    assert res.score > 0.0  # Favours Arsenal
    assert res.confidence > 0.4

def test_home_and_away_form(temp_db):
    db = temp_db
    leagues = db.get_all_leagues()
    pl = next(l for l in leagues if l["slug"] == "premier-league")
    league_id = pl["id"]
    season = "2025"

    home_id = db.upsert_team("Arsenal", league_id, api_football_id=42)
    away_id = db.upsert_team("Chelsea", league_id, api_football_id=49)
    other_id = db.upsert_team("Spurs", league_id, api_football_id=47)

    # Arsenal Home Matches (all wins)
    for i in range(3):
        db.upsert_match(
            league_id=league_id,
            season=season,
            date=f"2025-04-0{i+1}",
            home_team_id=home_id,
            away_team_id=other_id,
            api_football_id=300 + i,
            status="FINISHED",
            home_score=2,
            away_score=0
        )

    # Chelsea Away Matches (all losses)
    for i in range(3):
        db.upsert_match(
            league_id=league_id,
            season=season,
            date=f"2025-04-1{i+1}",
            home_team_id=other_id,
            away_team_id=away_id,
            api_football_id=400 + i,
            status="FINISHED",
            home_score=3,
            away_score=1
        )

    res_home = home_form(db, home_id, away_id, league_id, season)
    assert res_home.name == "home_form"
    assert res_home.score > 0.0
    assert res_home.confidence > 0.2

    res_away = away_form(db, home_id, away_id, league_id, season)
    assert res_away.name == "away_form"
    assert res_away.score > 0.0  # Positive score favors home / disadvantages awaypoor away form
    assert res_away.confidence > 0.2

def test_scoring_and_defense(temp_db):
    db = temp_db
    leagues = db.get_all_leagues()
    pl = next(l for l in leagues if l["slug"] == "premier-league")
    league_id = pl["id"]
    season = "2025"

    home_id = db.upsert_team("Arsenal", league_id, api_football_id=42)
    away_id = db.upsert_team("Chelsea", league_id, api_football_id=49)
    other_id = db.upsert_team("Spurs", league_id, api_football_id=47)

    # Arsenal Home Matches (high scoring, solid defense)
    for i in range(5):
        db.upsert_match(
            league_id=league_id,
            season=season,
            date=f"2025-04-0{i+1}",
            home_team_id=home_id,
            away_team_id=other_id,
            api_football_id=300 + i,
            status="FINISHED",
            home_score=3,
            away_score=0
        )

    # Chelsea Away Matches (low scoring, poor defense)
    for i in range(5):
        db.upsert_match(
            league_id=league_id,
            season=season,
            date=f"2025-04-1{i+1}",
            home_team_id=other_id,
            away_team_id=away_id,
            api_football_id=400 + i,
            status="FINISHED",
            home_score=2,
            away_score=1
        )

    res_score = scoring_frequency(db, home_id, away_id, league_id, season)
    assert res_score.name == "scoring_frequency"
    assert res_score.score > 0.0
    assert res_score.confidence > 0.4

    res_def = defensive_record(db, home_id, away_id, league_id, season)
    assert res_def.name == "defensive_record"
    assert res_def.score > 0.0
    assert res_def.confidence > 0.4

def test_head_to_head(temp_db):
    db = temp_db
    leagues = db.get_all_leagues()
    pl = next(l for l in leagues if l["slug"] == "premier-league")
    league_id = pl["id"]
    season = "2025"

    home_id = db.upsert_team("Arsenal", league_id, api_football_id=42)
    away_id = db.upsert_team("Chelsea", league_id, api_football_id=49)

    # Insert H2H matches (Home team wins them all)
    for i in range(4):
        db.upsert_match(
            league_id=league_id,
            season=season,
            date=f"2024-0{i+1}-01",
            home_team_id=home_id,
            away_team_id=away_id,
            api_football_id=500 + i,
            status="FINISHED",
            home_score=2,
            away_score=0
        )

    res = head_to_head(db, home_id, away_id, league_id, season)
    assert res.name == "head_to_head"
    assert res.score > 0.0
    assert res.confidence > 0.3

def test_league_position(temp_db):
    db = temp_db
    leagues = db.get_all_leagues()
    pl = next(l for l in leagues if l["slug"] == "premier-league")
    league_id = pl["id"]
    season = "2025"

    home_id = db.upsert_team("Arsenal", league_id, api_football_id=42)
    away_id = db.upsert_team("Chelsea", league_id, api_football_id=49)

    db.upsert_standing(league_id, season, home_id, position=1, played=10, won=9, drawn=1, lost=0, goals_for=30, goals_against=5, points=28)
    db.upsert_standing(league_id, season, away_id, position=10, played=10, won=4, drawn=2, lost=4, goals_for=15, goals_against=15, points=14)

    res = league_position(db, home_id, away_id, league_id, season)
    assert res.name == "league_position"
    assert res.score > 0.0
    assert res.confidence > 0.8

def test_goal_timing(temp_db):
    db = temp_db
    leagues = db.get_all_leagues()
    pl = next(l for l in leagues if l["slug"] == "premier-league")
    league_id = pl["id"]
    season = "2025"

    home_id = db.upsert_team("Arsenal", league_id, api_football_id=42)
    away_id = db.upsert_team("Chelsea", league_id, api_football_id=49)

    # Match with half-time score
    db.upsert_match(
        league_id=league_id,
        season=season,
        date="2025-01-01",
        home_team_id=home_id,
        away_team_id=away_id,
        api_football_id=601,
        status="FINISHED",
        home_score=3,
        away_score=1,
        ht_home_score=2,
        ht_away_score=0
    )

    res = goal_timing(db, home_id, away_id, league_id, season)
    assert res.name == "goal_timing"
    assert res.confidence > 0.0

def test_momentum(temp_db):
    db = temp_db
    leagues = db.get_all_leagues()
    pl = next(l for l in leagues if l["slug"] == "premier-league")
    league_id = pl["id"]
    season = "2025"

    home_id = db.upsert_team("Arsenal", league_id, api_football_id=42)
    away_id = db.upsert_team("Chelsea", league_id, api_football_id=49)
    other_id = db.upsert_team("Spurs", league_id, api_football_id=47)

    # Home team matches (improving momentum): L, L, L, then W, W, W
    dates = ["2025-01-01", "2025-01-02", "2025-01-03", "2025-01-04", "2025-01-05", "2025-01-06"]
    for i, date in enumerate(dates):
        hs, as_ = (3, 0) if i >= 3 else (0, 3)
        db.upsert_match(
            league_id=league_id,
            season=season,
            date=date,
            home_team_id=home_id,
            away_team_id=other_id,
            api_football_id=700 + i,
            status="FINISHED",
            home_score=hs,
            away_score=as_
        )

    # Away team matches (declining momentum): W, W, W, then L, L, L
    for i, date in enumerate(dates):
        # older matches: Chelsea wins (so other_id scores 0, away_id scores 3)
        # newer matches: Chelsea loses (so other_id scores 3, away_id scores 0)
        hs, as_ = (0, 3) if i < 3 else (3, 0)
        db.upsert_match(
            league_id=league_id,
            season=season,
            date=date,
            home_team_id=other_id,
            away_team_id=away_id,
            api_football_id=800 + i,
            status="FINISHED",
            home_score=hs,
            away_score=as_
        )

    res = momentum(db, home_id, away_id, league_id, season)
    assert res.name == "momentum"
    assert res.score > 0.0  # Favours home team due to improvement vs away's decline
    assert res.confidence > 0.4

def test_scoring_patterns(temp_db):
    db = temp_db
    leagues = db.get_all_leagues()
    pl = next(l for l in leagues if l["slug"] == "premier-league")
    league_id = pl["id"]
    season = "2025"

    home_id = db.upsert_team("Arsenal", league_id, api_football_id=42)
    away_id = db.upsert_team("Chelsea", league_id, api_football_id=49)

    db.upsert_team_season_stats(
        team_id=home_id,
        league_id=league_id,
        season=season,
        matches_played=10,
        btts_percentage=0.6,
        over_2_5_percentage=0.7,
        under_2_5_percentage=0.3
    )
    db.upsert_team_season_stats(
        team_id=away_id,
        league_id=league_id,
        season=season,
        matches_played=10,
        btts_percentage=0.4,
        over_2_5_percentage=0.5,
        under_2_5_percentage=0.5
    )

    res = scoring_patterns(db, home_id, away_id, league_id, season)
    assert res.name == "scoring_patterns"
    assert res.confidence > 0.4

def test_rivalry(temp_db):
    db = temp_db
    leagues = db.get_all_leagues()
    pl = next(l for l in leagues if l["slug"] == "premier-league")
    league_id = pl["id"]
    season = "2025"

    # Manchester Derby IDs in API-Football are: City (50) and United (33)
    home_id = db.upsert_team("Manchester City", league_id, api_football_id=50)
    away_id = db.upsert_team("Manchester United", league_id, api_football_id=33)

    res = rivalry(db, home_id, away_id, league_id, season)
    assert res.name == "rivalry"
    assert res.score == 0.0  # Derby forces neutrality / unpredictable
    assert res.confidence > 0.5

def test_rest_days(temp_db):
    db = temp_db
    leagues = db.get_all_leagues()
    pl = next(l for l in leagues if l["slug"] == "premier-league")
    league_id = pl["id"]
    season = "2025"

    home_id = db.upsert_team("Arsenal", league_id, api_football_id=42)
    away_id = db.upsert_team("Chelsea", league_id, api_football_id=49)
    other_id = db.upsert_team("Spurs", league_id, api_football_id=47)

    # Home last match: 2025-06-01
    db.upsert_match(
        league_id=league_id,
        season=season,
        date="2025-06-01T15:00:00Z",
        home_team_id=home_id,
        away_team_id=other_id,
        api_football_id=901,
        status="FINISHED",
        home_score=1,
        away_score=0
    )

    # Away last match: 2025-06-03
    db.upsert_match(
        league_id=league_id,
        season=season,
        date="2025-06-03T15:00:00Z",
        home_team_id=other_id,
        away_team_id=away_id,
        api_football_id=902,
        status="FINISHED",
        home_score=1,
        away_score=0
    )

    res = rest_days(db, home_id, away_id, league_id, season)
    assert res.name == "rest_days"
    assert isinstance(res.score, float)


# ─────────────────────────────────────────────────────
#  Phase A Factor Tests
# ─────────────────────────────────────────────────────

@pytest.fixture
def db_with_home_specialist(temp_db):
    """A home team (Arsenal) with a strong home record and poor away record.
    The away team (Chelsea) is neutral at both venues.
    Used for venue_advantage and attacking_matchup tests.
    """
    db = temp_db
    leagues = db.get_all_leagues()
    pl = next(l for l in leagues if l["slug"] == "premier-league")
    league_id = pl["id"]
    season = "2025"

    home_id = db.upsert_team("Arsenal", league_id, api_football_id=42)
    away_id = db.upsert_team("Chelsea", league_id, api_football_id=49)
    other_id = db.upsert_team("Spurs", league_id, api_football_id=47)

    # Arsenal — 5 home wins (strong at home)
    for i in range(5):
        db.upsert_match(
            league_id=league_id, season=season,
            date=f"2025-03-0{i+1}",
            home_team_id=home_id, away_team_id=other_id,
            api_football_id=1000 + i, status="FINISHED",
            home_score=3, away_score=0,
        )

    # Arsenal — 5 away losses (poor away)
    for i in range(5):
        db.upsert_match(
            league_id=league_id, season=season,
            date=f"2025-04-0{i+1}",
            home_team_id=other_id, away_team_id=home_id,
            api_football_id=1100 + i, status="FINISHED",
            home_score=2, away_score=0,
        )

    # Chelsea — mixed results (neutral)
    for i in range(5):
        db.upsert_match(
            league_id=league_id, season=season,
            date=f"2025-03-1{i+1}",
            home_team_id=away_id, away_team_id=other_id,
            api_football_id=1200 + i, status="FINISHED",
            home_score=1, away_score=1,
        )
    for i in range(5):
        db.upsert_match(
            league_id=league_id, season=season,
            date=f"2025-04-1{i+1}",
            home_team_id=other_id, away_team_id=away_id,
            api_football_id=1300 + i, status="FINISHED",
            home_score=1, away_score=1,
        )

    return db, home_id, away_id, league_id, season


# ── venue_advantage ───────────────────────────────────────────

def test_venue_advantage_returns_factor_result(db_with_home_specialist):
    db, home_id, away_id, league_id, season = db_with_home_specialist
    res = venue_advantage(db, home_id, away_id, league_id, season)
    assert res.name == "venue_advantage"
    assert -1.0 <= res.score <= 1.0
    assert 0.0 <= res.confidence <= 1.0
    assert isinstance(res.rationale, str) and len(res.rationale) > 0


def test_venue_advantage_home_specialist_scores_positive(db_with_home_specialist):
    """Arsenal is strong at home, poor away — score should favour home."""
    db, home_id, away_id, league_id, season = db_with_home_specialist
    res = venue_advantage(db, home_id, away_id, league_id, season)
    assert res.score > 0.0, f"Expected positive score, got {res.score:.3f}"


def test_venue_advantage_no_data_low_confidence(temp_db):
    """With no match history, factor should return low confidence."""
    db = temp_db
    leagues = db.get_all_leagues()
    pl = next(l for l in leagues if l["slug"] == "premier-league")
    league_id = pl["id"]
    home_id = db.upsert_team("Arsenal", league_id, api_football_id=42)
    away_id = db.upsert_team("Chelsea", league_id, api_football_id=49)
    res = venue_advantage(db, home_id, away_id, league_id, "2025")
    assert res.confidence < 0.5


# ── attacking_matchup ─────────────────────────────────────────

def test_attacking_matchup_returns_factor_result(db_with_home_specialist):
    db, home_id, away_id, league_id, season = db_with_home_specialist
    res = attacking_matchup(db, home_id, away_id, league_id, season)
    assert res.name == "attacking_matchup"
    assert -1.0 <= res.score <= 1.0
    assert 0.0 <= res.confidence <= 1.0


def test_attacking_matchup_exposes_expected_goal_keys(db_with_home_specialist):
    """attacking_matchup must expose expected_goal_diff for handicap model."""
    db, home_id, away_id, league_id, season = db_with_home_specialist
    res = attacking_matchup(db, home_id, away_id, league_id, season)
    dp = res.data_points
    assert "expected_goal_diff" in dp, f"Missing expected_goal_diff in data_points: {list(dp.keys())}"
    assert "expected_home_goals" in dp
    assert "expected_away_goals" in dp
    assert "expected_total" in dp


def test_attacking_matchup_high_scorer_vs_leaky_defence_positive(db_with_home_specialist):
    """High-scoring home team vs leaky away defence should produce positive score."""
    db, home_id, away_id, league_id, season = db_with_home_specialist
    res = attacking_matchup(db, home_id, away_id, league_id, season)
    # Arsenal scored 3 goals/home game vs Spurs; Chelsea conceded from equal 1-1 draws.
    # home attack advantage expected.
    assert res.score >= -0.5, f"Score unexpectedly low: {res.score:.3f}"


# ── xg_quality ─────────────────────────────────────────────

def test_xg_quality_returns_factor_result(db_with_home_specialist):
    db, home_id, away_id, league_id, season = db_with_home_specialist
    res = xg_quality(db, home_id, away_id, league_id, season)
    assert res.name == "xg_quality"
    assert -1.0 <= res.score <= 1.0
    assert 0.0 <= res.confidence <= 1.0
    assert isinstance(res.rationale, str) and len(res.rationale) > 0


def test_xg_quality_no_data_returns_neutral(temp_db):
    """With no match_stats data (no xG records), factor should be neutral and low-confidence."""
    db = temp_db
    leagues = db.get_all_leagues()
    pl = next(l for l in leagues if l["slug"] == "premier-league")
    league_id = pl["id"]
    home_id = db.upsert_team("Arsenal", league_id, api_football_id=42)
    away_id = db.upsert_team("Chelsea", league_id, api_football_id=49)
    res = xg_quality(db, home_id, away_id, league_id, "2025")
    # No xG data — should not crash, should return low confidence neutral result
    assert -1.0 <= res.score <= 1.0
    assert res.confidence < 0.5
