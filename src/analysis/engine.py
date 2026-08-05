"""
Footy Predict — Analysis Engine

The orchestrator that runs all 16 factors, aggregates their scores, and
feeds them into market-specific models to produce comprehensive match
analyses.

Aggregation formula
-------------------
    overall = Σ(score × weight × confidence) / Σ(weight × confidence)

Factors with low confidence are automatically down-weighted, so sparse
data doesn't poison the result.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from src.analysis.factors import ALL_FACTORS, FactorResult
from src.analysis.markets import MarketRecommendation, evaluate_all_markets
from src.config import CONFIDENCE_THRESHOLDS, FACTOR_WEIGHTS, LEAGUES
from src.database import Database

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
#  Core Data Structure
# ═══════════════════════════════════════════════════════════════

@dataclass
class MatchAnalysis:
    """Complete analysis for a single fixture.

    Attributes:
        match_id: Internal match ID from the database.
        home_team: Home team display name.
        away_team: Away team display name.
        league: League display name.
        match_date: ISO 8601 date string.
        factor_results: All 16 factor evaluations.
        overall_home_score: Weighted aggregate (-1 to +1).
        overall_confidence: Weighted average confidence (0 to 1).
        market_recommendations: Sorted list of market picks.
        risk_flags: Conditions that reduce prediction reliability.
        data_quality: ``"HIGH"`` / ``"MEDIUM"`` / ``"LOW"``.
        summary: Natural language summary of the analysis.
    """

    match_id: int
    home_team: str
    away_team: str
    league: str
    match_date: str
    factor_results: list[FactorResult] = field(default_factory=list)
    overall_home_score: float = 0.0
    overall_confidence: float = 0.0
    market_recommendations: list[MarketRecommendation] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    data_quality: str = "LOW"
    summary: str = ""


# ═══════════════════════════════════════════════════════════════
#  Analysis Engine
# ═══════════════════════════════════════════════════════════════

class AnalysisEngine:
    """Orchestrates factor evaluation and market modelling for matches.

    Usage::

        db = Database()
        engine = AnalysisEngine(db)
        analysis = engine.analyze_match(match_id=42)
        for rec in analysis.market_recommendations:
            print(f"{rec.market_name}: {rec.pick} ({rec.confidence})")
    """

    def __init__(self, db: Database, weights: dict[str, float] | None = None) -> None:
        self.db = db
        self.weights = weights or dict(FACTOR_WEIGHTS)

    # ───────────────────────────────────────────────────────────
    #  Single match analysis
    # ───────────────────────────────────────────────────────────

    def analyze_match(self, match_id: int) -> MatchAnalysis:
        """Run the full analysis pipeline for a single fixture.

        1. Load match + team data from DB.
        2. Run all 16 factors.
        3. Compute weighted aggregate score.
        4. Evaluate all market models.
        5. Assess data quality and generate summary.

        Args:
            match_id: Database ID of the match to analyse.

        Returns:
            A fully populated ``MatchAnalysis`` instance.

        Raises:
            ValueError: If match_id doesn't exist or lacks team data.
        """
        match = self.db.get_match(match_id)
        if not match:
            raise ValueError(f"Match {match_id} not found in database")

        home_team = self.db.get_team(match["home_team_id"])
        away_team = self.db.get_team(match["away_team_id"])
        if not home_team or not away_team:
            raise ValueError(
                f"Team data missing for match {match_id}: "
                f"home={match['home_team_id']}, away={match['away_team_id']}"
            )

        league_id = match["league_id"]
        season = match.get("season", "")

        # ── Run all factors ────────────────────────────────────
        factor_results: list[FactorResult] = []
        for name, func in ALL_FACTORS.items():
            try:
                result = func(
                    db=self.db,
                    home_id=match["home_team_id"],
                    away_id=match["away_team_id"],
                    league_id=league_id,
                    season=season,
                )
                factor_results.append(result)
            except Exception as e:
                logger.warning(
                    "Factor '%s' failed for match %d: %s", name, match_id, e
                )
                factor_results.append(FactorResult(
                    name=name, score=0.0, confidence=0.0,
                    rationale=f"Error: {e}", data_points={},
                ))

        # ── Aggregate scores ───────────────────────────────────
        overall_score, overall_confidence = self._aggregate(factor_results)

        # ── Risk flags ─────────────────────────────────────────
        risk_flags = self._assess_risks(factor_results, overall_confidence)

        # ── Data quality ───────────────────────────────────────
        data_quality = self._assess_data_quality(factor_results)

        # ── Gather patterns for market models ──────────────────
        patterns = self._gather_patterns(factor_results)

        # ── Evaluate markets ───────────────────────────────────
        market_recs = evaluate_all_markets(
            overall_score=overall_score,
            factors=factor_results,
            patterns=patterns,
        )

        # ── Generate summary ───────────────────────────────────
        league_name = self._get_league_name(league_id)
        summary = self._generate_summary(
            home_team["name"], away_team["name"],
            overall_score, overall_confidence,
            factor_results, data_quality,
        )

        return MatchAnalysis(
            match_id=match_id,
            home_team=home_team["name"],
            away_team=away_team["name"],
            league=league_name,
            match_date=match.get("date", ""),
            factor_results=factor_results,
            overall_home_score=round(overall_score, 4),
            overall_confidence=round(overall_confidence, 4),
            market_recommendations=market_recs,
            risk_flags=risk_flags,
            data_quality=data_quality,
            summary=summary,
        )

    # ───────────────────────────────────────────────────────────
    #  Batch analysis
    # ───────────────────────────────────────────────────────────

    def analyze_matchweek(
        self,
        league_slug: str | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> list[MatchAnalysis]:
        """Analyse all upcoming matches (optionally filtered by league/date).

        Args:
            league_slug: Filter to a specific league.
            from_date: Start of date window (ISO 8601).
            to_date: End of date window (ISO 8601).

        Returns:
            List of MatchAnalysis, sorted by match date then league.
        """
        league_id = None
        if league_slug:
            league = self.db.get_league(league_slug)
            if league:
                league_id = league["id"]
            else:
                logger.warning("League '%s' not found", league_slug)

        matches = self.db.get_upcoming_matches(
            league_id=league_id,
            from_date=from_date,
            to_date=to_date,
        )

        if not matches:
            logger.info("No upcoming matches found for the given criteria")
            return []

        analyses: list[MatchAnalysis] = []
        for match in matches:
            try:
                analysis = self.analyze_match(match["id"])
                analyses.append(analysis)
            except Exception as e:
                logger.warning("Failed to analyse match %d: %s", match["id"], e)

        # Sort by date, then league
        analyses.sort(key=lambda a: (a.match_date, a.league))
        return analyses

    def analyze_specific_match(
        self, home_team_query: str, away_team_query: str
    ) -> MatchAnalysis | None:
        """Find and analyse a specific fixture by team names.

        Does a fuzzy search through upcoming matches.

        Args:
            home_team_query: Search term for home team.
            away_team_query: Search term for away team.

        Returns:
            MatchAnalysis if found, None otherwise.
        """
        matches = self.db.get_upcoming_matches()

        for match in matches:
            home = self.db.get_team(match["home_team_id"])
            away = self.db.get_team(match["away_team_id"])
            if not home or not away:
                continue

            home_name = home["name"].lower()
            away_name = away["name"].lower()
            q_home = home_team_query.lower()
            q_away = away_team_query.lower()

            if (q_home in home_name or home_name in q_home) and \
               (q_away in away_name or away_name in q_away):
                return self.analyze_match(match["id"])

        logger.info(
            "No upcoming match found matching '%s vs %s'",
            home_team_query, away_team_query,
        )
        return None

    # ───────────────────────────────────────────────────────────
    #  Picks filtering
    # ───────────────────────────────────────────────────────────

    def get_high_confidence_picks(
        self,
        analyses: list[MatchAnalysis],
        min_confidence: str = "HIGH",
    ) -> list[tuple[MatchAnalysis, MarketRecommendation]]:
        """Filter recommendations across all analyses by confidence level.

        Args:
            analyses: List of match analyses.
            min_confidence: Minimum confidence level (``"HIGH"``, ``"MEDIUM"``).

        Returns:
            List of (analysis, recommendation) tuples, sorted by probability.
        """
        level_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
        min_level = level_order.get(min_confidence, 1)

        picks: list[tuple[MatchAnalysis, MarketRecommendation]] = []
        for analysis in analyses:
            for rec in analysis.market_recommendations:
                rec_level = level_order.get(rec.confidence, 2)
                if rec_level <= min_level:
                    picks.append((analysis, rec))

        picks.sort(key=lambda p: (-p[1].probability, p[1].confidence))
        return picks

    def suggest_parlay(
        self,
        analyses: list[MatchAnalysis],
        size: int = 50,
        min_confidence: str = "MEDIUM",
    ) -> list[tuple[MatchAnalysis, MarketRecommendation]]:
        """Suggest the best N picks for a parlay.

        Strategy:
        1. Only consider picks at or above min_confidence.
        2. Prefer the safest (highest probability) pick from each match.
        3. Diversify across leagues where possible.
        4. Sort by probability descending.

        Args:
            analyses: List of match analyses.
            size: Target number of picks.
            min_confidence: Minimum confidence threshold.

        Returns:
            List of (analysis, recommendation) tuples forming the parlay.
        """
        # Get the best pick per match
        best_per_match: list[tuple[MatchAnalysis, MarketRecommendation]] = []
        level_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
        min_level = level_order.get(min_confidence, 1)

        for analysis in analyses:
            eligible = [
                rec for rec in analysis.market_recommendations
                if level_order.get(rec.confidence, 2) <= min_level
            ]
            if eligible:
                # Pick the highest probability recommendation
                best = max(eligible, key=lambda r: r.probability)
                best_per_match.append((analysis, best))

        # Sort by probability (safest first)
        best_per_match.sort(key=lambda p: -p[1].probability)

        # Take up to size
        parlay = best_per_match[:size]

        # Calculate combined probability
        if parlay:
            combined_prob = 1.0
            for _, rec in parlay:
                combined_prob *= rec.probability
            logger.info(
                "Parlay of %d picks: combined probability %.6f",
                len(parlay), combined_prob,
            )

        return parlay

    # ───────────────────────────────────────────────────────────
    #  Internal methods
    # ───────────────────────────────────────────────────────────

    def _aggregate(
        self, factor_results: list[FactorResult]
    ) -> tuple[float, float]:
        """Compute weighted aggregate score and confidence.

        Formula:
            score = Σ(factor_score × weight × confidence)
                  / Σ(weight × confidence)
        """
        numerator = 0.0
        denominator = 0.0

        for result in factor_results:
            weight = self.weights.get(result.name, 0.0)
            w = weight * result.confidence
            numerator += result.score * w
            denominator += w

        if denominator < 1e-9:
            return 0.0, 0.0

        overall_score = numerator / denominator

        # Average confidence weighted by factor weights
        conf_num = sum(
            result.confidence * self.weights.get(result.name, 0.0)
            for result in factor_results
        )
        conf_den = sum(
            self.weights.get(result.name, 0.0) for result in factor_results
        )
        overall_confidence = conf_num / conf_den if conf_den > 0 else 0.0

        return overall_score, overall_confidence

    def _assess_risks(
        self, factor_results: list[FactorResult], confidence: float
    ) -> list[str]:
        """Generate risk flags based on factor analysis."""
        flags: list[str] = []

        # Low data quality
        low_conf_count = sum(1 for f in factor_results if f.confidence < 0.3)
        if low_conf_count >= 6:
            flags.append(
                f"⚠️ Sparse data: {low_conf_count}/{len(factor_results)} "
                "factors have low confidence"
            )

        # Conflicting signals
        positive = sum(1 for f in factor_results if f.score > 0.2 and f.confidence > 0.3)
        negative = sum(1 for f in factor_results if f.score < -0.2 and f.confidence > 0.3)
        if positive >= 3 and negative >= 3:
            flags.append("⚠️ Conflicting signals: factors disagree on favourite")

        # H2H upset history
        h2h = next((f for f in factor_results if f.name == "head_to_head"), None)
        if h2h and h2h.data_points.get("upset_rate", 0) > 0.3:
            flags.append("⚠️ High H2H upset rate — historical form may not hold")

        # Rivalry / derby
        rivalry = next((f for f in factor_results if f.name == "rivalry"), None)
        if rivalry and rivalry.confidence > 0.5 and abs(rivalry.score) < 0.1:
            flags.append("⚠️ Derby match — unpredictable, form often goes out the window")

        # Fixture congestion
        rest = next((f for f in factor_results if f.name == "rest_days"), None)
        if rest and rest.data_points.get("min_rest", 99) <= 3:
            flags.append("⚠️ Fixture congestion — one or both teams may rotate")

        # Momentum divergence
        momentum = next((f for f in factor_results if f.name == "momentum"), None)
        if momentum and abs(momentum.score) > 0.6 and momentum.confidence > 0.4:
            if momentum.score > 0:
                flags.append("📈 Strong home momentum — riding a wave")
            else:
                flags.append("📈 Strong away momentum — the visitors are surging")

        return flags

    def _assess_data_quality(self, factor_results: list[FactorResult]) -> str:
        """Classify overall data quality based on average confidence."""
        avg_conf = sum(f.confidence for f in factor_results) / len(factor_results) \
            if factor_results else 0.0

        if avg_conf >= 0.7:
            return "HIGH"
        if avg_conf >= 0.4:
            return "MEDIUM"
        return "LOW"

    def _gather_patterns(
        self, factor_results: list[FactorResult]
    ) -> dict[str, Any]:
        """Extract historical pattern data from factor results for market models.

        Key names are aligned to the actual data_points keys emitted by each
        factor function after Phase 1 enrichment.
        """
        patterns: dict[str, Any] = {}

        # --- Scoring patterns (BTTS / O/U rates) ---
        sp = next(
            (f for f in factor_results if f.name == "scoring_patterns"), None
        )
        if sp and sp.data_points:
            dp = sp.data_points
            # Poisson-based probabilities (new keys from enrichment)
            patterns["btts_rate"] = dp.get("btts_prob") or dp.get("btts_avg_pct")
            patterns["over_2_5_rate"] = dp.get("over_2_5_prob") or dp.get("over_2_5_avg_pct")
            patterns["over_1_5_rate"] = dp.get("over_1_5_prob")
            patterns["over_3_5_rate"] = dp.get("over_3_5_prob")
            patterns["over_0_5_rate"] = dp.get("over_0_5_prob")

        # --- Scoring frequency: top-level GPG keys ---
        scoring = next(
            (f for f in factor_results if f.name == "scoring_frequency"), None
        )
        if scoring and scoring.data_points:
            dp = scoring.data_points
            # Use top-level keys set in Phase 1 enrichment
            patterns["home_gpg"] = dp.get("home_gpg") or dp.get("home", {}).get("avg_goals")
            patterns["away_gpg"] = dp.get("away_gpg") or dp.get("away", {}).get("avg_goals")

        # --- Defensive record: top-level CS rate keys ---
        defence = next(
            (f for f in factor_results if f.name == "defensive_record"), None
        )
        if defence and defence.data_points:
            dp = defence.data_points
            # Use top-level keys set in Phase 1 enrichment
            patterns["home_cs_rate"] = dp.get("home_cs_rate") or dp.get("home", {}).get("clean_sheet_rate")
            patterns["away_cs_rate"] = dp.get("away_cs_rate") or dp.get("away", {}).get("clean_sheet_rate")

        # --- Recent form: win/draw/loss rates ---
        form = next((f for f in factor_results if f.name == "recent_form"), None)
        if form and form.data_points:
            dp = form.data_points
            # recent_form stores win_rate/draw_rate at the top level (not nested)
            h_wr = dp.get("home_win_rate") or (dp.get("home", {}) or {}).get("win_rate")
            a_wr = dp.get("away_win_rate") or (dp.get("away", {}) or {}).get("win_rate")
            h_dr = dp.get("home_draw_rate") or (dp.get("home", {}) or {}).get("draw_rate")
            a_dr = dp.get("away_draw_rate") or (dp.get("away", {}) or {}).get("draw_rate")
            patterns["home_win_rate"] = h_wr
            patterns["away_win_rate"] = a_wr
            if h_dr is not None and a_dr is not None:
                patterns["draw_rate"] = (h_dr + a_dr) / 2.0
            elif h_dr is not None:
                patterns["draw_rate"] = h_dr
            elif a_dr is not None:
                patterns["draw_rate"] = a_dr

        return patterns

    def _get_league_name(self, league_id: int) -> str:
        """Look up league name from ID."""
        for slug, config in LEAGUES.items():
            league = self.db.get_league(slug)
            if league and league["id"] == league_id:
                return config.name
        return f"League {league_id}"

    def _generate_summary(
        self,
        home_name: str,
        away_name: str,
        score: float,
        confidence: float,
        factors: list[FactorResult],
        data_quality: str,
    ) -> str:
        """Generate a natural language summary of the analysis."""
        # Determine who's favoured
        if score > 0.25:
            favour = f"{home_name} are favoured"
            strength = "strongly" if score > 0.5 else "slightly"
        elif score < -0.25:
            favour = f"{away_name} are favoured"
            strength = "strongly" if score < -0.5 else "slightly"
        else:
            favour = "This looks like an evenly-matched contest"
            strength = ""

        # Top factors
        influential = sorted(
            factors,
            key=lambda f: abs(f.score) * f.confidence * self.weights.get(f.name, 0),
            reverse=True,
        )[:3]

        factor_summary = "; ".join(
            f"{f.name.replace('_', ' ').title()} ({f.score:+.2f})"
            for f in influential
        )

        parts = [
            f"{favour}{f' ({strength})' if strength else ''}.",
            f"Overall score: {score:+.2f} (confidence: {confidence:.0%}).",
            f"Data quality: {data_quality}.",
            f"Key factors: {factor_summary}.",
        ]

        return " ".join(parts)
