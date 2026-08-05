"""
Migration 005 — Canonical team identity

Different sources spell the same club differently ("Man United" from the
football-data.co.uk CSVs, "Manchester United FC" from football-data.org), and
``upsert_team`` matched on ``(name, league_id)``. Each spelling therefore became
its own ``teams`` row, splitting one club's history across two identities.

Impact before this migration: the Premier League held 53 team rows for a
20-team competition. Arsenal's 304 CSV matches sat under one id while the
2026-27 fixtures pointed at a second id holding 38. Every form, venue and
head-to-head factor read only one half of the club's record.

This migration folds duplicate identities onto a single surviving row and
repoints every dependent table.

Survivor selection
------------------
The identity with the most matches wins (ties break to the lowest id), since
repointing fewer rows is cheaper and less likely to hit a unique conflict. The
survivor then adopts the most complete display name in the group, so reports
read "Manchester United FC" rather than "Man United".

Unique-constraint handling
--------------------------
``match_stats``, ``standings``, ``team_season_stats`` and ``head_to_head`` each
carry a unique constraint containing ``team_id``. Where repointing a losing row
would collide with a row the survivor already owns, the losing row is dropped —
the survivor holds the more complete record by construction.

Run with ``--dry-run`` to print the plan without writing.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.team_names import canonical_key  # noqa: E402

# (table, column) pairs pointing at teams(id), paired with the unique-constraint
# columns that must not collide when a losing row is repointed.
_TEAM_REFS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("matches", "home_team_id", ()),
    ("matches", "away_team_id", ()),
    ("match_stats", "team_id", ("match_id",)),
    ("standings", "team_id", ("league_id", "season")),
    ("team_season_stats", "team_id", ("league_id", "season")),
    ("head_to_head", "team_a_id", ("team_b_id",)),
    ("head_to_head", "team_b_id", ("team_a_id",)),
)


def _plan(conn: sqlite3.Connection) -> list[dict]:
    """Group teams by canonical identity and choose a survivor for each group.

    Returns:
        One entry per group needing a merge, each with ``survivor_id``,
        ``display_name``, ``loser_ids`` and ``aliases``.
    """
    rows = conn.execute(
        """
        SELECT t.id, t.name, t.league_id, t.aliases,
               (SELECT COUNT(*) FROM matches m
                 WHERE m.home_team_id = t.id OR m.away_team_id = t.id) AS n_matches
        FROM teams t
        """
    ).fetchall()

    groups: dict[tuple[int, str], list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        groups[(row["league_id"], canonical_key(row["name"]))].append(row)

    plan: list[dict] = []
    for (_league_id, key), members in sorted(groups.items()):
        if len(members) < 2:
            continue

        # Most matches wins; ties break to the lowest id for determinism.
        survivor = min(members, key=lambda r: (-r["n_matches"], r["id"]))
        losers = [r for r in members if r["id"] != survivor["id"]]

        # Longest name is the most complete spelling ("Manchester United FC").
        display_name = max((r["name"] for r in members), key=len)

        # Preserve every observed spelling so future lookups can resolve.
        aliases: set[str] = set()
        for r in members:
            aliases.add(r["name"])
            try:
                aliases.update(json.loads(r["aliases"] or "[]"))
            except (json.JSONDecodeError, TypeError):
                pass
        aliases.discard(display_name)

        plan.append({
            "canonical": key,
            "survivor_id": survivor["id"],
            "survivor_name": survivor["name"],
            "display_name": display_name,
            "loser_ids": [r["id"] for r in losers],
            "loser_names": [r["name"] for r in losers],
            "aliases": sorted(aliases),
        })

    return plan


def _repoint(conn: sqlite3.Connection, table: str, column: str,
             unique_with: tuple[str, ...], survivor_id: int, loser_id: int) -> int:
    """Point ``table.column`` from loser to survivor, dropping conflicts.

    Returns the number of losing rows deleted because repointing them would
    have violated a unique constraint the survivor already satisfies.
    """
    dropped = 0

    if unique_with:
        # Drop losing rows whose repointed key already exists for the survivor.
        # The correlated subquery references the outer table by name.
        match_cols = " AND ".join(f"s.{c} = {table}.{c}" for c in unique_with)
        cur = conn.execute(
            f"""
            DELETE FROM {table}
            WHERE {column} = ?
              AND EXISTS (
                  SELECT 1 FROM {table} AS s
                  WHERE s.{column} = ? AND {match_cols}
              )
            """,
            (loser_id, survivor_id),
        )
        dropped = cur.rowcount or 0

    conn.execute(
        f"UPDATE {table} SET {column} = ? WHERE {column} = ?",
        (survivor_id, loser_id),
    )
    return dropped


def migrate(conn: sqlite3.Connection, dry_run: bool = False) -> None:
    """Fold duplicate team identities onto a single row per club."""
    conn.row_factory = sqlite3.Row
    plan = _plan(conn)

    before = conn.execute("SELECT COUNT(*) FROM teams").fetchone()[0]

    if not plan:
        print("  No duplicate team identities found — nothing to do.")
        return

    print(f"  {len(plan)} merge group(s) covering "
          f"{sum(len(p['loser_ids']) for p in plan)} redundant team row(s).")

    total_dropped = 0
    for entry in plan:
        renamed = (
            f"  (renamed from {entry['survivor_name']!r})"
            if entry["display_name"] != entry["survivor_name"] else ""
        )
        print(f"    id={entry['survivor_id']:<6} {entry['display_name']}"
              f"{renamed}")
        print(f"             absorbs: {', '.join(entry['loser_names'])}")

        if dry_run:
            continue

        for loser_id in entry["loser_ids"]:
            for table, column, unique_with in _TEAM_REFS:
                total_dropped += _repoint(
                    conn, table, column, unique_with,
                    entry["survivor_id"], loser_id,
                )

        # Fold source ids and imagery onto the survivor before dropping losers.
        conn.execute(
            """
            UPDATE teams SET
                api_football_id = COALESCE(api_football_id, (
                    SELECT api_football_id FROM teams
                    WHERE id IN ({ph}) AND api_football_id IS NOT NULL LIMIT 1)),
                fdo_id = COALESCE(fdo_id, (
                    SELECT fdo_id FROM teams
                    WHERE id IN ({ph}) AND fdo_id IS NOT NULL LIMIT 1)),
                logo_url = COALESCE(logo_url, (
                    SELECT logo_url FROM teams
                    WHERE id IN ({ph}) AND logo_url IS NOT NULL LIMIT 1))
            WHERE id = ?
            """.format(ph=", ".join("?" * len(entry["loser_ids"]))),
            (*entry["loser_ids"], *entry["loser_ids"], *entry["loser_ids"],
             entry["survivor_id"]),
        )

        conn.execute(
            "DELETE FROM teams WHERE id IN ({ph})".format(
                ph=", ".join("?" * len(entry["loser_ids"]))),
            entry["loser_ids"],
        )

        # Name update must follow the deletes: UNIQUE(name, league_id) would
        # otherwise collide with the loser still holding that spelling.
        conn.execute(
            "UPDATE teams SET name = ?, aliases = ? WHERE id = ?",
            (entry["display_name"], json.dumps(entry["aliases"]),
             entry["survivor_id"]),
        )

    if dry_run:
        print(f"\n  DRY RUN — no changes written. teams would go {before} → "
              f"{before - sum(len(p['loser_ids']) for p in plan)}.")
        return

    after = conn.execute("SELECT COUNT(*) FROM teams").fetchone()[0]
    print(f"  teams: {before} → {after}")
    if total_dropped:
        print(f"  {total_dropped} dependent row(s) dropped as unique-constraint "
              f"duplicates (survivor retained the record).")


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
