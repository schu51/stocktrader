"""
Signal Generator
================
Converts research scores, fundamentals, and market data into trading signals.

Momentum-first strategy:
- Momentum is the primary signal (70% weight)
- Trend confirmation (price > 50MA > 200MA) is required for any BUY
- Exit triggered when price crosses below 50MA
- Valuation is not used (momentum stocks are always "expensive")
"""

from datetime import datetime
from typing import Dict, List, Optional, Tuple
import logging

from config import (
    Signal, ConvictionTier, SignalThresholds, DecisionConfig, DEFAULT_CONFIG,
    MomentumConfig, ProductValueOverride
)
from models import (
    TradingSignal, ResearchScore, MarketSnapshot
)
from momentum import MomentumAnalyzer, TrendAnalysis, PriceBar, TrendDirection

logger = logging.getLogger(__name__)

class SignalGenerator:
    """
    Generates trading signals from multiple data sources.

    Signal weights (momentum-first):
    1. Momentum (70%): MACD, RSI, 50/200 MA cross, VWAP
    2. Sentiment (20%): analyst consensus, news, insider activity
    3. Fundamental (10%): research score as a soft quality filter
    4. Valuation (0%): not used - momentum stocks are priced for growth

    Hard entry gate: price > 50MA AND 50MA > 200MA required for BUY signals.
    Hard exit signal: price crosses below 50MA -> warn to review open positions.
    """

    def __init__(self, config: DecisionConfig = None):
        self.config = config or DEFAULT_CONFIG
        self.thresholds = self.config.signal_thresholds
        self.momentum_config = self.config.momentum_config
        self.product_override = self.config.product_override

        self.momentum_analyzer = MomentumAnalyzer({
            "macd_fast": self.momentum_config.macd_fast_period,
            "macd_slow": self.momentum_config.macd_slow_period,
            "macd_signal": self.momentum_config.macd_signal_period,
            "rsi_period": self.momentum_config.rsi_period,
            "trend_confirmation_threshold": self.momentum_config.trend_confirmation_threshold,
        })

        self._last_trend_analysis: Optional[TrendAnalysis] = None

    def generate_signal(self,
                       symbol: str,
                       research_score: Optional[ResearchScore] = None,
                       market_data: Optional[MarketSnapshot] = None,
                       screening_data: Optional[Dict] = None,
                       price_history: Optional[List[PriceBar]] = None) -> TradingSignal:
        """
        Generate trading signal for a symbol.

        Momentum-first: trend confirmation gate is applied before any BUY signal
        is returned. Exit warning is generated when price crosses below 50MA.
        """
        timestamp = datetime.now()
        reasons = []
        warnings = []

        # Component signals
        fundamental_signal, fund_reasons = self._fundamental_signal(research_score)

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
        reasons.extend(mom_reasons)
        reasons.extend(sent_reasons)

        # Composite signal (momentum 70%, sentiment 20%, fundamental 10%)
        composite_signal, strength, confidence = self._composite_signal(
            fundamental_signal,
            momentum_signal,
            sentiment_signal,
            research_score
        )

        # Hard trend gate: block BUY when MA alignment is bearish
        composite_signal = self._apply_trend_gate(composite_signal, market_data, reasons)

        warnings = self._generate_warnings(market_data, screening_data, research_score)

        if self._last_trend_analysis:
            warnings.extend(self._last_trend_analysis.warnings)

        # Exit signal: warn when price crosses below 50MA (review open positions)
        if market_data and market_data.sma_50 and market_data.current_price:
            if market_data.current_price < market_data.sma_50:
                warnings.append("EXIT SIGNAL: Price closed below 50-day MA - review open positions")

        risk_reward = self._calculate_risk_reward(research_score, market_data)

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
            valuation_signal=None,
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

    def _apply_trend_gate(self,
                          signal: Signal,
                          market_data: Optional[MarketSnapshot],
                          reasons: List[str]) -> Signal:
        """
        Hard gate for BUY signals: only allow entry when MA alignment confirms uptrend.

        Rule: price > 50MA AND 50MA > 200MA required for BUY or STRONG_BUY.
        Prevents buying into downtrends regardless of sentiment or fundamentals.
        """
        if signal not in [Signal.BUY, Signal.STRONG_BUY]:
            return signal

        # Use full trend analysis MA cross if available
        if self._last_trend_analysis and self._last_trend_analysis.ma_cross:
            mc = self._last_trend_analysis.ma_cross
            if mc.trend_strength in ["strong_down", "down"]:
                reasons.append("Trend gate: bearish MA alignment - wait for trend reversal")
                return Signal.HOLD
            if mc.trend_strength == "neutral" and mc.price_vs_50 == "below":
                reasons.append("Trend gate: price below 50MA in neutral trend - entry blocked")
                return Signal.HOLD

        # Fallback: raw MA data from market snapshot
        elif market_data and market_data.sma_50 and market_data.current_price:
            if market_data.current_price < market_data.sma_50:
                reasons.append("Trend gate: price below 50-day MA - entry blocked")
                return Signal.HOLD

        return signal

    def _enhanced_momentum_signal(self,
                                  price_history: List[PriceBar],
                                  market_data: Optional[MarketSnapshot]) -> Tuple[Optional[Signal], List[str], TrendAnalysis]:
        """
        Generate enhanced momentum signal using full technical analysis.
        Uses: MACD, RSI, 50/200 MA Cross, Multi-timeframe VWAP.
        Trend strength (60%) weighted higher than price momentum (40%).
        """
        current_price = price_history[-1].close
        if market_data:
            current_price = market_data.current_price

        analysis = self.momentum_analyzer.analyze(
            symbol="",
            price_bars=price_history,
            current_price=current_price
        )

        reasons = []
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

        reasons.extend(analysis.signals[:3])

        return signal, reasons, analysis

    def _fundamental_signal(self,
                            research_score: Optional[ResearchScore]) -> Tuple[Optional[Signal], List[str]]:
        """Generate signal from research score (10% weight - quality filter only)."""
        if not research_score:
            return None, []

        score = research_score.overall_score
        reasons = []

        if score >= self.thresholds.strong_buy_score:
            signal = Signal.STRONG_BUY
            reasons.append(f"Research score {score:.2f} - strong fundamentals")
        elif score >= self.thresholds.buy_score:
            signal = Signal.BUY
            reasons.append(f"Research score {score:.2f} - solid fundamentals")
        elif score >= self.thresholds.hold_score_min:
            signal = Signal.HOLD
            reasons.append(f"Research score {score:.2f} - neutral fundamentals")
        elif score >= self.thresholds.sell_score:
            signal = Signal.SELL
            reasons.append(f"Research score {score:.2f} - weak fundamentals")
        else:
            signal = Signal.STRONG_SELL
            reasons.append(f"Research score {score:.2f} - poor fundamentals")

        return signal, reasons

    def _valuation_signal(self,
                         market_data: Optional[MarketSnapshot],
                         screening_data: Optional[Dict]) -> Tuple[Optional[Signal], List[str]]:
        """
        Valuation signal - not used in momentum strategy (weight = 0).
        Kept for reference; momentum stocks are structurally expensive.
        """
        return None, []

    def _momentum_signal(self,
                        market_data: Optional[MarketSnapshot]) -> Tuple[Optional[Signal], List[str]]:
        """
        Generate momentum signal from MA alignment.

        Entry condition: price > 50MA AND 50MA > 200MA (golden alignment).
        Exit condition: price < 50MA (trend broken).
        Rate-of-change context added from 52-week range position.
        """
        if not market_data:
            return None, []

        reasons = []
        signal = Signal.HOLD

        # Primary: MA alignment
        if market_data.sma_50 and market_data.sma_200:
            price = market_data.current_price
            sma50 = market_data.sma_50
            sma200 = market_data.sma_200

            if price > sma50 and sma50 > sma200:
                signal = Signal.STRONG_BUY
                reasons.append("Uptrend confirmed: price > 50MA > 200MA (golden alignment)")
            elif price > sma50 and sma50 <= sma200:
                signal = Signal.BUY
                reasons.append("Price above 50MA - bullish, but 50MA not yet above 200MA")
            elif price < sma50 and sma50 < sma200:
                signal = Signal.STRONG_SELL
                reasons.append("Downtrend: price < 50MA < 200MA (death cross alignment)")
            else:
                signal = Signal.SELL
                reasons.append("Price below 50-day MA - trend broken, review positions")

        elif market_data.sma_50:
            price = market_data.current_price
            sma50 = market_data.sma_50
            distance_pct = (price / sma50 - 1) * 100

            if price > sma50:
                signal = Signal.BUY
                reasons.append(f"Price {distance_pct:.1f}% above 50-day MA")
            else:
                signal = Signal.SELL
                reasons.append(f"Price {abs(distance_pct):.1f}% below 50-day MA - trend broken")

        # Rate of change context from 52-week range
        if market_data.week_52_high and market_data.week_52_low:
            high = market_data.week_52_high
            low = market_data.week_52_low
            if high > low:
                range_pct = (market_data.current_price - low) / (high - low)
                if range_pct > 0.80:
                    reasons.append("Price in top 20% of 52-week range - strong momentum")
                elif range_pct < 0.20:
                    reasons.append("Price in bottom 20% of 52-week range - weak momentum")

        return signal, reasons

    def _sentiment_signal(self,
                         screening_data: Optional[Dict]) -> Tuple[Optional[Signal], List[str]]:
        """Generate signal from sentiment data (20% weight)."""
        if not screening_data:
            return None, []

        reasons = []
        signal = Signal.HOLD

        unified = screening_data.get("unified", {})
        sentiment = unified.get("sentiment", {})
        analyst = unified.get("analyst", {})

        news_sentiment = sentiment.get("news_sentiment")
        if news_sentiment:
            if news_sentiment >= self.thresholds.bullish_sentiment_threshold:
                signal = Signal.BUY
                reasons.append(f"News sentiment {news_sentiment*100:.0f}% bullish")
            elif news_sentiment <= self.thresholds.bearish_sentiment_threshold:
                signal = Signal.SELL
                reasons.append(f"News sentiment {news_sentiment*100:.0f}% bearish")

        recommendation = analyst.get("recommendation")
        if recommendation:
            if recommendation.lower() in ["strong buy", "buy"]:
                reasons.append(f"Analyst consensus: {recommendation}")
                if signal == Signal.HOLD:
                    signal = Signal.BUY
            elif recommendation.lower() in ["sell", "strong sell"]:
                reasons.append(f"Analyst consensus: {recommendation}")
                signal = Signal.SELL

        bullish_pct = analyst.get("bullish_pct")
        if bullish_pct:
            if bullish_pct >= 70:
                reasons.append(f"{bullish_pct:.0f}% of analysts bullish")
            elif bullish_pct <= 30:
                reasons.append(f"Only {bullish_pct:.0f}% of analysts bullish - caution")

        insider = sentiment.get("insider_sentiment")
        if insider and isinstance(insider, (int, float)):
            if insider > 0:
                reasons.append("Positive insider buying activity")
            elif insider < 0:
                reasons.append("Net insider selling activity")

        return signal, reasons

    def _composite_signal(self,
                         fundamental: Optional[Signal],
                         momentum: Optional[Signal],
                         sentiment: Optional[Signal],
                         research_score: Optional[ResearchScore]) -> Tuple[Signal, float, float]:
        """
        Combine signals into final signal.

        Weights:
        - Momentum:    70%  (primary driver)
        - Sentiment:   20%  (confirmation)
        - Fundamental: 10%  (quality filter)
        - Valuation:    0%  (not used in momentum strategy)
        """
        signal_values = {
            Signal.STRONG_BUY: 2,
            Signal.BUY: 1,
            Signal.HOLD: 0,
            Signal.SELL: -1,
            Signal.STRONG_SELL: -2
        }

        weights = {
            "fundamental": 0.10,
            "momentum":    0.70,
            "sentiment":   0.20
        }

        total_weight = 0
        weighted_score = 0
        signals_present = 0

        if fundamental:
            weighted_score += signal_values[fundamental] * weights["fundamental"]
            total_weight += weights["fundamental"]
            signals_present += 1

        if momentum:
            weighted_score += signal_values[momentum] * weights["momentum"]
            total_weight += weights["momentum"]
            signals_present += 1

        if sentiment:
            weighted_score += signal_values[sentiment] * weights["sentiment"]
            total_weight += weights["sentiment"]
            signals_present += 1

        normalized_score = weighted_score / total_weight if total_weight > 0 else 0

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

        strength = min(abs(normalized_score) / 2, 1.0)

        if signals_present == 0:
            confidence = 0.0
        else:
            agreement = self._calculate_agreement([fundamental, momentum, sentiment])
            data_quality = signals_present / 3

            conviction_boost = 0
            if research_score and research_score.conviction_tier == ConvictionTier.HIGH:
                conviction_boost = 0.05
            elif research_score and research_score.conviction_tier == ConvictionTier.SPECULATIVE:
                conviction_boost = -0.05

            confidence = min(max(0.5 * agreement + 0.5 * data_quality + conviction_boost, 0.1), 1.0)

        return final_signal, round(strength, 3), round(confidence, 3)

    def _calculate_agreement(self, signals: List[Optional[Signal]]) -> float:
        """Calculate agreement ratio among non-None signals."""
        valid_signals = [s for s in signals if s is not None]

        if len(valid_signals) <= 1:
            return 1.0

        bullish = sum(1 for s in valid_signals if s in [Signal.STRONG_BUY, Signal.BUY])
        bearish = sum(1 for s in valid_signals if s in [Signal.STRONG_SELL, Signal.SELL])
        neutral = sum(1 for s in valid_signals if s == Signal.HOLD)

        max_agreement = max(bullish, bearish, neutral)
        return max_agreement / len(valid_signals)

    def _check_product_override_eligibility(self, research: Optional[ResearchScore]) -> bool:
        """Kept for backward compatibility. Not used in momentum strategy."""
        return False

    def _generate_warnings(self,
                          market_data: Optional[MarketSnapshot],
                          screening_data: Optional[Dict],
                          research_score: Optional[ResearchScore]) -> List[str]:
        """Generate warning flags for potential issues."""
        warnings = []

        if market_data:
            if market_data.avg_volume and market_data.avg_volume < 500000:
                warnings.append(f"LOW LIQUIDITY: Average volume {market_data.avg_volume:,} below 500K")

        if research_score:
            if research_score.conviction_tier == ConvictionTier.SPECULATIVE:
                warnings.append("SPECULATIVE: Position sizing should be reduced")
            if research_score.key_risks:
                warnings.append(f"KEY RISKS: {', '.join(research_score.key_risks[:2])}")

        if screening_data:
            unified = screening_data.get("unified", {})
            financial_health = unified.get("financial_health", {})
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

        current_price = market_data.current_price if market_data else None
        if not current_price:
            return None

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
            signal_scores = {
                Signal.STRONG_BUY: 2,
                Signal.BUY: 1,
                Signal.HOLD: 0,
                Signal.SELL: -1,
                Signal.STRONG_SELL: -2
            }
            base = signal_scores.get(signal.signal, 0)
            return base * signal.strength * signal.confidence

        return sorted(signals.items(), key=lambda x: score_signal(x[1]), reverse=True)
