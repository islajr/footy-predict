import pytest
import tempfile
import os
import json
from pathlib import Path
from src.database import Database
import src.tracking.calibrator as calibrator
from src.tracking.calibrator import Calibrator

@pytest.fixture
def temp_db_and_weights():
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    
    fd_w, weights_path = tempfile.mkstemp(suffix=".json")
    os.close(fd_w)
    
    db = Database(db_path)
    db.seed_leagues()
    
    # Patch WEIGHTS_FILE and MIN_PREDICTIONS_FOR_CALIBRATION in calibrator module
    old_weights_file = calibrator.WEIGHTS_FILE
    old_min_predictions = calibrator.MIN_PREDICTIONS_FOR_CALIBRATION
    old_min_signal = calibrator.MIN_SIGNAL_PREDICTIONS
    
    calibrator.WEIGHTS_FILE = Path(weights_path)
    calibrator.MIN_PREDICTIONS_FOR_CALIBRATION = 3
    calibrator.MIN_SIGNAL_PREDICTIONS = 1  # reduce signal requirement for easy testing
    
    # Create empty weights file first to avoid JSON load warning/error
    with open(weights_path, "w") as f:
        json.dump({}, f)
        
    yield db, Path(weights_path)
    
    # Restore
    calibrator.WEIGHTS_FILE = old_weights_file
    calibrator.MIN_PREDICTIONS_FOR_CALIBRATION = old_min_predictions
    calibrator.MIN_SIGNAL_PREDICTIONS = old_min_signal
    
    try:
        os.unlink(db_path)
        os.unlink(weights_path)
    except OSError:
        pass

def test_calibrator_readiness(temp_db_and_weights):
    db, weights_path = temp_db_and_weights
    cal = Calibrator(db)
    
    # Initially not ready
    assert cal.can_calibrate() is False
    readiness = cal.get_calibration_readiness()
    assert readiness["ready"] is False
    assert readiness["total_predictions"] == 0

def test_calibrator_loop(temp_db_and_weights):
    db, weights_path = temp_db_and_weights
    cal = Calibrator(db)
    
    # Insert 3 completed matches
    leagues = db.get_all_leagues()
    pl = next(l for l in leagues if l["slug"] == "premier-league")
    league_id = pl["id"]
    season = "2025"
    home_id = db.upsert_team("Arsenal", league_id, api_football_id=42)
    away_id = db.upsert_team("Chelsea", league_id, api_football_id=49)
    
    match_ids = []
    for i in range(3):
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
        
    # We want to test factor contribution.
    # Predictions must have "factor_scores" saved as JSON dict mapping factor name to {score, confidence}.
    factor_scores_1 = {
        "recent_form": {"score": 0.8, "confidence": 0.9},
        "home_form": {"score": 0.9, "confidence": 0.8}
    }
    p1 = db.insert_prediction(
        match_ids[0], "1x2", "1 (Home Win)", 0.8, "HIGH", "Rationale 1",
        factor_scores=factor_scores_1
    )
    db.record_result(p1, "1", True) # Correct
    
    factor_scores_2 = {
        "recent_form": {"score": 0.8, "confidence": 0.9},
        "home_form": {"score": 0.9, "confidence": 0.8}
    }
    p2 = db.insert_prediction(
        match_ids[1], "1x2", "1 (Home Win)", 0.7, "MEDIUM", "Rationale 2",
        factor_scores=factor_scores_2
    )
    db.record_result(p2, "1", True) # Correct

    factor_scores_3 = {
        "recent_form": {"score": 0.8, "confidence": 0.9},
        "home_form": {"score": 0.9, "confidence": 0.8}
    }
    p3 = db.insert_prediction(
        match_ids[2], "1x2", "1 (Home Win)", 0.9, "HIGH", "Rationale 3",
        factor_scores=factor_scores_3
    )
    db.record_result(p3, "2", False) # Incorrect
    
    assert cal.can_calibrate() is True
    
    report = cal.calibrate()
    assert report is not None
    assert "old_weights" in report
    assert "new_weights" in report
    assert "changes" in report
    
    # Check that weights file was written
    assert weights_path.exists()
    with open(weights_path) as f:
        saved_weights = json.load(f)
    assert len(saved_weights) > 0
