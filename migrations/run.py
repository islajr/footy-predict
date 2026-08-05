#!/usr/bin/env python3
"""Database migration runner for Footy Predict.

Supports plain ``.sql`` files and ``.py`` data migrations. A Python migration
must expose ``migrate(conn)`` and is used where the transformation needs
application logic — e.g. 005 resolves team identity via the canonicalisation
rules in ``src.fetchers.team_names``, which cannot be expressed in SQL.

Usage:
    python -m migrations.run 003            # Run migration 003
    python -m migrations.run --all          # Run all pending migrations
    python -m migrations.run --status       # Show migration status
"""

import importlib.util
import sqlite3
import sys
from pathlib import Path

# Project root is two levels up from this file (migrations/run.py)
PROJECT_ROOT = Path(__file__).parent.parent
DB_PATH = PROJECT_ROOT / "footy.db"
MIGRATIONS_DIR = Path(__file__).parent

# Suffixes treated as migrations, in the order they are discovered.
MIGRATION_SUFFIXES = (".sql", ".py")


def _migration_files() -> list[Path]:
    """All migration files, ordered by numeric prefix.

    ``run.py`` and ``__init__.py`` are the runner's own modules, not
    migrations, so they are excluded.
    """
    excluded = {"run", "__init__"}
    files = [
        p for p in MIGRATIONS_DIR.iterdir()
        if p.suffix in MIGRATION_SUFFIXES and p.stem not in excluded
    ]
    return sorted(files, key=lambda p: p.name)


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


def _run_python_migration(conn: sqlite3.Connection, path: Path) -> None:
    """Import a ``.py`` migration and hand it the open connection.

    The module owns its own transformation logic but not the transaction —
    ``_run_migration`` commits once the tracking row is inserted, so a failure
    part-way through rolls the whole migration back.
    """
    spec = importlib.util.spec_from_file_location(f"_migration_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load migration module: {path.name}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    if not hasattr(module, "migrate"):
        raise RuntimeError(f"{path.name} defines no migrate(conn) function")

    module.migrate(conn)


def _run_migration(conn: sqlite3.Connection, path: Path) -> None:
    """Execute a single migration file inside a transaction."""
    name = path.stem
    print(f"  → Running migration: {name}")

    if path.suffix == ".py":
        _run_python_migration(conn, path)
    else:
        conn.executescript(path.read_text())

    conn.execute("INSERT INTO _migrations (name) VALUES (?)", (name,))
    conn.commit()
    print(f"  ✓ Migration {name} applied.")


def _connect() -> sqlite3.Connection:
    """Open the project database with foreign keys enforced."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def run_all_pending() -> None:
    """Apply all pending migrations in numeric order."""
    files = _migration_files()
    if not files:
        print("No migration files found.")
        return

    conn = _connect()
    try:
        _ensure_migrations_table(conn)
        applied = _applied_migrations(conn)
        pending = [f for f in files if f.stem not in applied]

        if not pending:
            print("All migrations are up to date.")
            return

        print(f"Applying {len(pending)} pending migration(s)...")
        for path in pending:
            _run_migration(conn, path)
        print("Done.")
    finally:
        conn.close()


def run_one(migration_id: str) -> None:
    """Apply a specific migration by its numeric prefix or full stem name."""
    files = _migration_files()
    matches = [f for f in files if f.stem.startswith(migration_id)]
    if not matches:
        matches = [f for f in files if migration_id in f.stem]
    if not matches:
        print(f"Migration '{migration_id}' not found.")
        sys.exit(1)
    if len(matches) > 1:
        print(f"Ambiguous migration ID '{migration_id}' — matches: {[f.name for f in matches]}")
        sys.exit(1)

    path = matches[0]
    conn = _connect()
    try:
        _ensure_migrations_table(conn)
        applied = _applied_migrations(conn)
        if path.stem in applied:
            print(f"Migration '{path.stem}' already applied.")
            return
        _run_migration(conn, path)
    finally:
        conn.close()


def show_status() -> None:
    """Print the status of all migrations."""
    files = _migration_files()
    conn = _connect()
    try:
        _ensure_migrations_table(conn)
        applied = _applied_migrations(conn)
        print(f"{'Migration':<50} {'Status'}")
        print("-" * 60)
        for f in files:
            status = "✓ Applied" if f.stem in applied else "○ Pending"
            print(f"  {f.name:<48} {status}")
        if not files:
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
