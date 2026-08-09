"""
Footy Predict — Football-Data.org Client

Async client for Football-Data.org API v4, the SECONDARY data source
covering 7 of our tracked leagues (PL, La Liga, Serie A, Bundesliga,
Ligue 1, Liga Portugal, Eredivisie).

Rate limit: 10 requests / minute (no daily limit on the free tier).

API docs: https://docs.football-data.org/general/v4/index.html

Usage:
    async with FootballDataOrgClient(db) as client:
        matches = await client.get_matches("PL")
        count = await client.fetch_and_store_fixtures("premier-league")
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from src.config import (
    CACHE_TTL,
    FOOTBALL_DATA_ORG,
    LEAGUES,
    LeagueConfig,
)
from src.database import Database
from src.fetchers.cache import CacheManager

logger = logging.getLogger(__name__)

# Source identifier for DB tables
_SOURCE = "football_data_org"


class FDOError(Exception):
    """Raised when the Football-Data.org API returns an error."""


class FDORateLimitError(FDOError):
    """Raised when the per-minute rate limit is hit."""


class FootballDataOrgClient:
    """
    Async client for Football-Data.org API v4.

    Manages its own :class:`httpx.AsyncClient` lifecycle via async context
    manager.  All requests are cached and rate-limited.

    Attributes:
        db: Database instance for caching and usage tracking.
        cache: CacheManager wrapping the DB cache layer.
        base_url: Football-Data.org API base URL.
        rate_limit: Maximum API calls per minute.
    """

    def __init__(self, db: Database) -> None:
        self.db = db
        self.cache = CacheManager(db)
        self.base_url: str = FOOTBALL_DATA_ORG.base_url
        self.rate_limit: int = FOOTBALL_DATA_ORG.rate_limit_per_minute

        # Sliding window for per-minute rate limiting
        self._request_timestamps: list[float] = []
        self._client: httpx.AsyncClient | None = None

    # ── Context manager ───────────────────────────────────────

    async def __aenter__(self) -> FootballDataOrgClient:
        key = self.db.get_setting("football_data_org_key") or FOOTBALL_DATA_ORG.key
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "X-Auth-Token": key,
                "Accept": "application/json",
            },
            timeout=30.0,
        )
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    # ═══════════════════════════════════════════════════════════
    #  Core HTTP method
    # ═══════════════════════════════════════════════════════════

    async def _request(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
        ttl: int | None = None,
    ) -> dict:
        """
        Make a cached, rate-limited GET request.

        Args:
            endpoint: API path (e.g. ``'/competitions/PL/matches'``).
            params: Query parameters.
            ttl: Cache TTL in seconds (defaults to :attr:`CACHE_TTL.fixtures`).

        Returns:
            Full API response dict.

        Raises:
            FDORateLimitError: Per-minute limit exceeded after backoff.
            FDOError: Non-200 response or API-level error.
        """
        params = params or {}
        url = f"{self.base_url}{endpoint}"
        ttl = ttl if ttl is not None else CACHE_TTL.fixtures

        # 1. Cache check
        cached = self.cache.get(url, params, _SOURCE)
        if cached is not None:
            logger.debug("Cache HIT  %s %s", endpoint, params)
            return cached  # type: ignore[return-value]

        logger.debug("Cache MISS %s %s", endpoint, params)

        # 2. Rate-limit gate
        await self._enforce_rate_limit()

        # 3. Make the HTTP request
        if not self._client:
            raise RuntimeError(
                "FootballDataOrgClient must be used as an async context manager. "
                "Use 'async with FootballDataOrgClient(db) as client:'."
            )

        resp = await self._client.get(endpoint, params=params)

        # Handle 429 Too Many Requests with retry-after header
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("X-RequestCounter-Reset", 60))
            logger.warning(
                "FDO rate limited (429). Waiting %ds before retry…", retry_after
            )
            await asyncio.sleep(retry_after)
            resp = await self._client.get(endpoint, params=params)

        resp.raise_for_status()
        data: dict = resp.json()

        # 4. Check for error message
        if "errorCode" in data:
            raise FDOError(
                f"Football-Data.org error {data.get('errorCode')}: "
                f"{data.get('message', 'Unknown error')}"
            )

        # 5. Cache the response
        self.cache.set(url, params, _SOURCE, data, ttl)

        # 6. Track usage
        self.db.increment_api_usage(_SOURCE)

        return data

    async def _enforce_rate_limit(self) -> None:
        """
        Enforce per-minute rate limiting with a sliding window.

        Waits if the limit is reached, up to a maximum of 120 seconds.
        """
        now = time.monotonic()
        window = 60.0

        self._request_timestamps = [
            ts for ts in self._request_timestamps if now - ts < window
        ]

        if len(self._request_timestamps) >= self.rate_limit:
            oldest = self._request_timestamps[0]
            wait_time = window - (now - oldest) + 0.1
            if wait_time > 120:
                raise FDORateLimitError(
                    f"Rate limit backoff too long ({wait_time:.0f}s). Aborting."
                )
            logger.info(
                "FDO rate limit reached (%d/%d per minute). Waiting %.1fs…",
                len(self._request_timestamps), self.rate_limit, wait_time,
            )
            await asyncio.sleep(wait_time)

        self._request_timestamps.append(time.monotonic())

    # ═══════════════════════════════════════════════════════════
    #  Low-level API methods
    # ═══════════════════════════════════════════════════════════

    async def get_matches(
        self,
        competition_code: str,
        from_date: str | None = None,
        to_date: str | None = None,
        matchday: int | None = None,
    ) -> list[dict]:
        """
        Fetch matches for a competition.

        Args:
            competition_code: FDO competition code (e.g. ``'PL'``).
            from_date: Filter start date (``YYYY-MM-DD``).
            to_date: Filter end date (``YYYY-MM-DD``).
            matchday: Filter by matchday number.

        Returns:
            List of match dicts from the ``matches`` array.
        """
        params: dict[str, Any] = {}
        if from_date:
            params["dateFrom"] = from_date
        if to_date:
            params["dateTo"] = to_date
        if matchday is not None:
            params["matchday"] = matchday

        data = await self._request(
            f"/competitions/{competition_code}/matches",
            params,
            ttl=CACHE_TTL.fixtures,
        )
        return data.get("matches", [])

    async def get_standings(
        self,
        competition_code: str,
    ) -> list[dict]:
        """
        Fetch current league standings.

        Args:
            competition_code: FDO competition code.

        Returns:
            List of standing entries from the ``TOTAL`` table.

        .. warning::
           This sends no season parameter and discards the response's ``season``
           block, so the caller cannot tell which season it received. Across a
           season turnover the returned table and the label
           :meth:`fetch_and_store_standings` assigns it disagree by a full year,
           in either direction. See ``HANDOFF.md``, task 2.
        """
        data = await self._request(
            f"/competitions/{competition_code}/standings",
            ttl=CACHE_TTL.standings,
        )

        standings = data.get("standings", [])
        for table in standings:
            if table.get("type") == "TOTAL":
                return table.get("table", [])

        # Fallback: return first table if no TOTAL type
        return standings[0].get("table", []) if standings else []

    async def get_head_to_head(
        self,
        match_id: int,
        limit: int = 10,
    ) -> list[dict]:
        """
        Fetch head-to-head history for a specific match.

        Note: FDO uses match IDs (not team IDs) for H2H lookups.

        Args:
            match_id: FDO match ID.
            limit: Maximum number of past meetings.

        Returns:
            List of past H2H match dicts.
        """
        params = {"limit": limit}
        data = await self._request(
            f"/matches/{match_id}/head2head",
            params,
            ttl=CACHE_TTL.h2h,
        )
        return data.get("matches", [])

    async def get_team(
        self,
        team_id: int,
    ) -> dict:
        """
        Fetch team details.

        Args:
            team_id: FDO team ID.

        Returns:
            Team dict with name, crest, venue, etc.
        """
        data = await self._request(
            f"/teams/{team_id}",
            ttl=CACHE_TTL.team_stats,
        )
        return data

    # ═══════════════════════════════════════════════════════════
    #  High-level fetch-and-store methods
    # ═══════════════════════════════════════════════════════════

    async def fetch_and_store_fixtures(self, league_slug: str) -> int:
        """
        Fetch fixtures from Football-Data.org and upsert into DB.

        Args:
            league_slug: League identifier (e.g. ``'premier-league'``).

        Returns:
            Number of fixtures upserted.

        Raises:
            ValueError: If the league has no FDO code.
        """
        league = self._get_league(league_slug)
        if not league.has_fdo:
            raise ValueError(
                f"League '{league_slug}' is not covered by Football-Data.org."
            )

        # Ensure league is in DB
        league_id = self.db.upsert_league(
            name=league.name,
            country=league.country,
            slug=league.slug,
            api_football_id=league.api_football_id,
            fdo_code=league.football_data_org_code,
            csv_code=league.csv_code,
            tier=league.tier,
        )

        fdo_code: str = league.football_data_org_code  # type: ignore[assignment]
        matches = await self.get_matches(fdo_code)
        count = 0

        for match in matches:
            try:
                home_info = match.get("homeTeam", {})
                away_info = match.get("awayTeam", {})

                # Upsert teams with FDO IDs
                home_team_id = self.db.upsert_team(
                    name=home_info.get("name", home_info.get("shortName", "Unknown")),
                    league_id=league_id,
                    fdo_id=home_info.get("id"),
                    short_name=home_info.get("shortName"),
                    logo_url=home_info.get("crest"),
                )
                away_team_id = self.db.upsert_team(
                    name=away_info.get("name", away_info.get("shortName", "Unknown")),
                    league_id=league_id,
                    fdo_id=away_info.get("id"),
                    short_name=away_info.get("shortName"),
                    logo_url=away_info.get("crest"),
                )

                # Map FDO status to our status
                status = self._map_match_status(match.get("status", "SCHEDULED"))

                # Extract scores
                score = match.get("score", {})
                full_time = score.get("fullTime", {})
                half_time = score.get("halfTime", {})

                # Season info
                season_info = match.get("season", {})
                season_start = str(season_info.get("startDate", ""))[:4]
                season_end = str(season_info.get("endDate", ""))[:4]
                if season_start and season_end and season_start != season_end:
                    season_label = f"{season_start}-{season_end[-2:]}"
                else:
                    season_label = league.get_season_label()

                self.db.upsert_match(
                    league_id=league_id,
                    season=season_label,
                    date=match.get("utcDate", ""),
                    home_team_id=home_team_id,
                    away_team_id=away_team_id,
                    matchday=match.get("matchday"),
                    status=status,
                    home_score=full_time.get("home"),
                    away_score=full_time.get("away"),
                    ht_home_score=half_time.get("home"),
                    ht_away_score=half_time.get("away"),
                    referee=match.get("referees", [{}])[0].get("name")
                    if match.get("referees")
                    else None,
                    source=_SOURCE,
                )
                count += 1
            except Exception as exc:
                logger.warning(
                    "Failed to upsert FDO match %s: %s",
                    match.get("id", "?"),
                    exc,
                )

        logger.info(
            "Stored %d/%d FDO fixtures for %s", count, len(matches), league_slug
        )
        return count

    async def fetch_and_store_standings(self, league_slug: str) -> int:
        """
        Fetch current standings from Football-Data.org and upsert into DB.

        Args:
            league_slug: League identifier.

        Returns:
            Number of standing entries upserted.
        """
        league = self._get_league(league_slug)
        if not league.has_fdo:
            raise ValueError(
                f"League '{league_slug}' is not covered by Football-Data.org."
            )

        league_id = self.db.upsert_league(
            name=league.name,
            country=league.country,
            slug=league.slug,
            api_football_id=league.api_football_id,
            fdo_code=league.football_data_org_code,
            csv_code=league.csv_code,
            tier=league.tier,
        )

        fdo_code: str = league.football_data_org_code  # type: ignore[assignment]
        # NOTE: this label comes from today's date, not from the season the API
        # actually returned — the two diverge during a turnover. HANDOFF.md task 2.
        season_label = league.get_season_label()
        standings = await self.get_standings(fdo_code)
        count = 0

        for entry in standings:
            try:
                team_info = entry.get("team", {})
                team_id = self.db.upsert_team(
                    name=team_info.get("name", team_info.get("shortName", "Unknown")),
                    league_id=league_id,
                    fdo_id=team_info.get("id"),
                    short_name=team_info.get("shortName"),
                    logo_url=team_info.get("crest"),
                )

                self.db.upsert_standing(
                    league_id=league_id,
                    season=season_label,
                    team_id=team_id,
                    position=entry.get("position", 0),
                    played=entry.get("playedGames", 0),
                    won=entry.get("won", 0),
                    drawn=entry.get("draw", 0),
                    lost=entry.get("lost", 0),
                    goals_for=entry.get("goalsFor", 0),
                    goals_against=entry.get("goalsAgainst", 0),
                    goal_difference=entry.get("goalDifference", 0),
                    points=entry.get("points", 0),
                    form=entry.get("form"),
                )
                count += 1
            except Exception as exc:
                logger.warning(
                    "Failed to upsert FDO standing for %s: %s",
                    entry.get("team", {}).get("name", "?"),
                    exc,
                )

        logger.info(
            "Stored %d/%d FDO standings for %s", count, len(standings), league_slug
        )
        return count

    # ── Helpers ─────────────────────────────────────────────

    @staticmethod
    def _get_league(slug: str) -> LeagueConfig:
        """Look up a league config or raise ValueError."""
        league = LEAGUES.get(slug)
        if league is None:
            raise ValueError(
                f"Unknown league '{slug}'. "
                f"Available: {', '.join(sorted(LEAGUES.keys()))}"
            )
        return league

    @staticmethod
    def _map_match_status(fdo_status: str) -> str:
        """
        Map Football-Data.org match statuses to our internal statuses.

        FDO statuses:
            SCHEDULED, TIMED → SCHEDULED
            IN_PLAY, PAUSED, LIVE → IN_PLAY
            FINISHED → FINISHED
            SUSPENDED → SUSPENDED
            POSTPONED, CANCELLED, AWARDED → POSTPONED
        """
        mapping: dict[str, str] = {
            "SCHEDULED": "SCHEDULED",
            "TIMED": "SCHEDULED",
            "IN_PLAY": "IN_PLAY",
            "PAUSED": "IN_PLAY",
            "LIVE": "IN_PLAY",
            "FINISHED": "FINISHED",
            "SUSPENDED": "SUSPENDED",
            "POSTPONED": "POSTPONED",
            "CANCELLED": "POSTPONED",
            "AWARDED": "FINISHED",
        }
        return mapping.get(fdo_status, "SCHEDULED")
