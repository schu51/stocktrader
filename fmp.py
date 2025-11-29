"""
Financial Modeling Prep (FMP) Data Fetcher
==========================================
Additional financial data, ratios, and metrics.
Free tier: 250 requests/day
API key required: https://site.financialmodelingprep.com/developer/docs
"""

import requests
from datetime import datetime
from typing import Dict, List, Optional, Any
import logging

logger = logging.getLogger(__name__)

class FMPFetcher:
    """Fetch financial data from Financial Modeling Prep."""
    
    SOURCE_NAME = "fmp"
    BASE_URL = "https://financialmodelingprep.com/api/v3"
    
    def __init__(self, api_key: str, rate_limiter=None, cache=None):
        self.api_key = api_key
        self.rate_limiter = rate_limiter
        self.cache = cache
    
    def _acquire_rate_limit(self):
        if self.rate_limiter:
            self.rate_limiter.acquire(self.SOURCE_NAME)
    
    def _make_request(self, endpoint: str, params: Dict = None) -> Optional[Any]:
        """Make rate-limited request to FMP API."""
        self._acquire_rate_limit()
        
        params = params or {}
        params["apikey"] = self.api_key
        
        url = f"{self.BASE_URL}/{endpoint}"
        
        try:
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
            
            # Check for API errors
            if isinstance(data, dict) and "Error Message" in data:
                logger.error(f"FMP API error: {data['Error Message']}")
                return None
            
            return data
            
        except requests.exceptions.HTTPError as e:
            logger.error(f"HTTP error from FMP: {e}")
            return None
        except Exception as e:
            logger.error(f"Error fetching from FMP: {e}")
            return None
    
    # =========================================================================
    # Company Profile
    # =========================================================================
    
    def get_company_profile(self, symbol: str) -> Optional[Dict]:
        """Get company profile and overview."""
        data = self._make_request(f"profile/{symbol}")
        if data and len(data) > 0:
            return data[0]
        return None
    
    def get_company_profiles_batch(self, symbols: List[str]) -> List[Dict]:
        """Get profiles for multiple companies."""
        symbols_str = ",".join(symbols)
        data = self._make_request(f"profile/{symbols_str}")
        return data if data else []
    
    # =========================================================================
    # Financial Statements
    # =========================================================================
    
    def get_income_statement(self, symbol: str, period: str = "quarter", 
                             limit: int = 12) -> List[Dict]:
        """
        Get income statement data.
        
        Args:
            symbol: Stock ticker
            period: 'quarter' or 'annual'
            limit: Number of periods to return
        """
        data = self._make_request(f"income-statement/{symbol}", {
            "period": period,
            "limit": limit
        })
        return data if data else []
    
    def get_balance_sheet(self, symbol: str, period: str = "quarter",
                          limit: int = 12) -> List[Dict]:
        """Get balance sheet data."""
        data = self._make_request(f"balance-sheet-statement/{symbol}", {
            "period": period,
            "limit": limit
        })
        return data if data else []
    
    def get_cash_flow(self, symbol: str, period: str = "quarter",
                      limit: int = 12) -> List[Dict]:
        """Get cash flow statement data."""
        data = self._make_request(f"cash-flow-statement/{symbol}", {
            "period": period,
            "limit": limit
        })
        return data if data else []
    
    # =========================================================================
    # Financial Ratios & Metrics
    # =========================================================================
    
    def get_financial_ratios(self, symbol: str, period: str = "quarter",
                             limit: int = 12) -> List[Dict]:
        """
        Get comprehensive financial ratios.
        Includes: profitability, liquidity, efficiency, leverage ratios.
        """
        data = self._make_request(f"ratios/{symbol}", {
            "period": period,
            "limit": limit
        })
        return data if data else []
    
    def get_key_metrics(self, symbol: str, period: str = "quarter",
                        limit: int = 12) -> List[Dict]:
        """
        Get key financial metrics.
        Includes: revenue/share, net income/share, FCF/share, book value, etc.
        """
        data = self._make_request(f"key-metrics/{symbol}", {
            "period": period,
            "limit": limit
        })
        return data if data else []
    
    def get_financial_growth(self, symbol: str, period: str = "quarter",
                             limit: int = 12) -> List[Dict]:
        """Get growth rates for key financial metrics."""
        data = self._make_request(f"financial-growth/{symbol}", {
            "period": period,
            "limit": limit
        })
        return data if data else []
    
    def get_enterprise_value(self, symbol: str, period: str = "quarter",
                             limit: int = 12) -> List[Dict]:
        """Get enterprise value and related metrics."""
        data = self._make_request(f"enterprise-values/{symbol}", {
            "period": period,
            "limit": limit
        })
        return data if data else []
    
    # =========================================================================
    # Analyst & Estimates
    # =========================================================================
    
    def get_analyst_estimates(self, symbol: str, period: str = "quarter",
                              limit: int = 12) -> List[Dict]:
        """Get analyst estimates for earnings and revenue."""
        data = self._make_request(f"analyst-estimates/{symbol}", {
            "period": period,
            "limit": limit
        })
        return data if data else []
    
    def get_analyst_recommendations(self, symbol: str) -> List[Dict]:
        """Get analyst recommendations."""
        data = self._make_request(f"analyst-stock-recommendations/{symbol}")
        return data if data else []
    
    def get_price_target(self, symbol: str) -> List[Dict]:
        """Get analyst price targets."""
        data = self._make_request(f"price-target/{symbol}")
        return data if data else []
    
    def get_price_target_summary(self, symbol: str) -> Optional[Dict]:
        """Get price target consensus summary."""
        data = self._make_request(f"price-target-summary/{symbol}")
        if data and len(data) > 0:
            return data[0]
        return None
    
    # =========================================================================
    # Earnings
    # =========================================================================
    
    def get_earnings_calendar(self, from_date: str = None, to_date: str = None) -> List[Dict]:
        """Get earnings calendar for date range."""
        params = {}
        if from_date:
            params["from"] = from_date
        if to_date:
            params["to"] = to_date
        
        data = self._make_request("earning_calendar", params)
        return data if data else []
    
    def get_earnings_surprises(self, symbol: str) -> List[Dict]:
        """Get historical earnings surprises."""
        data = self._make_request(f"earnings-surprises/{symbol}")
        return data if data else []
    
    # =========================================================================
    # Stock Data
    # =========================================================================
    
    def get_quote(self, symbol: str) -> Optional[Dict]:
        """Get real-time stock quote."""
        data = self._make_request(f"quote/{symbol}")
        if data and len(data) > 0:
            return data[0]
        return None
    
    def get_quotes_batch(self, symbols: List[str]) -> List[Dict]:
        """Get quotes for multiple symbols."""
        symbols_str = ",".join(symbols)
        data = self._make_request(f"quote/{symbols_str}")
        return data if data else []
    
    def get_stock_peers(self, symbol: str) -> List[str]:
        """Get peer companies for a stock."""
        data = self._make_request(f"stock_peers", {"symbol": symbol})
        if data and len(data) > 0 and "peersList" in data[0]:
            return data[0]["peersList"]
        return []
    
    # =========================================================================
    # Insider & Institutional
    # =========================================================================
    
    def get_insider_trading(self, symbol: str, limit: int = 100) -> List[Dict]:
        """Get insider trading transactions."""
        data = self._make_request(f"insider-trading", {
            "symbol": symbol,
            "limit": limit
        })
        return data if data else []
    
    def get_institutional_holders(self, symbol: str) -> List[Dict]:
        """Get institutional holders."""
        data = self._make_request(f"institutional-holder/{symbol}")
        return data if data else []
    
    # =========================================================================
    # Stock Screener
    # =========================================================================
    
    def run_stock_screener(self, 
                           market_cap_min: int = None,
                           market_cap_max: int = None,
                           sector: str = None,
                           industry: str = None,
                           country: str = "US",
                           exchange: str = None,
                           dividend_min: float = None,
                           volume_min: int = None,
                           is_actively_trading: bool = True,
                           limit: int = 100) -> List[Dict]:
        """
        Run FMP stock screener with filters.
        
        Note: Free tier has limited screener access.
        """
        params = {"limit": limit}
        
        if market_cap_min:
            params["marketCapMoreThan"] = market_cap_min
        if market_cap_max:
            params["marketCapLowerThan"] = market_cap_max
        if sector:
            params["sector"] = sector
        if industry:
            params["industry"] = industry
        if country:
            params["country"] = country
        if exchange:
            params["exchange"] = exchange
        if dividend_min:
            params["dividendMoreThan"] = dividend_min
        if volume_min:
            params["volumeMoreThan"] = volume_min
        if is_actively_trading:
            params["isActivelyTrading"] = "true"
        
        data = self._make_request("stock-screener", params)
        return data if data else []
    
    # =========================================================================
    # Screening Integration
    # =========================================================================
    
    def get_screening_data(self, symbol: str) -> Dict:
        """
        Get comprehensive data for equity screening.
        Optimized for research engine integration.
        """
        result = {
            "symbol": symbol,
            "source": self.SOURCE_NAME,
            "fetched_at": datetime.now().isoformat()
        }
        
        # Company profile
        profile = self.get_company_profile(symbol)
        if profile:
            result["profile"] = {
                "name": profile.get("companyName"),
                "sector": profile.get("sector"),
                "industry": profile.get("industry"),
                "market_cap": profile.get("mktCap"),
                "price": profile.get("price"),
                "beta": profile.get("beta"),
                "avg_volume": profile.get("volAvg"),
                "exchange": profile.get("exchangeShortName"),
                "country": profile.get("country"),
                "is_etf": profile.get("isEtf"),
                "is_fund": profile.get("isFund"),
            }
        
        # Latest ratios
        ratios = self.get_financial_ratios(symbol, period="quarter", limit=1)
        if ratios:
            r = ratios[0]
            result["ratios"] = {
                "pe_ratio": r.get("priceEarningsRatio"),
                "peg_ratio": r.get("priceEarningsToGrowthRatio"),
                "pb_ratio": r.get("priceToBookRatio"),
                "ps_ratio": r.get("priceToSalesRatio"),
                "ev_ebitda": r.get("enterpriseValueMultiple"),
                "gross_margin": r.get("grossProfitMargin"),
                "operating_margin": r.get("operatingProfitMargin"),
                "net_margin": r.get("netProfitMargin"),
                "roe": r.get("returnOnEquity"),
                "roa": r.get("returnOnAssets"),
                "roic": r.get("returnOnCapitalEmployed"),
                "current_ratio": r.get("currentRatio"),
                "quick_ratio": r.get("quickRatio"),
                "debt_equity": r.get("debtEquityRatio"),
                "fcf_yield": r.get("freeCashFlowYield"),
            }
        
        # Growth metrics
        growth = self.get_financial_growth(symbol, period="quarter", limit=4)
        if growth:
            g = growth[0]
            result["growth"] = {
                "revenue_growth": g.get("revenueGrowth"),
                "gross_profit_growth": g.get("grossProfitGrowth"),
                "operating_income_growth": g.get("operatingIncomeGrowth"),
                "net_income_growth": g.get("netIncomeGrowth"),
                "eps_growth": g.get("epsgrowth"),
                "fcf_growth": g.get("freeCashFlowGrowth"),
            }
            
            # Calculate multi-period growth (if we have 4 quarters)
            if len(growth) >= 4:
                revenues = [g.get("revenueGrowth") for g in growth[:4] if g.get("revenueGrowth")]
                if revenues:
                    result["growth"]["avg_revenue_growth_4q"] = sum(revenues) / len(revenues)
        
        # Key metrics
        metrics = self.get_key_metrics(symbol, period="quarter", limit=1)
        if metrics:
            m = metrics[0]
            result["key_metrics"] = {
                "revenue_per_share": m.get("revenuePerShare"),
                "net_income_per_share": m.get("netIncomePerShare"),
                "fcf_per_share": m.get("freeCashFlowPerShare"),
                "book_value_per_share": m.get("bookValuePerShare"),
                "tangible_book_value_per_share": m.get("tangibleBookValuePerShare"),
                "enterprise_value": m.get("enterpriseValue"),
                "ev_to_sales": m.get("evToSales"),
                "ev_to_fcf": m.get("evToFreeCashFlow"),
                "market_cap": m.get("marketCap"),
                "pe_ratio": m.get("peRatio"),
                "pfcf_ratio": m.get("pfcfRatio"),
            }
        
        # Analyst estimates
        estimates = self.get_analyst_estimates(symbol, period="quarter", limit=4)
        if estimates:
            # Find next quarter estimate
            future_estimates = [e for e in estimates if e.get("date", "") > datetime.now().strftime("%Y-%m-%d")]
            if future_estimates:
                next_q = future_estimates[-1]
                result["estimates"] = {
                    "next_quarter_date": next_q.get("date"),
                    "estimated_revenue_avg": next_q.get("estimatedRevenueAvg"),
                    "estimated_revenue_high": next_q.get("estimatedRevenueHigh"),
                    "estimated_revenue_low": next_q.get("estimatedRevenueLow"),
                    "estimated_eps_avg": next_q.get("estimatedEpsAvg"),
                    "estimated_eps_high": next_q.get("estimatedEpsHigh"),
                    "estimated_eps_low": next_q.get("estimatedEpsLow"),
                    "num_analysts_revenue": next_q.get("numberAnalystsEstimatedRevenue"),
                    "num_analysts_eps": next_q.get("numberAnalystEstimatedEps"),
                }
        
        # Price target
        pt = self.get_price_target_summary(symbol)
        if pt:
            result["price_targets"] = {
                "current_price": pt.get("lastPrice"),
                "target_high": pt.get("targetHigh"),
                "target_low": pt.get("targetLow"),
                "target_mean": pt.get("targetConsensus"),
                "target_median": pt.get("targetMedian"),
                "num_analysts": pt.get("numberOfAnalysts"),
            }
            
            # Calculate upside
            if pt.get("targetConsensus") and pt.get("lastPrice"):
                result["price_targets"]["upside_pct"] = round(
                    (pt["targetConsensus"] / pt["lastPrice"] - 1) * 100, 2
                )
        
        return result
