"""Tests for market model functions.

These tests verify:
- The multi-direction pick counts for each model
- Market codes and pick label contents
- Probability invariants (probabilities sum correctly for complementary picks)
- evaluate_all_markets integration
"""
import pytest
from src.analysis.factors import FactorResult
from src.analysis.markets import (
    _classify_confidence,
    _blend,
    _implied_odds,
    model_1x2,
    model_double_chance,
    model_over_under,
    model_gg_ng,
    model_draw_no_bet,
    model_win_to_nil,
    model_win_either_half,
    model_team_over_under,
    model_handicap,
    evaluate_all_markets,
)


def test_classify_confidence():
    assert _classify_confidence(0.80) == "HIGH"
    assert _classify_confidence(0.65) == "MEDIUM"
    assert _classify_confidence(0.50) == "LOW"


def test_blend():
    # Model-only fallback
    assert _blend(None, 0.7) == 0.7
    # 0.6 * historical + 0.4 * model
    # 0.6 * 0.8 + 0.4 * 0.5 = 0.48 + 0.20 = 0.68
    assert pytest.approx(_blend(0.8, 0.5)) == 0.68


def test_implied_odds():
    assert _implied_odds(0.50) == 2.0
    assert _implied_odds(0.0) == 100.0
    assert _implied_odds(1.0) == 1.01


@pytest.fixture
def sample_factors():
    return [
        FactorResult(
            name="scoring_frequency",
            score=0.5,
            confidence=0.8,
            rationale="Good scoring",
            data_points={"home_gpg": 2.2, "away_gpg": 1.2},
        ),
        FactorResult(
            name="defensive_record",
            score=0.2,
            confidence=0.7,
            rationale="Decent defence",
            data_points={"home_cs_rate": 0.4, "away_cs_rate": 0.2},
        ),
        FactorResult(
            name="goal_timing",
            score=0.1,
            confidence=0.6,
            rationale="Average timing",
            data_points={"home_1h_win_rate": 0.45, "away_1h_win_rate": 0.30},
        ),
    ]


# ─── 1X2 ──────────────────────────────────────────────────────────────────────

def test_model_1x2_emits_all_three(sample_factors):
    """Model must emit exactly 3 picks — one per outcome direction."""
    recs = model_1x2(0.5, sample_factors)
    assert len(recs) == 3
    picks = {r.pick for r in recs}
    assert "1 (Home Win)" in picks
    assert "X (Draw)" in picks
    assert "2 (Away Win)" in picks


def test_model_1x2_probabilities_sum_to_one(sample_factors):
    """Three outcome probabilities should sum to approximately 1."""
    recs = model_1x2(0.5, sample_factors)
    total = sum(r.probability for r in recs)
    assert pytest.approx(total, abs=0.02) == 1.0


def test_model_1x2_market_code(sample_factors):
    recs = model_1x2(0.5, sample_factors)
    assert all(r.market_code == "1x2" for r in recs)


# ─── Double Chance ────────────────────────────────────────────────────────────

def test_model_double_chance_emits_all_three(sample_factors):
    """Model must emit exactly 3 picks — 1X, X2, and 12."""
    recs = model_double_chance(0.5, sample_factors)
    assert len(recs) == 3
    picks = {r.pick for r in recs}
    assert "1X" in picks
    assert "X2" in picks
    assert "12" in picks


def test_model_double_chance_market_code(sample_factors):
    recs = model_double_chance(0.5, sample_factors)
    assert all(r.market_code == "double-chance" for r in recs)


def test_model_double_chance_probabilities(sample_factors):
    """1X, X2, and 12 probabilities must all be > 0 and <= 1."""
    recs = model_double_chance(0.5, sample_factors)
    for r in recs:
        assert 0 < r.probability <= 1.0


# ─── Over/Under ───────────────────────────────────────────────────────────────

def test_model_over_under_emits_both_directions(sample_factors):
    """Model must emit exactly 2 picks — Over and Under."""
    recs = model_over_under(0.5, sample_factors, line=2.5)
    assert len(recs) == 2
    picks = {r.pick for r in recs}
    assert "Over 2.5" in picks
    assert "Under 2.5" in picks


def test_model_over_under_probabilities_complement(sample_factors):
    """Over + Under should sum to 1.0."""
    recs = model_over_under(0.5, sample_factors, line=2.5)
    probs = {r.pick: r.probability for r in recs}
    assert pytest.approx(probs["Over 2.5"] + probs["Under 2.5"], abs=0.001) == 1.0


def test_model_over_under_market_code(sample_factors):
    recs = model_over_under(0.5, sample_factors, line=2.5)
    assert all(r.market_code == "over-under-2.5" for r in recs)


def test_model_over_under_all_lines(sample_factors):
    """Should produce 2 picks for each of the four lines."""
    for line in [0.5, 1.5, 2.5, 3.5]:
        recs = model_over_under(0.5, sample_factors, line=line)
        assert len(recs) == 2, f"Expected 2 picks for O/U {line}"


# ─── GG/NG ───────────────────────────────────────────────────────────────────

def test_model_gg_ng_emits_both(sample_factors):
    recs = model_gg_ng(0.5, sample_factors)
    assert len(recs) == 2
    picks = {r.pick for r in recs}
    assert "GG (Both Teams Score)" in picks
    assert "NG (Clean Sheet Expected)" in picks


def test_model_gg_ng_probabilities_complement(sample_factors):
    recs = model_gg_ng(0.5, sample_factors)
    probs = {r.pick: r.probability for r in recs}
    total = probs["GG (Both Teams Score)"] + probs["NG (Clean Sheet Expected)"]
    assert pytest.approx(total, abs=0.001) == 1.0


def test_model_gg_ng_market_code(sample_factors):
    recs = model_gg_ng(0.5, sample_factors)
    assert all(r.market_code == "gg-ng" for r in recs)


# ─── Draw No Bet ──────────────────────────────────────────────────────────────

def test_model_draw_no_bet_emits_both(sample_factors):
    recs = model_draw_no_bet(0.5, sample_factors)
    assert len(recs) == 2
    picks = {r.pick for r in recs}
    assert "DNB Home" in picks
    assert "DNB Away" in picks


def test_model_draw_no_bet_market_code(sample_factors):
    recs = model_draw_no_bet(0.5, sample_factors)
    assert all(r.market_code == "draw-no-bet" for r in recs)


# ─── Win to Nil ───────────────────────────────────────────────────────────────

def test_model_win_to_nil_emits_four(sample_factors):
    """Model must emit exactly 4 picks — Yes/No for each team."""
    recs = model_win_to_nil(0.5, sample_factors)
    assert len(recs) == 4
    picks = {r.pick for r in recs}
    assert "Home Win to Nil: Yes" in picks
    assert "Home Win to Nil: No" in picks
    assert "Away Win to Nil: Yes" in picks
    assert "Away Win to Nil: No" in picks


def test_model_win_to_nil_no_probabilities_high(sample_factors):
    """The No picks should have higher probability than the Yes picks."""
    recs = model_win_to_nil(0.5, sample_factors)
    probs = {r.pick: r.probability for r in recs}
    assert probs["Home Win to Nil: No"] > probs["Home Win to Nil: Yes"]
    assert probs["Away Win to Nil: No"] > probs["Away Win to Nil: Yes"]


def test_model_win_to_nil_probabilities_complement(sample_factors):
    """Yes + No should sum to 1.0 for each team."""
    recs = model_win_to_nil(0.5, sample_factors)
    probs = {r.pick: r.probability for r in recs}
    assert pytest.approx(
        probs["Home Win to Nil: Yes"] + probs["Home Win to Nil: No"], abs=0.001
    ) == 1.0
    assert pytest.approx(
        probs["Away Win to Nil: Yes"] + probs["Away Win to Nil: No"], abs=0.001
    ) == 1.0


def test_model_win_to_nil_market_code(sample_factors):
    recs = model_win_to_nil(0.5, sample_factors)
    assert all(r.market_code == "win-to-nil" for r in recs)


# ─── Win Either Half ──────────────────────────────────────────────────────────

def test_model_win_either_half_emits_both(sample_factors):
    recs = model_win_either_half(0.5, sample_factors)
    assert len(recs) == 2
    picks = {r.pick for r in recs}
    assert "Home Win Either Half" in picks
    assert "Away Win Either Half" in picks


def test_model_win_either_half_market_code(sample_factors):
    recs = model_win_either_half(0.5, sample_factors)
    assert all(r.market_code == "win-either-half" for r in recs)


# ─── Team Over/Under ─────────────────────────────────────────────────────────

def test_model_team_over_under_emits_four(sample_factors):
    """Model must emit 4 picks — Home Over/Under and Away Over/Under."""
    recs = model_team_over_under(0.5, sample_factors, line=0.5)
    assert len(recs) == 4
    picks = {r.pick for r in recs}
    assert "Home Over 0.5" in picks
    assert "Home Under 0.5" in picks
    assert "Away Over 0.5" in picks
    assert "Away Under 0.5" in picks


def test_model_team_over_under_market_code(sample_factors):
    recs = model_team_over_under(0.5, sample_factors, line=0.5)
    assert all(r.market_code == "team-over-under-0.5" for r in recs)


def test_model_team_over_under_probabilities_complement(sample_factors):
    """Home Over + Home Under should sum to 1.0; same for Away."""
    recs = model_team_over_under(0.5, sample_factors, line=0.5)
    probs = {r.pick: r.probability for r in recs}
    assert pytest.approx(probs["Home Over 0.5"] + probs["Home Under 0.5"], abs=0.001) == 1.0
    assert pytest.approx(probs["Away Over 0.5"] + probs["Away Under 0.5"], abs=0.001) == 1.0


# ─── Handicap ────────────────────────────────────────────────────────────────

def test_model_handicap_emits_multiple_lines(sample_factors):
    """Handicap model exposes up to 3 lines for the favoured team."""
    recs = model_handicap(0.5, sample_factors)
    assert len(recs) >= 1
    assert len(recs) <= 3
    assert all(r.market_code == "handicap" for r in recs)


def test_model_handicap_probabilities_valid(sample_factors):
    recs = model_handicap(0.5, sample_factors)
    for r in recs:
        assert 0 < r.probability <= 1.0


# ─── Evaluate All Markets ─────────────────────────────────────────────────────

def test_evaluate_all_markets_coverage(sample_factors):
    """All core market codes must be present in the output."""
    recs = evaluate_all_markets(0.5, sample_factors)
    codes = {r.market_code for r in recs}
    required = {
        "1x2", "double-chance",
        "over-under-0.5", "over-under-1.5", "over-under-2.5", "over-under-3.5",
        "gg-ng", "draw-no-bet", "win-to-nil", "win-either-half",
        "team-over-under-0.5", "team-over-under-1.5",
        "handicap",
    }
    missing = required - codes
    assert not missing, f"Missing market codes: {missing}"


def test_evaluate_all_markets_count(sample_factors):
    """Total picks should be >= 30 (multi-direction expansion)."""
    recs = evaluate_all_markets(0.5, sample_factors)
    # Minimum expected:
    # 1x2(3) + DC(3) + O/U×4lines×2(8) + GG/NG(2) + DNB(2) + WtN(4) + WEH(2)
    # + TeamOU×2lines×4(8) + Handicap(1-3) = 33–35
    assert len(recs) >= 30


def test_evaluate_all_markets_sorted_confidence(sample_factors):
    """Output must be sorted: HIGH first, then MEDIUM, then LOW."""
    recs = evaluate_all_markets(0.5, sample_factors)
    order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    ranks = [order.get(r.confidence, 3) for r in recs]
    assert ranks == sorted(ranks), "Output is not sorted by confidence tier"
