"""
Universe Screener
=================

Dynamically screens for innovative, early-stage companies with high upside potential.
Focuses on Technology, Fintech, and Consumer sectors.

Features:
- Dynamic stock discovery based on growth criteria
- Persistent tracking of top performers
- Sector filtering (Tech, Fintech, Consumer)
- Integration with data infrastructure

Usage:
    from screening.universe_screener import UniverseScreener
    
    screener = UniverseScreener()
    
    # Get today's candidates
    candidates = screener.get_screening_candidates()
    
    # Track a performer
    screener.track_performer("TOST", score=4.4, signal="BUY")
    
    # Get persistent performers
    performers = screener.get_persistent_performers()
"""

import json
import logging
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set
from dataclasses import dataclass, field, asdict
from enum import Enum

logger = logging.getLogger(__name__)


class Sector(Enum):
    TECHNOLOGY = "Technology"
    FINTECH = "Fintech"
    CONSUMER = "Consumer"
    OTHER = "Other"


@dataclass
class ScreeningCriteria:
    """Criteria for universe screening."""
    
    # Market cap bounds
    min_market_cap: float = 500_000_000      # $500M minimum
    max_market_cap: float = 50_000_000_000   # $50B maximum (early/growth stage)
    
    # Growth requirements
    min_revenue_growth: float = 0.10          # 10% YoY minimum
    
    # Valuation bounds (growth company tolerant)
    max_pe_ratio: float = 150.0               # Allow high P/E for growth
    max_ps_ratio: float = 30.0                # P/S ceiling
    
    # Quality
    min_gross_margin: float = 0.30            # 30% minimum (software-like)
    
    # Sectors to include
    sectors: List[str] = field(default_factory=lambda: [
        "Technology", "Financial Services", "Consumer Cyclical", 
        "Communication Services", "Consumer Defensive"
    ])
    
    # Exclude industries
    exclude_industries: List[str] = field(default_factory=lambda: [
        "Banks", "Insurance", "Utilities", "REITs", "Oil & Gas",
        "Biotechnology", "Drug Manufacturers", "Healthcare Plans"
    ])


@dataclass
class PerformerRecord:
    """Record of a tracked performer."""
    symbol: str
    first_seen: str
    last_seen: str
    times_seen: int = 1
    best_score: float = 0.0
    last_score: float = 0.0
    last_signal: str = "HOLD"
    sector: str = ""
    thesis: str = ""
    notes: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict) -> "PerformerRecord":
        return cls(**data)


class UniverseScreener:
    """
    Dynamic universe screener for innovative growth companies.
    
    Combines:
    1. Static curated watchlist
    2. Dynamic screening results
    3. Persistent performer tracking
    """
    
    # Curated seed list of innovative growth companies
    # These are always included in screening
    SEED_WATCHLIST = {
        # Vertical SaaS / Software
        "TOST": {"name": "Toast", "sector": "Technology", "theme": "Restaurant tech"},
        "BILL": {"name": "Bill.com", "sector": "Fintech", "theme": "SMB payments"},
        "HUBS": {"name": "HubSpot", "sector": "Technology", "theme": "SMB CRM"},
        "PCOR": {"name": "Procore", "sector": "Technology", "theme": "Construction tech"},
        "SAMSF": {"name": "Samsara", "sector": "Technology", "theme": "IoT fleet"},
        
        # Cybersecurity
        "S": {"name": "SentinelOne", "sector": "Technology", "theme": "AI security"},
        "CRWD": {"name": "CrowdStrike", "sector": "Technology", "theme": "Endpoint security"},
        "ZS": {"name": "Zscaler", "sector": "Technology", "theme": "Zero trust"},
        
        # Data / AI Infrastructure
        "SNOW": {"name": "Snowflake", "sector": "Technology", "theme": "Data cloud"},
        "MDB": {"name": "MongoDB", "sector": "Technology", "theme": "Database"},
        "DDOG": {"name": "Datadog", "sector": "Technology", "theme": "Observability"},
        "PLTR": {"name": "Palantir", "sector": "Technology", "theme": "AI analytics"},
        
        # Fintech
        "SQ": {"name": "Block", "sector": "Fintech", "theme": "Payments"},
        "AFRM": {"name": "Affirm", "sector": "Fintech", "theme": "BNPL"},
        "SOFI": {"name": "SoFi", "sector": "Fintech", "theme": "Neobank"},
        "UPST": {"name": "Upstart", "sector": "Fintech", "theme": "AI lending"},
        "HOOD": {"name": "Robinhood", "sector": "Fintech", "theme": "Retail trading"},
        
        # Consumer / E-commerce
        "SHOP": {"name": "Shopify", "sector": "Consumer", "theme": "E-commerce platform"},
        "ETSY": {"name": "Etsy", "sector": "Consumer", "theme": "Handmade marketplace"},
        "CHWY": {"name": "Chewy", "sector": "Consumer", "theme": "Pet e-commerce"},
        "DUOL": {"name": "Duolingo", "sector": "Consumer", "theme": "EdTech"},
        
        # Infrastructure / Data Center
        "VRT": {"name": "Vertiv", "sector": "Technology", "theme": "Data center infra"},
        "NET": {"name": "Cloudflare", "sector": "Technology", "theme": "Edge computing"},
        
        # Advertising / Martech  
        "TTD": {"name": "Trade Desk", "sector": "Technology", "theme": "Programmatic ads"},
        "APP": {"name": "AppLovin", "sector": "Technology", "theme": "Mobile ads"},
    }
    
    def __init__(self, 
                 data_dir: Path = None,
                 criteria: ScreeningCriteria = None,
                 data_orchestrator = None):
        """
        Initialize universe screener.
        
        Args:
            data_dir: Directory for persistent data
            criteria: Screening criteria
            data_orchestrator: Optional DataOrchestrator for live screening
        """
        self.data_dir = Path(data_dir or "./screening_data")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        self.criteria = criteria or ScreeningCriteria()
        self.orchestrator = data_orchestrator
        
        # Load persistent data
        self.performers: Dict[str, PerformerRecord] = self._load_performers()
        self.custom_watchlist: Set[str] = self._load_custom_watchlist()
        
        logger.info(f"UniverseScreener initialized with {len(self.performers)} tracked performers")
    
    def _performers_file(self) -> Path:
        return self.data_dir / "persistent_performers.json"
    
    def _watchlist_file(self) -> Path:
        return self.data_dir / "custom_watchlist.json"
    
    def _load_performers(self) -> Dict[str, PerformerRecord]:
        """Load persistent performers from disk."""
        file = self._performers_file()
        if file.exists():
            try:
                with open(file) as f:
                    data = json.load(f)
                return {k: PerformerRecord.from_dict(v) for k, v in data.items()}
            except Exception as e:
                logger.warning(f"Error loading performers: {e}")
        return {}
    
    def _save_performers(self):
        """Save performers to disk."""
        with open(self._performers_file(), "w") as f:
            data = {k: v.to_dict() for k, v in self.performers.items()}
            json.dump(data, f, indent=2)
    
    def _load_custom_watchlist(self) -> Set[str]:
        """Load custom watchlist from disk."""
        file = self._watchlist_file()
        if file.exists():
            try:
                with open(file) as f:
                    return set(json.load(f))
            except Exception as e:
                logger.warning(f"Error loading watchlist: {e}")
        return set()
    
    def _save_custom_watchlist(self):
        """Save custom watchlist to disk."""
        with open(self._watchlist_file(), "w") as f:
            json.dump(list(self.custom_watchlist), f, indent=2)
    
    # =========================================================================
    # WATCHLIST MANAGEMENT
    # =========================================================================
    
    def add_to_watchlist(self, symbol: str, reason: str = ""):
        """Add symbol to custom watchlist."""
        symbol = symbol.upper()
        self.custom_watchlist.add(symbol)
        self._save_custom_watchlist()
        logger.info(f"Added {symbol} to watchlist: {reason}")
    
    def remove_from_watchlist(self, symbol: str):
        """Remove symbol from custom watchlist."""
        symbol = symbol.upper()
        self.custom_watchlist.discard(symbol)
        self._save_custom_watchlist()
    
    def get_full_watchlist(self) -> List[str]:
        """Get complete watchlist (seed + custom + performers)."""
        all_symbols = set(self.SEED_WATCHLIST.keys())
        all_symbols.update(self.custom_watchlist)
        all_symbols.update(self.performers.keys())
        return sorted(list(all_symbols))
    
    # =========================================================================
    # PERFORMER TRACKING
    # =========================================================================
    
    def track_performer(self,
                       symbol: str,
                       score: float = 0.0,
                       signal: str = "HOLD",
                       sector: str = "",
                       thesis: str = "",
                       note: str = ""):
        """
        Track a stock as a performer (showed up as opportunity).
        
        Called after the decision engine identifies a BUY signal.
        Helps identify stocks that consistently score well.
        """
        symbol = symbol.upper()
        today = date.today().isoformat()
        
        if symbol in self.performers:
            # Update existing
            record = self.performers[symbol]
            record.last_seen = today
            record.times_seen += 1
            record.last_score = score
            record.last_signal = signal
            if score > record.best_score:
                record.best_score = score
            if note:
                record.notes.append(f"{today}: {note}")
                record.notes = record.notes[-10:]  # Keep last 10 notes
        else:
            # New performer
            record = PerformerRecord(
                symbol=symbol,
                first_seen=today,
                last_seen=today,
                times_seen=1,
                best_score=score,
                last_score=score,
                last_signal=signal,
                sector=sector or self.SEED_WATCHLIST.get(symbol, {}).get("sector", ""),
                thesis=thesis,
                notes=[f"{today}: {note}"] if note else []
            )
            self.performers[symbol] = record
        
        self._save_performers()
        logger.info(f"Tracked performer: {symbol} (times seen: {self.performers[symbol].times_seen})")
    
    def get_persistent_performers(self, min_times_seen: int = 2) -> List[Dict]:
        """
        Get performers that have shown up multiple times.
        
        These are stocks that consistently score well and deserve attention.
        """
        persistent = []
        for symbol, record in self.performers.items():
            if record.times_seen >= min_times_seen:
                persistent.append({
                    "symbol": symbol,
                    "times_seen": record.times_seen,
                    "best_score": record.best_score,
                    "last_score": record.last_score,
                    "last_signal": record.last_signal,
                    "first_seen": record.first_seen,
                    "last_seen": record.last_seen,
                    "sector": record.sector,
                    "thesis": record.thesis
                })
        
        # Sort by times seen, then by best score
        persistent.sort(key=lambda x: (x["times_seen"], x["best_score"]), reverse=True)
        return persistent
    
    def get_performer_stats(self) -> Dict:
        """Get statistics about tracked performers."""
        if not self.performers:
            return {"total": 0}
        
        scores = [p.best_score for p in self.performers.values() if p.best_score > 0]
        times = [p.times_seen for p in self.performers.values()]
        
        return {
            "total_tracked": len(self.performers),
            "persistent_count": len([p for p in self.performers.values() if p.times_seen >= 2]),
            "avg_best_score": sum(scores) / len(scores) if scores else 0,
            "max_times_seen": max(times) if times else 0,
            "by_sector": self._count_by_sector()
        }
    
    def _count_by_sector(self) -> Dict[str, int]:
        """Count performers by sector."""
        counts = {}
        for record in self.performers.values():
            sector = record.sector or "Unknown"
            counts[sector] = counts.get(sector, 0) + 1
        return counts
    
    # =========================================================================
    # SCREENING
    # =========================================================================
    
    def get_screening_candidates(self, 
                                 include_seed: bool = True,
                                 include_custom: bool = True,
                                 include_performers: bool = True,
                                 max_candidates: int = 50) -> List[Dict]:
        """
        Get list of candidates to screen today.
        
        Combines:
        1. Seed watchlist (curated innovative companies)
        2. Custom watchlist (user additions)
        3. Persistent performers (stocks that keep scoring well)
        4. Dynamic discoveries (if orchestrator available)
        
        Returns:
            List of candidate dicts with symbol, source, and metadata
        """
        candidates = {}
        
        # 1. Seed watchlist
        if include_seed:
            for symbol, info in self.SEED_WATCHLIST.items():
                candidates[symbol] = {
                    "symbol": symbol,
                    "name": info.get("name", symbol),
                    "sector": info.get("sector", ""),
                    "theme": info.get("theme", ""),
                    "source": "seed",
                    "priority": 1
                }
        
        # 2. Custom watchlist
        if include_custom:
            for symbol in self.custom_watchlist:
                if symbol not in candidates:
                    candidates[symbol] = {
                        "symbol": symbol,
                        "name": symbol,
                        "sector": "",
                        "theme": "",
                        "source": "custom",
                        "priority": 2
                    }
        
        # 3. Persistent performers
        if include_performers:
            for symbol, record in self.performers.items():
                if symbol in candidates:
                    # Already in list, boost priority if persistent
                    if record.times_seen >= 3:
                        candidates[symbol]["priority"] = 0  # Highest priority
                        candidates[symbol]["times_seen"] = record.times_seen
                        candidates[symbol]["best_score"] = record.best_score
                else:
                    candidates[symbol] = {
                        "symbol": symbol,
                        "name": symbol,
                        "sector": record.sector,
                        "theme": record.thesis[:50] if record.thesis else "",
                        "source": "performer",
                        "priority": 1 if record.times_seen >= 2 else 3,
                        "times_seen": record.times_seen,
                        "best_score": record.best_score
                    }
        
        # 4. Dynamic screening (if orchestrator available)
        if self.orchestrator:
            try:
                dynamic = self._screen_dynamic()
                for item in dynamic:
                    symbol = item["symbol"]
                    if symbol not in candidates:
                        candidates[symbol] = {
                            **item,
                            "source": "dynamic",
                            "priority": 2
                        }
            except Exception as e:
                logger.warning(f"Dynamic screening failed: {e}")
        
        # Convert to list and sort by priority
        result = list(candidates.values())
        result.sort(key=lambda x: (x.get("priority", 5), -x.get("best_score", 0)))
        
        return result[:max_candidates]
    
    def _screen_dynamic(self) -> List[Dict]:
        """
        Run dynamic screening using data infrastructure.
        
        This would query a screener API or database for stocks
        matching our criteria that aren't in the watchlist.
        """
        # This is a placeholder for dynamic screening
        # In practice, you'd use FMP screener API or similar
        
        # Example FMP screener query structure:
        # GET /api/v3/stock-screener?
        #   marketCapMoreThan=500000000&
        #   marketCapLowerThan=50000000000&
        #   sector=Technology&
        #   country=US&
        #   isActivelyTrading=true
        
        logger.info("Dynamic screening not implemented - using static watchlist")
        return []
    
    def get_candidates_by_sector(self, sector: str) -> List[Dict]:
        """Get candidates filtered by sector."""
        all_candidates = self.get_screening_candidates()
        return [c for c in all_candidates if c.get("sector", "").lower() == sector.lower()]
    
    def get_candidates_summary(self) -> Dict:
        """Get summary of screening candidates."""
        candidates = self.get_screening_candidates()
        
        by_source = {}
        by_sector = {}
        
        for c in candidates:
            source = c.get("source", "unknown")
            by_source[source] = by_source.get(source, 0) + 1
            
            sector = c.get("sector", "Unknown")
            by_sector[sector] = by_sector.get(sector, 0) + 1
        
        return {
            "total_candidates": len(candidates),
            "by_source": by_source,
            "by_sector": by_sector,
            "high_priority": len([c for c in candidates if c.get("priority", 5) <= 1])
        }


# =============================================================================
# CLI
# =============================================================================

if __name__ == "__main__":
    print("Universe Screener")
    print("=" * 50)
    
    screener = UniverseScreener()
    
    # Get candidates
    candidates = screener.get_screening_candidates()
    print(f"\nTotal Candidates: {len(candidates)}")
    
    # Show by sector
    summary = screener.get_candidates_summary()
    print(f"\nBy Sector:")
    for sector, count in summary["by_sector"].items():
        print(f"  {sector}: {count}")
    
    print(f"\nBy Source:")
    for source, count in summary["by_source"].items():
        print(f"  {source}: {count}")
    
    # Show top 10
    print(f"\nTop 10 Candidates:")
    for c in candidates[:10]:
        priority = "★" * (3 - c.get("priority", 2))
        print(f"  {priority} {c['symbol']}: {c.get('theme', c.get('sector', ''))}")
    
    # Performer stats
    stats = screener.get_performer_stats()
    print(f"\nPerformer Stats:")
    print(f"  Total Tracked: {stats['total_tracked']}")
    print(f"  Persistent (2+ times): {stats['persistent_count']}")
