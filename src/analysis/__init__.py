"""
Footy Predict — Analysis Package

The analytical engine: factors evaluate individual match dimensions,
the engine orchestrates them, and market models translate insights
into specific betting market recommendations.
"""

from src.analysis.factors import ALL_FACTORS, FactorResult
from src.analysis.engine import AnalysisEngine, MatchAnalysis
from src.analysis.markets import MarketRecommendation, evaluate_all_markets

__all__ = [
    "ALL_FACTORS",
    "AnalysisEngine",
    "FactorResult",
    "MatchAnalysis",
    "MarketRecommendation",
    "evaluate_all_markets",
]
