"""
Footy Predict — Individual Analysis Factors

Each factor function examines one dimension of a match and produces a
FactorResult.  Scores run from -1.0 (strongly favours away) to +1.0
(strongly favours home).  Confidence indicates how trustworthy the
assessment is given the available data.

All factor functions share the same signature:

    factor(db, home_id, away_id, league_id, season) -> FactorResult
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from src.database import Database


# ═══════════════════════════════════════════════════════════════
#  Core Data Structure
# ═══════════════════════════════════════════════════════════════

@dataclass
class FactorResult:
    """Result of evaluating a single analysis factor.

    Attributes:
        name: Factor identifier (e.g. ``"recent_form"``).
        score: -1.0 (away-dominant) … 0.0 (even) … +1.0 (home-dominant).
        confidence: 0.0 (no data) … 1.0 (highly reliable assessment).
        rationale: Human-readable paragraph explaining the conclusion.
        data_points: Raw numbers backing the conclusion.
    """

    name: str
    score: float
    confidence: float
    rationale: str
    data_points: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.score = max(-1.0, min(1.0, self.score))
        self.confidence = max(0.0, min(1.0, self.confidence))


# ═══════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════

def _empty_result(name: str, reason: str = "Insufficient data") -> FactorResult:
    """Return a neutral, zero-confidence result for a given factor."""
    return FactorResult(
        name=name,
        score=0.0,
        confidence=0.0,
        rationale=f"{reason} — factor cannot contribute to analysis.",
        data_points={},
    )


def _match_points(match: dict[str, Any], team_id: int) -> int:
    """Return points earned by *team_id* in a finished match (3/1/0)."""
    h, a = match.get("home_score"), match.get("away_score")
    if h is None or a is None:
        return 0
    is_home = match["home_team_id"] == team_id
    team_goals = h if is_home else a
    opp_goals = a if is_home else h
    if team_goals > opp_goals:
        return 3
    if team_goals == opp_goals:
        return 1
    return 0


def _match_result_char(match: dict[str, Any], team_id: int) -> str:
    """Return 'W', 'D', or 'L' for *team_id* in a finished match."""
    pts = _match_points(match, team_id)
    return {3: "W", 1: "D", 0: "L"}[pts]


def _goals_for(match: dict[str, Any], team_id: int) -> int:
    """Goals scored by *team_id*."""
    if match["home_team_id"] == team_id:
        return match.get("home_score") or 0
    return match.get("away_score") or 0


def _goals_against(match: dict[str, Any], team_id: int) -> int:
    """Goals conceded by *team_id*."""
    if match["home_team_id"] == team_id:
        return match.get("away_score") or 0
    return match.get("home_score") or 0


def _confidence_from_matches(n: int, ideal: int = 10) -> float:
    """Scale confidence based on sample size relative to *ideal*."""
    if n == 0:
        return 0.0
    return min(1.0, n / ideal)


def _safe_divide(a: float, b: float, default: float = 0.0) -> float:
    """Divide without raising on zero denominator."""
    return a / b if b else default


def _trend_score(results: list[str]) -> float:
    """Return a trend score from a list of W/D/L strings (most recent last).

    Uses weighted recency: most recent match counts more.  Returns -1…+1.
    """
    if not results:
        return 0.0
    weights = [i + 1 for i in range(len(results))]
    total_weight = sum(weights)
    value_map = {"W": 1.0, "D": 0.0, "L": -1.0}
    score = sum(value_map.get(r, 0.0) * w for r, w in zip(results, weights))
    return score / total_weight


# ═══════════════════════════════════════════════════════════════
#  Factor 1 — Recent Form
# ═══════════════════════════════════════════════════════════════

def recent_form(
    db: Database,
    home_id: int,
    away_id: int,
    league_id: int,
    season: str,
) -> FactorResult:
    """Analyse the last 5-10 matches for each team.

    Calculates points-per-game, win rate, and trend (improving / declining),
    then compares the two teams.
    """
    home_matches = db.get_recent_matches(home_id, limit=10)
    away_matches = db.get_recent_matches(away_id, limit=10)

    if not home_matches and not away_matches:
        return _empty_result("recent_form")

    def _form_stats(matches: list[dict], team_id: int) -> dict[str, Any]:
        results = [_match_result_char(m, team_id) for m in reversed(matches)]
        pts = [_match_points(m, team_id) for m in matches]
        ppg = _safe_divide(sum(pts), len(pts))
        win_rate = _safe_divide(results.count("W"), len(results))
        draw_rate = _safe_divide(results.count("D"), len(results))
        loss_rate = _safe_divide(results.count("L"), len(results))
        trend = _trend_score(results)

        # Split into halves for momentum comparison
        mid = len(results) // 2 if len(results) >= 4 else 0
        recent_half = results[mid:] if mid else results
        older_half = results[:mid] if mid else []
        recent_ppg = _safe_divide(
            sum({"W": 3, "D": 1, "L": 0}[r] for r in recent_half),
            len(recent_half),
        )
        older_ppg = _safe_divide(
            sum({"W": 3, "D": 1, "L": 0}[r] for r in older_half),
            len(older_half),
        ) if older_half else recent_ppg

        return {
            "form_string": "".join(results[-5:]),
            "ppg": round(ppg, 2),
            "win_rate": round(win_rate, 3),
            "draw_rate": round(draw_rate, 3),
            "loss_rate": round(loss_rate, 3),
            "trend": round(trend, 3),
            "recent_ppg": round(recent_ppg, 2),
            "older_ppg": round(older_ppg, 2),
            "sample": len(matches),
        }

    h_stats = _form_stats(home_matches, home_id) if home_matches else None
    a_stats = _form_stats(away_matches, away_id) if away_matches else None

    # Build score: positive = home stronger
    if h_stats and a_stats:
        ppg_diff = h_stats["ppg"] - a_stats["ppg"]
        trend_diff = h_stats["trend"] - a_stats["trend"]
        # Normalise ppg_diff (max 3) and blend with trend
        score = 0.6 * (ppg_diff / 3.0) + 0.4 * ((trend_diff) / 2.0)
        score = max(-1.0, min(1.0, score))
        n_total = h_stats["sample"] + a_stats["sample"]
        conf = _confidence_from_matches(n_total, ideal=16)
    elif h_stats:
        score = 0.3 * (h_stats["ppg"] - 1.5) / 1.5
        conf = _confidence_from_matches(h_stats["sample"], ideal=10) * 0.5
    elif a_stats:
        score = -0.3 * (a_stats["ppg"] - 1.5) / 1.5
        conf = _confidence_from_matches(a_stats["sample"], ideal=10) * 0.5
    else:
        return _empty_result("recent_form")

    # Rationale
    parts: list[str] = []
    if h_stats:
        direction = "improving" if h_stats["recent_ppg"] > h_stats["older_ppg"] else (
            "declining" if h_stats["recent_ppg"] < h_stats["older_ppg"] else "steady"
        )
        parts.append(
            f"Home team form: {h_stats['form_string']} "
            f"({h_stats['ppg']} PPG, {h_stats['win_rate']:.0%} win rate, {direction})"
        )
    if a_stats:
        direction = "improving" if a_stats["recent_ppg"] > a_stats["older_ppg"] else (
            "declining" if a_stats["recent_ppg"] < a_stats["older_ppg"] else "steady"
        )
        parts.append(
            f"Away team form: {a_stats['form_string']} "
            f"({a_stats['ppg']} PPG, {a_stats['win_rate']:.0%} win rate, {direction})"
        )

    rationale = ". ".join(parts) + "."
    data_points = {"home": h_stats or {}, "away": a_stats or {}}

    return FactorResult(
        name="recent_form",
        score=score,
        confidence=conf,
        rationale=rationale,
        data_points=data_points,
    )


# ═══════════════════════════════════════════════════════════════
#  Factor 2 — Home Form
# ═══════════════════════════════════════════════════════════════

def home_form(
    db: Database,
    home_id: int,
    away_id: int,
    league_id: int,
    season: str,
) -> FactorResult:
    """Evaluate the home team's record when playing at home.

    A stronger home record signals higher home advantage.
    """
    matches = db.get_recent_matches(home_id, limit=10, home_only=True)
    if not matches:
        # Fall back to season stats
        stats = db.get_team_season_stats(home_id, league_id, season)
        if stats and stats.get("home_played", 0) > 0:
            hp = stats["home_played"]
            hw = stats.get("home_wins", 0)
            hd = stats.get("home_draws", 0)
            hl = stats.get("home_losses", 0)
            hgs = stats.get("home_goals_scored", 0)
            hgc = stats.get("home_goals_conceded", 0)
            win_rate = _safe_divide(hw, hp)
            gpg = _safe_divide(hgs, hp)
            cpg = _safe_divide(hgc, hp)
            score = (win_rate - 0.5) * 2  # 0.5 = neutral
            conf = _confidence_from_matches(hp, ideal=8) * 0.7
            rationale = (
                f"Home record from season stats: {hw}W {hd}D {hl}L in {hp} home matches "
                f"({win_rate:.0%} win rate). Scoring {gpg:.1f} GPG, conceding {cpg:.1f} GPG at home."
            )
            return FactorResult(
                name="home_form", score=score, confidence=conf,
                rationale=rationale,
                data_points={
                    "home_played": hp, "home_wins": hw, "home_draws": hd,
                    "home_losses": hl, "home_goals_scored": hgs,
                    "home_goals_conceded": hgc, "win_rate": round(win_rate, 3),
                    "gpg": round(gpg, 2), "cpg": round(cpg, 2),
                    "source": "season_stats",
                },
            )
        return _empty_result("home_form")

    wins = sum(1 for m in matches if _match_result_char(m, home_id) == "W")
    draws = sum(1 for m in matches if _match_result_char(m, home_id) == "D")
    losses = len(matches) - wins - draws
    goals_scored = sum(_goals_for(m, home_id) for m in matches)
    goals_conceded = sum(_goals_against(m, home_id) for m in matches)
    clean_sheets = sum(1 for m in matches if _goals_against(m, home_id) == 0)
    n = len(matches)

    win_rate = _safe_divide(wins, n)
    gpg = _safe_divide(goals_scored, n)
    cpg = _safe_divide(goals_conceded, n)
    cs_rate = _safe_divide(clean_sheets, n)

    # Scoring participation
    scored_in_count = sum(1 for m in matches if _goals_for(m, home_id) > 0)
    scored_in_pct = _safe_divide(scored_in_count, n)
    failed_to_score_count = n - scored_in_count

    # Current streak (most recent first from DB)
    result_chars = [_match_result_char(m, home_id) for m in matches]
    streak_type = result_chars[0] if result_chars else None
    streak_len = 0
    for r in result_chars:
        if r == streak_type:
            streak_len += 1
        else:
            break
    current_home_streak = {"type": streak_type, "length": streak_len} if streak_type else {}

    # Score: strong home record → positive
    score = (win_rate - 0.45) * 2.2  # Slight home bias expected (~45% baseline)
    score = max(-1.0, min(1.0, score))
    conf = _confidence_from_matches(n, ideal=8)

    rationale = (
        f"Home record (last {n} home matches): {wins}W {draws}D {losses}L "
        f"({win_rate:.0%} win rate). Scoring {gpg:.1f} GPG, conceding {cpg:.1f} GPG. "
        f"Clean sheet rate: {cs_rate:.0%}."
    )

    return FactorResult(
        name="home_form", score=score, confidence=conf,
        rationale=rationale,
        data_points={
            "home_played": n, "home_wins": wins, "home_draws": draws,
            "home_losses": losses, "goals_scored": goals_scored,
            "goals_conceded": goals_conceded, "clean_sheets": clean_sheets,
            "win_rate": round(win_rate, 3), "gpg": round(gpg, 2),
            "cpg": round(cpg, 2), "cs_rate": round(cs_rate, 3),
            "scored_in_pct": round(scored_in_pct, 3),
            "failed_to_score": failed_to_score_count,
            "current_home_streak": current_home_streak,
        },
    )


# ═══════════════════════════════════════════════════════════════
#  Factor 3 — Away Form
# ═══════════════════════════════════════════════════════════════

def away_form(
    db: Database,
    home_id: int,
    away_id: int,
    league_id: int,
    season: str,
) -> FactorResult:
    """Evaluate the away team's record when playing away from home."""
    matches = db.get_recent_matches(away_id, limit=10, away_only=True)
    if not matches:
        stats = db.get_team_season_stats(away_id, league_id, season)
        if stats and stats.get("away_played", 0) > 0:
            ap = stats["away_played"]
            aw = stats.get("away_wins", 0)
            ad = stats.get("away_draws", 0)
            al = stats.get("away_losses", 0)
            ags = stats.get("away_goals_scored", 0)
            agc = stats.get("away_goals_conceded", 0)
            win_rate = _safe_divide(aw, ap)
            gpg = _safe_divide(ags, ap)
            cpg = _safe_divide(agc, ap)
            # Positive score = away team is strong away → favours away → negative
            score = -((win_rate - 0.35) * 2)
            conf = _confidence_from_matches(ap, ideal=8) * 0.7
            rationale = (
                f"Away record from season stats: {aw}W {ad}D {al}L in {ap} away matches "
                f"({win_rate:.0%} win rate). Scoring {gpg:.1f} GPG, conceding {cpg:.1f} GPG away."
            )
            return FactorResult(
                name="away_form", score=score, confidence=conf,
                rationale=rationale,
                data_points={
                    "away_played": ap, "away_wins": aw, "away_draws": ad,
                    "away_losses": al, "away_goals_scored": ags,
                    "away_goals_conceded": agc, "win_rate": round(win_rate, 3),
                    "gpg": round(gpg, 2), "cpg": round(cpg, 2),
                    "source": "season_stats",
                },
            )
        return _empty_result("away_form")

    wins = sum(1 for m in matches if _match_result_char(m, away_id) == "W")
    draws = sum(1 for m in matches if _match_result_char(m, away_id) == "D")
    losses = len(matches) - wins - draws
    goals_scored = sum(_goals_for(m, away_id) for m in matches)
    goals_conceded = sum(_goals_against(m, away_id) for m in matches)
    clean_sheets = sum(1 for m in matches if _goals_against(m, away_id) == 0)
    n = len(matches)

    win_rate = _safe_divide(wins, n)
    gpg = _safe_divide(goals_scored, n)
    cpg = _safe_divide(goals_conceded, n)
    cs_rate = _safe_divide(clean_sheets, n)

    # Scoring participation
    scored_in_count = sum(1 for m in matches if _goals_for(m, away_id) > 0)
    scored_in_pct = _safe_divide(scored_in_count, n)
    failed_to_score_count = n - scored_in_count

    # Current streak (most recent first from DB)
    result_chars = [_match_result_char(m, away_id) for m in matches]
    streak_type = result_chars[0] if result_chars else None
    streak_len = 0
    for r in result_chars:
        if r == streak_type:
            streak_len += 1
        else:
            break
    current_away_streak = {"type": streak_type, "length": streak_len} if streak_type else {}

    # Strong away team → negative score (favours away)
    score = -((win_rate - 0.35) * 2.0)  # 35% away win rate is neutral baseline
    score = max(-1.0, min(1.0, score))
    conf = _confidence_from_matches(n, ideal=8)

    rationale = (
        f"Away record (last {n} away matches): {wins}W {draws}D {losses}L "
        f"({win_rate:.0%} win rate). Scoring {gpg:.1f} GPG, conceding {cpg:.1f} GPG. "
        f"Clean sheet rate: {cs_rate:.0%}."
    )

    return FactorResult(
        name="away_form", score=score, confidence=conf,
        rationale=rationale,
        data_points={
            "away_played": n, "away_wins": wins, "away_draws": draws,
            "away_losses": losses, "goals_scored": goals_scored,
            "goals_conceded": goals_conceded, "clean_sheets": clean_sheets,
            "win_rate": round(win_rate, 3), "gpg": round(gpg, 2),
            "cpg": round(cpg, 2), "cs_rate": round(cs_rate, 3),
            "scored_in_pct": round(scored_in_pct, 3),
            "failed_to_score": failed_to_score_count,
            "current_away_streak": current_away_streak,
        },
    )


# ═══════════════════════════════════════════════════════════════
#  Factor 4 — Scoring Frequency
# ═══════════════════════════════════════════════════════════════

def scoring_frequency(
    db: Database,
    home_id: int,
    away_id: int,
    league_id: int,
    season: str,
) -> FactorResult:
    """Compare goals-per-game and scoring consistency for both teams.

    Also computes venue-split GPG (home team at home, away team when away)
    and scored_in_pct for Win to Nil and GG/NG market models.
    """
    home_matches = db.get_recent_matches(home_id, limit=10)
    away_matches = db.get_recent_matches(away_id, limit=10)

    # Venue-specific match sets
    home_home_matches = db.get_recent_matches(home_id, limit=10, home_only=True)
    away_away_matches = db.get_recent_matches(away_id, limit=10, away_only=True)

    if not home_matches and not away_matches:
        return _empty_result("scoring_frequency")

    def _scoring_stats(matches: list[dict], team_id: int) -> dict[str, Any]:
        goals = [_goals_for(m, team_id) for m in matches]
        n = len(goals)
        avg = _safe_divide(sum(goals), n)
        scored_pct = _safe_divide(sum(1 for g in goals if g > 0), n)
        # Consistency: lower std dev relative to mean → more consistent
        variance = sum((g - avg) ** 2 for g in goals) / n if n else 0
        std_dev = math.sqrt(variance)
        consistency = max(0.0, 1.0 - _safe_divide(std_dev, max(avg, 1.0)))
        return {
            "avg_goals": round(avg, 2),
            "scored_pct": round(scored_pct, 3),
            "total_goals": sum(goals),
            "consistency": round(consistency, 3),
            "max_goals": max(goals) if goals else 0,
            "blank_games": sum(1 for g in goals if g == 0),
            "sample": n,
        }

    h_stats = _scoring_stats(home_matches, home_id) if home_matches else None
    a_stats = _scoring_stats(away_matches, away_id) if away_matches else None

    # Venue-split scoring rates
    def _venue_gpg(matches: list[dict], team_id: int) -> float | None:
        if not matches:
            return None
        goals = [_goals_for(m, team_id) for m in matches]
        return _safe_divide(sum(goals), len(goals))

    home_home_gpg = _venue_gpg(home_home_matches, home_id)
    away_away_gpg = _venue_gpg(away_away_matches, away_id)

    def _scored_in_pct(matches: list[dict], team_id: int) -> float | None:
        if not matches:
            return None
        n = len(matches)
        return _safe_divide(sum(1 for m in matches if _goals_for(m, team_id) > 0), n)

    home_scored_in_pct = _scored_in_pct(home_home_matches, home_id)
    away_scored_in_pct = _scored_in_pct(away_away_matches, away_id)

    home_blank_games = sum(1 for m in home_home_matches if _goals_for(m, home_id) == 0) if home_home_matches else None
    away_blank_games = sum(1 for m in away_away_matches if _goals_for(m, away_id) == 0) if away_away_matches else None

    if h_stats and a_stats:
        # Higher scoring home team → positive score
        gpg_diff = h_stats["avg_goals"] - a_stats["avg_goals"]
        score = gpg_diff / 3.0  # Normalise (max realistic diff ~3)
        score = max(-1.0, min(1.0, score))
        n_total = h_stats["sample"] + a_stats["sample"]
        conf = _confidence_from_matches(n_total, ideal=16)
    elif h_stats:
        score = (h_stats["avg_goals"] - 1.3) / 2.0
        conf = _confidence_from_matches(h_stats["sample"], ideal=10) * 0.5
    elif a_stats:
        score = -(a_stats["avg_goals"] - 1.3) / 2.0
        conf = _confidence_from_matches(a_stats["sample"], ideal=10) * 0.5
    else:
        return _empty_result("scoring_frequency")

    parts: list[str] = []
    if h_stats:
        parts.append(
            f"Home team averages {h_stats['avg_goals']} GPG "
            f"(scored in {h_stats['scored_pct']:.0%} of games, "
            f"consistency {h_stats['consistency']:.0%})"
        )
    if a_stats:
        parts.append(
            f"Away team averages {a_stats['avg_goals']} GPG "
            f"(scored in {a_stats['scored_pct']:.0%} of games, "
            f"consistency {a_stats['consistency']:.0%})"
        )

    return FactorResult(
        name="scoring_frequency",
        score=score,
        confidence=conf,
        rationale=". ".join(parts) + ".",
        data_points={
            "home": h_stats or {},
            "away": a_stats or {},
            "home_gpg": round(h_stats["avg_goals"], 2) if h_stats else None,
            "away_gpg": round(a_stats["avg_goals"], 2) if a_stats else None,
            "home_home_gpg": round(home_home_gpg, 2) if home_home_gpg is not None else None,
            "away_away_gpg": round(away_away_gpg, 2) if away_away_gpg is not None else None,
            "home_scored_in_pct": round(home_scored_in_pct, 3) if home_scored_in_pct is not None else None,
            "away_scored_in_pct": round(away_scored_in_pct, 3) if away_scored_in_pct is not None else None,
            "home_blank_games": home_blank_games,
            "away_blank_games": away_blank_games,
        },
    )


# ═══════════════════════════════════════════════════════════════
#  Factor 5 — Defensive Record
# ═══════════════════════════════════════════════════════════════

def defensive_record(
    db: Database,
    home_id: int,
    away_id: int,
    league_id: int,
    season: str,
) -> FactorResult:
    """Compare goals conceded per game and clean-sheet rates.

    Also computes venue-split CS rates (home team at home, away team when away)
    which are used by GG/NG and Win to Nil market models.
    """
    home_matches = db.get_recent_matches(home_id, limit=10)
    away_matches = db.get_recent_matches(away_id, limit=10)

    # Venue-specific match sets
    home_home_matches = db.get_recent_matches(home_id, limit=10, home_only=True)
    away_away_matches = db.get_recent_matches(away_id, limit=10, away_only=True)

    if not home_matches and not away_matches:
        return _empty_result("defensive_record")

    def _def_stats(matches: list[dict], team_id: int) -> dict[str, Any]:
        conceded = [_goals_against(m, team_id) for m in matches]
        n = len(conceded)
        avg = _safe_divide(sum(conceded), n)
        cs = sum(1 for c in conceded if c == 0)
        cs_rate = _safe_divide(cs, n)
        # Matches conceding 2+
        leaky_games = sum(1 for c in conceded if c >= 2)
        return {
            "avg_conceded": round(avg, 2),
            "clean_sheets": cs,
            "clean_sheet_rate": round(cs_rate, 3),
            "total_conceded": sum(conceded),
            "leaky_games": leaky_games,
            "leaky_rate": round(_safe_divide(leaky_games, n), 3),
            "sample": n,
        }

    h_stats = _def_stats(home_matches, home_id) if home_matches else None
    a_stats = _def_stats(away_matches, away_id) if away_matches else None

    # Venue-split CS rates and CPG
    def _venue_cs_rate(matches: list[dict], team_id: int) -> float | None:
        if not matches:
            return None
        conceded = [_goals_against(m, team_id) for m in matches]
        return _safe_divide(sum(1 for c in conceded if c == 0), len(conceded))

    def _venue_cpg(matches: list[dict], team_id: int) -> float | None:
        if not matches:
            return None
        conceded = [_goals_against(m, team_id) for m in matches]
        return _safe_divide(sum(conceded), len(conceded))

    home_cs_rate_home = _venue_cs_rate(home_home_matches, home_id)
    away_cs_rate_away = _venue_cs_rate(away_away_matches, away_id)
    home_cpg_home = _venue_cpg(home_home_matches, home_id)
    away_cpg_away = _venue_cpg(away_away_matches, away_id)

    if h_stats and a_stats:
        # Better home defence → positive (lower conceded = better)
        diff = a_stats["avg_conceded"] - h_stats["avg_conceded"]
        score = diff / 2.5  # Normalise
        score = max(-1.0, min(1.0, score))
        n_total = h_stats["sample"] + a_stats["sample"]
        conf = _confidence_from_matches(n_total, ideal=16)
    elif h_stats:
        score = (1.2 - h_stats["avg_conceded"]) / 2.0
        conf = _confidence_from_matches(h_stats["sample"], ideal=10) * 0.5
    elif a_stats:
        score = -(1.2 - a_stats["avg_conceded"]) / 2.0
        conf = _confidence_from_matches(a_stats["sample"], ideal=10) * 0.5
    else:
        return _empty_result("defensive_record")

    parts: list[str] = []
    if h_stats:
        parts.append(
            f"Home defence concedes {h_stats['avg_conceded']} GPG, "
            f"clean-sheet rate {h_stats['clean_sheet_rate']:.0%} "
            f"({h_stats['leaky_games']}/{h_stats['sample']} games conceding 2+)"
        )
    if a_stats:
        parts.append(
            f"Away defence concedes {a_stats['avg_conceded']} GPG, "
            f"clean-sheet rate {a_stats['clean_sheet_rate']:.0%} "
            f"({a_stats['leaky_games']}/{a_stats['sample']} games conceding 2+)"
        )

    return FactorResult(
        name="defensive_record",
        score=score,
        confidence=conf,
        rationale=". ".join(parts) + ".",
        data_points={
            "home": h_stats or {},
            "away": a_stats or {},
            "home_cs_rate": round(h_stats["clean_sheet_rate"], 3) if h_stats else None,
            "away_cs_rate": round(a_stats["clean_sheet_rate"], 3) if a_stats else None,
            "home_cpg": round(h_stats["avg_conceded"], 2) if h_stats else None,
            "away_cpg": round(a_stats["avg_conceded"], 2) if a_stats else None,
            "home_cs_rate_home": round(home_cs_rate_home, 3) if home_cs_rate_home is not None else None,
            "away_cs_rate_away": round(away_cs_rate_away, 3) if away_cs_rate_away is not None else None,
            "home_cpg_home": round(home_cpg_home, 2) if home_cpg_home is not None else None,
            "away_cpg_away": round(away_cpg_away, 2) if away_cpg_away is not None else None,
        },
    )


# ═══════════════════════════════════════════════════════════════
#  Factor 6 — Head to Head
# ═══════════════════════════════════════════════════════════════

def head_to_head(
    db: Database,
    home_id: int,
    away_id: int,
    league_id: int,
    season: str,
) -> FactorResult:
    """Evaluate historical head-to-head record between the two teams."""
    h2h = db.get_h2h(home_id, away_id)

    if not h2h or h2h.get("total_matches", 0) == 0:
        # Try computing from match history directly
        return _compute_h2h_from_matches(db, home_id, away_id)

    total = h2h["total_matches"]
    # Determine which team is team_a / team_b in the normalised record
    a_id, b_id = min(home_id, away_id), max(home_id, away_id)
    if home_id == a_id:
        home_wins = h2h["team_a_wins"]
        away_wins = h2h["team_b_wins"]
        home_goals = h2h["team_a_goals"]
        away_goals = h2h["team_b_goals"]
    else:
        home_wins = h2h["team_b_wins"]
        away_wins = h2h["team_a_wins"]
        home_goals = h2h["team_b_goals"]
        away_goals = h2h["team_a_goals"]
    draws = h2h["draws"]

    home_wr = _safe_divide(home_wins, total)
    away_wr = _safe_divide(away_wins, total)

    # Score: home dominance in H2H → positive
    score = (home_wr - away_wr) * 1.5
    score = max(-1.0, min(1.0, score))

    # H2H confidence scales with number of meetings but caps out
    conf = _confidence_from_matches(total, ideal=10)
    # Reduce confidence for very old data (no recency info available)
    conf *= 0.8

    rationale = (
        f"H2H record ({total} meetings): Home {home_wins}W, "
        f"Draws {draws}, Away {away_wins}W. "
        f"Home win rate {home_wr:.0%}, Away win rate {away_wr:.0%}. "
        f"Goals: Home {home_goals} – Away {away_goals}."
    )

    # Analyse recent H2H matches if available
    last_matches = h2h.get("last_matches", [])
    if isinstance(last_matches, str):
        try:
            last_matches = json.loads(last_matches)
        except (json.JSONDecodeError, TypeError):
            last_matches = []

    last_5_scorelines: list[str] = []
    btts_count = 0
    recent_h2h_trend = ""
    avg_goals_h2h: float | None = None

    if last_matches:
        recent_count = min(5, len(last_matches))
        rationale += f" Last {recent_count} meetings available for context."

        # Extract scorelines and BTTS from last_matches
        for m in last_matches[:5]:
            hs = m.get("home_score", "?")
            as_ = m.get("away_score", "?")
            last_5_scorelines.append(f"{hs}-{as_}")
            if isinstance(hs, int) and isinstance(as_, int) and hs > 0 and as_ > 0:
                btts_count += 1

        # Average goals per H2H meeting
        total_h2h_goals = home_goals + away_goals
        avg_goals_h2h = round(_safe_divide(total_h2h_goals, total), 2) if total > 0 else None

        # Recent H2H trend for home team in last 5 meetings
        trend_chars = []
        for m in last_matches[:5]:
            hs = m.get("home_score")
            as_ = m.get("away_score")
            h_team = m.get("home_team_id")
            if hs is None or as_ is None:
                continue
            if h_team == home_id:
                if hs > as_:
                    trend_chars.append("W")
                elif hs == as_:
                    trend_chars.append("D")
                else:
                    trend_chars.append("L")
            else:
                if as_ > hs:
                    trend_chars.append("W")
                elif hs == as_:
                    trend_chars.append("D")
                else:
                    trend_chars.append("L")
        recent_h2h_trend = "".join(trend_chars)

    btts_rate_h2h = round(_safe_divide(btts_count, min(5, len(last_matches) if last_matches else 0)), 3) if last_matches else None

    return FactorResult(
        name="head_to_head",
        score=score,
        confidence=conf,
        rationale=rationale,
        data_points={
            "total_meetings": total,
            "home_wins": home_wins,
            "draws": draws,
            "away_wins": away_wins,
            "home_goals": home_goals,
            "away_goals": away_goals,
            "home_win_rate": round(home_wr, 3),
            "away_win_rate": round(away_wr, 3),
            "last_5_scorelines": last_5_scorelines,
            "avg_goals_h2h": avg_goals_h2h,
            "btts_rate_h2h": btts_rate_h2h,
            "recent_h2h_trend": recent_h2h_trend,
        },
    )


def _compute_h2h_from_matches(
    db: Database, home_id: int, away_id: int
) -> FactorResult:
    """Fall back to computing H2H from the matches table."""
    # Get matches where both teams played each other
    home_as_home = db.get_recent_matches(home_id, limit=50, home_only=True)
    h2h_matches = [
        m for m in home_as_home
        if m["away_team_id"] == away_id
    ]
    away_as_home = db.get_recent_matches(away_id, limit=50, home_only=True)
    h2h_reverse = [
        m for m in away_as_home
        if m["away_team_id"] == home_id
    ]
    all_h2h = h2h_matches + h2h_reverse

    if not all_h2h:
        return _empty_result("head_to_head", "No head-to-head history found")

    home_wins = 0
    away_wins = 0
    draws = 0
    home_goals = 0
    away_goals = 0

    for m in all_h2h:
        hg = _goals_for(m, home_id)
        ag = _goals_for(m, away_id)
        home_goals += hg
        away_goals += ag
        if hg > ag:
            home_wins += 1
        elif ag > hg:
            away_wins += 1
        else:
            draws += 1

    total = len(all_h2h)
    home_wr = _safe_divide(home_wins, total)
    away_wr = _safe_divide(away_wins, total)

    score = (home_wr - away_wr) * 1.5
    score = max(-1.0, min(1.0, score))
    conf = _confidence_from_matches(total, ideal=8) * 0.7

    rationale = (
        f"H2H from match history ({total} meetings): "
        f"Home {home_wins}W, Draws {draws}, Away {away_wins}W."
    )

    return FactorResult(
        name="head_to_head",
        score=score,
        confidence=conf,
        rationale=rationale,
        data_points={
            "total_meetings": total,
            "home_wins": home_wins,
            "draws": draws,
            "away_wins": away_wins,
            "home_goals": home_goals,
            "away_goals": away_goals,
            "source": "match_history",
        },
    )


# ═══════════════════════════════════════════════════════════════
#  Factor 7 — League Position
# ═══════════════════════════════════════════════════════════════

def league_position(
    db: Database,
    home_id: int,
    away_id: int,
    league_id: int,
    season: str,
) -> FactorResult:
    """Compare league-table positions, points gaps, and relative strength."""
    standings = db.get_standings(league_id, season)

    if not standings:
        return _empty_result("league_position", "No standings data available")

    home_standing = next((s for s in standings if s["team_id"] == home_id), None)
    away_standing = next((s for s in standings if s["team_id"] == away_id), None)

    if not home_standing and not away_standing:
        return _empty_result("league_position", "Neither team found in standings")

    total_teams = len(standings)
    max_pos = total_teams

    if home_standing and away_standing:
        h_pos = home_standing["position"]
        a_pos = away_standing["position"]
        h_pts = home_standing.get("points", 0)
        a_pts = away_standing.get("points", 0)
        h_gd = home_standing.get("goal_difference", 0)
        a_gd = away_standing.get("goal_difference", 0)

        # Position difference normalised by table size
        pos_diff = (a_pos - h_pos) / max(max_pos - 1, 1)
        pts_diff = (h_pts - a_pts)
        # Normalise points diff (assume max realistic diff ~40)
        pts_factor = pts_diff / max(h_pts + a_pts, 1) if (h_pts + a_pts) > 0 else 0

        score = 0.5 * pos_diff + 0.5 * pts_factor
        score = max(-1.0, min(1.0, score))
        conf = 0.85  # Standings are objective data

        rationale = (
            f"League positions: Home {h_pos}{_ordinal(h_pos)} ({h_pts} pts, GD {h_gd:+d}), "
            f"Away {a_pos}{_ordinal(a_pos)} ({a_pts} pts, GD {a_gd:+d}). "
            f"Position gap: {abs(h_pos - a_pos)} places, points gap: {abs(h_pts - a_pts)}."
        )

        # Additional standings stats (goals, win%, form string)
        h_played = max(home_standing.get("played", 0) or 1, 1)
        a_played = max(away_standing.get("played", 0) or 1, 1)
        h_won = home_standing.get("won", 0) or 0
        h_drawn = home_standing.get("drawn", 0) or 0
        h_goals_for = home_standing.get("goals_for", 0) or 0
        h_goals_against = home_standing.get("goals_against", 0) or 0
        h_form_str = home_standing.get("form", "") or ""
        a_won = away_standing.get("won", 0) or 0
        a_drawn = away_standing.get("drawn", 0) or 0
        a_goals_for = away_standing.get("goals_for", 0) or 0
        a_goals_against = away_standing.get("goals_against", 0) or 0
        a_form_str = away_standing.get("form", "") or ""

        data_points = {
            "home_position": h_pos,
            "away_position": a_pos,
            "home_points": h_pts,
            "away_points": a_pts,
            "home_gd": h_gd,
            "away_gd": a_gd,
            "position_gap": abs(h_pos - a_pos),
            "points_gap": abs(h_pts - a_pts),
            "total_teams": total_teams,
            "home_goals_for_per_game": round(_safe_divide(h_goals_for, h_played), 2),
            "home_goals_against_per_game": round(_safe_divide(h_goals_against, h_played), 2),
            "home_win_pct": round(_safe_divide(h_won, h_played), 3),
            "home_draw_pct": round(_safe_divide(h_drawn, h_played), 3),
            "home_form_string": h_form_str,
            "away_goals_for_per_game": round(_safe_divide(a_goals_for, a_played), 2),
            "away_goals_against_per_game": round(_safe_divide(a_goals_against, a_played), 2),
            "away_win_pct": round(_safe_divide(a_won, a_played), 3),
            "away_draw_pct": round(_safe_divide(a_drawn, a_played), 3),
            "away_form_string": a_form_str,
        }
    elif home_standing:
        h_pos = home_standing["position"]
        # Higher position = better; normalise
        relative = 1.0 - (_safe_divide(h_pos - 1, max_pos - 1))
        score = (relative - 0.5) * 0.6
        conf = 0.4
        rationale = f"Home team is {h_pos}{_ordinal(h_pos)} (away team not found in standings)."
        data_points = {"home_position": h_pos, "total_teams": total_teams}
    else:
        a_pos = away_standing["position"]  # type: ignore[union-attr]
        relative = 1.0 - (_safe_divide(a_pos - 1, max_pos - 1))
        score = -(relative - 0.5) * 0.6
        conf = 0.4
        rationale = f"Away team is {a_pos}{_ordinal(a_pos)} (home team not found in standings)."
        data_points = {"away_position": a_pos, "total_teams": total_teams}

    return FactorResult(
        name="league_position",
        score=score,
        confidence=conf,
        rationale=rationale,
        data_points=data_points,
    )


def _ordinal(n: int) -> str:
    """Return ordinal suffix for a number (1→'st', 2→'nd', etc.)."""
    if 11 <= (n % 100) <= 13:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


# ═══════════════════════════════════════════════════════════════
#  Factor 8 — Goal Timing
# ═══════════════════════════════════════════════════════════════

def goal_timing(
    db: Database,
    home_id: int,
    away_id: int,
    league_id: int,
    season: str,
) -> FactorResult:
    """Analyse first-half vs second-half goal tendencies.

    Uses half-time scores from match records where available.
    """
    home_matches = db.get_recent_matches(home_id, limit=10)
    away_matches = db.get_recent_matches(away_id, limit=10)

    def _timing_stats(
        matches: list[dict], team_id: int
    ) -> dict[str, Any] | None:
        ht_data = [
            m for m in matches
            if m.get("ht_home_score") is not None
            and m.get("ht_away_score") is not None
        ]
        if not ht_data:
            return None

        first_half_goals = 0
        second_half_goals = 0
        total_goals = 0

        for m in ht_data:
            is_home = m["home_team_id"] == team_id
            ft_goals = _goals_for(m, team_id)
            if is_home:
                ht_goals = m["ht_home_score"]
            else:
                ht_goals = m["ht_away_score"]
            sh_goals = ft_goals - ht_goals
            first_half_goals += ht_goals
            second_half_goals += max(0, sh_goals)
            total_goals += ft_goals

        n = len(ht_data)
        fh_pct = _safe_divide(first_half_goals, total_goals)
        sh_pct = _safe_divide(second_half_goals, total_goals)

        # HT win/draw/loss rates
        ht_wins = 0
        ht_draws = 0
        ht_losses = 0
        for m in ht_data:
            is_home = m["home_team_id"] == team_id
            if is_home:
                t_ht = m["ht_home_score"]
                o_ht = m["ht_away_score"]
            else:
                t_ht = m["ht_away_score"]
                o_ht = m["ht_home_score"]
            if t_ht > o_ht:
                ht_wins += 1
            elif t_ht == o_ht:
                ht_draws += 1
            else:
                ht_losses += 1

        return {
            "first_half_goals": first_half_goals,
            "second_half_goals": second_half_goals,
            "total_goals": total_goals,
            "first_half_pct": round(fh_pct, 3),
            "second_half_pct": round(sh_pct, 3),
            "matches_with_ht_data": n,
            "ht_win_rate": round(_safe_divide(ht_wins, n), 3),
            "ht_draw_rate": round(_safe_divide(ht_draws, n), 3),
            "ht_loss_rate": round(_safe_divide(ht_losses, n), 3),
        }

    h_timing = _timing_stats(home_matches, home_id) if home_matches else None
    a_timing = _timing_stats(away_matches, away_id) if away_matches else None

    if not h_timing and not a_timing:
        return _empty_result("goal_timing", "No half-time score data available")

    # Goal timing doesn't directly favour home/away in the 1X2 sense,
    # so score is closer to 0.  But a team that scores heavily in the
    # first half and the opponent is a slow starter is valuable info.
    score = 0.0
    parts: list[str] = []

    if h_timing:
        parts.append(
            f"Home team scores {h_timing['first_half_pct']:.0%} of goals in the 1st half, "
            f"{h_timing['second_half_pct']:.0%} in the 2nd half "
            f"({h_timing['first_half_goals']}H1 / {h_timing['second_half_goals']}H2)"
        )
        if h_timing["first_half_pct"] > 0.55:
            score += 0.15  # Home starts strong

    if a_timing:
        parts.append(
            f"Away team scores {a_timing['first_half_pct']:.0%} of goals in the 1st half, "
            f"{a_timing['second_half_pct']:.0%} in the 2nd half "
            f"({a_timing['first_half_goals']}H1 / {a_timing['second_half_goals']}H2)"
        )
        if a_timing["second_half_pct"] > 0.65:
            score -= 0.1  # Away is a strong finisher

    n_samples = (h_timing or {}).get("matches_with_ht_data", 0) + \
                (a_timing or {}).get("matches_with_ht_data", 0)
    conf = _confidence_from_matches(n_samples, ideal=12) * 0.7

    return FactorResult(
        name="goal_timing",
        score=max(-1.0, min(1.0, score)),
        confidence=conf,
        rationale=". ".join(parts) + ".",
        data_points={
            "home": h_timing or {},
            "away": a_timing or {},
            "home_1h_win_rate": h_timing.get("ht_win_rate") if h_timing else None,
            "away_1h_win_rate": a_timing.get("ht_win_rate") if a_timing else None,
        },
    )


# ═══════════════════════════════════════════════════════════════
#  Factor 9 — Momentum
# ═══════════════════════════════════════════════════════════════

def momentum(
    db: Database,
    home_id: int,
    away_id: int,
    league_id: int,
    season: str,
) -> FactorResult:
    """Compare last 3 results to the previous 3 for each team.

    Detects whether a team is improving or declining.
    """
    home_matches = db.get_recent_matches(home_id, limit=6)
    away_matches = db.get_recent_matches(away_id, limit=6)

    if len(home_matches) < 3 and len(away_matches) < 3:
        return _empty_result("momentum", "Need at least 3 matches for momentum analysis")

    def _momentum_calc(
        matches: list[dict], team_id: int
    ) -> dict[str, Any] | None:
        if len(matches) < 3:
            return None
        # matches are most-recent-first from DB
        recent_3 = matches[:3]
        prev_3 = matches[3:6] if len(matches) >= 6 else matches[3:]

        recent_pts = sum(_match_points(m, team_id) for m in recent_3)
        recent_ppg = recent_pts / len(recent_3)

        if prev_3:
            prev_pts = sum(_match_points(m, team_id) for m in prev_3)
            prev_ppg = prev_pts / len(prev_3)
            delta = recent_ppg - prev_ppg
        else:
            prev_ppg = recent_ppg
            delta = 0.0

        recent_form = "".join(_match_result_char(m, team_id) for m in reversed(recent_3))
        prev_form = "".join(_match_result_char(m, team_id) for m in reversed(prev_3)) if prev_3 else ""

        if delta > 0.5:
            trend = "improving"
        elif delta < -0.5:
            trend = "declining"
        else:
            trend = "steady"

        return {
            "recent_ppg": round(recent_ppg, 2),
            "prev_ppg": round(prev_ppg, 2),
            "delta": round(delta, 2),
            "trend": trend,
            "recent_form": recent_form,
            "prev_form": prev_form,
            "recent_sample": len(recent_3),
            "prev_sample": len(prev_3),
        }

    h_mom = _momentum_calc(home_matches, home_id) if len(home_matches) >= 3 else None
    a_mom = _momentum_calc(away_matches, away_id) if len(away_matches) >= 3 else None

    score = 0.0
    parts: list[str] = []

    if h_mom and a_mom:
        # Positive delta = home improving more than away
        score = (h_mom["delta"] - a_mom["delta"]) / 4.0
        conf = 0.6
    elif h_mom:
        score = h_mom["delta"] / 4.0
        conf = 0.35
    elif a_mom:
        score = -a_mom["delta"] / 4.0
        conf = 0.35
    else:
        return _empty_result("momentum")

    if h_mom:
        parts.append(
            f"Home momentum: {h_mom['trend']} "
            f"(recent {h_mom['recent_form']} → {h_mom['recent_ppg']} PPG vs "
            f"previous {h_mom['prev_form']} → {h_mom['prev_ppg']} PPG, "
            f"Δ{h_mom['delta']:+.2f})"
        )
    if a_mom:
        parts.append(
            f"Away momentum: {a_mom['trend']} "
            f"(recent {a_mom['recent_form']} → {a_mom['recent_ppg']} PPG vs "
            f"previous {a_mom['prev_form']} → {a_mom['prev_ppg']} PPG, "
            f"Δ{a_mom['delta']:+.2f})"
        )

    return FactorResult(
        name="momentum",
        score=max(-1.0, min(1.0, score)),
        confidence=conf,
        rationale=". ".join(parts) + ".",
        data_points={"home": h_mom or {}, "away": a_mom or {}},
    )


# ═══════════════════════════════════════════════════════════════
#  Factor 10 — Scoring Patterns (BTTS / O/U)
# ═══════════════════════════════════════════════════════════════

def scoring_patterns(
    db: Database,
    home_id: int,
    away_id: int,
    league_id: int,
    season: str,
) -> FactorResult:
    """Evaluate BTTS rate, Over/Under rates from historical data.

    This factor doesn't strongly favour home or away; instead it captures
    match-type tendencies (high/low scoring, BTTS likely, etc.).
    """
    h_stats = db.get_team_season_stats(home_id, league_id, season)
    a_stats = db.get_team_season_stats(away_id, league_id, season)

    # Fall back to match-based computation if no season stats
    if not h_stats and not a_stats:
        return _compute_patterns_from_matches(db, home_id, away_id)

    data: dict[str, Any] = {}
    parts: list[str] = []

    # BTTS
    h_btts = h_stats.get("btts_percentage") if h_stats else None
    a_btts = a_stats.get("btts_percentage") if a_stats else None
    btts_avg: float | None = None
    if h_btts is not None and a_btts is not None:
        btts_avg = (h_btts + a_btts) / 2
    elif h_btts is not None:
        btts_avg = h_btts
    elif a_btts is not None:
        btts_avg = a_btts

    if btts_avg is not None:
        data["btts_avg_pct"] = round(btts_avg, 1)
        parts.append(f"BTTS tendency: {btts_avg:.0f}%")

    # Over 2.5
    h_o25 = h_stats.get("over_2_5_percentage") if h_stats else None
    a_o25 = a_stats.get("over_2_5_percentage") if a_stats else None
    o25_avg: float | None = None
    if h_o25 is not None and a_o25 is not None:
        o25_avg = (h_o25 + a_o25) / 2
    elif h_o25 is not None:
        o25_avg = h_o25
    elif a_o25 is not None:
        o25_avg = a_o25

    if o25_avg is not None:
        data["over_2_5_avg_pct"] = round(o25_avg, 1)
        parts.append(f"Over 2.5 tendency: {o25_avg:.0f}%")

    # Average goals
    h_avg_s = h_stats.get("avg_goals_scored") if h_stats else None
    a_avg_s = a_stats.get("avg_goals_scored") if a_stats else None
    h_avg_c = h_stats.get("avg_goals_conceded") if h_stats else None
    a_avg_c = a_stats.get("avg_goals_conceded") if a_stats else None

    if h_avg_s is not None and a_avg_c is not None:
        expected_home_goals = (h_avg_s + a_avg_c) / 2
        data["expected_home_goals"] = round(expected_home_goals, 2)
    if a_avg_s is not None and h_avg_c is not None:
        expected_away_goals = (a_avg_s + h_avg_c) / 2
        data["expected_away_goals"] = round(expected_away_goals, 2)

    if "expected_home_goals" in data and "expected_away_goals" in data:
        exp_total = data["expected_home_goals"] + data["expected_away_goals"]
        data["expected_total_goals"] = round(exp_total, 2)
        parts.append(f"Expected total goals: {exp_total:.1f}")

        # Add over_1_5 and over_3_5 averages from season stats if available
        if o25_avg is not None:
            data["under_2_5_avg_pct"] = round(100.0 - o25_avg, 1)

        # Poisson probabilities
        lam_home = data["expected_home_goals"]
        lam_away = data["expected_away_goals"]
        lam_total = lam_home + lam_away

        def _poisson_cdf(lam: float, k: int) -> float:
            """Cumulative Poisson probability P(X <= k)."""
            return sum(
                math.exp(-lam) * (lam ** i) / math.factorial(i)
                for i in range(k + 1)
            )

        data["home_over_0_5_prob"] = round(1.0 - math.exp(-lam_home), 4)
        data["home_over_1_5_prob"] = round(1.0 - _poisson_cdf(lam_home, 1), 4)
        data["away_over_0_5_prob"] = round(1.0 - math.exp(-lam_away), 4)
        data["away_over_1_5_prob"] = round(1.0 - _poisson_cdf(lam_away, 1), 4)
        data["over_0_5_prob"] = round(1.0 - math.exp(-lam_total), 4)
        data["over_1_5_prob"] = round(1.0 - _poisson_cdf(lam_total, 1), 4)
        data["over_2_5_prob"] = round(1.0 - _poisson_cdf(lam_total, 2), 4)
        data["over_3_5_prob"] = round(1.0 - _poisson_cdf(lam_total, 3), 4)
        data["btts_prob"] = round(
            (1.0 - math.exp(-lam_home)) * (1.0 - math.exp(-lam_away)), 4
        )

    # Failed to score
    h_fts = h_stats.get("failed_to_score") if h_stats else None
    a_fts = a_stats.get("failed_to_score") if a_stats else None
    h_mp = h_stats.get("matches_played", 1) if h_stats else 1
    a_mp = a_stats.get("matches_played", 1) if a_stats else 1

    if h_fts is not None:
        data["home_failed_to_score_pct"] = round(_safe_divide(h_fts, h_mp) * 100, 1)
    if a_fts is not None:
        data["away_failed_to_score_pct"] = round(_safe_divide(a_fts, a_mp) * 100, 1)

    if not parts:
        return _empty_result("scoring_patterns")

    # Score: this factor is about match characteristics rather than favouring
    # a side; use a small tilt based on whether home or away is the bigger scorer
    score = 0.0
    if h_avg_s is not None and a_avg_s is not None:
        score = (h_avg_s - a_avg_s) / 3.0
        score = max(-0.3, min(0.3, score))

    has_stats = sum(1 for v in [h_btts, a_btts, h_o25, a_o25] if v is not None)
    conf = min(1.0, has_stats / 4) * 0.7

    data["home_stats_available"] = h_stats is not None
    data["away_stats_available"] = a_stats is not None

    return FactorResult(
        name="scoring_patterns",
        score=score,
        confidence=conf,
        rationale=". ".join(parts) + ".",
        data_points=data,
    )


def _compute_patterns_from_matches(
    db: Database, home_id: int, away_id: int
) -> FactorResult:
    """Compute BTTS and O/U rates from raw match data when season stats unavailable."""
    home_matches = db.get_recent_matches(home_id, limit=10)
    away_matches = db.get_recent_matches(away_id, limit=10)

    if not home_matches and not away_matches:
        return _empty_result("scoring_patterns")

    def _pattern_stats(matches: list[dict], team_id: int) -> dict[str, Any]:
        n = len(matches)
        btts = 0
        over_25 = 0
        for m in matches:
            gf = _goals_for(m, team_id)
            ga = _goals_against(m, team_id)
            if gf > 0 and ga > 0:
                btts += 1
            if gf + ga > 2:
                over_25 += 1
        return {
            "btts_pct": round(_safe_divide(btts, n) * 100, 1),
            "over_2_5_pct": round(_safe_divide(over_25, n) * 100, 1),
            "sample": n,
        }

    h_pat = _pattern_stats(home_matches, home_id) if home_matches else None
    a_pat = _pattern_stats(away_matches, away_id) if away_matches else None

    data: dict[str, Any] = {"source": "match_history"}
    parts: list[str] = []

    if h_pat:
        data["home_btts_pct"] = h_pat["btts_pct"]
        data["home_over_2_5_pct"] = h_pat["over_2_5_pct"]
        parts.append(f"Home BTTS {h_pat['btts_pct']:.0f}%, O2.5 {h_pat['over_2_5_pct']:.0f}%")
    if a_pat:
        data["away_btts_pct"] = a_pat["btts_pct"]
        data["away_over_2_5_pct"] = a_pat["over_2_5_pct"]
        parts.append(f"Away BTTS {a_pat['btts_pct']:.0f}%, O2.5 {a_pat['over_2_5_pct']:.0f}%")

    n_total = (h_pat or {}).get("sample", 0) + (a_pat or {}).get("sample", 0)
    conf = _confidence_from_matches(n_total, ideal=16) * 0.5

    return FactorResult(
        name="scoring_patterns",
        score=0.0,
        confidence=conf,
        rationale=". ".join(parts) + ".",
        data_points=data,
    )


# ═══════════════════════════════════════════════════════════════
#  Factor 11 — Rest Days
# ═══════════════════════════════════════════════════════════════

def rest_days(
    db: Database,
    home_id: int,
    away_id: int,
    league_id: int,
    season: str,
) -> FactorResult:
    """Evaluate days since each team's last match.  Flag fixture congestion."""
    home_recent = db.get_recent_matches(home_id, limit=2)
    away_recent = db.get_recent_matches(away_id, limit=2)

    today = datetime.utcnow()

    def _days_since(matches: list[dict]) -> int | None:
        if not matches:
            return None
        last_date_str = matches[0].get("date", "")
        if not last_date_str:
            return None
        try:
            last_date = datetime.fromisoformat(last_date_str.replace("Z", "+00:00"))
            return (today - last_date.replace(tzinfo=None)).days
        except (ValueError, TypeError):
            return None

    h_rest = _days_since(home_recent)
    a_rest = _days_since(away_recent)

    if h_rest is None and a_rest is None:
        return _empty_result("rest_days", "Cannot determine last match dates")

    data: dict[str, Any] = {}
    parts: list[str] = []
    flags: list[str] = []

    if h_rest is not None:
        data["home_rest_days"] = h_rest
        parts.append(f"Home team: {h_rest} days rest")
        if h_rest <= 3:
            flags.append("Home fixture congestion")
        elif h_rest >= 14:
            flags.append("Home may be rusty (14+ days rest)")

    if a_rest is not None:
        data["away_rest_days"] = a_rest
        parts.append(f"Away team: {a_rest} days rest")
        if a_rest <= 3:
            flags.append("Away fixture congestion")
        elif a_rest >= 14:
            flags.append("Away may be rusty (14+ days rest)")

    if flags:
        data["flags"] = flags

    # Score: the better-rested team (within reason) has an advantage
    score = 0.0
    if h_rest is not None and a_rest is not None:
        # Optimal rest is ~5-7 days.  Penalise extremes.
        def _rest_quality(days: int) -> float:
            if days <= 2:
                return -0.3  # Very congested
            if days == 3:
                return -0.1
            if 4 <= days <= 7:
                return 0.1
            if 8 <= days <= 13:
                return 0.0
            return -0.05  # Rusty

        h_quality = _rest_quality(h_rest)
        a_quality = _rest_quality(a_rest)
        score = (h_quality - a_quality)
        score = max(-1.0, min(1.0, score))

    conf = 0.5 if (h_rest is not None and a_rest is not None) else 0.2

    # Store min_rest so _assess_risks can detect fixture congestion
    if h_rest is not None and a_rest is not None:
        data["min_rest"] = min(h_rest, a_rest)
    elif h_rest is not None:
        data["min_rest"] = h_rest
    elif a_rest is not None:
        data["min_rest"] = a_rest

    if flags:
        parts.append("⚠ " + "; ".join(flags))

    return FactorResult(
        name="rest_days",
        score=score,
        confidence=conf,
        rationale=". ".join(parts) + ".",
        data_points=data,
    )


# ═══════════════════════════════════════════════════════════════
#  Factor 12 — Rivalry / Derby Detection
# ═══════════════════════════════════════════════════════════════

# Known derby pairings by API-Football team IDs.  Extendable.
_KNOWN_DERBIES: list[set[int]] = [
    # England
    {33, 34},        # Manchester United vs Manchester City
    {40, 36},        # Liverpool vs Everton
    {47, 49},        # Tottenham vs Arsenal
    {49, 50},        # Arsenal vs Chelsea
    {48, 65},        # West Ham vs Nottingham Forest (London vs Midlands)
    # Spain
    {529, 530},      # Barcelona vs Real Madrid (El Clásico)
    {530, 531},      # Real Madrid vs Atlético Madrid
    # Italy
    {489, 492},      # AC Milan vs Inter (Derby della Madonnina)
    {487, 496},      # Lazio vs Roma (Derby della Capitale)
    {492, 496},      # Inter vs Roma
    {496, 497},      # Roma vs Juventus
    # Germany
    {157, 165},      # Bayern vs Dortmund (Der Klassiker)
    # France
    {85, 80},        # PSG vs Marseille (Le Classique)
    # Scotland
    {247, 248},      # Celtic vs Rangers (Old Firm)
    # Netherlands
    {194, 197},      # Ajax vs Feyenoord (De Klassieker)
    {194, 204},      # Ajax vs PSV
    # Turkey
    {645, 611},      # Galatasaray vs Fenerbahçe
    {645, 616},      # Galatasaray vs Beşiktaş
]


def rivalry(
    db: Database,
    home_id: int,
    away_id: int,
    league_id: int,
    season: str,
) -> FactorResult:
    """Detect derby / rivalry matches and assess historical upset frequency.

    Derbies tend to be more unpredictable — results cluster closer to 50/50.
    """
    # Check API-Football IDs for known derbies
    home_team = db.get_team(home_id)
    away_team = db.get_team(away_id)

    home_api_id = home_team.get("api_football_id") if home_team else None
    away_api_id = away_team.get("api_football_id") if away_team else None

    is_derby = False
    derby_name: str | None = None

    if home_api_id and away_api_id:
        pair = {home_api_id, away_api_id}
        if pair in _KNOWN_DERBIES:
            is_derby = True
            h_name = home_team.get("name", "Home") if home_team else "Home"
            a_name = away_team.get("name", "Away") if away_team else "Away"
            derby_name = f"{h_name} vs {a_name} derby"

    # Also detect same-city teams by name heuristic
    if not is_derby and home_team and away_team:
        h_name = (home_team.get("name") or "").lower()
        a_name = (away_team.get("name") or "").lower()
        # Simple city detection: if teams share a city word (> 3 chars)
        h_words = set(w for w in h_name.split() if len(w) > 3)
        a_words = set(w for w in a_name.split() if len(w) > 3)
        common = h_words & a_words
        # Exclude common football words
        common -= {"city", "united", "athletic", "sport", "club", "football", "real"}
        if common:
            is_derby = True
            derby_name = f"Possible local derby (shared: {', '.join(common)})"

    if not is_derby:
        return FactorResult(
            name="rivalry",
            score=0.0,
            confidence=0.3,
            rationale="No rivalry or derby detected. Standard match dynamics expected.",
            data_points={"is_derby": False},
        )

    # Derbies are more unpredictable — pull the score towards 0
    # and flag the increased variance
    h2h = db.get_h2h(home_id, away_id)
    upset_info = ""
    if h2h and h2h.get("total_matches", 0) >= 3:
        total = h2h["total_matches"]
        drw = h2h.get("draws", 0)
        draw_rate = _safe_divide(drw, total)
        upset_info = f" Draw rate in meetings: {draw_rate:.0%} ({drw}/{total})."

    score = 0.0  # Derbies neutralise advantage
    conf = 0.6

    rationale = (
        f"🔥 Derby detected: {derby_name}. "
        f"Derbies are historically more unpredictable with tighter margins."
        f"{upset_info}"
    )

    return FactorResult(
        name="rivalry",
        score=score,
        confidence=conf,
        rationale=rationale,
        data_points={
            "is_derby": True,
            "derby_name": derby_name,
            "h2h_available": h2h is not None,
        },
    )



# ═══════════════════════════════════════════════════════════════
#  Factor 13 — Venue Advantage
# ═══════════════════════════════════════════════════════════════

def venue_advantage(
    db: Database,
    home_id: int,
    away_id: int,
    league_id: int,
    season: str,
) -> FactorResult:
    """Quantify the home team's venue advantage based on home vs away record split.

    Compares the home team's home win rate vs their away win rate, and the
    away team's away win rate vs their home win rate.  A strong home performer
    playing at home against a poor away traveller = high positive score.
    """
    home_home = db.get_recent_matches(home_id, limit=12, home_only=True)
    home_away = db.get_recent_matches(home_id, limit=12, away_only=True)
    away_home = db.get_recent_matches(away_id, limit=12, home_only=True)
    away_away = db.get_recent_matches(away_id, limit=12, away_only=True)

    if not home_home and not away_away:
        return _empty_result("venue_advantage", "Insufficient venue split data")

    def _wr(matches: list[dict], team_id: int) -> float | None:
        """Win rate for the team in a filtered set of matches."""
        if not matches:
            return None
        wins = sum(1 for m in matches if _match_result_char(m, team_id) == "W")
        return _safe_divide(wins, len(matches))

    def _gpg(matches: list[dict], team_id: int) -> float | None:
        if not matches:
            return None
        goals = [_goals_for(m, team_id) for m in matches]
        return _safe_divide(sum(goals), len(goals))

    def _cpg(matches: list[dict], team_id: int) -> float | None:
        if not matches:
            return None
        conceded = [_goals_against(m, team_id) for m in matches]
        return _safe_divide(sum(conceded), len(conceded))

    def _cs_rate(matches: list[dict], team_id: int) -> float | None:
        if not matches:
            return None
        cs = sum(1 for m in matches if _goals_against(m, team_id) == 0)
        return _safe_divide(cs, len(matches))

    hh_wr = _wr(home_home, home_id)
    ha_wr = _wr(home_away, home_id)
    ah_wr = _wr(away_home, away_id)
    aa_wr = _wr(away_away, away_id)

    home_venue_boost = (hh_wr - ha_wr) if (hh_wr is not None and ha_wr is not None) else None
    away_venue_drag = (aa_wr - ah_wr) if (aa_wr is not None and ah_wr is not None) else None

    # Score: positive = home venue advantage is real and away team travels poorly
    score = 0.0
    parts: list[str] = []

    if home_venue_boost is not None:
        score += home_venue_boost * 0.6
        parts.append(
            f"Home team home WR {hh_wr:.0%} vs away WR {ha_wr:.0%} "
            f"(venue boost: {home_venue_boost:+.0%})"
        )

    if away_venue_drag is not None:
        score += away_venue_drag * 0.4  # negative drag = they travel poorly
        parts.append(
            f"Away team away WR {aa_wr:.0%} vs home WR {ah_wr:.0%} "
            f"(travel effect: {away_venue_drag:+.0%})"
        )

    score = max(-1.0, min(1.0, score))

    n_total = len(home_home) + len(away_away)
    conf = _confidence_from_matches(n_total, ideal=16) * 0.85

    return FactorResult(
        name="venue_advantage",
        score=score,
        confidence=conf,
        rationale=". ".join(parts) + "." if parts else "Venue split data computed.",
        data_points={
            "home_home_wr": round(hh_wr, 3) if hh_wr is not None else None,
            "home_away_wr": round(ha_wr, 3) if ha_wr is not None else None,
            "away_home_wr": round(ah_wr, 3) if ah_wr is not None else None,
            "away_away_wr": round(aa_wr, 3) if aa_wr is not None else None,
            "home_venue_boost": round(home_venue_boost, 3) if home_venue_boost is not None else None,
            "away_travel_drag": round(away_venue_drag, 3) if away_venue_drag is not None else None,
            "home_home_gpg": round(_gpg(home_home, home_id), 2) if _gpg(home_home, home_id) is not None else None,
            "away_away_gpg": round(_gpg(away_away, away_id), 2) if _gpg(away_away, away_id) is not None else None,
            "home_home_cpg": round(_cpg(home_home, home_id), 2) if _cpg(home_home, home_id) is not None else None,
            "away_away_cpg": round(_cpg(away_away, away_id), 2) if _cpg(away_away, away_id) is not None else None,
            "home_home_cs_rate": round(_cs_rate(home_home, home_id), 3) if _cs_rate(home_home, home_id) is not None else None,
            "away_away_cs_rate": round(_cs_rate(away_away, away_id), 3) if _cs_rate(away_away, away_id) is not None else None,
        },
    )


# ═══════════════════════════════════════════════════════════════
#  Factor 14 — Attacking Matchup
# ═══════════════════════════════════════════════════════════════

def attacking_matchup(
    db: Database,
    home_id: int,
    away_id: int,
    league_id: int,
    season: str,
) -> FactorResult:
    """Cross-compare each team's attack vs the opponent's defence.

    Uses goals-per-game as attack proxy and goals-conceded-per-game as
    defence proxy, then measures the mismatch in both directions.
    A home team with a much stronger attack vs a weak away defence → positive.
    """
    home_matches = db.get_recent_matches(home_id, limit=10)
    away_matches = db.get_recent_matches(away_id, limit=10)

    if not home_matches or not away_matches:
        return _empty_result("attacking_matchup", "Insufficient match data for both teams")

    # Home team: GPG scored, CPG conceded
    home_goals_scored = [_goals_for(m, home_id) for m in home_matches]
    home_goals_conceded = [_goals_against(m, home_id) for m in home_matches]
    home_gpg = _safe_divide(sum(home_goals_scored), len(home_goals_scored))
    home_cpg = _safe_divide(sum(home_goals_conceded), len(home_goals_conceded))

    # Away team: GPG scored, CPG conceded
    away_goals_scored = [_goals_for(m, away_id) for m in away_matches]
    away_goals_conceded = [_goals_against(m, away_id) for m in away_matches]
    away_gpg = _safe_divide(sum(away_goals_scored), len(away_goals_scored))
    away_cpg = _safe_divide(sum(away_goals_conceded), len(away_goals_conceded))

    # Attack vs Defence matchup scores
    # How does home's attack look vs away's defence?
    home_attack_vs_away_def = home_gpg - away_cpg  # Positive = home attack > away concedes
    # How does away's attack look vs home's defence?
    away_attack_vs_home_def = away_gpg - home_cpg  # Positive = away attack > home concedes

    # Net: positive = home is net better in the matchup
    matchup_diff = home_attack_vs_away_def - away_attack_vs_home_def
    score = matchup_diff / 3.0  # Normalise (~3 goal range is extreme)
    score = max(-1.0, min(1.0, score))

    n_total = len(home_matches) + len(away_matches)
    conf = _confidence_from_matches(n_total, ideal=16) * 0.75

    rationale = (
        f"Home attack ({home_gpg:.1f} GPG) vs Away defence ({away_cpg:.1f} CPG): "
        f"matchup edge {home_attack_vs_away_def:+.2f}. "
        f"Away attack ({away_gpg:.1f} GPG) vs Home defence ({home_cpg:.1f} CPG): "
        f"matchup edge {away_attack_vs_home_def:+.2f}."
    )

    return FactorResult(
        name="attacking_matchup",
        score=score,
        confidence=conf,
        rationale=rationale,
        data_points={
            "home_gpg": round(home_gpg, 2),
            "home_cpg": round(home_cpg, 2),
            "away_gpg": round(away_gpg, 2),
            "away_cpg": round(away_cpg, 2),
            "home_attack_vs_away_def": round(home_attack_vs_away_def, 3),
            "away_attack_vs_home_def": round(away_attack_vs_home_def, 3),
            "matchup_diff": round(matchup_diff, 3),
        },
    )


# ═══════════════════════════════════════════════════════════════
#  Factor 15 — xG Quality
# ═══════════════════════════════════════════════════════════════

def xg_quality(
    db: Database,
    home_id: int,
    away_id: int,
    league_id: int,
    season: str,
) -> FactorResult:
    """Compare expected goals (xG) vs actual goals to assess underlying quality.

    Overperforming xG (scoring more than expected) can indicate good finishing
    but may regress.  Underperforming suggests underlying quality better than
    results show.

    Falls back gracefully if no xG data is available.
    """
    # Fetch match stats with xG data
    home_recent = db.get_recent_matches(home_id, limit=10)
    away_recent = db.get_recent_matches(away_id, limit=10)

    if not home_recent and not away_recent:
        return _empty_result("xg_quality", "No match data for xG analysis")

    def _xg_stats(matches: list[dict], team_id: int) -> dict[str, Any] | None:
        """Compute xG stats for a team across recent matches."""
        xg_values: list[float] = []
        actual_goals: list[int] = []

        for m in matches:
            match_id = m.get("id")
            if match_id is None:
                continue
            stats = db.get_match_stats(match_id)
            if not stats:
                continue
            team_stat = next((s for s in stats if s["team_id"] == team_id), None)
            if team_stat and team_stat.get("expected_goals") is not None:
                xg_values.append(float(team_stat["expected_goals"]))
                actual_goals.append(_goals_for(m, team_id))

        if len(xg_values) < 3:  # Minimum 3 matches with xG data
            return None

        n = len(xg_values)
        avg_xg = _safe_divide(sum(xg_values), n)
        avg_actual = _safe_divide(sum(actual_goals), n)
        xg_diff = avg_actual - avg_xg  # Positive = overperforming xG

        return {
            "avg_xg": round(avg_xg, 3),
            "avg_actual": round(avg_actual, 3),
            "xg_diff": round(xg_diff, 3),
            "sample": n,
            "xg_overperformance": xg_diff > 0.2,
            "xg_underperformance": xg_diff < -0.2,
        }

    h_xg = _xg_stats(home_recent, home_id) if home_recent else None
    a_xg = _xg_stats(away_recent, away_id) if away_recent else None

    if not h_xg and not a_xg:
        return _empty_result("xg_quality", "No xG data found in match statistics")

    parts: list[str] = []
    score = 0.0

    if h_xg:
        parts.append(
            f"Home xG: {h_xg['avg_xg']:.2f} avg, actual {h_xg['avg_actual']:.2f} "
            f"({'overperforming' if h_xg['xg_overperformance'] else 'underperforming' if h_xg['xg_underperformance'] else 'on track'})"
        )
        # Underperforming xG = underlying quality is better = buy
        score += -h_xg["xg_diff"] * 0.3  # Regress toward xG

    if a_xg:
        parts.append(
            f"Away xG: {a_xg['avg_xg']:.2f} avg, actual {a_xg['avg_actual']:.2f} "
            f"({'overperforming' if a_xg['xg_overperformance'] else 'underperforming' if a_xg['xg_underperformance'] else 'on track'})"
        )
        score -= -a_xg["xg_diff"] * 0.3

    score = max(-1.0, min(1.0, score))
    n_total = (h_xg or {}).get("sample", 0) + (a_xg or {}).get("sample", 0)
    conf = _confidence_from_matches(n_total, ideal=10) * 0.65

    return FactorResult(
        name="xg_quality",
        score=score,
        confidence=conf,
        rationale=". ".join(parts) + ".",
        data_points={
            "home": h_xg or {},
            "away": a_xg or {},
            "home_avg_xg": h_xg.get("avg_xg") if h_xg else None,
            "away_avg_xg": a_xg.get("avg_xg") if a_xg else None,
            "home_xg_diff": h_xg.get("xg_diff") if h_xg else None,
            "away_xg_diff": a_xg.get("xg_diff") if a_xg else None,
        },
    )


# ═══════════════════════════════════════════════════════════════
#  Factor Registry
# ═══════════════════════════════════════════════════════════════

ALL_FACTORS: dict[str, callable] = {
    "recent_form": recent_form,
    "home_form": home_form,
    "away_form": away_form,
    "scoring_frequency": scoring_frequency,
    "defensive_record": defensive_record,
    "head_to_head": head_to_head,
    "league_position": league_position,
    "goal_timing": goal_timing,
    "momentum": momentum,
    "scoring_patterns": scoring_patterns,
    "rest_days": rest_days,
    "rivalry": rivalry,
    "venue_advantage": venue_advantage,
    "attacking_matchup": attacking_matchup,
    "xg_quality": xg_quality,
}
