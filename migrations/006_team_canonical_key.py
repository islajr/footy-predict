"""
Migration 006 — Team canonical key column

Migration 005 merged the duplicate club identities that already existed, but
nothing stopped them coming back: ``upsert_team`` resolved identity on exact
``(name, league_id)``, so the next ``footy fetch`` carrying football-data.org
spellings would have re-split every club it touched.

This migration adds the column those lookups now use and the unique index that
makes a re-split impossible at the storage layer rather than by convention.

The index is created AFTER the backfill — building it first would fail on the
NULL-keyed rows, and SQLite treats NULLs as distinct in unique indexes anyway,
so an unbackfilled column would give no protection at all.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.team_names import canonical_key  # noqa: E402


def migrate(conn: sqlite3.Connection) -> None:
    """Add teams.canonical_key, backfill it, and enforce uniqueness."""
    conn.row_factory = sqlite3.Row

    columns = {r["name"] for r in conn.execute("PRAGMA table_info(teams)")}
    if "canonical_key" not in columns:
        conn.execute("ALTER TABLE teams ADD COLUMN canonical_key TEXT")
        print("  + teams.canonical_key")

    rows = conn.execute("SELECT id, name FROM teams").fetchall()
    conn.executemany(
        "UPDATE teams SET canonical_key = ? WHERE id = ?",
        [(canonical_key(r["name"]), r["id"]) for r in rows],
    )
    print(f"  backfilled {len(rows)} team row(s)")

    # Surface any residual collision rather than failing on an opaque
    # IntegrityError from CREATE UNIQUE INDEX.
    clashes = conn.execute(
        """
        SELECT league_id, canonical_key, COUNT(*) AS n,
               GROUP_CONCAT(name, ' | ') AS names
        FROM teams
        GROUP BY league_id, canonical_key
        HAVING n > 1
        """
    ).fetchall()
    if clashes:
        print("  ✗ canonical key collisions remain — run migration 005 first:")
        for c in clashes:
            print(f"      league {c['league_id']}: {c['names']}")
        raise RuntimeError(f"{len(clashes)} unmerged team group(s)")

    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_teams_canonical
            ON teams(league_id, canonical_key)
        """
    )
    print("  + unique index idx_teams_canonical(league_id, canonical_key)")


if __name__ == "__main__":
    connection = sqlite3.connect(str(PROJECT_ROOT / "footy.db"))
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        migrate(connection)
        connection.commit()
    finally:
        connection.close()
