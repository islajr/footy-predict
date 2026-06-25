import pytest
import json
from src.tracking.recorder import ResultRecorder

def test_eval_1x2():
    # Home win
    actual, is_correct = ResultRecorder._eval_1x2("1", hs=3, as_=1)
    assert actual == "1"
    assert is_correct is True

    actual, is_correct = ResultRecorder._eval_1x2("X", hs=3, as_=1)
    assert actual == "1"
    assert is_correct is False

    # Draw
    actual, is_correct = ResultRecorder._eval_1x2("X", hs=1, as_=1)
    assert actual == "X"
    assert is_correct is True

    # Away win
    actual, is_correct = ResultRecorder._eval_1x2("2", hs=0, as_=2)
    assert actual == "2"
    assert is_correct is True

def test_eval_double_chance():
    # 1X pick covers Home win (1) or Draw (X)
    actual, is_correct = ResultRecorder._eval_double_chance("1X", hs=2, as_=1)
    assert actual == "1"
    assert is_correct is True

    actual, is_correct = ResultRecorder._eval_double_chance("1X", hs=1, as_=1)
    assert actual == "X"
    assert is_correct is True

    actual, is_correct = ResultRecorder._eval_double_chance("1X", hs=0, as_=1)
    assert actual == "2"
    assert is_correct is False

    # X2 pick covers Draw (X) or Away win (2)
    actual, is_correct = ResultRecorder._eval_double_chance("X2", hs=1, as_=1)
    assert actual == "X"
    assert is_correct is True

    actual, is_correct = ResultRecorder._eval_double_chance("X2", hs=0, as_=2)
    assert actual == "2"
    assert is_correct is True

    # 12 pick covers Home win (1) or Away win (2)
    actual, is_correct = ResultRecorder._eval_double_chance("12", hs=1, as_=1)
    assert actual == "X"
    assert is_correct is False

def test_eval_over_under():
    # Over 2.5 line
    actual, is_correct = ResultRecorder._eval_over_under("over-under-2.5", "Over", hs=2, as_=1)
    assert "Over 2.5" in actual
    assert is_correct is True

    actual, is_correct = ResultRecorder._eval_over_under("over-under-2.5", "Under", hs=2, as_=1)
    assert is_correct is False

    # Under 2.5 line
    actual, is_correct = ResultRecorder._eval_over_under("over-under-2.5", "Under", hs=1, as_=1)
    assert "Under 2.5" in actual
    assert is_correct is True

    # Invalid market code returns VOID
    actual, is_correct = ResultRecorder._eval_over_under("over-under-invalid", "Over", hs=1, as_=1)
    assert actual == "VOID"
    assert is_correct is True

def test_eval_gg_ng():
    # Both score -> GG
    actual, is_correct = ResultRecorder._eval_gg_ng("GG", hs=1, as_=1)
    assert actual == "GG"
    assert is_correct is True

    actual, is_correct = ResultRecorder._eval_gg_ng("NG", hs=1, as_=1)
    assert is_correct is False

    # One scores 0 -> NG
    actual, is_correct = ResultRecorder._eval_gg_ng("NG", hs=2, as_=0)
    assert actual == "NG"
    assert is_correct is True

    actual, is_correct = ResultRecorder._eval_gg_ng("GG", hs=0, as_=0)
    assert actual == "NG"
    assert is_correct is False

def test_eval_draw_no_bet():
    # Draw results in VOID (push)
    actual, is_correct = ResultRecorder._eval_draw_no_bet("Home", hs=1, as_=1)
    assert actual == "VOID"
    assert is_correct is True

    # Home win
    actual, is_correct = ResultRecorder._eval_draw_no_bet("Home", hs=2, as_=1)
    assert actual == "Home"
    assert is_correct is True

    # Away win
    actual, is_correct = ResultRecorder._eval_draw_no_bet("Away", hs=0, as_=1)
    assert actual == "Away"
    assert is_correct is True

def test_eval_win_to_nil():
    match = {}
    # Home win to nil
    actual, is_correct = ResultRecorder._eval_win_to_nil("Home", hs=2, as_=0, match=match)
    assert actual == "Home WTN"
    assert is_correct is True

    actual, is_correct = ResultRecorder._eval_win_to_nil("Home", hs=2, as_=1, match=match)
    assert actual == "No WTN"
    assert is_correct is False

    # Away win to nil
    actual, is_correct = ResultRecorder._eval_win_to_nil("Away", hs=0, as_=1, match=match)
    assert actual == "Away WTN"
    assert is_correct is True

def test_eval_win_either_half():
    match = {}
    # Missing HT scores should result in VOID
    actual, is_correct = ResultRecorder._eval_win_either_half("Home", hs=2, as_=1, ht_hs=None, ht_as=None, match=match)
    assert actual == "VOID"
    assert is_correct is True

    # Home wins first half (1-0), second half is (1-1) -> overall Home wins at least one half
    actual, is_correct = ResultRecorder._eval_win_either_half("Home", hs=2, as_=1, ht_hs=1, ht_as=0, match=match)
    assert "Home won half" in actual
    assert is_correct is True

    # Home loses first half (0-1), wins second half (2-0, final 2-1) -> overall Home wins at least one half
    actual, is_correct = ResultRecorder._eval_win_either_half("Home", hs=2, as_=1, ht_hs=0, ht_as=1, match=match)
    assert "Home won half" in actual
    assert is_correct is True

    # Home loses both halves (0-1 first half, 0-1 second half, final 0-2)
    actual, is_correct = ResultRecorder._eval_win_either_half("Home", hs=0, as_=2, ht_hs=0, ht_as=1, match=match)
    assert "Home lost both halves" in actual
    assert is_correct is False

def test_eval_handicap():
    # Handicap -1.5 for home team
    pred = {"supporting_stats": json.dumps({"handicap_value": -1.5})}
    
    # 2-0 win -> handicap adjusted score is 0.5-0 (Home win)
    actual, is_correct = ResultRecorder._eval_handicap(pred, "1", hs=2, as_=0)
    assert "1" in actual
    assert is_correct is True

    # 1-0 win -> handicap adjusted score is -0.5-0 (Away win)
    actual, is_correct = ResultRecorder._eval_handicap(pred, "2", hs=1, as_=0)
    assert "2" in actual
    assert is_correct is True

def test_eval_team_over_under():
    # Team Over 1.5 goals for Home team
    pred = {"supporting_stats": json.dumps({"team_side": "home", "line": 1.5})}
    match = {}
    
    actual, is_correct = ResultRecorder._eval_team_over_under(pred, "Over", hs=2, as_=0, match=match)
    assert "Over 1.5" in actual
    assert is_correct is True

    actual, is_correct = ResultRecorder._eval_team_over_under(pred, "Under", hs=1, as_=0, match=match)
    assert "Under 1.5" in actual
    assert is_correct is True

def test_eval_pick_normalization():
    # Test 1X2 pick normalization
    actual, is_correct = ResultRecorder._eval_1x2("1 (Home Win)", hs=2, as_=1)
    assert actual == "1"
    assert is_correct is True

    actual, is_correct = ResultRecorder._eval_1x2("X (Draw)", hs=1, as_=1)
    assert actual == "X"
    assert is_correct is True

    actual, is_correct = ResultRecorder._eval_1x2("2 (Away Win)", hs=0, as_=2)
    assert actual == "2"
    assert is_correct is True

    # Test GG/NG pick normalization
    actual, is_correct = ResultRecorder._eval_gg_ng("GG (Both Teams Score)", hs=1, as_=1)
    assert actual == "GG"
    assert is_correct is True

    actual, is_correct = ResultRecorder._eval_gg_ng("NG (No (clean sheet expected))", hs=2, as_=0)
    assert actual == "NG"
    assert is_correct is True

    # Test Draw No Bet pick normalization
    actual, is_correct = ResultRecorder._eval_draw_no_bet("DNB Home", hs=2, as_=1)
    assert actual == "Home"
    assert is_correct is True

    # Test Win to Nil pick normalization
    match = {}
    actual, is_correct = ResultRecorder._eval_win_to_nil("Home Win to Nil", hs=2, as_=0, match=match)
    assert "Home WTN" in actual
    assert is_correct is True

    # Test Win Either Half pick normalization
    match = {}
    actual, is_correct = ResultRecorder._eval_win_either_half("Home Win Either Half", hs=2, as_=1, ht_hs=1, ht_as=0, match=match)
    assert "Home won half" in actual
    assert is_correct is True

    # Test Handicap pick normalization and fallback to handicap_line
    pred = {"supporting_stats": json.dumps({"handicap_line": -1.5})}
    actual, is_correct = ResultRecorder._eval_handicap(pred, "Home -1.5", hs=2, as_=0)
    assert "1" in actual
    assert is_correct is True
