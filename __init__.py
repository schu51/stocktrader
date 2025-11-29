"""
Trading Agent Data Infrastructure
=================================

A modular data layer for equity research and trading decisions.

Quick Start:
    from data_infrastructure import DataOrchestrator
    
    # Initialize (uses default config)
    orchestrator = DataOrchestrator()
    
    # Get comprehensive screening data for a stock
    data = orchestrator.get_screening_data("TOST")
    
    # Access unified metrics
    print(data["unified"]["valuation"])
    print(data["unified"]["growth"])
    print(data["unified"]["quality"])
    
    # Batch fetch for multiple stocks
    universe_data = orchestrator.get_screening_data_batch(["TOST", "VRT", "S"])
    
    # Run quantitative screen
    screen_results = orchestrator.run_quantitative_screen()

Data Sources:
    - Yahoo Finance (primary): Prices, fundamentals, analyst data
    - SEC EDGAR: Regulatory filings, insider transactions
    - Financial Modeling Prep: Ratios, growth metrics, estimates
    - Finnhub: News, sentiment, earnings calendar
    - Web Scraper: Finviz data, short interest

Configuration:
    Set API keys via environment variables:
        ALPHA_VANTAGE_API_KEY
        FMP_API_KEY
        FINNHUB_API_KEY
        FRED_API_KEY
    
    Or pass directly to DataOrchestrator:
        orchestrator = DataOrchestrator(api_keys={"fmp": "your_key"})
"""

from .orchestrator import DataOrchestrator
from .config import (
    API_KEYS,
    RATE_LIMITS,
    DEFAULT_UNIVERSE,
    SECTOR_MAP,
    SCREENING_CRITERIA_MAP,
    DATA_DIR,
    DATABASE_PATH,
)
from .storage import Database
from .fetchers import (
    YahooFinanceFetcher,
    SECEdgarFetcher,
    FMPFetcher,
    FinnhubFetcher,
    WebScraper,
)

__version__ = "0.1.0"
__all__ = [
    "DataOrchestrator",
    "Database",
    "YahooFinanceFetcher",
    "SECEdgarFetcher",
    "FMPFetcher",
    "FinnhubFetcher",
    "WebScraper",
    "API_KEYS",
    "RATE_LIMITS",
    "DEFAULT_UNIVERSE",
    "SECTOR_MAP",
    "SCREENING_CRITERIA_MAP",
]
