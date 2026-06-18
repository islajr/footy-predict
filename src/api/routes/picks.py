from __future__ import annotations

from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, Query
from src.database import Database
from src.api.deps import get_db
from src.api.schemas import PickResponseSchema
from src.config import LEAGUES

router = APIRouter(prefix="/picks", tags=["Picks"])


@router.get("", response_model=list[PickResponseSchema])
def get_picks(
    min_confidence: str = Query("HIGH", description="Minimum confidence: HIGH, MEDIUM, LOW"),
    league: str | None = Query(None, description="League slug filter"),
    market: str | None = Query(None, description="Market code filter (e.g. 1x2)"),
    from_date: str | None = Query(None, description="Start date (YYYY-MM-DD)"),
    to_date: str | None = Query(None, description="End date (YYYY-MM-DD)"),
    db: Database = Depends(get_db),
):
    """Retrieve predictions matching criteria."""
    if not from_date:
        from_date = datetime.now().strftime("%Y-%m-%d")
    if not to_date:
        to_date = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")

    raw_picks = db.get_high_confidence_predictions(
        min_confidence=min_confidence.upper(),
        from_date=from_date,
        to_date=to_date,
    )

    picks = []
    for p in raw_picks:
        # Resolve league slug
        match_league_slug = None
        # We can find league slug matching the name or api id
        for slug, config in LEAGUES.items():
            if config.name == p.get("league_name"):
                match_league_slug = slug
                break

        if league and match_league_slug != league:
            continue

        if market and p.get("market") != market:
            continue

        picks.append(
            PickResponseSchema(
                match_id=p["match_id"],
                home_team=p["home_team_name"],
                away_team=p["away_team_name"],
                league=p["league_name"],
                match_date=p["date"],
                market_code=p["market"],
                market_name=p["market"].replace("-", " ").title(),  # fallback market name formatting
                pick=p["pick"],
                probability=p["probability"],
                implied_odds=p["implied_odds"],
                confidence=p["confidence"],
                rationale=p["rationale"] or "",
            )
        )

    return picks
