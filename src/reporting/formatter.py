"""
Footy Predict — Report Formatter

Generates rich, information-dense reports for three output channels:

- **CLI** — Uses the ``rich`` library for coloured panels, tables, and
  progress-bar-style factor scores rendered directly in the terminal.
- **Telegram** — HTML-formatted messages that respect the 4 096-character
  limit imposed by the Telegram Bot API.
- **Markdown** — (future) static Markdown files for archiving.

The formatter is stateless; every method is a pure transformation from
analysis data-structures to display-ready output.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, Any

from rich.columns import Columns
from rich.console import Group
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

if TYPE_CHECKING:
    from src.analysis.engine import MatchAnalysis
    from src.analysis.factors import FactorResult
    from src.analysis.markets import MarketRecommendation

try:
    from src.config import FACTOR_WEIGHTS
except ImportError:
    FACTOR_WEIGHTS: dict[str, float] = {}

# ═══════════════════════════════════════════════════════════════
#  Constants
# ═══════════════════════════════════════════════════════════════

TELEGRAM_MAX_LENGTH = 4096

CONFIDENCE_STYLES: dict[str, dict[str, str]] = {
    "HIGH":   {"color": "green",  "emoji": "🟢", "label": "HIGH"},
    "MEDIUM": {"color": "yellow", "emoji": "🟡", "label": "MEDIUM"},
    "LOW":    {"color": "red",    "emoji": "🔴", "label": "LOW"},
}

RESULT_EMOJI = {"W": "✅", "D": "➖", "L": "❌"}


def _bar(value: float, width: int = 12) -> str:
    """Render a fractional value (0–1) as a Unicode block bar."""
    filled = int(round(value * width))
    return "█" * filled + "░" * (width - filled)


def _bar_directional(score: float, width: int = 14) -> Text:
    """Rich Text bar with directional display for factor scores (-1..+1).

    Positive scores lean left (Home-favoured, shown in cyan);
    negative scores lean right (Away-favoured, shown in magenta).
    A midpoint marker divides the bar.
    """
    half = width // 2
    txt = Text()
    if score >= 0:
        filled = int(round(score * half))
        txt.append("░" * (half - filled), style="dim")
        txt.append("█" * filled, style="cyan")
        txt.append("│", style="bold white")
        txt.append("░" * half, style="dim")
        txt.append(f"  +{score:.2f}", style="bold cyan")
    else:
        filled = int(round(abs(score) * half))
        txt.append("░" * half, style="dim")
        txt.append("│", style="bold white")
        txt.append("█" * filled, style="magenta")
        txt.append("░" * (half - filled), style="dim")
        txt.append(f"  {score:.2f}", style="bold magenta")
    return txt



def _bar_colored(value: float, width: int = 12) -> Text:
    """Rich Text bar coloured green→yellow→red by magnitude (0–1 scale)."""
    filled = int(round(value * width))
    if value >= 0.7:
        color = "green"
    elif value >= 0.4:
        color = "yellow"
    else:
        color = "red"
    bar = Text()
    bar.append("█" * filled, style=color)
    bar.append("░" * (width - filled), style="dim")
    bar.append(f" {value:.0%}", style="bold " + color)
    return bar


def _confidence_style(confidence: str | float) -> str:
    """Return a Rich style string for a confidence level.

    Accepts either a string label (``"HIGH"``) or a float (in which case
    it classifies the float first using the standard thresholds).
    """
    if isinstance(confidence, (int, float)):
        if confidence >= 0.70:
            confidence = "HIGH"
        elif confidence >= 0.50:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"
    return CONFIDENCE_STYLES.get(str(confidence), CONFIDENCE_STYLES["LOW"])["color"]


def _confidence_emoji(confidence: str) -> str:
    """Return the emoji for a confidence level."""
    return CONFIDENCE_STYLES.get(confidence, CONFIDENCE_STYLES["LOW"])["emoji"]


def _parse_date(date_str: str) -> str:
    """Format an ISO date string for human display."""
    try:
        dt = datetime.fromisoformat(date_str)
        return dt.strftime("%a %d %b %H:%M")
    except (ValueError, TypeError):
        return str(date_str)[:16]


def _safe_attr(obj: Any, attr: str, default: Any = None) -> Any:
    """Safely get an attribute that may exist on a dataclass or dict."""
    if obj is None:
        return default

    # 1. Direct checks
    if isinstance(obj, dict):
        if attr in obj:
            return obj[attr]
    else:
        if hasattr(obj, attr):
            return getattr(obj, attr)

    # 2. Virtual mappings
    if attr == "match":
        home = _safe_attr(obj, "home_team")
        away = _safe_attr(obj, "away_team")
        date = _safe_attr(obj, "match_date")
        league = _safe_attr(obj, "league")
        if home is not None or away is not None or date is not None or league is not None:
            return {
                "home_team_name": home or "Home",
                "away_team_name": away or "Away",
                "date": date or "",
                "league_name": league or ""
            }
    elif attr == "recommendations":
        val = _safe_attr(obj, "market_recommendations")
        if val is not None:
            return val
    elif attr == "risk_factors":
        val = _safe_attr(obj, "risk_flags")
        if val is not None:
            return val
    elif attr == "market":
        val = _safe_attr(obj, "market_code")
        if val is not None:
            return val

    # 3. Fallback
    if isinstance(obj, dict):
        return obj.get(attr, default)
    return getattr(obj, attr, default)


def _safe_json(val: Any) -> Any:
    """Parse a value that might be a JSON string or already-parsed object."""
    if isinstance(val, str):
        try:
            return json.loads(val)
        except (json.JSONDecodeError, TypeError):
            return val
    return val


# ═══════════════════════════════════════════════════════════════
#  Telegram Message Splitting
# ═══════════════════════════════════════════════════════════════

def _split_telegram_messages(text: str, max_len: int = TELEGRAM_MAX_LENGTH) -> list[str]:
    """
    Split a long HTML string into chunks that each fit within
    Telegram's message size limit.  Splits on paragraph/newline
    boundaries when possible to avoid breaking tags.
    """
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
            # If a single line exceeds the limit, hard-split it
            while len(current) > max_len:
                messages.append(current[:max_len])
                current = current[max_len:]
        else:
            current = candidate

    if current.strip():
        messages.append(current.strip())

    return messages or [text[:max_len]]


# ═══════════════════════════════════════════════════════════════
#  ReportFormatter
# ═══════════════════════════════════════════════════════════════

class ReportFormatter:
    """
    Stateless formatter that transforms analysis data into
    display-ready Rich renderables (CLI) or HTML strings (Telegram).
    """

    # ─── CLI Formatters ─────────────────────────────────────

    def format_match_analysis_cli(
        self,
        analysis: MatchAnalysis,
        full_rationale: bool = True,
    ) -> Panel:
        """
        Build a Rich Panel with the full decision-support match card.

        Sections:
        1. Header — teams, date, league
        2. Statistical summary strip — form strings, PPG, CS rates, H2H
        3. Factor score bars — directional display, grouped by category
        4. Tiered market recommendations — HIGH / MEDIUM / LOW sections
        5. Risk warnings
        """
        match = _safe_attr(analysis, "match", {})
        home = _safe_attr(match, "home_team_name", "Home")
        away = _safe_attr(match, "away_team_name", "Away")
        date_str = _parse_date(_safe_attr(match, "date", ""))
        league = _safe_attr(match, "league_name", "")

        # ── Header ──────────────────────────────────────────
        header = Text()
        header.append("⚽ ", style="bold")
        header.append(f"{home}", style="bold cyan")
        header.append("  vs  ", style="dim")
        header.append(f"{away}", style="bold magenta")
        header.append(f"\n📅 {date_str}", style="dim")
        if league:
            header.append(f"  •  🏆 {league}", style="dim")

        # ── Statistical Summary Strip ────────────────────────
        factor_results: list = _safe_attr(analysis, "factor_results", [])

        def _fdata(name: str) -> dict:
            for fr in factor_results:
                if _safe_attr(fr, "name") == name:
                    return _safe_attr(fr, "data_points") or {}
            return {}

        rf_dp = _fdata("recent_form")
        hf_dp = _fdata("home_form")
        af_dp = _fdata("away_form")
        h2h_dp = _fdata("head_to_head")
        pos_dp = _fdata("league_position")
        def_dp = _fdata("defensive_record")

        summary = Text()
        home_rf = rf_dp.get("home", {})
        away_rf = rf_dp.get("away", {})
        home_form_str = home_rf.get("form_string") or pos_dp.get("home_form_string") or "—"
        away_form_str = away_rf.get("form_string") or pos_dp.get("away_form_string") or "—"
        home_ppg = home_rf.get("ppg") or pos_dp.get("home_goals_for_per_game")
        away_ppg = away_rf.get("ppg") or pos_dp.get("away_goals_for_per_game")
        home_cs = def_dp.get("home_cs_rate_home") or def_dp.get("home_cs_rate")
        away_cs = def_dp.get("away_cs_rate_away") or def_dp.get("away_cs_rate")
        h2h_hw = h2h_dp.get("home_wins", "?")
        h2h_d = h2h_dp.get("draws", "?")
        h2h_aw = h2h_dp.get("away_wins", "?")
        h2h_total = h2h_dp.get("total_meetings", "?")
        home_pos = pos_dp.get("home_position")
        away_pos = pos_dp.get("away_position")

        summary.append("📋 ", style="bold")
        summary.append(f"{home}: ", style="bold cyan")
        summary.append(
            f"{home_form_str}"
            + (f" {home_ppg:.2f} PPG" if home_ppg is not None else "")
            + (f" | CS {home_cs:.0%}" if home_cs is not None else "")
            + (f" | {home_pos}{'th' if home_pos else ''}" if home_pos else "")
        )
        summary.append("  vs  ", style="dim")
        summary.append(f"{away}: ", style="bold magenta")
        summary.append(
            f"{away_form_str}"
            + (f" {away_ppg:.2f} PPG" if away_ppg is not None else "")
            + (f" | CS {away_cs:.0%}" if away_cs is not None else "")
            + (f" | {away_pos}{'th' if away_pos else ''}" if away_pos else "")
        )
        if h2h_total not in ("?", None, 0):
            summary.append(
                f"\n🆚 H2H: {h2h_hw}W–{h2h_d}D–{h2h_aw}L ({h2h_total} meetings)",
                style="dim",
            )

        # ── Factor Scores ────────────────────────────────────
        factor_table = Table(
            title="📊 Factor Analysis",
            show_header=True,
            header_style="bold blue",
            expand=True,
            padding=(0, 1),
        )
        factor_table.add_column("Factor", style="bold", min_width=22)
        factor_table.add_column("Score  [dim cyan]← Home[/dim cyan]   [dim magenta]Away →[/dim magenta]", min_width=32)
        factor_table.add_column("Conf", justify="right", style="dim")
        factor_table.add_column("Wt", justify="right", style="dim")

        for fr in factor_results:
            name = _safe_attr(fr, "name", "?")
            score = _safe_attr(fr, "score", 0.0)
            if score is None:
                score = 0.0
            conf = _safe_attr(fr, "confidence", 0.0)
            if conf is None:
                conf = 0.0
            weight = FACTOR_WEIGHTS.get(name, 0.0)
            factor_table.add_row(
                name.replace("_", " ").title(),
                _bar_directional(float(score)),
                _bar_colored(float(conf), width=6),
                f"{weight:.0%}",
            )

        # ── Tiered Market Recommendations ────────────────────
        recommendations: list = _safe_attr(analysis, "recommendations", [])
        sections_content = []

        tier_defs = [
            ("HIGH",   "bold green",   "🟢 HIGH CONFIDENCE"),
            ("MEDIUM", "bold yellow",  "🟡 MEDIUM CONFIDENCE"),
            ("LOW",    "bold red",     "🔴 LOW CONFIDENCE"),
        ]

        for tier, tier_style, tier_label in tier_defs:
            tier_recs = [r for r in recommendations if _safe_attr(r, "confidence", "LOW") == tier]
            if not tier_recs:
                continue

            mkt_table = Table(
                show_header=True,
                header_style=tier_style,
                expand=True,
                padding=(0, 1),
                box=None,
            )
            mkt_table.add_column("#",      justify="right", style="dim", width=3)
            mkt_table.add_column("Market", style="bold",    min_width=16)
            mkt_table.add_column("Pick",   style="bold cyan", min_width=22)
            mkt_table.add_column("Prob",   justify="right", min_width=6)
            mkt_table.add_column("Odds",   justify="right", style="dim", min_width=6)
            mkt_table.add_column("Rationale", min_width=40)

            for pick_n, rec in enumerate(tier_recs, 1):
                market   = _safe_attr(rec, "market", "") or _safe_attr(rec, "market_code", "")
                pick     = _safe_attr(rec, "pick", "")
                prob     = float(_safe_attr(rec, "probability", 0.0) or 0.0)
                odds     = float(_safe_attr(rec, "implied_odds", 0.0) or 0.0)
                rationale = _safe_attr(rec, "rationale", "") or ""
                display_rat = rationale if full_rationale else (rationale[:90] + "…" if len(rationale) > 90 else rationale)
                mkt_table.add_row(
                    str(pick_n),
                    market,
                    pick,
                    f"{prob:.0%}",
                    f"{odds:.2f}" if odds else "—",
                    display_rat,
                )


            sections_content.append(
                Panel(
                    mkt_table,
                    title=f"[{tier_style}]{tier_label}[/{tier_style}] ({len(tier_recs)} picks)",
                    border_style=tier_style.split()[-1],  # last word = colour name
                    expand=True,
                    padding=(0, 1),
                )
            )

        # ── Risk Factors ────────────────────────────────────
        risks: list[str] = _safe_attr(analysis, "risk_factors", [])
        if isinstance(risks, str):
            risks = _safe_json(risks) or []
        risk_text = Text()
        if risks:
            risk_text.append("⚠️  Risk Factors\n", style="bold yellow")
            for risk in risks:
                risk_text.append(f"  • {risk}\n", style="yellow")
        else:
            risk_text.append("✅ No significant risk factors identified.\n", style="green")

        # ── Assemble panel ──────────────────────────────────
        overall_conf = _safe_attr(analysis, "overall_confidence", "LOW")
        border_color = _confidence_style(overall_conf)

        content_items = [header, "", summary, "", factor_table, ""]
        content_items.extend(sections_content)
        content_items.extend(["", risk_text])

        content = Group(*content_items)

        # Determine confidence label for subtitle
        if isinstance(overall_conf, float):
            if overall_conf >= 0.70:
                conf_label = "HIGH"
            elif overall_conf >= 0.50:
                conf_label = "MEDIUM"
            else:
                conf_label = "LOW"
        else:
            conf_label = str(overall_conf)

        return Panel(
            content,
            title="[bold]Match Analysis — Decision Support[/bold]",
            subtitle=f"[{border_color}]Overall confidence: {_confidence_emoji(conf_label)} {conf_label}[/{border_color}]",
            border_style=border_color,
            expand=True,
            padding=(1, 2),
        )

    def format_matchweek_summary_cli(self, analyses: list[MatchAnalysis]) -> Table:
        """
        Build a compact Rich Table summarising an entire matchweek.

        One row per match with the top pick, confidence, and probability.
        """
        table = Table(
            title="⚽ Matchweek Summary",
            show_header=True,
            header_style="bold white on blue",
            expand=True,
            row_styles=["", "dim"],
        )
        table.add_column("#", justify="right", style="dim", width=3)
        table.add_column("Date", style="dim", min_width=16)
        table.add_column("League", min_width=14)
        table.add_column("Match", min_width=30)
        table.add_column("Top Pick", style="bold cyan", min_width=18)
        table.add_column("Prob", justify="right", min_width=6)
        table.add_column("Conf", justify="center", min_width=8)

        for idx, analysis in enumerate(analyses, 1):
            match = _safe_attr(analysis, "match", {})
            home = _safe_attr(match, "home_team_name", "?")
            away = _safe_attr(match, "away_team_name", "?")
            date_str = _parse_date(_safe_attr(match, "date", ""))
            league = _safe_attr(match, "league_name", "")

            recs: list = _safe_attr(analysis, "recommendations", [])
            if recs:
                top = recs[0]
                pick = f"{_safe_attr(top, 'market', '')} → {_safe_attr(top, 'pick', '')}"
                prob = _safe_attr(top, "probability", 0.0) or 0.0
                conf = _safe_attr(top, "confidence", "LOW")
            else:
                pick, prob, conf = "—", 0.0, "LOW"

            conf_style = _confidence_style(conf)
            conf_display = Text(f"{_confidence_emoji(conf)} {conf}", style=conf_style)

            table.add_row(
                str(idx),
                date_str,
                league,
                f"{home} vs {away}",
                pick,
                f"{prob:.0%}",
                conf_display,
            )

        return table

    def format_picks_cli(
        self,
        analyses: list[MatchAnalysis],
        min_confidence: str | None = None,
    ) -> Table:
        """
        Build a Rich Table showing all picks, tiered by confidence.

        All three confidence tiers (HIGH / MEDIUM / LOW) are always shown.
        LOW-confidence picks are displayed last with a visual de-emphasis.
        Pass ``min_confidence='HIGH'`` or ``'MEDIUM'`` to filter if desired.

        Args:
            analyses: List of ``MatchAnalysis`` instances.
            min_confidence: Optional minimum tier to display.  Defaults to
                showing all tiers.
        """
        confidence_rank = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}
        min_rank = confidence_rank.get(min_confidence or "LOW", 1)

        table = Table(
            title="🎯 All Picks",
            show_header=True,
            header_style="bold white on dark_green",
            expand=True,
        )
        table.add_column("#",      justify="right", style="dim", width=3)
        table.add_column("Match",  min_width=28)
        table.add_column("League", style="dim", min_width=12)
        table.add_column("Market", min_width=14)
        table.add_column("Pick",   style="bold cyan")
        table.add_column("Prob",   justify="right")
        table.add_column("Odds",   justify="right", style="dim")
        table.add_column("Conf",   justify="center")

        # Group output by confidence tier so LOW picks appear last
        tiers_order = ["HIGH", "MEDIUM", "LOW"]
        pick_num = 0

        for tier in tiers_order:
            tier_rank = confidence_rank.get(tier, 1)
            if tier_rank < min_rank:
                continue

            tier_rows = []
            for analysis in analyses:
                match = _safe_attr(analysis, "match", {})
                home   = _safe_attr(match, "home_team_name", "?")
                away   = _safe_attr(match, "away_team_name", "?")
                league = _safe_attr(match, "league_name", "")
                recs: list = _safe_attr(analysis, "recommendations", [])
                for rec in recs:
                    conf = _safe_attr(rec, "confidence", "LOW")
                    if conf != tier:
                        continue
                    tier_rows.append((home, away, league, rec, conf))

            if not tier_rows:
                continue

            # Separator row for each tier
            conf_style = _confidence_style(tier)
            conf_emoji = _confidence_emoji(tier)
            table.add_row(
                "", f"[{conf_style}]{conf_emoji} {tier} CONFIDENCE[/{conf_style}]",
                "", "", "", "", "", "",
                style="bold",
            )

            for home, away, league, rec, conf in tier_rows:
                pick_num += 1
                prob     = float(_safe_attr(rec, "probability", 0.0) or 0.0)
                odds     = float(_safe_attr(rec, "implied_odds", 0.0) or 0.0)
                conf_style = _confidence_style(conf)
                row_style  = "dim" if conf == "LOW" else ""

                table.add_row(
                    str(pick_num),
                    f"{home} vs {away}",
                    league,
                    _safe_attr(rec, "market", "") or _safe_attr(rec, "market_code", ""),
                    _safe_attr(rec, "pick", ""),
                    f"{prob:.0%}",
                    f"{odds:.2f}" if odds else "—",
                    Text(f"{_confidence_emoji(conf)} {conf}", style=conf_style),
                    style=row_style,
                )

        if pick_num == 0:
            table.add_row(
                "—", "No picks found", "", "", "", "", "", ""
            )

        table.caption = f"[dim]{pick_num} pick{'s' if pick_num != 1 else ''} across all confidence tiers[/dim]"
        return table

    def format_accuracy_cli(self, stats: dict) -> Panel:
        """
        Build a Rich Panel showing an accuracy dashboard with overall
        stats, per-confidence breakdowns, and per-market breakdowns.
        """
        total = stats.get("total", 0)
        correct = stats.get("correct", 0) or 0
        incorrect = stats.get("incorrect", 0) or 0
        accuracy = stats.get("accuracy_pct", 0.0) or 0.0

        # ── Overall stats ───────────────────────────────────
        overview = Text()
        overview.append("📊 Overall Accuracy\n\n", style="bold")
        overview.append(f"  Total predictions:  ", style="dim")
        overview.append(f"{total}\n", style="bold")
        overview.append(f"  Correct:            ", style="dim")
        overview.append(f"{correct} ✅\n", style="bold green")
        overview.append(f"  Incorrect:          ", style="dim")
        overview.append(f"{incorrect} ❌\n", style="bold red")
        overview.append(f"  Accuracy:           ", style="dim")

        if accuracy >= 70:
            acc_style = "bold green"
        elif accuracy >= 50:
            acc_style = "bold yellow"
        else:
            acc_style = "bold red"
        overview.append(f"{accuracy:.1f}%\n", style=acc_style)
        overview.append(f"  ")
        overview.append(
            _bar_colored(accuracy / 100.0 if accuracy else 0.0, width=20)
        )

        # ── By Confidence ───────────────────────────────────
        conf_table = Table(
            title="By Confidence",
            show_header=True,
            header_style="bold blue",
            expand=True,
        )
        conf_table.add_column("Confidence", style="bold")
        conf_table.add_column("Total", justify="right")
        conf_table.add_column("Correct", justify="right", style="green")
        conf_table.add_column("Accuracy", justify="right")

        for row in stats.get("by_confidence", []):
            conf = row.get("confidence", "?")
            conf_acc = row.get("accuracy_pct", 0.0) or 0.0
            conf_table.add_row(
                Text(
                    f"{_confidence_emoji(conf)} {conf}",
                    style=_confidence_style(conf),
                ),
                str(row.get("total", 0)),
                str(row.get("correct", 0) or 0),
                f"{conf_acc:.1f}%",
            )

        # ── By Market ───────────────────────────────────────
        mkt_table = Table(
            title="By Market",
            show_header=True,
            header_style="bold blue",
            expand=True,
        )
        mkt_table.add_column("Market", style="bold")
        mkt_table.add_column("Total", justify="right")
        mkt_table.add_column("Correct", justify="right", style="green")
        mkt_table.add_column("Accuracy", justify="right")

        for row in stats.get("by_market", []):
            mkt_acc = row.get("accuracy_pct", 0.0) or 0.0
            mkt_table.add_row(
                row.get("market", "?"),
                str(row.get("total", 0)),
                str(row.get("correct", 0) or 0),
                f"{mkt_acc:.1f}%",
            )

        content = Group(overview, "", conf_table, "", mkt_table)

        border = "green" if accuracy >= 70 else ("yellow" if accuracy >= 50 else "red")
        return Panel(
            content,
            title="[bold]🎯 Accuracy Dashboard[/bold]",
            border_style=border,
            expand=True,
            padding=(1, 2),
        )

    def format_quota_status_cli(
        self,
        api_football_usage: dict,
        fdo_usage: dict,
    ) -> Panel:
        """
        Build a Rich Panel showing current API quota status for both
        data sources.
        """
        def _source_block(name: str, usage: dict, daily_limit: int) -> Text:
            made = usage.get("requests_made", 0)
            remaining = max(0, daily_limit - made)
            pct_used = made / daily_limit if daily_limit else 0

            block = Text()
            block.append(f"  {name}\n", style="bold")
            block.append(f"    Requests today:  ", style="dim")
            block.append(f"{made}", style="bold")
            block.append(f" / {daily_limit}\n", style="dim")
            block.append(f"    Remaining:       ", style="dim")

            if remaining > daily_limit * 0.3:
                rem_style = "bold green"
            elif remaining > daily_limit * 0.1:
                rem_style = "bold yellow"
            else:
                rem_style = "bold red"
            block.append(f"{remaining}\n", style=rem_style)
            block.append(f"    Usage:           ")
            block.append(
                _bar_colored(min(1.0, pct_used), width=16)
            )
            block.append("\n")
            return block

        from src.config import API_FOOTBALL, FOOTBALL_DATA_ORG

        af_block = _source_block(
            "API-Football", api_football_usage, API_FOOTBALL.daily_limit
        )
        fdo_block = _source_block(
            "Football-Data.org", fdo_usage, 10  # free tier
        )

        date_str = api_football_usage.get(
            "date", datetime.now().strftime("%Y-%m-%d")
        )

        content = Group(
            Text(f"📅 {date_str}\n", style="dim"),
            af_block,
            fdo_block,
        )

        return Panel(
            content,
            title="[bold]📡 API Quota Status[/bold]",
            border_style="blue",
            expand=True,
            padding=(1, 2),
        )

    # ─── Telegram Formatters ────────────────────────────────

    def format_match_analysis_telegram(self, analysis: MatchAnalysis) -> str:
        """
        Format a full match analysis as an HTML string suitable for
        Telegram's ``parse_mode='HTML'``.
        """
        match = _safe_attr(analysis, "match", {})
        home = _safe_attr(match, "home_team_name", "Home")
        away = _safe_attr(match, "away_team_name", "Away")
        date_str = _parse_date(_safe_attr(match, "date", ""))
        league = _safe_attr(match, "league_name", "")

        lines: list[str] = [
            f"⚽ <b>{home} vs {away}</b>",
            f"📅 {date_str}  •  🏆 {league}",
            "",
        ]

        # Factor scores
        factor_results: list = _safe_attr(analysis, "factor_results", [])
        if factor_results:
            lines.append("📊 <b>Factor Analysis</b>")
            for fr in factor_results:
                name = _safe_attr(fr, "name", "?").replace("_", " ").title()
                score = _safe_attr(fr, "score", 0.0) or 0.0
                bar = _bar(max(0.0, min(1.0, score)), width=8)
                lines.append(f"  {name}: {bar} {score:.0%}")
            lines.append("")

        # Recommendations
        recommendations: list = _safe_attr(analysis, "recommendations", [])
        if recommendations:
            lines.append("🎯 <b>Recommendations</b>")
            for rec in recommendations:
                market = _safe_attr(rec, "market", "")
                pick = _safe_attr(rec, "pick", "")
                prob = _safe_attr(rec, "probability", 0.0) or 0.0
                conf = _safe_attr(rec, "confidence", "LOW")
                emoji = _confidence_emoji(conf)
                lines.append(f"  {emoji} <b>{market}</b> → {pick} ({prob:.0%})")
            lines.append("")

        # Risks
        risks: list = _safe_attr(analysis, "risk_factors", [])
        if isinstance(risks, str):
            risks = _safe_json(risks) or []
        if risks:
            lines.append("⚠️ <b>Risk Factors</b>")
            for risk in risks:
                lines.append(f"  • {risk}")

        return "\n".join(lines)

    def format_matchweek_summary_telegram(
        self, analyses: list[MatchAnalysis]
    ) -> list[str]:
        """
        Format a matchweek summary as a list of HTML messages, each
        within the 4 096-character Telegram limit.
        """
        header = "⚽ <b>Matchweek Summary</b>\n\n"
        rows: list[str] = []

        for idx, analysis in enumerate(analyses, 1):
            match = _safe_attr(analysis, "match", {})
            home = _safe_attr(match, "home_team_name", "?")
            away = _safe_attr(match, "away_team_name", "?")
            league = _safe_attr(match, "league_name", "")

            recs: list = _safe_attr(analysis, "recommendations", [])
            if recs:
                top = recs[0]
                pick = f"{_safe_attr(top, 'market', '')} → {_safe_attr(top, 'pick', '')}"
                prob = _safe_attr(top, "probability", 0.0) or 0.0
                conf = _safe_attr(top, "confidence", "LOW")
                emoji = _confidence_emoji(conf)
            else:
                pick, prob, conf, emoji = "—", 0.0, "LOW", "🔴"

            rows.append(
                f"{idx}. <b>{home} vs {away}</b> ({league})\n"
                f"   {emoji} {pick} — {prob:.0%}"
            )

        full_text = header + "\n\n".join(rows)
        return _split_telegram_messages(full_text)

    def format_picks_telegram(
        self,
        analyses: list[MatchAnalysis],
        min_confidence: str = "HIGH",
    ) -> list[str]:
        """
        Format filtered picks for Telegram as a list of HTML messages.
        """
        confidence_rank = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}
        min_rank = confidence_rank.get(min_confidence, 1)

        header = f"🎯 <b>Picks (≥ {min_confidence})</b>\n\n"
        rows: list[str] = []
        pick_num = 0

        for analysis in analyses:
            match = _safe_attr(analysis, "match", {})
            home = _safe_attr(match, "home_team_name", "?")
            away = _safe_attr(match, "away_team_name", "?")

            recs: list = _safe_attr(analysis, "recommendations", [])
            for rec in recs:
                conf = _safe_attr(rec, "confidence", "LOW")
                if confidence_rank.get(conf, 0) < min_rank:
                    continue
                pick_num += 1
                market = _safe_attr(rec, "market", "")
                pick = _safe_attr(rec, "pick", "")
                prob = _safe_attr(rec, "probability", 0.0) or 0.0
                emoji = _confidence_emoji(conf)

                rows.append(
                    f"{pick_num}. {emoji} <b>{home} vs {away}</b>\n"
                    f"   {market} → <code>{pick}</code> ({prob:.0%})"
                )

        if not rows:
            rows.append(f"No picks meet the {min_confidence} threshold.")

        full_text = header + "\n\n".join(rows)
        full_text += f"\n\n<i>{pick_num} pick{'s' if pick_num != 1 else ''} total</i>"
        return _split_telegram_messages(full_text)

    def format_accuracy_telegram(self, stats: dict) -> str:
        """
        Format accuracy stats as a single Telegram HTML message.
        """
        total = stats.get("total", 0)
        correct = stats.get("correct", 0) or 0
        incorrect = stats.get("incorrect", 0) or 0
        accuracy = stats.get("accuracy_pct", 0.0) or 0.0

        lines: list[str] = [
            "📊 <b>Accuracy Dashboard</b>",
            "",
            f"Total: <b>{total}</b>",
            f"✅ Correct: <b>{correct}</b>",
            f"❌ Incorrect: <b>{incorrect}</b>",
            f"🎯 Accuracy: <b>{accuracy:.1f}%</b>",
            f"   {_bar(accuracy / 100.0 if accuracy else 0.0, width=16)}",
            "",
        ]

        by_conf = stats.get("by_confidence", [])
        if by_conf:
            lines.append("<b>By Confidence</b>")
            for row in by_conf:
                conf = row.get("confidence", "?")
                emoji = _confidence_emoji(conf)
                c_total = row.get("total", 0)
                c_acc = row.get("accuracy_pct", 0.0) or 0.0
                lines.append(f"  {emoji} {conf}: {c_acc:.1f}% ({c_total} picks)")
            lines.append("")

        by_mkt = stats.get("by_market", [])
        if by_mkt:
            lines.append("<b>By Market</b>")
            for row in by_mkt:
                mkt = row.get("market", "?")
                m_total = row.get("total", 0)
                m_acc = row.get("accuracy_pct", 0.0) or 0.0
                lines.append(f"  {mkt}: {m_acc:.1f}% ({m_total})")

        return "\n".join(lines)
