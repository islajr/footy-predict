"""
Footy Predict — Data Fetchers Package

Unified access to all data sources:

- **API-Football** (``api_football``): Primary source for all 12 leagues.
- **Football-Data.org** (``football_data_org``): Secondary source for 7 leagues.
- **CSV Loader** (``csv_loader``): Historical match data from football-data.co.uk.
- **Cache Manager** (``cache``): Shared caching layer backed by SQLite.

Usage:
    from src.fetchers import APIFootballClient, FootballDataOrgClient, CSVLoader

    # API clients are async context managers
    async with APIFootballClient(db) as api:
        await api.fetch_and_store_fixtures("premier-league")

    # CSV loader is synchronous
    loader = CSVLoader(db)
    loader.load_all_leagues_history()
"""

from src.fetchers.api_football import (
    APIFootballClient,
    APIFootballError,
    QuotaExhaustedError,
    RateLimitError,
)
from src.fetchers.cache import CacheManager, make_cache_key
from src.fetchers.csv_loader import CSVLoader
from src.fetchers.football_data_org import (
    FDOError,
    FDORateLimitError,
    FootballDataOrgClient,
)

__all__ = [
    # API clients
    "APIFootballClient",
    "FootballDataOrgClient",
    "CSVLoader",
    # Cache
    "CacheManager",
    "make_cache_key",
    # Exceptions
    "APIFootballError",
    "QuotaExhaustedError",
    "RateLimitError",
    "FDOError",
    "FDORateLimitError",
]
