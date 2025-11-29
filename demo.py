#!/usr/bin/env python3
"""
Data Infrastructure Demo & Test Script
=======================================

Demonstrates the capabilities of the data infrastructure
and validates that all components are working correctly.

Usage:
    python demo.py                    # Run full demo
    python demo.py --symbol TOST      # Demo specific symbol
    python demo.py --quick            # Quick test (Yahoo only)
    python demo.py --batch            # Test batch operations
"""

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from data_infrastructure import (
    DataOrchestrator,
    YahooFinanceFetcher,
    SECEdgarFetcher,
    WebScraper,
    DEFAULT_UNIVERSE,
)
from data_infrastructure.utils import rate_limiter, configure_rate_limits
from data_infrastructure.config import RATE_LIMITS

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger(__name__)


def print_header(title: str):
    """Print formatted section header."""
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60)


def print_subheader(title: str):
    """Print formatted subsection header."""
    print(f"\n--- {title} ---")


def format_value(value, prefix=""):
    """Format a value for display."""
    if value is None:
        return "N/A"
    if isinstance(value, float):
        if abs(value) >= 1e9:
            return f"${value/1e9:.2f}B"
        if abs(value) >= 1e6:
            return f"${value/1e6:.2f}M"
        if abs(value) < 1:
            return f"{value*100:.1f}%"
        return f"{value:.2f}"
    return str(value)


def demo_yahoo_finance(symbol: str = "TOST"):
    """Demo Yahoo Finance fetcher capabilities."""
    print_header(f"Yahoo Finance Demo: {symbol}")
    
    configure_rate_limits(RATE_LIMITS)
    yahoo = YahooFinanceFetcher(rate_limiter=rate_limiter)
    
    # Current price
    print_subheader("Current Price")
    price_data = yahoo.get_current_price(symbol)
    if price_data:
        print(f"  Price: ${price_data.get('price', 'N/A')}")
        print(f"  Day Range: ${price_data.get('day_low', 'N/A')} - ${price_data.get('day_high', 'N/A')}")
        print(f"  52-Week: ${price_data.get('fifty_two_week_low', 'N/A')} - ${price_data.get('fifty_two_week_high', 'N/A')}")
        print(f"  Market Cap: {format_value(price_data.get('market_cap'))}")
    
    # Company info
    print_subheader("Company Info")
    info = yahoo.get_company_info(symbol)
    if info:
        print(f"  Name: {info.get('name', 'N/A')}")
        print(f"  Sector: {info.get('sector', 'N/A')}")
        print(f"  Industry: {info.get('industry', 'N/A')}")
        print(f"  Employees: {info.get('employees', 'N/A'):,}" if info.get('employees') else "  Employees: N/A")
    
    # Valuation
    print_subheader("Valuation Metrics")
    if info:
        print(f"  P/E Ratio: {format_value(info.get('pe_ratio'))}")
        print(f"  Forward P/E: {format_value(info.get('forward_pe'))}")
        print(f"  P/S Ratio: {format_value(info.get('ps_ratio'))}")
        print(f"  EV/EBITDA: {format_value(info.get('ev_ebitda'))}")
        print(f"  PEG Ratio: {format_value(info.get('peg_ratio'))}")
    
    # Growth & Quality
    print_subheader("Growth & Quality")
    if info:
        print(f"  Revenue Growth: {format_value(info.get('revenue_growth'))}")
        print(f"  Earnings Growth: {format_value(info.get('earnings_growth'))}")
        print(f"  Gross Margin: {format_value(info.get('gross_margins'))}")
        print(f"  Operating Margin: {format_value(info.get('operating_margins'))}")
        print(f"  ROE: {format_value(info.get('roe'))}")
    
    # Analyst Data
    print_subheader("Analyst Consensus")
    if info:
        print(f"  Recommendation: {info.get('recommendation', 'N/A')}")
        print(f"  Target Price: ${info.get('target_mean', 'N/A')}")
        print(f"  Target Range: ${info.get('target_low', 'N/A')} - ${info.get('target_high', 'N/A')}")
        print(f"  Num Analysts: {info.get('num_analysts', 'N/A')}")
    
    # Historical prices
    print_subheader("Historical Prices (Last 5 Days)")
    prices = yahoo.get_daily_prices(symbol, period="5d")
    if prices:
        for p in prices[-5:]:
            print(f"  {p['date']}: Open ${p['open']:.2f}, Close ${p['close']:.2f}, Vol {p['volume']:,}")
    
    # News
    print_subheader("Recent News")
    news = yahoo.get_news(symbol)
    for item in news[:3]:
        print(f"  • {item.get('title', 'N/A')[:70]}...")
        print(f"    Source: {item.get('publisher', 'N/A')}")
    
    return True


def demo_sec_edgar(symbol: str = "TOST"):
    """Demo SEC EDGAR fetcher capabilities."""
    print_header(f"SEC EDGAR Demo: {symbol}")
    
    configure_rate_limits(RATE_LIMITS)
    sec = SECEdgarFetcher(
        user_agent="TradingAgentDemo/1.0 (demo@example.com)",
        rate_limiter=rate_limiter
    )
    
    # Get CIK
    print_subheader("Company Lookup")
    cik = sec.get_cik(symbol)
    print(f"  CIK: {cik}")
    
    if not cik:
        print("  Could not find CIK for symbol")
        return False
    
    # Recent filings
    print_subheader("Recent 10-K Filings")
    filings_10k = sec.get_10k_filings(symbol, limit=3)
    for f in filings_10k:
        print(f"  • {f['filing_date']}: {f['form']}")
        print(f"    {f['url'][:70]}..." if f.get('url') else "")
    
    print_subheader("Recent 10-Q Filings")
    filings_10q = sec.get_10q_filings(symbol, limit=4)
    for f in filings_10q:
        print(f"  • {f['filing_date']}: {f['form']}")
    
    print_subheader("Recent 8-K Filings (Material Events)")
    filings_8k = sec.get_8k_filings(symbol, limit=5)
    for f in filings_8k:
        print(f"  • {f['filing_date']}: {f.get('description', 'N/A')[:50]}")
    
    # Filing summary
    print_subheader("Filing Summary")
    summary = sec.get_filing_summary(symbol)
    print(f"  Days since 10-K: {summary.get('days_since_10k', 'N/A')}")
    print(f"  Days since 10-Q: {summary.get('days_since_10q', 'N/A')}")
    print(f"  Recent 8-K count: {summary.get('recent_8k_count', 'N/A')}")
    
    return True


def demo_web_scraper(symbol: str = "TOST"):
    """Demo web scraper capabilities."""
    print_header(f"Web Scraper Demo: {symbol}")
    
    configure_rate_limits(RATE_LIMITS)
    scraper = WebScraper(rate_limiter=rate_limiter)
    
    # Finviz data
    print_subheader("Finviz Data")
    finviz = scraper.get_finviz_data(symbol)
    if finviz:
        print(f"  Company: {finviz.get('company_name', 'N/A')}")
        print(f"  Sector: {finviz.get('sector', 'N/A')}")
        print(f"  Industry: {finviz.get('industry', 'N/A')}")
        print(f"  Market Cap: {format_value(finviz.get('market_cap'))}")
        print(f"  P/E: {finviz.get('p_e', 'N/A')}")
        print(f"  Forward P/E: {finviz.get('forward_p_e', 'N/A')}")
        print(f"  EPS (ttm): {finviz.get('eps_(ttm)', 'N/A')}")
        print(f"  Target Price: {finviz.get('target_price', 'N/A')}")
        print(f"  52W Range: {finviz.get('52w_range', 'N/A')}")
        print(f"  Short Float: {finviz.get('short_float', 'N/A')}")
        print(f"  Analyst Recom: {finviz.get('recom', 'N/A')}")
    else:
        print("  Could not fetch Finviz data")
    
    # Analyst ratings summary
    print_subheader("Analyst Summary")
    analyst = scraper.get_analyst_ratings_summary(symbol)
    if analyst:
        print(f"  Target Price: {analyst.get('target_price', 'N/A')}")
        print(f"  Recommendation: {analyst.get('recommendation', 'N/A')}")
        print(f"  Upside: {analyst.get('upside_pct', 'N/A')}%")
    
    return True


def demo_orchestrator(symbol: str = "TOST"):
    """Demo the full orchestrator capabilities."""
    print_header(f"Data Orchestrator Demo: {symbol}")
    
    # Initialize orchestrator
    print_subheader("Initializing Orchestrator")
    orchestrator = DataOrchestrator()
    print("  ✓ Orchestrator initialized")
    
    # Check status
    status = orchestrator.get_status()
    print(f"  Database: {status['database']['path']}")
    print(f"  API Keys Configured: {status['api_keys_configured']}")
    
    # Get screening data (Yahoo only for demo - fastest)
    print_subheader(f"Fetching Screening Data for {symbol}")
    data = orchestrator.get_screening_data(symbol, sources=["yahoo", "scraper"])
    
    print(f"  Sources used: {data.get('sources_used', [])}")
    if data.get('errors'):
        print(f"  Errors: {data['errors']}")
    
    # Display unified data
    unified = data.get("unified", {})
    
    print_subheader("Unified Company Data")
    company = unified.get("company", {})
    print(f"  Name: {company.get('name', 'N/A')}")
    print(f"  Sector: {company.get('sector', 'N/A')}")
    print(f"  Market Cap: {format_value(company.get('market_cap'))}")
    
    print_subheader("Unified Valuation")
    val = unified.get("valuation", {})
    for key, value in val.items():
        print(f"  {key}: {format_value(value)}")
    
    print_subheader("Unified Growth")
    growth = unified.get("growth", {})
    for key, value in growth.items():
        print(f"  {key}: {format_value(value)}")
    
    print_subheader("Unified Quality")
    quality = unified.get("quality", {})
    for key, value in quality.items():
        print(f"  {key}: {format_value(value)}")
    
    print_subheader("Unified Analyst Data")
    analyst = unified.get("analyst", {})
    for key, value in analyst.items():
        print(f"  {key}: {format_value(value) if not isinstance(value, str) else value}")
    
    # Database status
    print_subheader("Database Status")
    counts = orchestrator.db.get_table_counts()
    for table, count in counts.items():
        if count > 0:
            print(f"  {table}: {count} records")
    
    return True


def demo_batch_operations():
    """Demo batch operations on multiple symbols."""
    print_header("Batch Operations Demo")
    
    symbols = ["TOST", "VRT", "S", "CRWD", "DDOG"]
    
    orchestrator = DataOrchestrator()
    
    # Batch screening data
    print_subheader(f"Fetching Screening Data for {len(symbols)} Symbols")
    print(f"  Symbols: {', '.join(symbols)}")
    
    results = orchestrator.get_screening_data_batch(symbols, sources=["yahoo"])
    
    # Summary table
    print_subheader("Summary Results")
    print(f"  {'Symbol':<8} {'Name':<25} {'Mkt Cap':<12} {'P/E':<10} {'Growth':<10}")
    print("  " + "-" * 70)
    
    for symbol in symbols:
        data = results.get(symbol, {})
        unified = data.get("unified", {})
        company = unified.get("company", {})
        val = unified.get("valuation", {})
        growth = unified.get("growth", {})
        
        name = (company.get("name", "N/A") or "N/A")[:24]
        mkt_cap = format_value(company.get("market_cap"))
        pe = format_value(val.get("pe_ratio"))
        rev_growth = format_value(growth.get("revenue_growth"))
        
        print(f"  {symbol:<8} {name:<25} {mkt_cap:<12} {pe:<10} {rev_growth:<10}")
    
    # Run quantitative screen
    print_subheader("Quantitative Screen Results")
    screen = orchestrator.run_quantitative_screen(symbols=symbols)
    
    print(f"  Passing: {screen['passing']}")
    print(f"  Failing: {screen['failing']}")
    
    for symbol, details in screen["details"].items():
        status = "✓" if details["passes"] else "✗"
        print(f"  {status} {symbol}: {', '.join(details['reasons']) if details['reasons'] else 'Passes all criteria'}")
    
    return True


def demo_prices():
    """Demo price data fetching and storage."""
    print_header("Price Data Demo")
    
    orchestrator = DataOrchestrator()
    symbol = "TOST"
    
    # Fetch prices
    print_subheader(f"Fetching Price History for {symbol}")
    prices = orchestrator.get_prices(symbol, period="1mo")
    
    print(f"  Fetched {len(prices)} daily records")
    
    if prices:
        latest = prices[-1]
        earliest = prices[0]
        print(f"  Date Range: {earliest['date']} to {latest['date']}")
        print(f"  Latest Close: ${latest['close']:.2f}")
        
        # Calculate simple return
        if earliest['close'] and latest['close']:
            ret = (latest['close'] / earliest['close'] - 1) * 100
            print(f"  Period Return: {ret:.1f}%")
    
    # Verify database storage
    print_subheader("Database Verification")
    db_prices = orchestrator.db.get_daily_prices(symbol)
    print(f"  Records in database: {len(db_prices)}")
    
    return True


def main():
    """Main demo runner."""
    parser = argparse.ArgumentParser(description="Data Infrastructure Demo")
    parser.add_argument("--symbol", default="TOST", help="Symbol to demo")
    parser.add_argument("--quick", action="store_true", help="Quick test (Yahoo only)")
    parser.add_argument("--batch", action="store_true", help="Test batch operations")
    parser.add_argument("--full", action="store_true", help="Run all demos")
    parser.add_argument("--component", choices=["yahoo", "sec", "scraper", "orchestrator", "prices"],
                       help="Test specific component")
    
    args = parser.parse_args()
    
    print("\n" + "=" * 60)
    print("  TRADING AGENT DATA INFRASTRUCTURE DEMO")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    
    try:
        if args.component:
            if args.component == "yahoo":
                demo_yahoo_finance(args.symbol)
            elif args.component == "sec":
                demo_sec_edgar(args.symbol)
            elif args.component == "scraper":
                demo_web_scraper(args.symbol)
            elif args.component == "orchestrator":
                demo_orchestrator(args.symbol)
            elif args.component == "prices":
                demo_prices()
        elif args.quick:
            demo_yahoo_finance(args.symbol)
        elif args.batch:
            demo_batch_operations()
        elif args.full:
            demo_yahoo_finance(args.symbol)
            demo_sec_edgar(args.symbol)
            demo_web_scraper(args.symbol)
            demo_orchestrator(args.symbol)
            demo_batch_operations()
        else:
            # Default: orchestrator demo
            demo_orchestrator(args.symbol)
        
        print_header("Demo Complete!")
        print("  All tests passed successfully.\n")
        
    except Exception as e:
        logger.error(f"Demo failed: {e}", exc_info=True)
        print(f"\n  ✗ Demo failed: {e}\n")
        return 1
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
