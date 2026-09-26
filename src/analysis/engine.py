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
        """Generate a narrative commentary paragraph for a match analysis.

        Draws on actual factor data_points (form strings, PPG, H2H records,
        league positions, momentum) rather than just restating numbers.
        Returns a 3–5 sentence paragraph suitable for display to a human reader.
        """

        def _dp(name: str) -> dict:
            for f in factors:
                if f.name == name:
                    return f.data_points or {}
            return {}

        def _score(name: str) -> float:
            for f in factors:
                if f.name == name:
                    return f.score or 0.0
            return 0.0

        rf = _dp("recent_form")
        hf = _dp("home_form")
        af = _dp("away_form")
        h2h = _dp("head_to_head")
        pos = _dp("league_position")
        mom = _dp("momentum")
        def_dp = _dp("defensive_record")
        sf = _dp("scoring_frequency")

        sentences: list[str] = []

        # ── 1. Opening: overall lean + who's doing it ─────────
        if score > 0.35:
            opener = f"{home_name} hold a clear advantage heading into this fixture."
        elif score > 0.12:
            opener = f"{home_name} have the edge, though this one is closer than it looks."
        elif score < -0.35:
            opener = f"{away_name} carry the stronger form profile into this match."
        elif score < -0.12:
            opener = f"{away_name} have a slight advantage, but the margins are thin."
        else:
            opener = f"This looks like an evenly matched contest — the data doesn't favour either side clearly."
        sentences.append(opener)

        # ── 2. Form context for both teams ────────────────────
        h_rf = rf.get("home") or {}
        a_rf = rf.get("away") or {}
        h_form = h_rf.get("form_string") or pos.get("home_form_string") or ""
        a_form = a_rf.get("form_string") or pos.get("away_form_string") or ""
        h_ppg = h_rf.get("ppg")
        a_ppg = a_rf.get("ppg")

        # Home form sentence
        if h_form or h_ppg is not None:
            parts = []
            if h_form:
                parts.append(f"in {h_form} form")
            if h_ppg is not None:
                parts.append(f"{h_ppg:.1f} PPG")
            # Home venue record
            hf_wins = hf.get("home_wins")
            hf_played = hf.get("home_played")
            hf_gpg = hf.get("gpg")
            if hf_wins is not None and hf_played and hf_played > 0:
                wr = round(hf_wins / hf_played * 100)
                parts.append(f"winning {wr}% of home matches")
            if hf_gpg is not None:
                parts.append(f"scoring {hf_gpg:.1f} per game at home")
            sentences.append(f"{home_name} are {', '.join(parts)}." if parts else "")

        # Away form sentence
        if a_form or a_ppg is not None:
            parts = []
            if a_form:
                parts.append(f"in {a_form} form")
            if a_ppg is not None:
                parts.append(f"{a_ppg:.1f} PPG")
            af_wins = af.get("away_wins")
            af_played = af.get("away_played")
            af_gpg = af.get("gpg")
            if af_wins is not None and af_played and af_played > 0:
                wr = round(af_wins / af_played * 100)
                parts.append(f"winning {wr}% of away games")
            # Scoring struggles away
            af_blank = af.get("failed_to_score")
            if af_blank is not None and af_played and af_played > 0 and af_blank > af_played * 0.4:
                parts.append(f"failing to score in {af_blank} of {af_played} away trips")
            if af_gpg is not None:
                parts.append(f"scoring {af_gpg:.1f} per game on the road")
            sentences.append(f"{away_name} are {', '.join(parts)}." if parts else "")

        # ── 3. League position context (if available) ─────────
        h_pos = pos.get("home_position")
        a_pos = pos.get("away_position")
        h_pts = pos.get("home_points")
        a_pts = pos.get("away_points")
        pts_gap = pos.get("points_gap")
        if h_pos and a_pos:
            if pts_gap and pts_gap >= 5:
                gap_str = f"{pts_gap} points separating them in the table"
                sentences.append(
                    f"The table reflects the difference too — {home_name} are {h_pos}{'st' if h_pos == 1 else 'nd' if h_pos == 2 else 'rd' if h_pos == 3 else 'th'} "
                    f"vs {away_name} in {a_pos}{'st' if a_pos == 1 else 'nd' if a_pos == 2 else 'rd' if a_pos == 3 else 'th'}, with {gap_str}."
                )
            elif h_pts is not None and a_pts is not None and abs(h_pts - a_pts) <= 2:
                sentences.append(
                    f"In the table, they're level — {home_name} {h_pos}{'st' if h_pos == 1 else 'nd' if h_pos == 2 else 'rd' if h_pos == 3 else 'th'} "
                    f"on {h_pts} pts, {away_name} {a_pos}{'st' if a_pos == 1 else 'nd' if a_pos == 2 else 'rd' if a_pos == 3 else 'th'} "
                    f"on {a_pts} pts — which makes this a genuinely open match."
                )

        # ── 4. Head-to-head context ───────────────────────────
        h2h_total = h2h.get("total_meetings")
        h2h_hw = h2h.get("home_wins")
        h2h_aw = h2h.get("away_wins")
        h2h_d = h2h.get("draws")
        h2h_scorelines = h2h.get("last_5_scorelines") or []
        h2h_avg_goals = h2h.get("avg_goals_h2h")
        h2h_trend = h2h.get("recent_h2h_trend") or ""
        if h2h_total and h2h_total >= 3 and h2h_hw is not None and h2h_aw is not None:
            if h2h_hw > h2h_aw + 2:
                trend_note = f"{home_name} have dominated this fixture historically ({h2h_hw}W–{h2h_d}D–{h2h_aw}L"
            elif h2h_aw > h2h_hw + 2:
                trend_note = f"{away_name} have the better of this head-to-head ({h2h_hw}W–{h2h_d}D–{h2h_aw}L"
            else:
                trend_note = f"These sides are evenly matched head-to-head ({h2h_hw}W–{h2h_d}D–{h2h_aw}L"
            if h2h_avg_goals is not None:
                trend_note += f", averaging {h2h_avg_goals:.1f} goals per meeting"
            if h2h_scorelines:
                trend_note += f"; recent: {', '.join(h2h_scorelines[:3])}"
            trend_note += ")."
            sentences.append(trend_note)

        # ── 5. Momentum note ─────────────────────────────────
        mom_score = _score("momentum")
        h_mom = mom.get("home") or {}
        a_mom = mom.get("away") or {}
        h_trend_dir = h_mom.get("trend_direction") or ""
        a_trend_dir = a_mom.get("trend_direction") or ""

        if abs(mom_score) > 0.3:
            if mom_score > 0 and h_trend_dir == "improving":
                sentences.append(f"{home_name} are hitting form at the right time — momentum is in their favour going into this.")
            elif mom_score > 0 and a_trend_dir in ("declining", "dropping"):
                sentences.append(f"{away_name} have shown signs of a drop-off in recent weeks, which works in {home_name}'s favour.")
            elif mom_score < 0 and a_trend_dir == "improving":
                sentences.append(f"{away_name} look to be peaking at the right time — their trajectory over the last month points upward.")
            elif mom_score < 0 and h_trend_dir in ("declining", "dropping"):
                sentences.append(f"{home_name} have lost ground in recent weeks, which gives {away_name} more reason to be confident.")

        # ── 6. Defensive matchup note ─────────────────────────
        home_cpg = def_dp.get("home_cpg") or def_dp.get("home_cpg_home")
        away_cpg = def_dp.get("away_cpg") or def_dp.get("away_cpg_away")
        home_cs = def_dp.get("home_cs_rate") or def_dp.get("home_cs_rate_home")
        away_cs = def_dp.get("away_cs_rate") or def_dp.get("away_cs_rate_away")

        def_notes = []
        if home_cpg is not None and home_cpg < 0.8:
            def_notes.append(f"{home_name}'s defence has been excellent at home, conceding just {home_cpg:.1f} per game")
        elif home_cpg is not None and home_cpg > 1.8:
            def_notes.append(f"{home_name} have been vulnerable defensively, conceding {home_cpg:.1f} per game")
        if away_cpg is not None and away_cpg > 1.8:
            def_notes.append(f"{away_name} have struggled defensively away from home ({away_cpg:.1f} conceded per game)")
        elif away_cpg is not None and away_cpg < 0.9:
            def_notes.append(f"{away_name} have kept things tight on the road, conceding just {away_cpg:.1f} per game")
        if def_notes:
            sentences.append(". ".join(def_notes) + ".")

        # ── 7. Fallback if very sparse data ──────────────────
        if len(sentences) <= 2:
            sentences.append(
                f"Data coverage is {data_quality.lower()} — treat these signals with appropriate caution "
                f"and weight your own research more heavily for this one."
            )

        # Clean up empty sentences and join
        narrative = " ".join(s for s in sentences if s.strip())
        return narrative

