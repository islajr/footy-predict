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
    # Calibrate weights on request using saved json weights
    weights = Calibrator.load_weights()
    return AnalysisEngine(db, weights=weights)


def get_formatter() -> ReportFormatter:
    """Dependency provider for the ReportFormatter."""
    return ReportFormatter()
