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



# ═══════════════════════════════════════════════════════════════
#  1UP / 2UP settlement tests
# ═══════════════════════════════════════════════════════════════

class TestEval1UP:
    """1UP: wins if team goes ≥1 goal ahead at ANY point (regardless of FT)."""

    def test_home_1up_home_wins(self):
        """Home wins → home definitely led at some point → 1UP WIN."""
        _, is_correct = ResultRecorder._eval_1x2("Home 1X2 - 1UP", hs=2, as_=0, ht_hs=1, ht_as=0)
        assert is_correct is True

    def test_home_1up_home_led_at_ht_but_drew(self):
        """Home led at HT but drew — classic 1UP scenario. WIN."""
        _, is_correct = ResultRecorder._eval_1x2("Home 1X2 - 1UP", hs=1, as_=1, ht_hs=1, ht_as=0)
        assert is_correct is True

    def test_home_1up_home_led_at_ht_but_lost(self):
        """Home led 1-0 at HT but lost 1-2 — 1UP still wins (led at some point)."""
        _, is_correct = ResultRecorder._eval_1x2("Home 1X2 - 1UP", hs=1, as_=2, ht_hs=1, ht_as=0)
        assert is_correct is True

    def test_home_1up_never_led(self):
        """Home never led (0-0 at HT, 0-1 FT) → 1UP LOSS (approximation)."""
        _, is_correct = ResultRecorder._eval_1x2("Home 1X2 - 1UP", hs=0, as_=1, ht_hs=0, ht_as=0)
        assert is_correct is False

    def test_home_1up_never_led_drew(self):
        """Home never led (0-0 at HT, 1-1 FT drawn) → 1UP LOSS (approx: may have briefly led 2nd half but we can't detect it)."""
        _, is_correct = ResultRecorder._eval_1x2("Home 1X2 - 1UP", hs=1, as_=1, ht_hs=0, ht_as=0)
        # Conservative: cannot confirm led, so LOSS in approximation
        assert is_correct is False

    def test_away_1up_away_wins(self):
        """Away wins → away 1UP WIN."""
        _, is_correct = ResultRecorder._eval_1x2("Away 1X2 - 1UP", hs=0, as_=2, ht_hs=0, ht_as=1)
        assert is_correct is True

    def test_away_1up_led_ht_then_drew(self):
        """Away led at HT (0-1) but drew 1-1 → away 1UP WIN."""
        _, is_correct = ResultRecorder._eval_1x2("Away 1X2 - 1UP", hs=1, as_=1, ht_hs=0, ht_as=1)
        assert is_correct is True

    def test_away_1up_never_led(self):
        """Away never led (0-0 HT, 1-0 FT home win) → away 1UP LOSS."""
        _, is_correct = ResultRecorder._eval_1x2("Away 1X2 - 1UP", hs=1, as_=0, ht_hs=0, ht_as=0)
        assert is_correct is False

    def test_1up_no_ht_data_home_wins(self):
        """No HT data — home wins → treated as led (won outright implies led at some point)."""
        _, is_correct = ResultRecorder._eval_1x2("Home 1X2 - 1UP", hs=2, as_=1, ht_hs=None, ht_as=None)
        assert is_correct is True

    def test_1up_no_ht_data_home_draws(self):
        """No HT data — home draws → cannot confirm led → conservative LOSS."""
        _, is_correct = ResultRecorder._eval_1x2("Home 1X2 - 1UP", hs=1, as_=1, ht_hs=None, ht_as=None)
        assert is_correct is False


class TestEval2UP:
    """2UP: wins if team wins outright OR goes ≥2 goals ahead at any point."""

    def test_home_2up_home_wins_1nil(self):
        """Home wins 1-0 — won outright → 2UP WIN (win is sufficient)."""
        _, is_correct = ResultRecorder._eval_1x2("Home 1X2 - 2UP", hs=1, as_=0, ht_hs=1, ht_as=0)
        assert is_correct is True

    def test_home_2up_home_wins_2nil(self):
        """Home wins 2-0 — outright win + FT margin ≥2 → 2UP WIN."""
        _, is_correct = ResultRecorder._eval_1x2("Home 1X2 - 2UP", hs=2, as_=0, ht_hs=1, ht_as=0)
        assert is_correct is True

    def test_home_2up_led_2nil_ht_then_drew(self):
        """Home led 2-0 at HT then drew 2-2 — went 2 ahead → 2UP WIN."""
        _, is_correct = ResultRecorder._eval_1x2("Home 1X2 - 2UP", hs=2, as_=2, ht_hs=2, ht_as=0)
        assert is_correct is True

    def test_home_2up_ft_margin_2(self):
        """Home wins 3-1 — FT margin ≥2 → 2UP WIN (they were 2 ahead at some point)."""
        _, is_correct = ResultRecorder._eval_1x2("Home 1X2 - 2UP", hs=3, as_=1, ht_hs=1, ht_as=1)
        assert is_correct is True

    def test_home_2up_never_2ahead_drew(self):
        """Home drew 1-1, was 1-0 at HT → never went 2 ahead, didn't win → 2UP LOSS."""
        _, is_correct = ResultRecorder._eval_1x2("Home 1X2 - 2UP", hs=1, as_=1, ht_hs=1, ht_as=0)
        assert is_correct is False

    def test_home_2up_lost(self):
        """Home lost 0-2 — never led → 2UP LOSS."""
        _, is_correct = ResultRecorder._eval_1x2("Home 1X2 - 2UP", hs=0, as_=2, ht_hs=0, ht_as=1)
        assert is_correct is False

    def test_away_2up_away_wins(self):
        """Away wins 0-2 → away 2UP WIN."""
        _, is_correct = ResultRecorder._eval_1x2("Away 1X2 - 2UP", hs=0, as_=2, ht_hs=0, ht_as=1)
        assert is_correct is True

    def test_away_2up_led_2ahead_ht_then_collapsed(self):
        """Away led 0-2 at HT, drew 2-2 → 2UP WIN (went 2 ahead)."""
        _, is_correct = ResultRecorder._eval_1x2("Away 1X2 - 2UP", hs=2, as_=2, ht_hs=0, ht_as=2)
        assert is_correct is True

    def test_away_2up_drew_never_2ahead(self):
        """Away drew 1-1, led 0-1 at HT but never 2 ahead → 2UP LOSS."""
        _, is_correct = ResultRecorder._eval_1x2("Away 1X2 - 2UP", hs=1, as_=1, ht_hs=0, ht_as=1)
        assert is_correct is False


def test_auto_record_all_summary_keys(tmp_path):
    from src.database import Database
    db = Database(db_path=tmp_path / "test.db")
    recorder = ResultRecorder(db)
    summary = recorder.auto_record_all()
    assert "matches_processed" in summary
    assert "total_matches" in summary
    assert "predictions_evaluated" in summary
    assert "total_predictions" in summary
    assert summary["matches_processed"] == summary["total_matches"]
    assert summary["predictions_evaluated"] == summary["total_predictions"]
