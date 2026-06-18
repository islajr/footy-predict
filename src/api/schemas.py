from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field


class FactorResultSchema(BaseModel):
    name: str
    score: float
    confidence: float
    rationale: str
    data_points: dict[str, Any]


class MarketRecommendationSchema(BaseModel):
    market_code: str
    market_name: str
    pick: str
    probability: float
    confidence: str
    implied_odds: float
    rationale: str
    risk_factors: list[str]
    supporting_stats: dict[str, Any]


class MatchAnalysisSchema(BaseModel):
    match_id: int
    home_team: str
    away_team: str
    league: str
    match_date: str
    factor_results: list[FactorResultSchema]
    overall_home_score: float
    overall_confidence: float
    market_recommendations: list[MarketRecommendationSchema]
    risk_flags: list[str]
    data_quality: str
    summary: str


class PickResponseSchema(BaseModel):
    match_id: int
    home_team: str
    away_team: str
    league: str
    match_date: str
    market_code: str
    market_name: str
    pick: str
    probability: float
    implied_odds: float
    confidence: str
    rationale: str


class QuotaStatusSchema(BaseModel):
    provider: str
    used_today: int
    daily_limit: int
    remaining: int


class DatabaseStatsSchema(BaseModel):
    leagues_count: int
    teams_count: int
    matches_count: int
    finished_count: int
    upcoming_count: int
    predictions_count: int


class SettingsSchema(BaseModel):
    api_football_key: str
    football_data_org_key: str
    theme: str
    onboarding_complete: bool
    fetch_strategy: str


class LeaguePreferenceSchema(BaseModel):
    name: str
    country: str
    slug: str
    enabled: bool
    display_order: int


class MarketPreferenceSchema(BaseModel):
    market_code: str
    display_name: str
    enabled: bool
    min_probability: float
    display_order: int


class KeyTestRequestSchema(BaseModel):
    provider: str
    key: str


class KeyTestResponseSchema(BaseModel):
    valid: bool
    quota_remaining: int
    error: str | None = None


class ParlayItemSchema(BaseModel):
    match_id: int
    home_team: str
    away_team: str
    league: str
    market_code: str
    market_name: str
    pick: str
    probability: float
    confidence: str
    implied_odds: float


class ParlaySchema(BaseModel):
    id: int | None = None
    name: str
    created_at: str | None = None
    submitted_at: str | None = None
    size: int
    picks: list[ParlayItemSchema]
    status: str
    correct_count: int | None = None
    notes: str | None = None
