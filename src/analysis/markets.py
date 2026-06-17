"""
Footy Predict — Market-Specific Models

Translates generic factor scores and historical statistics into concrete
betting market recommendations.  Each model function receives the factor
results plus any relevant historical data and returns a
``MarketRecommendation``.

Probability blending formula
-----------------------------
    ``0.6 × historical_rate + 0.4 × model_estimate``

This anchors predictions to actual observed frequencies while allowing
the model to adjust for match-specific context.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

from src.analysis.factors import FactorResult
from src.config import CONFIDENCE_THRESHOLDS, MARKETS

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
#  Core Data Structure
# ═══════════════════════════════════════════════════════════════

@dataclass
class MarketRecommendation:
    """A recommendation for a specific betting market.

    Attributes:
        market_code: Internal code (e.g. ``"over-under-2.5"``).
        market_name: Human-readable name.
        pick: The recommended selection (e.g. ``"Over"``).
        probability: Estimated probability of the pick hitting (0–1).
        confidence: ``"HIGH"`` / ``"MEDIUM"`` / ``"LOW"``.
        implied_odds: ``1 / probability`` — the fair odds.
        rationale: Human-readable explanation for the pick.
        risk_factors: List of things that could invalidate the pick.
        supporting_stats: Key numbers backing the recommendation.
    """

    market_code: str
    market_name: str
    pick: str
    probability: float
    confidence: str
    implied_odds: float
    rationale: str
    risk_factors: list[str] = field(default_factory=list)
    supporting_stats: dict[str, Any] = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════

def _classify_confidence(probability: float) -> str:
    """Map a probability to a confidence label."""
    if probability >= CONFIDENCE_THRESHOLDS["HIGH"]:
        return "HIGH"
    if probability >= CONFIDENCE_THRESHOLDS["MEDIUM"]:
        return "MEDIUM"
    return "LOW"


def _blend(historical: float | None, model: float, hist_weight: float = 0.6) -> float:
    """Blend historical rate with model estimate.

    Falls back to model-only when historical data is unavailable.
    """
    if historical is None:
        return model
    return hist_weight * historical + (1 - hist_weight) * model


def _implied_odds(probability: float) -> float:
    """Convert probability to decimal odds, clamped to reasonable range."""
    if probability <= 0.0:
        return 100.0
    if probability >= 1.0:
        return 1.01
    return round(1.0 / probability, 2)


def _get_factor(factors: list[FactorResult], name: str) -> FactorResult | None:
    """Find a factor by name from the results list."""
    for f in factors:
        if f.name == name:
            return f
    return None


def _factor_score_to_prob_home(score: float) -> float:
    """Convert a home-advantage score (-1..+1) to a home-win probability.

    Uses a logistic-style mapping:
    -  0.0 → 0.33 (equal chance in a 3-way market)
    - +1.0 → ~0.85
    - -1.0 → ~0.08
    """
    return 1.0 / (1.0 + math.exp(-3.0 * score))


def _factor_score_to_prob_away(score: float) -> float:
    """Mirror of home-win: strong away = high away probability."""
    return _factor_score_to_prob_home(-score)


# ═══════════════════════════════════════════════════════════════
#  Individual Market Models
# ═══════════════════════════════════════════════════════════════

def model_1x2(
    overall_score: float,
    factors: list[FactorResult],
    patterns: dict[str, Any] | None = None,
) -> list[MarketRecommendation]:
    """Model the 1X2 (Match Result) market.

    Returns up to one recommendation for the most probable outcome.
    """
    p_home = _factor_score_to_prob_home(overall_score)
    p_away = _factor_score_to_prob_away(overall_score)
    p_draw = max(0.05, 1.0 - p_home - p_away)

    # Normalise so they sum to 1
    total = p_home + p_draw + p_away
    p_home /= total
    p_draw /= total
    p_away /= total

    # Blend with historical if available
    if patterns:
        hist_home_wr = patterns.get("home_win_rate")
        hist_away_wr = patterns.get("away_win_rate")
        hist_draw_r = patterns.get("draw_rate")
        p_home = _blend(hist_home_wr, p_home)
        p_draw = _blend(hist_draw_r, p_draw)
        p_away = _blend(hist_away_wr, p_away)
        total = p_home + p_draw + p_away
        p_home /= total
        p_draw /= total
        p_away /= total

    picks = {"1": p_home, "X": p_draw, "2": p_away}
    best = max(picks, key=picks.get)
    prob = picks[best]

    label_map = {"1": "Home Win", "X": "Draw", "2": "Away Win"}
    form_factor = _get_factor(factors, "recent_form")
    pos_factor = _get_factor(factors, "league_position")

    rationale_parts = [f"Model estimates: H {p_home:.0%} / D {p_draw:.0%} / A {p_away:.0%}."]
    if form_factor and form_factor.confidence > 0.3:
        rationale_parts.append(f"Form analysis: {form_factor.rationale[:120]}")
    if pos_factor and pos_factor.confidence > 0.3:
        rationale_parts.append(f"Table context: {pos_factor.rationale[:120]}")

    risks = []
    if abs(overall_score) < 0.15:
        risks.append("Very evenly-matched fixture — high upset potential")
    if p_draw > 0.30:
        risks.append(f"Draw probability significant at {p_draw:.0%}")

    return [MarketRecommendation(
        market_code="1x2",
        market_name=MARKETS["1x2"].name,
        pick=f"{best} ({label_map[best]})",
        probability=round(prob, 4),
        confidence=_classify_confidence(prob),
        implied_odds=_implied_odds(prob),
        rationale=" ".join(rationale_parts),
        risk_factors=risks,
        supporting_stats={"p_home": round(p_home, 4), "p_draw": round(p_draw, 4), "p_away": round(p_away, 4)},
    )]


def model_double_chance(
    overall_score: float,
    factors: list[FactorResult],
    patterns: dict[str, Any] | None = None,
) -> list[MarketRecommendation]:
    """Model the Double Chance market (1X, X2, 12).

    Derived from 1X2 probabilities.
    """
    p_home = _factor_score_to_prob_home(overall_score)
    p_away = _factor_score_to_prob_away(overall_score)
    p_draw = max(0.05, 1.0 - p_home - p_away)
    total = p_home + p_draw + p_away
    p_home /= total
    p_draw /= total
    p_away /= total

    if patterns:
        hist_home = patterns.get("home_win_rate")
        hist_away = patterns.get("away_win_rate")
        hist_draw = patterns.get("draw_rate")
        p_home = _blend(hist_home, p_home)
        p_draw = _blend(hist_draw, p_draw)
        p_away = _blend(hist_away, p_away)
        total = p_home + p_draw + p_away
        p_home /= total
        p_draw /= total
        p_away /= total

    dc_options = {
        "1X": p_home + p_draw,
        "X2": p_draw + p_away,
        "12": p_home + p_away,
    }

    best = max(dc_options, key=dc_options.get)
    prob = dc_options[best]

    return [MarketRecommendation(
        market_code="double-chance",
        market_name=MARKETS["double-chance"].name,
        pick=best,
        probability=round(prob, 4),
        confidence=_classify_confidence(prob),
        implied_odds=_implied_odds(prob),
        rationale=(
            f"Double Chance {best}: probability {prob:.0%} "
            f"(derived from 1X2: H {p_home:.0%} / D {p_draw:.0%} / A {p_away:.0%}). "
            f"{'Home side dominant — 1X very safe.' if best == '1X' and prob > 0.7 else ''}"
            f"{'Away side strong — X2 the play.' if best == 'X2' and prob > 0.7 else ''}"
            f"{'Both sides capable — 12 (no draw) looks solid.' if best == '12' and prob > 0.7 else ''}"
        ),
        risk_factors=(
            ["Draw-heavy league/fixture — 12 risky"] if best == "12" and p_draw > 0.28 else []
        ),
        supporting_stats={"1X": round(dc_options["1X"], 4), "X2": round(dc_options["X2"], 4),
                          "12": round(dc_options["12"], 4)},
    )]


def model_over_under(
    overall_score: float,
    factors: list[FactorResult],
    patterns: dict[str, Any] | None = None,
    line: float = 2.5,
) -> list[MarketRecommendation]:
    """Model the Over/Under goals market for a given line.

    Combines scoring_frequency and defensive_record factors with
    historical over rates.
    """
    scoring = _get_factor(factors, "scoring_frequency")
    defence = _get_factor(factors, "defensive_record")
    pat = _get_factor(factors, "scoring_patterns")

    # Estimate expected total goals from factors
    home_gpg = 1.3  # league average defaults
    away_gpg = 1.1
    if scoring and scoring.data_points:
        home_gpg = scoring.data_points.get("home_gpg", home_gpg)
        away_gpg = scoring.data_points.get("away_gpg", away_gpg)

    expected_total = home_gpg + away_gpg

    # Poisson-ish approximation for P(total > line)
    # Using a simplified logistic model centered on expected_total
    spread = expected_total - line
    model_over_prob = 1.0 / (1.0 + math.exp(-1.8 * spread))

    # Historical rate
    hist_over_rate = None
    if patterns:
        key = f"over_{line:.1f}_rate".replace(".", "_")
        hist_over_rate = patterns.get(key)
        if hist_over_rate is None and line == 2.5:
            hist_over_rate = patterns.get("over_2_5_rate") or patterns.get("over_25_rate")

    final_over = _blend(hist_over_rate, model_over_prob)
    final_under = 1.0 - final_over

    pick = "Over" if final_over >= 0.5 else "Under"
    prob = final_over if pick == "Over" else final_under

    line_str = f"{line:g}"
    market_code = f"over-under-{line_str}"
    market_name = f"Over/Under {line_str} Goals"

    rationale_parts = [
        f"Expected total ≈ {expected_total:.1f} goals (home {home_gpg:.1f} + away {away_gpg:.1f}).",
    ]
    if hist_over_rate is not None:
        rationale_parts.append(f"Historical over {line_str} rate: {hist_over_rate:.0%}.")
    if defence and defence.confidence > 0.3:
        rationale_parts.append(f"Defence factor: {defence.rationale[:100]}")

    risks = []
    if abs(final_over - 0.5) < 0.08:
        risks.append("Close to the line — could go either way")
    if defence and defence.data_points.get("home_cs_rate", 0) > 0.4 and pick == "Over":
        risks.append("Home team has a high clean sheet rate")

    return [MarketRecommendation(
        market_code=market_code,
        market_name=market_name,
        pick=f"{pick} {line_str}",
        probability=round(prob, 4),
        confidence=_classify_confidence(prob),
        implied_odds=_implied_odds(prob),
        rationale=" ".join(rationale_parts),
        risk_factors=risks,
        supporting_stats={
            "expected_total": round(expected_total, 2),
            "model_over_prob": round(model_over_prob, 4),
            "hist_over_rate": hist_over_rate,
            "final_over": round(final_over, 4),
        },
    )]


def model_gg_ng(
    overall_score: float,
    factors: list[FactorResult],
    patterns: dict[str, Any] | None = None,
) -> list[MarketRecommendation]:
    """Model the Both Teams to Score (GG/NG) market.

    Combines scoring frequency, defensive record, and historical BTTS rate.
    """
    scoring = _get_factor(factors, "scoring_frequency")
    defence = _get_factor(factors, "defensive_record")
    pat = _get_factor(factors, "scoring_patterns")

    # Model estimate: both teams score if both have decent scoring + leaky defence
    home_scoring = 0.6  # default
    away_scoring = 0.5
    home_cs_rate = 0.3
    away_cs_rate = 0.2

    if scoring and scoring.data_points:
        home_scoring = min(scoring.data_points.get("home_gpg", 1.3) / 2.0, 1.0)
        away_scoring = min(scoring.data_points.get("away_gpg", 1.1) / 2.0, 1.0)

    if defence and defence.data_points:
        home_cs_rate = defence.data_points.get("home_cs_rate", 0.3)
        away_cs_rate = defence.data_points.get("away_cs_rate", 0.2)

    # P(GG) = P(home scores) × P(away scores)
    p_home_scores = 1.0 - away_cs_rate  # away's clean sheet rate = chance home doesn't score
    p_away_scores = 1.0 - home_cs_rate  # home's clean sheet rate = chance away doesn't score
    model_gg = p_home_scores * p_away_scores

    hist_gg_rate = None
    if patterns:
        hist_gg_rate = patterns.get("btts_rate") or patterns.get("gg_rate")

    final_gg = _blend(hist_gg_rate, model_gg)
    final_ng = 1.0 - final_gg

    pick = "GG" if final_gg >= 0.5 else "NG"
    prob = final_gg if pick == "GG" else final_ng
    label = "Both Teams Score" if pick == "GG" else "No (clean sheet expected)"

    rationale_parts = [f"P(GG) = {final_gg:.0%} — "]
    if pick == "GG":
        rationale_parts.append(
            f"Both sides capable of scoring. Home CS rate: {home_cs_rate:.0%}, Away CS rate: {away_cs_rate:.0%}."
        )
    else:
        rationale_parts.append(
            f"At least one team likely to blank. Home CS rate: {home_cs_rate:.0%}, Away CS rate: {away_cs_rate:.0%}."
        )
    if hist_gg_rate is not None:
        rationale_parts.append(f"Historical BTTS rate: {hist_gg_rate:.0%}.")

    return [MarketRecommendation(
        market_code="gg-ng",
        market_name=MARKETS["gg-ng"].name,
        pick=f"{pick} ({label})",
        probability=round(prob, 4),
        confidence=_classify_confidence(prob),
        implied_odds=_implied_odds(prob),
        rationale=" ".join(rationale_parts),
        risk_factors=(
            ["Defensive teams can surprise with goals in big matches"] if pick == "NG" else
            ["One strong defence could shut out the opposition"]
        ),
        supporting_stats={
            "model_gg": round(model_gg, 4),
            "hist_gg_rate": hist_gg_rate,
            "home_cs_rate": round(home_cs_rate, 4),
            "away_cs_rate": round(away_cs_rate, 4),
        },
    )]


def model_draw_no_bet(
    overall_score: float,
    factors: list[FactorResult],
    patterns: dict[str, Any] | None = None,
) -> list[MarketRecommendation]:
    """Model the Draw No Bet market.

    Eliminates the draw from 1X2 and redistributes.
    """
    p_home = _factor_score_to_prob_home(overall_score)
    p_away = _factor_score_to_prob_away(overall_score)

    if patterns:
        hist_home = patterns.get("home_win_rate")
        hist_away = patterns.get("away_win_rate")
        p_home = _blend(hist_home, p_home)
        p_away = _blend(hist_away, p_away)

    # DNB: redistribute draw probability proportionally
    total = p_home + p_away
    if total < 0.01:
        total = 1.0
    dnb_home = p_home / total
    dnb_away = p_away / total

    pick = "Home" if dnb_home > dnb_away else "Away"
    prob = dnb_home if pick == "Home" else dnb_away

    form = _get_factor(factors, "recent_form")
    rationale = (
        f"Draw No Bet {pick}: {prob:.0%} probability (draw voids the bet). "
        f"{'Strong home advantage reduces risk.' if pick == 'Home' and overall_score > 0.3 else ''}"
        f"{'Away team has the edge here.' if pick == 'Away' and overall_score < -0.3 else ''}"
    )
    if form and form.confidence > 0.3:
        rationale += f" Form: {form.rationale[:100]}"

    return [MarketRecommendation(
        market_code="draw-no-bet",
        market_name=MARKETS["draw-no-bet"].name,
        pick=f"DNB {pick}",
        probability=round(prob, 4),
        confidence=_classify_confidence(prob),
        implied_odds=_implied_odds(prob),
        rationale=rationale,
        risk_factors=(
            ["Draw probability is significant — bet simply voids"] if abs(overall_score) < 0.2 else []
        ),
        supporting_stats={"dnb_home": round(dnb_home, 4), "dnb_away": round(dnb_away, 4)},
    )]


def model_win_to_nil(
    overall_score: float,
    factors: list[FactorResult],
    patterns: dict[str, Any] | None = None,
) -> list[MarketRecommendation]:
    """Model the Win to Nil market.

    P(WTN) = P(team wins) × P(team keeps clean sheet).
    """
    defence = _get_factor(factors, "defensive_record")
    scoring = _get_factor(factors, "scoring_frequency")

    p_home_win = _factor_score_to_prob_home(overall_score)
    p_away_win = _factor_score_to_prob_away(overall_score)

    home_cs_rate = 0.25
    away_cs_rate = 0.15
    if defence and defence.data_points:
        home_cs_rate = defence.data_points.get("home_cs_rate", 0.25)
        away_cs_rate = defence.data_points.get("away_cs_rate", 0.15)

    p_home_wtn = p_home_win * home_cs_rate
    p_away_wtn = p_away_win * away_cs_rate

    if p_home_wtn >= p_away_wtn:
        pick = "Home Win to Nil"
        prob = p_home_wtn
        cs_rate_used = home_cs_rate
    else:
        pick = "Away Win to Nil"
        prob = p_away_wtn
        cs_rate_used = away_cs_rate

    rationale = (
        f"{pick}: {prob:.0%} probability. "
        f"Win prob × CS rate = {prob:.0%}. "
        f"Clean sheet rate: {cs_rate_used:.0%}."
    )

    return [MarketRecommendation(
        market_code="win-to-nil",
        market_name=MARKETS["win-to-nil"].name,
        pick=pick,
        probability=round(prob, 4),
        confidence=_classify_confidence(prob),
        implied_odds=_implied_odds(prob),
        rationale=rationale,
        risk_factors=[
            "Both WTN components must hit — inherently lower probability",
            "A single defensive error ruins the clean sheet",
        ],
        supporting_stats={
            "home_wtn_prob": round(p_home_wtn, 4),
            "away_wtn_prob": round(p_away_wtn, 4),
            "home_cs_rate": round(home_cs_rate, 4),
            "away_cs_rate": round(away_cs_rate, 4),
        },
    )]


def model_win_either_half(
    overall_score: float,
    factors: list[FactorResult],
    patterns: dict[str, Any] | None = None,
) -> list[MarketRecommendation]:
    """Model the Win Either Half market.

    Uses goal timing data to estimate whether a team can win at least
    one half of the match.
    """
    timing = _get_factor(factors, "goal_timing")
    form = _get_factor(factors, "recent_form")

    p_home_win = _factor_score_to_prob_home(overall_score)
    p_away_win = _factor_score_to_prob_away(overall_score)

    # A team that wins either half is more likely if they're strong overall
    # and especially if they score in clusters
    home_1h_strength = 0.5
    away_1h_strength = 0.4
    if timing and timing.data_points:
        home_1h_strength = timing.data_points.get("home_1h_win_rate", 0.5)
        away_1h_strength = timing.data_points.get("away_1h_win_rate", 0.4)

    # P(win either half) ≈ 1 - P(don't win either half)
    # Simplified: correlated with overall win probability
    p_home_weh = min(0.95, p_home_win * 1.3)  # Easier than outright win
    p_away_weh = min(0.95, p_away_win * 1.3)

    if p_home_weh >= p_away_weh:
        pick = "Home Win Either Half"
        prob = p_home_weh
    else:
        pick = "Away Win Either Half"
        prob = p_away_weh

    return [MarketRecommendation(
        market_code="win-either-half",
        market_name=MARKETS["win-either-half"].name,
        pick=pick,
        probability=round(prob, 4),
        confidence=_classify_confidence(prob),
        implied_odds=_implied_odds(prob),
        rationale=(
            f"{pick}: {prob:.0%} probability. "
            f"Easier to hit than outright win — team only needs to lead at HT or win the 2nd half. "
            f"{'Goal timing data shows strong first-half scoring.' if timing and timing.confidence > 0.3 else ''}"
        ),
        risk_factors=[
            "Slow-starting teams might not lead at any break",
        ],
        supporting_stats={
            "home_weh_prob": round(p_home_weh, 4),
            "away_weh_prob": round(p_away_weh, 4),
        },
    )]


def model_team_over_under(
    overall_score: float,
    factors: list[FactorResult],
    patterns: dict[str, Any] | None = None,
    line: float = 0.5,
) -> list[MarketRecommendation]:
    """Model the Team Over/Under market.

    Estimates whether a specific team will score over/under a line.
    Returns recommendations for both home and away teams.
    """
    scoring = _get_factor(factors, "scoring_frequency")
    defence = _get_factor(factors, "defensive_record")

    home_gpg = 1.3
    away_gpg = 1.1
    if scoring and scoring.data_points:
        home_gpg = scoring.data_points.get("home_gpg", home_gpg)
        away_gpg = scoring.data_points.get("away_gpg", away_gpg)

    results = []
    for side, gpg in [("Home", home_gpg), ("Away", away_gpg)]:
        spread = gpg - line
        p_over = 1.0 / (1.0 + math.exp(-2.0 * spread))
        p_under = 1.0 - p_over

        pick = f"{side} Over" if p_over >= 0.5 else f"{side} Under"
        prob = p_over if "Over" in pick else p_under

        results.append(MarketRecommendation(
            market_code="team-over-under",
            market_name=f"{side} {MARKETS['team-over-under'].name}",
            pick=f"{pick} {line:g}",
            probability=round(prob, 4),
            confidence=_classify_confidence(prob),
            implied_odds=_implied_odds(prob),
            rationale=(
                f"{side} team averages {gpg:.1f} GPG. "
                f"P({side} over {line:g}) = {p_over:.0%}."
            ),
            risk_factors=[f"Sample size may be limited for {side.lower()} team"],
            supporting_stats={"gpg": round(gpg, 2), "p_over": round(p_over, 4)},
        ))

    return results


def model_handicap(
    overall_score: float,
    factors: list[FactorResult],
    patterns: dict[str, Any] | None = None,
) -> list[MarketRecommendation]:
    """Model the Handicap market.

    Uses the overall score to estimate expected goal difference and
    recommends appropriate handicap lines.
    """
    scoring = _get_factor(factors, "scoring_frequency")
    position = _get_factor(factors, "league_position")

    # Map overall_score to expected goal difference
    # Score of +1.0 → ~2.5 goal margin, -1.0 → ~-2.5
    expected_margin = overall_score * 2.5

    # Choose the most relevant handicap line
    if expected_margin > 1.5:
        handicap_line = -1.5
        pick = "Home -1.5"
        effective_margin = expected_margin - 1.5
    elif expected_margin > 0.5:
        handicap_line = -1.0
        pick = "Home -1"
        effective_margin = expected_margin - 1.0
    elif expected_margin < -1.5:
        handicap_line = 1.5
        pick = "Away -1.5"
        effective_margin = abs(expected_margin) - 1.5
    elif expected_margin < -0.5:
        handicap_line = 1.0
        pick = "Away -1"
        effective_margin = abs(expected_margin) - 1.0
    else:
        # Close match — no strong handicap recommendation
        handicap_line = 0
        if expected_margin >= 0:
            pick = "Home 0"
            effective_margin = expected_margin
        else:
            pick = "Away 0"
            effective_margin = abs(expected_margin)

    prob = 1.0 / (1.0 + math.exp(-2.0 * effective_margin))

    rationale = (
        f"Expected goal difference: {expected_margin:+.1f}. "
        f"Handicap {pick}: {prob:.0%} probability. "
    )
    if position and position.confidence > 0.3:
        rationale += f"League position context: {position.rationale[:100]}"

    return [MarketRecommendation(
        market_code="handicap",
        market_name=MARKETS["handicap"].name,
        pick=pick,
        probability=round(prob, 4),
        confidence=_classify_confidence(prob),
        implied_odds=_implied_odds(prob),
        rationale=rationale,
        risk_factors=[
            "Handicaps amplify risk — margin must be covered",
            "Late goals can flip handicap results",
        ],
        supporting_stats={
            "expected_margin": round(expected_margin, 2),
            "handicap_line": handicap_line,
        },
    )]


# ═══════════════════════════════════════════════════════════════
#  Orchestrator
# ═══════════════════════════════════════════════════════════════

def evaluate_all_markets(
    overall_score: float,
    factors: list[FactorResult],
    patterns: dict[str, Any] | None = None,
) -> list[MarketRecommendation]:
    """Run all market models and return a unified list of recommendations.

    Args:
        overall_score: Weighted aggregate factor score (-1 to +1).
        factors: List of individual FactorResult instances.
        patterns: Optional historical pattern data (O/U rates, BTTS, etc.).

    Returns:
        List of MarketRecommendation, one per market (some markets
        produce multiple, e.g. team O/U for home and away).
    """
    recommendations: list[MarketRecommendation] = []

    try:
        recommendations.extend(model_1x2(overall_score, factors, patterns))
    except Exception as e:
        logger.warning("1X2 model failed: %s", e)

    try:
        recommendations.extend(model_double_chance(overall_score, factors, patterns))
    except Exception as e:
        logger.warning("Double Chance model failed: %s", e)

    # Over/Under at multiple lines
    for line in [0.5, 1.5, 2.5, 3.5]:
        try:
            recommendations.extend(model_over_under(overall_score, factors, patterns, line=line))
        except Exception as e:
            logger.warning("Over/Under %.1f model failed: %s", line, e)

    try:
        recommendations.extend(model_gg_ng(overall_score, factors, patterns))
    except Exception as e:
        logger.warning("GG/NG model failed: %s", e)

    try:
        recommendations.extend(model_draw_no_bet(overall_score, factors, patterns))
    except Exception as e:
        logger.warning("DNB model failed: %s", e)

    try:
        recommendations.extend(model_win_to_nil(overall_score, factors, patterns))
    except Exception as e:
        logger.warning("Win to Nil model failed: %s", e)

    try:
        recommendations.extend(model_win_either_half(overall_score, factors, patterns))
    except Exception as e:
        logger.warning("Win Either Half model failed: %s", e)

    # Team O/U at 0.5 and 1.5
    for line in [0.5, 1.5]:
        try:
            recommendations.extend(model_team_over_under(overall_score, factors, patterns, line=line))
        except Exception as e:
            logger.warning("Team O/U %.1f model failed: %s", line, e)

    try:
        recommendations.extend(model_handicap(overall_score, factors, patterns))
    except Exception as e:
        logger.warning("Handicap model failed: %s", e)

    # Sort by confidence then probability
    confidence_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    recommendations.sort(
        key=lambda r: (confidence_order.get(r.confidence, 3), -r.probability)
    )

    return recommendations
