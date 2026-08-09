"""Tests for standings derived from stored match results.

Derivation is how the six tables a blank pre-season fetch overwrote come
back: every source match survived, so the table is recoverable arithmetic.
The write path is therefore guarded twice over — a derived table that ranks
nothing, or one whose match set is internally inconsistent, must not be
stored, because storing it recreates the condition being repaired.
"""

import pytest

from src.analysis.factors import standings_are_ranked
from src.database import Database
from src.standings import (
    compute_standings,
    derivable_league_seasons,
    incoherent_reason,
    store_derived_standings,
)


@pytest.fixture
def league(tmp_path):
    """A four-team league, ready for fixtures."""
    db = Database(str(tmp_path / "test_derive.db"))
    league_id = db.upsert_league("Premier League", "England", "premier-league", 39)
    teams = {
        name: db.upsert_team(name, league_id)
        for name in ("Arsenal FC", "Chelsea FC", "Liverpool FC", "Everton FC")
    }
    return db, league_id, teams


def _play(db, league_id, teams, home, away, hs, as_, date, season="2025-26",
          status="FINISHED"):
    return db.upsert_match(
        league_id=league_id, season=season, date=date,
        home_team_id=teams[home], away_team_id=teams[away],
        status=status,
        home_score=hs if status == "FINISHED" else None,
        away_score=as_ if status == "FINISHED" else None,
    )


def _full_round(db, league_id, teams, season="2025-26"):
    """A double round-robin: every pairing home and away, 12 matches."""
    names = list(teams)
    day = 1
    for first in names:
        for second in names:
            if first == second:
                continue
            _play(db, league_id, teams, first, second, 1, 0,
                  f"2025-09-{day:02d}T15:00:00Z", season=season)
            day += 1


# ── The arithmetic ────────────────────────────────────────────

def test_points_and_goals_come_from_the_results(league):
    db, league_id, teams = league
    _play(db, league_id, teams, "Arsenal FC", "Chelsea FC", 3, 1, "2025-08-16T15:00:00Z")
    _play(db, league_id, teams, "Liverpool FC", "Arsenal FC", 2, 2, "2025-08-23T15:00:00Z")
    _play(db, league_id, teams, "Everton FC", "Arsenal FC", 1, 0, "2025-08-30T15:00:00Z")

    table = compute_standings(db, league_id, "2025-26")
    arsenal = next(t for t in table if t["team_name"] == "Arsenal FC")

    assert arsenal["played"] == 3
    assert (arsenal["won"], arsenal["drawn"], arsenal["lost"]) == (1, 1, 1)
    assert arsenal["points"] == 4
    assert arsenal["goals_for"] == 5
    assert arsenal["goals_against"] == 4
    assert arsenal["goal_difference"] == 1


def test_ranking_is_points_then_goal_difference_then_goals_scored(league):
    db, league_id, teams = league
    # Arsenal and Chelsea both win once; Arsenal by more. Liverpool and
    # Everton lose those games, so they separate on goal difference too.
    _play(db, league_id, teams, "Arsenal FC", "Liverpool FC", 4, 0, "2025-08-16T15:00:00Z")
    _play(db, league_id, teams, "Chelsea FC", "Everton FC", 2, 1, "2025-08-16T17:00:00Z")

    table = compute_standings(db, league_id, "2025-26")
    assert [t["team_name"] for t in table] == [
        "Arsenal FC", "Chelsea FC", "Everton FC", "Liverpool FC",
    ]
    assert [t["position"] for t in table] == [1, 2, 3, 4]


def test_level_teams_break_on_goals_scored_then_name(league):
    db, league_id, teams = league
    # Two 2-2 draws: everyone on 1 point and GD 0. Arsenal/Chelsea scored 2,
    # so they outrank the pair that drew 0-0, and ties resolve by name.
    _play(db, league_id, teams, "Arsenal FC", "Chelsea FC", 2, 2, "2025-08-16T15:00:00Z")
    _play(db, league_id, teams, "Everton FC", "Liverpool FC", 0, 0, "2025-08-16T17:00:00Z")

    table = compute_standings(db, league_id, "2025-26")
    assert [t["team_name"] for t in table] == [
        "Arsenal FC", "Chelsea FC", "Everton FC", "Liverpool FC",
    ]


def test_home_and_away_splits_are_kept_apart(league):
    db, league_id, teams = league
    _play(db, league_id, teams, "Arsenal FC", "Chelsea FC", 3, 0, "2025-08-16T15:00:00Z")
    _play(db, league_id, teams, "Liverpool FC", "Arsenal FC", 1, 0, "2025-08-23T15:00:00Z")

    arsenal = next(
        t for t in compute_standings(db, league_id, "2025-26")
        if t["team_name"] == "Arsenal FC"
    )
    assert (arsenal["home_played"], arsenal["home_won"], arsenal["home_gf"]) == (1, 1, 3)
    assert (arsenal["away_played"], arsenal["away_lost"], arsenal["away_ga"]) == (1, 1, 1)


def test_form_is_the_last_five_oldest_first(league):
    db, league_id, teams = league
    outcomes = [(3, 0), (0, 1), (1, 1), (2, 0), (0, 2), (4, 1)]  # W L D W L W
    for i, (hs, as_) in enumerate(outcomes):
        _play(db, league_id, teams, "Arsenal FC", "Chelsea FC", hs, as_,
              f"2025-09-{i + 1:02d}T15:00:00Z")

    arsenal = next(
        t for t in compute_standings(db, league_id, "2025-26")
        if t["team_name"] == "Arsenal FC"
    )
    assert arsenal["form"] == "LDWLW", "form is not the last five in match order"


def test_a_team_with_no_finished_match_is_absent(league):
    """The table describes what was played, not who was registered."""
    db, league_id, teams = league
    _play(db, league_id, teams, "Arsenal FC", "Chelsea FC", 1, 0, "2025-08-16T15:00:00Z")

    names = {t["team_name"] for t in compute_standings(db, league_id, "2025-26")}
    assert names == {"Arsenal FC", "Chelsea FC"}


def test_scheduled_matches_do_not_count(league):
    db, league_id, teams = league
    _play(db, league_id, teams, "Arsenal FC", "Chelsea FC", 1, 0, "2025-08-16T15:00:00Z")
    _play(db, league_id, teams, "Arsenal FC", "Everton FC", None, None,
          "2026-05-16T15:00:00Z", status="SCHEDULED")

    arsenal = next(
        t for t in compute_standings(db, league_id, "2025-26")
        if t["team_name"] == "Arsenal FC"
    )
    assert arsenal["played"] == 1


def test_only_the_requested_season_is_counted(league):
    """The season comes from the matches, so a neighbouring one must not leak."""
    db, league_id, teams = league
    _play(db, league_id, teams, "Arsenal FC", "Chelsea FC", 1, 0, "2025-08-16T15:00:00Z")
    _play(db, league_id, teams, "Arsenal FC", "Chelsea FC", 5, 0, "2026-08-15T15:00:00Z",
          season="2026-27")

    old = compute_standings(db, league_id, "2025-26")
    new = compute_standings(db, league_id, "2026-27")
    assert next(t for t in old if t["team_name"] == "Arsenal FC")["goals_for"] == 1
    assert next(t for t in new if t["team_name"] == "Arsenal FC")["goals_for"] == 5


def test_a_season_with_no_matches_derives_nothing(league):
    db, league_id, _ = league
    assert compute_standings(db, league_id, "2026-27") == []


# ── Coherence of the match set underneath ─────────────────────

def test_a_club_split_across_two_rows_is_incoherent(league):
    """The La Liga 2025-26 signature: ``CA Osasuna`` and ``Osasuna``.

    Every fixture involving the duplicate is stored twice, once per spelling,
    so its opponents finish the season on more games than the calendar
    allows. The tell is that the two rows never meet each other — no real
    pair of clubs finishes a complete season without playing.
    """
    db, league_id, teams = league
    _full_round(db, league_id, teams)
    dupe = db.upsert_team("Everton Football Club 1878", league_id)
    for i, opponent in enumerate(("Arsenal FC", "Chelsea FC", "Liverpool FC")):
        db.upsert_match(
            league_id=league_id, season="2025-26",
            date=f"2025-10-{i + 1:02d}T15:00:00Z",
            home_team_id=dupe, away_team_id=teams[opponent],
            status="FINISHED", home_score=1, away_score=0,
        )

    table = compute_standings(db, league_id, "2025-26")
    reason = incoherent_reason(db, league_id, "2025-26", table)
    assert reason is not None
    assert "never met" in reason
    assert "Everton" in reason, "the reason should name the pair that never met"


def test_a_complete_season_where_everyone_met_is_coherent(league):
    db, league_id, teams = league
    _full_round(db, league_id, teams)
    table = compute_standings(db, league_id, "2025-26")
    assert {t["played"] for t in table} == {6}
    assert incoherent_reason(db, league_id, "2025-26", table) is None


def test_split_format_unevenness_is_not_flagged(league):
    """The Belgian Pro League finishes a season with teams on different totals.

    Championship and relegation playoffs replay part of the field, so played
    counts diverge legitimately. Every pair has still met, which is why the
    check is on pairings rather than on the more obvious count spread.
    """
    db, league_id, teams = league
    _full_round(db, league_id, teams)
    # A playoff round between the top two, on top of the completed round-robin.
    _play(db, league_id, teams, "Arsenal FC", "Chelsea FC", 2, 1, "2026-05-10T15:00:00Z")
    _play(db, league_id, teams, "Chelsea FC", "Arsenal FC", 0, 0, "2026-05-17T15:00:00Z")

    table = compute_standings(db, league_id, "2025-26")
    assert len({t["played"] for t in table}) > 1, "playoff did not skew the counts"
    assert incoherent_reason(db, league_id, "2025-26", table) is None


def test_mid_season_gaps_are_not_flagged(league):
    """Pairs that have not met yet are the normal state of a part-played season."""
    db, league_id, teams = league
    _play(db, league_id, teams, "Arsenal FC", "Chelsea FC", 1, 0, "2025-08-16T15:00:00Z")
    _play(db, league_id, teams, "Liverpool FC", "Everton FC", 1, 0, "2025-08-16T17:00:00Z")
    _play(db, league_id, teams, "Arsenal FC", "Liverpool FC", 2, 1, "2025-08-23T15:00:00Z")
    _play(db, league_id, teams, "Chelsea FC", "Everton FC", None, None,
          "2026-05-16T15:00:00Z", status="SCHEDULED")

    table = compute_standings(db, league_id, "2025-26")
    assert incoherent_reason(db, league_id, "2025-26", table) is None


# ── The write path ────────────────────────────────────────────

def test_a_derived_table_is_stored_with_its_ranking(league):
    db, league_id, teams = league
    _play(db, league_id, teams, "Arsenal FC", "Chelsea FC", 3, 0, "2025-08-16T15:00:00Z")
    _play(db, league_id, teams, "Liverpool FC", "Everton FC", 1, 0, "2025-08-16T17:00:00Z")
    _play(db, league_id, teams, "Chelsea FC", "Everton FC", None, None,
          "2026-05-16T15:00:00Z", status="SCHEDULED")

    assert store_derived_standings(db, league_id, "2025-26") == 4

    stored = db.get_standings(league_id, "2025-26")
    assert [s["position"] for s in stored] == [1, 2, 3, 4]
    top = stored[0]
    assert top["team_name"] == "Arsenal FC"
    assert (top["points"], top["goal_difference"], top["form"]) == (3, 3, "W")
    assert top["home_played"] == 1


def test_an_empty_derivation_is_not_written(league):
    """No matches means no table — and writing one would rank nothing."""
    db, league_id, _ = league
    assert store_derived_standings(db, league_id, "2026-27") == 0
    assert db.get_standings(league_id, "2026-27") == []


def test_an_incoherent_table_is_declined_by_default(league):
    db, league_id, teams = league
    _full_round(db, league_id, teams)
    dupe = db.upsert_team("Everton Football Club 1878", league_id)
    db.upsert_match(
        league_id=league_id, season="2025-26", date="2025-10-01T15:00:00Z",
        home_team_id=dupe, away_team_id=teams["Arsenal FC"],
        status="FINISHED", home_score=1, away_score=0,
    )

    assert store_derived_standings(db, league_id, "2025-26") == 0
    assert db.get_standings(league_id, "2025-26") == []

    written = store_derived_standings(db, league_id, "2025-26", force=True)
    assert written == 5, "force must still write the table it warned about"


def test_stale_rows_are_dropped_rather_than_left_behind(league):
    """A rewrite replaces the old table; it does not merge with it.

    The Premier League 2025-26 table held 23 rows for a 20-team competition,
    relegated clubs left by an earlier fetch. Writing 20 derived rows over
    that leaves three at position 1 — a duplicate position, so the repaired
    table would still fail ``standings_are_ranked`` and the whole exercise
    would accomplish nothing.
    """
    db, league_id, teams = league
    departed = db.upsert_team("Ipswich Town FC", league_id)
    db.upsert_standing(
        league_id=league_id, season="2025-26", team_id=departed, position=1, played=0,
    )
    for name in teams:
        db.upsert_standing(
            league_id=league_id, season="2025-26", team_id=teams[name],
            position=1, played=0,
        )

    _play(db, league_id, teams, "Arsenal FC", "Chelsea FC", 3, 0, "2025-08-16T15:00:00Z")
    _play(db, league_id, teams, "Liverpool FC", "Everton FC", 1, 0, "2025-08-16T17:00:00Z")
    _play(db, league_id, teams, "Chelsea FC", "Everton FC", None, None,
          "2026-05-16T15:00:00Z", status="SCHEDULED")

    assert store_derived_standings(db, league_id, "2025-26") == 4

    stored = db.get_standings(league_id, "2025-26")
    assert departed not in {s["team_id"] for s in stored}, "stale row survived"
    assert [s["position"] for s in stored] == [1, 2, 3, 4]
    assert standings_are_ranked(stored), "repaired table still does not rank"


def test_an_already_ranked_table_is_not_replaced(league):
    """A fetched table outranks a derived one, so it takes an explicit ask.

    The stored table carries a form string and home/away splits derivation
    only approximates, and it may cover teams whose matches were never
    stored — which the prune above would then delete.
    """
    db, league_id, teams = league
    db.upsert_standing(
        league_id=league_id, season="2025-26", team_id=teams["Arsenal FC"],
        position=1, played=38, won=26, drawn=7, lost=5,
        goals_for=85, goals_against=34, goal_difference=51, points=85,
    )
    _play(db, league_id, teams, "Chelsea FC", "Liverpool FC", 1, 0, "2025-08-16T15:00:00Z")
    _play(db, league_id, teams, "Chelsea FC", "Everton FC", None, None,
          "2026-05-16T15:00:00Z", status="SCHEDULED")

    assert store_derived_standings(db, league_id, "2025-26") == 0

    arsenal = next(
        s for s in db.get_standings(league_id, "2025-26")
        if s["team_id"] == teams["Arsenal FC"]
    )
    assert arsenal["played"] == 38, "derivation overwrote a ranked table"

    assert store_derived_standings(db, league_id, "2025-26", force=True) == 2
    assert teams["Arsenal FC"] not in {
        s["team_id"] for s in db.get_standings(league_id, "2025-26")
    }, "force did not replace the table"


# ── Finding the work ──────────────────────────────────────────

def test_derivable_seasons_flag_the_ones_needing_a_table(league):
    db, league_id, teams = league
    _play(db, league_id, teams, "Arsenal FC", "Chelsea FC", 1, 0, "2025-08-16T15:00:00Z")
    _play(db, league_id, teams, "Arsenal FC", "Chelsea FC", 2, 0, "2026-08-15T15:00:00Z",
          season="2026-27")
    store_derived_standings(db, league_id, "2026-27")

    found = {row["season"]: row for row in derivable_league_seasons(db)}
    assert found["2025-26"]["needs_table"] is True
    assert found["2025-26"]["finished"] == 1
    assert found["2026-27"]["needs_table"] is False, "a ranked table still needs one"
    assert found["2026-27"]["slug"] == "premier-league"
