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
    ├── picks [--min-confidence LVL]   High-confidence recommendations
    ├── parlay --size N                Parlay suggestion
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
@click.pass_context
def fetch(ctx: click.Context, league: str | None) -> None:
    """Fetch latest fixtures, standings, and team stats from APIs."""
    db = ctx.obj["db"]

    from src.config import API_FOOTBALL, API_ONLY_LEAGUES, FDO_LEAGUES, FOOTBALL_DATA_ORG, LEAGUES
    from src.fetchers.api_football import APIFootballClient
    from src.fetchers.football_data_org import FootballDataOrgClient

    if league and league not in LEAGUES:
        console.print(f"[red]Unknown league: '{league}'[/red]")
        console.print(f"Available: {', '.join(sorted(LEAGUES.keys()))}")
        sys.exit(1)

    async def _fetch():
        total_fixtures = 0
        total_standings = 0

        api_client = APIFootballClient(db)
        fdo_client = FootballDataOrgClient(db)

        target_leagues = [LEAGUES[league]] if league else list(LEAGUES.values())

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            for lg in target_leagues:
                task_id = progress.add_task(f"Fetching {lg.name}...", total=None)

                try:
                    # Use FDO for leagues it covers (fixtures + standings)
                    if lg.has_fdo and FOOTBALL_DATA_ORG.key:
                        count = await fdo_client.fetch_and_store_fixtures(lg.slug)
                        total_fixtures += count
                        st_count = await fdo_client.fetch_and_store_standings(lg.slug)
                        total_standings += st_count
                    elif API_FOOTBALL.key:
                        count = await api_client.fetch_and_store_fixtures(lg.slug)
                        total_fixtures += count
                        st_count = await api_client.fetch_and_store_standings(lg.slug)
                        total_standings += st_count
                    else:
                        console.print(f"[yellow]⚠ No API key for {lg.name} — skipping[/yellow]")

                    # Team stats from API-Football (for all leagues)
                    if API_FOOTBALL.key:
                        await api_client.fetch_and_store_team_stats(lg.slug)

                except Exception as e:
                    console.print(f"[red]✗ {lg.name}: {e}[/red]")

                progress.update(task_id, completed=True)

        # Show quota status
        quota = api_client.get_quota_status() if API_FOOTBALL.key else {}

        console.print()
        console.print(f"[green]✓ Fetched {total_fixtures} fixtures, {total_standings} standings[/green]")
        if quota:
            remaining = quota.get("remaining", "?")
            console.print(f"[dim]API-Football quota remaining: {remaining} calls[/dim]")

    _run_async(_fetch())


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
            task = progress.add_task("Loading all historical data...", total=None)
            results = loader.load_all_leagues_history()
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

    console.print(table)


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
@click.pass_context
def analyze_matchweek(ctx: click.Context, league: str | None, from_date: str | None, to_date: str | None) -> None:
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
        panel = formatter.format_match_analysis_cli(analysis)
        console.print(panel)
        console.print()

    # Store predictions in DB
    _store_predictions(db, analyses)

    console.print(f"\n[green]✓ Analysed {len(analyses)} matches[/green]")


@analyze.command("match")
@click.argument("fixture")
@click.pass_context
def analyze_match(ctx: click.Context, fixture: str) -> None:
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

    panel = formatter.format_match_analysis_cli(analysis)
    console.print(panel)

    # Store prediction
    _store_predictions(db, [analysis])


# ═══════════════════════════════════════════════════════════════
#  picks — Filtered recommendations
# ═══════════════════════════════════════════════════════════════

@cli.command()
@click.option("--min-confidence", "-c", default="HIGH",
              type=click.Choice(["HIGH", "MEDIUM", "LOW"], case_sensitive=False),
              help="Minimum confidence level.")
@click.option("--league", "-l", default=None, help="League slug filter.")
@click.pass_context
def picks(ctx: click.Context, min_confidence: str, league: str | None) -> None:
    """Show high-confidence picks from the latest analysis."""
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
    filtered = engine.get_high_confidence_picks(analyses, min_confidence=min_confidence.upper())

    if not filtered:
        console.print(f"[yellow]No {min_confidence} confidence picks found.[/yellow]")
        return

    panel = formatter.format_picks_cli(filtered, min_confidence=min_confidence.upper())
    console.print(panel)

    console.print(f"\n[green]✓ Found {len(filtered)} picks at {min_confidence}+ confidence[/green]")


# ═══════════════════════════════════════════════════════════════
#  parlay — Parlay builder
# ═══════════════════════════════════════════════════════════════

@cli.command()
@click.option("--size", "-n", default=50, type=int, help="Number of picks for the parlay.")
@click.option("--min-confidence", "-c", default="MEDIUM",
              type=click.Choice(["HIGH", "MEDIUM", "LOW"], case_sensitive=False),
              help="Minimum confidence for included picks.")
@click.option("--league", "-l", default=None, help="League slug filter.")
@click.pass_context
def parlay(ctx: click.Context, size: int, min_confidence: str, league: str | None) -> None:
    """Suggest picks for a parlay of N games."""
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
    # Use string path for hot reloading to function correctly
    uvicorn.run("src.api.app:app", host=host, port=port, reload=True)


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
