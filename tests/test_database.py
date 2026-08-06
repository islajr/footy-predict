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


def _fixture_db():
    """A league with two teams, for match identity tests."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = Database(path)
    l_id = db.upsert_league("Premier League", "England", "premier-league", 39)
    home = db.upsert_team("Arsenal", l_id)
    away = db.upsert_team("Chelsea", l_id)
    return db, path, l_id, home, away


def test_refetching_a_fixture_does_not_duplicate_it():
    """Re-running footy fetch must update the row, not add another.

    Regression cover for ON CONFLICT(api_football_id): the column is nullable
    and SQLite treats every NULL as distinct, so CSV and football-data.org
    rows never conflicted. 19911 match rows held 11609 duplicates.
    """
    db, path, l_id, home, away = _fixture_db()
    try:
        first = db.upsert_match(l_id, "2026-27", "2026-08-15T14:00:00Z",
                                home, away, source="football_data_org")
        for _ in range(5):
            again = db.upsert_match(l_id, "2026-27", "2026-08-15T14:00:00Z",
                                    home, away, source="football_data_org")
            assert again == first

        with db._connect() as conn:
            assert conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 1
    finally:
        os.unlink(path)


def test_csv_and_api_rows_converge_on_one_fixture():
    """The same fixture from two sources is one row, with both sources' fields.

    The CSV loader stores midnight; football-data.org stores the real kickoff.
    Same match — the time must not split it, and the precise time must win.
    """
    db, path, l_id, home, away = _fixture_db()
    try:
        fdo = db.upsert_match(l_id, "2026-27", "2026-08-15T14:00:00Z",
                              home, away, matchday=1, status="FINISHED",
                              home_score=2, away_score=1,
                              source="football_data_org")
        csv = db.upsert_match(l_id, "2026-27", "2026-08-15T00:00:00",
                              home, away, status="FINISHED",
                              home_score=2, away_score=1,
                              ht_home_score=1, ht_away_score=0,
                              referee="M Oliver", source="csv")
        assert fdo == csv, "same fixture split across rows"

        match = db.get_match(fdo)
        assert match["date"] == "2026-08-15T14:00:00Z", "midnight clobbered kickoff"
        assert match["matchday"] == 1        # from the FDO row
        assert match["referee"] == "M Oliver"  # from the CSV row
        assert match["ht_home_score"] == 1
    finally:
        os.unlink(path)


def test_date_revision_updates_rather_than_duplicates():
    """A revised kickoff date for the same matchday updates the existing row.

    football-data.org publishes a provisional kickoff and revises it later,
    sometimes by a day. The fixture is the same; the stored date must move.
    """
    db, path, l_id, home, away = _fixture_db()
    try:
        first = db.upsert_match(l_id, "2026-27", "2026-08-22T18:00:00Z",
                                home, away, matchday=1, source="football_data_org")
        revised = db.upsert_match(l_id, "2026-27", "2026-08-23T20:45:00Z",
                                  home, away, matchday=1, source="football_data_org")
        assert first == revised, "date revision split the fixture"

        match = db.get_match(first)
        assert match["date"] == "2026-08-23T20:45:00Z", "stored date did not update"

        with db._connect() as conn:
            assert conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 1
    finally:
        os.unlink(path)


def test_split_format_repeat_stays_separate():
    """A different calendar date is a different fixture.

    Split-format leagues (Scottish Premiership, Belgian Pro League) repeat a
    home/away pairing within one season, so the date must stay in the key.
    """
    db, path, l_id, home, away = _fixture_db()
    try:
        oct_match = db.upsert_match(l_id, "2026-27", "2026-10-19T15:00:00Z",
                                    home, away, matchday=10, home_score=2, away_score=2)
        feb_match = db.upsert_match(l_id, "2026-27", "2027-02-25T15:00:00Z",
                                    home, away, matchday=33, home_score=5, away_score=1)
        assert oct_match != feb_match
    finally:
        os.unlink(path)


def test_csv_with_no_matchday_still_deduplicates_by_date():
    """CSV rows carry no matchday, so they fall through to the date key.

    Covered separately from the two-source merge above because the matchday
    index is partial: a row without one must still meet a row that has one.
    """
    db, path, l_id, home, away = _fixture_db()
    try:
        csv = db.upsert_match(l_id, "2026-27", "2026-09-19T00:00:00",
                              home, away, status="FINISHED",
                              home_score=1, away_score=1, source="csv")
        again = db.upsert_match(l_id, "2026-27", "2026-09-19T00:00:00",
                                home, away, status="FINISHED",
                                home_score=1, away_score=1, source="csv")
        assert csv == again, "matchday-less rows stopped deduplicating"

        with db._connect() as conn:
            assert conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 1
    finally:
        os.unlink(path)


def test_late_scheduled_fetch_cannot_unfinish_a_result():
    """A recorded result outranks a stale SCHEDULED row from a re-fetch."""
    db, path, l_id, home, away = _fixture_db()
    try:
        mid = db.upsert_match(l_id, "2026-27", "2026-08-15T14:00:00Z",
                              home, away, status="FINISHED",
                              home_score=3, away_score=0)
        db.upsert_match(l_id, "2026-27", "2026-08-15T14:00:00Z",
                        home, away, status="SCHEDULED")

        match = db.get_match(mid)
        assert match["status"] == "FINISHED"
        assert match["home_score"] == 3
    finally:
        os.unlink(path)

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
