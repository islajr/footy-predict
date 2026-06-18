from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException, Query
from src.database import Database
from src.analysis.engine import AnalysisEngine
from src.api.deps import get_db, get_engine
from src.api.schemas import ParlaySchema, ParlayItemSchema

logger = logging.getLogger("footy.api.parlay")
router = APIRouter(prefix="/parlay", tags=["Parlay"])


@router.get("", response_model=list[ParlaySchema])
def get_parlays(db: Database = Depends(get_db)):
    """Retrieve all saved parlay slips."""
    parlays = db.get_all_parlays()
    res = []
    for p in parlays:
        res.append(
            ParlaySchema(
                id=p["id"],
                name=p["name"],
                created_at=p["created_at"],
                submitted_at=p["submitted_at"],
                size=p["size"],
                picks=[ParlayItemSchema(**item) for item in p["picks"]],
                status=p["status"],
                correct_count=p["correct_count"],
                notes=p["notes"],
            )
        )
    return res


@router.post("", response_model=ParlaySchema)
def save_parlay(req: ParlaySchema, db: Database = Depends(get_db)):
    """Save a new parlay slip (draft or submitted)."""
    picks_list = [item.dict() for item in req.picks]
    parlay_id = db.insert_parlay(
        name=req.name,
        size=req.size,
        picks=picks_list,
        status=req.status,
        notes=req.notes,
    )
    p = db.get_parlay(parlay_id)
    if not p:
        raise HTTPException(status_code=500, detail="Failed to save parlay.")
    return ParlaySchema(
        id=p["id"],
        name=p["name"],
        created_at=p["created_at"],
        submitted_at=p["submitted_at"],
        size=p["size"],
        picks=[ParlayItemSchema(**item) for item in p["picks"]],
        status=p["status"],
        correct_count=p["correct_count"],
        notes=p["notes"],
    )


@router.post("/{parlay_id}/submit", response_model=ParlaySchema)
def submit_parlay(parlay_id: int, db: Database = Depends(get_db)):
    """Lock and submit a parlay slip."""
    p = db.get_parlay(parlay_id)
    if not p:
        raise HTTPException(status_code=404, detail="Parlay not found.")
    db.update_parlay_status(parlay_id, "submitted")
    p = db.get_parlay(parlay_id)
    return ParlaySchema(
        id=p["id"],
        name=p["name"],
        created_at=p["created_at"],
        submitted_at=p["submitted_at"],
        size=p["size"],
        picks=[ParlayItemSchema(**item) for item in p["picks"]],
        status=p["status"],
        correct_count=p["correct_count"],
        notes=p["notes"],
    )


@router.post("/{parlay_id}/settle", response_model=ParlaySchema)
def settle_parlay(
    parlay_id: int,
    status: str = Query(..., description="won or lost"),
    correct_count: int = Query(..., description="Number of correct picks"),
    db: Database = Depends(get_db),
):
    """Settle a parlay slip with outcome status and correct counts."""
    p = db.get_parlay(parlay_id)
    if not p:
        raise HTTPException(status_code=404, detail="Parlay not found.")
    
    status_lower = status.lower()
    if status_lower not in ["won", "lost"]:
        raise HTTPException(status_code=400, detail="Status must be 'won' or 'lost'.")

    db.update_parlay_status(parlay_id, status_lower, correct_count)
    p = db.get_parlay(parlay_id)
    return ParlaySchema(
        id=p["id"],
        name=p["name"],
        created_at=p["created_at"],
        submitted_at=p["submitted_at"],
        size=p["size"],
        picks=[ParlayItemSchema(**item) for item in p["picks"]],
        status=p["status"],
        correct_count=p["correct_count"],
        notes=p["notes"],
    )


@router.delete("/{parlay_id}")
def delete_parlay(parlay_id: int, db: Database = Depends(get_db)):
    """Delete a saved parlay slip."""
    p = db.get_parlay(parlay_id)
    if not p:
        raise HTTPException(status_code=404, detail="Parlay not found.")
    db.delete_parlay(parlay_id)
    return {"message": "Parlay slip deleted."}


@router.get("/suggest", response_model=list[ParlayItemSchema])
def get_suggested_picks(
    size: int = Query(50, description="Target parlay size"),
    min_confidence: str = Query("MEDIUM", description="Minimum confidence: HIGH, MEDIUM, LOW"),
    db: Database = Depends(get_db),
    engine: AnalysisEngine = Depends(get_engine),
):
    """Retrieve suggestions for a parlay based on current week analysis."""
    # Analyze matchweek
    from_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    to_date = (datetime.now(timezone.utc) + timedelta(days=7)).strftime("%Y-%m-%d")

    analyses = engine.analyze_matchweek(from_date=from_date, to_date=to_date)
    # Suggestions list of tuples
    suggestions = engine.suggest_parlay(analyses, size=size, min_confidence=min_confidence.upper())

    picks = []
    for a, rec in suggestions:
        picks.append(
            ParlayItemSchema(
                match_id=a.match_id,
                home_team=a.home_team,
                away_team=a.away_team,
                league=a.league,
                market_code=rec.market_code,
                market_name=rec.market_name,
                pick=rec.pick,
                probability=rec.probability,
                confidence=rec.confidence,
                implied_odds=rec.implied_odds,
            )
        )
    return picks
