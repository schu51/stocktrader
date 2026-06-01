"""
Universe Screener
=================

Dynamically screens for stocks with momentum and growth characteristics.
Expanded universe covers 10 sectors for broader momentum opportunity.

Features:
- Dynamic stock discovery based on momentum criteria
- Persistent tracking of top performers
- Sector filtering across tech, fintech, healthcare, industrials, and more
- Integration with data infrastructure

Usage:
    from screening.universe_screener import UniverseScreener

    screener = UniverseScreener()

    # Get today's candidates
    candidates = screener.get_screening_candidates()

    # Track a performer
    screener.track_performer("NVDA", score=4.4, signal="BUY")

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
    HEALTHCARE = "Healthcare"
    INDUSTRIALS = "Industrials"
    OTHER = "Other"


@dataclass
class ScreeningCriteria:
    """Criteria for universe screening (momentum-friendly)."""

    # Market cap: liquid enough to trade cleanly
    min_market_cap: float = 2_000_000_000      # $2B minimum
    max_market_cap: float = 5_000_000_000_000  # No real ceiling

    # Growth: momentum stocks grow fast
    min_revenue_growth: float = 0.10            # 10% YoY minimum

    # Valuation: wide tolerance - momentum stocks are expensive by design
    max_pe_ratio: float = 500.0                 # Very permissive
    max_ps_ratio: float = 50.0                  # Allow high P/S for hypergrowth

    # Quality floor
    min_gross_margin: float = 0.25              # 25% minimum

    # Liquidity: must be able to enter/exit without slippage
    min_avg_volume: int = 1_000_000             # 1M daily volume minimum

    # Sectors to include (broad for momentum scanning)
    sectors: List[str] = field(default_factory=lambda: [
        "Technology", "Financial Services", "Consumer Cyclical",
        "Communication Services", "Consumer Defensive",
        "Healthcare", "Industrials", "Energy"
    ])

    # Exclude low-momentum / non-tradeable industries
    exclude_industries: List[str] = field(default_factory=lambda: [
        "Banks", "Insurance", "Utilities", "REITs", "Oil & Gas Exploration",
        "Drug Manufacturers - Major", "Healthcare Plans"
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
    Dynamic universe screener for momentum growth companies.

    Combines:
    1. Static curated watchlist (55 names across 10 sectors)
    2. Dynamic screening results
    3. Persistent performer tracking
    """

    # Expanded seed watchlist for momentum strategy.
    # Covers mega-cap tech, semiconductors, cybersecurity, SaaS, AI infra,
    # fintech, consumer, healthcare tech, industrials, and emerging themes.
    SEED_WATCHLIST = {
        # Mega-cap tech / AI
        "NVDA":  {"name": "NVIDIA",              "sector": "Technology",   "theme": "AI chips & infrastructure"},
        "META":  {"name": "Meta",                 "sector": "Technology",   "theme": "Social media + AI"},
        "GOOGL": {"name": "Alphabet",             "sector": "Technology",   "theme": "Search + cloud + AI"},
        "MSFT":  {"name": "Microsoft",            "sector": "Technology",   "theme": "Enterprise cloud + Copilot"},
        "AMZN":  {"name": "Amazon",               "sector": "Technology",   "theme": "AWS + e-commerce"},
        "TSLA":  {"name": "Tesla",                "sector": "Technology",   "theme": "EV + autonomy + energy"},

        # Semiconductors
        "AMD":   {"name": "AMD",                  "sector": "Technology",   "theme": "CPU/GPU for AI workloads"},
        "AVGO":  {"name": "Broadcom",             "sector": "Technology",   "theme": "AI networking chips"},
        "MRVL":  {"name": "Marvell",              "sector": "Technology",   "theme": "Custom AI silicon"},
        "SMCI":  {"name": "Super Micro",          "sector": "Technology",   "theme": "AI server systems"},
        "ARM":   {"name": "Arm Holdings",         "sector": "Technology",   "theme": "CPU architecture licensing"},
        "AMAT":  {"name": "Applied Materials",    "sector": "Technology",   "theme": "Semiconductor equipment"},
        "KLAC":  {"name": "KLA Corp",             "sector": "Technology",   "theme": "Semiconductor inspection"},

        # Cybersecurity
        "CRWD":  {"name": "CrowdStrike",          "sector": "Technology",   "theme": "Endpoint security"},
        "S":     {"name": "SentinelOne",          "sector": "Technology",   "theme": "AI security platform"},
        "ZS":    {"name": "Zscaler",              "sector": "Technology",   "theme": "Zero trust network"},
        "PANW":  {"name": "Palo Alto Networks",   "sector": "Technology",   "theme": "Platform security"},
        "OKTA":  {"name": "Okta",                 "sector": "Technology",   "theme": "Identity management"},
        "AXON":  {"name": "Axon Enterprise",      "sector": "Industrials",  "theme": "Public safety tech"},

        # Enterprise AI / Cloud software
        "DDOG":  {"name": "Datadog",              "sector": "Technology",   "theme": "Observability platform"},
        "SNOW":  {"name": "Snowflake",            "sector": "Technology",   "theme": "Data cloud"},
        "MDB":   {"name": "MongoDB",              "sector": "Technology",   "theme": "Developer data platform"},
        "NET":   {"name": "Cloudflare",           "sector": "Technology",   "theme": "Edge cloud"},
        "NOW":   {"name": "ServiceNow",           "sector": "Technology",   "theme": "Enterprise AI workflows"},
        "PLTR":  {"name": "Palantir",             "sector": "Technology",   "theme": "AI analytics / defense"},
        "CRM":   {"name": "Salesforce",           "sector": "Technology",   "theme": "CRM + AI agents"},
        "WDAY":  {"name": "Workday",              "sector": "Technology",   "theme": "HR + finance cloud"},

        # Vertical SaaS
        "TOST":  {"name": "Toast",                "sector": "Technology",   "theme": "Restaurant tech"},
        "PCOR":  {"name": "Procore",              "sector": "Technology",   "theme": "Construction tech"},
        "VEEV":  {"name": "Veeva",                "sector": "Healthcare",   "theme": "Life sciences cloud"},
        "SAMSF": {"name": "Samsara",              "sector": "Technology",   "theme": "IoT fleet management"},

        # AI infrastructure / Data center / Power
        "VRT":   {"name": "Vertiv",               "sector": "Industrials",  "theme": "Data center infrastructure"},
        "ETN":   {"name": "Eaton",                "sector": "Industrials",  "theme": "Power management"},
        "PWR":   {"name": "Quanta Services",      "sector": "Industrials",  "theme": "Grid & data center build-out"},
        "ANET":  {"name": "Arista Networks",      "sector": "Technology",   "theme": "Data center networking"},
        "CEG":   {"name": "Constellation Energy", "sector": "Industrials",  "theme": "Nuclear power for AI"},
        "VST":   {"name": "Vistra",               "sector": "Industrials",  "theme": "Power generation"},

        # Fintech / Payments
        "SQ":    {"name": "Block",                "sector": "Fintech",      "theme": "Payments ecosystem"},
        "AFRM":  {"name": "Affirm",               "sector": "Fintech",      "theme": "Buy now pay later"},
        "SOFI":  {"name": "SoFi",                 "sector": "Fintech",      "theme": "Digital banking"},
        "UPST":  {"name": "Upstart",              "sector": "Fintech",      "theme": "AI lending"},
        "HOOD":  {"name": "Robinhood",            "sector": "Fintech",      "theme": "Retail investing"},
        "NU":    {"name": "Nu Holdings",          "sector": "Fintech",      "theme": "LatAm digital bank"},
        "COIN":  {"name": "Coinbase",             "sector": "Fintech",      "theme": "Crypto exchange"},
        "V":     {"name": "Visa",                 "sector": "Fintech",      "theme": "Global payments network"},
        "MA":    {"name": "Mastercard",           "sector": "Fintech",      "theme": "Global payments network"},

        # Consumer / Digital media
        "SHOP":  {"name": "Shopify",              "sector": "Consumer",     "theme": "E-commerce platform"},
        "DUOL":  {"name": "Duolingo",             "sector": "Consumer",     "theme": "EdTech / AI tutoring"},
        "APP":   {"name": "AppLovin",             "sector": "Technology",   "theme": "Mobile advertising AI"},
        "TTD":   {"name": "Trade Desk",           "sector": "Technology",   "theme": "Programmatic advertising"},
        "RBLX":  {"name": "Roblox",               "sector": "Consumer",     "theme": "Gaming metaverse"},
        "SPOT":  {"name": "Spotify",              "sector": "Consumer",     "theme": "Audio streaming"},
        "RDDT":  {"name": "Reddit",               "sector": "Technology",   "theme": "Community platform + AI data"},

        # Healthcare tech
        "ISRG":  {"name": "Intuitive Surgical",   "sector": "Healthcare",   "theme": "Robotic surgery"},
        "DXCM":  {"name": "Dexcom",               "sector": "Healthcare",   "theme": "Continuous glucose monitoring"},
        "PODD":  {"name": "Insulet",              "sector": "Healthcare",   "theme": "Insulin delivery systems"},
        "RXRX":  {"name": "Recursion",            "sector": "Healthcare",   "theme": "AI drug discovery"},

        # Industrials / Defense
        "GE":    {"name": "GE Aerospace",         "sector": "Industrials",  "theme": "Jet engines"},
        "HWM":   {"name": "Howmet Aerospace",     "sector": "Industrials",  "theme": "Aerospace components"},
        "KTOS":  {"name": "Kratos Defense",       "sector": "Industrials",  "theme": "Drone warfare systems"},

        # Emerging / High-momentum themes
        "MSTR":  {"name": "MicroStrategy",        "sector": "Fintech",      "theme": "Bitcoin treasury play"},
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

        self.performers: Dict[str, PerformerRecord] = self._load_performers()
        self.custom_watchlist: Set[str] = self._load_custom_watchlist()

        logger.info(f"UniverseScreener initialized with {len(self.performers)} tracked performers")

    def _performers_file(self) -> Path:
        return self.data_dir / "persistent_performers.json"

    def _watchlist_file(self) -> Path:
        return self.data_dir / "custom_watchlist.json"

    def _load_performers(self) -> Dict[str, PerformerRecord]:
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
        with open(self._performers_file(), "w") as f:
            data = {k: v.to_dict() for k, v in self.performers.items()}
            json.dump(data, f, indent=2)

    def _load_custom_watchlist(self) -> Set[str]:
        file = self._watchlist_file()
        if file.exists():
            try:
                with open(file) as f:
                    return set(json.load(f))
            except Exception as e:
                logger.warning(f"Error loading watchlist: {e}")
        return set()

    def _save_custom_watchlist(self):
        with open(self._watchlist_file(), "w") as f:
            json.dump(list(self.custom_watchlist), f, indent=2)

    # =========================================================================
    # WATCHLIST MANAGEMENT
    # =========================================================================

    def add_to_watchlist(self, symbol: str, reason: str = ""):
        symbol = symbol.upper()
        self.custom_watchlist.add(symbol)
        self._save_custom_watchlist()
        logger.info(f"Added {symbol} to watchlist: {reason}")

    def remove_from_watchlist(self, symbol: str):
        symbol = symbol.upper()
        self.custom_watchlist.discard(symbol)
        self._save_custom_watchlist()

    def get_full_watchlist(self) -> List[str]:
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
        Track a stock as a performer (showed up as BUY opportunity).
        Stocks that appear consistently deserve priority attention.
        """
        symbol = symbol.upper()
        today = date.today().isoformat()

        if symbol in self.performers:
            record = self.performers[symbol]
            record.last_seen = today
            record.times_seen += 1
            record.last_score = score
            record.last_signal = signal
            if score > record.best_score:
                record.best_score = score
            if note:
                record.notes.append(f"{today}: {note}")
                record.notes = record.notes[-10:]
        else:
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
        """Get performers that have shown up multiple times."""
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

        persistent.sort(key=lambda x: (x["times_seen"], x["best_score"]), reverse=True)
        return persistent

    def get_performer_stats(self) -> Dict:
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
                                 max_candidates: int = 75) -> List[Dict]:
        """
        Get list of candidates to screen today.

        Priority order:
        1. Persistent performers (stocks that repeatedly generate BUY signals)
        2. Seed watchlist (55 curated names across 10 sectors)
        3. Custom watchlist additions
        4. Dynamic discoveries (if orchestrator available)
        """
        candidates = {}

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

        if include_performers:
            for symbol, record in self.performers.items():
                if symbol in candidates:
                    if record.times_seen >= 3:
                        candidates[symbol]["priority"] = 0
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

        if self.orchestrator:
            try:
                dynamic = self._screen_dynamic()
                for item in dynamic:
                    symbol = item["symbol"]
                    if symbol not in candidates:
                        candidates[symbol] = {**item, "source": "dynamic", "priority": 2}
            except Exception as e:
                logger.warning(f"Dynamic screening failed: {e}")

        result = list(candidates.values())
        result.sort(key=lambda x: (x.get("priority", 5), -x.get("best_score", 0)))

        return result[:max_candidates]

    def _screen_dynamic(self) -> List[Dict]:
        """Dynamic screening via data infrastructure (placeholder for FMP screener API)."""
        logger.info("Dynamic screening not implemented - using static watchlist")
        return []

    def get_candidates_by_sector(self, sector: str) -> List[Dict]:
        all_candidates = self.get_screening_candidates()
        return [c for c in all_candidates if c.get("sector", "").lower() == sector.lower()]

    def get_candidates_summary(self) -> Dict:
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

    candidates = screener.get_screening_candidates()
    print(f"\nTotal Candidates: {len(candidates)}")

    summary = screener.get_candidates_summary()
    print(f"\nBy Sector:")
    for sector, count in sorted(summary["by_sector"].items(), key=lambda x: -x[1]):
        print(f"  {sector}: {count}")

    print(f"\nBy Source:")
    for source, count in summary["by_source"].items():
        print(f"  {source}: {count}")

    print(f"\nTop 15 Candidates:")
    for c in candidates[:15]:
        priority = "★" * max(0, 3 - c.get("priority", 2))
        print(f"  {priority} {c['symbol']}: {c.get('theme', c.get('sector', ''))}")

    stats = screener.get_performer_stats()
    print(f"\nPerformer Stats:")
    print(f"  Total Tracked: {stats.get('total_tracked', 0)}")
    print(f"  Persistent (2+ times): {stats.get('persistent_count', 0)}")
