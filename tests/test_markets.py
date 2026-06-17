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
            data_points={"home_gpg": 2.2, "away_gpg": 1.2}
        ),
        FactorResult(
            name="defensive_record",
            score=0.2,
            confidence=0.7,
            rationale="Decent defence",
            data_points={"home_cs_rate": 0.4, "away_cs_rate": 0.2}
        ),
        FactorResult(
            name="goal_timing",
            score=0.1,
            confidence=0.6,
            rationale="Average timing",
            data_points={"home_scoring_1h": 0.6, "away_scoring_1h": 0.4}
        )
    ]

def test_model_1x2(sample_factors):
    # Overall score = 0.5 (leans home)
    recs = model_1x2(0.5, sample_factors)
    assert len(recs) == 1
    rec = recs[0]
    assert rec.market_code == "1x2"
    assert "1" in rec.pick or "X" in rec.pick or "2" in rec.pick
    assert rec.probability > 0

def test_model_double_chance(sample_factors):
    recs = model_double_chance(0.5, sample_factors)
    assert len(recs) == 1
    assert recs[0].market_code == "double-chance"
    assert any(dc in recs[0].pick for dc in ["1X", "X2", "12"])

def test_model_over_under(sample_factors):
    recs = model_over_under(0.5, sample_factors, line=2.5)
    assert len(recs) == 1
    assert recs[0].market_code == "over-under-2.5"
    assert "Over" in recs[0].pick or "Under" in recs[0].pick

def test_model_gg_ng(sample_factors):
    recs = model_gg_ng(0.5, sample_factors)
    assert len(recs) == 1
    assert recs[0].market_code == "gg-ng"
    assert "GG" in recs[0].pick or "NG" in recs[0].pick

def test_model_draw_no_bet(sample_factors):
    recs = model_draw_no_bet(0.5, sample_factors)
    assert len(recs) == 1
    assert recs[0].market_code == "draw-no-bet"
    assert "Home" in recs[0].pick or "Away" in recs[0].pick

def test_model_win_to_nil(sample_factors):
    recs = model_win_to_nil(0.5, sample_factors)
    # Returns WTN recommendations for the best side
    assert len(recs) == 1
    assert recs[0].market_code == "win-to-nil"
    assert "Win to Nil" in recs[0].pick

def test_model_win_either_half(sample_factors):
    recs = model_win_either_half(0.5, sample_factors)
    assert len(recs) == 1
    assert recs[0].market_code == "win-either-half"
    assert "Win Either Half" in recs[0].pick

def test_model_team_over_under(sample_factors):
    recs = model_team_over_under(0.5, sample_factors)
    assert len(recs) == 2
    assert all(r.market_code == "team-over-under" for r in recs)

def test_model_handicap(sample_factors):
    recs = model_handicap(0.5, sample_factors)
    assert len(recs) > 0
    assert all(r.market_code == "handicap" for r in recs)

def test_evaluate_all_markets(sample_factors):
    recs = evaluate_all_markets(0.5, sample_factors)
    assert len(recs) > 0
    market_codes = {r.market_code for r in recs}
    assert "1x2" in market_codes
    assert "double-chance" in market_codes
    assert "gg-ng" in market_codes
