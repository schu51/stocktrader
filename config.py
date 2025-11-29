"""
Data Infrastructure Configuration
=================================
API keys and settings for the trading agent data layer.

FREE API TIERS USED:
- Yahoo Finance (yfinance): Unlimited, no key required
- Alpha Vantage: 25 requests/day (free tier)
- Financial Modeling Prep: 250 requests/day (free tier)
- Finnhub: 60 requests/minute (free tier)
- FRED: 120 requests/minute (free, key required)
- SEC EDGAR: 10 requests/second (no key, requires User-Agent)

SETUP INSTRUCTIONS:
1. Alpha Vantage: https://www.alphavantage.co/support/#api-key
2. Financial Modeling Prep: https://site.financialmodelingprep.com/developer/docs
3. Finnhub: https://finnhub.io/register
4. FRED: https://fred.stlouisfed.org/docs/api/api_key.html
"""

import os
from pathlib import Path

# =============================================================================
# API KEYS - Set via environment variables or directly here
# =============================================================================

API_KEYS = {
    "alpha_vantage": os.getenv("ALPHA_VANTAGE_API_KEY", "demo"),
    "fmp": os.getenv("FMP_API_KEY", "demo"),
    "finnhub": os.getenv("FINNHUB_API_KEY", ""),
    "fred": os.getenv("FRED_API_KEY", ""),
}

# SEC EDGAR requires a User-Agent header with contact info
SEC_EDGAR_USER_AGENT = os.getenv(
    "SEC_EDGAR_USER_AGENT", 
    "TradingAgent/1.0 (contact@example.com)"
)

# =============================================================================
# RATE LIMITS (requests per time window)
# =============================================================================

RATE_LIMITS = {
    "yahoo_finance": {"requests": 2000, "window_seconds": 3600},  # ~2000/hour practical
    "alpha_vantage": {"requests": 5, "window_seconds": 60},  # 5/min, 25/day free
    "fmp": {"requests": 5, "window_seconds": 1},  # 250/day free
    "finnhub": {"requests": 60, "window_seconds": 60},
    "fred": {"requests": 120, "window_seconds": 60},
    "sec_edgar": {"requests": 10, "window_seconds": 1},
    "web_scraper": {"requests": 1, "window_seconds": 2},  # Be polite
}

# =============================================================================
# STORAGE SETTINGS
# =============================================================================

DATA_DIR = Path(__file__).parent.parent / "data"
DATABASE_PATH = DATA_DIR / "trading_agent.db"
CACHE_DIR = DATA_DIR / "cache"

# Cache expiration (seconds)
CACHE_EXPIRATION = {
    "price_data": 300,  # 5 minutes for real-time prices
    "daily_prices": 86400,  # 24 hours for daily OHLCV
    "fundamentals": 86400 * 7,  # 7 days for financials
    "sec_filings": 86400,  # 24 hours for filing lists
    "news": 3600,  # 1 hour for news
    "earnings_calendar": 3600,  # 1 hour
}

# =============================================================================
# DATA FIELD MAPPINGS (standardized field names)
# =============================================================================

STANDARDIZED_FIELDS = {
    # Price data
    "open": "open",
    "high": "high", 
    "low": "low",
    "close": "close",
    "adj_close": "adj_close",
    "volume": "volume",
    
    # Fundamentals - Income Statement
    "revenue": "revenue",
    "gross_profit": "gross_profit",
    "operating_income": "operating_income",
    "net_income": "net_income",
    "ebitda": "ebitda",
    "eps": "eps",
    "eps_diluted": "eps_diluted",
    
    # Fundamentals - Balance Sheet
    "total_assets": "total_assets",
    "total_liabilities": "total_liabilities",
    "total_equity": "total_equity",
    "cash": "cash",
    "total_debt": "total_debt",
    
    # Fundamentals - Cash Flow
    "operating_cash_flow": "operating_cash_flow",
    "free_cash_flow": "free_cash_flow",
    "capex": "capex",
    
    # Valuation Metrics
    "market_cap": "market_cap",
    "pe_ratio": "pe_ratio",
    "ps_ratio": "ps_ratio",
    "pb_ratio": "pb_ratio",
    "ev_ebitda": "ev_ebitda",
    "peg_ratio": "peg_ratio",
    
    # Growth Metrics
    "revenue_growth": "revenue_growth",
    "earnings_growth": "earnings_growth",
    "revenue_growth_3y": "revenue_growth_3y",
    
    # Quality Metrics
    "gross_margin": "gross_margin",
    "operating_margin": "operating_margin",
    "net_margin": "net_margin",
    "roe": "roe",
    "roic": "roic",
    "roa": "roa",
}

# =============================================================================
# RESEARCH ENGINE INTEGRATION - Maps data to screening criteria
# =============================================================================

SCREENING_CRITERIA_MAP = {
    # Stage 1C Quantitative Criteria
    "market_cap_min": 500_000_000,  # $500M minimum
    "market_cap_max": 100_000_000_000,  # $100B maximum
    "revenue_growth_min": 0.15,  # 15% minimum
    "gross_margin_min": 0.40,  # 40% minimum for SaaS
    "net_retention_min": 1.10,  # 110% NRR
    
    # Stage 1D Qualitative Scoring Weights
    "scoring_weights": {
        "product_market_fit": 0.25,
        "competitive_moat": 0.25,
        "intrinsic_value_asymmetry": 0.30,
        "management_quality": 0.20,
    }
}

# =============================================================================
# UNIVERSE DEFINITIONS
# =============================================================================

# Default universe for screening
DEFAULT_UNIVERSE = [
    # Cybersecurity
    "CRWD", "S", "ZS", "PANW", "FTNT", "CYBR", "OKTA", "QLYS",
    # Enterprise AI/Software
    "DDOG", "SNOW", "MDB", "NET", "PATH", "TEAM",
    # Vertical SaaS
    "TOST", "PCOR", "APPF", "VEEV", "NCNO",
    # AI Infrastructure
    "VRT", "APH", "ETN", "PWR", "CRWV", "APLD",
]

# Sector classifications
SECTOR_MAP = {
    "cybersecurity": ["CRWD", "S", "ZS", "PANW", "FTNT", "CYBR", "OKTA", "QLYS"],
    "enterprise_ai_software": ["DDOG", "SNOW", "MDB", "NET", "PATH", "TEAM"],
    "vertical_saas": ["TOST", "PCOR", "APPF", "VEEV", "NCNO"],
    "ai_infrastructure": ["VRT", "APH", "ETN", "PWR", "CRWV", "APLD"],
}
