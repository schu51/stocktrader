"""
Signal Generator
================
Converts research scores, fundamentals, and market data into trading signals.
"""

from datetime import datetime
from typing import Dict, List, Optional, Tuple
import logging

from .config import (
    Signal, ConvictionTier, SignalThresholds, DecisionConfig, DEFAULT_CONFIG,
    MomentumConfig, ProductValueOverride
)
from .models import (
    TradingSignal, ResearchScore, MarketSnapshot
)
from .momentum import MomentumAnalyzer, TrendAnalysis, PriceBar, TrendDirection

logger = logging.getLogger(__name__)

class SignalGenerator:
    """
    Generates trading signals from multiple data sources.
    
    Signal components:
    1. Fundamental signal - from research scores
    2. Valuation signal - from P/E, P/S vs targets
    3. Momentum signal - from enhanced technical analysis (MACD, RSI, MA, VWAP)
    4. Sentiment signal - from analyst/news data
    
    Final signal is weighted combination with confidence score.
    
    Enhanced momentum weighting:
    - Trend strength: 60%
    - Price momentum: 40%
    - Weekly/monthly signals prioritized over daily
    """
    
    def __init__(self, config: DecisionConfig = None):
        self.config = config or DEFAULT_CONFIG
        self.thresholds = self.config.signal_thresholds
        self.momentum_config = self.config.momentum_config
        self.product_override = self.config.product_override
        
        # Initialize momentum analyzer with config
        self.momentum_analyzer = MomentumAnalyzer({
            "macd_fast": self.momentum_config.macd_fast_period,
            "macd_slow": self.momentum_config.macd_slow_period,
            "macd_signal": self.momentum_config.macd_signal_period,
            "rsi_period": self.momentum_config.rsi_period,
            "trend_confirmation_threshold": self.momentum_config.trend_confirmation_threshold,
        })
        
        # Store last analysis for decision engine access
        self._last_trend_analysis: Optional[TrendAnalysis] = None
        self._last_falling_knife_score: int = 0
    
    def generate_signal(self,
                       symbol: str,
                       research_score: Optional[ResearchScore] = None,
                       market_data: Optional[MarketSnapshot] = None,
                       screening_data: Optional[Dict] = None,
                       price_history: Optional[List[PriceBar]] = None) -> TradingSignal:
        """
        Generate comprehensive trading signal for a symbol.
        
        Args:
            symbol: Stock ticker
            research_score: Stage 1D research score
            market_data: Current market snapshot
            screening_data: Data from data infrastructure
            price_history: Historical OHLCV data for enhanced momentum (optional)
        
        Returns:
            TradingSignal with signal, strength, and rationale
        """
        timestamp = datetime.now()
        reasons = []
        warnings = []
        
        # Generate component signals
        fundamental_signal, fund_reasons = self._fundamental_signal(research_score)
        valuation_signal, val_reasons = self._valuation_signal(market_data, screening_data)
        
        # Use enhanced momentum if price history available, otherwise fall back to simple
        if price_history and len(price_history) >= 50:
            momentum_signal, mom_reasons, trend_analysis = self._enhanced_momentum_signal(
                price_history, market_data
            )
            self._last_trend_analysis = trend_analysis
        else:
            momentum_signal, mom_reasons = self._momentum_signal(market_data)
            self._last_trend_analysis = None
        
        sentiment_signal, sent_reasons = self._sentiment_signal(screening_data)
        
        reasons.extend(fund_reasons)
        reasons.extend(val_reasons)
        reasons.extend(mom_reasons)
        reasons.extend(sent_reasons)
        
        # Check for product value override eligibility
        override_eligible = self._check_product_override_eligibility(research_score)
        if override_eligible:
            reasons.append("✓ Product value override eligible (exceptional PMF)")
        
        # Calculate composite signal
        composite_signal, strength, confidence = self._composite_signal(
            fundamental_signal,
            valuation_signal,
            momentum_signal,
            sentiment_signal,
            research_score,
            override_eligible
        )
        
        # Generate warnings
        warnings = self._generate_warnings(market_data, screening_data, research_score)
        
        # Add trend-specific warnings
        if self._last_trend_analysis:
            warnings.extend(self._last_trend_analysis.warnings)
        
        # Calculate risk/reward
        risk_reward = self._calculate_risk_reward(research_score, market_data)
        
        # Upside from analyst targets
        upside_pct = None
        if market_data and market_data.analyst_upside_pct:
            upside_pct = market_data.analyst_upside_pct
        elif screening_data:
            upside_pct = screening_data.get("unified", {}).get("analyst", {}).get("upside_pct")
        
        return TradingSignal(
            symbol=symbol,
            signal=composite_signal,
            timestamp=timestamp,
            strength=strength,
            confidence=confidence,
            fundamental_signal=fundamental_signal,
            valuation_signal=valuation_signal,
            momentum_signal=momentum_signal,
            sentiment_signal=sentiment_signal,
            research_score=research_score.overall_score if research_score else None,
            upside_pct=upside_pct,
            risk_reward_ratio=risk_reward,
            reasons=reasons,
            warnings=warnings
        )
    
    def get_trend_analysis(self) -> Optional[TrendAnalysis]:
        """Get the last trend analysis (if enhanced momentum was used)."""
        return self._last_trend_analysis
    
    def _check_product_override_eligibility(self, research: Optional[ResearchScore]) -> bool:
        """
        Check if research score qualifies for product value override.
        
        Override allows entry despite weak trend when product conviction is exceptional.
        """
        if not self.product_override.enabled:
            return False
        
        if not research:
            return False
        
        # Check overall score
        if research.overall_score < self.product_override.min_overall_score:
            return False
        
        # Check product-market fit specifically
        if research.product_market_fit < self.product_override.min_product_market_fit:
            return False
        
        return True
    
    def _enhanced_momentum_signal(self,
                                  price_history: List[PriceBar],
                                  market_data: Optional[MarketSnapshot]) -> Tuple[Optional[Signal], List[str], TrendAnalysis]:
        """
        Generate enhanced momentum signal using full technical analysis.
        
        Uses: MACD, RSI, 50/200 MA Cross, Multi-timeframe VWAP
        Focus: Trend strength (60%) > Price momentum (40%)
        """
        current_price = price_history[-1].close
        if market_data:
            current_price = market_data.current_price
        
        # Run full trend analysis
        analysis = self.momentum_analyzer.analyze(
            symbol="",  # Not needed for analysis
            price_bars=price_history,
            current_price=current_price
        )
        
        reasons = []
        
        # Map composite score to signal
        score = analysis.composite_score
        
        if score >= 70:
            signal = Signal.STRONG_BUY
            reasons.append(f"Strong uptrend (momentum score: {score:.0f}/100)")
        elif score >= 55:
            signal = Signal.BUY
            reasons.append(f"Uptrend confirmed (momentum score: {score:.0f}/100)")
        elif score >= 45:
            signal = Signal.HOLD
            reasons.append(f"Neutral trend (momentum score: {score:.0f}/100)")
        elif score >= 30:
            signal = Signal.SELL
            reasons.append(f"Downtrend detected (momentum score: {score:.0f}/100)")
        else:
            signal = Signal.STRONG_SELL
            reasons.append(f"Strong downtrend (momentum score: {score:.0f}/100)")
        
        # Add specific indicator signals
        reasons.extend(analysis.signals[:3])  # Top 3 signals
        
        # Calculate falling knife score for composite signal
        self._last_falling_knife_score = 0
        if analysis.trend_direction == TrendDirection.STRONG_DOWN:
            self._last_falling_knife_score = 3
        elif analysis.trend_direction == TrendDirection.DOWN:
            self._last_falling_knife_score = 2
        
        return signal, reasons, analysis
    
    def _fundamental_signal(self, 
                           research_score: Optional[ResearchScore]) -> Tuple[Optional[Signal], List[str]]:
        """Generate signal from research score."""
        if not research_score:
            return None, []
        
        score = research_score.overall_score
        reasons = []
        
        if score >= self.thresholds.strong_buy_score:
            signal = Signal.STRONG_BUY
            reasons.append(f"Research score {score:.2f} indicates strong buy (>={self.thresholds.strong_buy_score})")
        elif score >= self.thresholds.buy_score:
            signal = Signal.BUY
            reasons.append(f"Research score {score:.2f} indicates buy (>={self.thresholds.buy_score})")
        elif score >= self.thresholds.hold_score_min:
            signal = Signal.HOLD
            reasons.append(f"Research score {score:.2f} indicates hold")
        elif score >= self.thresholds.sell_score:
            signal = Signal.SELL
            reasons.append(f"Research score {score:.2f} indicates sell (<{self.thresholds.hold_score_min})")
        else:
            signal = Signal.STRONG_SELL
            reasons.append(f"Research score {score:.2f} indicates strong sell (<{self.thresholds.sell_score})")
        
        # Add conviction tier context
        if research_score.conviction_tier == ConvictionTier.HIGH:
            reasons.append(f"HIGH conviction tier supports signal strength")
        elif research_score.conviction_tier == ConvictionTier.SPECULATIVE:
            reasons.append(f"SPECULATIVE conviction tier - increased uncertainty")
        
        return signal, reasons
    
    def _valuation_signal(self,
                         market_data: Optional[MarketSnapshot],
                         screening_data: Optional[Dict]) -> Tuple[Optional[Signal], List[str]]:
        """Generate signal from valuation metrics."""
        reasons = []
        
        # Get valuation data
        pe_ratio = None
        ps_ratio = None
        upside_pct = None
        
        if market_data:
            pe_ratio = market_data.pe_ratio
            ps_ratio = market_data.ps_ratio
            upside_pct = market_data.analyst_upside_pct
        
        if screening_data:
            unified = screening_data.get("unified", {})
            val = unified.get("valuation", {})
            analyst = unified.get("analyst", {})
            
            pe_ratio = pe_ratio or val.get("pe_ratio")
            ps_ratio = ps_ratio or val.get("ps_ratio")
            upside_pct = upside_pct or analyst.get("upside_pct")
        
        if upside_pct is None:
            return None, []
        
        # Signal based on analyst upside
        if upside_pct >= self.thresholds.strong_buy_upside * 100:
            signal = Signal.STRONG_BUY
            reasons.append(f"Analyst upside {upside_pct:.1f}% suggests strong undervaluation")
        elif upside_pct >= self.thresholds.buy_upside * 100:
            signal = Signal.BUY
            reasons.append(f"Analyst upside {upside_pct:.1f}% suggests undervaluation")
        elif upside_pct >= self.thresholds.sell_downside * 100:
            signal = Signal.HOLD
            reasons.append(f"Analyst upside {upside_pct:.1f}% suggests fair value")
        else:
            signal = Signal.SELL
            reasons.append(f"Analyst upside {upside_pct:.1f}% suggests overvaluation")
        
        # Add P/E context
        if pe_ratio:
            if pe_ratio > 100:
                reasons.append(f"P/E {pe_ratio:.1f}x is elevated - valuation risk")
            elif pe_ratio < 20 and pe_ratio > 0:
                reasons.append(f"P/E {pe_ratio:.1f}x is attractive")
        
        return signal, reasons
    
    def _momentum_signal(self,
                        market_data: Optional[MarketSnapshot]) -> Tuple[Optional[Signal], List[str]]:
        """Generate signal from price momentum with falling knife detection."""
        if not market_data:
            return None, []
        
        reasons = []
        signal = Signal.HOLD
        falling_knife_score = 0  # Track severity of downtrend
        
        # 52-week range position
        if market_data.week_52_high and market_data.week_52_low:
            current = market_data.current_price
            high = market_data.week_52_high
            low = market_data.week_52_low
            
            if high > low:
                range_pct = (current - low) / (high - low)
                pct_from_high = (high - current) / high
                
                # FALLING KNIFE DETECTION
                # If price is >35% below 52-week high, this is a severe downtrend
                if pct_from_high > 0.50:
                    signal = Signal.STRONG_SELL
                    falling_knife_score += 3
                    reasons.append(f"⚠️ FALLING KNIFE: Price {pct_from_high*100:.0f}% below 52-week high")
                elif pct_from_high > 0.35:
                    signal = Signal.SELL
                    falling_knife_score += 2
                    reasons.append(f"⚠️ SEVERE DOWNTREND: Price {pct_from_high*100:.0f}% below 52-week high")
                elif pct_from_high > 0.25:
                    signal = Signal.SELL
                    falling_knife_score += 1
                    reasons.append(f"DOWNTREND: Price {pct_from_high*100:.0f}% below 52-week high")
                elif range_pct < 0.30:
                    # Only consider "value" if not in severe downtrend
                    signal = Signal.HOLD  # Neutral, not bullish - need confirmation
                    reasons.append(f"Price in lower 30% of range - needs trend confirmation")
                elif range_pct > 0.70:
                    signal = Signal.BUY
                    reasons.append(f"Price showing strength near 52-week high")
                else:
                    signal = Signal.HOLD
                    reasons.append(f"Price in middle of 52-week range")
        
        # Moving average signals (if available)
        if market_data.sma_50 and market_data.current_price:
            ma_distance = (market_data.current_price / market_data.sma_50 - 1)
            
            if ma_distance > self.thresholds.momentum_buy_threshold:
                reasons.append(f"Price {ma_distance*100:.1f}% above 50-day MA - positive momentum")
                if signal == Signal.HOLD:
                    signal = Signal.BUY
            elif ma_distance < -0.15:  # More than 15% below 50-day MA
                falling_knife_score += 1
                reasons.append(f"Price {abs(ma_distance)*100:.1f}% below 50-day MA - negative momentum")
                if signal in [Signal.HOLD, Signal.BUY]:
                    signal = Signal.SELL
            elif ma_distance < self.thresholds.momentum_sell_threshold:
                reasons.append(f"Price {abs(ma_distance)*100:.1f}% below 50-day MA")
        
        # 200-day MA check for trend confirmation
        if market_data.sma_200 and market_data.current_price:
            ma200_distance = (market_data.current_price / market_data.sma_200 - 1)
            
            if ma200_distance < -0.20:  # 20% below 200-day MA
                falling_knife_score += 1
                reasons.append(f"Price {abs(ma200_distance)*100:.1f}% below 200-day MA - long-term downtrend")
        
        # Store falling knife score for use in composite signal
        self._last_falling_knife_score = falling_knife_score
        
        return signal, reasons
    
    def _sentiment_signal(self,
                         screening_data: Optional[Dict]) -> Tuple[Optional[Signal], List[str]]:
        """Generate signal from sentiment data."""
        if not screening_data:
            return None, []
        
        reasons = []
        signal = Signal.HOLD
        
        unified = screening_data.get("unified", {})
        sentiment = unified.get("sentiment", {})
        analyst = unified.get("analyst", {})
        
        # News sentiment
        news_sentiment = sentiment.get("news_sentiment")
        if news_sentiment:
            if news_sentiment >= self.thresholds.bullish_sentiment_threshold:
                signal = Signal.BUY
                reasons.append(f"News sentiment {news_sentiment*100:.0f}% bullish")
            elif news_sentiment <= self.thresholds.bearish_sentiment_threshold:
                signal = Signal.SELL
                reasons.append(f"News sentiment {news_sentiment*100:.0f}% bearish")
        
        # Analyst consensus
        recommendation = analyst.get("recommendation")
        if recommendation:
            if recommendation.lower() in ["strong buy", "buy"]:
                reasons.append(f"Analyst consensus: {recommendation}")
                if signal == Signal.HOLD:
                    signal = Signal.BUY
            elif recommendation.lower() in ["sell", "strong sell"]:
                reasons.append(f"Analyst consensus: {recommendation}")
                signal = Signal.SELL
        
        # Bullish percentage
        bullish_pct = analyst.get("bullish_pct")
        if bullish_pct:
            if bullish_pct >= 70:
                reasons.append(f"{bullish_pct:.0f}% of analysts bullish")
            elif bullish_pct <= 30:
                reasons.append(f"Only {bullish_pct:.0f}% of analysts bullish - caution")
        
        # Insider sentiment
        insider = sentiment.get("insider_sentiment")
        if insider and isinstance(insider, (int, float)):
            if insider > 0:
                reasons.append("Positive insider buying activity")
            elif insider < 0:
                reasons.append("Net insider selling activity")
        
        return signal, reasons
    
    def _composite_signal(self,
                         fundamental: Optional[Signal],
                         valuation: Optional[Signal],
                         momentum: Optional[Signal],
                         sentiment: Optional[Signal],
                         research_score: Optional[ResearchScore],
                         override_eligible: bool = False) -> Tuple[Signal, float, float]:
        """
        Combine component signals into final signal with strength and confidence.
        
        Base Weighting:
        - Fundamental (research score): 40%
        - Valuation: 30%
        - Momentum: 15%
        - Sentiment: 15%
        
        DYNAMIC ADJUSTMENT: When momentum is strongly negative (falling knife),
        increase momentum weight to prevent buying into severe downtrends.
        
        PRODUCT OVERRIDE: If eligible, reduce momentum's veto power.
        """
        # Signal values for scoring
        signal_values = {
            Signal.STRONG_BUY: 2,
            Signal.BUY: 1,
            Signal.HOLD: 0,
            Signal.SELL: -1,
            Signal.STRONG_SELL: -2
        }
        
        # Base weights
        weights = {
            "fundamental": 0.40,
            "valuation": 0.30,
            "momentum": 0.15,
            "sentiment": 0.15
        }
        
        # DYNAMIC WEIGHT ADJUSTMENT FOR FALLING KNIVES
        # If momentum is strongly negative, it should have more say
        falling_knife_score = getattr(self, '_last_falling_knife_score', 0)
        
        # Product override reduces the falling knife penalty
        if override_eligible and falling_knife_score >= 2:
            falling_knife_score = max(1, falling_knife_score - 1)  # Reduce severity by 1
        
        if momentum in [Signal.SELL, Signal.STRONG_SELL] and falling_knife_score >= 2:
            # Severe downtrend: momentum gets veto power
            # Increase momentum weight, decrease others proportionally
            momentum_boost = min(0.25, falling_knife_score * 0.10)  # Up to 0.25 boost
            weights["momentum"] = 0.15 + momentum_boost  # Up to 0.40
            
            # Reduce other weights proportionally
            reduction = momentum_boost / 3
            weights["fundamental"] -= reduction
            weights["valuation"] -= reduction
            weights["sentiment"] -= reduction
        
        # Calculate weighted score
        total_weight = 0
        weighted_score = 0
        signals_present = 0
        
        if fundamental:
            weighted_score += signal_values[fundamental] * weights["fundamental"]
            total_weight += weights["fundamental"]
            signals_present += 1
        
        if valuation:
            weighted_score += signal_values[valuation] * weights["valuation"]
            total_weight += weights["valuation"]
            signals_present += 1
        
        if momentum:
            weighted_score += signal_values[momentum] * weights["momentum"]
            total_weight += weights["momentum"]
            signals_present += 1
        
        if sentiment:
            weighted_score += signal_values[sentiment] * weights["sentiment"]
            total_weight += weights["sentiment"]
            signals_present += 1
        
        # Normalize
        if total_weight > 0:
            normalized_score = weighted_score / total_weight
        else:
            normalized_score = 0
        
        # FALLING KNIFE OVERRIDE
        # Even if score is positive, severe downtrends should cap the signal
        # Unless product override is active
        if not override_eligible:
            if falling_knife_score >= 3 and normalized_score > 0:
                normalized_score = min(normalized_score, -0.5)  # Force to SELL at minimum
            elif falling_knife_score >= 2 and normalized_score > 0.5:
                normalized_score = min(normalized_score, 0)  # Cap at HOLD
        else:
            # With override: less aggressive capping
            if falling_knife_score >= 3 and normalized_score > 0.5:
                normalized_score = min(normalized_score, 0.5)  # Cap at BUY instead of SELL
        
        # Convert back to signal
        if normalized_score >= 1.5:
            final_signal = Signal.STRONG_BUY
        elif normalized_score >= 0.5:
            final_signal = Signal.BUY
        elif normalized_score >= -0.5:
            final_signal = Signal.HOLD
        elif normalized_score >= -1.5:
            final_signal = Signal.SELL
        else:
            final_signal = Signal.STRONG_SELL
        
        # Strength (0-1): How extreme is the signal
        strength = min(abs(normalized_score) / 2, 1.0)
        
        # Confidence (0-1): Based on signal agreement and data quality
        if signals_present == 0:
            confidence = 0.0
        else:
            # Base confidence on number of signals agreeing
            agreement = self._calculate_agreement([fundamental, valuation, momentum, sentiment])
            data_quality = signals_present / 4
            
            # Boost confidence if research score is high conviction
            conviction_boost = 0
            if research_score and research_score.conviction_tier == ConvictionTier.HIGH:
                conviction_boost = 0.1
            elif research_score and research_score.conviction_tier == ConvictionTier.SPECULATIVE:
                conviction_boost = -0.1
            
            # REDUCE confidence if signals disagree significantly (falling knife)
            if falling_knife_score >= 2:
                conviction_boost -= 0.15
            
            # Boost confidence if override eligible (high product conviction)
            if override_eligible:
                conviction_boost += 0.10
            
            confidence = min(max(0.5 * agreement + 0.5 * data_quality + conviction_boost, 0.1), 1.0)
        
        return final_signal, round(strength, 3), round(confidence, 3)
    
    def _calculate_agreement(self, signals: List[Optional[Signal]]) -> float:
        """Calculate agreement ratio among non-None signals."""
        valid_signals = [s for s in signals if s is not None]
        
        if len(valid_signals) <= 1:
            return 1.0
        
        # Count signals in each direction
        bullish = sum(1 for s in valid_signals if s in [Signal.STRONG_BUY, Signal.BUY])
        bearish = sum(1 for s in valid_signals if s in [Signal.STRONG_SELL, Signal.SELL])
        neutral = sum(1 for s in valid_signals if s == Signal.HOLD)
        
        # Agreement is the max faction
        max_agreement = max(bullish, bearish, neutral)
        return max_agreement / len(valid_signals)
    
    def _generate_warnings(self,
                          market_data: Optional[MarketSnapshot],
                          screening_data: Optional[Dict],
                          research_score: Optional[ResearchScore]) -> List[str]:
        """Generate warning flags for potential issues."""
        warnings = []
        
        # Valuation warnings
        if market_data:
            if market_data.pe_ratio and market_data.pe_ratio > 100:
                warnings.append(f"HIGH VALUATION: P/E ratio {market_data.pe_ratio:.1f}x exceeds 100")
            
            if market_data.ps_ratio and market_data.ps_ratio > 20:
                warnings.append(f"HIGH P/S: Price/Sales {market_data.ps_ratio:.1f}x exceeds 20")
        
        # Liquidity warnings
        if market_data:
            if market_data.avg_volume and market_data.avg_volume < 500000:
                warnings.append(f"LOW LIQUIDITY: Average volume {market_data.avg_volume:,} below 500K")
        
        # Research score warnings
        if research_score:
            if research_score.conviction_tier == ConvictionTier.SPECULATIVE:
                warnings.append("SPECULATIVE: Position sizing should be reduced")
            
            if research_score.key_risks:
                warnings.append(f"KEY RISKS: {', '.join(research_score.key_risks[:2])}")
        
        # Screening data warnings
        if screening_data:
            unified = screening_data.get("unified", {})
            quality = unified.get("quality", {})
            financial_health = unified.get("financial_health", {})
            
            # Margin warnings
            gross_margin = quality.get("gross_margin")
            if gross_margin and gross_margin < 0.30:
                warnings.append(f"LOW GROSS MARGIN: {gross_margin*100:.1f}% below 30%")
            
            # Debt warnings
            debt_equity = financial_health.get("debt_to_equity")
            if debt_equity and debt_equity > 2:
                warnings.append(f"HIGH LEVERAGE: Debt/Equity {debt_equity:.1f}x exceeds 2")
        
        return warnings
    
    def _calculate_risk_reward(self,
                              research_score: Optional[ResearchScore],
                              market_data: Optional[MarketSnapshot]) -> Optional[float]:
        """Calculate risk/reward ratio from scenario analysis."""
        if not research_score:
            return None
        
        current_price = None
        if market_data:
            current_price = market_data.current_price
        
        if not current_price:
            return None
        
        # Use scenario prices
        if research_score.base_case_price and research_score.bear_case_price:
            upside = research_score.base_case_price - current_price
            downside = current_price - research_score.bear_case_price
            
            if downside > 0:
                return round(upside / downside, 2)
        
        return None
    
    def generate_signals_batch(self,
                              symbols: List[str],
                              research_scores: Dict[str, ResearchScore] = None,
                              market_data: Dict[str, MarketSnapshot] = None,
                              screening_data: Dict[str, Dict] = None) -> Dict[str, TradingSignal]:
        """Generate signals for multiple symbols."""
        research_scores = research_scores or {}
        market_data = market_data or {}
        screening_data = screening_data or {}
        
        signals = {}
        for symbol in symbols:
            signals[symbol] = self.generate_signal(
                symbol=symbol,
                research_score=research_scores.get(symbol),
                market_data=market_data.get(symbol),
                screening_data=screening_data.get(symbol)
            )
        
        return signals
    
    def rank_signals(self, signals: Dict[str, TradingSignal]) -> List[Tuple[str, TradingSignal]]:
        """Rank signals by strength and confidence."""
        
        def score_signal(signal: TradingSignal) -> float:
            # Base score from signal type
            signal_scores = {
                Signal.STRONG_BUY: 2,
                Signal.BUY: 1,
                Signal.HOLD: 0,
                Signal.SELL: -1,
                Signal.STRONG_SELL: -2
            }
            base = signal_scores.get(signal.signal, 0)
            
            # Weight by strength and confidence
            return base * signal.strength * signal.confidence
        
        ranked = sorted(
            signals.items(),
            key=lambda x: score_signal(x[1]),
            reverse=True
        )
        
        return ranked
