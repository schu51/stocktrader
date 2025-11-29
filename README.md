# Trading Agent Data Infrastructure

A modular, extensible data layer for equity research and trading decisions.

## Architecture Overview

```
data_infrastructure/
├── __init__.py           # Public API exports
├── config.py             # Configuration, API keys, rate limits
├── orchestrator.py       # Central coordinator for all data fetching
├── demo.py               # Demo/test script
├── requirements.txt      # Python dependencies
│
├── fetchers/             # Data source modules
│   ├── yahoo_finance.py  # Yahoo Finance (prices, fundamentals, news)
│   ├── sec_edgar.py      # SEC EDGAR (filings, insider transactions)
│   ├── fmp.py            # Financial Modeling Prep (ratios, estimates)
│   ├── finnhub.py        # Finnhub (news, sentiment, earnings)
│   └── web_scraper.py    # Web scraping (Finviz, etc.)
│
├── storage/              # Persistence layer
│   └── database.py       # SQLite database for historical data
│
├── utils/                # Utility modules
│   ├── rate_limiter.py   # Token bucket rate limiter
│   └── cache.py          # Memory + file caching
│
└── data/                 # Data directory (created at runtime)
    ├── trading_agent.db  # SQLite database
    └── cache/            # Cached API responses
```

## Quick Start

```python
from data_infrastructure import DataOrchestrator

# Initialize
orchestrator = DataOrchestrator()

# Get comprehensive screening data for a stock
data = orchestrator.get_screening_data("TOST")

# Access unified metrics (merged from all sources)
print(data["unified"]["valuation"])
print(data["unified"]["growth"])
print(data["unified"]["quality"])
print(data["unified"]["analyst"])

# Batch fetch for multiple stocks
universe_data = orchestrator.get_screening_data_batch(["TOST", "VRT", "S"])

# Run quantitative screen (Stage 1C integration)
screen_results = orchestrator.run_quantitative_screen()
```

## Data Sources

| Source | API Key Required | Rate Limit | Data Provided |
|--------|-----------------|------------|---------------|
| Yahoo Finance | No | ~2000/hour | Prices, fundamentals, analyst data, news |
| SEC EDGAR | No (needs User-Agent) | 10/second | Filings, insider transactions, XBRL data |
| Financial Modeling Prep | Yes (free tier) | 250/day | Ratios, growth metrics, estimates |
| Finnhub | Yes (free tier) | 60/minute | News sentiment, earnings calendar |
| Finviz (scraper) | No | 1/2 seconds | Technical data, short interest |

## Configuration

### Environment Variables

```bash
export FMP_API_KEY="your_fmp_key"
export FINNHUB_API_KEY="your_finnhub_key"
export ALPHA_VANTAGE_API_KEY="your_av_key"  # Optional
export FRED_API_KEY="your_fred_key"          # Optional
```

### Direct Configuration

```python
orchestrator = DataOrchestrator(
    api_keys={
        "fmp": "your_fmp_key",
        "finnhub": "your_finnhub_key"
    }
)
```

## Research Engine Integration

The data infrastructure is designed to integrate with the equity research engine:

### Stage 1C - Quantitative Screen

```python
# Run automated quantitative screen
results = orchestrator.run_quantitative_screen(
    symbols=["TOST", "VRT", "S", "CRWD", "DDOG"],
    criteria={
        "market_cap_min": 500_000_000,      # $500M minimum
        "market_cap_max": 100_000_000_000,  # $100B maximum
        "revenue_growth_min": 0.15,          # 15% minimum
        "gross_margin_min": 0.40,            # 40% minimum
    }
)

print(results["passing"])  # Symbols that pass all criteria
print(results["details"])  # Detailed breakdown per symbol
```

### Stage 1D - Qualitative Data

```python
# Get comprehensive data for qualitative scoring
data = orchestrator.get_screening_data("TOST")

# Access for scoring:
# - Product-Market Fit: data["unified"]["growth"], news, earnings surprises
# - Competitive Moat: data["unified"]["quality"], margins, market position
# - Intrinsic Value: data["unified"]["valuation"], analyst targets
# - Management: SEC filings, insider transactions, earnings consistency
```

### Stage 2 - Deep Dive

```python
# Get detailed financials
financials = orchestrator.get_financials("TOST")

# Get SEC filings for deep research
sec_data = orchestrator.sec.get_key_financials("TOST")

# Get filing documents
filings_10k = orchestrator.sec.get_10k_filings("TOST", limit=3)
filings_8k = orchestrator.sec.get_8k_filings("TOST", limit=20)
```

## Unified Data Format

The `get_screening_data()` method returns a unified data structure:

```python
{
    "symbol": "TOST",
    "fetched_at": "2025-11-28T...",
    "sources_used": ["yahoo", "fmp", "finnhub", "sec", "scraper"],
    
    "unified": {
        "company": {
            "name": "Toast, Inc.",
            "sector": "Technology",
            "industry": "Software - Application",
            "market_cap": 25000000000,
            "exchange": "NYSE"
        },
        
        "valuation": {
            "pe_ratio": 114.5,
            "forward_pe": 85.2,
            "ps_ratio": 5.8,
            "pb_ratio": 12.3,
            "ev_ebitda": 65.4,
            "peg_ratio": 2.1
        },
        
        "growth": {
            "revenue_growth": 0.30,
            "earnings_growth": 0.45,
            "revenue_growth_3y": 0.35,
            "revenue_growth_5y": 0.42
        },
        
        "quality": {
            "gross_margin": 0.35,
            "operating_margin": 0.08,
            "net_margin": 0.05,
            "roe": 0.12,
            "roa": 0.06,
            "roic": 0.10
        },
        
        "analyst": {
            "recommendation": "buy",
            "num_analysts": 25,
            "target_mean": 55.0,
            "target_high": 70.0,
            "target_low": 40.0,
            "current_price": 42.0,
            "upside_pct": 31.0
        },
        
        "risk": {
            "beta": 1.8,
            "52_week_high": 48.0,
            "52_week_low": 18.0,
            "short_float": 0.05
        },
        
        "sentiment": {
            "news_sentiment": 0.65,
            "insider_sentiment": 15000
        },
        
        "filings": {
            "last_10k_date": "2025-02-28",
            "last_10q_date": "2025-11-05",
            "days_since_10q": 23,
            "recent_8k_count": 8
        }
    },
    
    # Raw data from each source also available
    "yahoo": {...},
    "fmp": {...},
    "finnhub": {...},
    "sec": {...},
    "scraper": {...}
}
```

## Database Schema

The infrastructure persists data in SQLite for historical tracking:

- `daily_prices` - OHLCV price history
- `fundamentals` - Quarterly/annual financial data
- `company_profiles` - Company metadata
- `valuation_metrics` - Time-series valuation data
- `earnings_calendar` - Earnings dates and estimates
- `news` - News articles and sentiment
- `insider_transactions` - Form 4 filings
- `institutional_holdings` - 13F data
- `research_scores` - Stage 1D scoring results
- `watchlist` - Active positions and targets
- `fetch_log` - API call tracking

## Running the Demo

```bash
# Install dependencies
pip install -r requirements.txt

# Run demo (default: orchestrator test)
python demo.py

# Test specific symbol
python demo.py --symbol VRT

# Quick test (Yahoo only)
python demo.py --quick

# Batch operations demo
python demo.py --batch

# Full demo (all components)
python demo.py --full

# Test specific component
python demo.py --component yahoo
python demo.py --component sec
python demo.py --component scraper
```

## Next Steps (Future Development)

1. **Execution Layer** - Brokerage API integration (Alpaca, IBKR)
2. **State/Memory** - Position tracking, decision history
3. **Decision Framework** - Automated buy/sell logic
4. **Monitoring** - Real-time alerts, portfolio health
5. **Risk Management** - Position sizing, correlation limits

## Free API Tier Limits

- **Yahoo Finance**: Unlimited (yfinance library)
- **SEC EDGAR**: 10 req/sec (requires User-Agent)
- **Financial Modeling Prep**: 250 req/day (free tier)
- **Finnhub**: 60 req/min (free tier)
- **Alpha Vantage**: 25 req/day (free tier)

For production use, consider upgrading to paid tiers.
