"""
Footy Predict — Historical CSV Loader

Downloads and parses CSV files from football-data.co.uk, then bulk-loads
them into the SQLite database via the Database class.

Supported CSV columns (football-data.co.uk):
    Div, Date, HomeTeam, AwayTeam, FTHG, FTAG, FTR,
    HTHG, HTAG, HTR, HS, AS, HST, AST, HC, AC, HF, AF,
    HY, AY, HR, AR, Referee, B365H, B365D, B365A

Usage:
    from src.fetchers.csv_loader import CSVLoader

    loader = CSVLoader(db)
    count = loader.load_season("premier-league", 2023)
    totals = loader.load_all_leagues_history()
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import pandas as pd

from src.config import (
    HISTORICAL_DIR,
    LEAGUES,
    LeagueConfig,
    get_csv_url,
    season_to_csv_code,
)
from src.database import Database

logger = logging.getLogger(__name__)

# ── Column mapping from CSV headers to normalised names ──────

_STATUS_MAP = {"H": "Home Win", "D": "Draw", "A": "Away Win"}

# Date formats found in football-data.co.uk CSVs
_DATE_FORMATS = ["%d/%m/%Y", "%d/%m/%y"]


def _parse_date(raw: str) -> str:
    """Parse a date string from the CSV into ISO 8601 format."""
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw.strip(), fmt).strftime("%Y-%m-%dT%H:%M:%S")
        except ValueError:
            continue
    # Last resort: return as-is (will be caught downstream)
    logger.warning("Could not parse date '%s', storing raw", raw)
    return raw


def _safe_int(val: Any) -> int | None:
    """Convert a value to int, returning None on failure."""
    if pd.isna(val):
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def _safe_float(val: Any) -> float | None:
    """Convert a value to float, returning None on failure."""
    if pd.isna(val):
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


class CSVLoader:
    """
    Downloads and ingests historical match CSVs from football-data.co.uk.

    This is intentionally **synchronous** — the CSVs are small (typically
    < 500 KB) and the SQLite writes must be serialised anyway.

    Attributes:
        db: The :class:`Database` instance used for storage.
        data_dir: Directory where downloaded CSVs are stored.
    """

    def __init__(
        self,
        db: Database,
        data_dir: Path = HISTORICAL_DIR,
    ) -> None:
        self.db = db
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)

    # ═══════════════════════════════════════════════════════════
    #  Download
    # ═══════════════════════════════════════════════════════════

    def download_season_csv(
        self,
        league_slug: str,
        season_year: int,
    ) -> Path:
        """
        Download a season CSV from football-data.co.uk.

        The file is saved to ``data/historical/<slug>_<season_code>.csv``.

        Args:
            league_slug: League identifier (e.g. ``'premier-league'``).
            season_year: Starting year of the season (e.g. ``2023`` for 2023-24).

        Returns:
            Path to the downloaded CSV file.

        Raises:
            ValueError: If the league has no CSV code configured.
            httpx.HTTPStatusError: If the download fails.
        """
        league = self._get_league(league_slug)
        if not league.has_csv:
            raise ValueError(
                f"League '{league_slug}' has no CSV code — "
                "historical CSV data is not available."
            )

        season_code = season_to_csv_code(season_year)
        url = get_csv_url(season_code, league.csv_code)  # type: ignore[arg-type]
        dest = self.data_dir / f"{league_slug}_{season_code}.csv"

        logger.info("Downloading %s → %s", url, dest)

        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()

        dest.write_bytes(resp.content)
        logger.info("Saved %d bytes to %s", len(resp.content), dest)
        return dest

    # ═══════════════════════════════════════════════════════════
    #  Parsing
    # ═══════════════════════════════════════════════════════════

    def parse_csv(
        self,
        csv_path: Path,
        league_slug: str,
    ) -> list[dict[str, Any]]:
        """
        Parse a football-data.co.uk CSV into normalised match dicts.

        Each dict contains:
            date, home_team, away_team, home_score, away_score,
            ht_home_score, ht_away_score, result, referee,
            home_stats, away_stats (sub-dicts with shots, corners, etc.),
            odds (B365 home/draw/away).

        Args:
            csv_path: Path to the CSV file.
            league_slug: League identifier for context.

        Returns:
            List of normalised match dicts.
        """
        try:
            df = pd.read_csv(csv_path, encoding="utf-8", on_bad_lines="skip")
        except UnicodeDecodeError:
            df = pd.read_csv(csv_path, encoding="latin-1", on_bad_lines="skip")

        # Drop completely empty rows (common at end of file)
        df.dropna(how="all", inplace=True)

        # Require minimum columns
        required = {"Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(
                f"CSV {csv_path.name} is missing required columns: {missing}"
            )

        matches: list[dict[str, Any]] = []

        for _, row in df.iterrows():
            # Skip rows where essential data is NaN
            if pd.isna(row.get("Date")) or pd.isna(row.get("HomeTeam")):
                continue

            home_stats: dict[str, Any] = {}
            away_stats: dict[str, Any] = {}

            # Shots
            if "HS" in df.columns:
                home_stats["shots"] = _safe_int(row.get("HS"))
            if "AS" in df.columns:
                away_stats["shots"] = _safe_int(row.get("AS"))

            # Shots on target
            if "HST" in df.columns:
                home_stats["shots_on_target"] = _safe_int(row.get("HST"))
            if "AST" in df.columns:
                away_stats["shots_on_target"] = _safe_int(row.get("AST"))

            # Corners
            if "HC" in df.columns:
                home_stats["corners"] = _safe_int(row.get("HC"))
            if "AC" in df.columns:
                away_stats["corners"] = _safe_int(row.get("AC"))

            # Fouls
            if "HF" in df.columns:
                home_stats["fouls"] = _safe_int(row.get("HF"))
            if "AF" in df.columns:
                away_stats["fouls"] = _safe_int(row.get("AF"))

            # Yellow cards
            if "HY" in df.columns:
                home_stats["yellow_cards"] = _safe_int(row.get("HY"))
            if "AY" in df.columns:
                away_stats["yellow_cards"] = _safe_int(row.get("AY"))

            # Red cards
            if "HR" in df.columns:
                home_stats["red_cards"] = _safe_int(row.get("HR"))
            if "AR" in df.columns:
                away_stats["red_cards"] = _safe_int(row.get("AR"))

            # Odds
            odds: dict[str, float | None] = {}
            if "B365H" in df.columns:
                odds["b365_home"] = _safe_float(row.get("B365H"))
            if "B365D" in df.columns:
                odds["b365_draw"] = _safe_float(row.get("B365D"))
            if "B365A" in df.columns:
                odds["b365_away"] = _safe_float(row.get("B365A"))

            match_dict: dict[str, Any] = {
                "date": _parse_date(str(row["Date"])),
                "home_team": str(row["HomeTeam"]).strip(),
                "away_team": str(row["AwayTeam"]).strip(),
                "home_score": _safe_int(row["FTHG"]),
                "away_score": _safe_int(row["FTAG"]),
                "result": _STATUS_MAP.get(str(row.get("FTR", "")).strip(), "Unknown"),
                "ht_home_score": _safe_int(row.get("HTHG")),
                "ht_away_score": _safe_int(row.get("HTAG")),
                "referee": str(row["Referee"]).strip() if pd.notna(row.get("Referee")) else None,
                "home_stats": home_stats,
                "away_stats": away_stats,
                "odds": odds,
            }
            matches.append(match_dict)

        logger.info(
            "Parsed %d matches from %s for %s", len(matches), csv_path.name, league_slug
        )
        return matches

    # ═══════════════════════════════════════════════════════════
    #  Load into Database
    # ═══════════════════════════════════════════════════════════

    def load_season(
        self,
        league_slug: str,
        season_year: int,
    ) -> int:
        """
        Download, parse, and insert a single season into the database.

        Upserts the league, teams, matches, and per-team match statistics.

        Args:
            league_slug: League identifier.
            season_year: Starting year of the season.

        Returns:
            Number of matches inserted/updated.
        """
        league = self._get_league(league_slug)

        # Ensure league exists in DB
        league_id = self.db.upsert_league(
            name=league.name,
            country=league.country,
            slug=league.slug,
            api_football_id=league.api_football_id,
            fdo_code=league.football_data_org_code,
            csv_code=league.csv_code,
            tier=league.tier,
        )

        # Download & parse
        csv_path = self.download_season_csv(league_slug, season_year)
        matches = self.parse_csv(csv_path, league_slug)

        season_label = f"{season_year}-{str(season_year + 1)[-2:]}"
        count = 0

        for m in matches:
            try:
                # Upsert teams
                home_team_id = self.db.upsert_team(
                    name=m["home_team"],
                    league_id=league_id,
                )
                away_team_id = self.db.upsert_team(
                    name=m["away_team"],
                    league_id=league_id,
                )

                # Determine match status
                status = "FINISHED" if m["home_score"] is not None else "SCHEDULED"

                # Upsert match
                match_id = self.db.upsert_match(
                    league_id=league_id,
                    season=season_label,
                    date=m["date"],
                    home_team_id=home_team_id,
                    away_team_id=away_team_id,
                    status=status,
                    home_score=m["home_score"],
                    away_score=m["away_score"],
                    ht_home_score=m["ht_home_score"],
                    ht_away_score=m["ht_away_score"],
                    referee=m["referee"],
                    source="csv",
                )

                # Upsert match stats (home team)
                home_stats = {k: v for k, v in m["home_stats"].items() if v is not None}
                if home_stats:
                    self.db.upsert_match_stats(
                        match_id=match_id,
                        team_id=home_team_id,
                        **home_stats,
                    )

                # Upsert match stats (away team)
                away_stats = {k: v for k, v in m["away_stats"].items() if v is not None}
                if away_stats:
                    self.db.upsert_match_stats(
                        match_id=match_id,
                        team_id=away_team_id,
                        **away_stats,
                    )

                count += 1
            except Exception as exc:
                logger.warning(
                    "Failed to insert match %s vs %s (%s): %s",
                    m.get("home_team"),
                    m.get("away_team"),
                    m.get("date"),
                    exc,
                )

        logger.info(
            "Loaded %d/%d matches for %s season %s",
            count, len(matches), league_slug, season_label,
        )
        return count

    def load_all_history(
        self,
        league_slug: str,
        start_season: int = 2018,
        end_season: int | None = None,
    ) -> int:
        """
        Load multiple seasons for a single league.

        Args:
            league_slug: League identifier.
            start_season: First season start year to load (inclusive).
            end_season: Last season start year to load (inclusive).
                Defaults to current season.

        Returns:
            Total number of matches loaded across all seasons.
        """
        league = self._get_league(league_slug)
        if end_season is None:
            end_season = league.get_current_season()

        total = 0
        for year in range(start_season, end_season + 1):
            try:
                total += self.load_season(league_slug, year)
            except Exception as exc:
                logger.warning(
                    "Failed to load %s season %d: %s", league_slug, year, exc
                )

        logger.info(
            "Loaded %d total matches for %s (%d–%d)",
            total, league_slug, start_season, end_season,
        )
        return total

    def load_all_leagues_history(
        self,
        start_season: int = 2018,
        end_season: int | None = None,
    ) -> dict[str, int]:
        """
        Load history for every league that has CSV data.

        Args:
            start_season: First season start year to load.
            end_season: Last season start year to load.

        Returns:
            Dict mapping league slug → total matches loaded.
        """
        results: dict[str, int] = {}

        for slug, league in LEAGUES.items():
            if not league.has_csv:
                logger.info("Skipping %s — no CSV data available", slug)
                continue

            try:
                count = self.load_all_history(
                    slug,
                    start_season=start_season,
                    end_season=end_season,
                )
                results[slug] = count
            except Exception as exc:
                logger.error("Failed to load history for %s: %s", slug, exc)
                results[slug] = 0

        logger.info("Finished loading history for %d leagues", len(results))
        return results

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
