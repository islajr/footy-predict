from __future__ import annotations

import httpx
import logging
from typing import Any
from fastapi import APIRouter, Depends, HTTPException
from src.database import Database
from src.api.deps import get_db
from src.api.schemas import (
    SettingsSchema,
    LeaguePreferenceSchema,
    MarketPreferenceSchema,
    KeyTestRequestSchema,
    KeyTestResponseSchema,
)

logger = logging.getLogger("footy.api.settings")
router = APIRouter(prefix="/settings", tags=["Settings"])


def mask_key(key: str | None) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "••••••••"
    return f"{key[:4]}••••••••{key[-4:]}"


def is_masked(key: str | None) -> bool:
    if not key:
        return False
    return "•" in key or "*" in key


@router.get("", response_model=SettingsSchema)
def get_settings(db: Database = Depends(get_db)):
    """Retrieve all current user preferences and settings."""
    return SettingsSchema(
        api_football_key=mask_key(db.get_setting("api_football_key")),
        football_data_org_key=mask_key(db.get_setting("football_data_org_key")),
        theme=db.get_setting("theme") or "dark",
        onboarding_complete=(db.get_setting("onboarding_complete") == "1"),
        fetch_strategy=db.get_setting("fetch_strategy") or "full",
    )


@router.post("", response_model=SettingsSchema)
def update_settings(settings: SettingsSchema, db: Database = Depends(get_db)):
    """Update settings key-value pairs."""
    if not is_masked(settings.api_football_key):
        db.set_setting("api_football_key", settings.api_football_key)
    if not is_masked(settings.football_data_org_key):
        db.set_setting("football_data_org_key", settings.football_data_org_key)
    db.set_setting("theme", settings.theme)
    db.set_setting("onboarding_complete", "1" if settings.onboarding_complete else "0")
    db.set_setting("fetch_strategy", settings.fetch_strategy)
    return get_settings(db)


@router.post("/test-key", response_model=KeyTestResponseSchema)
async def test_api_key(req: KeyTestRequestSchema, db: Database = Depends(get_db)):
    """Test validity of a specific API key via remote ping."""
    provider = req.provider.lower()
    key = req.key.strip()

    if is_masked(key):
        key = db.get_setting(f"{provider}_key") or ""
        key = key.strip()

    if not key:
        return KeyTestResponseSchema(valid=False, quota_remaining=0, error="API key is blank.")

    async with httpx.AsyncClient(timeout=10.0) as client:
        if provider == "api_football":
            url = "https://v3.football.api-sports.io/status"
            headers = {"x-apisports-key": key, "Accept": "application/json"}
            try:
                resp = await client.get(url, headers=headers)
                if resp.status_code != 200:
                    return KeyTestResponseSchema(
                        valid=False,
                        quota_remaining=0,
                        error=f"API returned status code {resp.status_code}.",
                    )
                
                data = resp.json()
                # Check for error blocks in api-sports responses
                errors = data.get("errors", [])
                if errors:
                    err_detail = errors if isinstance(errors, str) else str(errors)
                    return KeyTestResponseSchema(
                        valid=False,
                        quota_remaining=0,
                        error=f"API-Football error: {err_detail}",
                    )
                
                # Retrieve quota info from response
                quota_remaining = 0
                response_data = data.get("response", {})
                if response_data:
                    requests_data = response_data.get("requests", {})
                    limit = requests_data.get("limit", 100)
                    current = requests_data.get("current", 0)
                    quota_remaining = max(0, limit - current)

                return KeyTestResponseSchema(valid=True, quota_remaining=quota_remaining)
            except Exception as e:
                return KeyTestResponseSchema(valid=False, quota_remaining=0, error=str(e))

        elif provider == "football_data_org":
            url = "https://api.football-data.org/v4/competitions"
            headers = {"X-Auth-Token": key, "Accept": "application/json"}
            try:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    # Football-Data.org status response headers track request counts
                    # but doesn't expose remaining daily quota directly in the free tier
                    return KeyTestResponseSchema(valid=True, quota_remaining=10)
                else:
                    return KeyTestResponseSchema(
                        valid=False,
                        quota_remaining=0,
                        error=f"API returned status code {resp.status_code}.",
                    )
            except Exception as e:
                return KeyTestResponseSchema(valid=False, quota_remaining=0, error=str(e))

        else:
            raise HTTPException(status_code=400, detail=f"Unknown provider: {provider}")


@router.get("/leagues", response_model=list[LeaguePreferenceSchema])
def get_leagues_preferences(db: Database = Depends(get_db)):
    """Retrieve list of tracked leagues and active state preferences."""
    prefs = db.get_league_preferences()
    return [
        LeaguePreferenceSchema(
            name=p["name"],
            country=p["country"],
            slug=p["slug"],
            enabled=bool(p["enabled"]),
            display_order=p["display_order"],
        )
        for p in prefs
    ]


@router.post("/leagues", response_model=list[LeaguePreferenceSchema])
def update_leagues_preferences(
    leagues: list[LeaguePreferenceSchema], db: Database = Depends(get_db)
):
    """Save active states and display order for leagues."""
    for lg in leagues:
        db.set_league_preference(
            slug=lg.slug,
            enabled=1 if lg.enabled else 0,
            display_order=lg.display_order,
        )
    return get_leagues_preferences(db)


@router.get("/markets", response_model=list[MarketPreferenceSchema])
def get_markets_preferences(db: Database = Depends(get_db)):
    """Retrieve list of available betting markets and floor configurations."""
    prefs = db.get_market_preferences()
    return [
        MarketPreferenceSchema(
            market_code=p["market_code"],
            display_name=p["display_name"] or p["market_code"].replace("-", " ").title(),
            enabled=bool(p["enabled"]),
            min_probability=p["min_probability"],
            display_order=p["display_order"],
        )
        for p in prefs
    ]


@router.post("/markets", response_model=list[MarketPreferenceSchema])
def update_markets_preferences(
    markets: list[MarketPreferenceSchema], db: Database = Depends(get_db)
):
    """Save active states, display labels, and confidence thresholds for markets."""
    for mk in markets:
        db.set_market_preference(
            market_code=mk.market_code,
            enabled=1 if mk.enabled else 0,
            min_probability=mk.min_probability,
            display_order=mk.display_order,
        )
    return get_markets_preferences(db)
