import pytest
import tempfile
import os
from src.database import Database
from src.tracking.accuracy import AccuracyTracker

@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = Database(path)
    db.seed_leagues()
    yield db
    try:
        os.unlink(path)
    except OSError:
        pass

def test_accuracy_tracker_empty(temp_db):
    tracker = AccuracyTracker(temp_db)
    dashboard = tracker.get_dashboard()
    assert dashboard["total"] == 0
    assert dashboard["accuracy_pct"] is None
    assert tracker.get_brier_score() == 0.0

def test_accuracy_tracker_with_data(temp_db):
    db = temp_db
    leagues = db.get_all_leagues()
    pl = next(l for l in leagues if l["slug"] == "premier-league")
    league_id = pl["id"]
    season = "2025"

    home_id = db.upsert_team("Arsenal", league_id, api_football_id=42)
    away_id = db.upsert_team("Chelsea", league_id, api_football_id=49)

    # Insert a few completed matches
    match_ids = []
    for i in range(5):
        m_id = db.upsert_match(
            league_id=league_id,
            season=season,
            date=f"2025-06-0{i+1}",
            home_team_id=home_id,
            away_team_id=away_id,
            api_football_id=1000 + i,
            status="FINISHED",
            home_score=2,
            away_score=1
        )
        match_ids.append(m_id)

    # Insert predictions and record outcomes
    # Prediction 1: correct (probability 0.8)
    p1 = db.insert_prediction(match_ids[0], "1x2", "1 (Home Win)", 0.8, "HIGH", "Rationale 1")
    db.record_result(p1, "1", True)

    # Prediction 2: correct (probability 0.7)
    p2 = db.insert_prediction(match_ids[1], "1x2", "1 (Home Win)", 0.7, "MEDIUM", "Rationale 2")
    db.record_result(p2, "1", True)

    # Prediction 3: incorrect (probability 0.9)
    p3 = db.insert_prediction(match_ids[2], "1x2", "1 (Home Win)", 0.9, "HIGH", "Rationale 3")
    db.record_result(p3, "2", False)

    # Prediction 4: correct (probability 0.6)
    p4 = db.insert_prediction(match_ids[3], "over-under-2.5", "Over 2.5", 0.6, "MEDIUM", "Rationale 4")
    db.record_result(p4, "Over 2.5 (total=3)", True)

    # Prediction 5: incorrect (probability 0.5)
    p5 = db.insert_prediction(match_ids[4], "gg-ng", "GG", 0.5, "LOW", "Rationale 5")
    db.record_result(p5, "NG", False)

    tracker = AccuracyTracker(db)
    dashboard = tracker.get_dashboard()

    assert dashboard["total"] == 5
    # 3 correct out of 5 -> 60%
    assert pytest.approx(dashboard["accuracy_pct"]) == 60.0
    assert dashboard["correct"] == 3

    # Test Brier Score (1/N * sum( (prob - outcome)^2 ))
    # Correct picks: outcome=1. Incorrect: outcome=0.
    # p1: (0.8 - 1)^2 = 0.04
    # p2: (0.7 - 1)^2 = 0.09
    # p3: (0.9 - 0)^2 = 0.81
    # p4: (0.6 - 1)^2 = 0.16
    # p5: (0.5 - 0)^2 = 0.25
    # Sum = 0.04 + 0.09 + 0.81 + 0.16 + 0.25 = 1.35
    # Avg = 1.35 / 5 = 0.27
    brier = tracker.get_brier_score()
    assert pytest.approx(brier) == 0.27

    # Test streaks
    streaks = tracker.get_streaks()
    assert streaks is not None
    # Test calibration data
    cal = tracker.get_calibration_data()
    assert len(cal) > 0
    # Test ROI simulation
    roi = tracker.get_roi_simulation()
    assert roi is not None
    # Test time trend
    trend = tracker.get_time_trend()
    assert len(trend) > 0
