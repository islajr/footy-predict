"""Tests for Football-Data.org standings labelling and staleness detection.

The bug these cover: ``get_standings`` sent no season parameter and discarded
the response's own ``season`` block, while the caller labelled the table from
today's date. Across the July–August 2026 turnover the two diverged by a full
season in both directions, and the mislabelled blank table then overwrote real
final tables.

The payload shapes here are taken from the responses cached in ``footy.db`` on
2026-08-03, including the played-game totals that make four of them stale.
"""

import pytest
from datetime import date
from unittest.mock import patch

from src.database import Database
from src.fetchers.football_data_org import (
    FootballDataOrgClient,
    _season_start_year,
    _stale_table_reason,
)


@pytest.fixture
def temp_db(tmp_path):
    db = Database(str(tmp_path / "test_fdo.db"))
    db.seed_leagues()
    return db


def _table(n_teams: int, played_each: int) -> list[dict]:
    """Build a standings table with every team on the same played count.

    Real club names, not "Team 1"…"Team 20": ``canonical_key`` strips bare
    digit tokens, so numbered placeholders all collapse onto one team row.
    """
    names = [
        "Arsenal FC", "Chelsea FC", "Liverpool FC", "Everton FC",
        "Manchester City FC", "Manchester United FC", "Tottenham Hotspur FC",
        "Aston Villa FC", "Newcastle United FC", "Brighton & Hove Albion FC",
        "West Ham United FC", "Crystal Palace FC", "Fulham FC", "Brentford FC",
        "Nottingham Forest FC", "AFC Bournemouth", "Wolverhampton Wanderers FC",
        "Burnley FC", "Leeds United FC", "Sunderland AFC",
    ]
    assert n_teams <= len(names), "extend the name list for a larger table"
    return [
        {
            "position": i + 1,
            "team": {"id": 100 + i, "name": names[i], "shortName": names[i].split()[0]},
            "playedGames": played_each,
            "won": played_each, "draw": 0, "lost": 0,
            "goalsFor": played_each * 2, "goalsAgainst": 0,
            "goalDifference": played_each * 2, "points": played_each * 3,
        }
        for i in range(n_teams)
    ]


# ── Season block parsing ──────────────────────────────────────

def test_season_start_year_reads_start_date():
    assert _season_start_year({"startDate": "2026-08-21", "endDate": "2027-05-30"}) == 2026
    assert _season_start_year({"startDate": "2025-08-15"}) == 2025


def test_season_start_year_returns_none_when_unusable():
    """No season block means the caller cannot verify — it must not guess."""
    assert _season_start_year({}) is None
    assert _season_start_year({"startDate": None}) is None
    assert _season_start_year({"startDate": "n/a"}) is None


# ── Staleness detection ───────────────────────────────────────

def test_stale_when_season_starts_in_the_future():
    """The Premier League response cached 2026-08-03: 760 played, not yet started."""
    reason = _stale_table_reason(
        _table(20, 38),
        {"startDate": "2026-08-21", "currentMatchday": 1},
        today=date(2026, 8, 3),
    )
    assert reason is not None
    assert "760 played games" in reason


def test_stale_when_matchday_cannot_account_for_games_played():
    """Liga Portugal on 2026-08-09: season started yesterday, 592 games recorded.

    The future-start check cannot catch this one — the season is nominally
    under way — so the matchday ceiling is what fires.
    """
    reason = _stale_table_reason(
        _table(18, 33),
        {"startDate": "2026-08-08", "currentMatchday": 1},
        today=date(2026, 8, 9),
    )
    assert reason is not None
    assert "matchday 1 cannot account for" in reason


def test_empty_preseason_table_is_not_stale():
    """Serie A cached the same day: 0 played under a future start. Consistent."""
    assert _stale_table_reason(
        _table(20, 0),
        {"startDate": "2026-08-23", "currentMatchday": 1},
        today=date(2026, 8, 3),
    ) is None


def test_mid_season_table_is_not_stale():
    """An ordinary in-progress table must pass untouched."""
    assert _stale_table_reason(
        _table(20, 12),
        {"startDate": "2026-08-21", "currentMatchday": 12},
        today=date(2026, 11, 15),
    ) is None


def test_rearranged_fixtures_get_one_matchday_of_headroom():
    """Postponements leave teams a game ahead of the nominal matchday."""
    assert _stale_table_reason(
        _table(20, 13),
        {"startDate": "2026-08-21", "currentMatchday": 12},
        today=date(2026, 11, 15),
    ) is None


def test_completed_season_is_not_stale_against_its_own_matchday():
    """A final table under its own final matchday is exactly what it claims."""
    assert _stale_table_reason(
        _table(20, 38),
        {"startDate": "2025-08-15", "currentMatchday": 38},
        today=date(2026, 6, 1),
    ) is None


def test_empty_table_is_not_stale():
    assert _stale_table_reason([], {"startDate": "2026-08-21", "currentMatchday": 1}) is None


# ── The write path ────────────────────────────────────────────

async def test_standings_labelled_from_response_not_calendar(temp_db):
    """On 2026-08-03 the calendar said 2026-27; the response said 2025-26."""
    client = FootballDataOrgClient(temp_db)
    season = {"startDate": "2025-08-15", "endDate": "2026-05-30", "currentMatchday": 38}

    with patch.object(
        FootballDataOrgClient, "get_standings",
        return_value=(_table(20, 38), season),
    ):
        count = await client.fetch_and_store_standings("premier-league")

    assert count == 20
    league_id = temp_db.get_league("premier-league")["id"]
    assert len(temp_db.get_standings(league_id, "2025-26")) == 20, \
        "table was not stored under the season the response described"
    assert temp_db.get_standings(league_id, "2026-27") == [], \
        "table leaked into the season the calendar guessed"


async def test_stale_response_is_declined(temp_db):
    """A completed table under a not-yet-started season must not be written.

    Not under either label: the correct old label would overwrite a derived
    table that is probably better than this one.
    """
    client = FootballDataOrgClient(temp_db)
    season = {"startDate": "2026-08-21", "endDate": "2027-05-30", "currentMatchday": 1}

    with patch.object(
        FootballDataOrgClient, "get_standings",
        return_value=(_table(20, 38), season),
    ):
        count = await client.fetch_and_store_standings("premier-league")

    assert count == 0
    league_id = temp_db.get_league("premier-league")["id"]
    assert temp_db.get_standings(league_id, "2026-27") == []
    assert temp_db.get_standings(league_id, "2025-26") == []


async def test_missing_season_block_is_declined(temp_db):
    """Unverifiable is not the same as current — decline rather than guess."""
    client = FootballDataOrgClient(temp_db)

    with patch.object(
        FootballDataOrgClient, "get_standings",
        return_value=(_table(20, 38), {}),
    ):
        count = await client.fetch_and_store_standings("premier-league")

    assert count == 0


async def test_preseason_table_still_stores(temp_db):
    """A genuinely empty new-season table is legitimate and must be written."""
    client = FootballDataOrgClient(temp_db)
    season = {"startDate": "2026-08-21", "endDate": "2027-05-30", "currentMatchday": 1}

    with patch.object(
        FootballDataOrgClient, "get_standings",
        return_value=(_table(20, 0), season),
    ):
        count = await client.fetch_and_store_standings("premier-league")

    assert count == 20
    league_id = temp_db.get_league("premier-league")["id"]
    assert len(temp_db.get_standings(league_id, "2026-27")) == 20
