"""
Footy Predict — Result Recorder

Evaluates predictions against actual match outcomes once a fixture
has finished.  For every prediction attached to a completed match the
recorder determines the actual result in the relevant market and
records whether the pick was correct via ``Database.record_result``.

Supported markets
-----------------
- ``1x2``            — Full-time result (Home / Draw / Away)
- ``double-chance``  — Any two of 1X, X2, 12
- ``over-under-X``   — Total goals vs a numeric line
- ``gg-ng``          — Both Teams To Score / No Goal
- ``draw-no-bet``    — Result (void if draw)
- ``win-to-nil``     — Win + clean sheet
- ``win-either-half``— Win at least one half
- ``handicap``       — Goal handicap applied before result check
- ``team-over-under``— Individual team goals vs line
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from src.database import Database

logger = logging.getLogger(__name__)


class ResultRecorder:
    """Records and evaluates prediction outcomes for completed matches."""

    def __init__(self, db: Database) -> None:
        self.db = db

    # ═══════════════════════════════════════════════════════
    #  Public API
    # ═══════════════════════════════════════════════════════

    def record_match_results(self, match_id: int) -> dict[str, Any]:
        """
        Evaluate every prediction for *match_id* against the actual
        scoreline and persist the results.

        Args:
            match_id: Internal match ID (must be FINISHED with scores).

        Returns:
            Summary dict::

                {
                    "match_id": 42,
                    "predictions_evaluated": 5,
                    "correct": 3,
                    "incorrect": 2,
                    "void": 0,
                    "details": [ ... per-prediction dicts ... ]
                }

        Raises:
            ValueError: If the match is not found or not finished.
        """
        match = self.db.get_match(match_id)
        if not match:
            raise ValueError(f"Match {match_id} not found")
        if match["status"] != "FINISHED":
            raise ValueError(
                f"Match {match_id} is not FINISHED (status={match['status']})"
            )
        if match.get("home_score") is None or match.get("away_score") is None:
            raise ValueError(f"Match {match_id} has no final score recorded")

        predictions = self.db.get_predictions_for_match(match_id)
        summary: dict[str, Any] = {
            "match_id": match_id,
            "predictions_evaluated": 0,
            "correct": 0,
            "incorrect": 0,
            "void": 0,
            "details": [],
        }

        for pred in predictions:
            actual_outcome, is_correct = self._evaluate_prediction(pred, match)

            if actual_outcome == "VOID":
                summary["void"] += 1
            elif is_correct:
                summary["correct"] += 1
            else:
                summary["incorrect"] += 1

            summary["predictions_evaluated"] += 1

            # Persist
            self.db.record_result(
                prediction_id=pred["id"],
                actual_outcome=actual_outcome,
                is_correct=is_correct,
            )

            summary["details"].append({
                "prediction_id": pred["id"],
                "market": pred["market"],
                "pick": pred["pick"],
                "actual_outcome": actual_outcome,
                "is_correct": is_correct,
            })

        logger.info(
            "Recorded results for match %d: %d/%d correct",
            match_id,
            summary["correct"],
            summary["predictions_evaluated"],
        )
        return summary

    def auto_record_all(self) -> dict[str, int]:
        """
        Find every FINISHED match with un-recorded prediction results
        and evaluate them all.

        Returns:
            Aggregate counts::

                {
                    "matches_processed": 12,
                    "predictions_evaluated": 54,
                    "correct": 31,
                    "incorrect": 20,
                    "void": 3,
                    "errors": 0,
                }
        """
        matches = self.db.get_finished_matches_without_results()
        totals: dict[str, int] = {
            "matches_processed": 0,
            "predictions_evaluated": 0,
            "correct": 0,
            "incorrect": 0,
            "void": 0,
            "errors": 0,
        }

        for match in matches:
            try:
                result = self.record_match_results(match["id"])
                totals["matches_processed"] += 1
                totals["predictions_evaluated"] += result["predictions_evaluated"]
                totals["correct"] += result["correct"]
                totals["incorrect"] += result["incorrect"]
                totals["void"] += result["void"]
            except Exception as exc:
                logger.error(
                    "Error recording results for match %d: %s",
                    match["id"],
                    exc,
                )
                totals["errors"] += 1

        logger.info(
            "Auto-record complete: %d matches, %d predictions "
            "(%d correct, %d incorrect, %d void, %d errors)",
            totals["matches_processed"],
            totals["predictions_evaluated"],
            totals["correct"],
            totals["incorrect"],
            totals["void"],
            totals["errors"],
        )
        return totals

    # ═══════════════════════════════════════════════════════
    #  Evaluation Logic
    # ═══════════════════════════════════════════════════════

    def _evaluate_prediction(
        self, prediction: dict, match: dict
    ) -> tuple[str, bool]:
        """
        Determine the actual outcome for a prediction's market and
        whether the pick was correct.

        Args:
            prediction: A prediction dict (from ``get_predictions_for_match``).
            match: A match dict with at least ``home_score``, ``away_score``,
                   ``ht_home_score``, ``ht_away_score``.

        Returns:
            ``(actual_outcome, is_correct)`` where *actual_outcome* is a
            human-readable string and *is_correct* is ``True`` / ``False``.
            For void bets (e.g. draw-no-bet when the match draws),
            returns ``("VOID", True)`` (treated as a push, not a loss).
        """
        market = prediction["market"]
        pick = prediction["pick"]
        hs = match["home_score"]
        as_ = match["away_score"]
        ht_hs = match.get("ht_home_score")
        ht_as = match.get("ht_away_score")

        if market == "1x2":
            return self._eval_1x2(pick, hs, as_)

        if market == "double-chance":
            return self._eval_double_chance(pick, hs, as_)

        if market.startswith("over-under"):
            return self._eval_over_under(market, pick, hs, as_)

        if market == "gg-ng":
            return self._eval_gg_ng(pick, hs, as_)

        if market == "draw-no-bet":
            return self._eval_draw_no_bet(pick, hs, as_)

        if market == "win-to-nil":
            return self._eval_win_to_nil(pick, hs, as_, match)

        if market == "win-either-half":
            return self._eval_win_either_half(pick, hs, as_, ht_hs, ht_as, match)

        if market == "handicap":
            return self._eval_handicap(prediction, pick, hs, as_)

        if market == "team-over-under":
            return self._eval_team_over_under(prediction, pick, hs, as_, match)

        logger.warning("Unknown market '%s' — marking as VOID", market)
        return ("VOID", True)

    # ── Individual market evaluators ────────────────────────

    @staticmethod
    def _eval_1x2(pick: str, hs: int, as_: int) -> tuple[str, bool]:
        """Full-time result: '1' (Home), 'X' (Draw), '2' (Away)."""
        norm_pick = pick.upper()
        if norm_pick.startswith("1"):
            norm_pick = "1"
        elif norm_pick.startswith("X"):
            norm_pick = "X"
        elif norm_pick.startswith("2"):
            norm_pick = "2"

        if hs > as_:
            actual = "1"
        elif hs == as_:
            actual = "X"
        else:
            actual = "2"
        return actual, norm_pick == actual

    @staticmethod
    def _eval_double_chance(pick: str, hs: int, as_: int) -> tuple[str, bool]:
        """
        Double chance: '1X', 'X2', or '12'.
        The pick covers two of the three 1X2 outcomes.
        """
        if hs > as_:
            ft_result = "1"
        elif hs == as_:
            ft_result = "X"
        else:
            ft_result = "2"

        normalised = pick.upper()
        is_correct = ft_result in normalised
        return ft_result, is_correct

    @staticmethod
    def _eval_over_under(
        market: str, pick: str, hs: int, as_: int
    ) -> tuple[str, bool]:
        """
        Over/Under X goals.  Market code encodes the line,
        e.g. ``over-under-2.5``.
        """
        # Extract numeric line from market code
        match_obj = re.search(r"(\d+\.?\d*)", market.replace("over-under-", ""))
        if not match_obj:
            return "VOID", True
        line = float(match_obj.group(1))
        total = hs + as_
        actual = "Over" if total > line else "Under"
        return (
            f"{actual} {line} (total={total})",
            pick.lower().startswith(actual.lower()),
        )

    @staticmethod
    def _eval_gg_ng(pick: str, hs: int, as_: int) -> tuple[str, bool]:
        """Both Teams To Score (GG) or No Goal by at least one side (NG)."""
        norm_pick = pick.upper()
        if norm_pick.startswith("GG"):
            norm_pick = "GG"
        elif norm_pick.startswith("NG"):
            norm_pick = "NG"

        both_scored = hs > 0 and as_ > 0
        actual = "GG" if both_scored else "NG"
        return actual, norm_pick == actual

    @staticmethod
    def _eval_draw_no_bet(pick: str, hs: int, as_: int) -> tuple[str, bool]:
        """Draw No Bet: void (push) on a draw."""
        norm_pick = pick.lower()
        if "home" in norm_pick:
            norm_pick = "home"
        elif "away" in norm_pick:
            norm_pick = "away"

        if hs == as_:
            return "VOID", True  # push
        actual = "Home" if hs > as_ else "Away"
        return actual, norm_pick == actual.lower()

    @staticmethod
    def _eval_win_to_nil(
        pick: str, hs: int, as_: int, match: dict
    ) -> tuple[str, bool]:
        """Evaluate a Win to Nil pick.

        Supports the legacy ``'Home'`` / ``'Away'`` pick format and the new
        binary ``Yes`` / ``No`` framing introduced in Phase A.

        New pick strings:
        - ``"Home Win to Nil: Yes"``  — home wins and away scores 0
        - ``"Home Win to Nil: No"``   — away scores at least 1 (regardless of result)
        - ``"Away Win to Nil: Yes"``  — away wins and home scores 0
        - ``"Away Win to Nil: No"``   — home scores at least 1 (regardless of result)

        Args:
            pick: The pick string as stored in the database.
            hs: Home goals scored.
            as_: Away goals scored.
            match: Full match dict (unused but kept for signature parity).

        Returns:
            Tuple of (actual outcome description, whether the pick was correct).
        """
        lower = pick.lower()

        home_wtn = hs > as_ and as_ == 0   # home wins to nil
        away_wtn = as_ > hs and hs == 0    # away wins to nil

        # ── New Yes/No binary framing ──────────────────────────────
        if "yes" in lower or "no" in lower:
            is_yes = "yes" in lower
            if "home" in lower:
                # "Home Win to Nil: Yes" wins if home_wtn; "No" wins if away scored
                if is_yes:
                    outcome = "Home WtN achieved" if home_wtn else "Home WtN not achieved"
                    return outcome, home_wtn
                else:
                    # No wins when away scores >= 1
                    away_scored = as_ >= 1
                    outcome = f"Away scored {as_} goal(s)" if away_scored else "Away blanked (No loses)"
                    return outcome, away_scored
            else:  # away team
                if is_yes:
                    outcome = "Away WtN achieved" if away_wtn else "Away WtN not achieved"
                    return outcome, away_wtn
                else:
                    # No wins when home scores >= 1
                    home_scored = hs >= 1
                    outcome = f"Home scored {hs} goal(s)" if home_scored else "Home blanked (No loses)"
                    return outcome, home_scored

        # ── Legacy "Home" / "Away" format ─────────────────────────
        if "home" in lower:
            actual = "Home WTN" if home_wtn else ("Away WTN" if away_wtn else "No WTN")
            return actual, home_wtn
        else:
            actual = "Away WTN" if away_wtn else ("Home WTN" if home_wtn else "No WTN")
            return actual, away_wtn

    @staticmethod
    def _eval_win_either_half(
        pick: str,
        hs: int,
        as_: int,
        ht_hs: int | None,
        ht_as: int | None,
        match: dict,
    ) -> tuple[str, bool]:
        """
        Win Either Half: did the picked team win at least one half?
        Requires half-time scores. Pick format: 'Home' or 'Away'.
        """
        norm_pick = pick.lower()
        if "home" in norm_pick:
            norm_pick = "home"
        elif "away" in norm_pick:
            norm_pick = "away"

        if ht_hs is None or ht_as is None:
            return "VOID", True  # can't evaluate without HT scores

        # Second-half goals
        sh_hs = hs - ht_hs
        sh_as = as_ - ht_as

        home_won_h1 = ht_hs > ht_as
        home_won_h2 = sh_hs > sh_as
        away_won_h1 = ht_as > ht_hs
        away_won_h2 = sh_as > sh_hs

        if norm_pick == "home":
            won_half = home_won_h1 or home_won_h2
            actual = "Home won half" if won_half else "Home lost both halves"
            return actual, won_half
        else:
            won_half = away_won_h1 or away_won_h2
            actual = "Away won half" if won_half else "Away lost both halves"
            return actual, won_half

    @staticmethod
    def _eval_handicap(
        prediction: dict, pick: str, hs: int, as_: int
    ) -> tuple[str, bool]:
        """
        Handicap result: apply handicap to score then check 1X2.

        The handicap value is expected in ``prediction['supporting_stats']``
        under key ``'handicap_value'``, e.g. ``-1.5`` or ``+1``.
        Pick format: '1' / 'X' / '2' (after handicap applied).
        """
        norm_pick = pick.upper()
        if "HOME" in norm_pick:
            norm_pick = "1"
        elif "AWAY" in norm_pick:
            norm_pick = "2"
        elif norm_pick.startswith("1"):
            norm_pick = "1"
        elif norm_pick.startswith("2"):
            norm_pick = "2"
        elif norm_pick.startswith("X"):
            norm_pick = "X"

        stats = prediction.get("supporting_stats", {})
        if isinstance(stats, str):
            try:
                stats = json.loads(stats)
            except (json.JSONDecodeError, TypeError):
                stats = {}

        # Support handicap_line as used by model_handicap
        handicap = float(stats.get("handicap_value", stats.get("handicap_line", 0)))

        # Handicap applied to the home team
        adj_hs = hs + handicap
        if adj_hs > as_:
            actual = "1"
        elif adj_hs == as_:
            actual = "X"
        else:
            actual = "2"

        return f"{actual} (adj {adj_hs}-{as_})", norm_pick == actual

    @staticmethod
    def _eval_team_over_under(
        prediction: dict,
        pick: str,
        hs: int,
        as_: int,
        match: dict,
    ) -> tuple[str, bool]:
        """
        Team Over/Under: check a specific team's goals against a line.
        Checks supporting_stats for side and line, falling back to parsing the pick string.
        """
        stats = prediction.get("supporting_stats", {})
        if isinstance(stats, str):
            try:
                stats = json.loads(stats)
            except (json.JSONDecodeError, TypeError):
                stats = {}

        pick_lower = pick.lower()
        
        # Determine side (home/away)
        if "team_side" in stats:
            team_side = stats["team_side"].lower()
        else:
            team_side = "home" if "home" in pick_lower else "away"

        # Determine line
        if "line" in stats:
            line = float(stats["line"])
        else:
            match_obj = re.search(r"(\d+\.?\d*)", pick_lower)
            line = float(match_obj.group(1)) if match_obj else 0.5

        is_over = "over" in pick_lower
        team_goals = hs if team_side == "home" else as_
        actual_over = team_goals > line
        
        is_correct = (is_over and actual_over) or (not is_over and not actual_over)
        actual_outcome = f"{team_side.capitalize()} {'Over' if actual_over else 'Under'} {line:g} (scored {team_goals})"
        
        return actual_outcome, is_correct
