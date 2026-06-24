"""
Footy Predict — Application Entry Point

Initializes the database, seeds league data, and provides the main
application bootstrap used by both the CLI and Telegram bot.
"""

from __future__ import annotations

from src.database import Database


def init_app() -> Database:
    """
    Initialize the application: create DB, seed leagues.

    Creates the SQLite database (if it doesn't exist), applies the schema,
    and populates the leagues table from the central configuration.

    Returns:
        A fully initialized Database instance ready for use.

    Example:
        >>> db = init_app()
        >>> leagues = db.get_all_leagues()
        >>> len(leagues) >= 12
        True
    """
    db = Database()
    db.seed_leagues()
    return db


def start_server(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Start the uvicorn server for the FastAPI API layer."""
    import uvicorn
    uvicorn.run("src.api.app:app", host=host, port=port, reload=False)

