"""
Footy Predict — API-Football Client

Async client for API-Football (api-sports.io), the PRIMARY data source
for fixtures, standings, team statistics, and head-to-head records.

Every request follows the pipeline:
    cache check → quota check → rate-limit gate → HTTP request →
    cache store → usage increment → response

Rate limits:
    • 10 requests / minute  (sliding window)
    • 100 requests / day    (configurable via config)

All responses from API-Football share the envelope structure:
    {"get": ..., "parameters": {...}, "errors": ...,
     "results": N, "response": [...]}

Usage:
    async with APIFootballClient(db) as client:
        fixtures = await client.get_fixtures(league_id=39, season=2025)
        count = await client.fetch_and_store_fixtures("premier-league")
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from src.config import (
    API_FOOTBALL,
    CACHE_TTL,
    LEAGUES,
    LEAGUES_BY_API_FOOTBALL_ID,
    LeagueConfig,
)
from src.database import Database
from src.fetchers.cache import CacheManager

logger = logging.getLogger(__name__)

# API-Football source identifier used in DB tables
_SOURCE = "api_football"


def _parse_fixture_stats(statistics: list[dict]) -> dict:
    """Map API-Football ``/fixtures/statistics`` response to ``match_stats`` column names.

    API-Football returns a list of ``{"type": "...", "value": ...}`` dicts.
    This function extracts the ones we store and converts them to our schema keys.

    Args:
        statistics: The ``statistics`` array from one team's entry in the response.

    Returns:
        Dict of keyword args suitable for ``db.upsert_match_stats(**parsed)``.
    """
    # Map API-Football type strings → our column names
    type_map: dict[str, str] = {
        "Shots on Goal":          "shots_on_target",
        "Shots off Goal":         "shots",          # partial; total shots computed below
        "Total Shots":            "shots",
        "Ball Possession":        "possession",
        "Corner Kicks":           "corners",
        "Fouls":                  "fouls",
        "Yellow Cards":           "yellow_cards",
        "Red Cards":              "red_cards",
        "Offsides":               "offsides",
        "Total passes":           "passes",
        "Passes accurate":        "passes",         # overwrite if more specific
        "Passes %":               "pass_accuracy",
        "expected_goals":         "expected_goals",
        "Expected Goals":         "expected_goals",  # API uses this capitalisation
    }

    parsed: dict = {}
    for entry in statistics:
        api_type = entry.get("type", "")
        raw_value = entry.get("value")
        col = type_map.get(api_type)
        if col is None or raw_value is None:
            continue
        # Strip % sign from possession / pass accuracy
        if isinstance(raw_value, str) and raw_value.endswith("%"):
            try:
                raw_value = float(raw_value.rstrip("%"))
            except ValueError:
                continue
        try:
            parsed[col] = float(raw_value) if col in ("possession", "pass_accuracy", "expected_goals") else int(raw_value)
        except (ValueError, TypeError):
            pass

    return parsed



class APIFootballError(Exception):
    """Raised when the API-Football API returns an error response."""


class QuotaExhaustedError(APIFootballError):
    """Raised when the daily API quota has been reached."""


class RateLimitError(APIFootballError):
    """Raised when the per-minute rate limit is hit."""


class APIFootballClient:
    """
    Async client for API-Football v3 (api-sports.io).

    Manages its own :class:`httpx.AsyncClient` lifecycle via async context
    manager.  All requests are cached, rate-limited, and quota-tracked.

    Attributes:
        db: Database instance for caching and usage tracking.
        cache: CacheManager wrapping the DB cache layer.
        base_url: API-Football base URL.
        daily_limit: Maximum API calls per day.
        rate_limit: Maximum API calls per minute.
    """

    def __init__(self, db: Database) -> None:
        self.db = db
        self.cache = CacheManager(db)
        self.base_url: str = API_FOOTBALL.base_url
        self.daily_limit: int = API_FOOTBALL.daily_limit
        self.rate_limit: int = API_FOOTBALL.rate_limit_per_minute

        # Sliding window for per-minute rate limiting
        self._request_timestamps: list[float] = []
        self._client: httpx.AsyncClient | None = None

    # ── Context manager ───────────────────────────────────────

    async def __aenter__(self) -> APIFootballClient:
        key = self.db.get_setting("api_football_key") or API_FOOTBALL.key
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "x-apisports-key": key,
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
        Make a cached, rate-limited, quota-checked GET request.

        Args:
            endpoint: API path (e.g. ``'/fixtures'``).
            params: Query parameters.
            ttl: Cache TTL in seconds (defaults to :attr:`CACHE_TTL.fixtures`).

        Returns:
            Full API response dict.

        Raises:
            QuotaExhaustedError: Daily quota reached.
            RateLimitError: Per-minute limit exceeded after backoff.
            APIFootballError: Non-200 response or API-level error.
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

        # 2. Quota check
        remaining = self.db.get_remaining_quota(_SOURCE, self.daily_limit)
        if remaining <= 0:
            raise QuotaExhaustedError(
                f"Daily API-Football quota exhausted ({self.daily_limit} calls). "
                "Try again tomorrow."
            )

        # 3. Rate-limit gate (sliding window)
        await self._enforce_rate_limit()

        # 4. Make the HTTP request
        if not self._client:
            raise RuntimeError(
                "APIFootballClient must be used as an async context manager. "
                "Use 'async with APIFootballClient(db) as client:'."
            )

        resp = await self._client.get(endpoint, params=params)
        resp.raise_for_status()

        data: dict = resp.json()

        # 5. Check for API-level errors
        errors = data.get("errors")
        if errors:
            # API-Football returns errors as dict or list
            error_msg = json.dumps(errors) if isinstance(errors, (dict, list)) else str(errors)
            if error_msg and error_msg not in ("[]", "{}"):
                raise APIFootballError(f"API-Football error: {error_msg}")

        # 6. Cache the response
        self.cache.set(url, params, _SOURCE, data, ttl)

        # 7. Track usage
        self.db.increment_api_usage(_SOURCE, self.daily_limit)

        return data

    async def _enforce_rate_limit(self) -> None:
        """
        Enforce per-minute rate limiting with a sliding window.

        If the limit is reached, waits until the oldest request in the
        window expires.  Gives up after 120 seconds of waiting.
        """
        now = time.monotonic()
        window = 60.0  # seconds

        # Purge timestamps outside the window
        self._request_timestamps = [
            ts for ts in self._request_timestamps if now - ts < window
        ]

        if len(self._request_timestamps) >= self.rate_limit:
            # Wait until the oldest request falls outside the window
            oldest = self._request_timestamps[0]
            wait_time = window - (now - oldest) + 0.1  # small buffer
            if wait_time > 120:
                raise RateLimitError(
                    f"Rate limit backoff too long ({wait_time:.0f}s). Aborting."
                )
            logger.info(
                "Rate limit reached (%d/%d per minute). Waiting %.1fs…",
                len(self._request_timestamps), self.rate_limit, wait_time,
            )
            await asyncio.sleep(wait_time)

        self._request_timestamps.append(time.monotonic())

    # ═══════════════════════════════════════════════════════════
    #  Low-level API methods
    # ═══════════════════════════════════════════════════════════

    async def get_fixtures(
        self,
        league_id: int,
        season: int,
        next_n: int | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> list[dict]:
        """
        Fetch fixtures (scheduled, live, or finished).

        Args:
            league_id: API-Football league ID.
            season: Season year (e.g. ``2025``).
            next_n: If set, return only the next N upcoming fixtures.
            from_date: Filter start date (``YYYY-MM-DD``).
            to_date: Filter end date (``YYYY-MM-DD``).

        Returns:
            List of fixture dicts from the ``response`` array.
        """
        params: dict[str, Any] = {"league": league_id, "season": season}
        if next_n is not None:
            params["next"] = next_n
        if from_date:
            params["from"] = from_date
        if to_date:
            params["to"] = to_date

        data = await self._request("/fixtures", params, ttl=CACHE_TTL.fixtures)
        return data.get("response", [])

    async def get_team_statistics(
        self,
        team_id: int,
        league_id: int,
        season: int,
    ) -> dict:
        """
        Fetch seasonal statistics for a team in a league.

        Returns:
            The statistics dict from the ``response`` field.
        """
        params = {"team": team_id, "league": league_id, "season": season}
        data = await self._request(
            "/teams/statistics", params, ttl=CACHE_TTL.team_stats
        )
        return data.get("response", {})

    async def get_head_to_head(
        self,
        team1_id: int,
        team2_id: int,
        last: int = 10,
    ) -> list[dict]:
        """
        Fetch head-to-head history between two teams.

        Args:
            team1_id: First team's API-Football ID.
            team2_id: Second team's API-Football ID.
            last: Maximum number of past meetings to return.

        Returns:
            List of fixture dicts.
        """
        params = {"h2h": f"{team1_id}-{team2_id}", "last": last}
        data = await self._request("/fixtures/headtohead", params, ttl=CACHE_TTL.h2h)
        return data.get("response", [])

    async def get_fixture_statistics(
        self,
        fixture_id: int,
    ) -> list[dict]:
        """
        Fetch in-match statistics for a played fixture.

        Returns:
            List with one entry per team, each containing a ``statistics``
            array of ``{type, value}`` pairs.
        """
        params = {"fixture": fixture_id}
        data = await self._request(
            "/fixtures/statistics", params, ttl=CACHE_TTL.match_stats
        )
        return data.get("response", [])

    async def get_predictions(
        self,
        fixture_id: int,
    ) -> dict:
        """
        Fetch API-Football's built-in predictions for a fixture.

        Returns:
            Prediction dict from the ``response`` array (first element).
        """
        params = {"fixture": fixture_id}
        data = await self._request(
            "/predictions", params, ttl=CACHE_TTL.predictions
        )
        response = data.get("response", [])
        return response[0] if response else {}

    async def get_standings(
        self,
        league_id: int,
        season: int,
    ) -> list[dict]:
        """
        Fetch current league standings.

        Returns:
            List of standing entries from the first league group.
        """
        params = {"league": league_id, "season": season}
        data = await self._request("/standings", params, ttl=CACHE_TTL.standings)
        response = data.get("response", [])
        if response and response[0].get("league", {}).get("standings"):
            # standings is a list of groups; first group is the main table
            return response[0]["league"]["standings"][0]
        return []

    async def get_fixture_events(
        self,
        fixture_id: int,
    ) -> list[dict]:
        """
        Fetch events (goals, cards, substitutions) for a fixture.

        Returns:
            List of event dicts.
        """
        params = {"fixture": fixture_id}
        data = await self._request(
            "/fixtures/events", params, ttl=CACHE_TTL.match_stats
        )
        return data.get("response", [])

    # ═══════════════════════════════════════════════════════════
    #  Quota / status
    # ═══════════════════════════════════════════════════════════

    def get_quota_status(self) -> dict[str, Any]:
        """
        Get remaining API calls for today.

        Returns:
            Dict with ``used``, ``remaining``, ``daily_limit``, and
            ``rate_limit_per_minute``.
        """
        usage = self.db.get_api_usage_today(_SOURCE)
        used = usage["requests_made"]
        return {
            "source": _SOURCE,
            "used": used,
            "remaining": max(0, self.daily_limit - used),
            "daily_limit": self.daily_limit,
            "rate_limit_per_minute": self.rate_limit,
        }

    # ═══════════════════════════════════════════════════════════
    #  High-level fetch-and-store methods
    # ═══════════════════════════════════════════════════════════

    async def fetch_and_store_fixtures(self, league_slug: str) -> int:
        """
        Fetch fixtures from API-Football and upsert teams + matches into DB.

        Args:
            league_slug: League identifier (e.g. ``'premier-league'``).

        Returns:
            Number of fixtures upserted.
        """
        league = self._get_league(league_slug)
        season = self._get_season(league)

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

        try:
            fixtures = await self.get_fixtures(league_id=league.api_football_id, season=season)
        except APIFootballError as exc:
            if "Free plans do not have access" in str(exc) and season != 2024:
                logger.warning("Free plan detected. Retrying with season 2024.")
                self.db.set_setting("target_season", "2024")
                season = 2024
                fixtures = await self.get_fixtures(league_id=league.api_football_id, season=season)
            else:
                raise

        count = 0

        for fix in fixtures:
            try:
                fixture_info = fix.get("fixture", {})
                teams = fix.get("teams", {})
                goals = fix.get("goals", {})
                score = fix.get("score", {})

                home_info = teams.get("home", {})
                away_info = teams.get("away", {})

                # Upsert teams
                home_team_id = self.db.upsert_team(
                    name=home_info.get("name", "Unknown"),
                    league_id=league_id,
                    api_football_id=home_info.get("id"),
                    logo_url=home_info.get("logo"),
                )
                away_team_id = self.db.upsert_team(
                    name=away_info.get("name", "Unknown"),
                    league_id=league_id,
                    api_football_id=away_info.get("id"),
                    logo_url=away_info.get("logo"),
                )

                # Map API status → our status
                status = self._map_fixture_status(
                    fixture_info.get("status", {}).get("short", "NS")
                )

                # Half-time scores
                ht = score.get("halftime", {})

                # Build season label
                season_label = league.get_season_label(season)

                self.db.upsert_match(
                    league_id=league_id,
                    season=season_label,
                    date=fixture_info.get("date", ""),
                    home_team_id=home_team_id,
                    away_team_id=away_team_id,
                    api_football_id=fixture_info.get("id"),
                    matchday=fix.get("league", {}).get("round", "").split(" - ")[-1]
                    if "Round" in str(fix.get("league", {}).get("round", ""))
                    else None,
                    status=status,
                    home_score=goals.get("home"),
                    away_score=goals.get("away"),
                    ht_home_score=ht.get("home"),
                    ht_away_score=ht.get("away"),
                    referee=fixture_info.get("referee"),
                    venue=fixture_info.get("venue", {}).get("name")
                    if isinstance(fixture_info.get("venue"), dict)
                    else fixture_info.get("venue"),
                    source=_SOURCE,
                )
                count += 1
            except Exception as exc:
                logger.warning(
                    "Failed to upsert fixture %s: %s",
                    fix.get("fixture", {}).get("id", "?"),
                    exc,
                )

        logger.info(
            "Stored %d/%d team stats for %s", count, len(teams), league_slug
        )
        return count

    async def fetch_and_store_match_stats(
        self,
        league_slug: str,
        limit: int = 20,
    ) -> int:
        """Fetch per-match statistics from ``/fixtures/statistics`` and store in ``match_stats``.

        Identifies FINISHED matches that have no ``match_stats`` rows yet, then
        fetches statistics for up to *limit* of them, newest first. Each fixture
        call costs 1 API request, so the default limit of 20 is deliberately
        conservative to preserve daily quota for analysis and other sync steps.

        **Local-use note**: This is designed for manual or scheduled daily runs,
        not for real-time serving. Run ``footy sync --stats`` once per day.
        The quota guard stops automatically if the daily limit is reached.

        Args:
            league_slug: League identifier (e.g. ``'premier-league'``).
            limit:       Maximum fixtures to backfill in a single run (default 20).

        Returns:
            Number of fixtures whose stats were successfully stored.
        """
        league = self._get_league(league_slug)
        league_id = self.db.upsert_league(
            name=league.name,
            country=league.country,
            slug=league.slug,
            api_football_id=league.api_football_id,
            fdo_code=league.football_data_org_code,
            csv_code=league.csv_code,
            tier=league.tier,
        )

        unstatted = self.db.get_matches_without_stats(league_id, limit=limit)
        if not unstatted:
            logger.info("All FINISHED matches for %s already have stats.", league_slug)
            return 0

        count = 0
        for match in unstatted:
            api_fixture_id = match.get("api_football_id")
            if not api_fixture_id:
                continue
            try:
                raw_stats = await self.get_fixture_statistics(api_fixture_id)
                for team_entry in raw_stats:
                    team_api_id = team_entry.get("team", {}).get("id")
                    team = self.db.get_team_by_api_football_id(team_api_id)
                    if not team:
                        continue
                    parsed = _parse_fixture_stats(team_entry.get("statistics", []))
                    self.db.upsert_match_stats(
                        match_id=match["id"],
                        team_id=team["id"],
                        **parsed,
                    )
                count += 1
            except (QuotaExhaustedError, RateLimitError):
                logger.warning(
                    "Quota/rate limit hit during match stats fetch. "
                    "Stored stats for %d/%d fixtures.",
                    count, len(unstatted),
                )
                break
            except Exception as exc:
                logger.warning(
                    "Failed to fetch stats for fixture api_id=%s: %s",
                    api_fixture_id, exc,
                )

        logger.info(
            "Stored match stats for %d/%d fixtures for %s",
            count, len(unstatted), league_slug,
        )
        return count

    async def fetch_and_store_standings(self, league_slug: str) -> int:
        """Fetch current standings and upsert into DB.

        Args:
            league_slug: League identifier.

        Returns:
            Number of standing entries upserted.
        """
        league = self._get_league(league_slug)
        season = self._get_season(league)

        league_id = self.db.upsert_league(
            name=league.name,
            country=league.country,
            slug=league.slug,
            api_football_id=league.api_football_id,
            fdo_code=league.football_data_org_code,
            csv_code=league.csv_code,
            tier=league.tier,
        )

        try:
            standings = await self.get_standings(league.api_football_id, season)
        except APIFootballError as exc:
            if "Free plans do not have access" in str(exc) and season != 2024:
                logger.warning("Free plan detected. Retrying with season 2024.")
                self.db.set_setting("target_season", "2024")
                season = 2024
                standings = await self.get_standings(league.api_football_id, season)
            else:
                raise

        season_label = league.get_season_label(season)
        count = 0

        for entry in standings:
            try:
                team_info = entry.get("team", {})
                team_id = self.db.upsert_team(
                    name=team_info.get("name", "Unknown"),
                    league_id=league_id,
                    api_football_id=team_info.get("id"),
                    logo_url=team_info.get("logo"),
                )

                all_stats = entry.get("all", {})

                home_info = entry.get("home", {})
                away_info = entry.get("away", {})

                self.db.upsert_standing(
                    league_id=league_id,
                    season=season_label,
                    team_id=team_id,
                    position=entry.get("rank", 0),
                    played=all_stats.get("played", 0),
                    won=all_stats.get("win", 0),
                    drawn=all_stats.get("draw", 0),
                    lost=all_stats.get("lose", 0),
                    goals_for=all_stats.get("goals", {}).get("for", 0),
                    goals_against=all_stats.get("goals", {}).get("against", 0),
                    goal_difference=entry.get("goalsDiff", 0),
                    points=entry.get("points", 0),
                    form=entry.get("form"),
                    # Home/away splits — present in every API-Football standings response
                    home_played=home_info.get("played", 0) or 0,
                    home_won=home_info.get("win", 0) or 0,
                    home_drawn=home_info.get("draw", 0) or 0,
                    home_lost=home_info.get("lose", 0) or 0,
                    home_gf=home_info.get("goals", {}).get("for", 0) or 0,
                    home_ga=home_info.get("goals", {}).get("against", 0) or 0,
                    away_played=away_info.get("played", 0) or 0,
                    away_won=away_info.get("win", 0) or 0,
                    away_drawn=away_info.get("draw", 0) or 0,
                    away_lost=away_info.get("lose", 0) or 0,
                    away_gf=away_info.get("goals", {}).get("for", 0) or 0,
                    away_ga=away_info.get("goals", {}).get("against", 0) or 0,
                )
                count += 1
            except Exception as exc:
                logger.warning(
                    "Failed to upsert standing for team %s: %s",
                    entry.get("team", {}).get("name", "?"),
                    exc,
                )

        logger.info(
            "Stored %d/%d standings for %s", count, len(standings), league_slug
        )
        return count

    async def fetch_and_store_team_stats(self, league_slug: str) -> int:
        """
        Fetch and store seasonal statistics for all teams in a league.

        Retrieves the list of teams from the standings, then fetches
        detailed statistics for each one.

        Args:
            league_slug: League identifier.

        Returns:
            Number of teams whose stats were stored.
        """
        league = self._get_league(league_slug)
        season = self._get_season(league)

        league_id = self.db.upsert_league(
            name=league.name,
            country=league.country,
            slug=league.slug,
            api_football_id=league.api_football_id,
            fdo_code=league.football_data_org_code,
            csv_code=league.csv_code,
            tier=league.tier,
        )

        # Get teams from DB (should already be populated by fixtures/standings)
        teams = self.db.get_teams_by_league(league_id)
        if not teams:
            logger.warning("No teams found in DB for %s — fetch fixtures first", league_slug)
            return 0

        count = 0
        for team in teams:
            api_id = team.get("api_football_id")
            if not api_id:
                logger.debug("Skipping team %s — no API-Football ID", team["name"])
                continue

            try:
                try:
                    stats = await self.get_team_statistics(
                        team_id=api_id,
                        league_id=league.api_football_id,
                        season=season,
                    )
                except APIFootballError as exc:
                    if "Free plans do not have access" in str(exc) and season != 2024:
                        logger.warning("Free plan detected. Retrying with season 2024.")
                        self.db.set_setting("target_season", "2024")
                        season = 2024
                        stats = await self.get_team_statistics(
                            team_id=api_id,
                            league_id=league.api_football_id,
                            season=season,
                        )
                    else:
                        raise

                if not stats:
                    continue

                season_label = league.get_season_label(season)

                # Extract nested statistics
                fixtures_stats = stats.get("fixtures", {})
                goals_stats = stats.get("goals", {})
                clean_sheet = stats.get("clean_sheet", {})
                failed_to_score = stats.get("failed_to_score", {})
                penalty = stats.get("penalty", {})

                # Wins / draws / losses
                wins_data = fixtures_stats.get("wins", {})
                draws_data = fixtures_stats.get("draws", {})
                losses_data = fixtures_stats.get("loses", {})
                played_data = fixtures_stats.get("played", {})

                # Goals for/against
                goals_for = goals_stats.get("for", {})
                goals_against = goals_stats.get("against", {})

                self.db.upsert_team_season_stats(
                    team_id=team["id"],
                    league_id=league_id,
                    season=season_label,
                    matches_played=played_data.get("total", 0),
                    wins=wins_data.get("total", 0),
                    draws=draws_data.get("total", 0),
                    losses=losses_data.get("total", 0),
                    goals_scored=goals_for.get("total", {}).get("total", 0),
                    goals_conceded=goals_against.get("total", {}).get("total", 0),
                    home_played=played_data.get("home", 0),
                    home_wins=wins_data.get("home", 0),
                    home_draws=draws_data.get("home", 0),
                    home_losses=losses_data.get("home", 0),
                    home_goals_scored=goals_for.get("total", {}).get("home", 0),
                    home_goals_conceded=goals_against.get("total", {}).get("home", 0),
                    away_played=played_data.get("away", 0),
                    away_wins=wins_data.get("away", 0),
                    away_draws=draws_data.get("away", 0),
                    away_losses=losses_data.get("away", 0),
                    away_goals_scored=goals_for.get("total", {}).get("away", 0),
                    away_goals_conceded=goals_against.get("total", {}).get("away", 0),
                    form=stats.get("form"),
                    clean_sheets=(
                        (clean_sheet.get("home") or 0) +
                        (clean_sheet.get("away") or 0)
                    ),
                    failed_to_score=(
                        (failed_to_score.get("home") or 0) +
                        (failed_to_score.get("away") or 0)
                    ),
                    avg_goals_scored=goals_for.get("average", {}).get("total"),
                    avg_goals_conceded=goals_against.get("average", {}).get("total"),
                    penalty_scored=penalty.get("scored", {}).get("total", 0),
                    penalty_missed=penalty.get("missed", {}).get("total", 0),
                )
                count += 1
            except (QuotaExhaustedError, RateLimitError):
                logger.warning(
                    "Quota/rate limit hit while fetching team stats for %s. "
                    "Stored %d/%d teams.",
                    league_slug, count, len(teams),
                )
                break
            except Exception as exc:
                logger.warning(
                    "Failed to fetch stats for team %s (ID %s): %s",
                    team["name"], api_id, exc,
                )

        logger.info(
            "Stored stats for %d/%d teams in %s", count, len(teams), league_slug
        )
        return count

    async def fetch_and_store_h2h(
        self,
        home_team_api_id: int,
        away_team_api_id: int,
    ) -> None:
        """
        Fetch head-to-head history and store in the DB.

        Looks up the internal team IDs from their API-Football IDs,
        then upserts the H2H record.

        Args:
            home_team_api_id: Home team's API-Football ID.
            away_team_api_id: Away team's API-Football ID.
        """
        h2h_fixtures = await self.get_head_to_head(home_team_api_id, away_team_api_id)

        home_team = self.db.get_team_by_api_football_id(home_team_api_id)
        away_team = self.db.get_team_by_api_football_id(away_team_api_id)

        if not home_team or not away_team:
            logger.warning(
                "Cannot store H2H — team(s) not in DB. "
                "Home API ID: %d, Away API ID: %d",
                home_team_api_id, away_team_api_id,
            )
            return

        home_id = home_team["id"]
        away_id = away_team["id"]

        # Tally results
        home_wins = 0
        draws = 0
        away_wins = 0
        home_goals = 0
        away_goals = 0
        last_matches: list[dict] = []

        for fix in h2h_fixtures:
            teams = fix.get("teams", {})
            goals = fix.get("goals", {})
            fixture_info = fix.get("fixture", {})

            h_goals = goals.get("home") or 0
            a_goals = goals.get("away") or 0

            # Determine which side is "home_team_api_id"
            fix_home_id = teams.get("home", {}).get("id")

            if fix_home_id == home_team_api_id:
                home_goals += h_goals
                away_goals += a_goals
                if h_goals > a_goals:
                    home_wins += 1
                elif h_goals < a_goals:
                    away_wins += 1
                else:
                    draws += 1
            else:
                # Roles are swapped in this fixture
                home_goals += a_goals
                away_goals += h_goals
                if a_goals > h_goals:
                    home_wins += 1
                elif a_goals < h_goals:
                    away_wins += 1
                else:
                    draws += 1

            last_matches.append({
                "date": fixture_info.get("date", ""),
                "home": teams.get("home", {}).get("name", ""),
                "away": teams.get("away", {}).get("name", ""),
                "score": f"{h_goals}-{a_goals}",
                "venue": fixture_info.get("venue", {}).get("name", "")
                if isinstance(fixture_info.get("venue"), dict)
                else fixture_info.get("venue", ""),
            })

        self.db.upsert_h2h(
            team_a_id=home_id,
            team_b_id=away_id,
            total_matches=len(h2h_fixtures),
            team_a_wins=home_wins,
            draws=draws,
            team_b_wins=away_wins,
            team_a_goals=home_goals,
            team_b_goals=away_goals,
            last_matches=last_matches,
        )

        logger.info(
            "Stored H2H: %s vs %s (%d matches)",
            home_team["name"], away_team["name"], len(h2h_fixtures),
        )

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

    def _get_season(self, league: LeagueConfig) -> int:
        """Resolve the season key, checking database target_season setting first."""
        target = self.db.get_setting("target_season")
        if target:
            try:
                return int(target)
            except ValueError:
                pass
        return league.get_current_season()

    @staticmethod
    def _map_fixture_status(short_status: str) -> str:
        """
        Map API-Football fixture status codes to our internal statuses.

        API-Football codes:
            TBD, NS → SCHEDULED
            1H, HT, 2H, ET, P, BT, LIVE → IN_PLAY
            FT, AET, PEN → FINISHED
            SUSP, INT → SUSPENDED
            PST, CANC, ABD, AWD, WO → POSTPONED
        """
        mapping: dict[str, str] = {
            "TBD": "SCHEDULED",
            "NS": "SCHEDULED",
            "1H": "IN_PLAY",
            "HT": "IN_PLAY",
            "2H": "IN_PLAY",
            "ET": "IN_PLAY",
            "P": "IN_PLAY",
            "BT": "IN_PLAY",
            "LIVE": "IN_PLAY",
            "FT": "FINISHED",
            "AET": "FINISHED",
            "PEN": "FINISHED",
            "SUSP": "SUSPENDED",
            "INT": "SUSPENDED",
            "PST": "POSTPONED",
            "CANC": "POSTPONED",
            "ABD": "POSTPONED",
            "AWD": "FINISHED",
            "WO": "FINISHED",
        }
        return mapping.get(short_status, "SCHEDULED")
