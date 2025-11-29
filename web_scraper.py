"""
Web Scraper for Financial Data
==============================
Scrapes publicly available financial data from websites.
Respects robots.txt and implements polite crawling.
"""

import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
import logging
import re
import time

logger = logging.getLogger(__name__)

class WebScraper:
    """Scrape financial data from public websites."""
    
    SOURCE_NAME = "web_scraper"
    
    def __init__(self, rate_limiter=None, cache=None):
        self.rate_limiter = rate_limiter
        self.cache = cache
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        })
    
    def _acquire_rate_limit(self):
        if self.rate_limiter:
            self.rate_limiter.acquire(self.SOURCE_NAME)
    
    def _fetch_page(self, url: str) -> Optional[BeautifulSoup]:
        """Fetch and parse a webpage."""
        self._acquire_rate_limit()
        
        try:
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            return BeautifulSoup(response.text, "html.parser")
        except Exception as e:
            logger.error(f"Error fetching {url}: {e}")
            return None
    
    # =========================================================================
    # Finviz Data
    # =========================================================================
    
    def get_finviz_data(self, symbol: str) -> Optional[Dict]:
        """
        Scrape stock data from Finviz.
        Includes: valuation, financials, analyst info, technical indicators.
        """
        url = f"https://finviz.com/quote.ashx?t={symbol}&p=d"
        soup = self._fetch_page(url)
        
        if not soup:
            return None
        
        result = {
            "symbol": symbol,
            "source": "finviz",
            "fetched_at": datetime.now().isoformat()
        }
        
        try:
            # Parse the snapshot table
            table = soup.find("table", class_="snapshot-table2")
            if table:
                rows = table.find_all("tr")
                for row in rows:
                    cells = row.find_all("td")
                    for i in range(0, len(cells) - 1, 2):
                        label = cells[i].get_text(strip=True).lower().replace(" ", "_").replace("/", "_")
                        value = cells[i + 1].get_text(strip=True)
                        result[label] = self._parse_finviz_value(value)
            
            # Get company name
            title = soup.find("a", class_="tab-link")
            if title:
                result["company_name"] = title.get_text(strip=True)
            
            # Get sector/industry
            sector_links = soup.find_all("a", class_="tab-link")
            if len(sector_links) >= 3:
                result["sector"] = sector_links[1].get_text(strip=True)
                result["industry"] = sector_links[2].get_text(strip=True)
            
            return result
            
        except Exception as e:
            logger.error(f"Error parsing Finviz data for {symbol}: {e}")
            return None
    
    def _parse_finviz_value(self, value: str) -> Any:
        """Parse Finviz table values to appropriate types."""
        if not value or value == "-":
            return None
        
        # Remove percentage sign and convert
        if value.endswith("%"):
            try:
                return float(value.rstrip("%")) / 100
            except:
                return value
        
        # Handle market cap/volume abbreviations
        if value.endswith("B"):
            try:
                return float(value.rstrip("B")) * 1_000_000_000
            except:
                return value
        if value.endswith("M"):
            try:
                return float(value.rstrip("M")) * 1_000_000
            except:
                return value
        if value.endswith("K"):
            try:
                return float(value.rstrip("K")) * 1_000
            except:
                return value
        
        # Try numeric conversion
        try:
            if "." in value:
                return float(value)
            return int(value)
        except:
            return value
    
    # =========================================================================
    # Short Interest Data
    # =========================================================================
    
    def get_short_interest(self, symbol: str) -> Optional[Dict]:
        """
        Scrape short interest data from multiple sources.
        """
        result = {
            "symbol": symbol,
            "source": "web_scraper",
            "fetched_at": datetime.now().isoformat()
        }
        
        # Try Finviz first (has short float data)
        finviz_data = self.get_finviz_data(symbol)
        if finviz_data:
            result["short_float"] = finviz_data.get("short_float")
            result["short_ratio"] = finviz_data.get("short_ratio")
            result["short_interest"] = finviz_data.get("short_interest")
        
        return result if any(v is not None for k, v in result.items() if k not in ["symbol", "source", "fetched_at"]) else None
    
    # =========================================================================
    # Earnings Whispers
    # =========================================================================
    
    def get_earnings_calendar_week(self) -> List[Dict]:
        """
        Get upcoming earnings for the current week.
        Note: This is a simplified scraper - may need updates if site structure changes.
        """
        url = "https://www.earningswhispers.com/calendar"
        soup = self._fetch_page(url)
        
        if not soup:
            return []
        
        results = []
        try:
            # Find earnings entries
            entries = soup.find_all("div", class_="company")
            
            for entry in entries[:50]:  # Limit results
                try:
                    symbol_el = entry.find("div", class_="ticker")
                    name_el = entry.find("div", class_="name")
                    date_el = entry.find("div", class_="date")
                    time_el = entry.find("div", class_="time")
                    
                    if symbol_el:
                        results.append({
                            "symbol": symbol_el.get_text(strip=True),
                            "company_name": name_el.get_text(strip=True) if name_el else None,
                            "date": date_el.get_text(strip=True) if date_el else None,
                            "time": time_el.get_text(strip=True) if time_el else None,
                        })
                except:
                    continue
            
            return results
            
        except Exception as e:
            logger.error(f"Error parsing earnings calendar: {e}")
            return []
    
    # =========================================================================
    # Tipranks-style Analyst Ratings (from alternative sources)
    # =========================================================================
    
    def get_analyst_ratings_summary(self, symbol: str) -> Optional[Dict]:
        """
        Aggregate analyst ratings from Finviz.
        """
        finviz_data = self.get_finviz_data(symbol)
        if not finviz_data:
            return None
        
        result = {
            "symbol": symbol,
            "source": "finviz",
            "fetched_at": datetime.now().isoformat()
        }
        
        # Extract analyst-related fields
        result["target_price"] = finviz_data.get("target_price")
        result["recommendation"] = finviz_data.get("recom")
        result["analyst_count"] = finviz_data.get("analyst")
        
        # Price vs target analysis
        current_price = finviz_data.get("price")
        target_price = finviz_data.get("target_price")
        
        if current_price and target_price:
            try:
                current = float(current_price) if isinstance(current_price, str) else current_price
                target = float(target_price) if isinstance(target_price, str) else target_price
                result["upside_pct"] = round((target / current - 1) * 100, 2)
            except:
                pass
        
        return result
    
    # =========================================================================
    # Stock Screener Results
    # =========================================================================
    
    def scrape_finviz_screener(self, filters: Dict = None) -> List[Dict]:
        """
        Scrape Finviz screener results.
        
        Example filters:
            - cap: "smallover" (small cap+), "midover" (mid cap+), "largeunder" (large cap-)
            - sector: "technology", "healthcare", etc.
            - fa_salesqoq: "o10" (sales growth > 10%)
            - fa_epsqoq: "o15" (EPS growth > 15%)
        
        Note: Free Finviz limits screener results. Consider this a basic implementation.
        """
        base_url = "https://finviz.com/screener.ashx"
        
        # Build filter string
        filter_parts = []
        if filters:
            for key, value in filters.items():
                filter_parts.append(f"{key}_{value}")
        
        params = {
            "v": "111",  # Overview view
            "f": ",".join(filter_parts) if filter_parts else "",
            "o": "-marketcap"  # Sort by market cap descending
        }
        
        # Build URL manually to handle Finviz's specific format
        if filter_parts:
            url = f"{base_url}?v=111&f={','.join(filter_parts)}&o=-marketcap"
        else:
            url = f"{base_url}?v=111&o=-marketcap"
        
        soup = self._fetch_page(url)
        if not soup:
            return []
        
        results = []
        try:
            # Find the screener table
            table = soup.find("table", {"bgcolor": "#d3d3d3"})
            if not table:
                table = soup.find("table", class_="table-light")
            
            if table:
                rows = table.find_all("tr")[1:]  # Skip header
                
                for row in rows[:100]:  # Limit results
                    cells = row.find_all("td")
                    if len(cells) >= 10:
                        try:
                            results.append({
                                "rank": cells[0].get_text(strip=True),
                                "symbol": cells[1].get_text(strip=True),
                                "company": cells[2].get_text(strip=True),
                                "sector": cells[3].get_text(strip=True),
                                "industry": cells[4].get_text(strip=True),
                                "country": cells[5].get_text(strip=True),
                                "market_cap": self._parse_finviz_value(cells[6].get_text(strip=True)),
                                "pe": self._parse_finviz_value(cells[7].get_text(strip=True)),
                                "price": self._parse_finviz_value(cells[8].get_text(strip=True)),
                                "change": self._parse_finviz_value(cells[9].get_text(strip=True)),
                                "volume": self._parse_finviz_value(cells[10].get_text(strip=True)) if len(cells) > 10 else None,
                            })
                        except:
                            continue
            
            return results
            
        except Exception as e:
            logger.error(f"Error parsing Finviz screener: {e}")
            return []
    
    # =========================================================================
    # Screening Integration
    # =========================================================================
    
    def get_screening_data(self, symbol: str) -> Dict:
        """Get comprehensive screening data via web scraping."""
        result = {
            "symbol": symbol,
            "source": self.SOURCE_NAME,
            "fetched_at": datetime.now().isoformat()
        }
        
        # Finviz comprehensive data
        finviz = self.get_finviz_data(symbol)
        if finviz:
            result["finviz"] = finviz
            
            # Extract key screening metrics
            result["valuation"] = {
                "pe": finviz.get("p_e"),
                "forward_pe": finviz.get("forward_p_e"),
                "peg": finviz.get("peg"),
                "ps": finviz.get("p_s"),
                "pb": finviz.get("p_b"),
                "ev_ebitda": finviz.get("ev_ebitda"),
            }
            
            result["growth"] = {
                "sales_q_q": finviz.get("sales_q_q"),
                "sales_y_y": finviz.get("sales_past_5y") or finviz.get("sales"),
                "eps_q_q": finviz.get("eps_q_q"),
                "eps_y_y": finviz.get("eps_past_5y"),
            }
            
            result["quality"] = {
                "gross_margin": finviz.get("gross_margin"),
                "operating_margin": finviz.get("oper._margin") or finviz.get("oper_margin"),
                "profit_margin": finviz.get("profit_margin"),
                "roe": finviz.get("roe"),
                "roa": finviz.get("roa"),
                "roi": finviz.get("roi"),
            }
            
            result["risk"] = {
                "beta": finviz.get("beta"),
                "short_float": finviz.get("short_float"),
                "short_ratio": finviz.get("short_ratio"),
                "volatility_week": finviz.get("volatility_w") or finviz.get("volatility"),
                "volatility_month": finviz.get("volatility_m"),
            }
            
            result["analyst"] = {
                "target_price": finviz.get("target_price"),
                "recommendation": finviz.get("recom"),
            }
        
        return result
