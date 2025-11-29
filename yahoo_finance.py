"""
Yahoo Finance Data Fetcher
==========================
Primary data source using yfinance library.
Provides: price data, fundamentals, company info, earnings, analyst ratings.
No API key required.
"""

import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
import logging

logger = logging.getLogger(__name__)

class YahooFinanceFetcher:
    """Fetch market data from Yahoo Finance."""
    
    SOURCE_NAME = "yahoo_finance"
    
    def __init__(self, rate_limiter=None, cache=None):
        self.rate_limiter = rate_limiter
        self.cache = cache
    
    def _acquire_rate_limit(self):
        """Acquire rate limit token if limiter configured."""
        if self.rate_limiter:
            self.rate_limiter.acquire(self.SOURCE_NAME)
    
    def get_ticker(self, symbol: str) -> yf.Ticker:
        """Get yfinance Ticker object."""
        self._acquire_rate_limit()
        return yf.Ticker(symbol)
    
    # =========================================================================
    # Price Data
    # =========================================================================
    
    def get_daily_prices(self, symbol: str, period: str = "2y", 
                         start: str = None, end: str = None) -> List[Dict]:
        """
        Get daily OHLCV price data.
        
        Args:
            symbol: Stock ticker
            period: Data period (1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y, 10y, ytd, max)
            start: Start date (YYYY-MM-DD), overrides period
            end: End date (YYYY-MM-DD)
        
        Returns:
            List of daily price records
        """
        try:
            ticker = self.get_ticker(symbol)
            
            if start:
                df = ticker.history(start=start, end=end or datetime.now().strftime("%Y-%m-%d"))
            else:
                df = ticker.history(period=period)
            
            if df.empty:
                logger.warning(f"No price data for {symbol}")
                return []
            
            # Standardize column names and format
            df = df.reset_index()
            records = []
            for _, row in df.iterrows():
                records.append({
                    "date": row["Date"].strftime("%Y-%m-%d") if hasattr(row["Date"], "strftime") else str(row["Date"])[:10],
                    "open": round(row["Open"], 4) if pd.notna(row["Open"]) else None,
                    "high": round(row["High"], 4) if pd.notna(row["High"]) else None,
                    "low": round(row["Low"], 4) if pd.notna(row["Low"]) else None,
                    "close": round(row["Close"], 4) if pd.notna(row["Close"]) else None,
                    "adj_close": round(row["Close"], 4) if pd.notna(row["Close"]) else None,
                    "volume": int(row["Volume"]) if pd.notna(row["Volume"]) else None,
                })
            
            logger.info(f"Fetched {len(records)} price records for {symbol}")
            return records
            
        except Exception as e:
            logger.error(f"Error fetching prices for {symbol}: {e}")
            return []
    
    def get_current_price(self, symbol: str) -> Optional[Dict]:
        """Get current/latest price data."""
        try:
            ticker = self.get_ticker(symbol)
            info = ticker.info
            
            return {
                "symbol": symbol,
                "price": info.get("currentPrice") or info.get("regularMarketPrice"),
                "previous_close": info.get("previousClose"),
                "open": info.get("open") or info.get("regularMarketOpen"),
                "day_high": info.get("dayHigh") or info.get("regularMarketDayHigh"),
                "day_low": info.get("dayLow") or info.get("regularMarketDayLow"),
                "volume": info.get("volume") or info.get("regularMarketVolume"),
                "avg_volume": info.get("averageVolume"),
                "market_cap": info.get("marketCap"),
                "fifty_two_week_high": info.get("fiftyTwoWeekHigh"),
                "fifty_two_week_low": info.get("fiftyTwoWeekLow"),
                "timestamp": datetime.now().isoformat()
            }
        except Exception as e:
            logger.error(f"Error fetching current price for {symbol}: {e}")
            return None
    
    def get_prices_batch(self, symbols: List[str], period: str = "1y") -> Dict[str, List[Dict]]:
        """Get prices for multiple symbols efficiently."""
        results = {}
        try:
            # Use yf.download for batch efficiency
            self._acquire_rate_limit()
            df = yf.download(symbols, period=period, group_by="ticker", progress=False)
            
            for symbol in symbols:
                try:
                    if len(symbols) == 1:
                        symbol_df = df
                    else:
                        symbol_df = df[symbol] if symbol in df.columns.get_level_values(0) else pd.DataFrame()
                    
                    if symbol_df.empty:
                        results[symbol] = []
                        continue
                    
                    symbol_df = symbol_df.reset_index()
                    records = []
                    for _, row in symbol_df.iterrows():
                        records.append({
                            "date": row["Date"].strftime("%Y-%m-%d") if hasattr(row["Date"], "strftime") else str(row["Date"])[:10],
                            "open": round(float(row["Open"]), 4) if pd.notna(row.get("Open")) else None,
                            "high": round(float(row["High"]), 4) if pd.notna(row.get("High")) else None,
                            "low": round(float(row["Low"]), 4) if pd.notna(row.get("Low")) else None,
                            "close": round(float(row["Close"]), 4) if pd.notna(row.get("Close")) else None,
                            "adj_close": round(float(row["Adj Close"]), 4) if pd.notna(row.get("Adj Close")) else None,
                            "volume": int(row["Volume"]) if pd.notna(row.get("Volume")) else None,
                        })
                    results[symbol] = records
                except Exception as e:
                    logger.error(f"Error processing {symbol}: {e}")
                    results[symbol] = []
            
            return results
            
        except Exception as e:
            logger.error(f"Error in batch price fetch: {e}")
            return {s: [] for s in symbols}
    
    # =========================================================================
    # Company Info & Fundamentals
    # =========================================================================
    
    def get_company_info(self, symbol: str) -> Optional[Dict]:
        """Get comprehensive company information."""
        try:
            ticker = self.get_ticker(symbol)
            info = ticker.info
            
            return {
                "symbol": symbol,
                "name": info.get("longName") or info.get("shortName"),
                "sector": info.get("sector"),
                "industry": info.get("industry"),
                "description": info.get("longBusinessSummary"),
                "employees": info.get("fullTimeEmployees"),
                "market_cap": info.get("marketCap"),
                "exchange": info.get("exchange"),
                "country": info.get("country"),
                "website": info.get("website"),
                "currency": info.get("currency"),
                
                # Valuation metrics
                "pe_ratio": info.get("trailingPE"),
                "forward_pe": info.get("forwardPE"),
                "peg_ratio": info.get("pegRatio"),
                "ps_ratio": info.get("priceToSalesTrailing12Months"),
                "pb_ratio": info.get("priceToBook"),
                "ev_revenue": info.get("enterpriseToRevenue"),
                "ev_ebitda": info.get("enterpriseToEbitda"),
                
                # Growth & profitability
                "revenue_growth": info.get("revenueGrowth"),
                "earnings_growth": info.get("earningsGrowth"),
                "gross_margins": info.get("grossMargins"),
                "operating_margins": info.get("operatingMargins"),
                "profit_margins": info.get("profitMargins"),
                "roe": info.get("returnOnEquity"),
                "roa": info.get("returnOnAssets"),
                
                # Financial health
                "total_cash": info.get("totalCash"),
                "total_debt": info.get("totalDebt"),
                "current_ratio": info.get("currentRatio"),
                "quick_ratio": info.get("quickRatio"),
                "debt_to_equity": info.get("debtToEquity"),
                
                # Per share data
                "eps": info.get("trailingEps"),
                "forward_eps": info.get("forwardEps"),
                "book_value": info.get("bookValue"),
                "revenue_per_share": info.get("revenuePerShare"),
                
                # Dividends
                "dividend_yield": info.get("dividendYield"),
                "dividend_rate": info.get("dividendRate"),
                "payout_ratio": info.get("payoutRatio"),
                
                # Analyst data
                "target_high": info.get("targetHighPrice"),
                "target_low": info.get("targetLowPrice"),
                "target_mean": info.get("targetMeanPrice"),
                "target_median": info.get("targetMedianPrice"),
                "recommendation": info.get("recommendationKey"),
                "recommendation_mean": info.get("recommendationMean"),
                "num_analysts": info.get("numberOfAnalystOpinions"),
                
                # Beta and risk
                "beta": info.get("beta"),
                "fifty_day_avg": info.get("fiftyDayAverage"),
                "two_hundred_day_avg": info.get("twoHundredDayAverage"),
                
                "fetched_at": datetime.now().isoformat()
            }
        except Exception as e:
            logger.error(f"Error fetching company info for {symbol}: {e}")
            return None
    
    def get_financials(self, symbol: str) -> Dict[str, Any]:
        """Get financial statements (income, balance, cash flow)."""
        try:
            ticker = self.get_ticker(symbol)
            
            result = {
                "symbol": symbol,
                "income_statement": {},
                "balance_sheet": {},
                "cash_flow": {},
                "quarterly_income": {},
                "quarterly_balance": {},
                "quarterly_cash_flow": {},
            }
            
            # Annual financials
            if ticker.income_stmt is not None and not ticker.income_stmt.empty:
                result["income_statement"] = ticker.income_stmt.to_dict()
            if ticker.balance_sheet is not None and not ticker.balance_sheet.empty:
                result["balance_sheet"] = ticker.balance_sheet.to_dict()
            if ticker.cashflow is not None and not ticker.cashflow.empty:
                result["cash_flow"] = ticker.cashflow.to_dict()
            
            # Quarterly financials
            if ticker.quarterly_income_stmt is not None and not ticker.quarterly_income_stmt.empty:
                result["quarterly_income"] = ticker.quarterly_income_stmt.to_dict()
            if ticker.quarterly_balance_sheet is not None and not ticker.quarterly_balance_sheet.empty:
                result["quarterly_balance"] = ticker.quarterly_balance_sheet.to_dict()
            if ticker.quarterly_cashflow is not None and not ticker.quarterly_cashflow.empty:
                result["quarterly_cash_flow"] = ticker.quarterly_cashflow.to_dict()
            
            return result
            
        except Exception as e:
            logger.error(f"Error fetching financials for {symbol}: {e}")
            return {"symbol": symbol, "error": str(e)}
    
    # =========================================================================
    # Earnings & Analyst Data
    # =========================================================================
    
    def get_earnings(self, symbol: str) -> Dict[str, Any]:
        """Get earnings history and estimates."""
        try:
            ticker = self.get_ticker(symbol)
            
            result = {
                "symbol": symbol,
                "earnings_history": [],
                "earnings_dates": [],
                "revenue_estimates": {},
                "earnings_estimates": {}
            }
            
            # Earnings history
            if ticker.earnings_history is not None and not ticker.earnings_history.empty:
                eh = ticker.earnings_history.reset_index()
                for _, row in eh.iterrows():
                    result["earnings_history"].append({
                        "quarter": str(row.get("Quarter", "")),
                        "eps_estimate": row.get("epsEstimate"),
                        "eps_actual": row.get("epsActual"),
                        "surprise_pct": row.get("surprisePercent")
                    })
            
            # Upcoming earnings dates
            try:
                if ticker.calendar is not None:
                    cal = ticker.calendar
                    if isinstance(cal, pd.DataFrame) and not cal.empty:
                        result["next_earnings"] = cal.to_dict()
            except:
                pass
            
            # Analyst estimates
            try:
                if ticker.analyst_price_targets is not None:
                    result["price_targets"] = ticker.analyst_price_targets
            except:
                pass
            
            return result
            
        except Exception as e:
            logger.error(f"Error fetching earnings for {symbol}: {e}")
            return {"symbol": symbol, "error": str(e)}
    
    def get_analyst_recommendations(self, symbol: str) -> List[Dict]:
        """Get analyst recommendation history."""
        try:
            ticker = self.get_ticker(symbol)
            recs = ticker.recommendations
            
            if recs is None or recs.empty:
                return []
            
            recs = recs.reset_index()
            results = []
            for _, row in recs.iterrows():
                results.append({
                    "date": str(row.get("Date", ""))[:10] if pd.notna(row.get("Date")) else None,
                    "firm": row.get("Firm"),
                    "to_grade": row.get("To Grade"),
                    "from_grade": row.get("From Grade"),
                    "action": row.get("Action")
                })
            
            return results[-50:]  # Last 50 recommendations
            
        except Exception as e:
            logger.error(f"Error fetching recommendations for {symbol}: {e}")
            return []
    
    # =========================================================================
    # Holders & Ownership
    # =========================================================================
    
    def get_holders(self, symbol: str) -> Dict[str, Any]:
        """Get institutional and insider holder information."""
        try:
            ticker = self.get_ticker(symbol)
            
            result = {
                "symbol": symbol,
                "institutional_holders": [],
                "major_holders": {},
                "insider_transactions": []
            }
            
            # Institutional holders
            if ticker.institutional_holders is not None and not ticker.institutional_holders.empty:
                ih = ticker.institutional_holders
                for _, row in ih.iterrows():
                    result["institutional_holders"].append({
                        "holder": row.get("Holder"),
                        "shares": row.get("Shares"),
                        "date_reported": str(row.get("Date Reported", ""))[:10],
                        "pct_held": row.get("% Out"),
                        "value": row.get("Value")
                    })
            
            # Major holders summary
            if ticker.major_holders is not None and not ticker.major_holders.empty:
                mh = ticker.major_holders
                result["major_holders"] = mh.to_dict()
            
            # Insider transactions
            if ticker.insider_transactions is not None and not ticker.insider_transactions.empty:
                it = ticker.insider_transactions
                for _, row in it.iterrows():
                    result["insider_transactions"].append({
                        "insider": row.get("Insider"),
                        "relation": row.get("Relation"),
                        "transaction": row.get("Transaction"),
                        "date": str(row.get("Start Date", ""))[:10],
                        "shares": row.get("Shares"),
                        "value": row.get("Value")
                    })
            
            return result
            
        except Exception as e:
            logger.error(f"Error fetching holders for {symbol}: {e}")
            return {"symbol": symbol, "error": str(e)}
    
    # =========================================================================
    # News
    # =========================================================================
    
    def get_news(self, symbol: str) -> List[Dict]:
        """Get recent news for a symbol."""
        try:
            ticker = self.get_ticker(symbol)
            news = ticker.news
            
            if not news:
                return []
            
            results = []
            for item in news[:20]:  # Last 20 articles
                results.append({
                    "title": item.get("title"),
                    "publisher": item.get("publisher"),
                    "link": item.get("link"),
                    "published": datetime.fromtimestamp(item.get("providerPublishTime", 0)).isoformat() if item.get("providerPublishTime") else None,
                    "type": item.get("type"),
                    "thumbnail": item.get("thumbnail", {}).get("resolutions", [{}])[0].get("url") if item.get("thumbnail") else None
                })
            
            return results
            
        except Exception as e:
            logger.error(f"Error fetching news for {symbol}: {e}")
            return []
    
    # =========================================================================
    # Bulk Operations
    # =========================================================================
    
    def get_screening_data(self, symbol: str) -> Dict:
        """
        Get all data needed for equity screening in one call.
        Optimized for research engine integration.
        """
        result = {
            "symbol": symbol,
            "fetched_at": datetime.now().isoformat(),
            "source": self.SOURCE_NAME
        }
        
        try:
            ticker = self.get_ticker(symbol)
            info = ticker.info
            
            # Core identifiers
            result["name"] = info.get("longName") or info.get("shortName")
            result["sector"] = info.get("sector")
            result["industry"] = info.get("industry")
            result["market_cap"] = info.get("marketCap")
            result["exchange"] = info.get("exchange")
            
            # Valuation (for Stage 1C/1D)
            result["valuation"] = {
                "pe_ratio": info.get("trailingPE"),
                "forward_pe": info.get("forwardPE"),
                "ps_ratio": info.get("priceToSalesTrailing12Months"),
                "pb_ratio": info.get("priceToBook"),
                "ev_ebitda": info.get("enterpriseToEbitda"),
                "ev_revenue": info.get("enterpriseToRevenue"),
                "peg_ratio": info.get("pegRatio"),
            }
            
            # Growth (for quantitative screen)
            result["growth"] = {
                "revenue_growth": info.get("revenueGrowth"),
                "earnings_growth": info.get("earningsGrowth"),
                "earnings_quarterly_growth": info.get("earningsQuarterlyGrowth"),
            }
            
            # Quality/Profitability
            result["quality"] = {
                "gross_margin": info.get("grossMargins"),
                "operating_margin": info.get("operatingMargins"),
                "net_margin": info.get("profitMargins"),
                "roe": info.get("returnOnEquity"),
                "roa": info.get("returnOnAssets"),
                "roic": None,  # Not available in yfinance, calculate separately
            }
            
            # Financial Health
            result["financial_health"] = {
                "current_ratio": info.get("currentRatio"),
                "quick_ratio": info.get("quickRatio"),
                "debt_to_equity": info.get("debtToEquity"),
                "total_cash": info.get("totalCash"),
                "total_debt": info.get("totalDebt"),
                "free_cash_flow": info.get("freeCashflow"),
            }
            
            # Analyst Sentiment
            result["analyst"] = {
                "recommendation": info.get("recommendationKey"),
                "recommendation_mean": info.get("recommendationMean"),
                "num_analysts": info.get("numberOfAnalystOpinions"),
                "target_mean": info.get("targetMeanPrice"),
                "target_high": info.get("targetHighPrice"),
                "target_low": info.get("targetLowPrice"),
                "current_price": info.get("currentPrice") or info.get("regularMarketPrice"),
                "upside_pct": None  # Calculate: (target_mean / current_price - 1)
            }
            
            # Calculate upside
            if result["analyst"]["target_mean"] and result["analyst"]["current_price"]:
                result["analyst"]["upside_pct"] = round(
                    (result["analyst"]["target_mean"] / result["analyst"]["current_price"] - 1) * 100, 2
                )
            
            # Beta and volatility
            result["risk"] = {
                "beta": info.get("beta"),
                "fifty_two_week_high": info.get("fiftyTwoWeekHigh"),
                "fifty_two_week_low": info.get("fiftyTwoWeekLow"),
            }
            
            # Calculate 52-week range position
            if result["risk"]["fifty_two_week_high"] and result["risk"]["fifty_two_week_low"]:
                current = result["analyst"]["current_price"]
                high = result["risk"]["fifty_two_week_high"]
                low = result["risk"]["fifty_two_week_low"]
                if current and high != low:
                    result["risk"]["range_position"] = round((current - low) / (high - low) * 100, 1)
            
            return result
            
        except Exception as e:
            logger.error(f"Error fetching screening data for {symbol}: {e}")
            result["error"] = str(e)
            return result
    
    def get_screening_data_batch(self, symbols: List[str]) -> Dict[str, Dict]:
        """Get screening data for multiple symbols."""
        results = {}
        for symbol in symbols:
            results[symbol] = self.get_screening_data(symbol)
        return results
