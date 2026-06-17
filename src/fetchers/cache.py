"""
Footy Predict — Cache Manager

Wraps the Database cache methods with a clean interface for storing and
retrieving API responses.  Cache keys are deterministic SHA-256 hashes
of the URL + sorted query parameters, guaranteeing that logically
identical requests always resolve to the same cache entry.

Usage:
    cache = CacheManager(db)
    data = cache.get("https://api.example.com/foo", {"bar": 1}, "api_football")
    if data is None:
        data = await fetch(...)
        cache.set("https://api.example.com/foo", {"bar": 1}, "api_football", data, ttl=3600)
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from src.database import Database


def make_cache_key(url: str, params: dict[str, Any] | None = None) -> str:
    """
    Build a deterministic SHA-256 cache key from a URL and parameters.

    The parameters dict is sorted by key before hashing so that
    ``{"a": 1, "b": 2}`` and ``{"b": 2, "a": 1}`` produce the same key.

    Args:
        url: The request URL (without query string).
        params: Query parameters dict.  ``None`` is treated as empty.

    Returns:
        64-character hex digest uniquely identifying the request.
    """
    canonical = json.dumps(
        {"url": url, "params": sorted((params or {}).items())},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


class CacheManager:
    """
    High-level cache interface backed by the Database ``api_cache`` table.

    Each entry is stored with a TTL (time-to-live in seconds).  Expired
    entries are ignored on read and can be purged in bulk via
    :meth:`clear_expired`.

    Attributes:
        db: The :class:`Database` instance used for storage.
    """

    def __init__(self, db: Database) -> None:
        self.db = db

    # ── Read ──────────────────────────────────────────────────

    def get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        source: str = "api_football",
    ) -> dict | list | None:
        """
        Retrieve a cached API response, or ``None`` if missing / expired.

        Args:
            url: Original request URL.
            params: Original query parameters.
            source: Data source identifier (``'api_football'`` or
                ``'football_data_org'``).

        Returns:
            The deserialized response data, or ``None``.
        """
        key = make_cache_key(url, params)
        return self.db.get_cached_response(key)

    # ── Write ─────────────────────────────────────────────────

    def set(
        self,
        url: str,
        params: dict[str, Any] | None,
        source: str,
        data: dict | list,
        ttl: int,
    ) -> None:
        """
        Store an API response in the cache.

        Args:
            url: Original request URL.
            params: Original query parameters.
            source: Data source identifier.
            data: Response payload (must be JSON-serialisable).
            ttl: Time-to-live in seconds.
        """
        key = make_cache_key(url, params)
        self.db.cache_response(
            cache_key=key,
            url=url,
            source=source,
            response_data=data,
            ttl_seconds=ttl,
        )

    # ── Maintenance ───────────────────────────────────────────

    def clear_expired(self) -> int:
        """
        Delete all expired cache entries.

        Returns:
            Number of entries removed.
        """
        return self.db.clear_expired_cache()
