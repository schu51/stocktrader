"""
Decision Framework Data Models
==============================
Data classes for positions, decisions, signals, and portfolio state.
"""

from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Dict, List, Optional, Any
from enum import Enum
import json

from config import (
    Signal, ConvictionTier, PositionStatus, DecisionType
)

# =============================================================================
# RESEARCH DATA
# =============================================================================

@dataclass
class ResearchScore:
    """Stage 1D qualitative research score."""
    symbol: str
    score_date: date
    overall_score: float
    
    # Component scores (1-5 scale)
    product_market_fit: float = 0.0
    competitive_moat: float = 0.0
    intrinsic_value_asymmetry: float = 0.0
    management_quality: float = 0.0
    
    # Derived
    conviction_tier: ConvictionTier = ConvictionTier.LOW
    thesis: str = ""
    key_risks: List[str] = field(default_factory=list)
    catalysts: List[str] = field(default_factory=list)
    
    # Scenario analysis
    bear_case_price: Optional[float] = None
    base_case_price: Optional[float] = None
    bull_case_price: Optional[float] = None
    
    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "score_date": self.score_date.isoformat() if isinstance(self.score_date, date) else self.score_date,
            "overall_score": self.overall_score,
            "product_market_fit": self.product_market_fit,
            "competitive_moat": self.competitive_moat,
            "intrinsic_value_asymmetry": self.intrinsic_value_asymmetry,
            "management_quality": self.management_quality,
            "conviction_tier": self.conviction_tier.value,
            "thesis": self.thesis,
            "key_risks": self.key_risks,
            "catalysts": self.catalysts,
            "bear_case_price": self.bear_case_price,
            "base_case_price": self.base_case_price,
            "bull_case_price": self.bull_case_price,
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> "ResearchScore":
        data = data.copy()
        if "conviction_tier" in data:
            data["conviction_tier"] = ConvictionTier(data["conviction_tier"])
        if "score_date" in data and isinstance(data["score_date"], str):
            data["score_date"] = date.fromisoformat(data["score_date"])
        return cls(**data)

# =============================================================================
# MARKET DATA SNAPSHOT
# =============================================================================

@dataclass
class MarketSnapshot:
    """Current market data for a symbol."""
    symbol: str
    timestamp: datetime
    
    # Price data
    current_price: float
    previous_close: float
    day_change_pct: float
    
    # Range
    day_high: float
    day_low: float
    week_52_high: float
    week_52_low: float
    
    # Volume
    volume: int
    avg_volume: int
    
    # Valuation
    market_cap: float
    pe_ratio: Optional[float] = None
    ps_ratio: Optional[float] = None
    ev_ebitda: Optional[float] = None
    
    # Analyst
    analyst_target: Optional[float] = None
    analyst_upside_pct: Optional[float] = None
    
    # Technical
    sma_50: Optional[float] = None
    sma_200: Optional[float] = None
    rsi: Optional[float] = None
    
    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat(),
            "current_price": self.current_price,
            "previous_close": self.previous_close,
            "day_change_pct": self.day_change_pct,
            "day_high": self.day_high,
            "day_low": self.day_low,
            "week_52_high": self.week_52_high,
            "week_52_low": self.week_52_low,
            "volume": self.volume,
            "avg_volume": self.avg_volume,
            "market_cap": self.market_cap,
            "pe_ratio": self.pe_ratio,
            "ps_ratio": self.ps_ratio,
            "ev_ebitda": self.ev_ebitda,
            "analyst_target": self.analyst_target,
            "analyst_upside_pct": self.analyst_upside_pct,
            "sma_50": self.sma_50,
            "sma_200": self.sma_200,
            "rsi": self.rsi,
        }

# =============================================================================
# TRADING SIGNAL
# =============================================================================

@dataclass
class TradingSignal:
    """Generated trading signal with supporting data."""
    symbol: str
    signal: Signal
    timestamp: datetime
    
    # Signal strength (0-1)
    strength: float = 0.5
    confidence: float = 0.5
    
    # Component signals
    fundamental_signal: Optional[Signal] = None
    valuation_signal: Optional[Signal] = None
    momentum_signal: Optional[Signal] = None
    sentiment_signal: Optional[Signal] = None
    
    # Supporting metrics
    research_score: Optional[float] = None
    upside_pct: Optional[float] = None
    risk_reward_ratio: Optional[float] = None
    
    # Rationale
    reasons: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "signal": self.signal.value,
            "timestamp": self.timestamp.isoformat(),
            "strength": self.strength,
            "confidence": self.confidence,
            "fundamental_signal": self.fundamental_signal.value if self.fundamental_signal else None,
            "valuation_signal": self.valuation_signal.value if self.valuation_signal else None,
            "momentum_signal": self.momentum_signal.value if self.momentum_signal else None,
            "sentiment_signal": self.sentiment_signal.value if self.sentiment_signal else None,
            "research_score": self.research_score,
            "upside_pct": self.upside_pct,
            "risk_reward_ratio": self.risk_reward_ratio,
            "reasons": self.reasons,
            "warnings": self.warnings,
        }

# =============================================================================
# POSITION
# =============================================================================

@dataclass
class Position:
    """An open or historical portfolio position."""
    symbol: str
    status: PositionStatus
    
    # Position details
    shares: int = 0
    avg_cost: float = 0.0
    current_price: float = 0.0
    market_value: float = 0.0
    
    # P&L
    unrealized_pnl: float = 0.0
    unrealized_pnl_pct: float = 0.0
    realized_pnl: float = 0.0
    
    # Sizing
    target_allocation: float = 0.0    # Target % of portfolio
    current_allocation: float = 0.0   # Current % of portfolio
    max_allocation: float = 0.0       # Maximum allowed
    
    # Risk management
    stop_loss_price: Optional[float] = None
    stop_loss_pct: Optional[float] = None
    trailing_stop_price: Optional[float] = None
    target_price: Optional[float] = None
    
    # Research
    conviction_tier: ConvictionTier = ConvictionTier.MEDIUM
    research_score: float = 0.0
    thesis: str = ""
    
    # Dates
    entry_date: Optional[date] = None
    last_updated: Optional[datetime] = None
    
    # History
    transactions: List[Dict] = field(default_factory=list)
    
    def update_pnl(self, current_price: float):
        """Update P&L based on current price."""
        self.current_price = current_price
        self.market_value = self.shares * current_price
        
        if self.shares > 0 and self.avg_cost > 0:
            self.unrealized_pnl = (current_price - self.avg_cost) * self.shares
            self.unrealized_pnl_pct = (current_price / self.avg_cost - 1) * 100
    
    def check_stop_loss(self, current_price: float) -> bool:
        """Check if stop loss is triggered."""
        if self.stop_loss_price and current_price <= self.stop_loss_price:
            return True
        if self.trailing_stop_price and current_price <= self.trailing_stop_price:
            return True
        return False
    
    def check_target(self, current_price: float) -> bool:
        """Check if target price is reached."""
        if self.target_price and current_price >= self.target_price:
            return True
        return False
    
    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "status": self.status.value,
            "shares": self.shares,
            "avg_cost": self.avg_cost,
            "current_price": self.current_price,
            "market_value": self.market_value,
            "unrealized_pnl": self.unrealized_pnl,
            "unrealized_pnl_pct": self.unrealized_pnl_pct,
            "realized_pnl": self.realized_pnl,
            "target_allocation": self.target_allocation,
            "current_allocation": self.current_allocation,
            "max_allocation": self.max_allocation,
            "stop_loss_price": self.stop_loss_price,
            "stop_loss_pct": self.stop_loss_pct,
            "trailing_stop_price": self.trailing_stop_price,
            "target_price": self.target_price,
            "conviction_tier": self.conviction_tier.value,
            "research_score": self.research_score,
            "thesis": self.thesis,
            "entry_date": self.entry_date.isoformat() if self.entry_date else None,
            "last_updated": self.last_updated.isoformat() if self.last_updated else None,
            "transactions": self.transactions,
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> "Position":
        data = data.copy()
        if "status" in data:
            data["status"] = PositionStatus(data["status"])
        if "conviction_tier" in data:
            data["conviction_tier"] = ConvictionTier(data["conviction_tier"])
        if "entry_date" in data and isinstance(data["entry_date"], str):
            data["entry_date"] = date.fromisoformat(data["entry_date"])
        if "last_updated" in data and isinstance(data["last_updated"], str):
            data["last_updated"] = datetime.fromisoformat(data["last_updated"])
        return cls(**data)

# =============================================================================
# DECISION
# =============================================================================

@dataclass
class Decision:
    """A trading decision with full rationale."""
    decision_id: str
    timestamp: datetime
    symbol: str
    decision_type: DecisionType
    
    # Action details
    action: str                       # "BUY", "SELL", "HOLD"
    shares: int = 0
    limit_price: Optional[float] = None
    
    # Sizing
    position_size_pct: float = 0.0    # As % of portfolio
    position_value: float = 0.0       # Dollar value
    
    # Risk parameters
    stop_loss_price: Optional[float] = None
    target_price: Optional[float] = None
    risk_reward_ratio: Optional[float] = None
    max_loss_amount: Optional[float] = None
    
    # Supporting data
    signal: Optional[TradingSignal] = None
    research_score: Optional[ResearchScore] = None
    market_snapshot: Optional[MarketSnapshot] = None
    
    # Rationale
    primary_reason: str = ""
    supporting_reasons: List[str] = field(default_factory=list)
    risks: List[str] = field(default_factory=list)
    
    # Execution
    status: str = "PENDING"           # PENDING, CONFIRMED, EXECUTED, CANCELLED
    requires_confirmation: bool = True
    confirmed_by: Optional[str] = None
    confirmed_at: Optional[datetime] = None
    executed_at: Optional[datetime] = None
    execution_price: Optional[float] = None
    
    # Audit
    created_by: str = "decision_engine"
    notes: str = ""
    
    def to_dict(self) -> Dict:
        return {
            "decision_id": self.decision_id,
            "timestamp": self.timestamp.isoformat(),
            "symbol": self.symbol,
            "decision_type": self.decision_type.value,
            "action": self.action,
            "shares": self.shares,
            "limit_price": self.limit_price,
            "position_size_pct": self.position_size_pct,
            "position_value": self.position_value,
            "stop_loss_price": self.stop_loss_price,
            "target_price": self.target_price,
            "risk_reward_ratio": self.risk_reward_ratio,
            "max_loss_amount": self.max_loss_amount,
            "signal": self.signal.to_dict() if self.signal else None,
            "research_score": self.research_score.to_dict() if self.research_score else None,
            "market_snapshot": self.market_snapshot.to_dict() if self.market_snapshot else None,
            "primary_reason": self.primary_reason,
            "supporting_reasons": self.supporting_reasons,
            "risks": self.risks,
            "status": self.status,
            "requires_confirmation": self.requires_confirmation,
            "confirmed_by": self.confirmed_by,
            "confirmed_at": self.confirmed_at.isoformat() if self.confirmed_at else None,
            "executed_at": self.executed_at.isoformat() if self.executed_at else None,
            "execution_price": self.execution_price,
            "created_by": self.created_by,
            "notes": self.notes,
        }

# =============================================================================
# PORTFOLIO STATE
# =============================================================================

@dataclass
class PortfolioState:
    """Current state of the portfolio."""
    timestamp: datetime
    
    # Values
    total_value: float = 0.0
    cash: float = 0.0
    invested: float = 0.0
    
    # Positions
    positions: Dict[str, Position] = field(default_factory=dict)
    num_positions: int = 0
    
    # Allocations
    cash_allocation: float = 0.0
    sector_allocations: Dict[str, float] = field(default_factory=dict)
    top_5_allocation: float = 0.0
    
    # Performance
    day_pnl: float = 0.0
    day_pnl_pct: float = 0.0
    total_unrealized_pnl: float = 0.0
    total_realized_pnl: float = 0.0
    
    # Risk metrics
    portfolio_beta: float = 1.0
    max_drawdown: float = 0.0
    current_drawdown: float = 0.0
    
    # Capacity
    available_cash: float = 0.0
    buying_power: float = 0.0
    
    def get_position(self, symbol: str) -> Optional[Position]:
        """Get position by symbol."""
        return self.positions.get(symbol)
    
    def has_position(self, symbol: str) -> bool:
        """Check if symbol has an open position."""
        pos = self.positions.get(symbol)
        return pos is not None and pos.status == PositionStatus.OPEN
    
    def get_allocation(self, symbol: str) -> float:
        """Get current allocation for a symbol."""
        pos = self.positions.get(symbol)
        if pos and self.total_value > 0:
            return pos.market_value / self.total_value
        return 0.0
    
    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp.isoformat(),
            "total_value": self.total_value,
            "cash": self.cash,
            "invested": self.invested,
            "positions": {k: v.to_dict() for k, v in self.positions.items()},
            "num_positions": self.num_positions,
            "cash_allocation": self.cash_allocation,
            "sector_allocations": self.sector_allocations,
            "top_5_allocation": self.top_5_allocation,
            "day_pnl": self.day_pnl,
            "day_pnl_pct": self.day_pnl_pct,
            "total_unrealized_pnl": self.total_unrealized_pnl,
            "total_realized_pnl": self.total_realized_pnl,
            "portfolio_beta": self.portfolio_beta,
            "max_drawdown": self.max_drawdown,
            "current_drawdown": self.current_drawdown,
            "available_cash": self.available_cash,
            "buying_power": self.buying_power,
        }

# =============================================================================
# WATCHLIST ENTRY
# =============================================================================

@dataclass
class WatchlistEntry:
    """A symbol on the watchlist with entry criteria."""
    symbol: str
    added_date: date
    
    # Research
    research_score: float = 0.0
    conviction_tier: ConvictionTier = ConvictionTier.MEDIUM
    thesis: str = ""
    
    # Entry criteria
    target_entry_price: Optional[float] = None
    max_entry_price: Optional[float] = None
    entry_conditions: List[str] = field(default_factory=list)
    
    # Targets
    target_price: Optional[float] = None
    stop_loss_price: Optional[float] = None
    target_allocation: float = 0.0
    
    # Alerts
    price_alert_below: Optional[float] = None
    price_alert_above: Optional[float] = None
    
    # Status
    status: str = "WATCHING"          # WATCHING, READY, PASSED
    last_checked: Optional[datetime] = None
    notes: str = ""
    
    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "added_date": self.added_date.isoformat(),
            "research_score": self.research_score,
            "conviction_tier": self.conviction_tier.value,
            "thesis": self.thesis,
            "target_entry_price": self.target_entry_price,
            "max_entry_price": self.max_entry_price,
            "entry_conditions": self.entry_conditions,
            "target_price": self.target_price,
            "stop_loss_price": self.stop_loss_price,
            "target_allocation": self.target_allocation,
            "price_alert_below": self.price_alert_below,
            "price_alert_above": self.price_alert_above,
            "status": self.status,
            "last_checked": self.last_checked.isoformat() if self.last_checked else None,
            "notes": self.notes,
        }
