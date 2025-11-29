"""
Data Orchestrator
=================
Central coordinator for all data fetching operations.
Provides unified interface for the research engine.
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import json

from .config import (
    API_KEYS, SEC_EDGAR_USER_AGENT, RATE_LIMITS,
    DATA_DIR, DATABASE_PATH, CACHE_DIR, CACHE_EXPIRATION,
    DEFAULT_UNIVERSE, SECTOR_MAP, SCREENING_CRITERIA_MAP
)
from .utils import rate_limiter, configure_rate_limits, Cache
from .storage import Database
from .fetchers import (
    YahooFinanceFetcher,
    SECEdgarFetcher,
    FMPFetcher,
    FinnhubFetcher,
    WebScraper
)

logger = logging.getLogger(__name__)

class DataOrchestrator:
    """
    Central data orchestration for the trading agent.
    
    Usage:
        orchestrator = DataOrchestrator()
        
        # Get screening data for a single stock
        data = orchestrator.get_screening_data("TOST")
        
        # Get screening data for multiple stocks
        data = orchestrator.get_screening_data_batch(["TOST", "VRT", "S"])
        
        # Refresh all data for universe
        orchestrator.refresh_universe_data()
    """
    
    def __init__(self, 
                 api_keys: Dict[str, str] = None,
                 db_path: Path = None,
                 cache_dir: Path = None):
        """
        Initialize the data orchestrator.
        
        Args:
            api_keys: Override default API keys
            db_path: Override database path
            cache_dir: Override cache directory
        """
        # Setup directories
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        
        # Initialize configuration
        self.api_keys = api_keys or API_KEYS
        self.db_path = db_path or DATABASE_PATH
        self.cache_dir = cache_dir or CACHE_DIR
        
        # Configure rate limits
        configure_rate_limits(RATE_LIMITS)
        
        # Initialize cache
        self.cache = Cache(self.cache_dir)
        
        # Initialize database
        self.db = Database(self.db_path)
        
        # Initialize fetchers
        self._init_fetchers()
        
        logger.info("DataOrchestrator initialized")
    
    def _init_fetchers(self):
        """Initialize all data fetchers."""
        self.yahoo = YahooFinanceFetcher(
            rate_limiter=rate_limiter,
            cache=self.cache
        )
        
        self.sec = SECEdgarFetcher(
            user_agent=SEC_EDGAR_USER_AGENT,
            rate_limiter=rate_limiter,
            cache=self.cache
        )
        
        self.fmp = FMPFetcher(
            api_key=self.api_keys.get("fmp", ""),
            rate_limiter=rate_limiter,
            cache=self.cache
        )
        
        self.finnhub = FinnhubFetcher(
            api_key=self.api_keys.get("finnhub", ""),
            rate_limiter=rate_limiter,
            cache=self.cache
        )
        
        self.scraper = WebScraper(
            rate_limiter=rate_limiter,
            cache=self.cache
        )
    
    # =========================================================================
    # Screening Data (Research Engine Integration)
    # =========================================================================
    
    def get_screening_data(self, symbol: str, 
                          sources: List[str] = None,
                          use_cache: bool = True) -> Dict:
        """
        Get comprehensive screening data for a single symbol.
        
        Args:
            symbol: Stock ticker
            sources: List of sources to use. Options:
                    ['yahoo', 'fmp', 'finnhub', 'sec', 'scraper']
                    Default: all sources
            use_cache: Whether to use cached data
        
        Returns:
            Merged data from all sources
        """
        sources = sources or ["yahoo", "fmp", "finnhub", "sec", "scraper"]
        symbol = symbol.upper()
        
        # Check cache
        cache_key = f"screening_{symbol}"
        if use_cache:
            cached = self.cache.get(cache_key)
            if cached:
                logger.info(f"Cache hit for {symbol}")
                return cached
        
        result = {
            "symbol": symbol,
            "fetched_at": datetime.now().isoformat(),
            "sources_used": [],
            "errors": []
        }
        
        # Fetch from each source
        if "yahoo" in sources:
            try:
                yahoo_data = self.yahoo.get_screening_data(symbol)
                if yahoo_data and not yahoo_data.get("error"):
                    result["yahoo"] = yahoo_data
                    result["sources_used"].append("yahoo")
            except Exception as e:
                result["errors"].append(f"yahoo: {str(e)}")
                logger.error(f"Yahoo error for {symbol}: {e}")
        
        if "fmp" in sources and self.api_keys.get("fmp"):
            try:
                fmp_data = self.fmp.get_screening_data(symbol)
                if fmp_data:
                    result["fmp"] = fmp_data
                    result["sources_used"].append("fmp")
            except Exception as e:
                result["errors"].append(f"fmp: {str(e)}")
                logger.error(f"FMP error for {symbol}: {e}")
        
        if "finnhub" in sources and self.api_keys.get("finnhub"):
            try:
                finnhub_data = self.finnhub.get_screening_data(symbol)
                if finnhub_data:
                    result["finnhub"] = finnhub_data
                    result["sources_used"].append("finnhub")
            except Exception as e:
                result["errors"].append(f"finnhub: {str(e)}")
                logger.error(f"Finnhub error for {symbol}: {e}")
        
        if "sec" in sources:
            try:
                sec_data = self.sec.get_filing_summary(symbol)
                if sec_data:
                    result["sec"] = sec_data
                    result["sources_used"].append("sec")
            except Exception as e:
                result["errors"].append(f"sec: {str(e)}")
                logger.error(f"SEC error for {symbol}: {e}")
        
        if "scraper" in sources:
            try:
                scraper_data = self.scraper.get_screening_data(symbol)
                if scraper_data:
                    result["scraper"] = scraper_data
                    result["sources_used"].append("scraper")
            except Exception as e:
                result["errors"].append(f"scraper: {str(e)}")
                logger.error(f"Scraper error for {symbol}: {e}")
        
        # Merge into unified format
        result["unified"] = self._merge_screening_data(result)
        
        # Cache result
        if use_cache:
            self.cache.set(cache_key, result, ttl=CACHE_EXPIRATION.get("fundamentals", 86400))
        
        # Store in database
        self._store_screening_data(result)
        
        return result
    
    def get_screening_data_batch(self, symbols: List[str],
                                 sources: List[str] = None,
                                 max_workers: int = 3) -> Dict[str, Dict]:
        """
        Get screening data for multiple symbols in parallel.
        
        Args:
            symbols: List of stock tickers
            sources: Data sources to use
            max_workers: Number of parallel workers
        
        Returns:
            Dict mapping symbol -> screening data
        """
        results = {}
        
        # Use ThreadPoolExecutor for parallel fetching
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_symbol = {
                executor.submit(self.get_screening_data, symbol, sources): symbol
                for symbol in symbols
            }
            
            for future in as_completed(future_to_symbol):
                symbol = future_to_symbol[future]
                try:
                    results[symbol] = future.result()
                except Exception as e:
                    logger.error(f"Error fetching {symbol}: {e}")
                    results[symbol] = {
                        "symbol": symbol,
                        "error": str(e),
                        "fetched_at": datetime.now().isoformat()
                    }
        
        return results
    
    def _merge_screening_data(self, data: Dict) -> Dict:
        """
        Merge data from multiple sources into unified format.
        Priority: Yahoo > FMP > Finnhub > Scraper
        """
        unified = {
            "symbol": data["symbol"],
            "last_updated": data["fetched_at"]
        }
        
        # Company Info
        yahoo = data.get("yahoo", {})
        fmp = data.get("fmp", {})
        finnhub = data.get("finnhub", {})
        scraper = data.get("scraper", {})
        
        unified["company"] = {
            "name": yahoo.get("name") or fmp.get("profile", {}).get("name"),
            "sector": yahoo.get("sector") or fmp.get("profile", {}).get("sector"),
            "industry": yahoo.get("industry") or fmp.get("profile", {}).get("industry"),
            "market_cap": yahoo.get("market_cap") or fmp.get("profile", {}).get("market_cap"),
            "exchange": yahoo.get("exchange") or fmp.get("profile", {}).get("exchange"),
        }
        
        # Valuation Metrics
        yahoo_val = yahoo.get("valuation", {})
        fmp_ratios = fmp.get("ratios", {})
        finnhub_fin = finnhub.get("financials", {})
        
        unified["valuation"] = {
            "pe_ratio": yahoo_val.get("pe_ratio") or fmp_ratios.get("pe_ratio") or finnhub_fin.get("pe_ratio"),
            "forward_pe": yahoo_val.get("forward_pe") or fmp_ratios.get("forward_pe"),
            "ps_ratio": yahoo_val.get("ps_ratio") or fmp_ratios.get("ps_ratio"),
            "pb_ratio": yahoo_val.get("pb_ratio") or fmp_ratios.get("pb_ratio"),
            "ev_ebitda": yahoo_val.get("ev_ebitda") or fmp_ratios.get("ev_ebitda") or finnhub_fin.get("ev_ebitda"),
            "ev_revenue": yahoo_val.get("ev_revenue") or fmp_ratios.get("ev_revenue"),
            "peg_ratio": yahoo_val.get("peg_ratio") or fmp_ratios.get("peg_ratio") or finnhub_fin.get("peg_ratio"),
        }
        
        # Growth Metrics
        yahoo_growth = yahoo.get("growth", {})
        fmp_growth = fmp.get("growth", {})
        finnhub_fin = finnhub.get("financials", {})
        
        unified["growth"] = {
            "revenue_growth": yahoo_growth.get("revenue_growth") or fmp_growth.get("revenue_growth"),
            "earnings_growth": yahoo_growth.get("earnings_growth") or fmp_growth.get("net_income_growth"),
            "revenue_growth_3y": finnhub_fin.get("revenue_growth_3y"),
            "revenue_growth_5y": finnhub_fin.get("revenue_growth_5y"),
            "eps_growth_3y": finnhub_fin.get("eps_growth_3y"),
        }
        
        # Quality Metrics
        yahoo_quality = yahoo.get("quality", {})
        fmp_ratios = fmp.get("ratios", {})
        finnhub_fin = finnhub.get("financials", {})
        
        unified["quality"] = {
            "gross_margin": yahoo_quality.get("gross_margin") or fmp_ratios.get("gross_margin") or finnhub_fin.get("gross_margin"),
            "operating_margin": yahoo_quality.get("operating_margin") or fmp_ratios.get("operating_margin") or finnhub_fin.get("operating_margin"),
            "net_margin": yahoo_quality.get("net_margin") or fmp_ratios.get("net_margin") or finnhub_fin.get("net_margin"),
            "roe": yahoo_quality.get("roe") or fmp_ratios.get("roe") or finnhub_fin.get("roe"),
            "roa": yahoo_quality.get("roa") or fmp_ratios.get("roa") or finnhub_fin.get("roa"),
            "roic": fmp_ratios.get("roic") or finnhub_fin.get("roic"),
        }
        
        # Financial Health
        yahoo_health = yahoo.get("financial_health", {})
        fmp_ratios = fmp.get("ratios", {})
        
        unified["financial_health"] = {
            "current_ratio": yahoo_health.get("current_ratio") or fmp_ratios.get("current_ratio"),
            "quick_ratio": yahoo_health.get("quick_ratio") or fmp_ratios.get("quick_ratio"),
            "debt_to_equity": yahoo_health.get("debt_to_equity") or fmp_ratios.get("debt_equity"),
            "total_cash": yahoo_health.get("total_cash"),
            "total_debt": yahoo_health.get("total_debt"),
            "free_cash_flow": yahoo_health.get("free_cash_flow"),
        }
        
        # Analyst Data
        yahoo_analyst = yahoo.get("analyst", {})
        fmp_pt = fmp.get("price_targets", {})
        finnhub_pt = finnhub.get("price_target", {})
        finnhub_recs = finnhub.get("recommendations", {})
        
        unified["analyst"] = {
            "recommendation": yahoo_analyst.get("recommendation"),
            "num_analysts": yahoo_analyst.get("num_analysts") or fmp_pt.get("num_analysts"),
            "target_mean": yahoo_analyst.get("target_mean") or fmp_pt.get("target_mean") or finnhub_pt.get("target_mean"),
            "target_high": yahoo_analyst.get("target_high") or fmp_pt.get("target_high") or finnhub_pt.get("target_high"),
            "target_low": yahoo_analyst.get("target_low") or fmp_pt.get("target_low") or finnhub_pt.get("target_low"),
            "current_price": yahoo_analyst.get("current_price") or fmp_pt.get("current_price"),
            "upside_pct": yahoo_analyst.get("upside_pct") or fmp_pt.get("upside_pct"),
            "bullish_pct": finnhub_recs.get("bullish_pct"),
        }
        
        # Risk Metrics
        yahoo_risk = yahoo.get("risk", {})
        finnhub_fin = finnhub.get("financials", {})
        scraper_risk = scraper.get("risk", {})
        
        unified["risk"] = {
            "beta": yahoo_risk.get("beta") or finnhub_fin.get("beta"),
            "52_week_high": yahoo_risk.get("fifty_two_week_high"),
            "52_week_low": yahoo_risk.get("fifty_two_week_low"),
            "range_position": yahoo_risk.get("range_position"),
            "short_float": scraper_risk.get("short_float"),
            "short_ratio": scraper_risk.get("short_ratio"),
        }
        
        # Sentiment & Other
        finnhub_sentiment = finnhub.get("sentiment", {})
        
        unified["sentiment"] = {
            "news_sentiment": finnhub_sentiment.get("sentiment", {}).get("bullishPercent"),
            "buzz_score": finnhub_sentiment.get("buzz", {}).get("buzz"),
            "insider_sentiment": finnhub.get("insider_sentiment", {}).get("total_insider_change"),
        }
        
        # SEC Filings
        sec = data.get("sec", {})
        unified["filings"] = {
            "last_10k_date": sec.get("last_10k", {}).get("filing_date") if sec.get("last_10k") else None,
            "last_10q_date": sec.get("last_10q", {}).get("filing_date") if sec.get("last_10q") else None,
            "days_since_10k": sec.get("days_since_10k"),
            "days_since_10q": sec.get("days_since_10q"),
            "recent_8k_count": sec.get("recent_8k_count"),
        }
        
        return unified
    
    def _store_screening_data(self, data: Dict):
        """Store screening data in database for historical tracking."""
        symbol = data["symbol"]
        unified = data.get("unified", {})
        
        # Store company profile
        if unified.get("company"):
            self.db.upsert_company_profile(symbol, unified["company"])
        
        # Store valuation metrics
        if unified.get("valuation"):
            val_data = unified["valuation"].copy()
            val_data.update(unified.get("quality", {}))
            self.db.execute_query("""
                INSERT OR REPLACE INTO valuation_metrics 
                (symbol, date, pe_ratio, forward_pe, ps_ratio, pb_ratio, ev_ebitda, 
                 ev_revenue, peg_ratio, data, source, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (
                symbol,
                datetime.now().strftime("%Y-%m-%d"),
                val_data.get("pe_ratio"),
                val_data.get("forward_pe"),
                val_data.get("ps_ratio"),
                val_data.get("pb_ratio"),
                val_data.get("ev_ebitda"),
                val_data.get("ev_revenue"),
                val_data.get("peg_ratio"),
                json.dumps(val_data),
                ",".join(data.get("sources_used", []))
            ))
        
        # Log the fetch
        self.db.log_fetch(
            source="orchestrator",
            endpoint="screening_data",
            symbol=symbol,
            status="SUCCESS",
            records=len(data.get("sources_used", []))
        )
    
    # =========================================================================
    # Price Data
    # =========================================================================
    
    def get_prices(self, symbol: str, period: str = "2y",
                   start: str = None, end: str = None,
                   store: bool = True) -> List[Dict]:
        """Get and optionally store daily price data."""
        prices = self.yahoo.get_daily_prices(symbol, period=period, start=start, end=end)
        
        if prices and store:
            self.db.upsert_daily_prices(symbol, prices, source="yahoo")
        
        return prices
    
    def get_prices_batch(self, symbols: List[str], period: str = "1y",
                         store: bool = True) -> Dict[str, List[Dict]]:
        """Get prices for multiple symbols."""
        results = self.yahoo.get_prices_batch(symbols, period=period)
        
        if store:
            for symbol, prices in results.items():
                if prices:
                    self.db.upsert_daily_prices(symbol, prices, source="yahoo")
        
        return results
    
    # =========================================================================
    # Fundamentals
    # =========================================================================
    
    def get_financials(self, symbol: str) -> Dict:
        """Get comprehensive financial statements."""
        return self.yahoo.get_financials(symbol)
    
    def get_sec_financials(self, symbol: str) -> Dict:
        """Get financials from SEC EDGAR (more detailed)."""
        return self.sec.get_key_financials(symbol)
    
    # =========================================================================
    # News & Sentiment
    # =========================================================================
    
    def get_news(self, symbol: str, days: int = 30) -> List[Dict]:
        """Get news from multiple sources."""
        results = []
        
        # Yahoo news
        yahoo_news = self.yahoo.get_news(symbol)
        for item in yahoo_news:
            item["source_api"] = "yahoo"
            results.append(item)
        
        # Finnhub news (if configured)
        if self.api_keys.get("finnhub"):
            from_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
            finnhub_news = self.finnhub.get_company_news(symbol, from_date=from_date)
            for item in finnhub_news:
                item["source_api"] = "finnhub"
                results.append(item)
        
        # Sort by date (most recent first)
        results.sort(key=lambda x: x.get("published_at", ""), reverse=True)
        
        return results
    
    def get_sentiment(self, symbol: str) -> Dict:
        """Get sentiment analysis from Finnhub."""
        if not self.api_keys.get("finnhub"):
            return {"error": "Finnhub API key not configured"}
        
        return self.finnhub.get_news_sentiment(symbol)
    
    # =========================================================================
    # Earnings
    # =========================================================================
    
    def get_earnings_calendar(self, days_ahead: int = 30) -> List[Dict]:
        """Get upcoming earnings calendar."""
        results = []
        
        if self.api_keys.get("finnhub"):
            to_date = (datetime.now() + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
            results = self.finnhub.get_earnings_calendar(to_date=to_date)
        
        return results
    
    def get_earnings_history(self, symbol: str) -> Dict:
        """Get earnings history and surprises."""
        return self.yahoo.get_earnings(symbol)
    
    # =========================================================================
    # Universe Management
    # =========================================================================
    
    def refresh_universe_data(self, symbols: List[str] = None,
                              include_prices: bool = True):
        """
        Refresh all data for the investment universe.
        
        Args:
            symbols: List of symbols (default: DEFAULT_UNIVERSE)
            include_prices: Whether to fetch price history
        """
        symbols = symbols or DEFAULT_UNIVERSE
        
        logger.info(f"Refreshing data for {len(symbols)} symbols")
        
        # Fetch screening data
        screening_data = self.get_screening_data_batch(symbols)
        
        # Fetch prices
        if include_prices:
            self.get_prices_batch(symbols, period="1y")
        
        logger.info(f"Refresh complete for {len(symbols)} symbols")
        
        return {
            "symbols_refreshed": len(symbols),
            "screening_data_count": len(screening_data),
            "timestamp": datetime.now().isoformat()
        }
    
    def get_universe_summary(self, symbols: List[str] = None) -> List[Dict]:
        """Get summary metrics for the entire universe."""
        symbols = symbols or DEFAULT_UNIVERSE
        
        results = []
        for symbol in symbols:
            data = self.get_screening_data(symbol, sources=["yahoo"])
            unified = data.get("unified", {})
            
            results.append({
                "symbol": symbol,
                "name": unified.get("company", {}).get("name"),
                "market_cap": unified.get("company", {}).get("market_cap"),
                "pe_ratio": unified.get("valuation", {}).get("pe_ratio"),
                "ps_ratio": unified.get("valuation", {}).get("ps_ratio"),
                "revenue_growth": unified.get("growth", {}).get("revenue_growth"),
                "gross_margin": unified.get("quality", {}).get("gross_margin"),
                "upside_pct": unified.get("analyst", {}).get("upside_pct"),
            })
        
        return results
    
    # =========================================================================
    # Quantitative Screen (Stage 1C Integration)
    # =========================================================================
    
    def run_quantitative_screen(self, symbols: List[str] = None,
                                criteria: Dict = None) -> Dict:
        """
        Run Stage 1C quantitative screen on universe.
        
        Args:
            symbols: Universe to screen
            criteria: Override default screening criteria
        
        Returns:
            Dict with 'passing' and 'failing' symbol lists + details
        """
        symbols = symbols or DEFAULT_UNIVERSE
        criteria = criteria or SCREENING_CRITERIA_MAP
        
        passing = []
        failing = []
        details = {}
        
        for symbol in symbols:
            data = self.get_screening_data(symbol, sources=["yahoo", "fmp"])
            unified = data.get("unified", {})
            
            # Extract metrics
            market_cap = unified.get("company", {}).get("market_cap") or 0
            revenue_growth = unified.get("growth", {}).get("revenue_growth") or 0
            gross_margin = unified.get("quality", {}).get("gross_margin") or 0
            
            # Apply criteria
            passes = True
            reasons = []
            
            if market_cap < criteria.get("market_cap_min", 0):
                passes = False
                reasons.append(f"Market cap ${market_cap/1e9:.1f}B < ${criteria['market_cap_min']/1e9:.1f}B min")
            
            if market_cap > criteria.get("market_cap_max", float("inf")):
                passes = False
                reasons.append(f"Market cap ${market_cap/1e9:.1f}B > ${criteria['market_cap_max']/1e9:.1f}B max")
            
            if revenue_growth < criteria.get("revenue_growth_min", 0):
                passes = False
                reasons.append(f"Revenue growth {revenue_growth*100:.1f}% < {criteria['revenue_growth_min']*100:.0f}% min")
            
            if gross_margin < criteria.get("gross_margin_min", 0):
                passes = False
                reasons.append(f"Gross margin {gross_margin*100:.1f}% < {criteria['gross_margin_min']*100:.0f}% min")
            
            details[symbol] = {
                "passes": passes,
                "market_cap": market_cap,
                "revenue_growth": revenue_growth,
                "gross_margin": gross_margin,
                "reasons": reasons
            }
            
            if passes:
                passing.append(symbol)
            else:
                failing.append(symbol)
        
        return {
            "passing": passing,
            "failing": failing,
            "details": details,
            "criteria_used": criteria,
            "timestamp": datetime.now().isoformat()
        }
    
    # =========================================================================
    # Utilities
    # =========================================================================
    
    def get_status(self) -> Dict:
        """Get status of data infrastructure."""
        return {
            "database": {
                "path": str(self.db_path),
                "table_counts": self.db.get_table_counts()
            },
            "cache": self.cache.get_stats(),
            "fetch_stats": self.db.get_fetch_stats(hours=24),
            "api_keys_configured": {
                k: bool(v and v != "demo") 
                for k, v in self.api_keys.items()
            }
        }
    
    def clear_cache(self):
        """Clear all cached data."""
        self.cache.clear()
        logger.info("Cache cleared")
