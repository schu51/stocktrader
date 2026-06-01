"""
Position Sizing Calculator
==========================
Calculates optimal position sizes based on:
- Conviction tier
- Kelly criterion
- Volatility
- Portfolio constraints
"""

import math
from datetime import datetime
from typing import Dict, List, Optional, Tuple
import logging

from config import (
    ConvictionTier, PositionSizingConfig, PortfolioConstraints,
    RiskConfig, DecisionConfig, DEFAULT_CONFIG
)
from models import (
    Position, PortfolioState, ResearchScore, TradingSignal
)

logger = logging.getLogger(__name__)

class PositionSizer:
    """
    Calculates position sizes using multiple methodologies:
    
    1. Conviction-based: Fixed % based on research conviction
    2. Kelly criterion: Optimal sizing based on edge and odds
    3. Volatility-based: Size inversely proportional to volatility
    4. Risk-parity: Equal risk contribution per position
    
    Final size is the minimum of all methods, respecting constraints.
    """
    
    def __init__(self, config: DecisionConfig = None):
        self.config = config or DEFAULT_CONFIG
        self.sizing_config = self.config.position_sizing
        self.constraints = self.config.portfolio_constraints
        self.risk_config = self.config.risk_config
    
    def calculate_position_size(self,
                               symbol: str,
                               portfolio: PortfolioState,
                               conviction: ConvictionTier,
                               signal: Optional[TradingSignal] = None,
                               research: Optional[ResearchScore] = None,
                               current_price: float = 0,
                               volatility: float = None,
                               stop_loss_pct: float = None) -> Dict:
        """
        Calculate optimal position size.
        
        Args:
            symbol: Stock ticker
            portfolio: Current portfolio state
            conviction: Research conviction tier
            signal: Trading signal with strength/confidence
            research: Research score with scenario analysis
            current_price: Current stock price
            volatility: Annualized volatility (optional)
            stop_loss_pct: Stop loss percentage (optional)
        
        Returns:
            Dict with size recommendations and methodology breakdown
        """
        result = {
            "symbol": symbol,
            "timestamp": datetime.now().isoformat(),
            "conviction_tier": conviction.value,
            "methodologies": {},
            "constraints_applied": [],
            "final_recommendation": {}
        }
        
        portfolio_value = portfolio.total_value
        if portfolio_value <= 0:
            result["error"] = "Portfolio value must be positive"
            return result
        
        # 1. Conviction-based size
        conviction_size = self._conviction_based_size(conviction)
        result["methodologies"]["conviction_based"] = {
            "allocation_pct": conviction_size,
            "dollar_value": portfolio_value * conviction_size,
            "rationale": f"Based on {conviction.value} conviction tier"
        }
        
        # 2. Kelly criterion size (if we have edge estimate)
        kelly_size = None
        if signal and research:
            kelly_size = self._kelly_criterion_size(signal, research, current_price)
            if kelly_size:
                result["methodologies"]["kelly_criterion"] = {
                    "allocation_pct": kelly_size,
                    "dollar_value": portfolio_value * kelly_size,
                    "rationale": "Based on estimated edge and win probability"
                }
        
        # 3. Volatility-based size
        vol_size = None
        if volatility and volatility > 0:
            vol_size = self._volatility_based_size(volatility)
            result["methodologies"]["volatility_based"] = {
                "allocation_pct": vol_size,
                "dollar_value": portfolio_value * vol_size,
                "volatility_used": volatility,
                "rationale": f"Inverse volatility sizing (target {self.sizing_config.target_volatility*100:.0f}% portfolio vol)"
            }
        
        # 4. Risk-based size (using stop loss)
        risk_size = None
        if stop_loss_pct and stop_loss_pct > 0:
            risk_size = self._risk_based_size(stop_loss_pct)
            result["methodologies"]["risk_based"] = {
                "allocation_pct": risk_size,
                "dollar_value": portfolio_value * risk_size,
                "stop_loss_pct": stop_loss_pct,
                "rationale": f"Max {self.risk_config.max_risk_per_trade*100:.1f}% portfolio risk per trade"
            }
        
        # Determine raw size (minimum of all methods)
        sizes = [conviction_size]
        if kelly_size:
            sizes.append(kelly_size)
        if vol_size:
            sizes.append(vol_size)
        if risk_size:
            sizes.append(risk_size)
        
        raw_size = min(sizes)
        result["raw_size_pct"] = raw_size
        
        # Apply portfolio constraints
        final_size, constraints_applied = self._apply_constraints(
            raw_size, symbol, portfolio, conviction
        )
        
        result["constraints_applied"] = constraints_applied
        
        # Calculate shares and values
        shares = 0
        if current_price > 0:
            dollar_value = portfolio_value * final_size
            shares = int(dollar_value / current_price)
            actual_value = shares * current_price
            actual_pct = actual_value / portfolio_value if portfolio_value > 0 else 0
        else:
            actual_value = portfolio_value * final_size
            actual_pct = final_size
        
        result["final_recommendation"] = {
            "allocation_pct": round(final_size * 100, 2),
            "dollar_value": round(actual_value, 2),
            "shares": shares,
            "share_price": current_price,
            "actual_allocation_pct": round(actual_pct * 100, 2)
        }
        
        # Scaling recommendation
        if self.sizing_config.scale_in_tranches > 1:
            tranche_size = shares // self.sizing_config.scale_in_tranches
            result["scaling_recommendation"] = {
                "tranches": self.sizing_config.scale_in_tranches,
                "shares_per_tranche": tranche_size,
                "initial_tranche_pct": round(100 / self.sizing_config.scale_in_tranches, 1),
                "scale_in_trigger": f"{self.sizing_config.scale_in_threshold*100:.0f}% pullback"
            }
        
        return result
    
    def _conviction_based_size(self, conviction: ConvictionTier) -> float:
        """Get target position size based on conviction tier."""
        return self.sizing_config.target_position_by_conviction.get(conviction, 0.02)
    
    def _kelly_criterion_size(self,
                             signal: TradingSignal,
                             research: ResearchScore,
                             current_price: float) -> Optional[float]:
        """
        Calculate Kelly criterion position size.
        
        Kelly % = (bp - q) / b
        where:
            b = odds of winning (reward/risk ratio)
            p = probability of winning
            q = probability of losing (1 - p)
        """
        if not research.base_case_price or not research.bear_case_price:
            return None
        
        if current_price <= 0:
            return None
        
        # Estimate win probability from signal confidence
        # Adjust based on conviction tier
        base_win_prob = 0.50 + (signal.confidence * 0.25)  # 50-75% based on confidence
        
        conviction_adjustment = {
            ConvictionTier.HIGH: 0.10,
            ConvictionTier.MEDIUM: 0.05,
            ConvictionTier.LOW: 0.0,
            ConvictionTier.SPECULATIVE: -0.05
        }
        
        win_prob = base_win_prob + conviction_adjustment.get(research.conviction_tier, 0)
        win_prob = max(0.40, min(0.80, win_prob))  # Clamp between 40-80%
        
        # Calculate reward/risk ratio
        upside = research.base_case_price - current_price
        downside = current_price - research.bear_case_price
        
        if downside <= 0:
            return None
        
        reward_risk = upside / downside
        
        # Calculate Kelly percentage
        # Kelly = (b*p - q) / b where b = reward/risk, p = win prob, q = 1-p
        b = reward_risk
        p = win_prob
        q = 1 - p
        
        kelly_pct = (b * p - q) / b if b > 0 else 0
        
        # Apply Kelly fraction (use fractional Kelly for safety)
        fractional_kelly = kelly_pct * self.sizing_config.kelly_fraction
        
        # Must have minimum edge
        if kelly_pct < self.sizing_config.min_edge:
            return None
        
        return max(0, min(fractional_kelly, 0.10))  # Cap at 10%
    
    def _volatility_based_size(self, volatility: float) -> float:
        """
        Calculate position size based on volatility.
        Size inversely proportional to volatility.
        """
        if volatility <= 0:
            return 0.02  # Default to 2%
        
        # Target: each position contributes target_vol / sqrt(n_positions) to portfolio vol
        # Simplified: size = target_vol / position_vol
        
        target_vol = self.sizing_config.target_volatility
        max_pos_vol = self.sizing_config.max_position_volatility
        
        # Cap position volatility
        capped_vol = min(volatility, max_pos_vol)
        
        # Calculate size
        if capped_vol > 0:
            size = target_vol / capped_vol * 0.10  # Scale factor for reasonable sizes
        else:
            size = 0.02
        
        return max(0.01, min(size, 0.08))  # Between 1% and 8%
    
    def _risk_based_size(self, stop_loss_pct: float) -> float:
        """
        Calculate position size based on maximum risk per trade.
        
        Size = Max Risk % / Stop Loss %
        """
        if stop_loss_pct <= 0:
            return 0.02
        
        max_risk = self.risk_config.max_risk_per_trade
        size = max_risk / stop_loss_pct
        
        return max(0.01, min(size, 0.10))  # Between 1% and 10%
    
    def _apply_constraints(self,
                          raw_size: float,
                          symbol: str,
                          portfolio: PortfolioState,
                          conviction: ConvictionTier) -> Tuple[float, List[str]]:
        """Apply portfolio constraints to raw position size."""
        constraints_applied = []
        size = raw_size
        
        # 1. Maximum position by conviction
        max_by_conviction = self.sizing_config.max_position_by_conviction.get(
            conviction, 0.05
        )
        if size > max_by_conviction:
            size = max_by_conviction
            constraints_applied.append(
                f"Capped by conviction tier max: {max_by_conviction*100:.1f}%"
            )
        
        # 2. Absolute maximum single position
        if size > self.constraints.max_single_position:
            size = self.constraints.max_single_position
            constraints_applied.append(
                f"Capped by absolute max position: {self.constraints.max_single_position*100:.1f}%"
            )
        
        # 3. Check if adding would exceed position limit
        if portfolio.num_positions >= self.constraints.max_positions:
            if not portfolio.has_position(symbol):
                size = 0
                constraints_applied.append(
                    f"Max positions ({self.constraints.max_positions}) reached"
                )
        
        # 4. Check available cash
        max_from_cash = (portfolio.cash - 
                        portfolio.total_value * self.constraints.min_cash_allocation)
        max_cash_allocation = max_from_cash / portfolio.total_value if portfolio.total_value > 0 else 0
        
        if size > max_cash_allocation and max_cash_allocation > 0:
            size = max(0, max_cash_allocation)
            constraints_applied.append(
                f"Limited by available cash (maintaining {self.constraints.min_cash_allocation*100:.0f}% min cash)"
            )
        
        # 5. Check sector allocation (if sector info available)
        # This would require sector data from data infrastructure
        # Placeholder for now
        
        # 6. Check top-5 concentration
        if portfolio.positions:
            top_5_values = sorted(
                [p.market_value for p in portfolio.positions.values()],
                reverse=True
            )[:5]
            top_5_allocation = sum(top_5_values) / portfolio.total_value if portfolio.total_value > 0 else 0
            
            position_value = size * portfolio.total_value
            
            # Would this position be in top 5?
            if len(top_5_values) < 5 or position_value > min(top_5_values):
                new_top_5 = top_5_allocation + size
                if new_top_5 > self.constraints.top_5_max_allocation:
                    max_size = self.constraints.top_5_max_allocation - top_5_allocation
                    if max_size < size:
                        size = max(0, max_size)
                        constraints_applied.append(
                            f"Limited by top-5 concentration ({self.constraints.top_5_max_allocation*100:.0f}% max)"
                        )
        
        return size, constraints_applied
    
    def calculate_add_size(self,
                          position: Position,
                          portfolio: PortfolioState,
                          current_price: float) -> Dict:
        """
        Calculate size for adding to an existing position.
        
        Rules:
        - Can add if price is down from avg cost (scale-in)
        - Cannot exceed max position size
        - Must maintain portfolio constraints
        """
        result = {
            "symbol": position.symbol,
            "action": "ADD",
            "timestamp": datetime.now().isoformat()
        }
        
        # Check if price qualifies for scale-in
        if position.avg_cost > 0:
            pct_from_avg = (current_price - position.avg_cost) / position.avg_cost
            
            if pct_from_avg > -self.sizing_config.scale_in_threshold:
                result["recommendation"] = "WAIT"
                result["reason"] = f"Price only {pct_from_avg*100:.1f}% from avg cost, wait for {self.sizing_config.scale_in_threshold*100:.0f}% pullback"
                return result
        
        # Calculate room to add
        current_allocation = position.current_allocation
        max_allocation = self.sizing_config.max_position_by_conviction.get(
            position.conviction_tier, 0.05
        )
        
        room_to_add = max_allocation - current_allocation
        
        if room_to_add <= 0.005:  # Less than 0.5%
            result["recommendation"] = "FULL"
            result["reason"] = "Position already at maximum allocation"
            return result
        
        # Calculate add size (one tranche)
        tranche_size = max_allocation / self.sizing_config.scale_in_tranches
        add_size = min(room_to_add, tranche_size)
        
        # Apply constraints
        add_size, constraints = self._apply_constraints(
            add_size, position.symbol, portfolio, position.conviction_tier
        )
        
        # Calculate shares
        if current_price > 0 and portfolio.total_value > 0:
            dollar_value = portfolio.total_value * add_size
            shares = int(dollar_value / current_price)
        else:
            shares = 0
            dollar_value = 0
        
        result["recommendation"] = "ADD" if shares > 0 else "SKIP"
        result["add_allocation_pct"] = round(add_size * 100, 2)
        result["dollar_value"] = round(dollar_value, 2)
        result["shares"] = shares
        result["new_total_allocation"] = round((current_allocation + add_size) * 100, 2)
        result["constraints_applied"] = constraints
        
        return result
    
    def calculate_reduce_size(self,
                             position: Position,
                             portfolio: PortfolioState,
                             reason: str = "rebalance") -> Dict:
        """
        Calculate size for reducing a position.
        
        Reasons:
        - "take_profit": Partial profit taking
        - "rebalance": Reduce overweight position
        - "risk_reduction": Reduce risk exposure
        """
        result = {
            "symbol": position.symbol,
            "action": "REDUCE",
            "reason": reason,
            "timestamp": datetime.now().isoformat()
        }
        
        exit_rules = self.config.exit_rules
        
        if reason == "take_profit":
            # Check if profit target hit
            if position.unrealized_pnl_pct >= exit_rules.take_profit_partial * 100:
                reduce_pct = exit_rules.take_profit_partial_size
                result["reduce_shares"] = int(position.shares * reduce_pct)
                result["reduce_pct"] = reduce_pct * 100
                result["rationale"] = f"Taking {reduce_pct*100:.0f}% profit at {position.unrealized_pnl_pct:.1f}% gain"
            else:
                result["recommendation"] = "HOLD"
                result["reason"] = f"Profit {position.unrealized_pnl_pct:.1f}% below {exit_rules.take_profit_partial*100:.0f}% threshold"
                return result
        
        elif reason == "rebalance":
            # Reduce to target allocation
            target = position.target_allocation
            current = position.current_allocation
            
            if current > target * 1.2:  # More than 20% above target
                reduce_to = target
                reduce_pct = (current - target) / current if current > 0 else 0
                result["reduce_shares"] = int(position.shares * reduce_pct)
                result["reduce_pct"] = reduce_pct * 100
                result["new_allocation"] = target * 100
                result["rationale"] = f"Rebalancing from {current*100:.1f}% to {target*100:.1f}%"
            else:
                result["recommendation"] = "HOLD"
                result["reason"] = "Position within rebalancing threshold"
                return result
        
        elif reason == "risk_reduction":
            # Reduce by 25% for risk management
            reduce_pct = 0.25
            result["reduce_shares"] = int(position.shares * reduce_pct)
            result["reduce_pct"] = reduce_pct * 100
            result["rationale"] = "Risk reduction: removing 25% of position"
        
        result["recommendation"] = "REDUCE"
        return result
    
    def get_portfolio_sizing_summary(self, portfolio: PortfolioState) -> Dict:
        """Get summary of portfolio sizing and capacity."""
        summary = {
            "timestamp": datetime.now().isoformat(),
            "total_value": portfolio.total_value,
            "cash": portfolio.cash,
            "invested": portfolio.invested,
            "num_positions": portfolio.num_positions,
            "max_positions": self.constraints.max_positions,
            "positions_available": self.constraints.max_positions - portfolio.num_positions,
            "cash_allocation": portfolio.cash_allocation,
            "min_cash_required": self.constraints.min_cash_allocation,
            "available_for_new_positions": 0,
            "position_sizes_by_conviction": {}
        }
        
        # Calculate available capital for new positions
        min_cash = portfolio.total_value * self.constraints.min_cash_allocation
        available = max(0, portfolio.cash - min_cash)
        summary["available_for_new_positions"] = available
        
        # Show target sizes by conviction
        for tier in ConvictionTier:
            target = self.sizing_config.target_position_by_conviction.get(tier, 0)
            max_size = self.sizing_config.max_position_by_conviction.get(tier, 0)
            summary["position_sizes_by_conviction"][tier.value] = {
                "target_pct": target * 100,
                "max_pct": max_size * 100,
                "target_value": portfolio.total_value * target,
                "max_value": portfolio.total_value * max_size
            }
        
        return summary
