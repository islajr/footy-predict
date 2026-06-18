from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any
from fastapi import APIRouter, Depends, Request, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from src.database import Database
from src.analysis.engine import AnalysisEngine
from src.tracking.accuracy import AccuracyTracker
from src.tracking.calibrator import Calibrator
from pathlib import Path
from fastapi.templating import Jinja2Templates
from src.api.deps import get_db, get_engine
from src.config import LEAGUES, MARKETS, FACTOR_WEIGHTS

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
TEMPLATES_DIR = PROJECT_ROOT / "src" / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

logger = logging.getLogger("footy.api.views")
router = APIRouter(tags=["HTML Views"])


def get_freshness_data(db: Database) -> dict[str, Any] | None:
    """Calculate the database's data freshness based on last matches updates."""
    with db._connect() as conn:
        row = conn.execute("SELECT MAX(updated_at) FROM matches").fetchone()
        if not row or not row[0]:
            return {"label": "Never", "hours": 999}
        
        try:
            ts_str = row[0]
            # Normalize ISO timestamp for string parsing
            ts_str = ts_str.replace("T", " ").split(".")[0]
            dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            diff = datetime.now(timezone.utc) - dt
            hours = int(diff.total_seconds() / 3600)
            
            if hours < 1:
                label = "just now"
            elif hours == 1:
                label = "1 hour ago"
            else:
                label = f"{hours} hours ago"
                
            return {"label": label, "hours": hours}
        except Exception:
            return {"label": "Unknown", "hours": 0}


def get_view_context(request: Request, db: Database, active_page: str) -> dict[str, Any]:
    """Retrieve shared layout parameters (quota, theme, freshness labels)."""
    # Fetch settings
    api_football_key = db.get_setting("api_football_key") or ""
    fdo_key = db.get_setting("football_data_org_key") or ""
    theme = db.get_setting("theme") or "dark"
    onboarding_complete = (db.get_setting("onboarding_complete") == "1")
    fetch_strategy = db.get_setting("fetch_strategy") or "full"

    settings_dict = {
        "api_football_key": api_football_key,
        "football_data_org_key": fdo_key,
        "theme": theme,
        "onboarding_complete": onboarding_complete,
        "fetch_strategy": fetch_strategy
    }

    # Fetch quota
    api_football_usage = db.get_api_usage_today("api_football")
    requests_made = api_football_usage.get("requests_made", 0)
    # API-Football free tier limit: 100
    quota_dict = {
        "used": requests_made,
        "remaining": max(0, 100 - requests_made),
        "limit": 100
    }

    # Freshness
    freshness = get_freshness_data(db)

    return {
        "request": request,
        "settings": settings_dict,
        "quota": quota_dict,
        "freshness": freshness,
        "active_page": active_page
    }


@router.get("/onboarding", response_class=HTMLResponse)
def show_onboarding(request: Request, db: Database = Depends(get_db)):
    """Render the onboarding wizard."""
    # Seed defaults if database table is completely empty
    db.seed_user_defaults()

    settings_dict = {
        "api_football_key": db.get_setting("api_football_key") or "",
        "football_data_org_key": db.get_setting("football_data_org_key") or "",
        "theme": db.get_setting("theme") or "dark",
        "onboarding_complete": (db.get_setting("onboarding_complete") == "1"),
        "fetch_strategy": db.get_setting("fetch_strategy") or "full",
    }

    # Leagues list with config matching
    raw_leagues = db.get_league_preferences()
    # Markets list
    raw_markets = db.get_market_preferences()

    return templates.TemplateResponse(
        request=request,
        name="onboarding.html",
        context={
            "settings": settings_dict,
            "leagues": raw_leagues,
            "markets": raw_markets
        }
    )


@router.get("/", response_class=HTMLResponse)
@router.get("/dashboard", response_class=HTMLResponse)
def show_dashboard(request: Request, db: Database = Depends(get_db)):
    """Render the main dashboard overview page."""
    onboarding_complete = (db.get_setting("onboarding_complete") == "1")
    if not onboarding_complete:
        return RedirectResponse(url="/onboarding")

    context = get_view_context(request, db, "dashboard")

    # Fetch database summary statistics
    leagues = db.get_all_leagues()
    with db._connect() as conn:
        teams_count = conn.execute("SELECT COUNT(*) FROM teams").fetchone()[0]
        matches_count = conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
        predictions_count = conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]

    db_stats = {
        "leagues_count": len(leagues),
        "teams_count": teams_count,
        "matches_count": matches_count,
        "predictions_count": predictions_count,
    }

    # Fetch top 5 high-confidence predictions
    raw_picks = db.get_high_confidence_predictions(min_confidence="HIGH")
    top_picks = []
    for p in raw_picks[:5]:
        top_picks.append({
            "league": p["league_name"],
            "home_team": p["home_team_name"],
            "away_team": p["away_team_name"],
            "market_name": p["market"].replace("-", " ").title(),
            "pick": p["pick"],
            "probability": p["probability"]
        })

    # Fetch upcoming matches for active leagues
    enabled_slugs = db.get_enabled_leagues()
    upcoming_matches = []

    if enabled_slugs:
        # Get matches for enabled leagues
        from_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        to_date = (datetime.now(timezone.utc) + timedelta(days=7)).strftime("%Y-%m-%d")
        
        all_upcoming = db.get_upcoming_matches(from_date=from_date, to_date=to_date)
        for m in all_upcoming:
            if m["league_slug"] in enabled_slugs:
                upcoming_matches.append({
                    "id": m["id"],
                    "date": m["date"].replace("T", " ")[:16],
                    "home_team": m["home_team_name"],
                    "away_team": m["away_team_name"],
                    "league": m["league_name"]
                })

    context.update({
        "stats": db_stats,
        "top_picks": top_picks,
        "matches": upcoming_matches[:20]  # limit to 20
    })

    return templates.TemplateResponse(request=request, name="dashboard.html", context=context)


@router.get("/analysis", response_class=HTMLResponse)
def show_analysis(
    request: Request,
    league: str | None = Query(None, description="League slug filter"),
    db: Database = Depends(get_db),
    engine: AnalysisEngine = Depends(get_engine),
):
    """Render the matchweek analysis page with active league filters."""
    onboarding_complete = (db.get_setting("onboarding_complete") == "1")
    if not onboarding_complete:
        return RedirectResponse(url="/onboarding")

    context = get_view_context(request, db, "analysis")

    # Fetch active leagues configured
    active_leagues_prefs = db.get_league_preferences()
    active_leagues = [lg for lg in active_leagues_prefs if lg["enabled"]]

    # Run matchweek analysis
    from_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    to_date = (datetime.now(timezone.utc) + timedelta(days=7)).strftime("%Y-%m-%d")

    analyses = engine.analyze_matchweek(
        league_slug=league,
        from_date=from_date,
        to_date=to_date,
    )

    # Filter analyses in python by enabled leagues if no league filter was set
    enabled_slugs = {lg["slug"] for lg in active_leagues}
    if not league:
        # Convert config league display name to slugs to compare
        slug_map = {cfg.name: slug for slug, cfg in LEAGUES.items()}
        analyses = [
            a for a in analyses 
            if slug_map.get(a.league) in enabled_slugs
        ]

    # Map details to simple layout formats
    formatted_analyses = []
    for a in analyses:
        formatted_analyses.append({
            "match_id": a.match_id,
            "home_team": a.home_team,
            "away_team": a.away_team,
            "league": a.league,
            "match_date": a.match_date.replace("T", " ")[:16],
            "overall_home_score": a.overall_home_score,
            "overall_confidence": a.overall_confidence,
            "data_quality": a.data_quality,
            "market_recommendations": [
                {
                    "market_name": r.market_name,
                    "pick": r.pick,
                    "probability": r.probability
                }
                for r in a.market_recommendations
            ]
        })

    context.update({
        "active_leagues": active_leagues,
        "selected_league": league,
        "analyses": formatted_analyses
    })

    return templates.TemplateResponse(request=request, name="analysis.html", context=context)


@router.get("/analysis/match/{match_id}", response_class=HTMLResponse)
def show_match_detail(
    request: Request,
    match_id: int,
    db: Database = Depends(get_db),
    engine: AnalysisEngine = Depends(get_engine),
):
    """Render single match deep-dive analysis page."""
    onboarding_complete = (db.get_setting("onboarding_complete") == "1")
    if not onboarding_complete:
        return RedirectResponse(url="/onboarding")

    context = get_view_context(request, db, "analysis")

    try:
        a = engine.analyze_match(match_id)
    except ValueError as e:
        # Redirect to analysis list on invalid ID
        return RedirectResponse(url="/analysis")

    # Format kick-off date
    formatted_date = a.match_date.replace("T", " ")[:16]

    # Map data to layout structure
    analysis_data = {
        "match_id": a.match_id,
        "home_team": a.home_team,
        "away_team": a.away_team,
        "league": a.league,
        "match_date": formatted_date,
        "overall_home_score": a.overall_home_score,
        "overall_confidence": a.overall_confidence,
        "data_quality": a.data_quality,
        "summary": a.summary,
        "risk_flags": a.risk_flags,
        "factor_results": [
            {
                "name": f.name,
                "score": f.score,
                "confidence": f.confidence,
                "rationale": f.rationale
            }
            for f in a.factor_results
        ],
        "market_recommendations": [
            {
                "market_name": r.market_name,
                "pick": r.pick,
                "probability": r.probability,
                "confidence": r.confidence,
                "implied_odds": r.implied_odds,
                "rationale": r.rationale,
                "risk_factors": r.risk_factors
            }
            for r in a.market_recommendations
        ]
    }

    context.update({
        "analysis": analysis_data
    })

    return templates.TemplateResponse(request=request, name="match_detail.html", context=context)


@router.get("/picks", response_class=HTMLResponse)
def show_picks(
    request: Request,
    min_confidence: str = Query("HIGH", description="Confidence threshold"),
    league: str | None = Query(None, description="League slug filter"),
    market: str | None = Query(None, description="Market code filter"),
    db: Database = Depends(get_db),
):
    """Render list of high confidence system recommendations."""
    onboarding_complete = (db.get_setting("onboarding_complete") == "1")
    if not onboarding_complete:
        return RedirectResponse(url="/onboarding")

    context = get_view_context(request, db, "picks")

    # Active preferences lists
    active_leagues_prefs = db.get_league_preferences()
    active_leagues = [lg for lg in active_leagues_prefs if lg["enabled"]]

    active_markets_prefs = db.get_market_preferences()
    active_markets = [mk for mk in active_markets_prefs if mk["enabled"]]

    # Fetch predictions matching from DB
    from_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    to_date = (datetime.now(timezone.utc) + timedelta(days=7)).strftime("%Y-%m-%d")

    raw_picks = db.get_high_confidence_predictions(
        min_confidence=min_confidence.upper(),
        from_date=from_date,
        to_date=to_date,
    )

    picks = []
    # Convert config display names to slugs mapping for matching
    league_slug_map = {cfg.name: slug for slug, cfg in LEAGUES.items()}
    enabled_slugs = {lg["slug"] for lg in active_leagues}

    for p in raw_picks:
        match_league_slug = league_slug_map.get(p.get("league_name"))
        
        # Filter out disabled leagues
        if match_league_slug not in enabled_slugs:
            continue
        
        if league and match_league_slug != league:
            continue

        if market and p.get("market") != market:
            continue

        picks.append({
            "match_id": p["match_id"],
            "home_team": p["home_team_name"],
            "away_team": p["away_team_name"],
            "league": p["league_name"],
            "match_date": p["date"].replace("T", " ")[:16],
            "market_code": p["market"],
            "market_name": p["market"].replace("-", " ").title(),
            "pick": p["pick"],
            "probability": p["probability"],
            "implied_odds": p["implied_odds"],
            "confidence": p["confidence"],
            "rationale": p["rationale"] or "",
        })

    context.update({
        "picks": picks,
        "leagues": active_leagues,
        "markets": active_markets,
        "min_confidence": min_confidence.upper(),
        "selected_league": league,
        "selected_market": market,
    })

    return templates.TemplateResponse(request=request, name="picks.html", context=context)


@router.get("/statistics", response_class=HTMLResponse)
def show_statistics(request: Request, db: Database = Depends(get_db)):
    """Render overall accuracy and factor calibration details."""
    onboarding_complete = (db.get_setting("onboarding_complete") == "1")
    if not onboarding_complete:
        return RedirectResponse(url="/onboarding")

    context = get_view_context(request, db, "statistics")

    tracker = AccuracyTracker(db)
    dashboard = tracker.get_dashboard()

    cal = Calibrator(db)
    readiness = cal.get_calibration_readiness()
    
    # Retrieve current active weight settings
    factor_weights = cal.load_weights()

    # Calculate ROI simulator details
    roi_simulation = tracker.get_roi_simulation()
    roi_est = roi_simulation.get("roi_pct", 0.0) / 100.0 if roi_simulation else None

    context.update({
        "dashboard": dashboard,
        "readiness": readiness,
        "factor_weights": factor_weights,
        "roi_est": roi_est,
    })

    return templates.TemplateResponse(request=request, name="statistics.html", context=context)


@router.get("/settings", response_class=HTMLResponse)
def show_settings(request: Request, db: Database = Depends(get_db)):
    """Render settings preferences page."""
    onboarding_complete = (db.get_setting("onboarding_complete") == "1")
    if not onboarding_complete:
        return RedirectResponse(url="/onboarding")

    context = get_view_context(request, db, "settings")

    # Fetch preferences lists
    raw_leagues = db.get_league_preferences()
    raw_markets = db.get_market_preferences()

    context.update({
        "leagues": raw_leagues,
        "markets": raw_markets,
    })

    return templates.TemplateResponse(request=request, name="settings.html", context=context)


@router.get("/parlay", response_class=HTMLResponse)
def show_parlay(
    request: Request,
    size: int = Query(50, description="Target parlay size"),
    min_confidence: str = Query("MEDIUM", description="Minimum confidence"),
    db: Database = Depends(get_db),
    engine: AnalysisEngine = Depends(get_engine),
):
    """Render the Parlay Builder page."""
    onboarding_complete = (db.get_setting("onboarding_complete") == "1")
    if not onboarding_complete:
        return RedirectResponse(url="/onboarding")

    context = get_view_context(request, db, "parlay")

    from_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    to_date = (datetime.now(timezone.utc) + timedelta(days=7)).strftime("%Y-%m-%d")

    analyses = engine.analyze_matchweek(from_date=from_date, to_date=to_date)
    
    # Filter analyses by active/enabled leagues
    active_leagues_prefs = db.get_league_preferences()
    enabled_slugs = {lg["slug"] for lg in active_leagues_prefs if lg["enabled"]}
    league_slug_map = {cfg.name: slug for slug, cfg in LEAGUES.items()}
    analyses = [a for a in analyses if league_slug_map.get(a.league) in enabled_slugs]

    suggestions = engine.suggest_parlay(analyses, size=size, min_confidence=min_confidence.upper())

    # Format suggested picks
    suggested_picks = []
    for a, rec in suggestions:
        suggested_picks.append({
            "match_id": a.match_id,
            "home_team": a.home_team,
            "away_team": a.away_team,
            "league": a.league,
            "market_code": rec.market_code,
            "market_name": rec.market_name,
            "pick": rec.pick,
            "probability": rec.probability,
            "confidence": rec.confidence,
            "implied_odds": rec.implied_odds
        })

    # Available picks are ALL recommendations from the analyses that match the active leagues
    # and have at least the min_confidence.
    available_picks = []
    level_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    min_level = level_order.get(min_confidence.upper(), 1)
    
    for a in analyses:
        for rec in a.market_recommendations:
            if level_order.get(rec.confidence, 2) <= min_level:
                available_picks.append({
                    "match_id": a.match_id,
                    "home_team": a.home_team,
                    "away_team": a.away_team,
                    "league": a.league,
                    "market_code": rec.market_code,
                    "market_name": rec.market_name,
                    "pick": rec.pick,
                    "probability": rec.probability,
                    "confidence": rec.confidence,
                    "implied_odds": rec.implied_odds
                })

    # Sort available picks by probability descending
    available_picks.sort(key=lambda x: -x["probability"])

    context.update({
        "suggested_picks": suggested_picks,
        "available_picks": available_picks,
        "size": size,
        "min_confidence": min_confidence,
    })

    return templates.TemplateResponse(request=request, name="parlay.html", context=context)


@router.get("/parlay/history", response_class=HTMLResponse)
def show_parlay_history(
    request: Request,
    db: Database = Depends(get_db),
):
    """Render the Parlay History page."""
    onboarding_complete = (db.get_setting("onboarding_complete") == "1")
    if not onboarding_complete:
        return RedirectResponse(url="/onboarding")

    context = get_view_context(request, db, "parlay")

    parlays = db.get_all_parlays()

    formatted_parlays = []
    for p in parlays:
        combined_prob = 1.0
        combined_odds = 1.0
        for item in p["picks"]:
            combined_prob *= item.get("probability", 1.0)
            combined_odds *= item.get("implied_odds", 1.0)

        roi = None
        if p["status"] == "won":
            roi = combined_odds - 1.0
        elif p["status"] == "lost":
            roi = -1.0

        formatted_parlays.append({
            "id": p["id"],
            "name": p["name"],
            "created_at": p["created_at"][:16].replace("T", " ") if p["created_at"] else "",
            "submitted_at": p["submitted_at"][:16].replace("T", " ") if p["submitted_at"] else None,
            "size": p["size"],
            "picks": p["picks"],
            "status": p["status"],
            "correct_count": p["correct_count"],
            "notes": p["notes"],
            "combined_probability": combined_prob,
            "combined_odds": combined_odds,
            "roi": roi,
        })

    context.update({
        "parlays": formatted_parlays,
    })

    return templates.TemplateResponse(request=request, name="parlay_history.html", context=context)
