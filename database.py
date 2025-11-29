"""
Database Storage Layer
======================
SQLite-based persistence for market data, fundamentals, and research.
"""

import sqlite3
import json
from datetime import datetime, date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from contextlib import contextmanager
import threading

class Database:
    """SQLite database for trading agent data persistence."""
    
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._init_schema()
    
    @contextmanager
    def _get_connection(self):
        """Get thread-local database connection."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._local.conn.row_factory = sqlite3.Row
        try:
            yield self._local.conn
        except Exception:
            self._local.conn.rollback()
            raise
    
    def _init_schema(self):
        """Initialize database schema."""
        schema = """
        -- Price data (daily OHLCV)
        CREATE TABLE IF NOT EXISTS daily_prices (
            symbol TEXT NOT NULL,
            date DATE NOT NULL,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            adj_close REAL,
            volume INTEGER,
            source TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (symbol, date)
        );
        CREATE INDEX IF NOT EXISTS idx_prices_symbol ON daily_prices(symbol);
        CREATE INDEX IF NOT EXISTS idx_prices_date ON daily_prices(date);
        
        -- Company fundamentals (quarterly/annual)
        CREATE TABLE IF NOT EXISTS fundamentals (
            symbol TEXT NOT NULL,
            period_type TEXT NOT NULL,  -- 'quarterly' or 'annual'
            period_end DATE NOT NULL,
            fiscal_year INTEGER,
            fiscal_quarter INTEGER,
            data JSON NOT NULL,  -- Flexible JSON storage for all metrics
            source TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (symbol, period_type, period_end)
        );
        CREATE INDEX IF NOT EXISTS idx_fundamentals_symbol ON fundamentals(symbol);
        
        -- Company profile/overview
        CREATE TABLE IF NOT EXISTS company_profiles (
            symbol TEXT PRIMARY KEY,
            name TEXT,
            sector TEXT,
            industry TEXT,
            description TEXT,
            employees INTEGER,
            market_cap REAL,
            exchange TEXT,
            country TEXT,
            website TEXT,
            data JSON,  -- Additional profile data
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        
        -- Valuation metrics (current/historical)
        CREATE TABLE IF NOT EXISTS valuation_metrics (
            symbol TEXT NOT NULL,
            date DATE NOT NULL,
            pe_ratio REAL,
            forward_pe REAL,
            ps_ratio REAL,
            pb_ratio REAL,
            ev_ebitda REAL,
            ev_revenue REAL,
            peg_ratio REAL,
            dividend_yield REAL,
            data JSON,  -- Additional metrics
            source TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (symbol, date)
        );
        CREATE INDEX IF NOT EXISTS idx_valuation_symbol ON valuation_metrics(symbol);
        
        -- Earnings calendar and estimates
        CREATE TABLE IF NOT EXISTS earnings_calendar (
            symbol TEXT NOT NULL,
            report_date DATE NOT NULL,
            fiscal_quarter TEXT,
            eps_estimate REAL,
            eps_actual REAL,
            revenue_estimate REAL,
            revenue_actual REAL,
            surprise_pct REAL,
            time_of_day TEXT,  -- 'BMO', 'AMC', etc.
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (symbol, report_date)
        );
        CREATE INDEX IF NOT EXISTS idx_earnings_date ON earnings_calendar(report_date);
        
        -- News and sentiment
        CREATE TABLE IF NOT EXISTS news (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,
            headline TEXT NOT NULL,
            summary TEXT,
            source TEXT,
            url TEXT,
            published_at TIMESTAMP,
            sentiment_score REAL,
            sentiment_label TEXT,
            data JSON,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_news_symbol ON news(symbol);
        CREATE INDEX IF NOT EXISTS idx_news_published ON news(published_at);
        
        -- Insider transactions
        CREATE TABLE IF NOT EXISTS insider_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            insider_name TEXT,
            insider_title TEXT,
            transaction_type TEXT,  -- 'BUY', 'SELL', 'OPTION'
            shares INTEGER,
            price REAL,
            value REAL,
            transaction_date DATE,
            filing_date DATE,
            source TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_insider_symbol ON insider_transactions(symbol);
        CREATE INDEX IF NOT EXISTS idx_insider_date ON insider_transactions(transaction_date);
        
        -- Institutional holdings
        CREATE TABLE IF NOT EXISTS institutional_holdings (
            symbol TEXT NOT NULL,
            holder_name TEXT NOT NULL,
            shares INTEGER,
            value REAL,
            pct_held REAL,
            change_shares INTEGER,
            change_pct REAL,
            report_date DATE,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (symbol, holder_name, report_date)
        );
        
        -- Research scores and analysis
        CREATE TABLE IF NOT EXISTS research_scores (
            symbol TEXT NOT NULL,
            score_date DATE NOT NULL,
            overall_score REAL,
            pmf_score REAL,  -- Product-Market Fit
            moat_score REAL,  -- Competitive Moat
            iva_score REAL,  -- Intrinsic Value Asymmetry
            mgmt_score REAL,  -- Management Quality
            conviction_tier TEXT,  -- 'HIGH', 'MEDIUM', 'LOW'
            notes TEXT,
            data JSON,  -- Full scoring breakdown
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (symbol, score_date)
        );
        
        -- Watchlist and positions
        CREATE TABLE IF NOT EXISTS watchlist (
            symbol TEXT PRIMARY KEY,
            added_date DATE,
            target_price REAL,
            stop_loss REAL,
            thesis TEXT,
            status TEXT,  -- 'WATCHING', 'ACTIVE', 'CLOSED'
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        
        -- Data fetch log (for tracking updates)
        CREATE TABLE IF NOT EXISTS fetch_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            endpoint TEXT,
            symbol TEXT,
            status TEXT,  -- 'SUCCESS', 'ERROR', 'RATE_LIMITED'
            records_fetched INTEGER,
            error_message TEXT,
            fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_fetch_log_source ON fetch_log(source);
        CREATE INDEX IF NOT EXISTS idx_fetch_log_time ON fetch_log(fetched_at);
        """
        
        with self._get_connection() as conn:
            conn.executescript(schema)
            conn.commit()
    
    # =========================================================================
    # Price Data Methods
    # =========================================================================
    
    def upsert_daily_prices(self, symbol: str, prices: List[Dict], source: str = "unknown"):
        """Insert or update daily price data."""
        sql = """
        INSERT OR REPLACE INTO daily_prices 
        (symbol, date, open, high, low, close, adj_close, volume, source, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """
        with self._get_connection() as conn:
            data = [
                (symbol, p["date"], p.get("open"), p.get("high"), p.get("low"),
                 p.get("close"), p.get("adj_close"), p.get("volume"), source)
                for p in prices
            ]
            conn.executemany(sql, data)
            conn.commit()
    
    def get_daily_prices(self, symbol: str, start_date: Optional[str] = None, 
                         end_date: Optional[str] = None) -> List[Dict]:
        """Get daily prices for a symbol."""
        sql = "SELECT * FROM daily_prices WHERE symbol = ?"
        params = [symbol]
        
        if start_date:
            sql += " AND date >= ?"
            params.append(start_date)
        if end_date:
            sql += " AND date <= ?"
            params.append(end_date)
        
        sql += " ORDER BY date"
        
        with self._get_connection() as conn:
            cursor = conn.execute(sql, params)
            return [dict(row) for row in cursor.fetchall()]
    
    # =========================================================================
    # Fundamentals Methods
    # =========================================================================
    
    def upsert_fundamentals(self, symbol: str, period_type: str, period_end: str,
                            data: Dict, fiscal_year: int = None, 
                            fiscal_quarter: int = None, source: str = "unknown"):
        """Insert or update fundamental data."""
        sql = """
        INSERT OR REPLACE INTO fundamentals
        (symbol, period_type, period_end, fiscal_year, fiscal_quarter, data, source, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """
        with self._get_connection() as conn:
            conn.execute(sql, (symbol, period_type, period_end, fiscal_year, 
                              fiscal_quarter, json.dumps(data), source))
            conn.commit()
    
    def get_fundamentals(self, symbol: str, period_type: str = "quarterly",
                         limit: int = 8) -> List[Dict]:
        """Get fundamental data for a symbol."""
        sql = """
        SELECT * FROM fundamentals 
        WHERE symbol = ? AND period_type = ?
        ORDER BY period_end DESC
        LIMIT ?
        """
        with self._get_connection() as conn:
            cursor = conn.execute(sql, (symbol, period_type, limit))
            results = []
            for row in cursor.fetchall():
                r = dict(row)
                r["data"] = json.loads(r["data"])
                results.append(r)
            return results
    
    # =========================================================================
    # Company Profile Methods
    # =========================================================================
    
    def upsert_company_profile(self, symbol: str, profile: Dict):
        """Insert or update company profile."""
        sql = """
        INSERT OR REPLACE INTO company_profiles
        (symbol, name, sector, industry, description, employees, market_cap,
         exchange, country, website, data, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """
        with self._get_connection() as conn:
            conn.execute(sql, (
                symbol, profile.get("name"), profile.get("sector"),
                profile.get("industry"), profile.get("description"),
                profile.get("employees"), profile.get("market_cap"),
                profile.get("exchange"), profile.get("country"),
                profile.get("website"), json.dumps(profile)
            ))
            conn.commit()
    
    def get_company_profile(self, symbol: str) -> Optional[Dict]:
        """Get company profile."""
        sql = "SELECT * FROM company_profiles WHERE symbol = ?"
        with self._get_connection() as conn:
            cursor = conn.execute(sql, (symbol,))
            row = cursor.fetchone()
            if row:
                result = dict(row)
                if result.get("data"):
                    result["data"] = json.loads(result["data"])
                return result
            return None
    
    # =========================================================================
    # Research Methods
    # =========================================================================
    
    def upsert_research_score(self, symbol: str, score_date: str, scores: Dict):
        """Insert or update research scores."""
        sql = """
        INSERT OR REPLACE INTO research_scores
        (symbol, score_date, overall_score, pmf_score, moat_score, iva_score,
         mgmt_score, conviction_tier, notes, data, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """
        with self._get_connection() as conn:
            conn.execute(sql, (
                symbol, score_date, scores.get("overall_score"),
                scores.get("pmf_score"), scores.get("moat_score"),
                scores.get("iva_score"), scores.get("mgmt_score"),
                scores.get("conviction_tier"), scores.get("notes"),
                json.dumps(scores)
            ))
            conn.commit()
    
    def get_research_scores(self, symbol: str = None, 
                            min_score: float = None) -> List[Dict]:
        """Get research scores, optionally filtered."""
        sql = "SELECT * FROM research_scores WHERE 1=1"
        params = []
        
        if symbol:
            sql += " AND symbol = ?"
            params.append(symbol)
        if min_score:
            sql += " AND overall_score >= ?"
            params.append(min_score)
        
        sql += " ORDER BY score_date DESC, overall_score DESC"
        
        with self._get_connection() as conn:
            cursor = conn.execute(sql, params)
            results = []
            for row in cursor.fetchall():
                r = dict(row)
                if r.get("data"):
                    r["data"] = json.loads(r["data"])
                results.append(r)
            return results
    
    # =========================================================================
    # Logging Methods
    # =========================================================================
    
    def log_fetch(self, source: str, endpoint: str = None, symbol: str = None,
                  status: str = "SUCCESS", records: int = 0, error: str = None):
        """Log a data fetch operation."""
        sql = """
        INSERT INTO fetch_log (source, endpoint, symbol, status, records_fetched, error_message)
        VALUES (?, ?, ?, ?, ?, ?)
        """
        with self._get_connection() as conn:
            conn.execute(sql, (source, endpoint, symbol, status, records, error))
            conn.commit()
    
    def get_fetch_stats(self, source: str = None, hours: int = 24) -> Dict:
        """Get fetch statistics."""
        sql = """
        SELECT source, status, COUNT(*) as count, SUM(records_fetched) as total_records
        FROM fetch_log
        WHERE fetched_at > datetime('now', ?)
        """
        params = [f"-{hours} hours"]
        
        if source:
            sql += " AND source = ?"
            params.append(source)
        
        sql += " GROUP BY source, status"
        
        with self._get_connection() as conn:
            cursor = conn.execute(sql, params)
            return [dict(row) for row in cursor.fetchall()]
    
    # =========================================================================
    # Utility Methods
    # =========================================================================
    
    def execute_query(self, sql: str, params: tuple = ()) -> List[Dict]:
        """Execute arbitrary SELECT query."""
        with self._get_connection() as conn:
            cursor = conn.execute(sql, params)
            return [dict(row) for row in cursor.fetchall()]
    
    def get_table_counts(self) -> Dict[str, int]:
        """Get row counts for all tables."""
        tables = [
            "daily_prices", "fundamentals", "company_profiles", 
            "valuation_metrics", "earnings_calendar", "news",
            "insider_transactions", "institutional_holdings",
            "research_scores", "watchlist", "fetch_log"
        ]
        counts = {}
        with self._get_connection() as conn:
            for table in tables:
                cursor = conn.execute(f"SELECT COUNT(*) as cnt FROM {table}")
                counts[table] = cursor.fetchone()["cnt"]
        return counts
