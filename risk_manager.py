"""
Risk Manager
============
Manages portfolio and position-level risk controls.
"""

from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Tuple
import logging

from .config import (
    ConvictionTier, RiskConfig, ExitRules, PortfolioConstraints,
    DecisionConfig, DEFAULT_CONFIG, DecisionType
)
from .models import Position, PortfolioState, Decision, PositionStatus

logger = logging.getLogger(__name__)

class RiskManager:
    """
    Manages risk at portfolio and position levels.
    
    Responsibilities:
    1. Stop-loss management
    2. Portfolio drawdown monitoring
    3. Position concentration limits
    4. Volatility-based risk adjustments
    5. Pre-trade risk checks
    """
    
    def __init__(self, config: DecisionConfig = None):
        self.config = config or DEFAULT_CONFIG
        self.risk_config = self.config.risk_config
        self.exit_rules = self.config.exit_rules
        self.constraints = self.config.portfolio_constraints
        
        # Track portfolio high watermark for drawdown
        self.high_watermark: float = 0
        self.peak_date: Optional[date] = None
        
        # Daily/weekly tracking
        self.daily_start_value: float = 0
        self.weekly_start_value: float = 0
        self.last_reset_date: Optional[date] = None
    
    # =========================================================================
    # STOP-LOSS MANAGEMENT
    # =========================================================================
    
    def calculate_stop_loss(self,
                           entry_price: float,
                           conviction: ConvictionTier,
                           use_atr: bool = False,
                           atr_value: float = None) -> Dict:
        """
        Calculate stop-loss price for a new position.
        
        Args:
            entry_price: Entry price
            conviction: Conviction tier (determines stop distance)
            use_atr: Whether to use ATR-based stop
            atr_value: Average True Range if using ATR
        
        Returns:
            Dict with stop-loss price and details
        """
        # Get base stop percentage by conviction
        stop_pct = self.exit_rules.stop_loss_by_conviction.get(conviction, 0.15)
        
        result = {
            "entry_price": entry_price,
            "conviction_tier": conviction.value,
            "method": "percentage",
            "stop_percentage": stop_pct,
            "stop_price": round(entry_price * (1 - stop_pct), 2),
            "max_loss_per_share": round(entry_price * stop_pct, 2)
        }
        
        # ATR-based stop (if provided)
        if use_atr and atr_value and atr_value > 0:
            atr_multiplier = 2.0  # 2 ATR is common
            atr_stop_price = entry_price - (atr_value * atr_multiplier)
            atr_stop_pct = (entry_price - atr_stop_price) / entry_price
            
            result["atr_method"] = {
                "atr_value": atr_value,
                "multiplier": atr_multiplier,
                "stop_price": round(atr_stop_price, 2),
                "stop_percentage": round(atr_stop_pct, 4)
            }
            
            # Use wider of the two stops
            if atr_stop_price < result["stop_price"]:
                result["recommended_stop"] = atr_stop_price
                result["method"] = "atr"
            else:
                result["recommended_stop"] = result["stop_price"]
        else:
            result["recommended_stop"] = result["stop_price"]
        
        return result
    
    def update_trailing_stop(self,
                            position: Position,
                            current_price: float) -> Optional[float]:
        """
        Update trailing stop for a position.
        
        Trailing stop activates after position gains reach activation threshold,
        then trails the high by the configured distance.
        """
        if position.avg_cost <= 0:
            return None
        
        gain_pct = (current_price - position.avg_cost) / position.avg_cost
        
        # Check if trailing stop should be activated
        activation = self.exit_rules.trailing_stop_activation
        distance = self.exit_rules.trailing_stop_distance
        
        if gain_pct >= activation:
            # Calculate new trailing stop
            new_trailing = current_price * (1 - distance)
            
            # Only update if higher than existing
            if position.trailing_stop_price is None or new_trailing > position.trailing_stop_price:
                return round(new_trailing, 2)
        
        return position.trailing_stop_price
    
    def check_stop_triggers(self,
                           positions: Dict[str, Position],
                           current_prices: Dict[str, float]) -> List[Dict]:
        """
        Check all positions for stop-loss triggers.
        
        Returns:
            List of triggered stops with recommended actions
        """
        triggers = []
        
        for symbol, position in positions.items():
            if position.status != PositionStatus.OPEN:
                continue
            
            current_price = current_prices.get(symbol)
            if not current_price:
                continue
            
            triggered = False
            trigger_type = None
            trigger_price = None
            
            # Check hard stop
            if position.stop_loss_price and current_price <= position.stop_loss_price:
                triggered = True
                trigger_type = "HARD_STOP"
                trigger_price = position.stop_loss_price
            
            # Check trailing stop
            elif position.trailing_stop_price and current_price <= position.trailing_stop_price:
                triggered = True
                trigger_type = "TRAILING_STOP"
                trigger_price = position.trailing_stop_price
            
            if triggered:
                loss_pct = (current_price - position.avg_cost) / position.avg_cost if position.avg_cost > 0 else 0
                
                triggers.append({
                    "symbol": symbol,
                    "trigger_type": trigger_type,
                    "trigger_price": trigger_price,
                    "current_price": current_price,
                    "entry_price": position.avg_cost,
                    "shares": position.shares,
                    "loss_pct": round(loss_pct * 100, 2),
                    "loss_amount": round((current_price - position.avg_cost) * position.shares, 2),
                    "action": "EXIT",
                    "urgency": "HIGH"
                })
        
        return triggers
    
    # =========================================================================
    # DRAWDOWN MONITORING
    # =========================================================================
    
    def update_watermark(self, portfolio_value: float, as_of_date: date = None):
        """Update high watermark for drawdown tracking."""
        as_of_date = as_of_date or date.today()
        
        if portfolio_value > self.high_watermark:
            self.high_watermark = portfolio_value
            self.peak_date = as_of_date
    
    def calculate_drawdown(self, current_value: float) -> Dict:
        """Calculate current drawdown from high watermark."""
        if self.high_watermark <= 0:
            return {
                "current_drawdown": 0,
                "drawdown_pct": 0,
                "from_peak": 0,
                "peak_value": 0,
                "peak_date": None,
                "status": "NO_DATA"
            }
        
        drawdown = self.high_watermark - current_value
        drawdown_pct = drawdown / self.high_watermark
        
        # Determine status
        if drawdown_pct <= 0:
            status = "AT_HIGH"
        elif drawdown_pct < 0.05:
            status = "NORMAL"
        elif drawdown_pct < 0.10:
            status = "CAUTION"
        elif drawdown_pct < self.risk_config.max_portfolio_drawdown:
            status = "WARNING"
        else:
            status = "CRITICAL"
        
        return {
            "current_value": current_value,
            "peak_value": self.high_watermark,
            "peak_date": self.peak_date.isoformat() if self.peak_date else None,
            "drawdown_amount": round(drawdown, 2),
            "drawdown_pct": round(drawdown_pct * 100, 2),
            "max_allowed_pct": self.risk_config.max_portfolio_drawdown * 100,
            "status": status,
            "action_required": status in ["WARNING", "CRITICAL"]
        }
    
    def check_daily_loss_limit(self, 
                              current_value: float,
                              day_start_value: float = None) -> Dict:
        """Check if daily loss limit is breached."""
        start_value = day_start_value or self.daily_start_value
        
        if start_value <= 0:
            return {"status": "NO_DATA", "action_required": False}
        
        daily_change = current_value - start_value
        daily_pct = daily_change / start_value
        
        limit = self.risk_config.max_daily_loss
        
        result = {
            "day_start_value": start_value,
            "current_value": current_value,
            "daily_pnl": round(daily_change, 2),
            "daily_pct": round(daily_pct * 100, 2),
            "limit_pct": limit * 100,
            "remaining_pct": round((limit + daily_pct) * 100, 2) if daily_pct < 0 else limit * 100
        }
        
        if daily_pct <= -limit:
            result["status"] = "BREACHED"
            result["action_required"] = True
            result["recommended_action"] = "HALT_TRADING"
        elif daily_pct <= -limit * 0.75:
            result["status"] = "WARNING"
            result["action_required"] = True
            result["recommended_action"] = "REDUCE_EXPOSURE"
        else:
            result["status"] = "OK"
            result["action_required"] = False
        
        return result
    
    # =========================================================================
    # PRE-TRADE RISK CHECKS
    # =========================================================================
    
    def pre_trade_risk_check(self,
                            symbol: str,
                            action: str,  # "BUY", "SELL"
                            shares: int,
                            price: float,
                            portfolio: PortfolioState) -> Dict:
        """
        Perform comprehensive pre-trade risk checks.
        
        Returns:
            Dict with approval status and any concerns
        """
        result = {
            "symbol": symbol,
            "action": action,
            "shares": shares,
            "price": price,
            "trade_value": shares * price,
            "timestamp": datetime.now().isoformat(),
            "checks": [],
            "warnings": [],
            "approved": True
        }
        
        trade_value = shares * price
        
        if action == "BUY":
            # Check 1: Sufficient cash
            if trade_value > portfolio.available_cash:
                result["checks"].append({
                    "check": "CASH_AVAILABLE",
                    "status": "FAILED",
                    "message": f"Trade value ${trade_value:,.2f} exceeds available cash ${portfolio.available_cash:,.2f}"
                })
                result["approved"] = False
            else:
                result["checks"].append({
                    "check": "CASH_AVAILABLE",
                    "status": "PASSED"
                })
            
            # Check 2: Position limit
            if portfolio.num_positions >= self.constraints.max_positions:
                if not portfolio.has_position(symbol):
                    result["checks"].append({
                        "check": "POSITION_LIMIT",
                        "status": "FAILED",
                        "message": f"Max positions ({self.constraints.max_positions}) reached"
                    })
                    result["approved"] = False
            else:
                result["checks"].append({
                    "check": "POSITION_LIMIT",
                    "status": "PASSED"
                })
            
            # Check 3: Single position concentration
            new_allocation = trade_value / portfolio.total_value if portfolio.total_value > 0 else 1
            existing_allocation = portfolio.get_allocation(symbol)
            total_allocation = existing_allocation + new_allocation
            
            if total_allocation > self.constraints.max_single_position:
                result["checks"].append({
                    "check": "CONCENTRATION",
                    "status": "WARNING",
                    "message": f"Position would be {total_allocation*100:.1f}% of portfolio (max {self.constraints.max_single_position*100:.1f}%)"
                })
                result["warnings"].append("Exceeds max single position concentration")
            else:
                result["checks"].append({
                    "check": "CONCENTRATION",
                    "status": "PASSED"
                })
            
            # Check 4: Minimum cash maintenance
            remaining_cash = portfolio.cash - trade_value
            min_cash = portfolio.total_value * self.constraints.min_cash_allocation
            if remaining_cash < min_cash:
                result["checks"].append({
                    "check": "MIN_CASH",
                    "status": "WARNING",
                    "message": f"Would leave ${remaining_cash:,.2f} cash, below minimum ${min_cash:,.2f}"
                })
                result["warnings"].append("Trade would breach minimum cash requirement")
            else:
                result["checks"].append({
                    "check": "MIN_CASH",
                    "status": "PASSED"
                })
            
            # Check 5: Daily loss limit not breached
            daily_check = self.check_daily_loss_limit(portfolio.total_value)
            if daily_check.get("status") == "BREACHED":
                result["checks"].append({
                    "check": "DAILY_LOSS",
                    "status": "FAILED",
                    "message": "Daily loss limit breached - trading halted"
                })
                result["approved"] = False
            elif daily_check.get("status") == "WARNING":
                result["checks"].append({
                    "check": "DAILY_LOSS",
                    "status": "WARNING",
                    "message": "Approaching daily loss limit"
                })
                result["warnings"].append("Near daily loss limit")
            else:
                result["checks"].append({
                    "check": "DAILY_LOSS",
                    "status": "PASSED"
                })
            
            # Check 6: Drawdown status
            drawdown = self.calculate_drawdown(portfolio.total_value)
            if drawdown.get("status") == "CRITICAL":
                result["checks"].append({
                    "check": "DRAWDOWN",
                    "status": "FAILED",
                    "message": f"Portfolio drawdown {drawdown['drawdown_pct']:.1f}% exceeds max {drawdown['max_allowed_pct']:.1f}%"
                })
                result["approved"] = False
            elif drawdown.get("status") == "WARNING":
                result["checks"].append({
                    "check": "DRAWDOWN",
                    "status": "WARNING",
                    "message": f"Portfolio drawdown {drawdown['drawdown_pct']:.1f}% approaching limit"
                })
                result["warnings"].append("Elevated portfolio drawdown")
            else:
                result["checks"].append({
                    "check": "DRAWDOWN",
                    "status": "PASSED"
                })
        
        elif action == "SELL":
            # Check: Has sufficient shares to sell
            position = portfolio.get_position(symbol)
            if not position or position.shares < shares:
                current_shares = position.shares if position else 0
                result["checks"].append({
                    "check": "SHARES_AVAILABLE",
                    "status": "FAILED",
                    "message": f"Attempting to sell {shares} shares but only have {current_shares}"
                })
                result["approved"] = False
            else:
                result["checks"].append({
                    "check": "SHARES_AVAILABLE",
                    "status": "PASSED"
                })
        
        # Summary
        failed_checks = sum(1 for c in result["checks"] if c["status"] == "FAILED")
        warning_checks = sum(1 for c in result["checks"] if c["status"] == "WARNING")
        
        result["summary"] = {
            "total_checks": len(result["checks"]),
            "passed": len(result["checks"]) - failed_checks - warning_checks,
            "warnings": warning_checks,
            "failed": failed_checks
        }
        
        return result
    
    # =========================================================================
    # PORTFOLIO RISK ASSESSMENT
    # =========================================================================
    
    def assess_portfolio_risk(self, portfolio: PortfolioState) -> Dict:
        """
        Comprehensive portfolio risk assessment.
        """
        result = {
            "timestamp": datetime.now().isoformat(),
            "portfolio_value": portfolio.total_value,
            "risk_metrics": {},
            "concentration_risk": {},
            "position_risks": [],
            "overall_risk_score": 0,
            "recommendations": []
        }
        
        # 1. Concentration metrics
        if portfolio.positions:
            allocations = sorted(
                [(s, p.current_allocation) for s, p in portfolio.positions.items()],
                key=lambda x: x[1],
                reverse=True
            )
            
            top_position = allocations[0] if allocations else (None, 0)
            top_5_allocation = sum(a for _, a in allocations[:5])
            
            result["concentration_risk"] = {
                "largest_position": top_position[0],
                "largest_allocation_pct": round(top_position[1] * 100, 2),
                "top_5_allocation_pct": round(top_5_allocation * 100, 2),
                "max_allowed_top_5_pct": self.constraints.top_5_max_allocation * 100,
                "num_positions": len(allocations),
                "herfindahl_index": sum(a**2 for _, a in allocations)  # Concentration measure
            }
            
            if top_5_allocation > self.constraints.top_5_max_allocation:
                result["recommendations"].append(
                    f"Top 5 concentration ({top_5_allocation*100:.1f}%) exceeds limit - consider rebalancing"
                )
        
        # 2. Drawdown metrics
        drawdown_info = self.calculate_drawdown(portfolio.total_value)
        result["risk_metrics"]["drawdown"] = drawdown_info
        
        if drawdown_info.get("action_required"):
            result["recommendations"].append(
                f"Portfolio drawdown at {drawdown_info['drawdown_pct']:.1f}% - consider risk reduction"
            )
        
        # 3. Cash allocation
        result["risk_metrics"]["cash_allocation"] = {
            "current_pct": round(portfolio.cash_allocation * 100, 2),
            "min_required_pct": self.constraints.min_cash_allocation * 100,
            "target_pct": self.constraints.target_cash_allocation * 100,
            "status": "OK" if portfolio.cash_allocation >= self.constraints.min_cash_allocation else "LOW"
        }
        
        if portfolio.cash_allocation < self.constraints.min_cash_allocation:
            result["recommendations"].append(
                f"Cash allocation ({portfolio.cash_allocation*100:.1f}%) below minimum - reduce positions"
            )
        
        # 4. Position-level risks
        for symbol, position in portfolio.positions.items():
            if position.status != PositionStatus.OPEN:
                continue
            
            pos_risk = {
                "symbol": symbol,
                "allocation_pct": round(position.current_allocation * 100, 2),
                "unrealized_pnl_pct": round(position.unrealized_pnl_pct, 2),
                "has_stop_loss": position.stop_loss_price is not None,
                "has_trailing_stop": position.trailing_stop_price is not None,
                "risk_flags": []
            }
            
            # Check for positions without stops
            if not position.stop_loss_price and not position.trailing_stop_price:
                pos_risk["risk_flags"].append("NO_STOP_LOSS")
                result["recommendations"].append(f"{symbol}: Add stop-loss protection")
            
            # Check for large losers
            if position.unrealized_pnl_pct < -15:
                pos_risk["risk_flags"].append("LARGE_LOSS")
                result["recommendations"].append(f"{symbol}: Review thesis - down {abs(position.unrealized_pnl_pct):.1f}%")
            
            # Check for overweight positions
            max_allocation = self.constraints.max_single_position
            if position.current_allocation > max_allocation:
                pos_risk["risk_flags"].append("OVERWEIGHT")
                result["recommendations"].append(f"{symbol}: Overweight at {position.current_allocation*100:.1f}%")
            
            result["position_risks"].append(pos_risk)
        
        # 5. Calculate overall risk score (0-100, higher = more risky)
        risk_score = 0
        
        # Drawdown contribution (0-30 points)
        dd_pct = drawdown_info.get("drawdown_pct", 0)
        risk_score += min(30, dd_pct * 2)
        
        # Concentration contribution (0-25 points)
        if result["concentration_risk"]:
            hhi = result["concentration_risk"].get("herfindahl_index", 0)
            risk_score += min(25, hhi * 100)
        
        # Cash contribution (0-20 points)
        if portfolio.cash_allocation < self.constraints.min_cash_allocation:
            risk_score += 20
        elif portfolio.cash_allocation < self.constraints.target_cash_allocation:
            risk_score += 10
        
        # Position risk contribution (0-25 points)
        positions_without_stops = sum(
            1 for p in result["position_risks"] if "NO_STOP_LOSS" in p.get("risk_flags", [])
        )
        risk_score += min(25, positions_without_stops * 5)
        
        result["overall_risk_score"] = round(risk_score, 1)
        
        if risk_score < 30:
            result["risk_level"] = "LOW"
        elif risk_score < 50:
            result["risk_level"] = "MODERATE"
        elif risk_score < 70:
            result["risk_level"] = "ELEVATED"
        else:
            result["risk_level"] = "HIGH"
        
        return result
    
    # =========================================================================
    # EARNINGS RISK
    # =========================================================================
    
    def check_earnings_exposure(self,
                               positions: Dict[str, Position],
                               earnings_calendar: Dict[str, date]) -> List[Dict]:
        """
        Check positions with upcoming earnings for risk exposure.
        
        Returns positions that may need adjustment before earnings.
        """
        today = date.today()
        alerts = []
        
        for symbol, position in positions.items():
            if position.status != PositionStatus.OPEN:
                continue
            
            earnings_date = earnings_calendar.get(symbol)
            if not earnings_date:
                continue
            
            days_to_earnings = (earnings_date - today).days
            
            if 0 <= days_to_earnings <= 14:  # Within 14 days
                alert = {
                    "symbol": symbol,
                    "earnings_date": earnings_date.isoformat(),
                    "days_until": days_to_earnings,
                    "current_allocation": position.current_allocation,
                    "current_pnl_pct": position.unrealized_pnl_pct
                }
                
                # Recommend action based on configuration
                if self.risk_config.reduce_into_earnings and days_to_earnings <= 3:
                    reduction = self.risk_config.earnings_position_reduction
                    alert["recommended_action"] = f"REDUCE_{int(reduction*100)}%"
                    alert["reason"] = "Reduce exposure before earnings"
                elif days_to_earnings <= 7:
                    alert["recommended_action"] = "REVIEW"
                    alert["reason"] = "Review thesis and set tight stops"
                else:
                    alert["recommended_action"] = "MONITOR"
                    alert["reason"] = "Monitor for any pre-announcement news"
                
                alerts.append(alert)
        
        # Sort by days until earnings
        alerts.sort(key=lambda x: x["days_until"])
        
        return alerts
    
    # =========================================================================
    # INITIALIZATION
    # =========================================================================
    
    def initialize_tracking(self, portfolio_value: float):
        """Initialize or reset risk tracking values."""
        today = date.today()
        
        self.high_watermark = portfolio_value
        self.peak_date = today
        self.daily_start_value = portfolio_value
        self.weekly_start_value = portfolio_value
        self.last_reset_date = today
    
    def daily_reset(self, portfolio_value: float):
        """Reset daily tracking values."""
        self.daily_start_value = portfolio_value
        self.last_reset_date = date.today()
        
        # Update watermark if new high
        self.update_watermark(portfolio_value)
    
    def weekly_reset(self, portfolio_value: float):
        """Reset weekly tracking values."""
        self.weekly_start_value = portfolio_value
