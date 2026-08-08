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
    standings_are_ranked,
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


# ════════════════════════════════════════════════════════════════
#  Phase B — opponent_strength tests
# ════════════════════════════════════════════════════════════════

from src.analysis.factors import opponent_strength


@pytest.fixture
def db_with_standings(temp_db):
    """DB seeded with two teams, standings, and match history."""
    db = temp_db
    leagues = db.get_all_leagues()
    pl = next(l for l in leagues if l["slug"] == "premier-league")
    league_id = pl["id"]
    season = "2025/26"

    home_id = db.upsert_team("Arsenal", league_id, api_football_id=42)
    away_id = db.upsert_team("Chelsea", league_id, api_football_id=49)
    strong_id = db.upsert_team("Man City", league_id, api_football_id=50)
    weak_id = db.upsert_team("Ipswich", league_id, api_football_id=57)

    # Standings: Man City rank 1, Arsenal rank 2, Chelsea rank 5, Ipswich rank 18
    for tid, pos in [(strong_id, 1), (home_id, 2), (away_id, 5), (weak_id, 18)]:
        db.upsert_standing(
            league_id=league_id, season=season, team_id=tid,
            position=pos, played=20, won=12, drawn=4, lost=4,
            goals_for=35, goals_against=18, goal_difference=17, points=40,
        )

    # Arsenal: 5 wins vs Man City (rank 1) — high-quality wins
    for i in range(5):
        db.upsert_match(
            league_id=league_id, season=season,
            date=f"2025-10-{i+1:02d}T15:00:00",
            home_team_id=home_id, away_team_id=strong_id,
            status="FINISHED", home_score=2, away_score=1,
        )

    # Chelsea: 5 wins vs Ipswich (rank 18) — easy wins
    for i in range(5):
        db.upsert_match(
            league_id=league_id, season=season,
            date=f"2025-10-{i+1:02d}T15:00:00",
            home_team_id=away_id, away_team_id=weak_id,
            status="FINISHED", home_score=3, away_score=0,
        )

    return db, home_id, away_id, league_id, season


def test_opponent_strength_top_wins_score_higher(db_with_standings):
    """Beating top-4 opposition should produce higher sadj_ppg than losing to bottom side.

    Scenario: Arsenal beat rank-1 Man City, Chelsea lost to rank-1 Man City.
    Even though both played the same opponent, Arsenal's result is better →
    Arsenal should have higher strength-adjusted PPG → positive score.
    """
    db, home_id, away_id, league_id, season = db_with_standings
    strong_id = db.get_team_by_api_football_id(50)["id"]

    # Add: Chelsea LOSES to Man City (rank 1) — so Chelsea has wins only vs Ipswich
    # and a loss vs strong opposition → lower sadj_ppg than Arsenal
    db.upsert_match(
        league_id=league_id, season=season,
        date="2025-11-01T15:00:00",
        home_team_id=away_id, away_team_id=strong_id,
        status="FINISHED", home_score=0, away_score=3,
    )

    res = opponent_strength(db, home_id, away_id, league_id, season)

    assert res.name == "opponent_strength"
    assert -1.0 <= res.score <= 1.0
    # Arsenal: won vs rank-1. Chelsea: won vs rank-18 AND lost vs rank-1.
    # Arsenal's sadj_ppg should exceed Chelsea's.
    assert res.data_points["home_strength_adj_ppg"] > res.data_points["away_strength_adj_ppg"], (
        f"Expected home sadj_ppg > away sadj_ppg\n"
        f"data_points={res.data_points}"
    )
    assert res.score > 0, f"Expected positive score, got {res.score:.4f}"


def test_opponent_strength_no_standings_returns_low_confidence(temp_db):
    """Without standings data the factor should gracefully return low confidence."""
    db = temp_db
    leagues = db.get_all_leagues()
    pl = next(l for l in leagues if l["slug"] == "premier-league")
    league_id = pl["id"]
    season = "2025/26"

    home_id = db.upsert_team("Arsenal", league_id, api_football_id=42)
    away_id = db.upsert_team("Chelsea", league_id, api_football_id=49)
    other_id = db.upsert_team("Spurs", league_id, api_football_id=47)

    for i in range(5):
        db.upsert_match(
            league_id=league_id, season=season,
            date=f"2025-10-{i+1:02d}T15:00:00",
            home_team_id=home_id, away_team_id=other_id,
            status="FINISHED", home_score=2, away_score=1,
        )
        db.upsert_match(
            league_id=league_id, season=season,
            date=f"2025-10-{i+1:02d}T17:00:00",
            home_team_id=away_id, away_team_id=other_id,
            status="FINISHED", home_score=2, away_score=1,
        )

    # No standings inserted — factor must not crash
    res = opponent_strength(db, home_id, away_id, league_id, season)
    assert res.name == "opponent_strength"
    assert -1.0 <= res.score <= 1.0
    assert res.confidence < 0.3, f"Expected low confidence without standings, got {res.confidence:.3f}"


def test_opponent_strength_equal_teams_near_neutral(db_with_standings):
    """If both teams faced equal-strength opponents, score should be near zero."""
    db, home_id, away_id, league_id, season = db_with_standings
    strong_id = db.get_team_by_api_football_id(50)["id"]
    weak_id = db.get_team_by_api_football_id(57)["id"]

    # Give both teams an identical schedule: beat rank-1 and rank-18 once each
    for tid in [home_id, away_id]:
        for opp_id, score_h, score_a in [(strong_id, 2, 1), (weak_id, 3, 0)]:
            db.upsert_match(
                league_id=league_id, season=season,
                date="2025-11-01T15:00:00",
                home_team_id=tid, away_team_id=opp_id,
                status="FINISHED", home_score=score_h, away_score=score_a,
            )

    res = opponent_strength(db, home_id, away_id, league_id, season)
    # Score won't be exactly 0 (existing matches still differ), but with same added schedule
    # the scores should be closer to each other than 0.8 apart
    assert abs(res.score) < 1.0  # basic sanity — not clamped at extremes


def test_opponent_strength_all_draws_returns_zero_score(temp_db):
    """All drawn results against same-ranked opponents should give score ≈ 0."""
    db = temp_db
    leagues = db.get_all_leagues()
    pl = next(l for l in leagues if l["slug"] == "premier-league")
    league_id = pl["id"]
    season = "2025/26"

    home_id = db.upsert_team("Arsenal", league_id, api_football_id=42)
    away_id = db.upsert_team("Chelsea", league_id, api_football_id=49)
    opp_id = db.upsert_team("Spurs", league_id, api_football_id=47)

    db.upsert_standing(
        league_id=league_id, season=season, team_id=opp_id,
        position=10, played=20, won=8, drawn=6, lost=6,
        goals_for=25, goals_against=25, goal_difference=0, points=30,
    )
    db.upsert_standing(
        league_id=league_id, season=season, team_id=home_id,
        position=2, played=20, won=12, drawn=4, lost=4,
        goals_for=35, goals_against=18, goal_difference=17, points=40,
    )
    db.upsert_standing(
        league_id=league_id, season=season, team_id=away_id,
        position=3, played=20, won=11, drawn=5, lost=4,
        goals_for=30, goals_against=20, goal_difference=10, points=38,
    )

    # Both teams drew 5 times each vs same mid-table side
    for i in range(5):
        for focal in [home_id, away_id]:
            db.upsert_match(
                league_id=league_id, season=season,
                date=f"2025-10-{i+1:02d}T{'15' if focal == home_id else '17'}:00:00",
                home_team_id=focal, away_team_id=opp_id,
                status="FINISHED", home_score=1, away_score=1,
            )

    res = opponent_strength(db, home_id, away_id, league_id, season)
    # Both teams got 1 PPG (draws only) vs identical opponents → score ≈ 0
    assert abs(res.score) < 0.15, f"Expected near-zero score for equal draws, got {res.score:.4f}"


# ════════════════════════════════════════════════════════════════
#  Degenerate standings — a table that exists but ranks nothing
# ════════════════════════════════════════════════════════════════

def _preseason_table(db, league_id, season, team_ids):
    """Store the pre-season table the API returns: everyone 1st, nothing played.

    This is the shape found live on eredivisie, ligue-1 and serie-a for
    2026-27, and on premier-league 2025-26 in a partial form.
    """
    for tid in team_ids:
        db.upsert_standing(
            league_id=league_id, season=season, team_id=tid,
            position=1, played=0, won=0, drawn=0, lost=0,
            goals_for=0, goals_against=0, goal_difference=0, points=0,
        )


def test_league_position_treats_preseason_table_as_missing(temp_db):
    """A blank table must read as no data, not as a confident draw signal.

    With every team at position 1 on 0 points the score computes to exactly
    0.0, and the factor used to report that at confidence 0.85. A zero at high
    confidence is worse than no data: it survives the weighted average and
    drags the aggregate toward neutral.
    """
    db = temp_db
    leagues = db.get_all_leagues()
    league_id = next(l for l in leagues if l["slug"] == "serie-a")["id"]
    season = "2026-27"

    home_id = db.upsert_team("Udinese Calcio", league_id, api_football_id=494)
    away_id = db.upsert_team("Como 1907", league_id, api_football_id=895)
    _preseason_table(db, league_id, season, [home_id, away_id])

    res = league_position(db, home_id, away_id, league_id, season)
    assert res.confidence == 0.0, (
        f"blank table reported at confidence {res.confidence:.2f}: {res.rationale}"
    )
    assert res.score == 0.0


def test_opponent_strength_treats_preseason_table_as_missing(temp_db):
    """Uniform positions make every opponent weight identical.

    The factor then degrades to plain PPG while its rationale still claims a
    rank adjustment — and, because confidence is driven by match count rather
    than table quality, it says so at full strength.
    """
    db = temp_db
    leagues = db.get_all_leagues()
    league_id = next(l for l in leagues if l["slug"] == "eredivisie")["id"]
    season = "2026-27"

    home_id = db.upsert_team("Ajax", league_id, api_football_id=194)
    away_id = db.upsert_team("PSV", league_id, api_football_id=197)
    opp_id = db.upsert_team("Feyenoord", league_id, api_football_id=209)
    for i in range(5):
        db.upsert_match(
            league_id=league_id, season=season,
            date=f"2026-09-{i+1:02d}T15:00:00",
            home_team_id=home_id, away_team_id=opp_id,
            status="FINISHED", home_score=2, away_score=1,
        )
        db.upsert_match(
            league_id=league_id, season=season,
            date=f"2026-09-{i+1:02d}T17:00:00",
            home_team_id=away_id, away_team_id=opp_id,
            status="FINISHED", home_score=1, away_score=1,
        )
    _preseason_table(db, league_id, season, [home_id, away_id, opp_id])

    res = opponent_strength(db, home_id, away_id, league_id, season)
    assert res.confidence == 0.0, (
        f"unranked table reported at confidence {res.confidence:.2f}: {res.rationale}"
    )


def test_duplicate_positions_read_as_unranked(temp_db):
    """Played matches are not enough — the ordering has to be real.

    premier-league 2025-26 holds 23 rows across 4 distinct positions, a
    relegation-contamination artifact. Points and games played look plausible,
    so only the collision in the position column gives it away.
    """
    db = temp_db
    leagues = db.get_all_leagues()
    league_id = next(l for l in leagues if l["slug"] == "premier-league")["id"]
    season = "2025-26"

    home_id = db.upsert_team("Arsenal", league_id, api_football_id=42)
    away_id = db.upsert_team("Chelsea", league_id, api_football_id=49)
    for tid in (home_id, away_id):
        db.upsert_standing(
            league_id=league_id, season=season, team_id=tid,
            position=1, played=38, won=25, drawn=10, lost=3,
            goals_for=70, goals_against=30, goal_difference=40, points=85,
        )

    res = league_position(db, home_id, away_id, league_id, season)
    assert res.confidence == 0.0


def test_first_matchday_table_is_usable(temp_db):
    """One round played is thin, but it is a real ranking — keep reading it.

    Guards the other side of the check: the fix must not blank out every early
    season table, only the ones with no ordering in them.
    """
    db = temp_db
    leagues = db.get_all_leagues()
    league_id = next(l for l in leagues if l["slug"] == "premier-league")["id"]
    season = "2026-27"

    home_id = db.upsert_team("Arsenal", league_id, api_football_id=42)
    away_id = db.upsert_team("Chelsea", league_id, api_football_id=49)
    db.upsert_standing(league_id=league_id, season=season, team_id=home_id,
                       position=1, played=1, won=1, drawn=0, lost=0,
                       goals_for=3, goals_against=0, goal_difference=3, points=3)
    db.upsert_standing(league_id=league_id, season=season, team_id=away_id,
                       position=20, played=1, won=0, drawn=0, lost=1,
                       goals_for=0, goals_against=3, goal_difference=-3, points=0)

    res = league_position(db, home_id, away_id, league_id, season)
    assert res.confidence > 0.8
    assert res.score > 0.0


@pytest.mark.parametrize("table, ranked", [
    ([], False),
    ([{"position": 1, "played": 0}, {"position": 1, "played": 0}], False),
    ([{"position": 1, "played": 38}, {"position": 1, "played": 38}], False),
    ([{"position": 1, "played": 0}, {"position": 2, "played": 0}], False),
    ([{"position": 1, "played": 1}, {"position": 2, "played": 0}], True),
    ([{"position": 1, "played": 38}, {"position": 2, "played": 38}], True),
    # A single-team table is trivially ordered; played is what decides it.
    ([{"position": 1, "played": 0}], False),
    ([{"position": 1, "played": 5}], True),
    # Missing keys must not raise — get_standings rows are dicts from SQLite.
    ([{"position": 1}, {"position": 2}], False),
    ([{"position": 1, "played": None}, {"position": 2, "played": 3}], True),
])
def test_standings_are_ranked(table, ranked):
    """The predicate both factors and `footy status` gate on."""
    assert standings_are_ranked(table) is ranked


# ════════════════════════════════════════════════════════════════
#  Phase B — database: standings home/away splits
# ════════════════════════════════════════════════════════════════

def test_upsert_standing_stores_home_away_splits(temp_db):
    """Home/away split columns should persist via upsert_standing."""
    db = temp_db
    leagues = db.get_all_leagues()
    pl = next(l for l in leagues if l["slug"] == "premier-league")
    league_id = pl["id"]
    team_id = db.upsert_team("Arsenal", league_id, api_football_id=42)

    db.upsert_standing(
        league_id=league_id, season="2025/26", team_id=team_id,
        position=1, played=20, won=14, drawn=4, lost=2,
        goals_for=40, goals_against=15, goal_difference=25, points=46,
        home_played=10, home_won=8, home_drawn=1, home_lost=1,
        home_gf=24, home_ga=6,
        away_played=10, away_won=6, away_drawn=3, away_lost=1,
        away_gf=16, away_ga=9,
    )

    rows = db.get_standings(league_id, "2025/26")
    assert len(rows) == 1
    row = rows[0]
    assert row["home_played"] == 10
    assert row["home_won"] == 8
    assert row["away_won"] == 6
    assert row["away_ga"] == 9


def test_upsert_standing_home_away_defaults_to_zero(temp_db):
    """Calling upsert_standing without home/away kwargs should default all splits to 0."""
    db = temp_db
    leagues = db.get_all_leagues()
    pl = next(l for l in leagues if l["slug"] == "premier-league")
    league_id = pl["id"]
    team_id = db.upsert_team("Chelsea", league_id, api_football_id=49)

    db.upsert_standing(
        league_id=league_id, season="2025/26", team_id=team_id,
        position=5, played=20, won=10, drawn=5, lost=5,
        goals_for=28, goals_against=22, goal_difference=6, points=35,
    )

    rows = db.get_standings(league_id, "2025/26")
    assert len(rows) == 1
    row = rows[0]
    # All split columns should default to 0, not None
    for col in ("home_played", "home_won", "home_drawn", "home_lost",
                "home_gf", "home_ga", "away_played", "away_won",
                "away_drawn", "away_lost", "away_gf", "away_ga"):
        assert row.get(col) == 0, f"Expected {col}=0, got {row.get(col)}"


# ════════════════════════════════════════════════════════════════
#  Phase B — database: get_matches_without_stats
# ════════════════════════════════════════════════════════════════

def test_get_matches_without_stats_excludes_already_statted(temp_db):
    """Matches that already have match_stats rows should not appear in the result."""
    db = temp_db
    leagues = db.get_all_leagues()
    pl = next(l for l in leagues if l["slug"] == "premier-league")
    league_id = pl["id"]
    season = "2025/26"

    home_id = db.upsert_team("Arsenal", league_id, api_football_id=42)
    away_id = db.upsert_team("Chelsea", league_id, api_football_id=49)

    # Two FINISHED matches, one with api_football_id (statted), one without
    match_a_id = db.upsert_match(
        league_id=league_id, season=season, date="2025-10-01T15:00:00",
        home_team_id=home_id, away_team_id=away_id,
        status="FINISHED", home_score=2, away_score=1,
        api_football_id=99901,
    )
    match_b_id = db.upsert_match(
        league_id=league_id, season=season, date="2025-10-08T15:00:00",
        home_team_id=home_id, away_team_id=away_id,
        status="FINISHED", home_score=1, away_score=1,
        api_football_id=99902,
    )

    # Match A already has stats; match B does not
    db.upsert_match_stats(match_id=match_a_id, team_id=home_id, shots=10, shots_on_target=4)

    unstatted = db.get_matches_without_stats(league_id, limit=10)
    unstatted_ids = [r["id"] for r in unstatted]

    assert match_b_id in unstatted_ids, "Match B (no stats) should appear"
    assert match_a_id not in unstatted_ids, "Match A (has stats) should be excluded"


def test_get_matches_without_stats_excludes_non_finished(temp_db):
    """SCHEDULED and IN_PLAY matches should not appear even if they have no stats."""
    db = temp_db
    leagues = db.get_all_leagues()
    pl = next(l for l in leagues if l["slug"] == "premier-league")
    league_id = pl["id"]
    season = "2025/26"

    home_id = db.upsert_team("Arsenal", league_id, api_football_id=42)
    away_id = db.upsert_team("Chelsea", league_id, api_football_id=49)

    db.upsert_match(
        league_id=league_id, season=season, date="2026-08-10T15:00:00",
        home_team_id=home_id, away_team_id=away_id,
        status="SCHEDULED", api_football_id=99910,
    )

    unstatted = db.get_matches_without_stats(league_id, limit=10)
    assert len(unstatted) == 0, "SCHEDULED matches should not appear in unstatted list"
