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
    "yahoo_finance": {"requests": 2000, "window_seconds": 3600},
    "alpha_vantage": {"requests": 5, "window_seconds": 60},
    "fmp": {"requests": 5, "window_seconds": 1},
    "finnhub": {"requests": 60, "window_seconds": 60},
    "fred": {"requests": 120, "window_seconds": 60},
    "sec_edgar": {"requests": 10, "window_seconds": 1},
    "web_scraper": {"requests": 1, "window_seconds": 2},
}

# =============================================================================
# STORAGE SETTINGS
# =============================================================================

DATA_DIR = Path(__file__).parent.parent / "data"
DATABASE_PATH = DATA_DIR / "trading_agent.db"
CACHE_DIR = DATA_DIR / "cache"

CACHE_EXPIRATION = {
    "price_data": 300,
    "daily_prices": 86400,
    "fundamentals": 86400 * 7,
    "sec_filings": 86400,
    "news": 3600,
    "earnings_calendar": 3600,
}

# =============================================================================
# DATA FIELD MAPPINGS (standardized field names)
# =============================================================================

STANDARDIZED_FIELDS = {
    "open": "open",
    "high": "high",
    "low": "low",
    "close": "close",
    "adj_close": "adj_close",
    "volume": "volume",
    "revenue": "revenue",
    "gross_profit": "gross_profit",
    "operating_income": "operating_income",
    "net_income": "net_income",
    "ebitda": "ebitda",
    "eps": "eps",
    "eps_diluted": "eps_diluted",
    "total_assets": "total_assets",
    "total_liabilities": "total_liabilities",
    "total_equity": "total_equity",
    "cash": "cash",
    "total_debt": "total_debt",
    "operating_cash_flow": "operating_cash_flow",
    "free_cash_flow": "free_cash_flow",
    "capex": "capex",
    "market_cap": "market_cap",
    "pe_ratio": "pe_ratio",
    "ps_ratio": "ps_ratio",
    "pb_ratio": "pb_ratio",
    "ev_ebitda": "ev_ebitda",
    "peg_ratio": "peg_ratio",
    "revenue_growth": "revenue_growth",
    "earnings_growth": "earnings_growth",
    "revenue_growth_3y": "revenue_growth_3y",
    "gross_margin": "gross_margin",
    "operating_margin": "operating_margin",
    "net_margin": "net_margin",
    "roe": "roe",
    "roic": "roic",
    "roa": "roa",
}

# =============================================================================
# SCREENING CRITERIA - Momentum-friendly thresholds
# =============================================================================

SCREENING_CRITERIA_MAP = {
    # Market cap: liquid enough to enter/exit cleanly
    "market_cap_min": 2_000_000_000,      # $2B minimum
    "market_cap_max": 5_000_000_000_000,  # No ceiling on mega-caps

    # Growth: momentum stocks grow fast
    "revenue_growth_min": 0.10,            # 10% YoY minimum (flexible)

    # Quality floor: we're buying strength, not cheapness
    "gross_margin_min": 0.25,              # 25% minimum (allows hardware/infra names)

    # Liquidity: must be tradeable without slippage
    "avg_volume_min": 1_000_000,           # 1M avg daily volume minimum

    # Scoring weights (used when research scoring is applied as a filter)
    "scoring_weights": {
        "product_market_fit": 0.25,
        "competitive_moat": 0.25,
        "intrinsic_value_asymmetry": 0.30,
        "management_quality": 0.20,
    }
}

# =============================================================================
# UNIVERSE - Expanded for momentum strategy
#
# Momentum trading requires a wide scan pool (70+ names) across multiple
# sectors so the model can find what is actually trending at any given time.
# A narrow SaaS-only universe creates correlation risk and misses rotations
# into semis, infrastructure, healthcare tech, industrials, etc.
# =============================================================================

DEFAULT_UNIVERSE = [
    # Mega-cap tech / AI (persistent momentum leaders)
    "NVDA", "META", "GOOGL", "MSFT", "AMZN", "AAPL", "TSLA",

    # Semiconductors (high-beta momentum)
    "AMD", "AVGO", "QCOM", "MRVL", "SMCI", "ON", "LAM", "AMAT", "KLAC", "ARM",

    # Cybersecurity
    "CRWD", "S", "ZS", "PANW", "FTNT", "CYBR", "OKTA",

    # Enterprise AI / Cloud software
    "DDOG", "SNOW", "MDB", "NET", "PATH", "TEAM", "NOW", "CRM", "WDAY",

    # Vertical SaaS
    "TOST", "PCOR", "APPF", "VEEV", "NCNO", "SAMSF",

    # AI infrastructure / Data center / Power
    "VRT", "APH", "ETN", "PWR", "CRWV", "APLD", "CEG", "VST", "ANET",

    # Fintech / Payments
    "SQ", "AFRM", "SOFI", "UPST", "HOOD", "NU", "PYPL", "V", "MA",

    # Consumer / Digital media
    "SHOP", "DUOL", "APP", "TTD", "RBLX", "SPOT", "PINS",

    # Healthcare tech
    "ISRG", "DXCM", "PODD", "RXRX", "TDOC",

    # Industrials / Defense / Infrastructure
    "HWM", "GE", "RTX", "LHX", "AXON", "KTOS",

    # Emerging / High-momentum themes
    "COIN", "MSTR", "PLTR", "RDDT",
]

# Sector classifications (updated to match expanded universe)
SECTOR_MAP = {
    "mega_cap_tech": ["NVDA", "META", "GOOGL", "MSFT", "AMZN", "AAPL", "TSLA"],
    "semiconductors": ["AMD", "AVGO", "QCOM", "MRVL", "SMCI", "ON", "LAM", "AMAT", "KLAC", "ARM"],
    "cybersecurity": ["CRWD", "S", "ZS", "PANW", "FTNT", "CYBR", "OKTA"],
    "enterprise_ai_software": ["DDOG", "SNOW", "MDB", "NET", "PATH", "TEAM", "NOW", "CRM", "WDAY"],
    "vertical_saas": ["TOST", "PCOR", "APPF", "VEEV", "NCNO", "SAMSF"],
    "ai_infrastructure": ["VRT", "APH", "ETN", "PWR", "CRWV", "APLD", "CEG", "VST", "ANET"],
    "fintech_payments": ["SQ", "AFRM", "SOFI", "UPST", "HOOD", "NU", "PYPL", "V", "MA"],
    "consumer_digital": ["SHOP", "DUOL", "APP", "TTD", "RBLX", "SPOT", "PINS"],
    "healthcare_tech": ["ISRG", "DXCM", "PODD", "RXRX", "TDOC"],
    "industrials_defense": ["HWM", "GE", "RTX", "LHX", "AXON", "KTOS"],
    "emerging_themes": ["COIN", "MSTR", "PLTR", "RDDT"],
}
