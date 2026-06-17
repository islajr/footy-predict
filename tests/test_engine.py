import pytest
import tempfile
import os
from src.database import Database
from src.analysis.engine import AnalysisEngine, MatchAnalysis

@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = Database(path)
    db.seed_leagues()
    yield db
    try:
        os.unlink(path)
    except OSError:
        pass

def test_engine_initialization(temp_db):
    engine = AnalysisEngine(temp_db)
    assert engine.db == temp_db
    assert len(engine.weights) > 0
    assert pytest.approx(sum(engine.weights.values())) == 1.0

def test_analyze_match_insufficient_data(temp_db):
    db = temp_db
    leagues = db.get_all_leagues()
    pl = next(l for l in leagues if l["slug"] == "premier-league")
    league_id = pl["id"]
    season = "2025"

    home_id = db.upsert_team("Arsenal", league_id, api_football_id=42)
    away_id = db.upsert_team("Chelsea", league_id, api_football_id=49)

    # Insert a scheduled match
    match_id = db.upsert_match(
        league_id=league_id,
        season=season,
        date="2025-06-20",
        home_team_id=home_id,
        away_team_id=away_id,
        status="SCHEDULED"
    )

    engine = AnalysisEngine(db)
    analysis = engine.analyze_match(match_id)

    assert isinstance(analysis, MatchAnalysis)
    assert analysis.match_id == match_id
    assert analysis.home_team == "Arsenal"
    assert analysis.away_team == "Chelsea"
    assert analysis.league == pl["name"]
    # Due to no historical data, it should return low data quality
    assert analysis.data_quality == "LOW"
    assert len(analysis.factor_results) > 0
    # Should still generate some fallback recommendations
    assert len(analysis.market_recommendations) > 0

def test_analyze_match_with_data(temp_db):
    db = temp_db
    leagues = db.get_all_leagues()
    pl = next(l for l in leagues if l["slug"] == "premier-league")
    league_id = pl["id"]
    season = "2025"

    home_id = db.upsert_team("Arsenal", league_id, api_football_id=42)
    away_id = db.upsert_team("Chelsea", league_id, api_football_id=49)
    other_id = db.upsert_team("Spurs", league_id, api_football_id=47)

    # Insert matches for Arsenal (Recent Form: W, W, W, W, W)
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

    # Insert matches for Chelsea (Recent Form: L, L, L, L, L)
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

    # Insert standings
    db.upsert_standing(league_id, season, home_id, position=1, played=10, won=9, drawn=1, lost=0, goals_for=30, goals_against=5, points=28)
    db.upsert_standing(league_id, season, away_id, position=10, played=10, won=4, drawn=2, lost=4, goals_for=15, goals_against=15, points=14)

    # Insert scheduled match to analyze
    match_id = db.upsert_match(
        league_id=league_id,
        season=season,
        date="2025-06-25",
        home_team_id=home_id,
        away_team_id=away_id,
        status="SCHEDULED"
    )

    engine = AnalysisEngine(db)
    analysis = engine.analyze_match(match_id)

    assert isinstance(analysis, MatchAnalysis)
    assert analysis.overall_home_score > 0.0  # Should favor Arsenal
    assert analysis.overall_confidence > 0.3
    # Check that recommendations are generated and some are high/medium confidence
    assert len(analysis.market_recommendations) > 0
    assert any(rec.confidence in ["HIGH", "MEDIUM"] for rec in analysis.market_recommendations)
