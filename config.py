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

# =============================================================================
# TRADING ENGINE TYPES
# Enums and dataclasses used by signals.py, decision_engine.py, models.py,
# position_sizing.py, and risk_manager.py.
# =============================================================================

from enum import Enum
from dataclasses import dataclass, field
from typing import Dict


class Signal(Enum):
    STRONG_BUY  = "STRONG_BUY"
    BUY         = "BUY"
    HOLD        = "HOLD"
    SELL        = "SELL"
    STRONG_SELL = "STRONG_SELL"


class ConvictionTier(Enum):
    HIGH        = "HIGH"
    MEDIUM      = "MEDIUM"
    LOW         = "LOW"
    SPECULATIVE = "SPECULATIVE"


class PositionStatus(Enum):
    OPEN    = "OPEN"
    CLOSED  = "CLOSED"
    PENDING = "PENDING"


class DecisionType(Enum):
    INITIATE        = "INITIATE"
    ADD_TO_POSITION = "ADD_TO_POSITION"
    REDUCE          = "REDUCE"
    REDUCE_POSITION = "REDUCE_POSITION"
    EXIT            = "EXIT"
    EXIT_POSITION   = "EXIT_POSITION"
    STOP_LOSS       = "STOP_LOSS"
    TAKE_PROFIT     = "TAKE_PROFIT"
    HOLD            = "HOLD"


@dataclass
class SignalThresholds:
    strong_buy_score:            float = 4.5
    buy_score:                   float = 3.8
    hold_score_min:              float = 3.0
    sell_score:                  float = 2.5
    strong_buy_upside:           float = 0.30
    buy_upside:                  float = 0.15
    sell_downside:               float = -0.10
    bullish_sentiment_threshold: float = 0.60
    bearish_sentiment_threshold: float = 0.40
    momentum_buy_threshold:      float = 0.05
    momentum_sell_threshold:     float = -0.05


@dataclass
class MomentumConfig:
    macd_fast_period:              int   = 12
    macd_slow_period:              int   = 26
    macd_signal_period:            int   = 9
    rsi_period:                    int   = 14
    trend_confirmation_threshold:  float = 60.0  # Raised to reduce over-signaling


@dataclass
class ProductValueOverride:
    enabled:                bool  = False  # Disabled in momentum strategy
    min_overall_score:      float = 4.5
    min_product_market_fit: float = 4.5


@dataclass
class PositionSizingConfig:
    target_volatility:           float = 0.15
    max_position_volatility:     float = 0.30
    kelly_fraction:              float = 0.25
    min_edge:                    float = 0.0
    scale_in_tranches:           int   = 1
    scale_in_threshold:          float = 0.05
    target_position_by_conviction: Dict = field(default_factory=lambda: {
        ConvictionTier.HIGH:        0.05,
        ConvictionTier.MEDIUM:      0.03,
        ConvictionTier.LOW:         0.02,
        ConvictionTier.SPECULATIVE: 0.01,
    })
    max_position_by_conviction: Dict = field(default_factory=lambda: {
        ConvictionTier.HIGH:        0.08,
        ConvictionTier.MEDIUM:      0.05,
        ConvictionTier.LOW:         0.03,
        ConvictionTier.SPECULATIVE: 0.02,
    })


@dataclass
class PortfolioConstraints:
    max_positions:         int   = 20
    max_single_position:   float = 0.08
    min_cash_allocation:   float = 0.05
    target_cash_allocation: float = 0.10
    top_5_max_allocation:  float = 0.40


@dataclass
class RiskConfig:
    max_risk_per_trade:          float = 0.02
    max_portfolio_drawdown:      float = 0.15
    max_daily_loss:              float = 0.03
    reduce_into_earnings:        bool  = True
    earnings_position_reduction: float = 0.50


@dataclass
class ExitRules:
    trailing_stop_activation: float = 0.15  # Activate after 15% gain
    trailing_stop_distance:   float = 0.08  # 8% trailing stop
    stop_loss_by_conviction: Dict = field(default_factory=lambda: {
        ConvictionTier.HIGH:        0.07,
        ConvictionTier.MEDIUM:      0.08,
        ConvictionTier.LOW:         0.10,
        ConvictionTier.SPECULATIVE: 0.12,
    })
    # Tightened trailing stop tiers (protect compounding gains aggressively)
    trail_100_pct:            float = 0.08   # 100%+ gain → 8% trail
    trail_50_pct:             float = 0.10   # 50%+ gain → 10% trail
    trail_25_pct:             float = 0.015  # 25%+ gain → break-even + 1.5%
    # Partial profit taking
    take_profit_partial:      float = 0.40
    take_profit_partial_size: float = 0.40
    # Time-stop loss: close if underwater after N days (capital discipline)
    time_stop_loss_days:      int   = 5      # Days before time-stop fires
    time_stop_loss_threshold: float = -0.05  # -5% triggers time-stop
    # Max holding period for review
    max_holding_period_days:  int   = 90


@dataclass
class EntryRules:
    # Research score minimum (0.0 = no requirement, rely on MA trend gate instead)
    min_overall_score:          float = 0.0
    # Max decline from 52w high allowed (0.99 = effectively disabled, MA gate handles this)
    max_decline_from_high_pct:  float = 0.99
    require_above_sma:          bool  = True
    max_pe_ratio:               float = 500.0
    max_ps_ratio:               float = 50.0
    min_upside_pct:             float = -100.0
    min_gross_margin:           float = 0.0
    min_revenue_growth:         float = 0.0
    # RSI overbought filter: skip entry if RSI above this threshold
    rsi_overbought_block:       float = 78.0
    # Earnings blackout: skip entry if earnings within this many days
    earnings_blackout_days:     int   = 5
    # Sector concentration: max allocation to any single sector
    max_sector_allocation:      float = 0.35  # 35% max in one sector


@dataclass
class DecisionConfig:
    signal_thresholds:     SignalThresholds     = field(default_factory=SignalThresholds)
    momentum_config:       MomentumConfig       = field(default_factory=MomentumConfig)
    product_override:      ProductValueOverride  = field(default_factory=ProductValueOverride)
    position_sizing:       PositionSizingConfig  = field(default_factory=PositionSizingConfig)
    portfolio_constraints: PortfolioConstraints  = field(default_factory=PortfolioConstraints)
    risk_config:           RiskConfig            = field(default_factory=RiskConfig)
    exit_rules:            ExitRules             = field(default_factory=ExitRules)
    entry_rules:           EntryRules            = field(default_factory=EntryRules)
    require_confirmation:  bool                  = False  # Auto-execute without manual confirm


DEFAULT_CONFIG = DecisionConfig()


# =============================================================================
# Sector classifications (updated to match expanded universe)
SECTOR_MAP = {
    # ── Tech / Growth (hand-curated, maps to Finviz "technology") ────────────
    "technology": [
        "NVDA", "META", "GOOGL", "MSFT", "AMZN", "AAPL", "TSLA",
        "AMD", "AVGO", "QCOM", "MRVL", "SMCI", "ON", "AMAT", "KLAC", "LRCX", "ARM", "TXN",
        "CRWD", "ZS", "PANW", "FTNT", "OKTA",
        "DDOG", "SNOW", "MDB", "NET", "NOW", "CRM", "WDAY", "TEAM", "PATH",
        "VRT", "APH", "ANET", "PLTR", "RDDT", "MSTR",
        "ORCL", "ADBE", "INTU", "CDNS", "SNPS", "NFLX", "UBER", "ABNB",
    ],
    # ── Healthcare ───────────────────────────────────────────────────────────
    "healthcare": [
        "LLY", "NVO", "ABBV", "UNH", "JNJ", "MRK", "TMO", "ABT", "DHR",
        "ISRG", "DXCM", "PODD", "REGN", "VRTX", "MRNA", "BIIB", "GILD",
        "HCA", "CI", "ELV", "CVS", "MCK",
    ],
    # ── Financials ───────────────────────────────────────────────────────────
    "financials": [
        "BRK-B", "JPM", "BAC", "WFC", "GS", "MS", "BLK", "SCHW", "AXP",
        "V", "MA", "PYPL", "COIN", "NU", "AFRM", "SOFI", "HOOD", "UPST",
        "CB", "PGR", "TRV", "AON", "MMC",
    ],
    # ── Consumer Cyclical ────────────────────────────────────────────────────
    "consumer_cyclical": [
        "AMZN", "TSLA", "HD", "MCD", "NKE", "SBUX", "LOW", "TJX", "BKNG",
        "SHOP", "DUOL", "APP", "TTD", "RBLX", "SPOT", "PINS",
        "ABNB", "LVS", "MGM", "CCL", "RCL",
    ],
    # ── Industrials ──────────────────────────────────────────────────────────
    "industrials": [
        "CAT", "DE", "UPS", "FDX", "HON", "GE", "MMM", "RTX", "LMT", "NOC",
        "ETN", "PWR", "URI", "GNRC", "HWM", "GEV",
        "AXON", "KTOS", "LHX",
    ],
    # ── Communication Services ───────────────────────────────────────────────
    "communication_services": [
        "GOOGL", "META", "NFLX", "DIS", "CMCSA", "T", "VZ", "TMUS",
        "SNAP", "PINS", "RDDT", "SPOT",
    ],
    # ── Consumer Defensive ───────────────────────────────────────────────────
    "consumer_defensive": [
        "COST", "WMT", "PG", "KO", "PEP", "PM", "MO", "CL", "GIS", "K",
    ],
    # ── Energy ───────────────────────────────────────────────────────────────
    "energy": [
        "XOM", "CVX", "COP", "EOG", "SLB", "OXY", "MPC", "VLO", "PSX",
        "CEG", "VST", "NEE", "AEP", "SO",
    ],
    # ── Basic Materials ──────────────────────────────────────────────────────
    "basic_materials": [
        "LIN", "APD", "SHW", "ECL", "NEM", "GOLD", "FCX", "AA", "NUE", "X",
    ],
    # ── Real Estate ──────────────────────────────────────────────────────────
    "real_estate": [
        "PLD", "AMT", "EQIX", "CCI", "SPG", "O", "WELL", "AVB", "EQR",
    ],
    # ── Utilities ────────────────────────────────────────────────────────────
    "utilities": [
        "NEE", "DUK", "SO", "AEP", "XEL", "SRE", "D", "PCG", "EXC",
    ],
}
