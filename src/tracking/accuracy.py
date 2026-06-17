"""
Footy Predict — Accuracy Tracker

Computes detailed accuracy metrics and visualisations for the
prediction system's historical performance.

Metrics
-------
- **Dashboard** — Aggregate accuracy stats with market/confidence filters.
- **Brier score** — Probabilistic calibration measure (lower = better).
- **Calibration data** — Predicted-probability buckets vs actual hit rates.
- **ROI simulation** — Hypothetical return-on-investment at unit stake.
- **Streaks** — Current and all-time longest win/loss streaks.
- **Time trend** — Rolling-window accuracy over the most recent predictions.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from rich.console import Console

from src.database import Database
from src.reporting.formatter import ReportFormatter

logger = logging.getLogger(__name__)


class AccuracyTracker:
    """Computes and presents prediction accuracy analytics."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self.formatter = ReportFormatter()
        self._console = Console()

    # ═══════════════════════════════════════════════════════
    #  Dashboard
    # ═══════════════════════════════════════════════════════

    def get_dashboard(
        self,
        market: str | None = None,
        league: str | None = None,
    ) -> dict[str, Any]:
        """
        Return a comprehensive accuracy dashboard.

        Wraps ``Database.get_accuracy_stats`` and enriches the result
        with Brier score, streak data, and calibration curves.

        Args:
            market: Optional market code filter (e.g. ``'over-under-2.5'``).
            league: Optional league slug filter (e.g. ``'serie-a'``).

        Returns:
            Dict with keys ``total``, ``correct``, ``incorrect``,
            ``accuracy_pct``, ``by_confidence``, ``by_market``,
            ``brier_score``, ``streaks``, ``calibration``.
        """
        stats = self.db.get_accuracy_stats(
            market=market, league_slug=league
        )

        # Enrich with extra analytics
        stats["brier_score"] = self.get_brier_score(market=market)
        stats["streaks"] = self.get_streaks()
        stats["calibration"] = self.get_calibration_data()

        return stats

    # ═══════════════════════════════════════════════════════
    #  Brier Score
    # ═══════════════════════════════════════════════════════

    def get_brier_score(self, market: str | None = None) -> float:
        """
        Calculate the Brier score across all recorded predictions.

        The Brier score is the mean squared error between the predicted
        probability and the binary outcome (1 = correct, 0 = incorrect).
        Lower is better; a perfectly calibrated model at 70% confidence
        would have a Brier score of 0.21.

        Args:
            market: Optional market code to restrict calculation.

        Returns:
            Brier score as a float (0.0–1.0), or 0.0 if no data.
        """
        results = self._get_results_with_probabilities(market)
        if not results:
            return 0.0

        total_sq_error = sum(
            (r["probability"] - r["is_correct"]) ** 2 for r in results
        )
        return round(total_sq_error / len(results), 4)

    # ═══════════════════════════════════════════════════════
    #  Calibration
    # ═══════════════════════════════════════════════════════

    def get_calibration_data(self) -> dict[str, Any]:
        """
        Bucket predictions by predicted probability and compute
        the actual hit rate in each bucket.

        Returns:
            Dict mapping bucket labels (e.g. ``'70-80%'``) to dicts::

                {
                    "count": 42,
                    "correct": 30,
                    "actual_rate": 71.4,
                    "predicted_avg": 74.8,
                }
        """
        results = self._get_results_with_probabilities()
        if not results:
            return {}

        buckets: dict[str, dict[str, Any]] = {}
        for lo in range(0, 100, 10):
            hi = lo + 10
            label = f"{lo}-{hi}%"
            in_bucket = [
                r
                for r in results
                if lo / 100.0 <= r["probability"] < hi / 100.0
            ]
            if in_bucket:
                correct = sum(1 for r in in_bucket if r["is_correct"])
                buckets[label] = {
                    "count": len(in_bucket),
                    "correct": correct,
                    "actual_rate": round(correct / len(in_bucket) * 100, 1),
                    "predicted_avg": round(
                        sum(r["probability"] for r in in_bucket)
                        / len(in_bucket)
                        * 100,
                        1,
                    ),
                }
            else:
                buckets[label] = {
                    "count": 0,
                    "correct": 0,
                    "actual_rate": 0.0,
                    "predicted_avg": 0.0,
                }

        return buckets

    # ═══════════════════════════════════════════════════════
    #  ROI Simulation
    # ═══════════════════════════════════════════════════════

    def get_roi_simulation(self, stake: float = 1.0) -> dict[str, Any]:
        """
        Simulate returns if every pick was backed at the system's
        implied odds (1 / probability) with a fixed unit stake.

        Args:
            stake: Unit stake amount (default 1.0).

        Returns:
            Dict with ``total_staked``, ``total_returned``,
            ``net_profit``, ``roi_pct``, ``by_confidence``.
        """
        results = self._get_results_with_probabilities()
        if not results:
            return {
                "total_staked": 0.0,
                "total_returned": 0.0,
                "net_profit": 0.0,
                "roi_pct": 0.0,
                "by_confidence": {},
            }

        total_staked = 0.0
        total_returned = 0.0
        by_conf: dict[str, dict[str, float]] = {}

        for r in results:
            prob = r["probability"]
            odds = 1.0 / prob if prob > 0 else 1.0
            conf = r.get("confidence", "LOW")

            total_staked += stake
            returned = stake * odds if r["is_correct"] else 0.0
            total_returned += returned

            if conf not in by_conf:
                by_conf[conf] = {"staked": 0.0, "returned": 0.0}
            by_conf[conf]["staked"] += stake
            by_conf[conf]["returned"] += returned

        net = total_returned - total_staked
        roi = (net / total_staked * 100) if total_staked else 0.0

        # Add ROI to per-confidence breakdown
        by_conf_result: dict[str, dict[str, float]] = {}
        for conf, data in by_conf.items():
            conf_net = data["returned"] - data["staked"]
            conf_roi = (conf_net / data["staked"] * 100) if data["staked"] else 0.0
            by_conf_result[conf] = {
                "staked": round(data["staked"], 2),
                "returned": round(data["returned"], 2),
                "net_profit": round(conf_net, 2),
                "roi_pct": round(conf_roi, 1),
            }

        return {
            "total_staked": round(total_staked, 2),
            "total_returned": round(total_returned, 2),
            "net_profit": round(net, 2),
            "roi_pct": round(roi, 1),
            "by_confidence": by_conf_result,
        }

    # ═══════════════════════════════════════════════════════
    #  Streaks
    # ═══════════════════════════════════════════════════════

    def get_streaks(self) -> dict[str, Any]:
        """
        Compute current and all-time longest win and loss streaks.

        Returns:
            Dict with ``current_win_streak``, ``current_loss_streak``,
            ``longest_win_streak``, ``longest_loss_streak``.
        """
        results = self._get_results_chronological()
        if not results:
            return {
                "current_win_streak": 0,
                "current_loss_streak": 0,
                "longest_win_streak": 0,
                "longest_loss_streak": 0,
            }

        current_win = 0
        current_loss = 0
        longest_win = 0
        longest_loss = 0
        streak_win = 0
        streak_loss = 0

        for r in results:
            if r["is_correct"]:
                streak_win += 1
                streak_loss = 0
            else:
                streak_loss += 1
                streak_win = 0

            longest_win = max(longest_win, streak_win)
            longest_loss = max(longest_loss, streak_loss)

        # The current streaks are whatever the last contiguous run is
        current_win = streak_win
        current_loss = streak_loss

        return {
            "current_win_streak": current_win,
            "current_loss_streak": current_loss,
            "longest_win_streak": longest_win,
            "longest_loss_streak": longest_loss,
        }

    # ═══════════════════════════════════════════════════════
    #  Time Trend
    # ═══════════════════════════════════════════════════════

    def get_time_trend(self, window: int = 20) -> list[dict[str, Any]]:
        """
        Compute rolling accuracy over time using a sliding window.

        Args:
            window: Number of predictions in each rolling window.

        Returns:
            List of dicts, one per window step::

                {
                    "window_end": 40,    # index of last prediction
                    "accuracy": 72.5,
                    "correct": 14,
                    "total": 20,
                }
        """
        results = self._get_results_chronological()
        if len(results) < window:
            if results:
                correct = sum(1 for r in results if r["is_correct"])
                return [{
                    "window_end": len(results),
                    "accuracy": round(correct / len(results) * 100, 1),
                    "correct": correct,
                    "total": len(results),
                }]
            return []

        trend: list[dict[str, Any]] = []
        for end in range(window, len(results) + 1):
            chunk = results[end - window : end]
            correct = sum(1 for r in chunk if r["is_correct"])
            trend.append({
                "window_end": end,
                "accuracy": round(correct / window * 100, 1),
                "correct": correct,
                "total": window,
            })

        return trend

    # ═══════════════════════════════════════════════════════
    #  Pretty-Print
    # ═══════════════════════════════════════════════════════

    def print_dashboard(
        self,
        market: str | None = None,
        league: str | None = None,
    ) -> None:
        """
        Fetch and render the accuracy dashboard to the terminal
        using Rich formatting.
        """
        stats = self.get_dashboard(market=market, league=league)
        panel = self.formatter.format_accuracy_cli(stats)
        self._console.print(panel)

    # ═══════════════════════════════════════════════════════
    #  Internal Helpers
    # ═══════════════════════════════════════════════════════

    def _get_results_with_probabilities(
        self, market: str | None = None
    ) -> list[dict[str, Any]]:
        """
        Query prediction results joined with their probability and
        confidence from the predictions table.
        """
        with self.db._connect() as conn:
            query = """
                SELECT p.probability, p.confidence, pr.is_correct
                FROM prediction_results pr
                JOIN predictions p ON pr.prediction_id = p.id
            """
            params: list[Any] = []
            if market:
                query += " WHERE p.market = ?"
                params.append(market)

            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]

    def _get_results_chronological(self) -> list[dict[str, Any]]:
        """
        Query prediction results in chronological order (by the
        match date, then prediction ID).
        """
        with self.db._connect() as conn:
            rows = conn.execute(
                """
                SELECT pr.is_correct, p.probability, p.confidence,
                       m.date, pr.recorded_at
                FROM prediction_results pr
                JOIN predictions p ON pr.prediction_id = p.id
                JOIN matches m ON p.match_id = m.id
                ORDER BY m.date ASC, p.id ASC
                """
            ).fetchall()
            return [dict(r) for r in rows]
