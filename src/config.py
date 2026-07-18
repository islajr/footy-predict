"""
Footy Predict — Central Configuration

League definitions, API settings, cache TTLs, market configurations,
and analysis weights. All environment-specific values load from .env.
"""

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ═══════════════════════════════════════════════════════════════
#  Paths
# ═══════════════════════════════════════════════════════════════

PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"
HISTORICAL_DIR = DATA_DIR / "historical"

# DATABASE_URL allows the db path to be overridden via environment variable.
# On Railway, set DATABASE_URL=/data/footy.db (mounted volume).
# Locally this resolves to the repo-root footy.db — no change needed.
_db_url = os.getenv("DATABASE_URL")
DB_PATH: Path = Path(_db_url) if _db_url else PROJECT_ROOT / "footy.db"

# Ensure data directories exist
DATA_DIR.mkdir(exist_ok=True)
HISTORICAL_DIR.mkdir(parents=True, exist_ok=True)


# ═══════════════════════════════════════════════════════════════
#  API Configuration
# ═══════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class APIFootballConfig:
    """API-Football (api-sports.io) — primary data source."""

    base_url: str = "https://v3.football.api-sports.io"
    key: str = ""
    daily_limit: int = 100
    rate_limit_per_minute: int = 10

    def __post_init__(self):
        # Frozen dataclass workaround for env loading
        object.__setattr__(self, "key", os.getenv("API_FOOTBALL_KEY", ""))


@dataclass(frozen=True)
class FootballDataOrgConfig:
    """Football-Data.org API v4 — secondary source for 7 leagues."""

    base_url: str = "https://api.football-data.org/v4"
    key: str = ""
    rate_limit_per_minute: int = 10

    def __post_init__(self):
        object.__setattr__(self, "key", os.getenv("FOOTBALL_DATA_ORG_KEY", ""))


@dataclass(frozen=True)
class TelegramConfig:
    """Telegram bot for alerts and reports."""

    bot_token: str = ""
    chat_id: str = ""

    def __post_init__(self):
        object.__setattr__(self, "bot_token", os.getenv("TELEGRAM_BOT_TOKEN", ""))
        object.__setattr__(self, "chat_id", os.getenv("TELEGRAM_CHAT_ID", ""))


API_FOOTBALL = APIFootballConfig()
FOOTBALL_DATA_ORG = FootballDataOrgConfig()
TELEGRAM = TelegramConfig()


# ═══════════════════════════════════════════════════════════════
#  Cache TTLs (seconds)
# ═══════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class CacheTTL:
    """Time-to-live for cached API responses."""

    standings: int = 86_400       # 24 hours
    fixtures: int = 21_600        # 6 hours
    team_stats: int = 604_800     # 7 days
    h2h: int = 2_592_000          # 30 days
    match_stats: int = 2_592_000  # 30 days (historical, stable)
    predictions: int = 259_200    # 3 days


CACHE_TTL = CacheTTL()


# ═══════════════════════════════════════════════════════════════
#  League Definitions — all 12 tracked leagues
# ═══════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class LeagueConfig:
    """
    Configuration for a single league.

    Attributes:
        name: Display name of the league.
        country: Country the league belongs to.
        slug: URL/CLI-friendly identifier.
        api_football_id: ID used in API-Football requests.
        football_data_org_code: Code for Football-Data.org (None if not covered).
        csv_code: Code for football-data.co.uk CSV downloads (None if unavailable).
        tier: "core" (top 5 European) or "extended".
        season_format: "european" (Aug–May, keyed by start year) or
                       "calendar" (Jan–Dec, keyed by year).
    """

    name: str
    country: str
    slug: str
    api_football_id: int
    football_data_org_code: str | None = None
    csv_code: str | None = None
    tier: str = "extended"
    season_format: str = "european"

    @property
    def has_fdo(self) -> bool:
        """Whether Football-Data.org covers this league."""
        return self.football_data_org_code is not None

    @property
    def has_csv(self) -> bool:
        """Whether historical CSVs are available for this league."""
        return self.csv_code is not None

    def get_current_season(self) -> int:
        """
        Return the current season key for API calls.

        European leagues (Aug–May): returns the start year (e.g. 2025 for 2025-26).
        Calendar leagues (Jan–Dec): returns the calendar year.
        """
        now = datetime.now()
        if self.season_format == "calendar":
            return now.year
        # European: before August → still in the season that started last year
        return now.year if now.month >= 8 else now.year - 1

    def get_season_label(self, season: int | None = None) -> str:
        """Human-readable season label, e.g. '2025-26' or '2026'."""
        if season is None:
            season = self.get_current_season()
        if self.season_format == "calendar":
            return str(season)
        return f"{season}-{str(season + 1)[-2:]}"


# ── League registry ────────────────────────────────────────────

LEAGUES: dict[str, LeagueConfig] = {
    # ── Core 5 ──────────────────────────────────────────────
    "premier-league": LeagueConfig(
        name="Premier League",
        country="England",
        slug="premier-league",
        api_football_id=39,
        football_data_org_code="PL",
        csv_code="E0",
        tier="core",
    ),
    "la-liga": LeagueConfig(
        name="La Liga",
        country="Spain",
        slug="la-liga",
        api_football_id=140,
        football_data_org_code="PD",
        csv_code="SP1",
        tier="core",
    ),
    "serie-a": LeagueConfig(
        name="Serie A",
        country="Italy",
        slug="serie-a",
        api_football_id=135,
        football_data_org_code="SA",
        csv_code="I1",
        tier="core",
    ),
    "bundesliga": LeagueConfig(
        name="Bundesliga",
        country="Germany",
        slug="bundesliga",
        api_football_id=78,
        football_data_org_code="BL1",
        csv_code="D1",
        tier="core",
    ),
    "ligue-1": LeagueConfig(
        name="Ligue 1",
        country="France",
        slug="ligue-1",
        api_football_id=61,
        football_data_org_code="FL1",
        csv_code="F1",
        tier="core",
    ),
    # ── Extended leagues ────────────────────────────────────
    "liga-portugal": LeagueConfig(
        name="Liga Portugal",
        country="Portugal",
        slug="liga-portugal",
        api_football_id=94,
        football_data_org_code="PPL",
        csv_code="P1",
    ),
    "eredivisie": LeagueConfig(
        name="Eredivisie",
        country="Netherlands",
        slug="eredivisie",
        api_football_id=88,
        football_data_org_code="DED",
        csv_code="N1",
    ),
    "scottish-premiership": LeagueConfig(
        name="Scottish Premiership",
        country="Scotland",
        slug="scottish-premiership",
        api_football_id=179,
        csv_code="SC0",
    ),
    "super-lig": LeagueConfig(
        name="Süper Lig",
        country="Turkey",
        slug="super-lig",
        api_football_id=203,
        csv_code="T1",
    ),
    "saudi-pro-league": LeagueConfig(
        name="Saudi Pro League",
        country="Saudi Arabia",
        slug="saudi-pro-league",
        api_football_id=307,
        # No FDO, no CSV — relies entirely on API-Football
    ),
    "eliteserien": LeagueConfig(
        name="Eliteserien",
        country="Norway",
        slug="eliteserien",
        api_football_id=103,
        csv_code="N0",
        season_format="calendar",
    ),
    "belgian-pro-league": LeagueConfig(
        name="Belgian Pro League",
        country="Belgium",
        slug="belgian-pro-league",
        api_football_id=144,
        csv_code="B1",
    ),
}

# ── Convenience lookups ─────────────────────────────────────

LEAGUES_BY_API_FOOTBALL_ID: dict[int, LeagueConfig] = {
    lg.api_football_id: lg for lg in LEAGUES.values()
}

LEAGUES_BY_FDO_CODE: dict[str, LeagueConfig] = {
    lg.football_data_org_code: lg
    for lg in LEAGUES.values()
    if lg.football_data_org_code
}

FDO_LEAGUES: list[LeagueConfig] = [lg for lg in LEAGUES.values() if lg.has_fdo]
API_ONLY_LEAGUES: list[LeagueConfig] = [lg for lg in LEAGUES.values() if not lg.has_fdo]


# ═══════════════════════════════════════════════════════════════
#  SportyBet Market Definitions
# ═══════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class MarketDef:
    """Definition of a betting market with SportyBet naming."""

    code: str
    name: str
    sportybet_name: str
    description: str
    category: str  # "result", "goals", "team_performance"


MARKETS: dict[str, MarketDef] = {
    "1x2": MarketDef(
        "1x2", "Match Result", "1X2",
        "Home (1), Draw (X), Away (2)", "result",
    ),
    "double-chance": MarketDef(
        "double-chance", "Double Chance", "DC",
        "1X, X2, or 12", "result",
    ),
    "draw-no-bet": MarketDef(
        "draw-no-bet", "Draw No Bet", "DNB",
        "Stake refunded on draw", "result",
    ),
    "over-under-0.5": MarketDef(
        "over-under-0.5", "Over/Under 0.5 Goals", "Over/Under 0.5",
        "Total goals over or under 0.5", "goals",
    ),
    "over-under-1.5": MarketDef(
        "over-under-1.5", "Over/Under 1.5 Goals", "Over/Under 1.5",
        "Total goals over or under 1.5", "goals",
    ),
    "over-under-2.5": MarketDef(
        "over-under-2.5", "Over/Under 2.5 Goals", "Over/Under 2.5",
        "Total goals over or under 2.5", "goals",
    ),
    "over-under-3.5": MarketDef(
        "over-under-3.5", "Over/Under 3.5 Goals", "Over/Under 3.5",
        "Total goals over or under 3.5", "goals",
    ),
    "gg-ng": MarketDef(
        "gg-ng", "Both Teams to Score", "GG/NG",
        "GG = both score, NG = at least one blanks", "goals",
    ),
    "win-to-nil": MarketDef(
        "win-to-nil", "Win to Nil", "Win to Nil",
        "Team wins and keeps a clean sheet", "team_performance",
    ),
    "win-either-half": MarketDef(
        "win-either-half", "Win Either Half", "Win Either Half",
        "Team wins at least one half", "team_performance",
    ),
    "handicap": MarketDef(
        "handicap", "Handicap", "Handicap",
        "Goal advantage/disadvantage applied", "result",
    ),
    "team-over-under": MarketDef(
        "team-over-under", "Team Over/Under", "Home/Away Over/Under",
        "Total goals by a specific team", "goals",
    ),
}


# ═══════════════════════════════════════════════════════════════
#  Analysis Factor Weights (initial defaults; adjusted by calibrator)
# ═══════════════════════════════════════════════════════════════

FACTOR_WEIGHTS: dict[str, float] = {
    # Form cluster — deliberately superior; these are the most predictive signals.
    # A team's recent results, venue-specific record, and momentum are the
    # strongest indicators of performance. These weights are intentionally
    # higher and are NOT reduced to accommodate new factors.
    "recent_form":        0.155,
    "home_form":          0.130,
    "away_form":          0.130,
    # Contextual cluster — team quality signals independent of recent run
    "scoring_frequency":  0.100,
    "defensive_record":   0.100,
    "head_to_head":       0.075,   # -0.015 vs Phase A
    "league_position":    0.075,   # -0.005 vs Phase A; home/away splits now enrich this
    "attacking_matchup":  0.060,
    "venue_advantage":    0.055,
    # Nuance cluster — real signals but secondary to form; weights reduced to
    # accommodate opponent_strength without touching the form cluster above.
    "opponent_strength":  0.045,   # Phase B: adjusts form quality by opponent rank
    "momentum":           0.040,   # Phase C: +0.020 vs Phase B (stronger recency signal)
    "goal_timing":        0.015,   # Phase C: -0.010 vs Phase B
    "scoring_patterns":   0.000,   # Phase C: zeroed — subsumed by scoring_frequency
    "xg_quality":         0.010,   # unchanged; grows as match_stats data populates
    "rest_days":          0.005,   # local tool — manual syncs mean less urgency
    "rivalry":            0.005,   # unchanged at floor weight
}

# Sanity check: weights must sum to 1.0
assert abs(sum(FACTOR_WEIGHTS.values()) - 1.0) < 1e-9, (
    f"Factor weights sum to {sum(FACTOR_WEIGHTS.values())}, expected 1.0"
)


# ═══════════════════════════════════════════════════════════════
#  Confidence Thresholds & Calibration
# ═══════════════════════════════════════════════════════════════

CONFIDENCE_THRESHOLDS: dict[str, float] = {
    "HIGH":   0.75,
    "MEDIUM": 0.60,
    "LOW":    0.00,
}

# Minimum recorded predictions before the calibrator starts adjusting weights
MIN_PREDICTIONS_FOR_CALIBRATION = 100


# ═══════════════════════════════════════════════════════════════
#  Historical CSV Configuration
# ═══════════════════════════════════════════════════════════════

CSV_BASE_URL = "https://www.football-data.co.uk/mmz4281"


def get_csv_url(season_code: str, division_code: str) -> str:
    """
    Build the download URL for a historical CSV from football-data.co.uk.

    Args:
        season_code: Two-digit season code, e.g. "2526" for 2025-26.
        division_code: League code, e.g. "E0" for English Premier League.

    Returns:
        Full URL to the CSV file.

    Example:
        >>> get_csv_url("2526", "E0")
        'https://www.football-data.co.uk/mmz4281/2526/E0.csv'
    """
    return f"{CSV_BASE_URL}/{season_code}/{division_code}.csv"


def season_to_csv_code(season_year: int) -> str:
    """
    Convert a season start year to the two-digit code used by football-data.co.uk.

    Args:
        season_year: The starting year, e.g. 2025 for 2025-26 season.

    Returns:
        Code like "2526".
    """
    return f"{str(season_year)[-2:]}{str(season_year + 1)[-2:]}"
