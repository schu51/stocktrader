"""
SEC EDGAR Data Fetcher
======================
Fetch SEC filings, insider transactions, and institutional holdings.
No API key required, but requires User-Agent header.
Rate limit: 10 requests/second
"""

import requests
import json
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
import logging
import re

logger = logging.getLogger(__name__)

class SECEdgarFetcher:
    """Fetch regulatory data from SEC EDGAR."""
    
    SOURCE_NAME = "sec_edgar"
    BASE_URL = "https://data.sec.gov"
    SUBMISSIONS_URL = f"{BASE_URL}/submissions"
    COMPANY_TICKERS_URL = f"{BASE_URL}/files/company_tickers.json"
    
    def __init__(self, user_agent: str, rate_limiter=None, cache=None):
        self.user_agent = user_agent
        self.rate_limiter = rate_limiter
        self.cache = cache
        self.headers = {
            "User-Agent": user_agent,
            "Accept-Encoding": "gzip, deflate"
        }
        self._cik_cache = {}
    
    def _acquire_rate_limit(self):
        if self.rate_limiter:
            self.rate_limiter.acquire(self.SOURCE_NAME)
    
    def _make_request(self, url: str) -> Optional[Dict]:
        """Make rate-limited request to SEC."""
        self._acquire_rate_limit()
        try:
            response = requests.get(url, headers=self.headers, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            logger.error(f"HTTP error fetching {url}: {e}")
            return None
        except Exception as e:
            logger.error(f"Error fetching {url}: {e}")
            return None
    
    # =========================================================================
    # CIK Lookup
    # =========================================================================
    
    def get_cik(self, symbol: str) -> Optional[str]:
        """Get CIK (Central Index Key) for a ticker symbol."""
        symbol = symbol.upper()
        
        # Check cache first
        if symbol in self._cik_cache:
            return self._cik_cache[symbol]
        
        try:
            data = self._make_request(self.COMPANY_TICKERS_URL)
            if data:
                for entry in data.values():
                    if entry.get("ticker", "").upper() == symbol:
                        cik = str(entry.get("cik_str", "")).zfill(10)
                        self._cik_cache[symbol] = cik
                        return cik
            
            logger.warning(f"CIK not found for {symbol}")
            return None
            
        except Exception as e:
            logger.error(f"Error looking up CIK for {symbol}: {e}")
            return None
    
    # =========================================================================
    # Company Filings
    # =========================================================================
    
    def get_company_filings(self, symbol: str) -> Optional[Dict]:
        """Get all company filings metadata."""
        cik = self.get_cik(symbol)
        if not cik:
            return None
        
        url = f"{self.SUBMISSIONS_URL}/CIK{cik}.json"
        data = self._make_request(url)
        
        if data:
            return {
                "symbol": symbol,
                "cik": cik,
                "name": data.get("name"),
                "sic": data.get("sic"),
                "sic_description": data.get("sicDescription"),
                "exchanges": data.get("exchanges", []),
                "filings": data.get("filings", {}).get("recent", {})
            }
        return None
    
    def get_recent_filings(self, symbol: str, form_types: List[str] = None, 
                           limit: int = 50) -> List[Dict]:
        """
        Get recent filings for a company.
        
        Args:
            symbol: Stock ticker
            form_types: Filter by form types (e.g., ['10-K', '10-Q', '8-K'])
            limit: Maximum number of filings to return
        """
        company_data = self.get_company_filings(symbol)
        if not company_data or not company_data.get("filings"):
            return []
        
        filings = company_data["filings"]
        results = []
        
        # Parse parallel arrays
        accession_numbers = filings.get("accessionNumber", [])
        form_list = filings.get("form", [])
        filing_dates = filings.get("filingDate", [])
        primary_docs = filings.get("primaryDocument", [])
        descriptions = filings.get("primaryDocDescription", [])
        
        for i in range(min(len(accession_numbers), limit * 2)):  # Check more to account for filtering
            if len(results) >= limit:
                break
            
            form = form_list[i] if i < len(form_list) else ""
            
            # Filter by form type if specified
            if form_types and form not in form_types:
                continue
            
            accession = accession_numbers[i].replace("-", "")
            
            results.append({
                "symbol": symbol,
                "form": form,
                "filing_date": filing_dates[i] if i < len(filing_dates) else None,
                "accession_number": accession_numbers[i] if i < len(accession_numbers) else None,
                "primary_document": primary_docs[i] if i < len(primary_docs) else None,
                "description": descriptions[i] if i < len(descriptions) else None,
                "url": f"https://www.sec.gov/Archives/edgar/data/{company_data['cik'].lstrip('0')}/{accession}/{primary_docs[i]}" if i < len(primary_docs) else None
            })
        
        return results
    
    def get_10k_filings(self, symbol: str, limit: int = 5) -> List[Dict]:
        """Get recent 10-K annual report filings."""
        return self.get_recent_filings(symbol, form_types=["10-K", "10-K/A"], limit=limit)
    
    def get_10q_filings(self, symbol: str, limit: int = 10) -> List[Dict]:
        """Get recent 10-Q quarterly report filings."""
        return self.get_recent_filings(symbol, form_types=["10-Q", "10-Q/A"], limit=limit)
    
    def get_8k_filings(self, symbol: str, limit: int = 20) -> List[Dict]:
        """Get recent 8-K current report filings (material events)."""
        return self.get_recent_filings(symbol, form_types=["8-K", "8-K/A"], limit=limit)
    
    # =========================================================================
    # Insider Transactions (Form 4)
    # =========================================================================
    
    def get_insider_transactions(self, symbol: str, limit: int = 50) -> List[Dict]:
        """Get Form 4 insider transaction filings."""
        form_4_filings = self.get_recent_filings(symbol, form_types=["4"], limit=limit)
        return form_4_filings
    
    # =========================================================================
    # Institutional Holdings (13F)
    # =========================================================================
    
    def get_13f_filings(self, symbol: str, limit: int = 10) -> List[Dict]:
        """Get 13F institutional holdings filings."""
        return self.get_recent_filings(symbol, form_types=["13F-HR", "13F-HR/A"], limit=limit)
    
    # =========================================================================
    # Bulk SEC Data Downloads
    # =========================================================================
    
    def get_company_facts(self, symbol: str) -> Optional[Dict]:
        """
        Get company facts (standardized financial data from filings).
        Returns XBRL-extracted financial data.
        """
        cik = self.get_cik(symbol)
        if not cik:
            return None
        
        url = f"{self.BASE_URL}/api/xbrl/companyfacts/CIK{cik}.json"
        data = self._make_request(url)
        
        if data:
            return {
                "symbol": symbol,
                "cik": cik,
                "entity_name": data.get("entityName"),
                "facts": data.get("facts", {})
            }
        return None
    
    def get_financial_metric(self, symbol: str, metric: str, 
                             taxonomy: str = "us-gaap") -> List[Dict]:
        """
        Get specific financial metric from company facts.
        
        Args:
            symbol: Stock ticker
            metric: XBRL metric name (e.g., 'Revenues', 'NetIncomeLoss', 'Assets')
            taxonomy: Taxonomy name (usually 'us-gaap' or 'dei')
        
        Common metrics:
            - Revenues, RevenueFromContractWithCustomerExcludingAssessedTax
            - NetIncomeLoss, ProfitLoss
            - Assets, AssetsCurrent
            - Liabilities, LiabilitiesCurrent
            - StockholdersEquity
            - OperatingIncomeLoss
            - GrossProfit
            - EarningsPerShareBasic, EarningsPerShareDiluted
            - CommonStockSharesOutstanding
        """
        facts_data = self.get_company_facts(symbol)
        if not facts_data or not facts_data.get("facts"):
            return []
        
        facts = facts_data["facts"]
        
        # Try to find the metric
        if taxonomy in facts and metric in facts[taxonomy]:
            metric_data = facts[taxonomy][metric]
            units = metric_data.get("units", {})
            
            # Get the relevant unit (usually USD for monetary, shares for counts)
            results = []
            for unit_name, values in units.items():
                for item in values:
                    results.append({
                        "metric": metric,
                        "value": item.get("val"),
                        "unit": unit_name,
                        "end_date": item.get("end"),
                        "start_date": item.get("start"),
                        "fiscal_year": item.get("fy"),
                        "fiscal_period": item.get("fp"),
                        "form": item.get("form"),
                        "filed": item.get("filed"),
                        "accession": item.get("accn")
                    })
            
            # Sort by end date descending
            results.sort(key=lambda x: x.get("end_date", ""), reverse=True)
            return results
        
        return []
    
    def get_key_financials(self, symbol: str) -> Dict[str, List[Dict]]:
        """Get key financial metrics for analysis."""
        metrics = {
            "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"],
            "net_income": ["NetIncomeLoss", "ProfitLoss"],
            "gross_profit": ["GrossProfit"],
            "operating_income": ["OperatingIncomeLoss"],
            "total_assets": ["Assets"],
            "total_liabilities": ["Liabilities"],
            "stockholders_equity": ["StockholdersEquity"],
            "cash": ["CashAndCashEquivalentsAtCarryingValue", "Cash"],
            "eps_basic": ["EarningsPerShareBasic"],
            "eps_diluted": ["EarningsPerShareDiluted"],
            "shares_outstanding": ["CommonStockSharesOutstanding", "WeightedAverageNumberOfSharesOutstandingBasic"],
        }
        
        results = {}
        for key, metric_names in metrics.items():
            for metric in metric_names:
                data = self.get_financial_metric(symbol, metric)
                if data:
                    results[key] = data[:20]  # Last 20 data points
                    break
            if key not in results:
                results[key] = []
        
        return results
    
    # =========================================================================
    # Screening Integration
    # =========================================================================
    
    def get_filing_summary(self, symbol: str) -> Dict:
        """Get summary of recent filings for research screening."""
        result = {
            "symbol": symbol,
            "source": self.SOURCE_NAME,
            "fetched_at": datetime.now().isoformat()
        }
        
        # Get recent filings
        recent_10k = self.get_10k_filings(symbol, limit=1)
        recent_10q = self.get_10q_filings(symbol, limit=4)
        recent_8k = self.get_8k_filings(symbol, limit=10)
        
        result["last_10k"] = recent_10k[0] if recent_10k else None
        result["last_10q"] = recent_10q[0] if recent_10q else None
        result["recent_10q_count"] = len(recent_10q)
        result["recent_8k_count"] = len(recent_8k)
        result["recent_8k"] = recent_8k[:5]  # Last 5 8-K filings
        
        # Calculate days since last filing
        if result["last_10k"]:
            try:
                last_10k_date = datetime.strptime(result["last_10k"]["filing_date"], "%Y-%m-%d")
                result["days_since_10k"] = (datetime.now() - last_10k_date).days
            except:
                pass
        
        if result["last_10q"]:
            try:
                last_10q_date = datetime.strptime(result["last_10q"]["filing_date"], "%Y-%m-%d")
                result["days_since_10q"] = (datetime.now() - last_10q_date).days
            except:
                pass
        
        return result
