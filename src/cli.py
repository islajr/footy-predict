"""
Footy Predict — CLI Interface

Click-based command-line interface.  This is the primary way to interact
with the system.

Command tree::

    footy
    ├── fetch [--league SLUG]          Refresh data from APIs
    ├── load-history [--league SLUG]   Bulk-load historical CSVs
    ├── status                         API quota / cache / DB stats
    ├── analyze
    │   ├── matchweek [--league SLUG]  Analyse next matchweek
    │   └── match "Team A vs Team B"   Deep-dive on a specific fixture
    ├── picks [--min-confidence LVL] [--market CODE] [--min-odds N]  Recommendations, tiered by confidence
    ├── parlay --size N [--min-odds N] Parlay suggestion
    ├── report [--min-odds N] [--days N]  Weekly decision-support pick report
    ├── record                         Record results for finished matches
    ├── accuracy [--market M] [--lg L] Accuracy dashboard
    ├── calibrate                      Run weight calibration
    └── bot start                      Launch Telegram bot
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime, timedelta

import click
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from src.main import init_app

console = Console()
logger = logging.getLogger("footy")


# ═══════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════

def _run_async(coro):
    """Bridge async code into Click's synchronous handlers."""
    return asyncio.run(coro)


def _setup_logging(verbose: bool) -> None:
    """Configure structured logging."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s │ %(name)-20s │ %(levelname)-7s │ %(message)s",
        datefmt="%H:%M:%S",
    )


# ═══════════════════════════════════════════════════════════════
#  Root Group
# ═══════════════════════════════════════════════════════════════

@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging.")
@click.pass_context
def cli(ctx: click.Context, verbose: bool) -> None:
    """⚽ Footy Predict — Data-backed football analysis for SportyBet parlays."""
    _setup_logging(verbose)
    ctx.ensure_object(dict)
    ctx.obj["db"] = init_app()
    ctx.obj["verbose"] = verbose


# ═══════════════════════════════════════════════════════════════
#  fetch — Pull data from APIs
# ═══════════════════════════════════════════════════════════════

@cli.command()
@click.option("--league", "-l", default=None, help="League slug (e.g. premier-league). Omit for all leagues.")
@click.option("--stats", is_flag=True, default=False,
              help="Also fetch per-match statistics (/fixtures/statistics) for finished matches without stats yet. "
                   "Each match costs 1 API call. Use --stats-limit to control how many are fetched per run.")
@click.option("--stats-limit", default=20, type=int, show_default=True,
              help="Maximum number of finished matches to backfill with stats per league per run. "
                   "Keep conservative (default: 20) to preserve daily API quota.")
@click.pass_context
def fetch(ctx: click.Context, league: str | None, stats: bool, stats_limit: int) -> None:
    """Fetch latest fixtures, standings, and team stats from APIs.

    Run this daily (or before running analysis) to keep the database current.
    Use --stats once quota permits to backfill xG and in-match statistics,
    which activates the xg_quality analysis factor.

    \\b
    Examples:
        footy fetch                              # All leagues, no stats
        footy fetch --league premier-league      # One league only
        footy fetch --stats                      # Include match stats (20/league)
        footy fetch --stats --stats-limit 5      # Conservative: 5 matches/league
    """
    db = ctx.obj["db"]

    from src.config import API_FOOTBALL, FDO_LEAGUES, FOOTBALL_DATA_ORG, LEAGUES
    from src.fetchers.api_football import APIFootballClient
    from src.fetchers.football_data_org import FootballDataOrgClient

    if league and league not in LEAGUES:
        console.print(f"[red]Unknown league: '{league}'[/red]")
        console.print(f"Available: {', '.join(sorted(LEAGUES.keys()))}")
        sys.exit(1)

    async def _fetch():
        total_fixtures = 0
        total_standings = 0
        total_stats = 0

        api_key = db.get_setting("api_football_key") or API_FOOTBALL.key
        fdo_key = db.get_setting("football_data_org_key") or FOOTBALL_DATA_ORG.key

        target_leagues = [LEAGUES[league]] if league else list(LEAGUES.values())

        async with APIFootballClient(db) as api_client, FootballDataOrgClient(db) as fdo_client:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console,
            ) as progress:
                for lg in target_leagues:
                    task_id = progress.add_task(f"Fetching {lg.name}...", total=None)

                    try:
                        # Fixtures + standings: prefer FDO for leagues it covers
                        if lg.has_fdo and fdo_key:
                            count = await fdo_client.fetch_and_store_fixtures(lg.slug)
                            total_fixtures += count
                            st_count = await fdo_client.fetch_and_store_standings(lg.slug)
                            total_standings += st_count
                        elif api_key:
                            count = await api_client.fetch_and_store_fixtures(lg.slug)
                            total_fixtures += count
                            st_count = await api_client.fetch_and_store_standings(lg.slug)
                            total_standings += st_count
                        else:
                            console.print(f"[yellow]⚠ No API key for {lg.name} — skipping[/yellow]")

                        # Team season stats (always from API-Football)
                        if api_key:
                            await api_client.fetch_and_store_team_stats(lg.slug)

                        # Per-match statistics — opt-in via --stats flag
                        # Each call costs 1 API request; quota guard built into method
                        if stats and api_key:
                            progress.update(task_id, description=f"Backfilling match stats for {lg.name}...")
                            n_statted = await api_client.fetch_and_store_match_stats(
                                lg.slug, limit=stats_limit
                            )
                            total_stats += n_statted

                    except Exception as e:
                        console.print(f"[red]✗ {lg.name}: {e}[/red]")

                    progress.update(task_id, completed=True)

            # Quota summary
            quota = api_client.get_quota_status() if api_key else {}

        console.print()
        console.print(f"[green]✓ Fetched {total_fixtures} fixtures, {total_standings} standings[/green]")
        if stats:
            console.print(f"[green]✓ Backfilled match stats for {total_stats} fixture(s)[/green]")
        if quota:
            remaining = quota.get("remaining", "?")
            used = quota.get("used", "?")
            limit = quota.get("limit", "?")
            console.print(f"[dim]API-Football quota: {used}/{limit} used today, {remaining} remaining[/dim]")

    _run_async(_fetch())



# ═══════════════════════════════════════════════════════════════
#  fetch-upcoming — Pre-weekend quota-aware fixture sync
# ═══════════════════════════════════════════════════════════════

@cli.command("fetch-upcoming")
@click.option("--days", "-d", default=7, type=int, show_default=True,
              help="Sync fixtures for the next N days (default: 7).")
@click.option("--budget", default=60, type=int, show_default=True,
              help="Max API-Football calls to spend in this run (default: 60). "
                   "Leaves headroom for other operations within the 100/day cap.")
@click.option("--league", "-l", default=None,
              help="Restrict to a single league slug. Omit for all active leagues.")
@click.pass_context
def fetch_upcoming(ctx: click.Context, days: int, budget: int, league: str | None) -> None:
    """Sync upcoming fixtures and standings — optimised for pre-weekend use.

    Fetches fixtures for the next DAYS days plus current standings for every
    active league. Quota-aware: aborts early if fewer than 20 API calls remain
    or the run would exceed --budget calls.

    Run this on Thursday evening before building your parlay slip so the
    database reflects the latest form, standings, and fixture list.

    \\b
    Examples:
        footy fetch-upcoming                    # Next 7 days, all leagues
        footy fetch-upcoming --days 10          # Extend window slightly
        footy fetch-upcoming --league premier-league  # One league only
        footy fetch-upcoming --budget 40        # Conservative budget
    """
    db = ctx.obj["db"]

    from src.config import API_FOOTBALL, FDO_LEAGUES, FOOTBALL_DATA_ORG, LEAGUES
    from src.fetchers.api_football import APIFootballClient
    from src.fetchers.football_data_org import FootballDataOrgClient

    ABORT_THRESHOLD = 20  # Never leave fewer than this many calls unused

    if league and league not in LEAGUES:
        console.print(f"[red]Unknown league: '{league}'[/red]")
        console.print(f"Available: {', '.join(sorted(LEAGUES.keys()))}")
        sys.exit(1)

    async def _fetch_upcoming() -> None:
        api_key = db.get_setting("api_football_key") or API_FOOTBALL.key
        fdo_key = db.get_setting("football_data_org_key") or FOOTBALL_DATA_ORG.key

        target_leagues = [LEAGUES[league]] if league else list(LEAGUES.values())
        calls_this_run = 0
        total_fixtures = 0
        total_standings = 0
        skipped = []

        async with APIFootballClient(db) as api_client, \
                   FootballDataOrgClient(db) as fdo_client:

            # ── Pre-flight quota check ──────────────────────────────────
            quota = api_client.get_quota_status() if api_key else {}
            remaining_before = quota.get("remaining", 0)

            console.print(
                f"[dim]Quota before run: {quota.get('used', 0)}/{quota.get('daily_limit', 100)} used "
                f"({remaining_before} remaining)[/dim]"
            )

            if api_key and remaining_before < ABORT_THRESHOLD:
                console.print(
                    f"[red]✗ Aborting — only {remaining_before} API calls remain today "
                    f"(minimum required: {ABORT_THRESHOLD}).[/red]"
                )
                console.print("[dim]Wait for the quota to reset at midnight UTC.[/dim]")
                return

            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console,
            ) as progress:
                for lg in target_leagues:
                    # ── Per-league budget guard ─────────────────────────
                    if api_key:
                        quota_now = api_client.get_quota_status()
                        remaining_now = quota_now.get("remaining", 0)
                        if remaining_now < ABORT_THRESHOLD or calls_this_run >= budget:
                            skipped.append(lg.name)
                            continue

                    task_id = progress.add_task(
                        f"[cyan]{lg.name}[/cyan] — fixtures + standings", total=None
                    )

                    try:
                        if lg.has_fdo and fdo_key:
                            # FDO: fixtures (filtered to next N days server-side)
                            count = await fdo_client.fetch_and_store_fixtures(lg.slug)
                            total_fixtures += count
                            st = await fdo_client.fetch_and_store_standings(lg.slug)
                            total_standings += st
                            # FDO doesn't consume API-Football quota
                        elif api_key:
                            count = await api_client.fetch_and_store_fixtures(lg.slug)
                            total_fixtures += count
                            calls_this_run += 1

                            st = await api_client.fetch_and_store_standings(lg.slug)
                            total_standings += st
                            calls_this_run += 1
                        else:
                            progress.update(task_id, description=f"[yellow]⚠ {lg.name} — no API key, skipped[/yellow]")
                            progress.update(task_id, completed=True)
                            continue

                    except Exception as e:
                        console.print(f"[red]✗ {lg.name}: {e}[/red]")

                    progress.update(task_id, completed=True)

            # ── Post-run summary ────────────────────────────────────────
            console.print()
            console.print(
                f"[green]✓ Synced {total_fixtures} fixtures, {total_standings} standings "
                f"across {len(target_leagues) - len(skipped)} league(s)[/green]"
            )
            if skipped:
                console.print(
                    f"[yellow]⚠ Skipped (quota/budget): {', '.join(skipped)}[/yellow]"
                )

            if api_key:
                quota_after = api_client.get_quota_status()
                used_this_run = quota_after.get("used", 0) - quota.get("used", 0)
                console.print(
                    f"[dim]API-Football: {used_this_run} call(s) used this run · "
                    f"{quota_after.get('remaining', '?')} remaining today[/dim]"
                )

    _run_async(_fetch_upcoming())


# ═══════════════════════════════════════════════════════════════
#  load-history — Bulk CSV import
# ═══════════════════════════════════════════════════════════════

@cli.command("load-history")
@click.option("--league", "-l", default=None, help="League slug. Omit for all leagues with CSV data.")
@click.option("--seasons", "-s", default=3, type=int, help="Number of past seasons to load (default: 3).")
@click.pass_context
def load_history(ctx: click.Context, league: str | None, seasons: int) -> None:
    """Load historical match data from football-data.co.uk CSVs."""
    db = ctx.obj["db"]

    from src.config import LEAGUES
    from src.fetchers.csv_loader import CSVLoader

    loader = CSVLoader(db)

    if league:
        if league not in LEAGUES:
            console.print(f"[red]Unknown league: '{league}'[/red]")
            sys.exit(1)
        lg = LEAGUES[league]
        if not lg.has_csv:
            console.print(f"[yellow]{lg.name} has no CSV data available[/yellow]")
            sys.exit(1)

        current_season = lg.get_current_season()
        start_season = current_season - seasons + 1

        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
            task = progress.add_task(f"Loading {lg.name} history...", total=None)
            count = loader.load_all_history(league, start_season=start_season, end_season=current_season)
            progress.update(task, completed=True)

        console.print(f"[green]✓ Loaded {count} matches for {lg.name}[/green]")
    else:
        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
            task = progress.add_task(f"Loading last {seasons} season(s) for all leagues...", total=None)
            results = loader.load_all_leagues_history(seasons=seasons)
            progress.update(task, completed=True)

        total = sum(results.values())
        console.print(f"\n[green]✓ Loaded {total} matches across {len(results)} leagues[/green]")
        for lg_slug, count in sorted(results.items()):
            console.print(f"  {lg_slug}: {count} matches")


# ═══════════════════════════════════════════════════════════════
#  status — Dashboard
# ═══════════════════════════════════════════════════════════════

@cli.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show API quota, cache stats, and database summary."""
    db = ctx.obj["db"]

    from src.config import API_FOOTBALL, FOOTBALL_DATA_ORG
    from src.reporting.formatter import ReportFormatter

    formatter = ReportFormatter()

    # API usage
    api_usage = db.get_api_usage_today("api_football")
    fdo_usage = db.get_api_usage_today("football_data_org")

    panel = formatter.format_quota_status_cli(
        api_football_usage=api_usage,
        fdo_usage=fdo_usage,
    )
    console.print(panel)

    # DB stats
    leagues = db.get_all_leagues()
    table = Table(title="📊 Database Summary", show_header=True)
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green", justify="right")

    table.add_row("Leagues", str(len(leagues)))

    with db._connect() as conn:
        team_count = conn.execute("SELECT COUNT(*) FROM teams").fetchone()[0]
        match_count = conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
        prediction_count = conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]
        finished = conn.execute("SELECT COUNT(*) FROM matches WHERE status = 'FINISHED'").fetchone()[0]
        upcoming = conn.execute("SELECT COUNT(*) FROM matches WHERE status IN ('SCHEDULED', 'NOT_STARTED')").fetchone()[0]

    table.add_row("Teams", str(team_count))
    table.add_row("Total matches", str(match_count))
    table.add_row("  Finished", str(finished))
    table.add_row("  Upcoming", str(upcoming))
    table.add_row("Predictions", str(prediction_count))

    # Standings health. A table that exists but ranks nothing reads as absent
    # to league_position and opponent_strength, so it is worth seeing here
    # rather than discovering it in a rationale that says "no data available".
    from src.analysis.factors import standings_are_ranked

    with db._connect() as conn:
        sets = conn.execute(
            "SELECT DISTINCT league_id, season FROM standings"
        ).fetchall()
    unranked = [
        s for s in sets if not standings_are_ranked(db.get_standings(s["league_id"], s["season"]))
    ]
    table.add_row("Standings sets", str(len(sets)))
    if unranked:
        table.add_row("  Unranked (ignored)", f"[yellow]{len(unranked)}[/yellow]")

    console.print(table)

    if unranked:
        slugs = {l["id"]: l["slug"] for l in leagues}
        detail = ", ".join(
            sorted(f"{slugs.get(s['league_id'], s['league_id'])} {s['season']}" for s in unranked)
        )
        console.print(
            f"[yellow]![/yellow] No usable ranking in: {detail}\n"
            "  Position factors sit out these league-seasons. Pre-season tables "
            "fill in after matchday 1."
        )


# ═══════════════════════════════════════════════════════════════
#  analyze — Analysis commands
# ═══════════════════════════════════════════════════════════════

@cli.group()
def analyze():
    """Analyse matches with data-backed factor scoring."""
    pass


@analyze.command("matchweek")
@click.option("--league", "-l", default=None, help="League slug. Omit for all leagues.")
@click.option("--from", "from_date", default=None, help="Start date (YYYY-MM-DD).")
@click.option("--to", "to_date", default=None, help="End date (YYYY-MM-DD).")
@click.option(
    "--full", "-F", is_flag=True, default=True,
    help="Display full untruncated rationale in analysis panels. Default: True.",
)
@click.pass_context
def analyze_matchweek(
    ctx: click.Context,
    league: str | None,
    from_date: str | None,
    to_date: str | None,
    full: bool,
) -> None:
    """Analyse all matches in the upcoming matchweek."""
    db = ctx.obj["db"]

    from src.analysis.engine import AnalysisEngine
    from src.reporting.formatter import ReportFormatter
    from src.tracking.calibrator import Calibrator

    weights = Calibrator.load_weights()
    engine = AnalysisEngine(db, weights=weights)
    formatter = ReportFormatter()

    # Default date range: next 7 days
    if not from_date:
        from_date = datetime.now().strftime("%Y-%m-%d")
    if not to_date:
        to_date = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")

    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
        task = progress.add_task("Analysing matchweek...", total=None)
        analyses = engine.analyze_matchweek(
            league_slug=league,
            from_date=from_date,
            to_date=to_date,
        )
        progress.update(task, completed=True)

    if not analyses:
        console.print("[yellow]No upcoming matches found for the given criteria.[/yellow]")
        console.print("[dim]Try 'footy fetch' to pull latest fixtures first.[/dim]")
        return

    # Summary table
    summary = formatter.format_matchweek_summary_cli(analyses)
    console.print(summary)

    # Detailed panels for each match
    for analysis in analyses:
        panel = formatter.format_match_analysis_cli(analysis, full_rationale=full)
        console.print(panel)
        console.print()

    # Store predictions in DB
    _store_predictions(db, analyses)

    console.print(f"\n[green]✓ Analysed {len(analyses)} matches[/green]")


@analyze.command("match")
@click.argument("fixture")
@click.option(
    "--full", "-F", is_flag=True, default=True,
    help="Display full untruncated rationale in analysis panels. Default: True.",
)
@click.pass_context
def analyze_match(ctx: click.Context, fixture: str, full: bool) -> None:
    """Deep-dive analysis of a specific fixture.

    FIXTURE should be in the format "Team A vs Team B".
    """
    db = ctx.obj["db"]

    from src.analysis.engine import AnalysisEngine
    from src.reporting.formatter import ReportFormatter
    from src.tracking.calibrator import Calibrator

    # Parse "Team A vs Team B"
    parts = fixture.lower().split(" vs ")
    if len(parts) != 2:
        console.print("[red]Format: 'Team A vs Team B'[/red]")
        sys.exit(1)

    home_query, away_query = parts[0].strip(), parts[1].strip()

    weights = Calibrator.load_weights()
    engine = AnalysisEngine(db, weights=weights)
    formatter = ReportFormatter()

    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
        task = progress.add_task(f"Analysing {fixture}...", total=None)
        analysis = engine.analyze_specific_match(home_query, away_query)
        progress.update(task, completed=True)

    if not analysis:
        console.print(f"[yellow]No upcoming match found for '{fixture}'[/yellow]")
        console.print("[dim]Make sure teams are spelled correctly and data has been fetched.[/dim]")
        return

    panel = formatter.format_match_analysis_cli(analysis, full_rationale=full)
    console.print(panel)

    # Store prediction
    _store_predictions(db, [analysis])



# ═══════════════════════════════════════════════════════════════
#  picks — Filtered recommendations
# ═══════════════════════════════════════════════════════════════

@cli.command()
@click.option(
    "--min-confidence", "-c",
    default=None,
    type=click.Choice(["HIGH", "MEDIUM", "LOW"], case_sensitive=False),
    help="Filter to this confidence tier and above. Defaults to showing all tiers.",
)
@click.option("--league", "-l", default=None, help="League slug filter.")
@click.option(
    "--market", "-m", default=None,
    help="Filter to a specific market code (e.g. 'over-under-2.5', '1x2', 'win-to-nil').",
)
@click.option(
    "--min-odds", "-o", default=0.0, type=float,
    help=(
        "Minimum model implied odds (e.g. 1.32 ≈ 1.20 real odds after bookmaker margin). "
        "Default: 0.0 (show all). Set VALUE_MIN_ODDS env var for a persistent default."
    ),
)
@click.pass_context
def picks(
    ctx: click.Context,
    min_confidence: str | None,
    league: str | None,
    market: str | None,
    min_odds: float,
) -> None:
    """Show all picks from the latest analysis, tiered by confidence.

    All three confidence tiers (HIGH / MEDIUM / LOW) are shown by default.
    LOW-confidence picks are displayed last with visual de-emphasis so they
    are visible but clearly distinguished.

    Use --min-confidence to restrict to a specific tier and above.
    Use --market to filter to a single market code.
    Use --min-odds to apply a value floor (e.g. 1.32 targets ≥1.20 real odds).

    The value zone is defined as HIGH confidence + implied odds ≥ min-odds.
    The system surface evidence and rationale — you make the final pick decision.
    """
    from src.config import VALUE_MIN_ODDS
    db = ctx.obj["db"]

    from src.analysis.engine import AnalysisEngine
    from src.reporting.formatter import ReportFormatter
    from src.tracking.calibrator import Calibrator

    weights = Calibrator.load_weights()
    engine = AnalysisEngine(db, weights=weights)
    formatter = ReportFormatter()

    from_date = datetime.now().strftime("%Y-%m-%d")
    to_date = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")

    analyses = engine.analyze_matchweek(
        league_slug=league, from_date=from_date, to_date=to_date
    )

    # Effective odds floor: explicit arg overrides config default
    odds_floor = min_odds if min_odds > 0 else 0.0  # 0.0 = no filter unless user sets one

    # Apply market filter
    if market:
        mkt_lower = market.lower()
        for analysis in analyses:
            recs = getattr(analysis, "market_recommendations", [])
            filtered_recs = [
                r for r in recs
                if (getattr(r, "market_code", "") or "").lower() == mkt_lower
            ]
            if hasattr(analysis, "market_recommendations"):
                object.__setattr__(analysis, "market_recommendations", filtered_recs)

    # Apply odds filter
    if odds_floor > 0:
        for analysis in analyses:
            recs = getattr(analysis, "market_recommendations", [])
            filtered_recs = [r for r in recs if getattr(r, "implied_odds", 0) >= odds_floor]
            object.__setattr__(analysis, "market_recommendations", filtered_recs)

    if not analyses:
        console.print("[yellow]No upcoming matches found.[/yellow]")
        return

    panel = formatter.format_picks_cli(
        analyses,
        min_confidence=min_confidence.upper() if min_confidence else None,
    )
    console.print(panel)

    tier_note = f" (≥ {min_confidence.upper()})" if min_confidence else " (all tiers)"
    mkt_note = f" — market: {market}" if market else ""
    odds_note = f" — odds ≥ {odds_floor:.2f}" if odds_floor > 0 else ""
    console.print(
        f"\n[green]✓ {len(analyses)} match(es) analysed{tier_note}{mkt_note}{odds_note}[/green]"
    )
    if odds_floor == 0 and min_confidence and min_confidence.upper() == "HIGH":
        console.print(
            f"[dim]Tip: add --min-odds {VALUE_MIN_ODDS} to apply the value zone filter "
            f"(≈ ≥1.20 real odds after bookmaker margin)[/dim]"
        )


# ═══════════════════════════════════════════════════════════════
#  parlay — Parlay builder
# ═══════════════════════════════════════════════════════════════

@cli.command()
@click.option("--size", "-n", default=50, type=int, help="Number of picks for the parlay.")
@click.option("--min-confidence", "-c", default="MEDIUM",
              type=click.Choice(["HIGH", "MEDIUM", "LOW"], case_sensitive=False),
              help="Minimum confidence for included picks.")
@click.option("--league", "-l", default=None, help="League slug filter.")
@click.option(
    "--min-odds", "-o", default=0.0, type=float,
    help="Minimum model implied odds for parlay picks (e.g. 1.32). Default: 0.0 (no filter).",
)
@click.pass_context
def parlay(ctx: click.Context, size: int, min_confidence: str, league: str | None, min_odds: float) -> None:
    """Suggest picks for a parlay of N games.

    Use --min-odds 1.32 to restrict to value zone picks (≈ ≥1.20 real SportyBet odds).
    """
    db = ctx.obj["db"]

    from src.analysis.engine import AnalysisEngine
    from src.reporting.formatter import ReportFormatter
    from src.tracking.calibrator import Calibrator

    weights = Calibrator.load_weights()
    engine = AnalysisEngine(db, weights=weights)
    formatter = ReportFormatter()

    from_date = datetime.now().strftime("%Y-%m-%d")
    to_date = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")

    analyses = engine.analyze_matchweek(league_slug=league, from_date=from_date, to_date=to_date)
    parlay_picks = engine.suggest_parlay(analyses, size=size, min_confidence=min_confidence.upper())

    # Apply odds floor — remove picks below the value zone threshold
    if min_odds > 0:
        parlay_picks = [
            (a, r) for a, r in parlay_picks
            if getattr(r, "implied_odds", 0) >= min_odds
        ]

    if not parlay_picks:
        console.print("[yellow]Not enough qualifying picks for the requested parlay size.[/yellow]")
        return

    # Build parlay display
    table = Table(title=f"🎯 {len(parlay_picks)}-Pick Parlay Suggestion", show_header=True, show_lines=True)
    table.add_column("#", style="dim", width=4)
    table.add_column("Match", style="cyan", min_width=25)
    table.add_column("League", style="magenta")
    table.add_column("Market", style="white")
    table.add_column("Pick", style="green bold")
    table.add_column("Prob", justify="right")
    table.add_column("Odds", justify="right", style="yellow")
    table.add_column("Conf", justify="center")

    combined_prob = 1.0
    for i, (analysis, rec) in enumerate(parlay_picks, 1):
        combined_prob *= rec.probability
        conf_emoji = {"HIGH": "🟢", "MEDIUM": "🟡", "LOW": "🔴"}.get(rec.confidence, "⚪")

        table.add_row(
            str(i),
            f"{analysis.home_team} vs {analysis.away_team}",
            analysis.league,
            rec.market_name,
            rec.pick,
            f"{rec.probability:.0%}",
            f"{rec.implied_odds:.2f}",
            f"{conf_emoji} {rec.confidence}",
        )

    console.print(table)

    # Combined odds
    combined_odds = 1.0 / combined_prob if combined_prob > 0 else float("inf")
    console.print(f"\n[bold]Combined probability:[/bold] {combined_prob:.2e}")
    console.print(f"[bold]Combined decimal odds:[/bold] {combined_odds:.2f}")
    console.print(f"\n[dim]Note: These are the safest picks from the analysis. "
                  f"Always apply your own judgement.[/dim]")


# ═══════════════════════════════════════════════════════════════
#  record — Record match results
# ═══════════════════════════════════════════════════════════════

@cli.command()
@click.pass_context
def record(ctx: click.Context) -> None:
    """Record results for completed matches and evaluate predictions."""
    db = ctx.obj["db"]

    from src.tracking.recorder import ResultRecorder

    recorder = ResultRecorder(db)

    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
        task = progress.add_task("Recording results...", total=None)
        summary = recorder.auto_record_all()
        progress.update(task, completed=True)

    if summary["total_matches"] == 0:
        console.print("[yellow]No completed matches with unrecorded predictions found.[/yellow]")
        console.print("[dim]Run 'footy fetch' to update match statuses, then 'footy record'.[/dim]")
        return

    console.print(f"\n[green]✓ Processed {summary['total_matches']} matches[/green]")
    console.print(f"  Predictions evaluated: {summary['total_predictions']}")
    console.print(f"  Correct: [green]{summary['correct']}[/green]")
    console.print(f"  Incorrect: [red]{summary['incorrect']}[/red]")
    if summary.get("void", 0):
        console.print(f"  Void: {summary['void']}")


# ═══════════════════════════════════════════════════════════════
#  accuracy — Accuracy dashboard
# ═══════════════════════════════════════════════════════════════

@cli.command()
@click.option("--market", "-m", default=None, help="Filter by market code (e.g. over-under-2.5).")
@click.option("--league", "-l", default=None, help="Filter by league slug.")
@click.pass_context
def accuracy(ctx: click.Context, market: str | None, league: str | None) -> None:
    """Display prediction accuracy dashboard."""
    db = ctx.obj["db"]

    from src.tracking.accuracy import AccuracyTracker

    tracker = AccuracyTracker(db)
    dashboard = tracker.get_dashboard(market=market, league=league)

    if dashboard.get("total", 0) == 0:
        console.print("[yellow]No prediction results recorded yet.[/yellow]")
        console.print("[dim]Run 'footy record' after matches finish to start tracking.[/dim]")
        return

    tracker.print_dashboard(market=market, league=league)


# ═══════════════════════════════════════════════════════════════
#  calibrate — Weight adjustment
# ═══════════════════════════════════════════════════════════════

@cli.command()
@click.option("--reset", is_flag=True, help="Reset weights to defaults instead of calibrating.")
@click.pass_context
def calibrate(ctx: click.Context, reset: bool) -> None:
    """Run the weight calibration loop (requires 100+ predictions)."""
    db = ctx.obj["db"]

    from src.tracking.calibrator import Calibrator

    cal = Calibrator(db)

    if reset:
        weights = cal.reset_weights()
        console.print("[green]✓ Weights reset to defaults[/green]")
        for name, w in sorted(weights.items(), key=lambda x: -x[1]):
            console.print(f"  {name}: {w:.3f}")
        return

    readiness = cal.get_calibration_readiness()
    if not readiness["ready"]:
        console.print(
            f"[yellow]Not enough data: {readiness['total_predictions']}/{readiness['required']} "
            f"predictions ({readiness['progress_pct']:.0f}% ready)[/yellow]"
        )
        return

    try:
        report = cal.calibrate()
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        return

    # Display changes
    table = Table(title="⚖️ Calibration Results", show_header=True)
    table.add_column("Factor", style="cyan")
    table.add_column("Old Weight", justify="right")
    table.add_column("New Weight", justify="right")
    table.add_column("Change", justify="right")
    table.add_column("Reason", style="dim")

    for change in report["changes"]:
        adj = change["adjustment"]
        if abs(adj) > 1e-6:
            color = "green" if adj > 0 else "red"
            adj_str = f"[{color}]{adj:+.3f}[/{color}]"
        else:
            adj_str = "[dim]—[/dim]"

        table.add_row(
            change["factor"],
            f"{change['old']:.3f}",
            f"{report['new_weights'].get(change['factor'], 0):.3f}",
            adj_str,
            change["reason"][:60],
        )

    console.print(table)
    console.print(
        f"\n[green]✓ Calibration complete. Baseline accuracy: {report['baseline_accuracy']:.1%}[/green]"
    )


# ═══════════════════════════════════════════════════════════════
#  bot — Telegram bot launcher
# ═══════════════════════════════════════════════════════════════

@cli.group()
def bot():
    """Telegram bot commands."""
    pass


@bot.command("start")
@click.pass_context
def bot_start(ctx: click.Context) -> None:
    """Start the Telegram bot (runs in foreground)."""
    db = ctx.obj["db"]

    from src.config import TELEGRAM

    if not TELEGRAM.bot_token:
        console.print("[red]TELEGRAM_BOT_TOKEN not set in .env[/red]")
        sys.exit(1)
    if not TELEGRAM.chat_id:
        console.print("[yellow]⚠ TELEGRAM_CHAT_ID not set — bot will work but can't send proactive messages[/yellow]")

    from src.reporting.telegram_bot import FootyBot

    bot_instance = FootyBot(db)
    console.print("[green]🤖 Starting Footy Predict Telegram bot...[/green]")
    console.print("[dim]Press Ctrl+C to stop[/dim]")

    try:
        bot_instance.run()
    except KeyboardInterrupt:
        console.print("\n[yellow]Bot stopped.[/yellow]")


# ═══════════════════════════════════════════════════════════════
#  serve — Web Application Launcher
# ═══════════════════════════════════════════════════════════════

@cli.command()
@click.option("--host", default="127.0.0.1", help="Host interface to bind to.")
@click.option("--port", default=8000, type=int, help="Port to run the server on.")
def serve(host: str, port: int) -> None:
    """Start the FastAPI local web application server."""
    console.print(f"[green]🚀 Starting Footy Predict Server on http://{host}:{port}...[/green]")
    import uvicorn
    uvicorn.run("src.api.app:app", host=host, port=port, reload=False)


# ═══════════════════════════════════════════════════════════════
#  Prediction storage helper
# ═══════════════════════════════════════════════════════════════


def _store_predictions(db, analyses) -> int:
    """Store analysis results as predictions in the database.

    Returns count of predictions stored.
    """
    import json

    count = 0
    for analysis in analyses:
        # Build factor breakdown for the calibrator
        factor_scores = {}
        for f in analysis.factor_results:
            factor_scores[f.name] = {
                "score": round(f.score, 4),
                "confidence": round(f.confidence, 4),
            }

        for rec in analysis.market_recommendations:
            try:
                db.insert_prediction(
                    match_id=analysis.match_id,
                    market=rec.market_code,
                    pick=rec.pick,
                    probability=rec.probability,
                    confidence=rec.confidence,
                    implied_odds=rec.implied_odds,
                    rationale=rec.rationale,
                    risk_factors=rec.risk_factors,
                    supporting_stats=rec.supporting_stats,
                    factor_scores=factor_scores,
                )
                count += 1
            except Exception:
                # Duplicate or constraint violation — skip
                pass

    return count


# ═══════════════════════════════════════════════════════════════
#  report — Weekly decision-support pick report
# ═══════════════════════════════════════════════════════════════

@cli.command()
@click.option(
    "--min-odds", "-o", default=None, type=float,
    help="Minimum model implied odds (default: VALUE_MIN_ODDS from config, currently 1.32).",
)
@click.option(
    "--min-confidence", "-c", default="MEDIUM",
    type=click.Choice(["HIGH", "MEDIUM", "LOW"], case_sensitive=False),
    help="Minimum confidence tier to include. Default: MEDIUM (the correct value zone when combined with --min-odds).",
)
@click.option("--league", "-l", default=None, help="League slug filter.")
@click.option(
    "--days", "-d", default=7, type=int,
    help="How many days ahead to look for fixtures. Default: 7.",
)
@click.option(
    "--output", "-f", default=None, type=click.Path(),
    help="Optional file path to save the report (plain text).",
)
@click.option(
    "--full", "-F", is_flag=True, default=False,
    help="Display full untruncated rationale in terminal output.",
)
@click.pass_context
def report(
    ctx: click.Context,
    min_odds: float | None,
    min_confidence: str,
    league: str | None,
    days: int,
    output: str | None,
    full: bool,
) -> None:
    """Generate a weekly decision-support pick report.

    Shows the best value-zone pick(s) per upcoming fixture with full rationale
    and alternative picks. Designed to be read alongside your own research.

    \b
    Value zone = MEDIUM (or HIGH) confidence AND implied odds >= min-odds.
    Default: MEDIUM confidence + 1.32 model odds (≈ ≥1.20 real SportyBet odds).

    Why MEDIUM as default? HIGH picks (P≥0.80) have model implied odds ≤1.25,
    which is below the 1.32 value floor. The real value zone lives in MEDIUM
    (P 0.60-0.76, model odds 1.32-1.67, real odds ≈1.19-1.50). Use
    --min-confidence HIGH to see all high-confidence picks regardless of odds.

    \b
    Examples:
        footy report                           # MEDIUM picks, odds>=1.32, next 7 days
        footy report --days 3                  # this weekend only
        footy report --full                    # display full untruncated rationale
        footy report --min-odds 1.40           # stricter odds floor
        footy report --min-confidence HIGH     # all HIGH picks (any odds)
        footy report -c HIGH --min-odds 0      # HIGH picks, no odds filter
        footy report --output weekend.txt      # save to file (always full rationale)
    """
    from src.config import VALUE_MIN_ODDS
    from src.analysis.engine import AnalysisEngine
    from src.tracking.calibrator import Calibrator
    from rich.rule import Rule

    db = ctx.obj["db"]
    weights = Calibrator.load_weights()
    engine = AnalysisEngine(db, weights=weights)

    odds_floor = min_odds if min_odds is not None else VALUE_MIN_ODDS
    conf_floor = min_confidence.upper()

    from_date = datetime.now().strftime("%Y-%m-%d")
    to_date = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")

    analyses = engine.analyze_matchweek(
        league_slug=league, from_date=from_date, to_date=to_date
    )

    if not analyses:
        console.print("[yellow]No upcoming matches found in the selected window.[/yellow]")
        return

    # Confidence tier ordering
    _tier_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    _conf_floor_val = _tier_order.get(conf_floor, 0)

    def _passes(rec) -> bool:
        tier_ok = _tier_order.get(getattr(rec, "confidence", "LOW"), 2) <= _conf_floor_val
        odds_ok = getattr(rec, "implied_odds", 0) >= odds_floor
        return tier_ok and odds_ok

    _conf_colours = {"HIGH": "green bold", "MEDIUM": "yellow", "LOW": "dim"}
    _conf_icons = {"HIGH": "🟢", "MEDIUM": "🟡", "LOW": "⚪"}

    # ── Header ─────────────────────────────────────────────────────
    lines: list[str] = []

    date_range = f"{from_date}  →  {to_date}"
    header = (
        f"\n[bold cyan]FOOTY PREDICT — WEEKEND PICKS REPORT[/bold cyan]\n"
        f"[dim]{date_range}  ·  {conf_floor}+ confidence  ·  odds ≥ {odds_floor:.2f} (model)  "
        f"≈ ≥{odds_floor * 0.90:.2f} real SportyBet odds[/dim]"
    )
    console.print(header)
    console.print(Rule(style="cyan"))

    total_value_picks = 0
    matches_with_picks = 0

    for analysis in analyses:
        recs = getattr(analysis, "market_recommendations", [])
        value_recs = [r for r in recs if _passes(r)]

        if not value_recs:
            continue

        # Sort: highest probability first within tier
        value_recs.sort(key=lambda r: (-_tier_order.get(r.confidence, 2), -r.probability))

        top = value_recs[0]
        alternates = value_recs[1:5]  # up to 4 alternates

        matches_with_picks += 1
        total_value_picks += len(value_recs)

        home = getattr(analysis, "home_team", "Home")
        away = getattr(analysis, "away_team", "Away")
        league_name = getattr(analysis, "league", "")
        match_date = getattr(analysis, "match_date", "")
        dq = getattr(analysis, "data_quality", "MEDIUM")
        risks = getattr(analysis, "risk_flags", [])

        # ── Match header ──────────────────────────────────────────
        dq_colour = {"HIGH": "green", "MEDIUM": "yellow", "LOW": "red"}.get(dq, "white")
        console.print(
            f"\n  [bold white]{home}[/bold white] [dim]vs[/dim] [bold white]{away}[/bold white]"
            f"  [dim]{league_name}  ·  {match_date}[/dim]"
            f"  [dim]data:[/dim] [{dq_colour}]{dq}[/{dq_colour}]"
        )

        # ── Top pick ─────────────────────────────────────────────
        conf_c = _conf_colours.get(top.confidence, "white")
        conf_i = _conf_icons.get(top.confidence, "⚪")
        console.print(
            f"  {conf_i} [bold]TOP PICK:[/bold]  "
            f"[{conf_c}]{top.pick}[/{conf_c}]"
            f"  [cyan]{top.probability:.0%}[/cyan]"
            f"  [yellow]{top.implied_odds:.2f} odds[/yellow]"
            f"  [{conf_c}]{top.confidence}[/{conf_c}]"
        )

        # Rationale
        rationale = getattr(top, "rationale", "") or ""
        if rationale:
            if full or len(rationale) <= 220:
                short = rationale.rstrip()
            else:
                short = rationale[:220].rstrip() + "…"
            console.print(f"  [dim]  {short}[/dim]")

        # Risk flags
        if risks:
            console.print(f"  [red dim]  ⚠ {' · '.join(risks[:3])}[/red dim]")

        # Alternates
        if alternates:
            alt_parts = []
            for a in alternates:
                alt_parts.append(
                    f"[dim]{a.pick}[/dim] [cyan]{a.probability:.0%}[/cyan] [yellow]{a.implied_odds:.2f}[/yellow]"
                )
            console.print("  [dim]  Also:[/dim] " + "  [dim]·[/dim] ".join(alt_parts))

        console.print(Rule(style="dim", characters="─"))

    # ── Summary ────────────────────────────────────────────────────
    if matches_with_picks == 0:
        console.print(
            f"\n[yellow]No value zone picks found (conf ≥ {conf_floor}, odds ≥ {odds_floor:.2f}).[/yellow]\n"
            f"[dim]Try: footy report --min-odds 1.20 --min-confidence MEDIUM[/dim]"
        )
        return

    console.print(
        f"\n[green bold]✓ {matches_with_picks} matches · {total_value_picks} value zone picks[/green bold]"
        f"  [dim](conf ≥ {conf_floor} · odds ≥ {odds_floor:.2f})[/dim]"
    )
    console.print(
        "[dim]Note: odds shown are model implied odds (1/P). "
        "Real SportyBet odds ≈ model odds × 0.90 after bookmaker margin. "
        "This report is decision-support — do your own research before placing bets.[/dim]\n"
    )

    # Optional file output (plain text, no Rich markup)
    if output:
        try:
            import re
            _ansi_strip = re.compile(r"\[/?[a-z /#]+\]")

            with open(output, "w") as f:
                f.write(f"FOOTY PREDICT — WEEKEND PICKS REPORT\n")
                f.write(f"{date_range} · {conf_floor}+ · odds≥{odds_floor:.2f}\n")
                f.write("=" * 70 + "\n\n")

                for analysis in analyses:
                    recs = getattr(analysis, "market_recommendations", [])
                    value_recs = [r for r in recs if _passes(r)]
                    if not value_recs:
                        continue
                    value_recs.sort(
                        key=lambda r: (-_tier_order.get(r.confidence, 2), -r.probability)
                    )
                    top = value_recs[0]
                    home = getattr(analysis, "home_team", "Home")
                    away = getattr(analysis, "away_team", "Away")
                    league_name = getattr(analysis, "league", "")
                    match_date = getattr(analysis, "match_date", "")
                    f.write(f"{home} vs {away}  |  {league_name}  |  {match_date}\n")
                    f.write(
                        f"  TOP: {top.pick}  {top.probability:.0%}  {top.implied_odds:.2f} odds  {top.confidence}\n"
                    )
                    rationale = getattr(top, "rationale", "") or ""
                    if rationale:
                        f.write(f"  {rationale}\n")
                    if len(value_recs) > 1:
                        alts = "  Also: " + "  |  ".join(
                            f"{r.pick} {r.probability:.0%} {r.implied_odds:.2f}"
                            for r in value_recs[1:5]
                        )
                        f.write(alts + "\n")
                    f.write("-" * 70 + "\n")

            console.print(f"[green]Report saved → {output}[/green]")
        except Exception as e:
            console.print(f"[red]Could not save report: {e}[/red]")

