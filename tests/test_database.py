import pytest
import tempfile
import os
from src.database import Database

@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = Database(path)
    yield db
    try:
        os.unlink(path)
    except OSError:
        pass

def test_database_creation(temp_db):
    assert os.path.exists(temp_db.db_path)

def test_league_operations(temp_db):
    db = temp_db
    # Seed leagues
    db.seed_leagues()
    leagues = db.get_all_leagues()
    assert len(leagues) == 12

    # Get league by slug
    pl = db.get_league("premier-league")
    assert pl is not None
    assert pl["name"] == "Premier League"

    # Upsert league
    l_id = db.upsert_league("New League", "Country", "new-league", 999, "NL", "NLL")
    assert l_id > 0
    nl = db.get_league("new-league")
    assert nl["name"] == "New League"

def test_team_operations(temp_db):
    db = temp_db
    l_id = db.upsert_league("Premier League", "England", "premier-league", 39)
    
    # Upsert team
    t_id = db.upsert_team("Arsenal", l_id, api_football_id=42, short_name="Ars", aliases=["Gunners"])
    assert t_id > 0

    team = db.get_team(t_id)
    assert team["name"] == "Arsenal"
    assert team["api_football_id"] == 42
    assert team["short_name"] == "Ars"

    # Get team by name
    team_by_name = db.find_team_by_name("Arsenal", l_id)
    assert team_by_name is not None
    assert team_by_name["id"] == t_id


def test_team_identity_survives_source_spellings(temp_db):
    """One club, many spellings, one row.

    Regression cover for the split that gave the Premier League 53 team rows
    for a 20-team competition — half a club's history under each spelling.
    """
    db = temp_db
    l_id = db.upsert_league("Premier League", "England", "premier-league", 39)

    csv_id = db.upsert_team("Man United", l_id)
    fdo_id = db.upsert_team("Manchester United FC", l_id, fdo_id=66)
    api_id = db.upsert_team("Manchester United", l_id, api_football_id=33)

    assert csv_id == fdo_id == api_id, "club split across rows"

    team = db.get_team(csv_id)
    # The fullest spelling wins the display name; the rest survive as aliases.
    assert team["name"] == "Manchester United FC"
    assert "Man United" in team["aliases"]
    # Source ids collected along the way are retained, not overwritten.
    assert team["fdo_id"] == 66
    assert team["api_football_id"] == 33


def test_distinct_clubs_stay_distinct(temp_db):
    """Canonicalisation must not over-merge genuinely different clubs."""
    db = temp_db
    l_id = db.upsert_league("Premier League", "England", "premier-league", 39)

    ids = {
        db.upsert_team(name, l_id)
        for name in ("Manchester United", "Manchester City",
                     "Sheffield United", "Sheffield Wednesday")
    }
    assert len(ids) == 4

def test_match_operations(temp_db):
    db = temp_db
    l_id = db.upsert_league("Premier League", "England", "premier-league", 39)
    home_id = db.upsert_team("Arsenal", l_id, api_football_id=42)
    away_id = db.upsert_team("Chelsea", l_id, api_football_id=49)

    match_id = db.upsert_match(
        league_id=l_id,
        season="2025",
        date="2025-06-20",
        home_team_id=home_id,
        away_team_id=away_id,
        api_football_id=12345,
        status="SCHEDULED"
    )
    assert match_id > 0

    match = db.get_match(match_id)
    assert match is not None
    assert match["home_team_name"] == "Arsenal"
    assert match["away_team_name"] == "Chelsea"
    assert match["status"] == "SCHEDULED"

def test_cache_operations(temp_db):
    db = temp_db
    
    cache_key = "some_deterministic_key"
    data = {"result": "ok"}
    
    db.cache_response(cache_key, "https://api.example.com", "api_football", data, ttl_seconds=60)
    
    # Get cache
    cached = db.get_cached_response(cache_key)
    assert cached == data

def test_api_usage_operations(temp_db):
    db = temp_db
    
    # Initially 0 requests tracked today
    usage = db.get_api_usage_today("api_football")
    assert usage["requests_made"] == 0

    # Track requests
    db.increment_api_usage("api_football", limit=100)
    usage = db.get_api_usage_today("api_football")
    assert usage["requests_made"] == 1
    assert usage["requests_limit"] == 100

    # Check remaining quota
    remaining = db.get_remaining_quota("api_football", daily_limit=100)
    assert remaining == 99
