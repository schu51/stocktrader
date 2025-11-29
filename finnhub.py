"""
Finnhub Data Fetcher
====================
News, sentiment, earnings calendar, and company data.
Free tier: 60 requests/minute
API key required: https://finnhub.io/register
"""

import requests
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
import logging

logger = logging.getLogger(__name__)

class FinnhubFetcher:
    """Fetch data from Finnhub API."""
    
    SOURCE_NAME = "finnhub"
    BASE_URL = "https://finnhub.io/api/v1"
    
    def __init__(self, api_key: str, rate_limiter=None, cache=None):
        self.api_key = api_key
        self.rate_limiter = rate_limiter
        self.cache = cache
    
    def _acquire_rate_limit(self):
        if self.rate_limiter:
            self.rate_limiter.acquire(self.SOURCE_NAME)
    
    def _make_request(self, endpoint: str, params: Dict = None) -> Optional[Any]:
        """Make rate-limited request to Finnhub."""
        if not self.api_key:
            logger.warning("Finnhub API key not configured")
            return None
        
        self._acquire_rate_limit()
        
        params = params or {}
        params["token"] = self.api_key
        
        url = f"{self.BASE_URL}/{endpoint}"
        
        try:
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            logger.error(f"HTTP error from Finnhub: {e}")
            return None
        except Exception as e:
            logger.error(f"Error fetching from Finnhub: {e}")
            return None
    
    # =========================================================================
    # Company Profile
    # =========================================================================
    
    def get_company_profile(self, symbol: str) -> Optional[Dict]:
        """Get company profile."""
        data = self._make_request("stock/profile2", {"symbol": symbol})
        return data if data else None
    
    def get_company_peers(self, symbol: str) -> List[str]:
        """Get peer companies."""
        data = self._make_request("stock/peers", {"symbol": symbol})
        return data if data else []
    
    # =========================================================================
    # News & Sentiment
    # =========================================================================
    
    def get_company_news(self, symbol: str, from_date: str = None, 
                         to_date: str = None) -> List[Dict]:
        """
        Get company news.
        
        Args:
            symbol: Stock ticker
            from_date: Start date (YYYY-MM-DD), defaults to 30 days ago
            to_date: End date (YYYY-MM-DD), defaults to today
        """
        if not from_date:
            from_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        if not to_date:
            to_date = datetime.now().strftime("%Y-%m-%d")
        
        data = self._make_request("company-news", {
            "symbol": symbol,
            "from": from_date,
            "to": to_date
        })
        
        if not data:
            return []
        
        # Standardize format
        results = []
        for item in data[:50]:  # Limit to 50 articles
            results.append({
                "symbol": symbol,
                "headline": item.get("headline"),
                "summary": item.get("summary"),
                "source": item.get("source"),
                "url": item.get("url"),
                "image": item.get("image"),
                "category": item.get("category"),
                "published_at": datetime.fromtimestamp(item.get("datetime", 0)).isoformat() if item.get("datetime") else None,
                "related": item.get("related"),
            })
        
        return results
    
    def get_market_news(self, category: str = "general") -> List[Dict]:
        """
        Get general market news.
        
        Args:
            category: 'general', 'forex', 'crypto', or 'merger'
        """
        data = self._make_request("news", {"category": category})
        
        if not data:
            return []
        
        results = []
        for item in data[:30]:
            results.append({
                "headline": item.get("headline"),
                "summary": item.get("summary"),
                "source": item.get("source"),
                "url": item.get("url"),
                "image": item.get("image"),
                "category": item.get("category"),
                "published_at": datetime.fromtimestamp(item.get("datetime", 0)).isoformat() if item.get("datetime") else None,
            })
        
        return results
    
    def get_news_sentiment(self, symbol: str) -> Optional[Dict]:
        """Get news sentiment scores for a company."""
        data = self._make_request("news-sentiment", {"symbol": symbol})
        
        if not data:
            return None
        
        return {
            "symbol": symbol,
            "buzz": data.get("buzz", {}),
            "sentiment": data.get("sentiment", {}),
            "company_news_score": data.get("companyNewsScore"),
            "sector_average_bullish_percent": data.get("sectorAverageBullishPercent"),
            "sector_average_news_score": data.get("sectorAverageNewsScore"),
        }
    
    # =========================================================================
    # Earnings
    # =========================================================================
    
    def get_earnings_calendar(self, from_date: str = None, 
                              to_date: str = None) -> List[Dict]:
        """Get earnings calendar for date range."""
        if not from_date:
            from_date = datetime.now().strftime("%Y-%m-%d")
        if not to_date:
            to_date = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")
        
        data = self._make_request("calendar/earnings", {
            "from": from_date,
            "to": to_date
        })
        
        if not data or "earningsCalendar" not in data:
            return []
        
        results = []
        for item in data["earningsCalendar"]:
            results.append({
                "symbol": item.get("symbol"),
                "date": item.get("date"),
                "hour": item.get("hour"),  # 'bmo' (before market), 'amc' (after market), 'dmh' (during)
                "eps_estimate": item.get("epsEstimate"),
                "eps_actual": item.get("epsActual"),
                "revenue_estimate": item.get("revenueEstimate"),
                "revenue_actual": item.get("revenueActual"),
                "quarter": item.get("quarter"),
                "year": item.get("year"),
            })
        
        return results
    
    def get_earnings_surprises(self, symbol: str, limit: int = 4) -> List[Dict]:
        """Get historical earnings surprises."""
        data = self._make_request("stock/earnings", {
            "symbol": symbol,
            "limit": limit
        })
        
        if not data:
            return []
        
        results = []
        for item in data:
            results.append({
                "symbol": symbol,
                "date": item.get("date"),
                "eps_actual": item.get("actual"),
                "eps_estimate": item.get("estimate"),
                "surprise_pct": item.get("surprisePercent"),
                "quarter": item.get("quarter"),
                "year": item.get("year"),
            })
        
        return results
    
    # =========================================================================
    # Analyst Recommendations
    # =========================================================================
    
    def get_recommendation_trends(self, symbol: str) -> List[Dict]:
        """Get analyst recommendation trends over time."""
        data = self._make_request("stock/recommendation", {"symbol": symbol})
        
        if not data:
            return []
        
        results = []
        for item in data:
            results.append({
                "symbol": symbol,
                "period": item.get("period"),
                "strong_buy": item.get("strongBuy"),
                "buy": item.get("buy"),
                "hold": item.get("hold"),
                "sell": item.get("sell"),
                "strong_sell": item.get("strongSell"),
            })
        
        return results
    
    def get_price_target(self, symbol: str) -> Optional[Dict]:
        """Get analyst price target consensus."""
        data = self._make_request("stock/price-target", {"symbol": symbol})
        
        if not data:
            return None
        
        return {
            "symbol": symbol,
            "target_high": data.get("targetHigh"),
            "target_low": data.get("targetLow"),
            "target_mean": data.get("targetMean"),
            "target_median": data.get("targetMedian"),
            "last_updated": data.get("lastUpdated"),
        }
    
    def get_upgrades_downgrades(self, symbol: str = None, 
                                from_date: str = None,
                                to_date: str = None) -> List[Dict]:
        """Get analyst upgrades/downgrades."""
        params = {}
        if symbol:
            params["symbol"] = symbol
        if from_date:
            params["from"] = from_date
        if to_date:
            params["to"] = to_date
        
        data = self._make_request("stock/upgrade-downgrade", params)
        
        if not data:
            return []
        
        results = []
        for item in data[:50]:
            results.append({
                "symbol": item.get("symbol"),
                "date": item.get("gradeDate"),
                "company": item.get("company"),  # Analyst firm
                "from_grade": item.get("fromGrade"),
                "to_grade": item.get("toGrade"),
                "action": item.get("action"),
            })
        
        return results
    
    # =========================================================================
    # Insider Transactions
    # =========================================================================
    
    def get_insider_transactions(self, symbol: str) -> List[Dict]:
        """Get insider transactions."""
        data = self._make_request("stock/insider-transactions", {"symbol": symbol})
        
        if not data or "data" not in data:
            return []
        
        results = []
        for item in data["data"][:50]:
            results.append({
                "symbol": symbol,
                "name": item.get("name"),
                "share": item.get("share"),
                "change": item.get("change"),
                "filing_date": item.get("filingDate"),
                "transaction_date": item.get("transactionDate"),
                "transaction_code": item.get("transactionCode"),
                "transaction_price": item.get("transactionPrice"),
            })
        
        return results
    
    def get_insider_sentiment(self, symbol: str) -> Optional[Dict]:
        """Get aggregated insider sentiment."""
        data = self._make_request("stock/insider-sentiment", {
            "symbol": symbol,
            "from": (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d"),
            "to": datetime.now().strftime("%Y-%m-%d")
        })
        
        if not data or "data" not in data:
            return None
        
        # Aggregate sentiment
        total_change = sum(item.get("change", 0) for item in data["data"])
        months_with_data = len(data["data"])
        
        return {
            "symbol": symbol,
            "total_insider_change": total_change,
            "months_with_activity": months_with_data,
            "average_monthly_change": total_change / months_with_data if months_with_data > 0 else 0,
            "recent_data": data["data"][:6] if data["data"] else []  # Last 6 months
        }
    
    # =========================================================================
    # Quote & Basic Data
    # =========================================================================
    
    def get_quote(self, symbol: str) -> Optional[Dict]:
        """Get real-time quote."""
        data = self._make_request("quote", {"symbol": symbol})
        
        if not data:
            return None
        
        return {
            "symbol": symbol,
            "current_price": data.get("c"),
            "change": data.get("d"),
            "change_pct": data.get("dp"),
            "high": data.get("h"),
            "low": data.get("l"),
            "open": data.get("o"),
            "previous_close": data.get("pc"),
            "timestamp": datetime.fromtimestamp(data.get("t", 0)).isoformat() if data.get("t") else None
        }
    
    def get_basic_financials(self, symbol: str) -> Optional[Dict]:
        """Get basic financial metrics."""
        data = self._make_request("stock/metric", {
            "symbol": symbol,
            "metric": "all"
        })
        
        if not data or "metric" not in data:
            return None
        
        m = data["metric"]
        return {
            "symbol": symbol,
            # Valuation
            "pe_ratio": m.get("peNormalizedAnnual"),
            "ps_ratio": m.get("psTTM"),
            "pb_ratio": m.get("pbQuarterly"),
            "ev_ebitda": m.get("enterpriseValueOverEBITDATTM"),
            "peg_ratio": m.get("pegRatio"),
            # Growth
            "revenue_growth_3y": m.get("revenueGrowth3Y"),
            "revenue_growth_5y": m.get("revenueGrowth5Y"),
            "eps_growth_3y": m.get("epsGrowth3Y"),
            "eps_growth_5y": m.get("epsGrowth5Y"),
            # Profitability
            "gross_margin": m.get("grossMarginTTM"),
            "operating_margin": m.get("operatingMarginTTM"),
            "net_margin": m.get("netMarginTTM"),
            "roe": m.get("roeTTM"),
            "roa": m.get("roaTTM"),
            "roic": m.get("roicTTM"),
            # Financial Health
            "current_ratio": m.get("currentRatioQuarterly"),
            "quick_ratio": m.get("quickRatioQuarterly"),
            "debt_equity": m.get("totalDebtToEquityQuarterly"),
            # Per Share
            "book_value_per_share": m.get("bookValuePerShareQuarterly"),
            "tangible_bv_per_share": m.get("tangibleBookValuePerShareQuarterly"),
            "revenue_per_share": m.get("revenuePerShareTTM"),
            "fcf_per_share": m.get("freeCashFlowPerShareTTM"),
            # Size
            "market_cap": m.get("marketCapitalization"),
            "enterprise_value": m.get("enterpriseValue"),
            # Dividend
            "dividend_yield": m.get("dividendYieldIndicatedAnnual"),
            "payout_ratio": m.get("payoutRatioTTM"),
            # Beta
            "beta": m.get("beta"),
            "52_week_high": m.get("52WeekHigh"),
            "52_week_low": m.get("52WeekLow"),
        }
    
    # =========================================================================
    # Screening Integration
    # =========================================================================
    
    def get_screening_data(self, symbol: str) -> Dict:
        """Get comprehensive data for equity screening."""
        result = {
            "symbol": symbol,
            "source": self.SOURCE_NAME,
            "fetched_at": datetime.now().isoformat()
        }
        
        # Basic financials (most comprehensive single call)
        financials = self.get_basic_financials(symbol)
        if financials:
            result["financials"] = financials
        
        # News sentiment
        sentiment = self.get_news_sentiment(symbol)
        if sentiment:
            result["sentiment"] = sentiment
        
        # Recommendation trends
        recs = self.get_recommendation_trends(symbol)
        if recs:
            result["recommendations"] = recs[0] if recs else {}  # Most recent
            
            # Calculate consensus
            if result["recommendations"]:
                r = result["recommendations"]
                total = (r.get("strongBuy", 0) + r.get("buy", 0) + r.get("hold", 0) + 
                        r.get("sell", 0) + r.get("strongSell", 0))
                if total > 0:
                    bullish = r.get("strongBuy", 0) + r.get("buy", 0)
                    result["recommendations"]["bullish_pct"] = round(bullish / total * 100, 1)
        
        # Price target
        pt = self.get_price_target(symbol)
        if pt:
            result["price_target"] = pt
        
        # Earnings surprises
        earnings = self.get_earnings_surprises(symbol, limit=4)
        if earnings:
            result["earnings_history"] = earnings
            # Calculate average surprise
            surprises = [e.get("surprise_pct", 0) for e in earnings if e.get("surprise_pct") is not None]
            if surprises:
                result["avg_earnings_surprise"] = round(sum(surprises) / len(surprises), 2)
        
        # Insider sentiment
        insider = self.get_insider_sentiment(symbol)
        if insider:
            result["insider_sentiment"] = insider
        
        return result
