"""
Footy Predict — Telegram Bot

Async Telegram bot using ``python-telegram-bot`` v21+.
Receives commands, runs analysis, and sends formatted reports
directly to the user's chat.

Commands
--------
- ``/start`` — Welcome message with command list
- ``/help`` — Full command reference
- ``/matchweek`` or ``/mw`` — Analyse all leagues' next matchweek
- ``/league <slug>`` — Analyse one league
- ``/match <Home> vs <Away>`` — Deep-dive on a specific fixture
- ``/picks`` — High-confidence picks only
- ``/accuracy`` — Accuracy stats
- ``/quota`` — API usage stats
- ``/fetch`` — Trigger data refresh

Messages are sent in HTML parse mode and automatically split
if they exceed Telegram's 4 096-character limit.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

from src.config import API_FOOTBALL, FOOTBALL_DATA_ORG, LEAGUES, TELEGRAM
from src.database import Database
from src.reporting.formatter import ReportFormatter

logger = logging.getLogger(__name__)

TELEGRAM_MAX_LEN = 4096


def _split_message(text: str, max_len: int = TELEGRAM_MAX_LEN) -> list[str]:
    """Split a long message into Telegram-safe chunks."""
    if len(text) <= max_len:
        return [text]

    messages: list[str] = []
    current = ""
    for line in text.split("\n"):
        candidate = current + "\n" + line if current else line
        if len(candidate) > max_len:
            if current:
                messages.append(current.strip())
            current = line
            while len(current) > max_len:
                messages.append(current[:max_len])
                current = current[max_len:]
        else:
            current = candidate
    if current.strip():
        messages.append(current.strip())
    return messages or [text[:max_len]]


class FootyBot:
    """Telegram bot for Footy Predict.

    Usage::

        db = Database()
        bot = FootyBot(db)
        bot.run()  # Blocks until stopped
    """

    def __init__(self, db: Database) -> None:
        self.db = db
        self.formatter = ReportFormatter()

    def run(self) -> None:
        """Build and start the bot (blocks until Ctrl+C)."""
        app = (
            Application.builder()
            .token(TELEGRAM.bot_token)
            .build()
        )

        # Register handlers
        app.add_handler(CommandHandler("start", self._cmd_start))
        app.add_handler(CommandHandler("help", self._cmd_help))
        app.add_handler(CommandHandler("matchweek", self._cmd_matchweek))
        app.add_handler(CommandHandler("mw", self._cmd_matchweek))
        app.add_handler(CommandHandler("league", self._cmd_league))
        app.add_handler(CommandHandler("match", self._cmd_match))
        app.add_handler(CommandHandler("picks", self._cmd_picks))
        app.add_handler(CommandHandler("accuracy", self._cmd_accuracy))
        app.add_handler(CommandHandler("quota", self._cmd_quota))
        app.add_handler(CommandHandler("fetch", self._cmd_fetch))

        app.run_polling(allowed_updates=Update.ALL_TYPES)

    # ───────────────────────────────────────────────────────────
    #  Command Handlers
    # ───────────────────────────────────────────────────────────

    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /start command."""
        text = (
            "⚽ <b>Footy Predict</b>\n\n"
            "Data-backed football analysis for SportyBet parlays.\n\n"
            "<b>Commands:</b>\n"
            "/mw — Analyse next matchweek\n"
            "/league &lt;slug&gt; — Analyse one league\n"
            "/match &lt;Home vs Away&gt; — Deep-dive\n"
            "/picks — High-confidence picks\n"
            "/accuracy — Prediction stats\n"
            "/quota — API usage\n"
            "/fetch — Refresh data\n"
            "/help — Full reference"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)

    async def _cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /help command."""
        leagues_list = ", ".join(f"<code>{s}</code>" for s in sorted(LEAGUES.keys()))
        text = (
            "📖 <b>Footy Predict — Command Reference</b>\n\n"
            "<b>/matchweek</b> or <b>/mw</b>\n"
            "Analyse all upcoming matches across all 12 leagues.\n\n"
            "<b>/league &lt;slug&gt;</b>\n"
            f"Analyse one league. Slugs: {leagues_list}\n\n"
            "<b>/match &lt;Home vs Away&gt;</b>\n"
            "Deep-dive on a specific fixture. Example:\n"
            "<code>/match Arsenal vs Chelsea</code>\n\n"
            "<b>/picks</b>\n"
            "Show only HIGH confidence recommendations.\n\n"
            "<b>/accuracy</b>\n"
            "Prediction accuracy dashboard.\n\n"
            "<b>/quota</b>\n"
            "API call usage for today.\n\n"
            "<b>/fetch</b>\n"
            "Trigger a data refresh from APIs.\n"
        )
        for chunk in _split_message(text):
            await update.message.reply_text(chunk, parse_mode=ParseMode.HTML)

    async def _cmd_matchweek(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /matchweek and /mw commands."""
        await update.message.reply_text("⏳ Analysing matchweek...")

        try:
            from src.analysis.engine import AnalysisEngine
            from src.tracking.calibrator import Calibrator

            weights = Calibrator.load_weights()
            engine = AnalysisEngine(self.db, weights=weights)

            from_date = datetime.now().strftime("%Y-%m-%d")
            to_date = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")

            analyses = engine.analyze_matchweek(from_date=from_date, to_date=to_date)

            if not analyses:
                await update.message.reply_text(
                    "No upcoming matches found. Run /fetch to pull latest data."
                )
                return

            # Summary
            summary = self.formatter.format_matchweek_summary_telegram(analyses)
            for chunk in _split_message(summary):
                await update.message.reply_text(chunk, parse_mode=ParseMode.HTML)

            # Individual match reports (top 10 by confidence)
            top_analyses = sorted(analyses, key=lambda a: -a.overall_confidence)[:10]
            for analysis in top_analyses:
                report = self.formatter.format_match_analysis_telegram(analysis)
                for chunk in _split_message(report):
                    await update.message.reply_text(chunk, parse_mode=ParseMode.HTML)

        except Exception as e:
            logger.exception("Matchweek analysis failed")
            await update.message.reply_text(f"❌ Error: {e}")

    async def _cmd_league(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /league <slug> command."""
        if not context.args:
            leagues_list = "\n".join(
                f"<code>{s}</code> — {LEAGUES[s].name}" for s in sorted(LEAGUES.keys())
            )
            await update.message.reply_text(
                f"📋 <b>Available leagues:</b>\n\n{leagues_list}\n\n"
                f"Usage: /league premier-league",
                parse_mode=ParseMode.HTML,
            )
            return

        slug = context.args[0].lower()
        if slug not in LEAGUES:
            await update.message.reply_text(f"❌ Unknown league: {slug}")
            return

        await update.message.reply_text(f"⏳ Analysing {LEAGUES[slug].name}...")

        try:
            from src.analysis.engine import AnalysisEngine
            from src.tracking.calibrator import Calibrator

            weights = Calibrator.load_weights()
            engine = AnalysisEngine(self.db, weights=weights)

            from_date = datetime.now().strftime("%Y-%m-%d")
            to_date = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")

            analyses = engine.analyze_matchweek(
                league_slug=slug, from_date=from_date, to_date=to_date
            )

            if not analyses:
                await update.message.reply_text(
                    f"No upcoming {LEAGUES[slug].name} matches. Try /fetch first."
                )
                return

            for analysis in analyses:
                report = self.formatter.format_match_analysis_telegram(analysis)
                for chunk in _split_message(report):
                    await update.message.reply_text(chunk, parse_mode=ParseMode.HTML)

        except Exception as e:
            logger.exception("League analysis failed")
            await update.message.reply_text(f"❌ Error: {e}")

    async def _cmd_match(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /match <Home vs Away> command."""
        if not context.args:
            await update.message.reply_text(
                "Usage: <code>/match Arsenal vs Chelsea</code>",
                parse_mode=ParseMode.HTML,
            )
            return

        fixture = " ".join(context.args)
        parts = fixture.lower().split(" vs ")
        if len(parts) != 2:
            await update.message.reply_text("❌ Format: /match Team A vs Team B")
            return

        home_query, away_query = parts[0].strip(), parts[1].strip()
        await update.message.reply_text(f"⏳ Analysing {fixture}...")

        try:
            from src.analysis.engine import AnalysisEngine
            from src.tracking.calibrator import Calibrator

            weights = Calibrator.load_weights()
            engine = AnalysisEngine(self.db, weights=weights)
            analysis = engine.analyze_specific_match(home_query, away_query)

            if not analysis:
                await update.message.reply_text(f"No upcoming match found for '{fixture}'")
                return

            report = self.formatter.format_match_analysis_telegram(analysis)
            for chunk in _split_message(report):
                await update.message.reply_text(chunk, parse_mode=ParseMode.HTML)

        except Exception as e:
            logger.exception("Match analysis failed")
            await update.message.reply_text(f"❌ Error: {e}")

    async def _cmd_picks(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /picks command — high-confidence recommendations."""
        await update.message.reply_text("⏳ Finding high-confidence picks...")

        try:
            from src.analysis.engine import AnalysisEngine
            from src.tracking.calibrator import Calibrator

            weights = Calibrator.load_weights()
            engine = AnalysisEngine(self.db, weights=weights)

            from_date = datetime.now().strftime("%Y-%m-%d")
            to_date = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")

            analyses = engine.analyze_matchweek(from_date=from_date, to_date=to_date)
            picks = engine.get_high_confidence_picks(analyses, min_confidence="HIGH")

            if not picks:
                await update.message.reply_text("No HIGH confidence picks found this matchweek.")
                return

            report = self.formatter.format_picks_telegram(picks, min_confidence="HIGH")
            for chunk in _split_message(report):
                await update.message.reply_text(chunk, parse_mode=ParseMode.HTML)

        except Exception as e:
            logger.exception("Picks command failed")
            await update.message.reply_text(f"❌ Error: {e}")

    async def _cmd_accuracy(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /accuracy command."""
        try:
            from src.tracking.accuracy import AccuracyTracker

            tracker = AccuracyTracker(self.db)
            dashboard = tracker.get_dashboard()

            if dashboard.get("total", 0) == 0:
                await update.message.reply_text(
                    "No prediction results recorded yet. "
                    "Run analysis, wait for matches to finish, then record results."
                )
                return

            report = self.formatter.format_accuracy_telegram(dashboard)
            for chunk in _split_message(report):
                await update.message.reply_text(chunk, parse_mode=ParseMode.HTML)

        except Exception as e:
            logger.exception("Accuracy command failed")
            await update.message.reply_text(f"❌ Error: {e}")

    async def _cmd_quota(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /quota command."""
        api_usage = self.db.get_api_usage_today("api_football")
        fdo_usage = self.db.get_api_usage_today("football_data_org")

        api_remaining = API_FOOTBALL.daily_limit - api_usage.get("count", 0)
        fdo_count = fdo_usage.get("count", 0)

        text = (
            "📊 <b>API Usage Today</b>\n\n"
            f"<b>API-Football:</b>\n"
            f"  Used: {api_usage.get('count', 0)} / {API_FOOTBALL.daily_limit}\n"
            f"  Remaining: {api_remaining}\n"
            f"  {'🟢 Plenty of quota' if api_remaining > 30 else '🟡 Getting low' if api_remaining > 10 else '🔴 Nearly exhausted'}\n\n"
            f"<b>Football-Data.org:</b>\n"
            f"  Used today: {fdo_count}\n"
            f"  Rate limit: {FOOTBALL_DATA_ORG.rate_limit_per_minute}/min (no daily cap)"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)

    async def _cmd_fetch(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /fetch command — trigger data refresh."""
        await update.message.reply_text("⏳ Fetching latest data from APIs...")

        try:
            from src.fetchers.api_football import APIFootballClient
            from src.fetchers.football_data_org import FootballDataOrgClient

            api_client = APIFootballClient(self.db)
            fdo_client = FootballDataOrgClient(self.db)

            total = 0
            errors: list[str] = []

            for slug, lg in LEAGUES.items():
                try:
                    if lg.has_fdo and FOOTBALL_DATA_ORG.key:
                        count = await fdo_client.fetch_and_store_fixtures(slug)
                        await fdo_client.fetch_and_store_standings(slug)
                    elif API_FOOTBALL.key:
                        count = await api_client.fetch_and_store_fixtures(slug)
                        await api_client.fetch_and_store_standings(slug)
                    else:
                        count = 0
                    total += count
                except Exception as e:
                    errors.append(f"{lg.name}: {e}")

            result = f"✅ Fetched data for {total} fixtures across {len(LEAGUES)} leagues."
            if errors:
                result += f"\n\n⚠️ {len(errors)} errors:\n" + "\n".join(errors[:5])

            await update.message.reply_text(result)

        except Exception as e:
            logger.exception("Fetch command failed")
            await update.message.reply_text(f"❌ Error: {e}")
