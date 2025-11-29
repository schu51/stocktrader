"""
Screening Module
================

Handles stock universe discovery and tracking.

Features:
- Curated seed watchlist
- Dynamic screening
- Persistent performer tracking
"""

from .universe_screener import UniverseScreener, ScreeningCriteria, Sector

__all__ = [
    "UniverseScreener",
    "ScreeningCriteria",
    "Sector"
]
