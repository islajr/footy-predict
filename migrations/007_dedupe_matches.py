"""
Migration 007 — Collapse duplicate match rows

``upsert_match`` deduped with ``ON CONFLICT(api_football_id)``. That column is
UNIQUE but nullable, and SQLite treats every NULL as distinct, so the clause
never fired for the CSV and football-data.org loaders — neither of which
supplies an API-Football id. Every re-fetch inserted a fresh copy of the same
fixture.

Result: 19,911 match rows holding 11,609 duplicates, some fixtures repeated
eight times. Form, head-to-head and venue factors counted the same result once
per copy, so a team's "last 5" could be one match repeated.

Identity key
------------
``(league_id, season, home_team_id, away_team_id, date(date))``.

The calendar date is part of the key, not an accident. Split-format leagues —
Scottish Premiership, Belgian Pro League — legitimately repeat a home/away
pairing within one season, so keying on the pairing alone would delete real
matches. Only the *time* is dropped: CSV rows carry midnight while
football-data.org carries the true kickoff, and those are the same fixture.

Merging
-------
The survivor is the most complete row; every field it lacks is filled from the
copies via COALESCE, so the CSV row's referee and the FDO row's matchday both
survive on one record. Dependents are repointed before the losers are deleted.

The dependent tables are discovered from the live schema rather than hardcoded.
This database carries a ``predictions_v3`` table that the application never
writes to — migration 003 rebuilds ``predictions`` under that name and renames
it, and a second, partly-failed run left the intermediate behind. A hardcoded
list missed it and the delete failed on a foreign key. Reading the schema means
any such stray table is handled rather than tripped over.

Run with ``--dry-run`` to print the plan without writing.
"""

from __future__ import annotations

import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent


@dataclass(frozen=True)
class _Ref:
    """A table holding a foreign key onto matches(id)."""

    table: str
    column: str
    unique_with: tuple[str, ...]
    children: list[tuple[str, str]]


# Nullable columns worth recovering from a losing row.
_MERGE_COLS = (
    "api_football_id", "matchday", "home_score", "away_score",
    "ht_home_score", "ht_away_score", "referee", "venue",
)

# (table, column) pairs pointing at matches(id), with the columns that make a
# row unique so repointing cannot violate a constraint, and any grandchildren
# that must be cleared first — prediction_results has a NOT NULL FK onto
# predictions, so dropping a duplicate prediction without it fails.
_MATCH_REFS: tuple[tuple[str, str, tuple[str, ...], tuple[tuple[str, str], ...]], ...] = (
    ("match_stats", "match_id", ("team_id",), ()),
    ("predictions", "match_id", ("market", "pick"),
     (("prediction_results", "prediction_id"),)),
)


def _referencing_tables(
    conn: sqlite3.Connection, target: str
) -> list[tuple[str, str]]:
    """Every (table, column) in the live schema with an FK onto ``target``."""
    refs: list[tuple[str, str]] = []
    tables = [
        r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'"
        )
    ]
    for table in tables:
        for fk in conn.execute(f"PRAGMA foreign_key_list({table})"):
            if fk["table"] == target:
                refs.append((table, fk["from"]))
    return refs


def _unique_columns(
    conn: sqlite3.Connection, table: str, fk_column: str
) -> tuple[str, ...]:
    """Columns that, alongside ``fk_column``, form a unique constraint.

    Repointing a dependent onto the survivor collides when the survivor already
    owns a row with the same key, so those rows must be dropped instead. An
    empty result means no constraint applies and every row can be repointed.
    """
    for idx in conn.execute(f"PRAGMA index_list({table})"):
        if not idx["unique"]:
            continue
        cols = [
            r["name"] for r in conn.execute(f"PRAGMA index_info({idx['name']})")
        ]
        if fk_column in cols:
            return tuple(c for c in cols if c != fk_column)
    return ()


def _build_ref_plan(conn: sqlite3.Connection) -> list[_Ref]:
    """Resolve the full dependent graph for matches, children first."""
    plan: list[_Ref] = []
    for table, column in _referencing_tables(conn, "matches"):
        plan.append(
            _Ref(
                table=table,
                column=column,
                unique_with=_unique_columns(conn, table, column),
                children=_referencing_tables(conn, table),
            )
        )
    return plan

# Most complete row wins. FINISHED beats SCHEDULED; a real kickoff time beats
# the CSV loader's midnight placeholder; ties break to the lowest id so the
# oldest row — the one most likely to already carry dependents — survives.
_SURVIVOR_RANK = """
    ORDER BY
        (status = 'FINISHED')                      DESC,
        (home_score IS NOT NULL)                   DESC,
        (ht_home_score IS NOT NULL)                DESC,
        (matchday IS NOT NULL)                     DESC,
        (date NOT LIKE '%T00:00:00%')              DESC,
        (referee IS NOT NULL)                      DESC,
        (api_football_id IS NOT NULL)              DESC,
        id ASC
"""


def _groups(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Duplicate groups, each with its survivor and losers already chosen."""
    return conn.execute(
        f"""
        SELECT
            league_id, season, home_team_id, away_team_id, date(date) AS d,
            COUNT(*) AS n,
            (SELECT id FROM matches m2
              WHERE m2.league_id = matches.league_id
                AND m2.season = matches.season
                AND m2.home_team_id = matches.home_team_id
                AND m2.away_team_id = matches.away_team_id
                AND date(m2.date) = date(matches.date)
              {_SURVIVOR_RANK}
              LIMIT 1) AS survivor_id
        FROM matches
        GROUP BY league_id, season, home_team_id, away_team_id, date(date)
        HAVING n > 1
        """
    ).fetchall()


def _losers(conn: sqlite3.Connection, group: sqlite3.Row) -> list[int]:
    rows = conn.execute(
        """
        SELECT id FROM matches
        WHERE league_id = ? AND season = ? AND home_team_id = ?
          AND away_team_id = ? AND date(date) = ? AND id != ?
        """,
        (group["league_id"], group["season"], group["home_team_id"],
         group["away_team_id"], group["d"], group["survivor_id"]),
    ).fetchall()
    return [r["id"] for r in rows]


def _fill_from_losers(conn: sqlite3.Connection, survivor_id: int,
                      loser_ids: list[int]) -> None:
    """Backfill the survivor's NULL fields from its duplicates."""
    placeholders = ", ".join("?" * len(loser_ids))
    assignments = ", ".join(
        f"""{col} = COALESCE({col}, (
                SELECT {col} FROM matches
                WHERE id IN ({placeholders}) AND {col} IS NOT NULL
                LIMIT 1))"""
        for col in _MERGE_COLS
    )
    conn.execute(
        f"UPDATE matches SET {assignments} WHERE id = ?",
        (*(loser_ids * len(_MERGE_COLS)), survivor_id),
    )


def _repoint(conn: sqlite3.Connection, refs: list[_Ref], survivor_id: int,
             loser_ids: list[int]) -> int:
    """Move dependents onto the survivor, dropping would-be duplicates."""
    placeholders = ", ".join("?" * len(loser_ids))
    dropped = 0

    for ref in refs:
        if ref.unique_with:
            match_cols = " AND ".join(
                f"s.{c} = {ref.table}.{c}" for c in ref.unique_with
            )
            doomed = f"""
                SELECT id FROM {ref.table}
                WHERE {ref.column} IN ({placeholders})
                  AND EXISTS (
                      SELECT 1 FROM {ref.table} AS s
                      WHERE s.{ref.column} = ? AND {match_cols}
                  )
            """

            # Clear grandchildren before the rows they depend on disappear.
            for child_table, child_fk in ref.children:
                conn.execute(
                    f"DELETE FROM {child_table} WHERE {child_fk} IN ({doomed})",
                    (*loser_ids, survivor_id),
                )

            cur = conn.execute(
                f"DELETE FROM {ref.table} WHERE id IN ({doomed})",
                (*loser_ids, survivor_id),
            )
            dropped += cur.rowcount or 0

        conn.execute(
            f"UPDATE {ref.table} SET {ref.column} = ? "
            f"WHERE {ref.column} IN ({placeholders})",
            (survivor_id, *loser_ids),
        )

    return dropped


def migrate(conn: sqlite3.Connection, dry_run: bool = False) -> None:
    """Collapse each duplicate fixture group onto one merged row."""
    conn.row_factory = sqlite3.Row

    before = conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
    groups = _groups(conn)

    if not groups:
        print("  No duplicate matches found — nothing to do.")
        return

    excess = sum(g["n"] - 1 for g in groups)
    print(f"  {len(groups)} duplicated fixture(s), {excess} excess row(s).")

    refs = _build_ref_plan(conn)
    print(f"  dependents: {', '.join(f'{r.table}.{r.column}' for r in refs)}")

    if dry_run:
        print("\n  Sample of the merge plan:")
        for g in groups[:5]:
            print(f"    {g['d']}  league={g['league_id']} season={g['season']}  "
                  f"{g['n']} copies → id={g['survivor_id']}")
        print(f"\n  DRY RUN — no changes written. "
              f"matches would go {before} → {before - excess}.")
        return

    dropped = 0
    for g in groups:
        loser_ids = _losers(conn, g)
        if not loser_ids:
            continue
        _fill_from_losers(conn, g["survivor_id"], loser_ids)
        dropped += _repoint(conn, refs, g["survivor_id"], loser_ids)
        conn.execute(
            f"DELETE FROM matches WHERE id IN ({', '.join('?' * len(loser_ids))})",
            loser_ids,
        )

    after = conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
    print(f"  matches: {before} → {after}")
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
