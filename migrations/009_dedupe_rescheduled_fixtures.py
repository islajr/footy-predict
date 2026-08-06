"""
Migration 009 — Collapse fixtures duplicated by a kickoff-date revision

Migration 007 deduplicated on ``(league_id, season, home_team_id,
away_team_id, date(date))`` and 008 made that key a unique index. The date has
to be in the key: split-format leagues — Scottish Premiership, Belgian Pro
League — legitimately repeat a home/away pairing within one season, so the
pairing alone is not unique.

But a date is not stable. Football-data.org publishes a provisional kickoff
for an unplayed fixture and revises it later, sometimes by a day. The revised
row has a different key, so the upsert inserts instead of updating and the
fixture exists twice. The 2026/27 fixture lists carried 87 such pairs across
six leagues:

    Udinese v Como    2026-08-22 and 2026-08-23   both matchday 1
    Bologna v Lazio   2026-08-23 and 2026-08-24   both matchday 1

All 174 rows were SCHEDULED with no result and no dependents, and in every
group the higher id held the later ``updated_at`` — two fetches of the same
list, weeks apart. The revision moved the date forward in 41 groups and back
in 46, which is what ordinary rescheduling looks like.

Identity key
------------
``(league_id, season, home_team_id, away_team_id, matchday)``.

A home/away pairing occurs at most once per matchday, including in
split-format leagues where the repeat meeting falls in a later round. The key
holds no date, so a revision updates the row it belongs to. Migration 010 adds
it as a partial unique index over the rows that carry a matchday; rows without
one — every CSV load — keep the date-keyed identity from 008.

Merging
-------
The survivor and the field merge come from 007, whose ranking prefers the most
complete row and breaks ties to the lowest id so existing dependents stay put.
One rule is added here: an unplayed survivor adopts the most recently fetched
row's date, because that is the currently published kickoff. A fixture that
already has a result keeps its own date — that is the day it was played.

Run with ``--dry-run`` to print the plan without writing.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent
PROJECT_ROOT = MIGRATIONS_DIR.parent


def _load_007():
    """Import migration 007 for its merge machinery.

    The FK repointing in 007 resolves the dependent graph from the live schema
    and clears grandchildren in order; reimplementing it here to change one
    GROUP BY would be 200 lines of duplicated risk. The filename is not a valid
    identifier, so it loads by path.
    """
    path = MIGRATIONS_DIR / "007_dedupe_matches.py"
    spec = importlib.util.spec_from_file_location("_migration_007_dedupe_matches", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {path.name}")
    module = importlib.util.module_from_spec(spec)
    # @dataclass resolves type hints through sys.modules, and fails obscurely
    # when the defining module is absent.
    sys.modules.setdefault(spec.name, module)
    spec.loader.exec_module(module)
    return module


def _groups(conn: sqlite3.Connection, m007) -> list[sqlite3.Row]:
    """Duplicate groups under the matchday key, survivors already chosen."""
    return conn.execute(
        f"""
        SELECT
            league_id, season, home_team_id, away_team_id, matchday,
            COUNT(*) AS n,
            (SELECT id FROM matches m2
              WHERE m2.league_id    = matches.league_id
                AND m2.season       = matches.season
                AND m2.home_team_id = matches.home_team_id
                AND m2.away_team_id = matches.away_team_id
                AND m2.matchday     = matches.matchday
              {m007._SURVIVOR_RANK}
              LIMIT 1) AS survivor_id
        FROM matches
        WHERE matchday IS NOT NULL
        GROUP BY league_id, season, home_team_id, away_team_id, matchday
        HAVING n > 1
        """
    ).fetchall()


def _losers(conn: sqlite3.Connection, group: sqlite3.Row) -> list[int]:
    rows = conn.execute(
        """
        SELECT id FROM matches
        WHERE league_id = ? AND season = ? AND home_team_id = ?
          AND away_team_id = ? AND matchday = ? AND id != ?
        """,
        (group["league_id"], group["season"], group["home_team_id"],
         group["away_team_id"], group["matchday"], group["survivor_id"]),
    ).fetchall()
    return [r["id"] for r in rows]


def _current_kickoff(conn: sqlite3.Connection, survivor_id: int,
                     loser_ids: list[int]) -> str | None:
    """The date an unplayed survivor should adopt, or None to keep its own.

    The duplicate exists because the kickoff moved, so the newest row holds the
    date the fixture is actually scheduled for. A survivor with a result keeps
    its own date — a played match happened when it happened, whatever a later
    fixture-list fetch says.

    Read-only: the caller applies this after deleting the losers, since moving
    the survivor onto a date a loser still occupies violates the date-keyed
    identity index from migration 008.
    """
    survivor = conn.execute(
        "SELECT home_score, date, updated_at FROM matches WHERE id = ?",
        (survivor_id,),
    ).fetchone()
    if survivor["home_score"] is not None:
        return None

    placeholders = ", ".join("?" * len(loser_ids))
    newest = conn.execute(
        f"""SELECT date, updated_at FROM matches
            WHERE id IN ({placeholders})
            ORDER BY updated_at DESC, id DESC LIMIT 1""",
        loser_ids,
    ).fetchone()

    if newest["updated_at"] <= survivor["updated_at"]:
        return None
    if newest["date"] == survivor["date"]:
        return None
    return newest["date"]


def migrate(conn: sqlite3.Connection, dry_run: bool = False) -> None:
    """Collapse each rescheduled-fixture group onto one merged row."""
    conn.row_factory = sqlite3.Row
    m007 = _load_007()

    before = conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
    groups = _groups(conn, m007)

    if not groups:
        print("  No fixtures duplicated by a date revision — nothing to do.")
        return

    excess = sum(g["n"] - 1 for g in groups)
    print(f"  {len(groups)} rescheduled fixture(s), {excess} excess row(s).")

    refs = m007._build_ref_plan(conn)
    print(f"  dependents: {', '.join(f'{r.table}.{r.column}' for r in refs)}")

    if dry_run:
        print("\n  Sample of the merge plan:")
        for g in groups[:5]:
            dates = conn.execute(
                """SELECT GROUP_CONCAT(date(date), ' / ') FROM matches
                   WHERE league_id=? AND season=? AND home_team_id=?
                     AND away_team_id=? AND matchday=?""",
                (g["league_id"], g["season"], g["home_team_id"],
                 g["away_team_id"], g["matchday"]),
            ).fetchone()[0]
            print(f"    league={g['league_id']} season={g['season']} "
                  f"md={g['matchday']}  {dates}  "
                  f"{g['n']} copies → id={g['survivor_id']}")
        print(f"\n  DRY RUN — no changes written. "
              f"matches would go {before} → {before - excess}.")
        return

    dropped = 0
    rescheduled = 0
    for g in groups:
        loser_ids = _losers(conn, g)
        if not loser_ids:
            continue
        m007._fill_from_losers(conn, g["survivor_id"], loser_ids)
        kickoff = _current_kickoff(conn, g["survivor_id"], loser_ids)
        dropped += m007._repoint(conn, refs, g["survivor_id"], loser_ids)
        conn.execute(
            f"DELETE FROM matches WHERE id IN ({', '.join('?' * len(loser_ids))})",
            loser_ids,
        )
        # After the delete: the date the survivor is moving onto is the one a
        # loser held until a moment ago, and the date-keyed index from 008 does
        # not care that they are the same fixture.
        if kickoff is not None:
            conn.execute("UPDATE matches SET date = ? WHERE id = ?",
                         (kickoff, g["survivor_id"]))
            rescheduled += 1

    after = conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
    print(f"  matches: {before} → {after}")
    print(f"  {rescheduled} survivor(s) moved to the currently published kickoff.")
    if dropped:
        print(f"  {dropped} dependent row(s) dropped as duplicates of the "
              f"survivor's own.")


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    connection = sqlite3.connect(str(PROJECT_ROOT / "footy.db"))
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        migrate(connection, dry_run=dry)
        if dry:
            connection.rollback()
        else:
            connection.commit()
    finally:
        connection.close()
