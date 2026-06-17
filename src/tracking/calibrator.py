"""
Footy Predict — Weight Calibrator

The learning mechanism that adjusts factor weights based on historical
prediction accuracy.  After ≥100 evaluated predictions the calibrator
can run, measuring which factors contribute to correct vs incorrect
predictions and nudging weights accordingly.

Algorithm
---------
1. For each factor, gather predictions where that factor had a strong
   signal (|score| > 0.3 and confidence > 0.4).
2. Split into correct and incorrect pools.
3. Compare the factor's accuracy contribution to the overall baseline.
4. If above baseline → increase weight slightly (+0.01 to +0.03).
5. If below baseline → decrease weight slightly.
6. Re-normalise all weights to sum to 1.0.
7. Record changes in the ``weight_history`` table.
8. Persist updated weights to ``data/weights.json``.

Adjustments are deliberately small to prevent overfitting — the system
improves gradually over hundreds of predictions.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from src.config import DATA_DIR, FACTOR_WEIGHTS, MIN_PREDICTIONS_FOR_CALIBRATION
from src.database import Database

logger = logging.getLogger(__name__)

WEIGHTS_FILE = DATA_DIR / "weights.json"

# Calibration hyperparameters
MAX_ADJUSTMENT = 0.03       # Maximum weight change per factor per calibration
MIN_WEIGHT = 0.005          # No factor weight drops below this
SIGNAL_THRESHOLD_SCORE = 0.3      # Factor must have |score| > this
SIGNAL_THRESHOLD_CONFIDENCE = 0.4  # Factor must have confidence > this
MIN_SIGNAL_PREDICTIONS = 15       # Need at least this many signal predictions


class Calibrator:
    """Adjusts factor weights based on prediction outcome data.

    Usage::

        db = Database()
        cal = Calibrator(db)
        if cal.can_calibrate():
            report = cal.calibrate()
            for change in report["changes"]:
                print(f"{change['factor']}: {change['old']:.3f} → {change['new']:.3f}")
    """

    def __init__(self, db: Database) -> None:
        self.db = db

    # ───────────────────────────────────────────────────────────
    #  Weight I/O
    # ───────────────────────────────────────────────────────────

    @staticmethod
    def load_weights() -> dict[str, float]:
        """Load weights from file, falling back to config defaults.

        Returns:
            Dict of factor_name → weight (summing to 1.0).
        """
        if WEIGHTS_FILE.exists():
            try:
                with open(WEIGHTS_FILE) as f:
                    loaded = json.load(f)
                # Validate and normalise
                if isinstance(loaded, dict) and len(loaded) > 0:
                    total = sum(loaded.values())
                    if total > 0:
                        return {k: v / total for k, v in loaded.items()}
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to load weights file: %s", e)

        return dict(FACTOR_WEIGHTS)

    @staticmethod
    def save_weights(weights: dict[str, float]) -> None:
        """Persist weights to the JSON file."""
        WEIGHTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(WEIGHTS_FILE, "w") as f:
            json.dump(weights, f, indent=2)
        logger.info("Saved calibrated weights to %s", WEIGHTS_FILE)

    # ───────────────────────────────────────────────────────────
    #  Calibration check
    # ───────────────────────────────────────────────────────────

    def can_calibrate(self) -> bool:
        """Check if we have enough data to run calibration."""
        total = self.db.get_total_predictions()
        if total < MIN_PREDICTIONS_FOR_CALIBRATION:
            logger.info(
                "Need %d predictions to calibrate, have %d",
                MIN_PREDICTIONS_FOR_CALIBRATION, total,
            )
            return False
        return True

    def get_calibration_readiness(self) -> dict[str, Any]:
        """Get a status report on calibration readiness."""
        total = self.db.get_total_predictions()
        stats = self.db.get_accuracy_stats()
        return {
            "total_predictions": total,
            "required": MIN_PREDICTIONS_FOR_CALIBRATION,
            "ready": total >= MIN_PREDICTIONS_FOR_CALIBRATION,
            "progress_pct": min(100.0, (total / MIN_PREDICTIONS_FOR_CALIBRATION) * 100),
            "current_accuracy": stats.get("accuracy_pct", 0.0),
        }

    # ───────────────────────────────────────────────────────────
    #  Main calibration
    # ───────────────────────────────────────────────────────────

    def calibrate(self) -> dict[str, Any]:
        """Run the calibration loop and return a report.

        Returns:
            Dict with keys:
            - ``changes``: list of per-factor adjustments
            - ``old_weights``: weights before calibration
            - ``new_weights``: weights after calibration
            - ``baseline_accuracy``: overall accuracy before calibration

        Raises:
            RuntimeError: If not enough data to calibrate.
        """
        if not self.can_calibrate():
            readiness = self.get_calibration_readiness()
            raise RuntimeError(
                f"Need {MIN_PREDICTIONS_FOR_CALIBRATION} predictions, "
                f"have {readiness['total_predictions']}"
            )

        old_weights = self.load_weights()
        new_weights = dict(old_weights)

        # Get overall accuracy as baseline
        stats = self.db.get_accuracy_stats()
        baseline_accuracy = stats.get("accuracy_pct", 50.0) / 100.0

        # Analyse each factor's contribution
        changes: list[dict[str, Any]] = []
        predictions_with_factors = self._load_predictions_with_factors()

        for factor_name in old_weights:
            factor_result = self._evaluate_factor_contribution(
                factor_name, predictions_with_factors, baseline_accuracy
            )

            if factor_result is None:
                # Not enough signal predictions for this factor
                changes.append({
                    "factor": factor_name,
                    "old": old_weights[factor_name],
                    "new": old_weights[factor_name],
                    "adjustment": 0.0,
                    "reason": "Insufficient signal predictions",
                    "signal_count": 0,
                })
                continue

            adjustment, reason, signal_count, factor_accuracy = factor_result

            # Apply bounded adjustment
            clamped = max(-MAX_ADJUSTMENT, min(MAX_ADJUSTMENT, adjustment))
            new_weight = max(MIN_WEIGHT, old_weights[factor_name] + clamped)
            new_weights[factor_name] = new_weight

            changes.append({
                "factor": factor_name,
                "old": old_weights[factor_name],
                "new": new_weight,
                "adjustment": clamped,
                "reason": reason,
                "signal_count": signal_count,
                "factor_accuracy": round(factor_accuracy, 4),
            })

        # Normalise to sum to 1.0
        total = sum(new_weights.values())
        if total > 0:
            new_weights = {k: v / total for k, v in new_weights.items()}

        # Save and record
        self.save_weights(new_weights)

        for change in changes:
            if abs(change["adjustment"]) > 1e-6:
                self.db.record_weight_change(
                    factor_name=change["factor"],
                    old_weight=change["old"],
                    new_weight=new_weights[change["factor"]],
                    reason=change["reason"],
                )

        report = {
            "changes": changes,
            "old_weights": old_weights,
            "new_weights": new_weights,
            "baseline_accuracy": round(baseline_accuracy, 4),
            "total_predictions_analysed": len(predictions_with_factors),
        }

        logger.info(
            "Calibration complete: %d factors adjusted",
            sum(1 for c in changes if abs(c["adjustment"]) > 1e-6),
        )

        return report

    # ───────────────────────────────────────────────────────────
    #  Internal methods
    # ───────────────────────────────────────────────────────────

    def _load_predictions_with_factors(self) -> list[dict[str, Any]]:
        """Load all evaluated predictions with their factor breakdowns.

        Returns list of dicts with keys:
        - correct: bool
        - factors: dict mapping factor_name → {score, confidence}
        """
        # Get all predictions that have results
        stats = self.db.get_accuracy_stats()
        results: list[dict[str, Any]] = []

        # Query all prediction results from the database
        with self.db._connect() as conn:
            rows = conn.execute("""
                SELECT
                    p.id,
                    p.market_code,
                    p.probability,
                    p.confidence,
                    p.factor_breakdown,
                    pr.correct
                FROM predictions p
                JOIN prediction_results pr ON p.id = pr.prediction_id
                WHERE pr.correct IS NOT NULL
            """).fetchall()

        for row in rows:
            factor_breakdown = {}
            raw_breakdown = row["factor_breakdown"]
            if raw_breakdown:
                try:
                    if isinstance(raw_breakdown, str):
                        factor_breakdown = json.loads(raw_breakdown)
                    else:
                        factor_breakdown = raw_breakdown
                except (json.JSONDecodeError, TypeError):
                    pass

            results.append({
                "prediction_id": row["id"],
                "correct": bool(row["correct"]),
                "market_code": row["market_code"],
                "probability": row["probability"],
                "factors": factor_breakdown,
            })

        return results

    def _evaluate_factor_contribution(
        self,
        factor_name: str,
        predictions: list[dict[str, Any]],
        baseline_accuracy: float,
    ) -> tuple[float, str, int, float] | None:
        """Evaluate how well a factor's strong signals correlate with correct predictions.

        Returns (adjustment, reason, signal_count, factor_accuracy) or None.
        """
        # Find predictions where this factor had a strong signal
        signal_predictions: list[dict[str, Any]] = []

        for pred in predictions:
            factor_data = pred.get("factors", {}).get(factor_name, {})
            if not factor_data:
                continue

            score = abs(factor_data.get("score", 0))
            confidence = factor_data.get("confidence", 0)

            if score > SIGNAL_THRESHOLD_SCORE and confidence > SIGNAL_THRESHOLD_CONFIDENCE:
                signal_predictions.append(pred)

        if len(signal_predictions) < MIN_SIGNAL_PREDICTIONS:
            return None

        # Calculate accuracy when this factor had a strong signal
        correct_count = sum(1 for p in signal_predictions if p["correct"])
        factor_accuracy = correct_count / len(signal_predictions)

        # Compare to baseline
        diff = factor_accuracy - baseline_accuracy

        if diff > 0.05:
            adjustment = min(MAX_ADJUSTMENT, diff * 0.5)
            reason = (
                f"Above baseline by {diff:.1%}: factor accuracy {factor_accuracy:.1%} "
                f"vs baseline {baseline_accuracy:.1%} → increasing weight"
            )
        elif diff < -0.05:
            adjustment = max(-MAX_ADJUSTMENT, diff * 0.5)
            reason = (
                f"Below baseline by {abs(diff):.1%}: factor accuracy {factor_accuracy:.1%} "
                f"vs baseline {baseline_accuracy:.1%} → decreasing weight"
            )
        else:
            adjustment = 0.0
            reason = (
                f"Within baseline (±5%): factor accuracy {factor_accuracy:.1%} "
                f"vs baseline {baseline_accuracy:.1%} → no change"
            )

        return adjustment, reason, len(signal_predictions), factor_accuracy

    # ───────────────────────────────────────────────────────────
    #  Weight history
    # ───────────────────────────────────────────────────────────

    def get_weight_history(self, limit: int = 20) -> list[dict[str, Any]]:
        """Retrieve recent weight change log entries."""
        return self.db.get_weight_history(limit=limit)

    def reset_weights(self) -> dict[str, float]:
        """Reset weights to the config defaults and save.

        Returns:
            The reset weights dict.
        """
        weights = dict(FACTOR_WEIGHTS)
        self.save_weights(weights)
        logger.info("Weights reset to defaults")
        return weights
