from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query
from src.database import Database
from src.analysis.engine import AnalysisEngine
from src.api.deps import get_db, get_engine
from src.api.schemas import MatchAnalysisSchema

router = APIRouter(prefix="/analysis", tags=["Analysis"])


def store_predictions(db: Database, analyses: list[Any]) -> int:
    """Store computed match recommendations into the database as predictions."""
    count = 0
    for analysis in analyses:
        factor_scores = {}
        for f in analysis.factor_results:
            factor_scores[f.name] = {
                "score": round(f.score, 4),
                "confidence": round(f.confidence, 4),
            }

        for rec in analysis.market_recommendations:
            try:
                db.insert_prediction(
                    match_id=analysis.match_id,
                    market=rec.market_code,
                    pick=rec.pick,
                    probability=rec.probability,
                    confidence=rec.confidence,
                    implied_odds=rec.implied_odds,
                    rationale=rec.rationale,
                    risk_factors=rec.risk_factors,
                    supporting_stats=rec.supporting_stats,
                    factor_scores=factor_scores,
                )
                count += 1
            except Exception:
                # Duplicate or constraint violation — skip
                pass
    return count


@router.get("/matchweek", response_model=list[MatchAnalysisSchema])
def get_matchweek_analysis(
    league: str | None = Query(None, description="League slug filter"),
    from_date: str | None = Query(None, description="Start date (YYYY-MM-DD)"),
    to_date: str | None = Query(None, description="End date (YYYY-MM-DD)"),
    db: Database = Depends(get_db),
    engine: AnalysisEngine = Depends(get_engine),
):
    """Run/fetch matchweek analysis for all or specific leagues."""
    if not from_date:
        from_date = datetime.now().strftime("%Y-%m-%d")
    if not to_date:
        to_date = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")

    analyses = engine.analyze_matchweek(
        league_slug=league,
        from_date=from_date,
        to_date=to_date,
    )

    # Automatically store predictions in the database
    store_predictions(db, analyses)

    return analyses


@router.get("/match/{match_id}", response_model=MatchAnalysisSchema)
def get_match_analysis(
    match_id: int,
    db: Database = Depends(get_db),
    engine: AnalysisEngine = Depends(get_engine),
):
    """Analyze a single specific match by its match ID."""
    try:
        analysis = engine.analyze_match(match_id)
        # Store predictions
        store_predictions(db, [analysis])
        return analysis
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/trigger", response_model=list[MatchAnalysisSchema])
def trigger_analysis(
    league: str | None = Query(None, description="League slug filter"),
    from_date: str | None = Query(None, description="Start date (YYYY-MM-DD)"),
    to_date: str | None = Query(None, description="End date (YYYY-MM-DD)"),
    db: Database = Depends(get_db),
    engine: AnalysisEngine = Depends(get_engine),
):
    """Trigger fresh analysis for a date range, saving predictions."""
    if not from_date:
        from_date = datetime.now().strftime("%Y-%m-%d")
    if not to_date:
        to_date = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")

    analyses = engine.analyze_matchweek(
        league_slug=league,
        from_date=from_date,
        to_date=to_date,
    )
    store_predictions(db, analyses)
    return analyses


@router.get("/search", response_model=MatchAnalysisSchema)
def search_and_analyze_match(
    home: str = Query(..., description="Home team query"),
    away: str = Query(..., description="Away team query"),
    db: Database = Depends(get_db),
    engine: AnalysisEngine = Depends(get_engine),
):
    """Deep-dive analysis of a specific fixture using team names search."""
    analysis = engine.analyze_specific_match(home, away)
    if not analysis:
        raise HTTPException(
            status_code=404,
            detail=f"Upcoming match not found for '{home} vs {away}'",
        )
    # Store predictions
    store_predictions(db, [analysis])
    return analysis
