#!/usr/bin/env python3
"""Database migration runner for Footy Predict.

Usage:
    python -m migrations.run 003            # Run migration 003
    python -m migrations.run --all          # Run all pending migrations
    python -m migrations.run --status       # Show migration status
"""

import sqlite3
import sys
from pathlib import Path

# Project root is two levels up from this file (migrations/run.py)
PROJECT_ROOT = Path(__file__).parent.parent
DB_PATH = PROJECT_ROOT / "footy.db"
MIGRATIONS_DIR = Path(__file__).parent


def _ensure_migrations_table(conn: sqlite3.Connection) -> None:
    """Create a migrations tracking table if it doesn't exist."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS _migrations (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT    NOT NULL UNIQUE,
            applied_at  TEXT    DEFAULT (datetime('now'))
        )
        """
    )
    conn.commit()


def _applied_migrations(conn: sqlite3.Connection) -> set[str]:
    """Return the set of migration names that have already been applied."""
    rows = conn.execute("SELECT name FROM _migrations").fetchall()
    return {r[0] for r in rows}


def _run_migration(conn: sqlite3.Connection, sql_path: Path) -> None:
    """Execute a single SQL migration file inside a transaction."""
    name = sql_path.stem
    print(f"  → Running migration: {name}")
    sql = sql_path.read_text()
    conn.executescript(sql)
    conn.execute("INSERT INTO _migrations (name) VALUES (?)", (name,))
    conn.commit()
    print(f"  ✓ Migration {name} applied.")


def run_all_pending() -> None:
    """Apply all pending migrations in alphabetical order."""
    sql_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not sql_files:
        print("No migration files found.")
        return

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        _ensure_migrations_table(conn)
        applied = _applied_migrations(conn)
        pending = [f for f in sql_files if f.stem not in applied]

        if not pending:
            print("All migrations are up to date.")
            return

        print(f"Applying {len(pending)} pending migration(s)...")
        for sql_file in pending:
            _run_migration(conn, sql_file)
        print("Done.")
    finally:
        conn.close()


def run_one(migration_id: str) -> None:
    """Apply a specific migration by its numeric prefix or full stem name."""
    sql_files = list(MIGRATIONS_DIR.glob(f"{migration_id}*.sql"))
    if not sql_files:
        sql_files = list(MIGRATIONS_DIR.glob(f"*{migration_id}*.sql"))
    if not sql_files:
        print(f"Migration '{migration_id}' not found.")
        sys.exit(1)
    if len(sql_files) > 1:
        print(f"Ambiguous migration ID '{migration_id}' — matches: {[f.name for f in sql_files]}")
        sys.exit(1)

    sql_file = sql_files[0]
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        _ensure_migrations_table(conn)
        applied = _applied_migrations(conn)
        if sql_file.stem in applied:
            print(f"Migration '{sql_file.stem}' already applied.")
            return
        _run_migration(conn, sql_file)
    finally:
        conn.close()


def show_status() -> None:
    """Print the status of all migrations."""
    sql_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        _ensure_migrations_table(conn)
        applied = _applied_migrations(conn)
        print(f"{'Migration':<50} {'Status'}")
        print("-" * 60)
        for f in sql_files:
            status = "✓ Applied" if f.stem in applied else "○ Pending"
            print(f"  {f.name:<48} {status}")
        if not sql_files:
            print("  No migration files found.")
    finally:
        conn.close()


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or "--help" in args or "-h" in args:
        print(__doc__)
        sys.exit(0)

    if "--status" in args:
        show_status()
    elif "--all" in args:
        run_all_pending()
    else:
        run_one(args[0])
