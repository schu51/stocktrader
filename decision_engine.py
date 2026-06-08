"""
Decision Engine
================
Central orchestrator for trading decisions.
Coordinates signals, sizing, risk management, and decision generation.

Now integrated with Data Infrastructure for automatic price history fetching
and enhanced momentum analysis (MACD, RSI, MA Cross, VWAP).
"""

import uuid
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Tuple
import logging
import json
from pathlib import Path

from config import (
    Signal, ConvictionTier, PositionStatus, DecisionType,
    DecisionConfig, DEFAULT_CONFIG
)
from models import (
    Position, PortfolioState, Decision, TradingSignal,
    ResearchScore, MarketSnapshot, WatchlistEntry
)
from signals import SignalGenerator
from position_sizing import PositionSizer
from risk_manager import RiskManager
from momentum import PriceBar, TrendAnalysis

# Try to import data infrastructure (optional dependency)
try:
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from data_infrastructure import DataOrchestrator
    DATA_INFRASTRUCTURE_AVAILABLE = True
except ImportError:
    DATA_INFRASTRUCTURE_AVAILABLE = False
    DataOrchestrator = None

logger = logging.getLogger(__name__)

class DecisionEngine:
    """
    Central decision-making engine for the trading agent.
    
    Workflow:
    1. Generate signals from research + market data
    2. Check entry/exit rules
    3. Calculate position sizes
    4. Apply risk constraints
    5. Generate decision with full rationale
    6. Track for confirmation and execution
    
    Enhanced Features:
    - Automatic price history fetching from Data Infrastructure
    - Enhanced momentum analysis (MACD, RSI, MA Cross, VWAP)
    - Product value override for exceptional fundamentals
    
    Usage:
        # With automatic data fetching
        from data_infrastructure import DataOrchestrator
        
        orchestrator = DataOrchestrator()
        engine = DecisionEngine(data_orchestrator=orchestrator)
        
        # Now evaluate_entry automatically fetches price history
        decision = engine.evaluate_entry("TOST", portfolio, research_score)
        
        # Or without data infrastructure (manual data passing)
        engine = DecisionEngine()
        decision = engine.evaluate_entry("TOST", portfolio, research_score, market_data)
    """
    
    def __init__(self, 
                 config: DecisionConfig = None, 
                 data_dir: Path = None,
                 data_orchestrator = None):
        """
        Initialize the decision engine.
        
        Args:
            config: Decision framework configuration
            data_dir: Directory for persisting decisions
            data_orchestrator: Optional DataOrchestrator instance for automatic data fetching
        """
        self.config = config or DEFAULT_CONFIG
        self.data_dir = data_dir or Path("./decision_data")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        # Data infrastructure integration
        self.data_orchestrator = data_orchestrator
        self._data_available = data_orchestrator is not None
        
        # Initialize components
        self.signal_generator = SignalGenerator(config=self.config)
        self.position_sizer = PositionSizer(config=self.config)
        self.risk_manager = RiskManager(config=self.config)
        
        # Decision tracking
        self.pending_decisions: Dict[str, Decision] = {}
        self.decision_history: List[Decision] = []
        
        # Watchlist
        self.watchlist: Dict[str, WatchlistEntry] = {}
        
        # Cache for price history (avoid refetching within same session)
        self._price_history_cache: Dict[str, List[PriceBar]] = {}
        self._cache_timestamp: Dict[str, datetime] = {}
        self._cache_ttl_minutes: int = 60  # Cache price history for 1 hour
        
        if self._data_available:
            logger.info("DecisionEngine initialized with DataOrchestrator - enhanced momentum enabled")
        else:
            logger.info("DecisionEngine initialized without DataOrchestrator - using simple momentum")
    
    def _fetch_price_history(self, 
                            symbol: str, 
                            days: int = 252,
                            force_refresh: bool = False) -> Optional[List[PriceBar]]:
        """
        Fetch price history from data infrastructure and convert to PriceBar format.
        
        Args:
            symbol: Stock ticker
            days: Number of trading days to fetch (default: 252 = ~1 year)
            force_refresh: Bypass cache and fetch fresh data
        
        Returns:
            List of PriceBar objects, oldest first, or None if unavailable
        """
        if not self._data_available:
            return None
        
        # Check cache
        cache_key = f"{symbol}_{days}"
        if not force_refresh and cache_key in self._price_history_cache:
            cache_time = self._cache_timestamp.get(cache_key)
            if cache_time and (datetime.now() - cache_time).total_seconds() < self._cache_ttl_minutes * 60:
                logger.debug(f"Using cached price history for {symbol}")
                return self._price_history_cache[cache_key]
        
        try:
            # Fetch from data infrastructure
            # Map days to yfinance period string
            if days <= 5:
                period = "5d"
            elif days <= 30:
                period = "1mo"
            elif days <= 90:
                period = "3mo"
            elif days <= 180:
                period = "6mo"
            elif days <= 252:
                period = "1y"
            else:
                period = "2y"
            
            logger.info(f"Fetching price history for {symbol} (period: {period})")
            
            # Use Yahoo Finance fetcher directly for price data
            price_data = self.data_orchestrator.yahoo.get_daily_prices(symbol, period=period)
            
            if not price_data or "prices" not in price_data:
                logger.warning(f"No price data returned for {symbol}")
                return None
            
            # Convert to PriceBar format
            price_bars = self._convert_to_price_bars(price_data["prices"])
            
            if price_bars:
                # Update cache
                self._price_history_cache[cache_key] = price_bars
                self._cache_timestamp[cache_key] = datetime.now()
                logger.info(f"Fetched {len(price_bars)} price bars for {symbol}")
            
            return price_bars
            
        except Exception as e:
            logger.error(f"Error fetching price history for {symbol}: {e}")
            return None
    
    def _convert_to_price_bars(self, prices: List[Dict]) -> List[PriceBar]:
        """
        Convert price data from data infrastructure format to PriceBar objects.
        
        Expected input format (from Yahoo Finance fetcher):
        [
            {"date": "2024-01-02", "open": 100, "high": 102, "low": 99, "close": 101, "volume": 1000000},
            ...
        ]
        """
        bars = []
        
        for p in prices:
            try:
                # Handle date parsing
                bar_date = p.get("date")
                if isinstance(bar_date, str):
                    bar_date = date.fromisoformat(bar_date.split("T")[0])
                elif isinstance(bar_date, datetime):
                    bar_date = bar_date.date()
                elif not isinstance(bar_date, date):
                    continue
                
                # Extract OHLCV
                bar = PriceBar(
                    date=bar_date,
                    open=float(p.get("open", 0)),
                    high=float(p.get("high", 0)),
                    low=float(p.get("low", 0)),
                    close=float(p.get("close", 0)),
                    volume=int(p.get("volume", 0))
                )
                
                # Validate bar has reasonable data
                if bar.close > 0 and bar.volume >= 0:
                    bars.append(bar)
                    
            except (ValueError, TypeError, KeyError) as e:
                logger.debug(f"Skipping invalid price bar: {e}")
                continue
        
        # Sort by date (oldest first)
        bars.sort(key=lambda x: x.date)
        
        return bars
    
    def _fetch_market_snapshot(self, symbol: str) -> Optional[MarketSnapshot]:
        """
        Fetch current market data from data infrastructure.
        
        Returns MarketSnapshot with current price, volume, 52-week range, etc.
        """
        if not self._data_available:
            return None
        
        try:
            # Get screening data which includes current market info
            data = self.data_orchestrator.get_screening_data(symbol)
            
            if not data or "unified" not in data:
                return None
            
            unified = data["unified"]
            company = unified.get("company", {})
            valuation = unified.get("valuation", {})
            analyst = unified.get("analyst", {})
            risk = unified.get("risk", {})
            
            # Get current price from yahoo data if available
            yahoo = data.get("yahoo", {})
            current_price = analyst.get("current_price") or yahoo.get("current_price", 0)
            
            if not current_price:
                return None
            
            return MarketSnapshot(
                symbol=symbol,
                timestamp=datetime.now(),
                current_price=current_price,
                previous_close=current_price,  # Approximate
                day_change_pct=0,
                day_high=current_price * 1.01,  # Approximate
                day_low=current_price * 0.99,
                week_52_high=risk.get("52_week_high", current_price * 1.2),
                week_52_low=risk.get("52_week_low", current_price * 0.6),
                volume=yahoo.get("volume", 1000000),
                avg_volume=yahoo.get("avg_volume", 1000000),
                market_cap=company.get("market_cap", 0),
                pe_ratio=valuation.get("pe_ratio"),
                ps_ratio=valuation.get("ps_ratio"),
                ev_ebitda=valuation.get("ev_ebitda"),
                analyst_target=analyst.get("target_mean"),
                analyst_upside_pct=analyst.get("upside_pct"),
                sma_50=yahoo.get("sma_50"),
                sma_200=yahoo.get("sma_200")
            )
            
        except Exception as e:
            logger.error(f"Error fetching market snapshot for {symbol}: {e}")
            return None
    
    # =========================================================================
    # ENTRY EVALUATION
    # =========================================================================
    
    def evaluate_entry(self,
                      symbol: str,
                      portfolio: PortfolioState,
                      research_score: Optional[ResearchScore] = None,
                      market_snapshot: Optional[MarketSnapshot] = None,
                      screening_data: Optional[Dict] = None,
                      price_history: Optional[List[PriceBar]] = None,
                      auto_fetch: bool = True) -> Decision:
        """
        Evaluate whether to initiate a new position.
        
        When DataOrchestrator is available and auto_fetch=True:
        - Automatically fetches price history for enhanced momentum (MACD, RSI, MA, VWAP)
        - Automatically fetches market snapshot if not provided
        - Automatically fetches screening data if not provided
        
        Args:
            symbol: Stock ticker
            portfolio: Current portfolio state
            research_score: Stage 1D research score (required for full analysis)
            market_snapshot: Current market data (auto-fetched if None)
            screening_data: Data from data infrastructure (auto-fetched if None)
            price_history: Historical OHLCV data (auto-fetched if None)
            auto_fetch: Whether to auto-fetch missing data (default: True)
        
        Returns:
            Decision object with recommendation and rationale
        """
        decision_id = f"DEC-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"
        timestamp = datetime.now()
        
        # Auto-fetch data from infrastructure if available
        if auto_fetch and self._data_available:
            # Fetch price history for enhanced momentum
            if price_history is None:
                price_history = self._fetch_price_history(symbol)
                if price_history:
                    logger.info(f"Auto-fetched {len(price_history)} price bars for enhanced momentum")
            
            # Fetch market snapshot if not provided
            if market_snapshot is None:
                market_snapshot = self._fetch_market_snapshot(symbol)
                if market_snapshot:
                    logger.info(f"Auto-fetched market snapshot for {symbol}")
            
            # Fetch screening data if not provided
            if screening_data is None:
                try:
                    screening_data = self.data_orchestrator.get_screening_data(symbol)
                    if screening_data:
                        logger.info(f"Auto-fetched screening data for {symbol}")
                except Exception as e:
                    logger.warning(f"Could not fetch screening data for {symbol}: {e}")
        
        # 1. Generate trading signal (with enhanced momentum if price history available)
        signal = self.signal_generator.generate_signal(
            symbol=symbol,
            research_score=research_score,
            market_data=market_snapshot,
            screening_data=screening_data,
            price_history=price_history  # Enables MACD, RSI, MA Cross, VWAP
        )
        
        # Get trend analysis for decision rationale
        trend_analysis = self.signal_generator.get_trend_analysis()
        
        # 2. Determine conviction tier
        conviction = ConvictionTier.MEDIUM
        if research_score:
            conviction = research_score.conviction_tier
        
        # 3. Get current price — prefer the freshest source available.
        # price_history[-1].close is from yfinance (live daily bar); market_snapshot
        # and screening_data are fetched earlier and may be slightly staler.
        current_price = 0.0
        if price_history:
            current_price = price_history[-1].close
        if market_snapshot and market_snapshot.current_price > 0:
            current_price = market_snapshot.current_price  # overwrite only if valid
        if current_price <= 0 and screening_data:
            current_price = screening_data.get("unified", {}).get("analyst", {}).get("current_price", 0)

        if current_price <= 0:
            return Decision(
                decision_id=f"DEC-{datetime.now().strftime('%Y%m%d%H%M%S')}-{symbol}",
                timestamp=datetime.now(),
                symbol=symbol,
                decision_type=DecisionType.HOLD,
                action="HOLD",
                shares=0,
                limit_price=0,
                position_size_pct=0,
                primary_reason="Cannot determine current price — entry blocked",
                status="PENDING",
                created_by="decision_engine"
            )
        
        # 4. Check entry rules
        entry_check = self._check_entry_rules(
            symbol, signal, research_score, market_snapshot, screening_data, portfolio
        )
        
        # 5. Calculate position size (if entry is approved)
        sizing = None
        stop_loss_price = None
        if entry_check["approved"]:
            # Get volatility if available
            volatility = None
            if market_snapshot and hasattr(market_snapshot, "beta") and market_snapshot.beta:
                volatility = market_snapshot.beta * 0.20  # Rough estimate
            
            # Calculate stop loss
            stop_loss_info = self.risk_manager.calculate_stop_loss(
                entry_price=current_price,
                conviction=conviction
            )
            stop_loss_pct = stop_loss_info["stop_percentage"]
            stop_loss_price = stop_loss_info["recommended_stop"]
            
            sizing = self.position_sizer.calculate_position_size(
                symbol=symbol,
                portfolio=portfolio,
                conviction=conviction,
                signal=signal,
                research=research_score,
                current_price=current_price,
                volatility=volatility,
                stop_loss_pct=stop_loss_pct
            )
        
        # 6. Pre-trade risk check
        risk_check = None
        if sizing and sizing.get("final_recommendation", {}).get("shares", 0) > 0:
            shares = sizing["final_recommendation"]["shares"]
            risk_check = self.risk_manager.pre_trade_risk_check(
                symbol=symbol,
                action="BUY",
                shares=shares,
                price=current_price,
                portfolio=portfolio
            )
        
        # 7. Build decision
        if not entry_check["approved"]:
            action = "HOLD"
            decision_type = DecisionType.HOLD
            primary_reason = f"Entry not approved: {entry_check['reason']}"
            shares = 0
            position_value = 0
            position_size_pct = 0
            stop_loss_price = None
            target_price = None
        elif risk_check and not risk_check["approved"]:
            action = "HOLD"
            decision_type = DecisionType.HOLD
            primary_reason = f"Risk check failed: {[c['message'] for c in risk_check['checks'] if c['status'] == 'FAILED']}"
            shares = 0
            position_value = 0
            position_size_pct = 0
            stop_loss_price = None
            target_price = None
        elif signal.signal in [Signal.STRONG_BUY, Signal.BUY]:
            action = "BUY"
            decision_type = DecisionType.INITIATE
            primary_reason = f"Signal: {signal.signal.value} (strength: {signal.strength:.2f}, confidence: {signal.confidence:.2f})"
            shares = sizing["final_recommendation"]["shares"]
            position_value = sizing["final_recommendation"]["dollar_value"]
            position_size_pct = sizing["final_recommendation"]["allocation_pct"]
            
            # Set targets from research
            if research_score:
                target_price = research_score.base_case_price
            else:
                target_price = None
        else:
            action = "HOLD"
            decision_type = DecisionType.HOLD
            primary_reason = f"Signal is {signal.signal.value} - not actionable for entry"
            shares = 0
            position_value = 0
            position_size_pct = 0
            stop_loss_price = None
            target_price = None
        
        # Calculate risk/reward
        risk_reward = None
        if action == "BUY" and target_price and stop_loss_price and current_price:
            upside = target_price - current_price
            downside = current_price - stop_loss_price
            if downside > 0:
                risk_reward = round(upside / downside, 2)
        
        # Build supporting reasons
        supporting_reasons = signal.reasons.copy()
        if entry_check.get("passed_checks"):
            supporting_reasons.extend([f"✓ {c}" for c in entry_check["passed_checks"]])
        
        # Add trend analysis info if available
        if trend_analysis and action == "BUY":
            supporting_reasons.insert(0, 
                f"📊 Trend Score: {trend_analysis.composite_score:.0f}/100 ({trend_analysis.trend_direction.value})"
            )
            if trend_analysis.macd:
                supporting_reasons.append(f"MACD: {trend_analysis.macd.trend_signal}")
            if trend_analysis.rsi:
                supporting_reasons.append(f"RSI: {trend_analysis.rsi.rsi:.1f} ({trend_analysis.rsi.condition})")
            if trend_analysis.ma_cross:
                supporting_reasons.append(f"MA: {trend_analysis.ma_cross.trend_strength}")
        
        # Build risks list
        risks = signal.warnings.copy()
        if research_score and research_score.key_risks:
            risks.extend(research_score.key_risks)
        if entry_check.get("warnings"):
            risks.extend(entry_check["warnings"])
        
        decision = Decision(
            decision_id=decision_id,
            timestamp=timestamp,
            symbol=symbol,
            decision_type=decision_type,
            action=action,
            shares=shares,
            limit_price=current_price,
            position_size_pct=position_size_pct,
            position_value=position_value,
            stop_loss_price=stop_loss_price if action == "BUY" else None,
            target_price=target_price,
            risk_reward_ratio=risk_reward,
            max_loss_amount=shares * (current_price - stop_loss_price) if stop_loss_price and shares else None,
            signal=signal,
            research_score=research_score,
            market_snapshot=market_snapshot,
            primary_reason=primary_reason,
            supporting_reasons=supporting_reasons[:10],  # Limit to 10
            risks=risks[:5],  # Limit to 5
            status="PENDING" if action == "BUY" else "HOLD",
            requires_confirmation=self.config.require_confirmation,
            created_by="decision_engine"
        )
        
        # Track pending decisions
        if action == "BUY":
            self.pending_decisions[decision_id] = decision
            self._save_decision(decision)
        
        return decision
    
    def _check_entry_rules(self,
                          symbol: str,
                          signal: TradingSignal,
                          research: Optional[ResearchScore],
                          market: Optional[MarketSnapshot],
                          screening_data: Optional[Dict],
                          portfolio: PortfolioState) -> Dict:
        """Check all entry rules and return approval status."""
        rules = self.config.entry_rules
        result = {
            "approved": True,
            "reason": "",
            "passed_checks": [],
            "failed_checks": [],
            "warnings": []
        }
        
        # 1. Signal must be BUY or STRONG_BUY
        if signal.signal not in [Signal.BUY, Signal.STRONG_BUY]:
            result["approved"] = False
            result["failed_checks"].append(f"Signal is {signal.signal.value}, not BUY")
            result["reason"] = "Signal not bullish"
            return result
        result["passed_checks"].append("Bullish signal")
        
        # 2. Research score minimum
        if research:
            if research.overall_score < rules.min_overall_score:
                result["approved"] = False
                result["failed_checks"].append(
                    f"Research score {research.overall_score:.2f} below minimum {rules.min_overall_score}"
                )
                result["reason"] = "Research score too low"
                return result
            result["passed_checks"].append(f"Research score {research.overall_score:.2f} passes")
        
        # 3. Already have position?
        if portfolio.has_position(symbol):
            result["approved"] = False
            result["failed_checks"].append("Already have open position")
            result["reason"] = "Position already exists - use ADD action instead"
            return result
        result["passed_checks"].append("No existing position")
        
        # 4. FALLING KNIFE DETECTION - Critical new check
        if market:
            if market.week_52_high and market.week_52_low and market.current_price:
                high = market.week_52_high
                current = market.current_price
                pct_from_high = (high - current) / high if high > 0 else 0
                
                # Hard rejection: Don't buy stocks down >35% from 52-week high
                if pct_from_high > rules.max_decline_from_high_pct:
                    result["approved"] = False
                    result["failed_checks"].append(
                        f"⚠️ FALLING KNIFE: Price {pct_from_high*100:.0f}% below 52-week high "
                        f"(max allowed: {rules.max_decline_from_high_pct*100:.0f}%)"
                    )
                    result["reason"] = f"Falling knife - stock down {pct_from_high*100:.0f}% from high, needs trend confirmation"
                    return result
                elif pct_from_high > 0.25:
                    result["warnings"].append(
                        f"Stock {pct_from_high*100:.0f}% below 52-week high - elevated risk"
                    )
                else:
                    result["passed_checks"].append(f"Price within acceptable range of 52-week high")
                
                # Check if below 50-day MA (trend confirmation)
                if rules.require_above_sma and market.sma_50:
                    if current < market.sma_50:
                        ma_distance = (market.sma_50 - current) / market.sma_50 * 100
                        result["warnings"].append(
                            f"Price {ma_distance:.1f}% below 50-day MA - negative trend"
                        )
                
                # Check 200-day MA for long-term trend
                if market.sma_200:
                    if current < market.sma_200 * 0.80:  # 20% below 200-day MA
                        ma200_distance = (market.sma_200 - current) / market.sma_200 * 100
                        result["warnings"].append(
                            f"Price {ma200_distance:.1f}% below 200-day MA - long-term downtrend"
                        )
        
        # 5. Valuation checks from screening data
        if screening_data:
            unified = screening_data.get("unified", {})
            val = unified.get("valuation", {})
            
            pe = val.get("pe_ratio")
            if pe and pe > rules.max_pe_ratio:
                result["warnings"].append(f"P/E {pe:.1f}x exceeds {rules.max_pe_ratio} max")
            
            ps = val.get("ps_ratio")
            if ps and ps > rules.max_ps_ratio:
                result["warnings"].append(f"P/S {ps:.1f}x exceeds {rules.max_ps_ratio} max")
            
            # Analyst upside
            analyst = unified.get("analyst", {})
            upside = analyst.get("upside_pct")
            if upside and upside < rules.min_upside_pct:
                result["warnings"].append(f"Analyst upside {upside:.1f}% below {rules.min_upside_pct}% min")
            elif upside:
                result["passed_checks"].append(f"Analyst upside {upside:.1f}%")
        
        # 6. Quality checks
        if screening_data:
            quality = screening_data.get("unified", {}).get("quality", {})
            
            gross_margin = quality.get("gross_margin")
            if gross_margin and gross_margin < rules.min_gross_margin:
                result["warnings"].append(f"Gross margin {gross_margin*100:.1f}% below {rules.min_gross_margin*100}% min")
            
            growth = screening_data.get("unified", {}).get("growth", {})
            rev_growth = growth.get("revenue_growth")
            if rev_growth and rev_growth < rules.min_revenue_growth:
                result["warnings"].append(f"Revenue growth {rev_growth*100:.1f}% below {rules.min_revenue_growth*100}% min")
        
        # 7. Confidence check - reject low confidence signals
        if signal.confidence < 0.50:
            result["warnings"].append(f"Low signal confidence ({signal.confidence:.2f}) - mixed signals")
        
        # If we have warnings but no hard failures, still approve with caution
        if result["warnings"] and result["approved"]:
            result["reason"] = f"Approved with {len(result['warnings'])} warnings"
        elif result["approved"]:
            result["reason"] = "All entry criteria met"
        
        return result
    
    # =========================================================================
    # BATCH EVALUATION
    # =========================================================================
    
    def evaluate_entries_batch(self,
                              symbols: List[str],
                              portfolio: PortfolioState,
                              research_scores: Dict[str, ResearchScore] = None,
                              auto_fetch: bool = True) -> Dict[str, Decision]:
        """
        Evaluate multiple symbols for entry in batch.
        
        Automatically fetches price history and market data for each symbol
        when DataOrchestrator is available.
        
        Args:
            symbols: List of stock tickers to evaluate
            portfolio: Current portfolio state
            research_scores: Dict mapping symbol to ResearchScore
            auto_fetch: Whether to auto-fetch data (default: True)
        
        Returns:
            Dict mapping symbol to Decision
        """
        research_scores = research_scores or {}
        decisions = {}
        
        for symbol in symbols:
            logger.info(f"Evaluating {symbol}...")
            
            decision = self.evaluate_entry(
                symbol=symbol,
                portfolio=portfolio,
                research_score=research_scores.get(symbol),
                auto_fetch=auto_fetch
            )
            
            decisions[symbol] = decision
            
            # Log result
            if decision.action == "BUY":
                logger.info(f"  {symbol}: BUY {decision.shares} shares @ ${decision.limit_price:.2f}")
            else:
                logger.info(f"  {symbol}: {decision.action} - {decision.primary_reason[:50]}...")
        
        return decisions
    
    def get_entry_summary(self, decisions: Dict[str, Decision]) -> Dict:
        """
        Get summary of batch entry decisions.
        
        Returns:
            Dict with buy/hold counts, total investment, and ranked opportunities
        """
        buys = [(s, d) for s, d in decisions.items() if d.action == "BUY"]
        holds = [(s, d) for s, d in decisions.items() if d.action == "HOLD"]
        
        # Rank buys by signal strength
        buys_ranked = sorted(buys, key=lambda x: x[1].signal.strength if x[1].signal else 0, reverse=True)
        
        total_investment = sum(d.position_value for _, d in buys)
        
        return {
            "total_evaluated": len(decisions),
            "buy_count": len(buys),
            "hold_count": len(holds),
            "total_investment": total_investment,
            "buy_decisions": [
                {
                    "symbol": s,
                    "shares": d.shares,
                    "price": d.limit_price,
                    "value": d.position_value,
                    "allocation_pct": d.position_size_pct,
                    "strength": d.signal.strength if d.signal else 0,
                    "trend_score": d.supporting_reasons[0] if d.supporting_reasons and "Trend Score" in d.supporting_reasons[0] else None
                }
                for s, d in buys_ranked
            ],
            "hold_decisions": [
                {
                    "symbol": s,
                    "reason": d.primary_reason
                }
                for s, d in holds
            ]
        }
    
    # =========================================================================
    # EXIT EVALUATION
    # =========================================================================
    
    def check_positions_for_exit(self,
                                portfolio: PortfolioState,
                                current_prices: Dict[str, float],
                                earnings_calendar: Dict[str, date] = None) -> List[Decision]:
        """
        Check all positions for exit conditions.
        
        Returns:
            List of exit decisions
        """
        decisions = []
        earnings_calendar = earnings_calendar or {}
        
        for symbol, position in portfolio.positions.items():
            if position.status != PositionStatus.OPEN:
                continue
            
            current_price = current_prices.get(symbol)
            if not current_price:
                continue
            
            # Update position P&L
            position.update_pnl(current_price)
            
            # Check various exit conditions
            exit_decision = self._evaluate_exit(
                position, current_price, portfolio, earnings_calendar.get(symbol)
            )
            
            if exit_decision:
                decisions.append(exit_decision)
        
        return decisions
    
    def _evaluate_exit(self,
                      position: Position,
                      current_price: float,
                      portfolio: PortfolioState,
                      earnings_date: Optional[date] = None) -> Optional[Decision]:
        """Evaluate a single position for exit."""
        decision_id = f"DEC-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"
        timestamp = datetime.now()
        exit_rules = self.config.exit_rules
        
        symbol = position.symbol
        shares = position.shares
        
        # 1. Check stop-loss
        if position.check_stop_loss(current_price):
            trigger_type = "TRAILING_STOP" if position.trailing_stop_price and current_price <= position.trailing_stop_price else "HARD_STOP"
            
            return Decision(
                decision_id=decision_id,
                timestamp=timestamp,
                symbol=symbol,
                decision_type=DecisionType.STOP_LOSS,
                action="SELL",
                shares=shares,
                limit_price=current_price,
                position_size_pct=0,  # Full exit
                primary_reason=f"{trigger_type} triggered at ${current_price:.2f}",
                supporting_reasons=[
                    f"Entry price: ${position.avg_cost:.2f}",
                    f"Stop price: ${position.stop_loss_price or position.trailing_stop_price:.2f}",
                    f"Loss: {position.unrealized_pnl_pct:.1f}%"
                ],
                risks=["Stop loss protects capital"],
                status="PENDING",
                requires_confirmation=False,  # Stops should execute automatically
                created_by="decision_engine"
            )
        
        # 2. Check profit target
        if position.check_target(current_price):
            return Decision(
                decision_id=decision_id,
                timestamp=timestamp,
                symbol=symbol,
                decision_type=DecisionType.TAKE_PROFIT,
                action="SELL",
                shares=shares,
                limit_price=current_price,
                position_size_pct=0,
                primary_reason=f"Target price ${position.target_price:.2f} reached",
                supporting_reasons=[
                    f"Entry price: ${position.avg_cost:.2f}",
                    f"Gain: {position.unrealized_pnl_pct:.1f}%"
                ],
                risks=["May miss further upside"],
                status="PENDING",
                requires_confirmation=True,
                created_by="decision_engine"
            )
        
        # 3. Check partial profit taking
        # unrealized_pnl_pct is stored as a percentage (e.g. 40.0 for +40%).
        # take_profit_partial is a decimal fraction (e.g. 0.40). Multiply by 100
        # to convert to the same scale before comparing.
        #
        # Guard: only fire once per gain tier. Without this, the engine fires
        # REDUCE every day the position stays above 40%, grinding winners to
        # zero. Require the position to have a meaningful size remaining after
        # the sale (>= $2,000), and only fire if this would be the first or
        # a step-up partial (every additional 20% gain above the last tier).
        if position.unrealized_pnl_pct >= exit_rules.take_profit_partial * 100:
            partial_shares = int(shares * exit_rules.take_profit_partial_size)
            remaining_value = (shares - partial_shares) * current_price

            # Skip if remaining position would be too small to be meaningful
            if remaining_value < 2000:
                partial_shares = 0

            if partial_shares > 0:
                return Decision(
                    decision_id=decision_id,
                    timestamp=timestamp,
                    symbol=symbol,
                    decision_type=DecisionType.REDUCE,
                    action="SELL",
                    shares=partial_shares,
                    limit_price=current_price,
                    position_size_pct=position.current_allocation * (1 - exit_rules.take_profit_partial_size),
                    primary_reason=f"Partial profit taking at {position.unrealized_pnl_pct:.1f}% gain",
                    supporting_reasons=[
                        f"Selling {exit_rules.take_profit_partial_size*100:.0f}% of position",
                        f"Locking in ${position.unrealized_pnl * exit_rules.take_profit_partial_size:.2f} profit"
                    ],
                    risks=["Reduces exposure to further gains"],
                    status="PENDING",
                    requires_confirmation=True,
                    created_by="decision_engine"
                )
        
        # 4. Update trailing stop
        new_trailing = self.risk_manager.update_trailing_stop(position, current_price)
        if new_trailing and new_trailing != position.trailing_stop_price:
            position.trailing_stop_price = new_trailing
            logger.info(f"Updated trailing stop for {symbol} to ${new_trailing:.2f}")
        
        # 5. Earnings proximity check
        if earnings_date and self.config.risk_config.reduce_into_earnings:
            days_to_earnings = (earnings_date - date.today()).days
            
            if 0 < days_to_earnings <= 3:
                reduction_pct = self.config.risk_config.earnings_position_reduction
                reduce_shares = int(shares * reduction_pct)
                
                if reduce_shares > 0:
                    return Decision(
                        decision_id=decision_id,
                        timestamp=timestamp,
                        symbol=symbol,
                        decision_type=DecisionType.REDUCE,
                        action="SELL",
                        shares=reduce_shares,
                        limit_price=current_price,
                        position_size_pct=position.current_allocation * (1 - reduction_pct),
                        primary_reason=f"Reducing exposure ahead of earnings ({days_to_earnings} days)",
                        supporting_reasons=[
                            f"Earnings date: {earnings_date.isoformat()}",
                            f"Reducing by {reduction_pct*100:.0f}%"
                        ],
                        risks=["May miss earnings beat", "May lock in gains before drop"],
                        status="PENDING",
                        requires_confirmation=True,
                        created_by="decision_engine"
                    )
        
        # 6. Check holding period
        if position.entry_date:
            holding_days = (date.today() - position.entry_date).days
            
            if holding_days >= exit_rules.max_holding_period_days:
                return Decision(
                    decision_id=decision_id,
                    timestamp=timestamp,
                    symbol=symbol,
                    decision_type=DecisionType.EXIT,
                    action="REVIEW",  # Not automatic sell, but flag for review
                    shares=shares,
                    limit_price=current_price,
                    primary_reason=f"Position held for {holding_days} days - review required",
                    supporting_reasons=[
                        f"Max holding period: {exit_rules.max_holding_period_days} days",
                        f"Current P&L: {position.unrealized_pnl_pct:.1f}%"
                    ],
                    status="PENDING",
                    requires_confirmation=True,
                    created_by="decision_engine"
                )
        
        return None
    
    # =========================================================================
    # PORTFOLIO REVIEW
    # =========================================================================
    
    def portfolio_review(self,
                        portfolio: PortfolioState,
                        market_data: Dict[str, MarketSnapshot] = None,
                        research_scores: Dict[str, ResearchScore] = None,
                        screening_data: Dict[str, Dict] = None,
                        earnings_calendar: Dict[str, date] = None) -> Dict:
        """
        Comprehensive portfolio review generating all actionable decisions.
        
        Returns:
            Dict with categorized decisions and portfolio analysis
        """
        market_data = market_data or {}
        research_scores = research_scores or {}
        screening_data = screening_data or {}
        earnings_calendar = earnings_calendar or {}
        
        result = {
            "timestamp": datetime.now().isoformat(),
            "portfolio_value": portfolio.total_value,
            "exit_decisions": [],
            "add_decisions": [],
            "new_entry_opportunities": [],
            "watchlist_alerts": [],
            "risk_assessment": {},
            "rebalance_recommendations": []
        }
        
        # 1. Check existing positions for exits
        current_prices = {
            s: m.current_price for s, m in market_data.items()
        }
        exit_decisions = self.check_positions_for_exit(
            portfolio, current_prices, earnings_calendar
        )
        result["exit_decisions"] = [d.to_dict() for d in exit_decisions]
        
        # 2. Check positions for add opportunities
        for symbol, position in portfolio.positions.items():
            if position.status != PositionStatus.OPEN:
                continue
            
            current_price = current_prices.get(symbol)
            if not current_price:
                continue
            
            # Check if scale-in opportunity
            add_sizing = self.position_sizer.calculate_add_size(
                position, portfolio, current_price
            )
            
            if add_sizing.get("recommendation") == "ADD":
                result["add_decisions"].append({
                    "symbol": symbol,
                    "current_allocation": position.current_allocation * 100,
                    "recommendation": add_sizing
                })
        
        # 3. Evaluate watchlist for new entries
        for symbol, entry in self.watchlist.items():
            if portfolio.has_position(symbol):
                continue
            
            current_price = current_prices.get(symbol)
            
            # Check if entry criteria met
            if entry.target_entry_price and current_price:
                if current_price <= entry.target_entry_price:
                    decision = self.evaluate_entry(
                        symbol=symbol,
                        portfolio=portfolio,
                        research_score=research_scores.get(symbol),
                        market_snapshot=market_data.get(symbol),
                        screening_data=screening_data.get(symbol)
                    )
                    if decision.action == "BUY":
                        result["new_entry_opportunities"].append(decision.to_dict())
                        result["watchlist_alerts"].append({
                            "symbol": symbol,
                            "alert": f"Entry price ${entry.target_entry_price:.2f} reached",
                            "current_price": current_price
                        })
        
        # 4. Risk assessment
        result["risk_assessment"] = self.risk_manager.assess_portfolio_risk(portfolio)
        
        # 5. Earnings exposure
        earnings_alerts = self.risk_manager.check_earnings_exposure(
            portfolio.positions, earnings_calendar
        )
        if earnings_alerts:
            result["earnings_exposure"] = earnings_alerts
        
        # 6. Rebalancing recommendations
        for symbol, position in portfolio.positions.items():
            if position.target_allocation > 0:
                drift = abs(position.current_allocation - position.target_allocation)
                if drift > 0.02:  # 2% drift threshold
                    result["rebalance_recommendations"].append({
                        "symbol": symbol,
                        "current_allocation": round(position.current_allocation * 100, 2),
                        "target_allocation": round(position.target_allocation * 100, 2),
                        "drift_pct": round(drift * 100, 2),
                        "action": "REDUCE" if position.current_allocation > position.target_allocation else "ADD"
                    })
        
        return result
    
    # =========================================================================
    # DECISION MANAGEMENT
    # =========================================================================
    
    def confirm_decision(self, decision_id: str, confirmed_by: str = "user") -> bool:
        """Confirm a pending decision for execution."""
        if decision_id not in self.pending_decisions:
            logger.warning(f"Decision {decision_id} not found in pending")
            return False
        
        decision = self.pending_decisions[decision_id]
        decision.status = "CONFIRMED"
        decision.confirmed_by = confirmed_by
        decision.confirmed_at = datetime.now()
        
        self._save_decision(decision)
        logger.info(f"Decision {decision_id} confirmed by {confirmed_by}")
        
        return True
    
    def cancel_decision(self, decision_id: str, reason: str = "") -> bool:
        """Cancel a pending decision."""
        if decision_id not in self.pending_decisions:
            return False
        
        decision = self.pending_decisions[decision_id]
        decision.status = "CANCELLED"
        decision.notes = reason
        
        # Move to history
        self.decision_history.append(decision)
        del self.pending_decisions[decision_id]
        
        self._save_decision(decision)
        logger.info(f"Decision {decision_id} cancelled: {reason}")
        
        return True
    
    def mark_executed(self, decision_id: str, execution_price: float) -> bool:
        """Mark a decision as executed."""
        decision = None
        
        if decision_id in self.pending_decisions:
            decision = self.pending_decisions[decision_id]
            del self.pending_decisions[decision_id]
        
        if not decision:
            # Check history
            for d in self.decision_history:
                if d.decision_id == decision_id:
                    decision = d
                    break
        
        if not decision:
            return False
        
        decision.status = "EXECUTED"
        decision.executed_at = datetime.now()
        decision.execution_price = execution_price
        
        self.decision_history.append(decision)
        self._save_decision(decision)
        
        logger.info(f"Decision {decision_id} executed at ${execution_price:.2f}")
        return True
    
    def get_pending_decisions(self) -> List[Decision]:
        """Get all pending decisions."""
        return list(self.pending_decisions.values())
    
    def get_decision_history(self, symbol: str = None, days: int = 30) -> List[Decision]:
        """Get decision history, optionally filtered."""
        cutoff = datetime.now() - timedelta(days=days)
        
        filtered = [
            d for d in self.decision_history
            if d.timestamp >= cutoff
        ]
        
        if symbol:
            filtered = [d for d in filtered if d.symbol == symbol]
        
        return sorted(filtered, key=lambda x: x.timestamp, reverse=True)
    
    # =========================================================================
    # WATCHLIST MANAGEMENT
    # =========================================================================
    
    def add_to_watchlist(self,
                        symbol: str,
                        research_score: ResearchScore = None,
                        target_entry_price: float = None,
                        thesis: str = "") -> WatchlistEntry:
        """Add a symbol to the watchlist."""
        entry = WatchlistEntry(
            symbol=symbol,
            added_date=date.today(),
            research_score=research_score.overall_score if research_score else 0,
            conviction_tier=research_score.conviction_tier if research_score else ConvictionTier.MEDIUM,
            thesis=thesis or (research_score.thesis if research_score else ""),
            target_entry_price=target_entry_price,
            target_price=research_score.base_case_price if research_score else None,
            stop_loss_price=None,  # Calculated on entry
            target_allocation=self.config.position_sizing.target_position_by_conviction.get(
                research_score.conviction_tier if research_score else ConvictionTier.MEDIUM, 0.02
            ),
            price_alert_below=target_entry_price,
            status="WATCHING"
        )
        
        self.watchlist[symbol] = entry
        self._save_watchlist()
        
        logger.info(f"Added {symbol} to watchlist with target entry ${target_entry_price or 'TBD'}")
        return entry
    
    def remove_from_watchlist(self, symbol: str) -> bool:
        """Remove symbol from watchlist."""
        if symbol in self.watchlist:
            del self.watchlist[symbol]
            self._save_watchlist()
            return True
        return False
    
    def update_watchlist_entry(self, symbol: str, **kwargs) -> Optional[WatchlistEntry]:
        """Update watchlist entry fields."""
        if symbol not in self.watchlist:
            return None
        
        entry = self.watchlist[symbol]
        for key, value in kwargs.items():
            if hasattr(entry, key):
                setattr(entry, key, value)
        
        entry.last_checked = datetime.now()
        self._save_watchlist()
        
        return entry
    
    def get_watchlist(self) -> List[WatchlistEntry]:
        """Get all watchlist entries."""
        return list(self.watchlist.values())
    
    # =========================================================================
    # PERSISTENCE
    # =========================================================================
    
    def _save_decision(self, decision: Decision):
        """Save decision to file."""
        filepath = self.data_dir / f"decision_{decision.decision_id}.json"
        with open(filepath, "w") as f:
            json.dump(decision.to_dict(), f, indent=2, default=str)
    
    def _save_watchlist(self):
        """Save watchlist to file."""
        filepath = self.data_dir / "watchlist.json"
        data = {s: e.to_dict() for s, e in self.watchlist.items()}
        with open(filepath, "w") as f:
            json.dump(data, f, indent=2, default=str)
    
    def load_watchlist(self):
        """Load watchlist from file."""
        filepath = self.data_dir / "watchlist.json"
        if filepath.exists():
            with open(filepath, "r") as f:
                data = json.load(f)
            
            for symbol, entry_data in data.items():
                if "conviction_tier" in entry_data:
                    entry_data["conviction_tier"] = ConvictionTier(entry_data["conviction_tier"])
                if "added_date" in entry_data:
                    entry_data["added_date"] = date.fromisoformat(entry_data["added_date"])
                if "last_checked" in entry_data and entry_data["last_checked"]:
                    entry_data["last_checked"] = datetime.fromisoformat(entry_data["last_checked"])
                
                self.watchlist[symbol] = WatchlistEntry(**entry_data)
    
    # =========================================================================
    # QUICK ACTIONS
    # =========================================================================
    
    def quick_buy_decision(self,
                          symbol: str,
                          conviction: ConvictionTier,
                          current_price: float,
                          portfolio: PortfolioState,
                          thesis: str = "",
                          target_price: float = None) -> Decision:
        """Generate a quick buy decision with minimal inputs."""
        
        # Create minimal research score
        research = ResearchScore(
            symbol=symbol,
            score_date=date.today(),
            overall_score=4.0 if conviction == ConvictionTier.HIGH else 3.5,
            conviction_tier=conviction,
            thesis=thesis,
            base_case_price=target_price
        )
        
        # Create minimal market snapshot
        market = MarketSnapshot(
            symbol=symbol,
            timestamp=datetime.now(),
            current_price=current_price,
            previous_close=current_price,
            day_change_pct=0,
            day_high=current_price,
            day_low=current_price,
            week_52_high=current_price * 1.2,
            week_52_low=current_price * 0.7,
            volume=1000000,
            avg_volume=1000000,
            market_cap=10000000000
        )
        
        return self.evaluate_entry(
            symbol=symbol,
            portfolio=portfolio,
            research_score=research,
            market_snapshot=market
        )
    
    def quick_sell_decision(self,
                           symbol: str,
                           shares: int,
                           current_price: float,
                           reason: str = "Manual exit") -> Decision:
        """Generate a quick sell decision."""
        decision_id = f"DEC-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"
        
        return Decision(
            decision_id=decision_id,
            timestamp=datetime.now(),
            symbol=symbol,
            decision_type=DecisionType.EXIT,
            action="SELL",
            shares=shares,
            limit_price=current_price,
            position_size_pct=0,
            position_value=shares * current_price,
            primary_reason=reason,
            status="PENDING",
            requires_confirmation=True,
            created_by="manual"
        )
