from __future__ import annotations

from fastapi import Request
from src.database import Database
from src.analysis.engine import AnalysisEngine
from src.tracking.calibrator import Calibrator
from src.reporting.formatter import ReportFormatter


def get_db(request: Request) -> Database:
    """Dependency provider for the Database instance."""
    return request.app.state.db


def get_engine(request: Request) -> AnalysisEngine:
    """Dependency provider for the AnalysisEngine instance."""
    db = get_db(request)
    # Cache weights in app state to avoid reading JSON from disk on every request
    app_state = request.app.state
    if not hasattr(app_state, "cached_weights") or app_state.cached_weights is None:
        app_state.cached_weights = Calibrator.load_weights()
    return AnalysisEngine(db, weights=app_state.cached_weights)


def get_formatter() -> ReportFormatter:
    """Dependency provider for the ReportFormatter."""
    return ReportFormatter()
