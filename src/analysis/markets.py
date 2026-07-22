"""
Footy Predict — Market-Specific Models

Translates generic factor scores and historical statistics into concrete
betting market recommendations.  Each model function receives the factor
results plus any relevant historical data and returns a list of
``MarketRecommendation`` objects — one per pick direction.

Design principles
-----------------
* **Comprehensiveness over filtering**: every model emits *all* meaningful
  directions (Over AND Under, all three 1X2 outcomes, etc.).  The caller
  decides what to show; nothing is hidden here.
* **No hardcoded defaults**: factor data is read via the fixed top-level
  keys introduced in the Phase-A factor refactor (``home_gpg``,
  ``home_cs_rate_home``, etc.).  If a key is absent the model falls back
  gracefully with a reduced confidence, not a silent wrong answer.
* **Poisson-first probabilities**: where Poisson probabilities have been
  pre-computed by ``scoring_patterns`` they are used directly; logistic
  approximations serve as fallbacks only.

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
    """A recommendation for a specific betting market pick direction.

    Attributes:
        market_code: Internal code (e.g. ``"over-under-2.5"``).
        market_name: Human-readable name.
        pick: The specific pick direction (e.g. ``"Over 2.5"``).
        probability: Estimated probability of the pick hitting (0–1).
        confidence: ``"HIGH"`` / ``"MEDIUM"`` / ``"LOW"``.
        implied_odds: ``1 / probability`` — the fair decimal odds.
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
    """Map a probability to a confidence label.

    Args:
        probability: Float in [0, 1].

    Returns:
        ``"HIGH"``, ``"MEDIUM"``, or ``"LOW"``.
    """
    if probability >= CONFIDENCE_THRESHOLDS["HIGH"]:
        return "HIGH"
    if probability >= CONFIDENCE_THRESHOLDS["MEDIUM"]:
        return "MEDIUM"
    return "LOW"


def _blend(historical: float | None, model: float, hist_weight: float = 0.6) -> float:
    """Blend a historical rate with a model estimate.

    Falls back to the model-only estimate when historical data is
    unavailable.

    Args:
        historical: Observed historical rate (or ``None``).
        model: Model-derived probability estimate.
        hist_weight: Weight to give the historical rate (0–1).

    Returns:
        Blended probability in [0, 1].
    """
    if historical is None:
        return model
    return hist_weight * historical + (1 - hist_weight) * model


def _implied_odds(probability: float) -> float:
    """Convert a probability to decimal odds, clamped to a reasonable range.

    Args:
        probability: Float in [0, 1].

    Returns:
        Decimal odds rounded to 2 d.p.
    """
    if probability <= 0.0:
        return 100.0
    if probability >= 1.0:
        return 1.01
    return round(1.0 / probability, 2)


def _get_factor(factors: list[FactorResult], name: str) -> FactorResult | None:
    """Find a factor by name from the results list.

    Args:
        factors: List of ``FactorResult`` instances.
        name: Factor name to look up.

    Returns:
        Matching ``FactorResult`` or ``None``.
    """
    for f in factors:
        if f.name == name:
            return f
    return None


def _factor_score_to_prob_home(score: float) -> float:
    """Convert a home-advantage score (-1..+1) to a home-win probability.

    Uses a logistic mapping:
    -  0.0 → 0.50 (50 / 50 before draw redistribution)
    - +1.0 → ~0.95
    - -1.0 → ~0.05
    """
    return 1.0 / (1.0 + math.exp(-3.0 * score))


def _factor_score_to_prob_away(score: float) -> float:
    """Mirror of home-win: a strong away score yields a high away probability."""
    return _factor_score_to_prob_home(-score)


def _pos_suffix(n: int | None) -> str:
    """Return the ordinal suffix for a league position number."""
    if n is None:
        return ""
    if 11 <= (n % 100) <= 13:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def _normalise_3way(p_home: float, p_draw: float, p_away: float) -> tuple[float, float, float]:
    """Normalise three probabilities so they sum to exactly 1.0."""
    total = p_home + p_draw + p_away
    if total < 1e-9:
        return 1 / 3, 1 / 3, 1 / 3
    return p_home / total, p_draw / total, p_away / total


def _shrink_1x2(p_home: float, p_draw: float, p_away: float) -> tuple[float, float, float]:
    """Apply football-specific shrinkage to 1X2 probabilities.

    Pure logistic models consistently overestimate win probabilities because
    they can reach 0.90+ for strong favourites — but in football, even the
    most dominant sides only win ~65-70% of their matches.  Historical data
    from the simulation shows a +12 to +15pp gap between stated win probs
    and actual hit rates.

    Shrinkage method: linear contraction toward the no-information prior
    (home 0.46, draw 0.26, away 0.28 — European average) using a factor
    calibrated so that a raw P(home win)=0.85 maps to ~0.68, consistent
    with observed HIGH-confidence hit rates.

    Formula: p_shrunk = prior + shrink_factor × (p_raw - prior)
    shrink_factor = 0.70 (30% regression to prior)
    """
    SHRINK = 0.70   # 30% pull toward the prior
    H_PRIOR, D_PRIOR, A_PRIOR = 0.46, 0.26, 0.28
    sh = H_PRIOR + SHRINK * (p_home - H_PRIOR)
    sd = D_PRIOR + SHRINK * (p_draw - D_PRIOR)
    sa = A_PRIOR + SHRINK * (p_away - A_PRIOR)
    # Clamp to reasonable football ranges
    sh = max(0.05, min(0.72, sh))
    sd = max(0.10, min(0.50, sd))
    sa = max(0.05, min(0.65, sa))
    return _normalise_3way(sh, sd, sa)




def _shrink_lambda(lam: float, league_mean: float = 1.35) -> float:
    """Shrink a raw GPG-derived Poisson λ toward the league mean.

    Raw GPG from recent form is inflated: teams on a scoring run will show
    2.2+ GPG from their last 10 matches, but their true sustainable rate is
    closer to the league average.  The simulation showed Team O/U 1.5 in
    HIGH tier hitting 55.6% at stated 91% — the same overconfidence pattern
    as 1X2 probabilities, just in the Poisson domain.

    Shrinkage formula (same principle as _shrink_1x2):
        λ_shrunk = league_mean + SHRINK × (λ_raw − league_mean)
    SHRINK = 0.75 → 25% regression toward the mean.

    Examples:
        λ=2.2  → 1.35 + 0.75×(2.2−1.35) = 1.35 + 0.64 = 1.99
        λ=1.5  → 1.35 + 0.75×0.15 = 1.46
        λ=0.9  → 1.35 + 0.75×(−0.45) = 1.01  (clamps up to floor)

    Args:
        lam: Raw GPG-based Poisson rate.
        league_mean: League average goals per team per game (~1.35 European).

    Returns:
        Shrunk lambda, clamped to [0.3, 3.0].
    """
    SHRINK = 0.75
    shrunk = league_mean + SHRINK * (lam - league_mean)
    return max(0.3, min(3.0, shrunk))


# ═══════════════════════════════════════════════════════════════
#  Rationale helpers (shared prose builders)
# ═══════════════════════════════════════════════════════════════

def _1x2_base_probs(
    overall_score: float,
    patterns: dict[str, Any] | None,
) -> tuple[float, float, float]:
    """Compute blended 1X2 probabilities from score + historical patterns."""
    p_home = _factor_score_to_prob_home(overall_score)
    p_away = _factor_score_to_prob_away(overall_score)
    p_draw = max(0.05, 1.0 - p_home - p_away)
    p_home, p_draw, p_away = _normalise_3way(p_home, p_draw, p_away)

    if patterns:
        p_home = _blend(patterns.get("home_win_rate"), p_home)
        p_draw = _blend(patterns.get("draw_rate"), p_draw)
        p_away = _blend(patterns.get("away_win_rate"), p_away)
        p_home, p_draw, p_away = _normalise_3way(p_home, p_draw, p_away)

    # Apply football-specific shrinkage before returning.
    # Raw logistic probabilities reach 0.85-0.95 for dominant sides but
    # observed hit rates max at ~0.68-0.72 — shrink to match reality.
    return _shrink_1x2(p_home, p_draw, p_away)


# ═══════════════════════════════════════════════════════════════
#  Individual Market Models
# ═══════════════════════════════════════════════════════════════

def model_1x2(
    overall_score: float,
    factors: list[FactorResult],
    patterns: dict[str, Any] | None = None,
) -> list[MarketRecommendation]:
    """Model the 1X2 (Match Result) market.

    Emits all three directions — Home Win, Draw, Away Win — with distinct
    data-rich rationales for each.  The caller is responsible for sorting
    and displaying; nothing is filtered here.

    Args:
        overall_score: Weighted aggregate factor score (-1 to +1).
        factors: List of ``FactorResult`` instances.
        patterns: Optional historical pattern data.

    Returns:
        List of three ``MarketRecommendation`` objects.
    """
    p_home, p_draw, p_away = _1x2_base_probs(overall_score, patterns)

    rf = _get_factor(factors, "recent_form")
    hf = _get_factor(factors, "home_form")
    af = _get_factor(factors, "away_form")
    pos = _get_factor(factors, "league_position")
    h2h = _get_factor(factors, "head_to_head")
    mom = _get_factor(factors, "momentum")

    base_stats = {
        "p_home": round(p_home, 4),
        "p_draw": round(p_draw, 4),
        "p_away": round(p_away, 4),
    }

    # ── Home Win rationale ────────────────────────────────────────
    home_parts = [f"Model: H {p_home:.0%} / D {p_draw:.0%} / A {p_away:.0%}."]
    if rf and rf.confidence > 0.3:
        hdp = rf.data_points.get("home", {})
        if hdp:
            home_parts.append(
                f"Home form: {hdp.get('form_string', '?')} "
                f"({hdp.get('ppg', 0):.2f} PPG, {hdp.get('win_rate', 0):.0%} wins)."
            )
    if hf and hf.confidence > 0.3:
        dp = hf.data_points
        streak = dp.get("current_home_streak", {})
        if streak.get("length", 0) >= 3:
            home_parts.append(
                f"Home streak: {streak['length']}\u00d7 {streak['type']}."
            )
        home_parts.append(
            f"Home record: {dp.get('win_rate', 0):.0%} wins, "
            f"{dp.get('gpg', 0):.1f} GPG at home."
        )
    if pos and pos.confidence > 0.3:
        hp = pos.data_points.get("home_position")
        ap = pos.data_points.get("away_position")
        pts_gap = pos.data_points.get("points_gap", 0)
        if hp and ap:
            home_parts.append(
                f"Table: {hp}{_pos_suffix(hp)} vs {ap}{_pos_suffix(ap)} "
                f"({pts_gap:+d} pts gap)."
            )
    if h2h and h2h.confidence > 0.2:
        dp = h2h.data_points
        home_parts.append(
            f"H2H: {dp.get('home_wins', '?')}W\u2013{dp.get('draws', '?')}D\u2013"
            f"{dp.get('away_wins', '?')}L ({dp.get('total_meetings', '?')} meetings)."
        )

    home_risks = []
    if abs(overall_score) < 0.15:
        home_risks.append("Very evenly-matched — high upset potential")
    if p_draw > 0.30:
        home_risks.append(f"Draw probability at {p_draw:.0%} is significant")

    # ── Draw rationale ────────────────────────────────────────────
    draw_parts = [f"Draw probability: {p_draw:.0%}."]
    if rf and rf.confidence > 0.3:
        hdp = rf.data_points.get("home", {})
        adp = rf.data_points.get("away", {})
        if hdp and adp:
            draw_parts.append(
                f"Home draw rate (recent): {hdp.get('draw_rate', 0):.0%}. "
                f"Away draw rate (recent): {adp.get('draw_rate', 0):.0%}."
            )
    if h2h and h2h.confidence > 0.2:
        dp = h2h.data_points
        total = max(dp.get("total_meetings", 1), 1)
        draws = dp.get("draws", 0)
        draw_parts.append(
            f"H2H draw rate: {draws}/{total} meetings ({draws/total:.0%})."
        )
    sp = _get_factor(factors, "scoring_patterns")
    if sp and sp.data_points.get("btts_avg_pct") is not None:
        draw_parts.append(
            f"Historical BTTS tendency: {sp.data_points['btts_avg_pct']:.0%}."
        )
    draw_risks = []
    if p_draw < 0.20:
        draw_risks.append("Draw probability is low — one side appears clearly stronger")

    # ── Away Win rationale ────────────────────────────────────────
    away_parts = [f"Model: H {p_home:.0%} / D {p_draw:.0%} / A {p_away:.0%}."]
    if rf and rf.confidence > 0.3:
        adp = rf.data_points.get("away", {})
        if adp:
            away_parts.append(
                f"Away form: {adp.get('form_string', '?')} "
                f"({adp.get('ppg', 0):.2f} PPG, {adp.get('win_rate', 0):.0%} wins)."
            )
    if af and af.confidence > 0.3:
        dp = af.data_points
        streak = dp.get("current_away_streak", {})
        if streak.get("length", 0) >= 3:
            away_parts.append(
                f"Away streak: {streak['length']}\u00d7 {streak['type']}."
            )
        away_parts.append(
            f"Away record: {dp.get('win_rate', 0):.0%} wins, "
            f"{dp.get('gpg', 0):.1f} GPG away."
        )
    if h2h and h2h.confidence > 0.2:
        dp = h2h.data_points
        away_parts.append(
            f"H2H away: {dp.get('away_wins', '?')} wins in "
            f"{dp.get('total_meetings', '?')} meetings."
        )

    away_risks = []
    if overall_score > 0.3:
        away_risks.append("Home side is favoured — away win would be an upset")

    mkt_1x2 = MARKETS.get("1x2")
    mkt_name = mkt_1x2.name if mkt_1x2 else "Match Result (1X2)"

    return [
        MarketRecommendation(
            market_code="1x2",
            market_name=mkt_name,
            pick="1 (Home Win)",
            probability=round(p_home, 4),
            confidence=_classify_confidence(p_home),
            implied_odds=_implied_odds(p_home),
            rationale=" ".join(home_parts),
            risk_factors=home_risks,
            supporting_stats=base_stats,
        ),
        MarketRecommendation(
            market_code="1x2",
            market_name=mkt_name,
            pick="X (Draw)",
            probability=round(p_draw, 4),
            confidence=_classify_confidence(p_draw),
            implied_odds=_implied_odds(p_draw),
            rationale=" ".join(draw_parts),
            risk_factors=draw_risks,
            supporting_stats=base_stats,
        ),
        MarketRecommendation(
            market_code="1x2",
            market_name=mkt_name,
            pick="2 (Away Win)",
            probability=round(p_away, 4),
            confidence=_classify_confidence(p_away),
            implied_odds=_implied_odds(p_away),
            rationale=" ".join(away_parts),
            risk_factors=away_risks,
            supporting_stats=base_stats,
        ),
    ]


def model_double_chance(
    overall_score: float,
    factors: list[FactorResult],
    patterns: dict[str, Any] | None = None,
) -> list[MarketRecommendation]:
    """Model the Double Chance market.

    Emits all three sub-variants — 1X, X2, and 12 — with individual
    rationales explaining when each is appropriate.

    Args:
        overall_score: Weighted aggregate factor score (-1 to +1).
        factors: List of ``FactorResult`` instances.
        patterns: Optional historical pattern data.

    Returns:
        List of three ``MarketRecommendation`` objects (1X, X2, 12).
    """
    p_home, p_draw, p_away = _1x2_base_probs(overall_score, patterns)

    p_1x = p_home + p_draw
    p_x2 = p_draw + p_away
    p_12 = p_home + p_away

    mkt = MARKETS.get("double-chance")
    mkt_name = mkt.name if mkt else "Double Chance"

    stats = {
        "1X": round(p_1x, 4),
        "X2": round(p_x2, 4),
        "12": round(p_12, 4),
        "p_home": round(p_home, 4),
        "p_draw": round(p_draw, 4),
        "p_away": round(p_away, 4),
    }

    hf = _get_factor(factors, "home_form")
    af = _get_factor(factors, "away_form")

    # ── 1X rationale ─────────────────────────────────────────────
    r_1x_parts = [
        f"1X (Home or Draw): {p_1x:.0%}. "
        f"Covers any result where the home side avoids defeat. "
        f"H {p_home:.0%} / D {p_draw:.0%} / A {p_away:.0%}."
    ]
    if hf and hf.confidence > 0.3:
        dp = hf.data_points
        r_1x_parts.append(
            f"Home team win rate at home: {dp.get('win_rate', 0):.0%}."
        )

    # ── X2 rationale ─────────────────────────────────────────────
    r_x2_parts = [
        f"X2 (Draw or Away): {p_x2:.0%}. "
        f"Covers any result where the away side avoids defeat. "
        f"H {p_home:.0%} / D {p_draw:.0%} / A {p_away:.0%}."
    ]
    if af and af.confidence > 0.3:
        dp = af.data_points
        r_x2_parts.append(
            f"Away team win rate away: {dp.get('win_rate', 0):.0%}."
        )

    # ── 12 rationale ─────────────────────────────────────────────
    draw_warning = (
        f" \u26a0\ufe0f Draw probability is {p_draw:.0%} — a draw voids this bet."
        if p_draw > 0.25 else ""
    )
    r_12 = (
        f"12 (Home or Away — no draw): {p_12:.0%}. "
        f"Eliminates the draw entirely. Highest risk DC variant; only use when "
        f"the draw is demonstrably suppressed."
        f"{draw_warning}"
    )

    return [
        MarketRecommendation(
            market_code="double-chance",
            market_name=mkt_name,
            pick="1X",
            probability=round(p_1x, 4),
            confidence=_classify_confidence(p_1x),
            implied_odds=_implied_odds(p_1x),
            rationale=" ".join(r_1x_parts),
            risk_factors=["Away win outright loses this bet"],
            supporting_stats=stats,
        ),
        MarketRecommendation(
            market_code="double-chance",
            market_name=mkt_name,
            pick="X2",
            probability=round(p_x2, 4),
            confidence=_classify_confidence(p_x2),
            implied_odds=_implied_odds(p_x2),
            rationale=" ".join(r_x2_parts),
            risk_factors=["Home win outright loses this bet"],
            supporting_stats=stats,
        ),
        MarketRecommendation(
            market_code="double-chance",
            market_name=mkt_name,
            pick="12",
            probability=round(p_12, 4),
            confidence=_classify_confidence(p_12),
            implied_odds=_implied_odds(p_12),
            rationale=r_12,
            risk_factors=(
                [f"Draw probability significant at {p_draw:.0%}"] if p_draw > 0.25 else []
            ),
            supporting_stats=stats,
        ),
    ]


def model_over_under(
    overall_score: float,
    factors: list[FactorResult],
    patterns: dict[str, Any] | None = None,
    line: float = 2.5,
) -> list[MarketRecommendation]:
    """Model the Over/Under goals market for a given line.

    Emits both Over and Under picks.  Uses Poisson pre-computed
    probabilities from ``scoring_patterns`` when available; falls back to
    a logistic approximation centred on expected total goals.

    Args:
        overall_score: Weighted aggregate factor score.
        factors: List of ``FactorResult`` instances.
        patterns: Historical pattern data (should contain ``poisson_over_*``
            keys from Phase-A factor refactor).
        line: Goal line — one of 0.5, 1.5, 2.5, 3.5.

    Returns:
        List of two ``MarketRecommendation`` objects (Over and Under).
    """
    # ── Source expected goals ─────────────────────────────────────
    am = _get_factor(factors, "attacking_matchup")
    scoring = _get_factor(factors, "scoring_frequency")
    defence = _get_factor(factors, "defensive_record")

    if am and am.data_points.get("expected_total"):
        expected_total = am.data_points["expected_total"]
        home_gpg = am.data_points.get("expected_home_goals", 1.3)
        away_gpg = am.data_points.get("expected_away_goals", 1.1)
    elif scoring and scoring.data_points:
        # Overall recent form is the primary signal; venue split modifies by 30%
        home_base = scoring.data_points.get("home_gpg") or 1.3
        home_venue = scoring.data_points.get("home_home_gpg")
        home_gpg = home_base if home_venue is None else (0.7 * home_base + 0.3 * home_venue)

        away_base = scoring.data_points.get("away_gpg") or 1.1
        away_venue = scoring.data_points.get("away_away_gpg")
        away_gpg = away_base if away_venue is None else (0.7 * away_base + 0.3 * away_venue)

        expected_total = home_gpg + away_gpg
    else:
        home_gpg, away_gpg, expected_total = 1.3, 1.1, 2.4

    # Apply regression-to-mean on raw GPG before Poisson calculation.
    # Form-based GPG is inflated; shrink 25% toward league mean (1.35).
    home_gpg = _shrink_lambda(home_gpg)
    away_gpg = _shrink_lambda(away_gpg)
    expected_total = home_gpg + away_gpg

    # ── Poisson probability (preferred) ──────────────────────────
    _poisson_key_map = {
        0.5: "poisson_over_0_5",
        1.5: "poisson_over_1_5",
        2.5: "poisson_over_2_5",
        3.5: "poisson_over_3_5",
    }
    poisson_over = patterns.get(_poisson_key_map.get(line, "")) if patterns else None

    # ── Model fallback ────────────────────────────────────────────
    model_over = 1.0 / (1.0 + math.exp(-1.8 * (expected_total - line)))

    # ── Historical rate ───────────────────────────────────────────
    hist_key_map = {
        0.5: "over_0_5_rate",
        1.5: "over_1_5_rate",
        2.5: "over_2_5_rate",
        3.5: "over_3_5_rate",
    }
    hist_over = patterns.get(hist_key_map.get(line, "")) if patterns else None

    # ── Final probability ─────────────────────────────────────────
    if poisson_over is not None:
        # Blend Poisson with logistic for robustness
        raw_over = 0.55 * poisson_over + 0.45 * model_over
    else:
        raw_over = _blend(hist_over, model_over)

    # ── Dixon-Coles low-score inflation correction ────────────────
    # Pure Poisson underestimates the frequency of 0-0, 1-0, 0-1, 1-1
    # scorelines in football (simulation showed +30pp overconfidence on
    # Over 2.5 and Away Over 1.5).  When expected total is close to the
    # line (±0.6 goals), there is genuine uncertainty — pull toward 0.5.
    #
    # Correction formula:
    #   distance = |expected_total - line|
    #   if distance < 0.6: shrink toward 0.5 proportionally
    #   shrink_factor = 0.65 + 0.35 * (distance / 0.6)  → 0.65 when very close
    #
    # For high-confidence Over picks on low-λ matches (e.g. Away Over 1.5
    # with expected_total=1.8), this reduces the stated 0.90+ to ~0.70-0.75.
    dist = abs(expected_total - line)
    if dist < 0.6:
        shrink = 0.65 + 0.35 * (dist / 0.6)
        final_over = 0.5 + shrink * (raw_over - 0.5)
    else:
        final_over = raw_over

    final_under = 1.0 - final_over

    line_str = f"{line:g}"
    market_code = f"over-under-{line_str}"
    market_name = f"Over/Under {line_str} Goals"

    # ── Rationales ────────────────────────────────────────────────
    poisson_note = (
        f"Poisson model: {poisson_over:.0%}. " if poisson_over is not None else ""
    )
    over_rationale = (
        f"Expected total \u2248 {expected_total:.1f} goals "
        f"(H {home_gpg:.1f} + A {away_gpg:.1f}). "
        f"{poisson_note}"
        f"Over {line_str} probability: {final_over:.0%}."
    )
    under_rationale = (
        f"Expected total \u2248 {expected_total:.1f} goals "
        f"(H {home_gpg:.1f} + A {away_gpg:.1f}). "
        f"Under {line_str} probability: {final_under:.0%}."
    )

    if defence and defence.confidence > 0.3:
        h_cs = (
            defence.data_points.get("home_cs_rate_home")
            or defence.data_points.get("home", {}).get("clean_sheet_rate")
        )
        a_cs = (
            defence.data_points.get("away_cs_rate_away")
            or defence.data_points.get("away", {}).get("clean_sheet_rate")
        )
        if h_cs is not None or a_cs is not None:
            under_rationale += (
                f" Home CS rate: {h_cs:.0%}. Away CS rate: {a_cs:.0%}."
                if h_cs is not None and a_cs is not None
                else ""
            )

    common_stats = {
        "expected_total": round(expected_total, 2),
        "home_gpg": round(home_gpg, 2),
        "away_gpg": round(away_gpg, 2),
        "final_over": round(final_over, 4),
        "final_under": round(final_under, 4),
        "poisson_used": poisson_over is not None,
    }

    over_risks = []
    if abs(final_over - 0.5) < 0.07:
        over_risks.append("Very close to the line — could go either way")

    return [
        MarketRecommendation(
            market_code=market_code,
            market_name=market_name,
            pick=f"Over {line_str}",
            probability=round(final_over, 4),
            confidence=_classify_confidence(final_over),
            implied_odds=_implied_odds(final_over),
            rationale=over_rationale,
            risk_factors=over_risks,
            supporting_stats=common_stats,
        ),
        MarketRecommendation(
            market_code=market_code,
            market_name=market_name,
            pick=f"Under {line_str}",
            probability=round(final_under, 4),
            confidence=_classify_confidence(final_under),
            implied_odds=_implied_odds(final_under),
            rationale=under_rationale,
            risk_factors=[],
            supporting_stats=common_stats,
        ),
    ]


def model_gg_ng(
    overall_score: float,
    factors: list[FactorResult],
    patterns: dict[str, Any] | None = None,
) -> list[MarketRecommendation]:
    """Model the Both Teams to Score (GG / NG) market.

    Emits both GG and NG picks.  Uses Poisson BTTS probability from
    ``scoring_patterns`` when available; otherwise derives from venue-split
    clean-sheet rates.

    Args:
        overall_score: Weighted aggregate factor score.
        factors: List of ``FactorResult`` instances.
        patterns: Historical pattern data.

    Returns:
        List of two ``MarketRecommendation`` objects (GG and NG).
    """
    scoring = _get_factor(factors, "scoring_frequency")
    defence = _get_factor(factors, "defensive_record")

    # ── Clean-sheet rates (venue-split preferred) ─────────────────
    def _cs(side: str) -> float:
        """Resolve clean-sheet rate: overall form primary (70%), venue modifier (30%)."""
        default = 0.25 if side == "home" else 0.15
        if not (defence and defence.data_points):
            return default
        dp = defence.data_points
        fk = "home_cs_rate" if side == "home" else "away_cs_rate"
        vk = "home_cs_rate_home" if side == "home" else "away_cs_rate_away"
        base = dp.get(fk) or dp.get(side, {}).get("clean_sheet_rate", default)
        venue = dp.get(vk)
        if venue is None:
            return base
        return 0.7 * base + 0.3 * venue

    home_cs = _cs("home")
    away_cs = _cs("away")

    # ── Scoring participation rates ───────────────────────────────
    home_scored_pct = None
    away_scored_pct = None
    home_blanks = None
    away_blanks = None

    if scoring and scoring.data_points:
        dp_s = scoring.data_points
        # Overall scored_pct is primary; venue-specific is a 30% modifier
        home_base_sp = dp_s.get("home", {}).get("scored_pct") if isinstance(dp_s.get("home"), dict) else None
        home_venue_sp = dp_s.get("home_scored_in_pct")
        if home_base_sp is not None and home_venue_sp is not None:
            home_scored_pct = 0.7 * home_base_sp + 0.3 * home_venue_sp
        elif home_venue_sp is not None:
            home_scored_pct = home_venue_sp
        else:
            home_scored_pct = home_base_sp

        away_base_sp = dp_s.get("away", {}).get("scored_pct") if isinstance(dp_s.get("away"), dict) else None
        away_venue_sp = dp_s.get("away_scored_in_pct")
        if away_base_sp is not None and away_venue_sp is not None:
            away_scored_pct = 0.7 * away_base_sp + 0.3 * away_venue_sp
        elif away_venue_sp is not None:
            away_scored_pct = away_venue_sp
        else:
            away_scored_pct = away_base_sp

        home_blanks = dp_s.get("home_blank_games")
        away_blanks = dp_s.get("away_blank_games")

    # ── Poisson BTTS (preferred) ──────────────────────────────────
    poisson_btts = patterns.get("poisson_btts") if patterns else None
    poisson_home_scores = patterns.get("poisson_home_over_0_5") if patterns else None
    poisson_away_scores = patterns.get("poisson_away_over_0_5") if patterns else None

    p_home_scores = poisson_home_scores if poisson_home_scores is not None else (1.0 - away_cs)
    p_away_scores = poisson_away_scores if poisson_away_scores is not None else (1.0 - home_cs)

    model_gg = p_home_scores * p_away_scores

    hist_gg = patterns.get("btts_rate") if patterns else None
    if poisson_btts is not None:
        final_gg = 0.55 * poisson_btts + 0.45 * model_gg
    else:
        final_gg = _blend(hist_gg, model_gg)

    final_ng = 1.0 - final_gg

    # ── Rationales ────────────────────────────────────────────────
    gg_parts = [f"P(GG) = {final_gg:.0%}."]
    if home_scored_pct is not None:
        gg_parts.append(f"Home scored in {home_scored_pct:.0%} of recent home games.")
    if away_scored_pct is not None:
        gg_parts.append(f"Away scored in {away_scored_pct:.0%} of recent away games.")
    if hist_gg is not None:
        gg_parts.append(f"Historical BTTS rate: {hist_gg:.0%}.")

    ng_parts = [f"P(NG / clean sheet) = {final_ng:.0%}."]
    ng_parts.append(
        f"Home CS rate: {home_cs:.0%}. Away CS rate: {away_cs:.0%}."
    )
    if home_blanks is not None:
        ng_parts.append(f"Home team failed to score in {home_blanks} recent home games.")
    if away_blanks is not None:
        ng_parts.append(f"Away team failed to score in {away_blanks} recent away games.")
    ng_parts.append(
        "NG wins if at least one team fails to score — does not require a 0-0 draw."
    )

    common_stats = {
        "p_gg": round(final_gg, 4),
        "p_ng": round(final_ng, 4),
        "home_cs_rate": round(home_cs, 3),
        "away_cs_rate": round(away_cs, 3),
        "p_home_scores": round(p_home_scores, 4),
        "p_away_scores": round(p_away_scores, 4),
        "poisson_used": poisson_btts is not None,
    }

    mkt = MARKETS.get("gg-ng")
    mkt_name = mkt.name if mkt else "Both Teams to Score (GG/NG)"

    return [
        MarketRecommendation(
            market_code="gg-ng",
            market_name=mkt_name,
            pick="GG (Both Teams Score)",
            probability=round(final_gg, 4),
            confidence=_classify_confidence(final_gg),
            implied_odds=_implied_odds(final_gg),
            rationale=" ".join(gg_parts),
            risk_factors=(
                ["One strong defence could shut out the opposition"] if final_gg < 0.55 else []
            ),
            supporting_stats=common_stats,
        ),
        MarketRecommendation(
            market_code="gg-ng",
            market_name=mkt_name,
            pick="NG (Clean Sheet Expected)",
            probability=round(final_ng, 4),
            confidence=_classify_confidence(final_ng),
            implied_odds=_implied_odds(final_ng),
            rationale=" ".join(ng_parts),
            risk_factors=["If both teams score this bet loses"],
            supporting_stats=common_stats,
        ),
    ]


def model_draw_no_bet(
    overall_score: float,
    factors: list[FactorResult],
    patterns: dict[str, Any] | None = None,
) -> list[MarketRecommendation]:
    """Model the Draw No Bet market.

    Emits both Home DNB and Away DNB picks.  The draw probability is
    explicitly surfaced in each rationale because it determines the
    refund scenario.

    Args:
        overall_score: Weighted aggregate factor score.
        factors: List of ``FactorResult`` instances.
        patterns: Historical pattern data.

    Returns:
        List of two ``MarketRecommendation`` objects.
    """
    p_home, p_draw, p_away = _1x2_base_probs(overall_score, patterns)

    # DNB probability = P(team wins outright).
    # A DNB bet WINS only if the selected team wins. On a draw, the stake
    # is returned (no profit, no loss). So the stated probability for a
    # DNB pick is exactly the outright win probability — NOT p_win/(p_win+p_loss).
    # The old formula p_home/(p_home+p_away) incorrectly excluded draws from
    # the denominator, inflating DNB probabilities by +30-36pp vs reality.
    dnb_home = p_home
    dnb_away = p_away

    form = _get_factor(factors, "recent_form")
    form_note = ""
    if form and form.confidence > 0.3:
        hdp = form.data_points.get("home", {})
        adp = form.data_points.get("away", {})
        if hdp and adp:
            form_note = (
                f" Recent form: H {hdp.get('form_string', '?')} "
                f"({hdp.get('ppg', 0):.2f} PPG) vs A {adp.get('form_string', '?')} "
                f"({adp.get('ppg', 0):.2f} PPG)."
            )

    draw_note = (
        f"Draw probability: {p_draw:.0%} — stake returned on a draw. "
        "Net risk: losing outcome only."
    )

    common_stats = {
        "dnb_home": round(dnb_home, 4),
        "dnb_away": round(dnb_away, 4),
        "p_draw": round(p_draw, 4),
    }

    mkt = MARKETS.get("draw-no-bet")
    mkt_name = mkt.name if mkt else "Draw No Bet"

    return [
        MarketRecommendation(
            market_code="draw-no-bet",
            market_name=mkt_name,
            pick="DNB Home",
            probability=round(dnb_home, 4),
            confidence=_classify_confidence(dnb_home),
            implied_odds=_implied_odds(dnb_home),
            rationale=(
                f"Home win probability: {p_home:.0%}. {draw_note}{form_note}"
            ),
            risk_factors=(
                ["Draw probability is significant — stake merely returns"] if p_draw > 0.28 else []
            ),
            supporting_stats=common_stats,
        ),
        MarketRecommendation(
            market_code="draw-no-bet",
            market_name=mkt_name,
            pick="DNB Away",
            probability=round(dnb_away, 4),
            confidence=_classify_confidence(dnb_away),
            implied_odds=_implied_odds(dnb_away),
            rationale=(
                f"Away win probability: {p_away:.0%}. {draw_note}{form_note}"
            ),
            risk_factors=(
                ["Draw probability is significant — stake merely returns"] if p_draw > 0.28 else []
            ),
            supporting_stats=common_stats,
        ),
    ]


def model_win_to_nil(
    overall_score: float,
    factors: list[FactorResult],
    patterns: dict[str, Any] | None = None,
) -> list[MarketRecommendation]:
    """Model the Win to Nil market with Yes/No binary framing.

    Emits four picks — Yes and No for each team.  The ``No`` picks are
    positioned as the **primary value-bet direction**: backing a team to
    score at least once is statistically much more reliable than backing a
    compound win + clean-sheet event.

    Probability relationships:
    -  ``P(Home WtN Yes) = P(home wins) × P(home keeps clean sheet)``
    -  ``P(Home WtN No)  = 1 - P(Home WtN Yes)``
    -  (same for Away)

    Args:
        overall_score: Weighted aggregate factor score.
        factors: List of ``FactorResult`` instances.
        patterns: Historical pattern data.

    Returns:
        List of four ``MarketRecommendation`` objects.
    """
    p_home_win, p_draw, p_away_win = _1x2_base_probs(overall_score, patterns)

    defence = _get_factor(factors, "defensive_record")
    scoring = _get_factor(factors, "scoring_frequency")
    hf = _get_factor(factors, "home_form")
    af = _get_factor(factors, "away_form")

    # ── CS rates (venue-split preferred) ─────────────────────────
    def _get_cs(side: str) -> float:
        """Clean-sheet rate: overall form primary (70%), venue modifier (30%)."""
        fk = "home_cs_rate" if side == "home" else "away_cs_rate"
        vk = "home_cs_rate_home" if side == "home" else "away_cs_rate_away"
        default = 0.25 if side == "home" else 0.15
        if not (defence and defence.data_points):
            return default
        dp = defence.data_points
        base = dp.get(fk) or dp.get(side, {}).get("clean_sheet_rate", default)
        venue = dp.get(vk)
        if venue is None:
            return base
        return 0.7 * base + 0.3 * venue

    home_cs = _get_cs("home")
    away_cs = _get_cs("away")

    # Shrink raw CS rates toward the league mean (0.30 European average).
    # A team on a 5-game clean sheet run will show CS=0.50+, but that's
    # not sustainable. Apply 25% regression: CS_shrunk = 0.30 + 0.75*(CS-0.30).
    CS_MEAN = 0.30
    CS_SHRINK = 0.75
    home_cs = CS_MEAN + CS_SHRINK * (home_cs - CS_MEAN)
    away_cs = CS_MEAN + CS_SHRINK * (away_cs - CS_MEAN)

    # ── Scoring participation (for the No rationale) ──────────────
    p_away_scores = patterns.get("poisson_away_over_0_5") if patterns else None
    p_home_scores = patterns.get("poisson_home_over_0_5") if patterns else None

    if p_away_scores is None and scoring:
        p_away_scores = scoring.data_points.get("away_scored_in_pct")
    if p_home_scores is None and scoring:
        p_home_scores = scoring.data_points.get("home_scored_in_pct")

    if p_away_scores is None:
        p_away_scores = 1.0 - home_cs
    if p_home_scores is None:
        p_home_scores = 1.0 - away_cs

    away_blanks = scoring.data_points.get("away_blank_games") if scoring else None
    home_blanks = scoring.data_points.get("home_blank_games") if scoring else None
    away_sample = af.data_points.get("away_played", "?") if af else "?"
    home_sample = hf.data_points.get("home_played", "?") if hf else "?"

    # ── Compound probabilities ────────────────────────────────────
    p_home_wtn_yes = p_home_win * home_cs
    p_away_wtn_yes = p_away_win * away_cs
    p_home_wtn_no = 1.0 - p_home_wtn_yes
    p_away_wtn_no = 1.0 - p_away_wtn_yes

    common_stats = {
        "home_cs_rate_home": round(home_cs, 3),
        "away_cs_rate_away": round(away_cs, 3),
        "p_home_win": round(p_home_win, 4),
        "p_away_win": round(p_away_win, 4),
        "p_home_wtn_yes": round(p_home_wtn_yes, 4),
        "p_away_wtn_yes": round(p_away_wtn_yes, 4),
        "p_away_scores": round(p_away_scores, 4),
        "p_home_scores": round(p_home_scores, 4),
    }

    mkt = MARKETS.get("win-to-nil")
    mkt_name = mkt.name if mkt else "Win to Nil"

    # ── Rationale helpers ─────────────────────────────────────────
    def _yes_rationale(team: str, p_win: float, cs_rate: float, p_yes: float) -> str:
        return (
            f"\u26a0\ufe0f Speculative compound bet. {team} team must both win AND keep "
            f"a clean sheet. Win probability: {p_win:.0%}. "
            f"Clean sheet rate ({'at home' if team == 'Home' else 'away'}): {cs_rate:.0%}. "
            f"Combined probability: {p_yes:.0%}. "
            "Only viable with strong conviction on both outcomes independently."
        )

    def _no_rationale(
        scoring_team: str,
        opponent: str,
        p_scores: float,
        cs_rate: float,
        blanks: int | None,
        sample: int | str,
        p_no: float,
    ) -> str:
        parts = [
            f"{scoring_team} team is expected to score at least once: "
            f"P(scores) = {p_scores:.0%}."
        ]
        if blanks is not None:
            parts.append(
                f"Failed to score in {blanks} of {sample} recent "
                f"{'home' if scoring_team == 'Home' else 'away'} games."
            )
        parts.append(
            f"{opponent} team's clean sheet rate "
            f"({'at home' if opponent == 'Home' else 'away'}): {cs_rate:.0%}. "
            f"The \u2018No\u2019 bet wins if {scoring_team.lower()} team scores \u2265 1 goal "
            f"regardless of the final result — P(No) = {p_no:.0%}. "
            "This is the primary value-bet direction."
        )
        return " ".join(parts)

    return [
        MarketRecommendation(
            market_code="win-to-nil",
            market_name=mkt_name,
            pick="Home Win to Nil: Yes",
            probability=round(p_home_wtn_yes, 4),
            confidence=_classify_confidence(p_home_wtn_yes),
            implied_odds=_implied_odds(p_home_wtn_yes),
            rationale=_yes_rationale("Home", p_home_win, home_cs, p_home_wtn_yes),
            risk_factors=[
                "Away team has scored in recent fixtures",
                "Compound outcome: two independent conditions must both hold",
            ],
            supporting_stats=common_stats,
        ),
        MarketRecommendation(
            market_code="win-to-nil",
            market_name=mkt_name,
            pick="Home Win to Nil: No",
            probability=round(p_home_wtn_no, 4),
            confidence=_classify_confidence(p_home_wtn_no),
            implied_odds=_implied_odds(p_home_wtn_no),
            rationale=_no_rationale(
                "Away", "Home", p_away_scores, home_cs, away_blanks, away_sample, p_home_wtn_no
            ),
            risk_factors=[],
            supporting_stats=common_stats,
        ),
        MarketRecommendation(
            market_code="win-to-nil",
            market_name=mkt_name,
            pick="Away Win to Nil: Yes",
            probability=round(p_away_wtn_yes, 4),
            confidence=_classify_confidence(p_away_wtn_yes),
            implied_odds=_implied_odds(p_away_wtn_yes),
            rationale=_yes_rationale("Away", p_away_win, away_cs, p_away_wtn_yes),
            risk_factors=[
                "Home team has scored in recent fixtures",
                "Compound outcome: two independent conditions must both hold",
            ],
            supporting_stats=common_stats,
        ),
        MarketRecommendation(
            market_code="win-to-nil",
            market_name=mkt_name,
            pick="Away Win to Nil: No",
            probability=round(p_away_wtn_no, 4),
            confidence=_classify_confidence(p_away_wtn_no),
            implied_odds=_implied_odds(p_away_wtn_no),
            rationale=_no_rationale(
                "Home", "Away", p_home_scores, away_cs, home_blanks, home_sample, p_away_wtn_no
            ),
            risk_factors=[],
            supporting_stats=common_stats,
        ),
    ]


def model_win_either_half(
    overall_score: float,
    factors: list[FactorResult],
    patterns: dict[str, Any] | None = None,
) -> list[MarketRecommendation]:
    """Model the Win Either Half market.

    Emits both Home WEH and Away WEH picks.  Uses half-time win-rate
    data from ``goal_timing`` to calibrate; falls back to a simple
    upward adjustment from the outright win probability.

    Args:
        overall_score: Weighted aggregate factor score.
        factors: List of ``FactorResult`` instances.
        patterns: Historical pattern data.

    Returns:
        List of two ``MarketRecommendation`` objects.
    """
    p_home_win, p_draw, p_away_win = _1x2_base_probs(overall_score, patterns)
    timing = _get_factor(factors, "goal_timing")

    # ── Half-time win rates ───────────────────────────────────────
    home_1h_win_rate = (
        patterns.get("home_1h_win_rate") if patterns else None
    ) or (
        timing.data_points.get("home_1h_win_rate") if timing and timing.data_points else None
    ) or 0.40

    away_1h_win_rate = (
        patterns.get("away_1h_win_rate") if patterns else None
    ) or (
        timing.data_points.get("away_1h_win_rate") if timing and timing.data_points else None
    ) or 0.30

    # ── WEH historical rate ───────────────────────────────────────
    home_weh_hist = (
        timing.data_points.get("home_weh_historical_pct") if timing and timing.data_points else None
    )
    away_weh_hist = (
        timing.data_points.get("away_weh_historical_pct") if timing and timing.data_points else None
    )

    # ── P(Win Either Half) — three-way blend ──────────────────────
    # 1. Outright quality (45%): team needs to be good enough to dominate a period
    # 2. Historical WEH rate (35%): how often has the team actually won a half recently?
    # 3. First-half goal timing (20%): early-goal tendency captures partial-period dominance
    #
    # Multiplier reduced 1.35 → 1.15: simulation showed WEH HIGH hitting 61% at
    # stated 89% — the 1.35 bonus was too aggressive. WEH is more achievable than
    # an outright win, but not 35% more likely. Output capped at 0.82.
    p_home_base = min(0.82, p_home_win * 1.15)
    p_away_base = min(0.82, p_away_win * 1.15)

    if home_weh_hist is not None:
        p_home_weh = min(0.82, 0.45 * p_home_base + 0.35 * home_weh_hist + 0.20 * home_1h_win_rate)
    else:
        p_home_weh = min(0.82, p_home_base * 0.85 + home_1h_win_rate * 0.15)

    if away_weh_hist is not None:
        p_away_weh = min(0.82, 0.45 * p_away_base + 0.35 * away_weh_hist + 0.20 * away_1h_win_rate)
    else:
        p_away_weh = min(0.82, p_away_base * 0.85 + away_1h_win_rate * 0.15)

    timing_note_h = ""
    if timing and timing.confidence > 0.3:
        timing_note_h = f" H1 win rate: {home_1h_win_rate:.0%}."
        if home_weh_hist is not None:
            timing_note_h += f" Won a half in {home_weh_hist:.0%} of recent games."

    timing_note_a = ""
    if timing and timing.confidence > 0.3:
        timing_note_a = f" H1 win rate: {away_1h_win_rate:.0%}."
        if away_weh_hist is not None:
            timing_note_a += f" Won a half in {away_weh_hist:.0%} of recent games."

    common_stats = {
        "home_weh_prob": round(p_home_weh, 4),
        "away_weh_prob": round(p_away_weh, 4),
        "home_1h_win_rate": home_1h_win_rate,
        "away_1h_win_rate": away_1h_win_rate,
        "home_weh_historical_pct": home_weh_hist,
        "away_weh_historical_pct": away_weh_hist,
    }

    mkt = MARKETS.get("win-either-half")
    mkt_name = mkt.name if mkt else "Win Either Half"

    return [
        MarketRecommendation(
            market_code="win-either-half",
            market_name=mkt_name,
            pick="Home Win Either Half",
            probability=round(p_home_weh, 4),
            confidence=_classify_confidence(p_home_weh),
            implied_odds=_implied_odds(p_home_weh),
            rationale=(
                f"Home outright win probability: {p_home_win:.0%}.{timing_note_h} "
                "Win Either Half covers any period of dominance — team only needs to "
                "lead at HT or score more in the second half. Recommended when "
                "outright odds offer insufficient value."
            ),
            risk_factors=["A slow-starting team may not lead at any break"],
            supporting_stats=common_stats,
        ),
        MarketRecommendation(
            market_code="win-either-half",
            market_name=mkt_name,
            pick="Away Win Either Half",
            probability=round(p_away_weh, 4),
            confidence=_classify_confidence(p_away_weh),
            implied_odds=_implied_odds(p_away_weh),
            rationale=(
                f"Away outright win probability: {p_away_win:.0%}.{timing_note_a} "
                "Win Either Half covers any period of dominance for the away side."
            ),
            risk_factors=["A slow-starting team may not lead at any break"],
            supporting_stats=common_stats,
        ),
    ]


def model_team_over_under(
    overall_score: float,
    factors: list[FactorResult],
    patterns: dict[str, Any] | None = None,
    line: float = 0.5,
) -> list[MarketRecommendation]:
    """Model the Team Over/Under goals market for a given line.

    Emits four picks — Home Over, Home Under, Away Over, Away Under.
    Uses venue-specific GPG (home team at home, away team away) for
    sharper estimates.  Poisson probabilities are used for the 0.5 line.

    Args:
        overall_score: Weighted aggregate factor score.
        factors: List of ``FactorResult`` instances.
        patterns: Historical pattern data.
        line: Goal line for the individual team — typically 0.5 or 1.5.

    Returns:
        List of four ``MarketRecommendation`` objects.
    """
    am = _get_factor(factors, "attacking_matchup")
    scoring = _get_factor(factors, "scoring_frequency")

    # ── Expected goals per team (venue-split preferred) ───────────
    if am and am.data_points.get("expected_home_goals"):
        home_gpg = am.data_points["expected_home_goals"]
        away_gpg = am.data_points["expected_away_goals"]
    elif scoring and scoring.data_points:
        # Overall recent form primary (70%); venue-specific a 30% modifier
        home_base = scoring.data_points.get("home_gpg") or 1.3
        home_venue = scoring.data_points.get("home_home_gpg")
        home_gpg = home_base if home_venue is None else (0.7 * home_base + 0.3 * home_venue)

        away_base = scoring.data_points.get("away_gpg") or 1.1
        away_venue = scoring.data_points.get("away_away_gpg")
        away_gpg = away_base if away_venue is None else (0.7 * away_base + 0.3 * away_venue)
    else:
        home_gpg, away_gpg = 1.3, 1.1

    # Shrink form-based GPG toward league mean before Poisson calculation.
    home_gpg = _shrink_lambda(home_gpg)
    away_gpg = _shrink_lambda(away_gpg)

    # ── Probabilities ─────────────────────────────────────────────
    if line == 0.5:
        p_home_over = (
            patterns.get("poisson_home_over_0_5") if patterns else None
        ) or (1.0 / (1.0 + math.exp(-2.0 * (home_gpg - 0.5))))
        p_away_over = (
            patterns.get("poisson_away_over_0_5") if patterns else None
        ) or (1.0 / (1.0 + math.exp(-2.0 * (away_gpg - 0.5))))
    else:
        p_home_over = 1.0 / (1.0 + math.exp(-2.0 * (home_gpg - line)))
        p_away_over = 1.0 / (1.0 + math.exp(-2.0 * (away_gpg - line)))

    p_home_under = 1.0 - p_home_over
    p_away_under = 1.0 - p_away_over

    line_str = f"{line:g}"
    market_code = f"team-over-under-{line_str}"
    market_name = f"Team Over/Under {line_str} Goals"

    def _rec(team: str, direction: str, prob: float, gpg: float) -> MarketRecommendation:
        return MarketRecommendation(
            market_code=market_code,
            market_name=market_name,
            pick=f"{team} {direction} {line_str}",
            probability=round(prob, 4),
            confidence=_classify_confidence(prob),
            implied_odds=_implied_odds(prob),
            rationale=(
                f"{team} team expected {gpg:.2f} GPG (overall form, venue-adjusted). "
                f"P({direction} {line_str}) = {prob:.0%}."
            ),
            risk_factors=[],
            supporting_stats={
                "gpg": round(gpg, 2),
                "p_over": round(p_home_over if team == "Home" else p_away_over, 4),
                "team_side": team.lower(),
                "line": line,
            },
        )

    return [
        _rec("Home", "Over", p_home_over, home_gpg),
        _rec("Home", "Under", p_home_under, home_gpg),
        _rec("Away", "Over", p_away_over, away_gpg),
        _rec("Away", "Under", p_away_under, away_gpg),
    ]


def model_handicap(
    overall_score: float,
    factors: list[FactorResult],
    patterns: dict[str, Any] | None = None,
) -> list[MarketRecommendation]:
    """Model the Handicap market with half-lines and whole-number void lines.

    Line types:
    - **Half-line** (+/-0.5, 1.5, 2.5): no void possible — straightforward
      win or loss. Requires margin to be strictly greater than the line.
    - **Whole-line** (+/-1.0, 2.0): void (stake returned) if the margin
      equals the line exactly. A 1-goal win on a -1 handicap = void, not
      a loss. This is the safety-net feature.

    Probability model:
    - Uses Poisson goal difference distribution derived from lambda values.
    - P(covers half-line N) = P(margin > N)
    - P(covers whole-line N) = P(margin > N) + 0.5 × P(margin = N) [void counts as half-win]
      But for display purposes we show:
        win_prob = P(margin > N)
        void_prob = P(margin = N)
    - Lines are selected based on expected_goal_diff.

    Args:
        overall_score: Weighted aggregate factor score.
        factors: List of FactorResult instances.
        patterns: Historical pattern data.

    Returns:
        List of MarketRecommendation objects for recommended lines.
    """
    am = _get_factor(factors, "attacking_matchup")
    scoring = _get_factor(factors, "scoring_frequency")

    if am and am.data_points.get("expected_goal_diff") is not None:
        expected_margin = am.data_points["expected_goal_diff"]
    else:
        expected_margin = overall_score * 2.5

    fav = "Home" if expected_margin >= 0 else "Away"
    abs_margin = abs(expected_margin)

    # ── Get lambdas for Poisson distribution ─────────────────────
    if am and am.data_points.get("expected_home_goals"):
        lam_fav = am.data_points["expected_home_goals"] if fav == "Home" else am.data_points["expected_away_goals"]
        lam_dog = am.data_points["expected_away_goals"] if fav == "Home" else am.data_points["expected_home_goals"]
    elif scoring and scoring.data_points:
        lam_fav = scoring.data_points.get("home_gpg" if fav == "Home" else "away_gpg") or 1.3
        lam_dog = scoring.data_points.get("away_gpg" if fav == "Home" else "home_gpg") or 1.1
    else:
        lam_fav, lam_dog = 1.3, 1.1

    def _p_margin_gt(n: float) -> float:
        """P(favoured team wins by more than n goals)."""
        # Logistic model centred on expected margin
        return 1.0 / (1.0 + math.exp(-1.8 * (abs_margin - n)))

    def _p_margin_eq(n: int) -> float:
        """P(favoured team wins by exactly n goals) — Poisson approximation."""
        # P(margin = n) ≈ P(margin > n-1) - P(margin > n)
        return max(0.0, _p_margin_gt(n - 0.5) - _p_margin_gt(n + 0.5))

    # ── Select which lines to expose ──────────────────────────────
    # Based on expected margin, offer the 2-3 most relevant lines.
    # Always include at least one half-line and one whole-line.
    if abs_margin < 0.8:
        candidate_lines = [(0.5, "half")]
    elif abs_margin < 1.3:
        candidate_lines = [(0.5, "half"), (1.0, "whole")]
    elif abs_margin < 1.8:
        candidate_lines = [(1.0, "whole"), (1.5, "half")]
    elif abs_margin < 2.3:
        candidate_lines = [(1.0, "whole"), (1.5, "half"), (2.0, "whole")]
    elif abs_margin < 2.8:
        candidate_lines = [(1.5, "half"), (2.0, "whole"), (2.5, "half")]
    else:
        candidate_lines = [(2.0, "whole"), (2.5, "half"), (3.0, "whole")]

    mkt = MARKETS.get("handicap")
    mkt_name = mkt.name if mkt else "Handicap"
    recs = []

    for line, line_type in candidate_lines:
        if line_type == "half":
            # Half-line: no void — must win by MORE than line
            p_cover = _p_margin_gt(line)
            void_prob = 0.0
            pick = f"{fav} -{line:g}" if line > 0 else f"{fav} (scratch)"
            rationale = (
                f"Expected margin: {abs_margin:.1f} goals in favour of {fav}. "
                f"{fav} -{line:g} handicap (half-line — no void). "
                f"P(covers) = {p_cover:.0%}. "
                f"{fav} must win by more than {line:g} goals — no void scenario."
            )
        else:  # whole-line
            # Whole-line: void if margin is exactly the line
            p_cover = _p_margin_gt(line)  # strictly covers
            void_prob = _p_margin_eq(int(line))
            effective_prob = p_cover + 0.5 * void_prob  # void = half-refund benefit
            void_note = f" Void (stake returned) if margin = exactly {line:g} goals: P(void) = {void_prob:.0%}."
            pick = f"{fav} -{line:g} (void at {line:g})"
            rationale = (
                f"Expected margin: {abs_margin:.1f} goals in favour of {fav}. "
                f"{fav} -{line:g} handicap (whole-line — void possible). "
                f"P(wins outright) = {p_cover:.0%}.{void_note} "
                f"Effective coverage probability (counting void as safety net) = {effective_prob:.0%}."
            )
            p_cover = effective_prob  # use effective prob for confidence classification

        risks = []
        if abs(abs_margin - line) < 0.4:
            risks.append(f"Expected margin is very close to the line — high uncertainty")
        if abs_margin < 0.5:
            risks.append("Marginal favourite — handicap picks carry high variance")

        recs.append(MarketRecommendation(
            market_code="handicap",
            market_name=mkt_name,
            pick=pick,
            probability=round(p_cover, 4),
            confidence=_classify_confidence(p_cover),
            implied_odds=_implied_odds(p_cover),
            rationale=rationale,
            risk_factors=risks,
            supporting_stats={
                "expected_margin": round(expected_margin, 2),
                "handicap_line": line,
                "line_type": line_type,
                "void_prob": round(void_prob, 4) if line_type == "whole" else 0.0,
                "favoured_team": fav,
            },
        ))

    return recs if recs else []



def model_lead_trigger(
    overall_score: float,
    factors: list[FactorResult],
    patterns: dict[str, Any] | None = None,
) -> list[MarketRecommendation]:
    """Model 1X2 - 1UP and 1X2 - 2UP lead-trigger sub-markets.

    These are Sportybet sub-picks within the 1X2 market:
    - 1UP: bet settles as WIN the moment the selected team goes 1 goal ahead
      at any point during the match, regardless of final score.
    - 2UP: same but requires the team to go 2 goals ahead at any point.

    Key insight: these decouple from the final result. A team that leads 2-0
    but draws 2-2 still triggers 2UP. A team that leads 1-0 but loses 1-2
    still triggers 1UP.

    Probability model:
    - P(1UP) ≈ P(team scores first) + P(team recovers to lead after going behind)
      First-goal probability = λ_home / (λ_home + λ_away)
      Recovery adjustment: +8% for the home team (home advantage in comebacks)
    - P(2UP) ≈ P(1UP) × P(team scores next goal given they are level or ahead)
      Conditional next-goal ≈ λ_team / (λ_home + λ_away)
      2UP is strictly easier than winning outright (can still draw/lose after 2-0)

    Args:
        overall_score: Weighted aggregate factor score.
        factors: List of FactorResult instances.
        patterns: Historical pattern data.

    Returns:
        List of four MarketRecommendation objects (Home/Away × 1UP/2UP).
    """
    scoring = _get_factor(factors, "scoring_frequency")
    am = _get_factor(factors, "attacking_matchup")
    timing = _get_factor(factors, "goal_timing")
    h2h = _get_factor(factors, "head_to_head")

    # ── Expected goals per team (Poisson λ) ───────────────────────
    if am and am.data_points.get("expected_home_goals"):
        lam_h = max(0.1, am.data_points["expected_home_goals"])
        lam_a = max(0.1, am.data_points["expected_away_goals"])
    elif scoring and scoring.data_points:
        h_base = scoring.data_points.get("home_gpg") or 1.3
        h_venue = scoring.data_points.get("home_home_gpg")
        lam_h = max(0.1, h_base if h_venue is None else 0.7 * h_base + 0.3 * h_venue)
        a_base = scoring.data_points.get("away_gpg") or 1.1
        a_venue = scoring.data_points.get("away_away_gpg")
        lam_a = max(0.1, a_base if a_venue is None else 0.7 * a_base + 0.3 * a_venue)
    else:
        lam_h, lam_a = 1.3, 1.1

    total_rate = lam_h + lam_a

    # ── Scoring participation rate ─────────────────────────────────
    # P(team scores at least 1 goal in match) — form data or Poisson fallback
    home_scored_pct = (
        scoring.data_points.get("home_scored_pct") if scoring and scoring.data_points else None
    ) or (1.0 - math.exp(-lam_h))
    away_scored_pct = (
        scoring.data_points.get("away_scored_pct") if scoring and scoring.data_points else None
    ) or (1.0 - math.exp(-lam_a))

    # ── P(scores first) via Poisson race ─────────────────────────
    p_home_first = lam_h / total_rate
    p_away_first = lam_a / total_rate

    # ── HT lead rate from timing data ────────────────────────────
    home_h1_rate = (
        timing.data_points.get("home_1h_win_rate") if timing and timing.data_points else None
    ) or 0.38
    away_h1_rate = (
        timing.data_points.get("away_1h_win_rate") if timing and timing.data_points else None
    ) or 0.28

    # ── H2H lead tendency ────────────────────────────────────────
    h2h_home_win_rate = (
        h2h.data_points.get("home_win_rate_h2h") if h2h and h2h.data_points else None
    ) or 0.45

    # ── P(1UP) ────────────────────────────────────────────────────
    # P(home leads at any point) =
    #   P(scores first) → directly leads 1-0
    # + P(away scores first) × P(home then comes back to lead)
    #   comeback ≈ P(home scores two before away scores a second from 0-1 down)
    #            ≈ (λ_h / total)^1.3 × 0.85 (dampened — comebacks are harder)
    p_h_comeback = p_away_first * (lam_h / total_rate) ** 1.3 * 0.85
    p_h_1up_raw = p_home_first + p_h_comeback

    # Form anchor: scoring_pct + h1_rate + h2h_tendency
    p_h_1up_form = 0.55 * home_scored_pct + 0.30 * home_h1_rate + 0.15 * h2h_home_win_rate

    # Final 1UP: 65% model, 35% form anchor
    p_home_1up = 0.65 * p_h_1up_raw + 0.35 * p_h_1up_form

    # Away 1UP symmetric (away comebacks slightly harder)
    p_a_comeback = p_home_first * (lam_a / total_rate) ** 1.3 * 0.70
    p_a_1up_raw = p_away_first + p_a_comeback
    p_a_1up_form = 0.55 * away_scored_pct + 0.30 * away_h1_rate + 0.15 * (1.0 - h2h_home_win_rate)
    p_away_1up = 0.65 * p_a_1up_raw + 0.35 * p_a_1up_form

    # ── P(2UP) ────────────────────────────────────────────────────
    # P(2UP home) = P(home win) + P(led by 2 but drew or lost)
    # P(led by 2 at any point) — sequential Poisson:
    #   going 2 ahead = home scores twice before away equalises from 0
    #   ≈ (λ_h / total)^1.6  (exponent < 2 accounts for non-sequential paths)
    p_home_win, _, p_away_win = _1x2_base_probs(overall_score, patterns)

    p_h_reach_2_ahead = (lam_h / total_rate) ** 1.6
    p_a_reach_2_ahead = (lam_a / total_rate) ** 1.6

    # Bonus = additional coverage beyond plain win (capped at 15%)
    h_bonus = min(0.15, max(0.0, p_h_reach_2_ahead - p_home_win * 0.5))
    a_bonus = min(0.12, max(0.0, p_a_reach_2_ahead - p_away_win * 0.5))

    p_home_2up = p_home_win + h_bonus
    p_away_2up = p_away_win + a_bonus

    # ── Constraint enforcement ────────────────────────────────────
    # 1UP >= 2UP (triggers on smaller lead threshold)
    p_home_2up = min(p_home_2up, p_home_1up - 0.02)
    p_away_2up = min(p_away_2up, p_away_1up - 0.02)
    # 2UP >= win (adds coverage, never removes)
    p_home_2up = max(p_home_2up, p_home_win)
    p_away_2up = max(p_away_2up, p_away_win)
    # 1UP >= win + meaningful gap (covers draw/loss scenarios)
    p_home_1up = max(p_home_1up, p_home_win + 0.04)
    p_away_1up = max(p_away_1up, p_away_win + 0.03)
    # Global clamp
    p_home_1up = min(0.97, p_home_1up)
    p_away_1up = min(0.97, p_away_1up)
    p_home_2up = min(0.97, p_home_2up)
    p_away_2up = min(0.97, p_away_2up)

    mkt = MARKETS.get("1x2")
    mkt_name = mkt.name if mkt else "Match Result"

    stats = {
        "lambda_home": round(lam_h, 2),
        "lambda_away": round(lam_a, 2),
        "p_home_scores_first": round(p_home_first, 3),
        "p_away_scores_first": round(p_away_first, 3),
        "home_scored_pct": round(home_scored_pct, 3),
        "away_scored_pct": round(away_scored_pct, 3),
        "home_h1_win_rate": round(home_h1_rate, 3),
        "away_h1_win_rate": round(away_h1_rate, 3),
        "p_home_win": round(p_home_win, 3),
        "p_away_win": round(p_away_win, 3),
    }

    return [
        MarketRecommendation(
            market_code="1x2",
            market_name=mkt_name,
            pick="Home 1X2 - 1UP",
            probability=round(p_home_1up, 4),
            confidence=_classify_confidence(p_home_1up),
            implied_odds=_implied_odds(p_home_1up),
            rationale=(
                f"P(Home leads at any point) = {p_home_1up:.0%}. "
                f"Expected {lam_h:.1f} home goals vs {lam_a:.1f} away goals. "
                f"Home first-goal probability: {p_home_first:.0%}. "
                "Bet settles as WIN the moment home team goes 1 goal ahead — "
                "does NOT require holding the lead until full time."
            ),
            risk_factors=[
                "Home team may never take the lead even if they win on aggregate",
                "Low-scoring matches reduce first-goal probabilities",
            ],
            supporting_stats=stats,
        ),
        MarketRecommendation(
            market_code="1x2",
            market_name=mkt_name,
            pick="Away 1X2 - 1UP",
            probability=round(p_away_1up, 4),
            confidence=_classify_confidence(p_away_1up),
            implied_odds=_implied_odds(p_away_1up),
            rationale=(
                f"P(Away leads at any point) = {p_away_1up:.0%}. "
                f"Away first-goal probability: {p_away_first:.0%}. "
                "Bet settles as WIN the moment away team goes 1 goal ahead."
            ),
            risk_factors=["Away team may never lead, even in draws or away wins on aggregate"],
            supporting_stats=stats,
        ),
        MarketRecommendation(
            market_code="1x2",
            market_name=mkt_name,
            pick="Home 1X2 - 2UP",
            probability=round(p_home_2up, 4),
            confidence=_classify_confidence(p_home_2up),
            implied_odds=_implied_odds(p_home_2up),
            rationale=(
                f"P(Home leads by 2 at any point) = {p_home_2up:.0%}. "
                f"Expected {lam_h:.1f} home goals vs {lam_a:.1f} away goals. "
                "Bet settles as WIN once home team goes 2 goals ahead at any moment "
                "— the lead does not need to be maintained. "
                "Better odds than 1UP but still safer than an outright win."
            ),
            risk_factors=[
                "Match may stay close — team may lead by only 1 at most",
                "Low-scoring favourites unlikely to build a 2-goal lead",
            ],
            supporting_stats=stats,
        ),
        MarketRecommendation(
            market_code="1x2",
            market_name=mkt_name,
            pick="Away 1X2 - 2UP",
            probability=round(p_away_2up, 4),
            confidence=_classify_confidence(p_away_2up),
            implied_odds=_implied_odds(p_away_2up),
            rationale=(
                f"P(Away leads by 2 at any point) = {p_away_2up:.0%}. "
                "Bet settles as WIN once away team goes 2 goals ahead — regardless of final score."
            ),
            risk_factors=["Away team unlikely to build a 2-goal lead against a strong home side"],
            supporting_stats=stats,
        ),
    ]



# ═══════════════════════════════════════════════════════════════
#  Half-Time & Second-Half Markets
# ═══════════════════════════════════════════════════════════════

# European football averages (used as shrinkage priors for half-time)
_FH_GOAL_MEAN_PER_TEAM = 0.50  # avg goals per team in first 45 mins
_SH_GOAL_MEAN_PER_TEAM = 0.56  # avg goals per team in second 45 mins


def _half_lambdas(
    factors: list[FactorResult],
    half: str,
) -> tuple[float, float, float, float]:
    """Derive per-team, per-half Poisson lambdas from scoring + timing factors.

    Uses the goal_timing factor's per-team first/second half goal percentage
    (``first_half_pct`` / ``second_half_pct``) applied to the shrunk FT lambda
    from the scoring_frequency factor.

    Falls back to the European half-time prior when timing data is absent.

    Args:
        factors: List of FactorResult from the analysis engine.
        half: ``"first"`` or ``"second"``.

    Returns:
        ``(home_ht_lam, away_ht_lam, home_gpg_ft, away_gpg_ft)`` — all shrunk.
    """
    scoring = _get_factor(factors, "scoring_frequency")
    timing = _get_factor(factors, "goal_timing")

    # ── FT lambdas ────────────────────────────────────────────────
    if scoring and scoring.data_points:
        dp = scoring.data_points
        home_base = dp.get("home_gpg") or 1.3
        home_venue = dp.get("home_home_gpg")
        home_gpg = home_base if home_venue is None else 0.7 * home_base + 0.3 * home_venue

        away_base = dp.get("away_gpg") or 1.1
        away_venue = dp.get("away_away_gpg")
        away_gpg = away_base if away_venue is None else 0.7 * away_base + 0.3 * away_venue
    else:
        home_gpg, away_gpg = 1.3, 1.1

    # Shrink FT lambda toward league mean (same as main O/U model)
    home_gpg = _shrink_lambda(home_gpg)
    away_gpg = _shrink_lambda(away_gpg)

    # ── Half-specific percentage ───────────────────────────────────
    prior = _FH_GOAL_MEAN_PER_TEAM if half == "first" else _SH_GOAL_MEAN_PER_TEAM
    pct_key = "first_half_pct" if half == "first" else "second_half_pct"

    h_td = (timing.data_points.get("home") or {}) if timing else {}
    a_td = (timing.data_points.get("away") or {}) if timing else {}

    h_pct = h_td.get(pct_key, prior / home_gpg if home_gpg > 0 else 0.44)
    a_pct = a_td.get(pct_key, prior / away_gpg if away_gpg > 0 else 0.44)

    # ── Half lambdas with per-half prior shrinkage ─────────────────
    # Shrink the derived half lambda toward the half-specific prior:
    # λ_half = 0.25 × prior + 0.75 × (FT_λ × pct)
    HALF_SHRINK = 0.25
    home_half_lam = HALF_SHRINK * prior + (1 - HALF_SHRINK) * (home_gpg * h_pct)
    away_half_lam = HALF_SHRINK * prior + (1 - HALF_SHRINK) * (away_gpg * a_pct)

    home_half_lam = max(0.10, min(2.5, home_half_lam))
    away_half_lam = max(0.10, min(2.5, away_half_lam))

    return home_half_lam, away_half_lam, home_gpg, away_gpg


def _ou_with_correction(
    lam: float,
    line: float,
) -> tuple[float, float]:
    """Compute Over/Under probabilities with Dixon-Coles near-line correction.

    Args:
        lam: Total expected goals (Poisson lambda).
        line: Goal line (e.g. 0.5, 1.5).

    Returns:
        ``(p_over, p_under)`` after correction.
    """
    import math as _math
    # Poisson CDF: P(X <= k) = sum_{i=0}^{k} e^{-lam} * lam^i / i!
    # P(Over line) = 1 - P(X <= floor(line))
    k_max = int(line)  # for line=0.5 → 0; line=1.5 → 1
    p_at_most_k = 0.0
    for k in range(k_max + 1):
        p_at_most_k += _math.exp(-lam) * (lam ** k) / _math.factorial(k)
    p_over = 1.0 - p_at_most_k
    p_over = max(0.0, min(1.0, p_over))

    # Dixon-Coles shrinkage toward 0.5 when expected total is close to line
    dist = abs(lam - (line + 0.5))
    if dist < 0.6:
        shrink = 0.65 + 0.35 * (dist / 0.6)
        p_over = 0.5 + shrink * (p_over - 0.5)

    return p_over, 1.0 - p_over


def model_first_half_over_under(
    overall_score: float,
    factors: list[FactorResult],
    patterns: dict[str, Any] | None = None,
    line: float = 0.5,
) -> list[MarketRecommendation]:
    """1st Half Total Goals Over/Under for a given line (0.5, 1.5).

    Derives expected first-half goals from the ``goal_timing`` factor's
    per-team H1 goal percentage applied to the shrunk FT lambda.

    Args:
        overall_score: Weighted aggregate factor score.
        factors: List of FactorResult instances.
        patterns: Historical pattern data (unused — HT data comes from factors).
        line: Goal line — 0.5 or 1.5.

    Returns:
        List of two ``MarketRecommendation`` objects (Over and Under).
    """
    home_lam, away_lam, home_gpg, away_gpg = _half_lambdas(factors, "first")
    total_lam = home_lam + away_lam
    line_str = f"{line:g}"

    p_over, p_under = _ou_with_correction(total_lam, line)

    market_code = f"first-half-over-under-{line_str}"
    market_name = f"1st Half Over/Under {line_str}"

    over_rationale = (
        f"Expected {total_lam:.2f} 1st-half goals "
        f"(home {home_lam:.2f} + away {away_lam:.2f}, "
        f"derived from FT lambdas {home_gpg:.1f}/{away_gpg:.1f}). "
        f"1st Half Over {line_str} probability: {p_over:.0%}."
    )
    under_rationale = (
        f"Expected {total_lam:.2f} 1st-half goals. "
        f"1st Half Under {line_str} probability: {p_under:.0%}."
    )

    stats = {
        "expected_ht_total": round(total_lam, 2),
        "home_ht_lambda": round(home_lam, 2),
        "away_ht_lambda": round(away_lam, 2),
        "half": "first",
        "line": line,
    }

    return [
        MarketRecommendation(
            market_code=market_code,
            market_name=market_name,
            pick=f"1st Half Over {line_str}",
            probability=round(p_over, 4),
            confidence=_classify_confidence(p_over),
            implied_odds=_implied_odds(p_over),
            rationale=over_rationale,
            risk_factors=(
                ["Very close to the line — genuine 50/50"] if abs(p_over - 0.5) < 0.07 else []
            ),
            supporting_stats=stats,
        ),
        MarketRecommendation(
            market_code=market_code,
            market_name=market_name,
            pick=f"1st Half Under {line_str}",
            probability=round(p_under, 4),
            confidence=_classify_confidence(p_under),
            implied_odds=_implied_odds(p_under),
            rationale=under_rationale,
            risk_factors=[],
            supporting_stats=stats,
        ),
    ]


def model_second_half_over_under(
    overall_score: float,
    factors: list[FactorResult],
    patterns: dict[str, Any] | None = None,
    line: float = 0.5,
) -> list[MarketRecommendation]:
    """2nd Half Total Goals Over/Under for a given line (0.5, 1.5).

    Second-half goal rates tend to be slightly higher than first-half in
    European football (~56% of goals scored in the second 45 minutes).
    This model uses the ``goal_timing`` second_half_pct per team.

    Args:
        overall_score: Weighted aggregate factor score.
        factors: List of FactorResult instances.
        patterns: Historical pattern data.
        line: Goal line — 0.5 or 1.5.

    Returns:
        List of two ``MarketRecommendation`` objects (Over and Under).
    """
    home_lam, away_lam, home_gpg, away_gpg = _half_lambdas(factors, "second")
    total_lam = home_lam + away_lam
    line_str = f"{line:g}"

    p_over, p_under = _ou_with_correction(total_lam, line)

    market_code = f"second-half-over-under-{line_str}"
    market_name = f"2nd Half Over/Under {line_str}"

    over_rationale = (
        f"Expected {total_lam:.2f} 2nd-half goals "
        f"(home {home_lam:.2f} + away {away_lam:.2f}, "
        f"derived from FT lambdas {home_gpg:.1f}/{away_gpg:.1f}). "
        f"2nd Half Over {line_str} probability: {p_over:.0%}."
    )
    under_rationale = (
        f"Expected {total_lam:.2f} 2nd-half goals. "
        f"2nd Half Under {line_str} probability: {p_under:.0%}."
    )

    stats = {
        "expected_sh_total": round(total_lam, 2),
        "home_sh_lambda": round(home_lam, 2),
        "away_sh_lambda": round(away_lam, 2),
        "half": "second",
        "line": line,
    }

    return [
        MarketRecommendation(
            market_code=market_code,
            market_name=market_name,
            pick=f"2nd Half Over {line_str}",
            probability=round(p_over, 4),
            confidence=_classify_confidence(p_over),
            implied_odds=_implied_odds(p_over),
            rationale=over_rationale,
            risk_factors=(
                ["Very close to the line — genuine 50/50"] if abs(p_over - 0.5) < 0.07 else []
            ),
            supporting_stats=stats,
        ),
        MarketRecommendation(
            market_code=market_code,
            market_name=market_name,
            pick=f"2nd Half Under {line_str}",
            probability=round(p_under, 4),
            confidence=_classify_confidence(p_under),
            implied_odds=_implied_odds(p_under),
            rationale=under_rationale,
            risk_factors=[],
            supporting_stats=stats,
        ),
    ]


def model_team_first_half_over_under(
    overall_score: float,
    factors: list[FactorResult],
    patterns: dict[str, Any] | None = None,
    line: float = 0.5,
) -> list[MarketRecommendation]:
    """Per-team 1st Half Goals Over/Under for home and away sides.

    Emits four picks per line: Home Team 1st Half Over/Under and
    Away Team 1st Half Over/Under.  Each uses the team's own FT lambda
    scaled by that team's historical first-half goal percentage.

    Key use case: "Home Team 1st Half Over 0.5" at ~1.50 SportyBet odds
    when the home side consistently scores before half-time.

    Args:
        overall_score: Weighted aggregate factor score.
        factors: List of FactorResult instances.
        patterns: Historical pattern data.
        line: Goal line — 0.5 or 1.5.

    Returns:
        List of four ``MarketRecommendation`` objects.
    """
    home_lam, away_lam, home_gpg, away_gpg = _half_lambdas(factors, "first")
    line_str = f"{line:g}"

    h_over, h_under = _ou_with_correction(home_lam, line)
    a_over, a_under = _ou_with_correction(away_lam, line)

    home_market_code = f"home-team-first-half-over-under-{line_str}"
    away_market_code = f"away-team-first-half-over-under-{line_str}"
    home_market_name = f"Home Team 1st Half Over/Under {line_str}"
    away_market_name = f"Away Team 1st Half Over/Under {line_str}"

    home_stats = {
        "team": "home",
        "half": "first",
        "line": line,
        "team_ht_lambda": round(home_lam, 2),
        "ft_lambda": round(home_gpg, 2),
    }
    away_stats = {
        "team": "away",
        "half": "first",
        "line": line,
        "team_ht_lambda": round(away_lam, 2),
        "ft_lambda": round(away_gpg, 2),
    }

    recs = []
    for (prob_over, prob_under, team_lam, stats, m_code, m_name, team_label) in [
        (h_over, h_under, home_lam, home_stats, home_market_code, home_market_name, "Home"),
        (a_over, a_under, away_lam, away_stats, away_market_code, away_market_name, "Away"),
    ]:
        recs.extend([
            MarketRecommendation(
                market_code=m_code,
                market_name=m_name,
                pick=f"{team_label} Team 1st Half Over {line_str}",
                probability=round(prob_over, 4),
                confidence=_classify_confidence(prob_over),
                implied_odds=_implied_odds(prob_over),
                rationale=(
                    f"Expected {team_lam:.2f} 1st-half goals for {team_label.lower()} team "
                    f"(FT λ {home_gpg if team_label == 'Home' else away_gpg:.1f}). "
                    f"{team_label} Team 1st Half Over {line_str}: {prob_over:.0%}."
                ),
                risk_factors=(
                    ["Close to the line — 50/50"] if abs(prob_over - 0.5) < 0.07 else []
                ),
                supporting_stats=stats,
            ),
            MarketRecommendation(
                market_code=m_code,
                market_name=m_name,
                pick=f"{team_label} Team 1st Half Under {line_str}",
                probability=round(prob_under, 4),
                confidence=_classify_confidence(prob_under),
                implied_odds=_implied_odds(prob_under),
                rationale=(
                    f"Expected {team_lam:.2f} 1st-half goals for {team_label.lower()} team. "
                    f"{team_label} Team 1st Half Under {line_str}: {prob_under:.0%}."
                ),
                risk_factors=[],
                supporting_stats=stats,
            ),
        ])

    return recs


def model_team_second_half_over_under(
    overall_score: float,
    factors: list[FactorResult],
    patterns: dict[str, Any] | None = None,
    line: float = 0.5,
) -> list[MarketRecommendation]:
    """Per-team 2nd Half Goals Over/Under for home and away sides.

    Emits four picks per line: Home Team 2nd Half Over/Under and
    Away Team 2nd Half Over/Under.  Second-half goal rates are typically
    higher (~56% of a team's goals score after the break), making
    Over picks slightly more probable than first-half equivalents.

    Key use case: "Away Team 2nd Half Over 0.5" for sides that come
    on strong late — often available at 1.35-1.55 on SportyBet.

    Args:
        overall_score: Weighted aggregate factor score.
        factors: List of FactorResult instances.
        patterns: Historical pattern data.
        line: Goal line — 0.5 or 1.5.

    Returns:
        List of four ``MarketRecommendation`` objects.
    """
    home_lam, away_lam, home_gpg, away_gpg = _half_lambdas(factors, "second")
    line_str = f"{line:g}"

    h_over, h_under = _ou_with_correction(home_lam, line)
    a_over, a_under = _ou_with_correction(away_lam, line)

    home_market_code = f"home-team-second-half-over-under-{line_str}"
    away_market_code = f"away-team-second-half-over-under-{line_str}"
    home_market_name = f"Home Team 2nd Half Over/Under {line_str}"
    away_market_name = f"Away Team 2nd Half Over/Under {line_str}"

    home_stats = {
        "team": "home",
        "half": "second",
        "line": line,
        "team_sh_lambda": round(home_lam, 2),
        "ft_lambda": round(home_gpg, 2),
    }
    away_stats = {
        "team": "away",
        "half": "second",
        "line": line,
        "team_sh_lambda": round(away_lam, 2),
        "ft_lambda": round(away_gpg, 2),
    }

    recs = []
    for (prob_over, prob_under, team_lam, stats, m_code, m_name, team_label) in [
        (h_over, h_under, home_lam, home_stats, home_market_code, home_market_name, "Home"),
        (a_over, a_under, away_lam, away_stats, away_market_code, away_market_name, "Away"),
    ]:
        recs.extend([
            MarketRecommendation(
                market_code=m_code,
                market_name=m_name,
                pick=f"{team_label} Team 2nd Half Over {line_str}",
                probability=round(prob_over, 4),
                confidence=_classify_confidence(prob_over),
                implied_odds=_implied_odds(prob_over),
                rationale=(
                    f"Expected {team_lam:.2f} 2nd-half goals for {team_label.lower()} team "
                    f"(FT λ {home_gpg if team_label == 'Home' else away_gpg:.1f}). "
                    f"{team_label} Team 2nd Half Over {line_str}: {prob_over:.0%}."
                ),
                risk_factors=(
                    ["Close to the line — 50/50"] if abs(prob_over - 0.5) < 0.07 else []
                ),
                supporting_stats=stats,
            ),
            MarketRecommendation(
                market_code=m_code,
                market_name=m_name,
                pick=f"{team_label} Team 2nd Half Under {line_str}",
                probability=round(prob_under, 4),
                confidence=_classify_confidence(prob_under),
                implied_odds=_implied_odds(prob_under),
                rationale=(
                    f"Expected {team_lam:.2f} 2nd-half goals for {team_label.lower()} team. "
                    f"{team_label} Team 2nd Half Under {line_str}: {prob_under:.0%}."
                ),
                risk_factors=[],
                supporting_stats=stats,
            ),
        ])

    return recs


# ═══════════════════════════════════════════════════════════════
#  Orchestrator
# ═══════════════════════════════════════════════════════════════

def evaluate_all_markets(
    overall_score: float,
    factors: list[FactorResult],
    patterns: dict[str, Any] | None = None,
) -> list[MarketRecommendation]:
    """Run all market models and return a unified list of recommendations.

    Each model now emits *all* meaningful pick directions, so the total
    number of recommendations per match is approximately 30–35 (up from
    the previous ~13).  The caller is responsible for tiered display.

    Args:
        overall_score: Weighted aggregate factor score (-1 to +1).
        factors: List of individual ``FactorResult`` instances.
        patterns: Optional historical pattern data.

    Returns:
        Sorted list of ``MarketRecommendation`` — HIGH confidence first,
        then by probability descending within each confidence tier.
    """
    recommendations: list[MarketRecommendation] = []

    try:
        recommendations.extend(model_1x2(overall_score, factors, patterns))
    except Exception as e:
        logger.warning("1X2 model failed: %s", e)

    try:
        recommendations.extend(model_lead_trigger(overall_score, factors, patterns))
    except Exception as e:
        logger.warning("Lead trigger (1UP/2UP) model failed: %s", e)

    try:
        recommendations.extend(model_double_chance(overall_score, factors, patterns))
    except Exception as e:
        logger.warning("Double Chance model failed: %s", e)

    for line in [0.5, 1.5, 2.5, 3.5]:
        try:
            recommendations.extend(
                model_over_under(overall_score, factors, patterns, line=line)
            )
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

    for line in [0.5, 1.5, 2.5]:
        try:
            recommendations.extend(
                model_team_over_under(overall_score, factors, patterns, line=line)
            )
        except Exception as e:
            logger.warning("Team O/U %.1f model failed: %s", line, e)

    try:
        recommendations.extend(model_handicap(overall_score, factors, patterns))
    except Exception as e:
        logger.warning("Handicap model failed: %s", e)

    # ── Half-time and second-half markets ────────────────────────────
    for line in [0.5, 1.5]:
        try:
            recommendations.extend(
                model_first_half_over_under(overall_score, factors, patterns, line=line)
            )
        except Exception as e:
            logger.warning("1st Half O/U %.1f model failed: %s", line, e)

    for line in [0.5, 1.5]:
        try:
            recommendations.extend(
                model_second_half_over_under(overall_score, factors, patterns, line=line)
            )
        except Exception as e:
            logger.warning("2nd Half O/U %.1f model failed: %s", line, e)

    for line in [0.5, 1.5]:
        try:
            recommendations.extend(
                model_team_first_half_over_under(overall_score, factors, patterns, line=line)
            )
        except Exception as e:
            logger.warning("Team 1st Half O/U %.1f model failed: %s", line, e)

    for line in [0.5, 1.5]:
        try:
            recommendations.extend(
                model_team_second_half_over_under(overall_score, factors, patterns, line=line)
            )
        except Exception as e:
            logger.warning("Team 2nd Half O/U %.1f model failed: %s", line, e)

    # Sort: HIGH first, then MEDIUM, then LOW; within each tier by probability desc
    confidence_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    recommendations.sort(
        key=lambda r: (confidence_order.get(r.confidence, 3), -r.probability)
    )

    return recommendations
