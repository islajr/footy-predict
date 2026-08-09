"""
Footy Predict — Derived League Tables

Computes a league table from the ``matches`` already in the database rather
than fetching one. Two jobs:

1. Coverage. Standings exist only for the league-seasons some API was asked
   for; the rest have complete match results and no table at all, which
   leaves ``league_position`` and ``opponent_strength`` reading nothing.
2. Repair. A blank pre-season fetch overwrote six real tables (see the
   ``upsert_standing`` guard and ``HANDOFF.md``). Every source match
   survived, so derivation is how those tables come back.

Nothing here writes without checking first. A derived table is only as good
as the match set under it, and this module refuses two ways it can be wrong:

- ``standings_are_ranked`` must pass. A table where every team sits at
  position 1 means the match query returned nothing, and writing it would
  recreate exactly the condition the repair exists to remove.
- Once a season has no fixtures left to play, every pair of teams in it
  must have met. A club stored under two ``teams`` rows is the pair that
  never can — ``CA Osasuna`` and ``Osasuna`` are one club, which is why La
  Liga 2025-26 derives 21 teams with 19 of them on 40 games of a 38-game
  season. Deriving from that produces a confidently wrong table, so it is
  declined.

The season a table belongs to is the season its *matches* carry. Callers
must not take it from an existing standings row — that label is the thing
under suspicion.
"""

from __future__ import annotations

import logging
from itertools import combinations
from typing import Any

from src.analysis.factors import standings_are_ranked
from src.database import Database

logger = logging.getLogger(__name__)

FORM_LENGTH = 5


def compute_standings(
    db: Database, league_id: int, season: str
) -> list[dict[str, Any]]:
    """Derive a ranked league table from this season's FINISHED matches.

    Returns one row per team appearing in at least one finished fixture,
    ordered by position, with the home/away splits and the last-five form
    string filled in. A team with no finished match is absent rather than
    present on zero — the table describes what was played.

    Ranking is points, then goal difference, then goals scored, then name.
    Several leagues break ties on head-to-head instead, so two level teams
    can sit a place either way of their official order; the factors reading
    this compare positions and points gaps, where that is immaterial.
    """
    with db._connect() as conn:
        rows = conn.execute(
            """SELECT m.home_team_id, m.away_team_id,
                      m.home_score, m.away_score,
                      h.name AS home_name, a.name AS away_name
               FROM matches m
               JOIN teams h ON h.id = m.home_team_id
               JOIN teams a ON a.id = m.away_team_id
               WHERE m.league_id = ? AND m.season = ?
                 AND m.status = 'FINISHED'
                 AND m.home_score IS NOT NULL AND m.away_score IS NOT NULL
               ORDER BY m.date""",
            (league_id, season),
        ).fetchall()

    table: dict[int, dict[str, Any]] = {}
    results: dict[int, list[str]] = {}

    def entry(team_id: int, name: str) -> dict[str, Any]:
        if team_id not in table:
            table[team_id] = {
                "team_id": team_id, "team_name": name, "position": 0,
                "played": 0, "won": 0, "drawn": 0, "lost": 0,
                "goals_for": 0, "goals_against": 0,
                "goal_difference": 0, "points": 0, "form": None,
                "home_played": 0, "home_won": 0, "home_drawn": 0,
                "home_lost": 0, "home_gf": 0, "home_ga": 0,
                "away_played": 0, "away_won": 0, "away_drawn": 0,
                "away_lost": 0, "away_gf": 0, "away_ga": 0,
            }
            results[team_id] = []
        return table[team_id]

    for row in rows:
        home_goals, away_goals = row["home_score"], row["away_score"]
        sides = (
            (entry(row["home_team_id"], row["home_name"]), "home", home_goals, away_goals),
            (entry(row["away_team_id"], row["away_name"]), "away", away_goals, home_goals),
        )
        for team, venue, scored, conceded in sides:
            if scored > conceded:
                outcome, points = "won", 3
            elif scored < conceded:
                outcome, points = "lost", 0
            else:
                outcome, points = "drawn", 1

            team["played"] += 1
            team[outcome] += 1
            team["points"] += points
            team["goals_for"] += scored
            team["goals_against"] += conceded
            team[f"{venue}_played"] += 1
            team[f"{venue}_{outcome}"] += 1
            team[f"{venue}_gf"] += scored
            team[f"{venue}_ga"] += conceded
            results[team["team_id"]].append(outcome[0].upper())

    ranked = list(table.values())
    for team in ranked:
        team["goal_difference"] = team["goals_for"] - team["goals_against"]
        # Oldest first across the last five, the order a fetched form string
        # arrives in and the one ``factors._form_score`` weights.
        team["form"] = "".join(results[team["team_id"]][-FORM_LENGTH:]) or None

    ranked.sort(
        key=lambda t: (-t["points"], -t["goal_difference"], -t["goals_for"], t["team_name"])
    )
    for position, team in enumerate(ranked, start=1):
        team["position"] = position
    return ranked


def incoherent_reason(
    db: Database, league_id: int, season: str, table: list[dict[str, Any]]
) -> str | None:
    """Return why a derived table cannot be trusted, or None if it can.

    One check, applied only once the season has no fixtures left to play: by
    then every pair of teams in the table must have met at least once. A
    club stored under two ``teams`` rows is precisely the pair that never
    can — ``CA Osasuna`` and ``Osasuna`` are one club, so La Liga 2025-26
    derives 21 teams with 19 of them on 40 games of a 38-game season, and a
    table built on that is confidently wrong in every position.

    Unequal played counts are the more obvious symptom but the wrong test:
    split-format leagues (the Belgian Pro League's championship and
    relegation playoffs) legitimately finish a season with teams on
    different totals. Those seasons pass here, and the two 2025-26 identity
    splits still fail.

    Mid-season nothing is checked. Pairs that have not met yet are the
    normal state of an incomplete fixture list.
    """
    if not table:
        return None

    with db._connect() as conn:
        remaining = conn.execute(
            """SELECT COUNT(*) FROM matches
               WHERE league_id = ? AND season = ? AND status != 'FINISHED'""",
            (league_id, season),
        ).fetchone()[0]
        if remaining:
            return None

        met = {
            frozenset((row["home_team_id"], row["away_team_id"]))
            for row in conn.execute(
                """SELECT home_team_id, away_team_id FROM matches
                   WHERE league_id = ? AND season = ? AND status = 'FINISHED'
                     AND home_score IS NOT NULL AND away_score IS NOT NULL""",
                (league_id, season),
            )
        }

    names = {team["team_id"]: team["team_name"] for team in table}
    never = [
        (names[a], names[b])
        for a, b in combinations(sorted(names), 2)
        if frozenset((a, b)) not in met
    ]
    if never:
        shown = ", ".join(f"{a} / {b}" for a, b in never[:3])
        more = f" (+{len(never) - 3} more)" if len(never) > 3 else ""
        return (
            f"season is complete yet {len(never)} pair(s) of teams never met — "
            f"{shown}{more}; likely one club stored under two rows"
        )
    return None


def store_derived_standings(
    db: Database, league_id: int, season: str, force: bool = False
) -> int:
    """Derive a table and write it, replacing whatever was stored before.

    Returns the number of rows written, or 0 if the write was declined.
    Three things stop it, all logged, all overridden by ``force``:

    - The derivation ranks nothing. Writing it recreates the every-team-at-
      position-1 condition this whole repair exists to remove. Not
      overridable — there is no reading under which that write is wanted.
    - The match set is incoherent (see ``incoherent_reason``).
    - A table is already stored *and* already ranks. That one is real data
      from an API, carrying a form string and home/away splits the
      derivation only approximates, and it may cover teams whose matches
      were never stored. Deriving over it is a downgrade, so it needs to be
      asked for.

    The write replaces rather than merges: rows for teams outside the
    derived table are deleted first. The Premier League 2025-26 table held
    three relegated clubs on top of its twenty, and leaving them behind at
    position 1 would keep the whole table failing ``standings_are_ranked``
    however correct the twenty rows written over them were.
    """
    table = compute_standings(db, league_id, season)

    if not standings_are_ranked(table):
        logger.warning(
            "Derived standings for league %s %s carry no ranking (%d rows) — not stored",
            league_id, season, len(table),
        )
        return 0

    if standings_are_ranked(db.get_standings(league_id, season)) and not force:
        logger.info(
            "League %s %s already has a ranked table — not replacing it with a "
            "derived one (use force to override)",
            league_id, season,
        )
        return 0

    reason = incoherent_reason(db, league_id, season, table)
    if reason:
        if not force:
            logger.warning(
                "Derived standings for league %s %s look wrong: %s — not stored",
                league_id, season, reason,
            )
            return 0
        logger.warning(
            "Derived standings for league %s %s look wrong: %s — stored anyway (force)",
            league_id, season, reason,
        )

    removed = db.delete_standings_except(
        league_id, season, [team["team_id"] for team in table]
    )
    if removed:
        logger.info(
            "Dropped %d standings row(s) for league %s %s not in the derived table",
            removed, league_id, season,
        )

    for team in table:
        db.upsert_standing(
            league_id=league_id,
            season=season,
            team_id=team["team_id"],
            position=team["position"],
            played=team["played"],
            won=team["won"],
            drawn=team["drawn"],
            lost=team["lost"],
            goals_for=team["goals_for"],
            goals_against=team["goals_against"],
            goal_difference=team["goal_difference"],
            points=team["points"],
            form=team["form"],
            home_played=team["home_played"],
            home_won=team["home_won"],
            home_drawn=team["home_drawn"],
            home_lost=team["home_lost"],
            home_gf=team["home_gf"],
            home_ga=team["home_ga"],
            away_played=team["away_played"],
            away_won=team["away_won"],
            away_drawn=team["away_drawn"],
            away_lost=team["away_lost"],
            away_gf=team["away_gf"],
            away_ga=team["away_ga"],
        )
    logger.info(
        "Derived standings for league %s %s: %d teams", league_id, season, len(table)
    )
    return len(table)


def derivable_league_seasons(db: Database) -> list[dict[str, Any]]:
    """League-seasons with finished matches, and whether a table already ranks them.

    Ordered newest season first. ``needs_table`` marks the ones a plain
    ``derive-standings`` run should target: no stored standings at all, or
    stored standings that rank nothing.
    """
    with db._connect() as conn:
        rows = conn.execute(
            """SELECT m.league_id, m.season, l.slug, COUNT(*) AS finished
               FROM matches m
               JOIN leagues l ON l.id = m.league_id
               WHERE m.status = 'FINISHED'
                 AND m.home_score IS NOT NULL AND m.away_score IS NOT NULL
               GROUP BY m.league_id, m.season
               ORDER BY m.season DESC, l.slug""",
        ).fetchall()

    out = []
    for row in rows:
        stored = db.get_standings(row["league_id"], row["season"])
        out.append({
            "league_id": row["league_id"],
            "slug": row["slug"],
            "season": row["season"],
            "finished": row["finished"],
            "stored": len(stored),
            "needs_table": not standings_are_ranked(stored),
        })
    return out
