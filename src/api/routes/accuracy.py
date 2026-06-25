from __future__ import annotations

from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query
from src.database import Database
from src.api.deps import get_db
from src.tracking.accuracy import AccuracyTracker
from src.tracking.calibrator import Calibrator

router = APIRouter(prefix="/accuracy", tags=["Accuracy"])


@router.get("")
def get_accuracy_dashboard(
    market: str | None = Query(None, description="Filter by market code"),
    league: str | None = Query(None, description="Filter by league slug"),
    db: Database = Depends(get_db),
):
    """Retrieve accuracy dashboard metrics including Brier score and streaks."""
    tracker = AccuracyTracker(db)
    dashboard = tracker.get_dashboard(market=market, league=league)
    return dashboard


@router.get("/readiness")
def get_calibration_readiness(db: Database = Depends(get_db)):
    """Retrieve calibration readiness status and threshold requirements."""
    cal = Calibrator(db)
    return cal.get_calibration_readiness()


from fastapi import Request

@router.post("/calibrate")
def run_calibration(
    request: Request,
    reset: bool = Query(False, description="Reset weights to default values"),
    db: Database = Depends(get_db),
):
    """Trigger the learning weight calibration loop or reset factor weights."""
    cal = Calibrator(db)
    if reset:
        weights = cal.reset_weights()
        if hasattr(request.app.state, "cached_weights"):
            request.app.state.cached_weights = weights
        return {"message": "Weights successfully reset to default settings.", "weights": weights}

    readiness = cal.get_calibration_readiness()
    if not readiness["ready"]:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Insufficient historical data: {readiness['total_predictions']}/{readiness['required']} "
                f"evaluated predictions ({readiness['progress_pct']:.1f}% complete)."
            ),
        )

    try:
        report = cal.calibrate()
        if "new_weights" in report and hasattr(request.app.state, "cached_weights"):
            request.app.state.cached_weights = report["new_weights"]
        return report
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
