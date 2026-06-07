"""
Enhanced Momentum & Trend Analysis Module
==========================================

Provides comprehensive technical analysis for trend strength and price momentum.

Indicators (in priority order per user preference):
1. MACD - Trend direction and momentum
2. RSI - Overbought/oversold, momentum confirmation
3. 50/200 MA Cross - Long-term trend (golden/death cross)
4. VWAP - Volume-weighted price levels (daily, weekly, monthly)

Design Philosophy:
- Trend strength weighted higher than price momentum (60/40)
- Weekly/monthly signals more important than daily
- Requires trend confirmation for entry (unless product value override)
"""

from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Tuple
from enum import Enum
import math

class TrendDirection(Enum):
    """Overall trend direction."""
    STRONG_UP = "STRONG_UP"
    UP = "UP"
    NEUTRAL = "NEUTRAL"
    DOWN = "DOWN"
    STRONG_DOWN = "STRONG_DOWN"

class CrossoverType(Enum):
    """MA crossover types."""
    GOLDEN_CROSS = "GOLDEN_CROSS"      # 50 crosses above 200
    DEATH_CROSS = "DEATH_CROSS"        # 50 crosses below 200
    NONE = "NONE"

@dataclass
class PriceBar:
    """OHLCV price bar."""
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: int
    
    @property
    def typical_price(self) -> float:
        """(High + Low + Close) / 3"""
        return (self.high + self.low + self.close) / 3
    
    @property
    def vwap_numerator(self) -> float:
        """Typical price * volume for VWAP calculation."""
        return self.typical_price * self.volume

@dataclass
class MACDResult:
    """MACD indicator values."""
    macd_line: float          # MACD line (fast EMA - slow EMA)
    signal_line: float        # Signal line (EMA of MACD)
    histogram: float          # MACD - Signal
    histogram_direction: str  # "expanding", "contracting", "flat"
    trend_signal: str         # "bullish", "bearish", "neutral"
    crossover: Optional[str]  # "bullish_cross", "bearish_cross", None
    
    def score(self) -> float:
        """Score from 0-100 for trend strength."""
        score = 50  # Neutral base
        
        # Histogram position (+/- 20 points)
        if self.histogram > 0:
            score += min(20, self.histogram * 100)  # Cap at +20
        else:
            score += max(-20, self.histogram * 100)  # Cap at -20
        
        # Histogram direction (+/- 15 points)
        if self.histogram_direction == "expanding" and self.histogram > 0:
            score += 15
        elif self.histogram_direction == "expanding" and self.histogram < 0:
            score -= 15
        elif self.histogram_direction == "contracting" and self.histogram > 0:
            score -= 5  # Losing momentum
        elif self.histogram_direction == "contracting" and self.histogram < 0:
            score += 5  # Bearish momentum fading
        
        # Crossover (+/- 15 points)
        if self.crossover == "bullish_cross":
            score += 15
        elif self.crossover == "bearish_cross":
            score -= 15
        
        return max(0, min(100, score))

@dataclass
class RSIResult:
    """RSI indicator values."""
    rsi: float
    condition: str           # "oversold", "neutral", "overbought"
    trend_alignment: str     # "bullish", "bearish", "neutral"
    divergence: Optional[str]  # "bullish_div", "bearish_div", None
    
    def score(self) -> float:
        """Score from 0-100 for momentum health."""
        # For uptrend: RSI 40-70 is healthy
        # Oversold (<30) can be opportunity OR falling knife
        # Overbought (>70) can be strong momentum OR exhaustion
        
        if 50 <= self.rsi <= 65:
            # Sweet spot for uptrend momentum
            return 75
        elif 40 <= self.rsi < 50:
            # Acceptable, slight caution
            return 60
        elif 65 < self.rsi <= 70:
            # Strong but getting extended
            return 65
        elif 70 < self.rsi <= 80:
            # Overbought but could continue
            return 50
        elif self.rsi > 80:
            # Severely overbought
            return 30
        elif 30 <= self.rsi < 40:
            # Weak but not oversold
            return 40
        elif 20 <= self.rsi < 30:
            # Oversold - could bounce or continue falling
            return 35
        else:  # < 20
            # Severely oversold - high risk
            return 25

@dataclass 
class MACrossResult:
    """Moving average crossover analysis."""
    price: float
    sma_50: float
    sma_200: float
    price_vs_50: str        # "above", "below"
    price_vs_200: str       # "above", "below"
    ma_50_vs_200: str       # "above", "below"
    crossover_type: CrossoverType
    days_since_crossover: Optional[int]
    trend_strength: str     # "strong_up", "up", "neutral", "down", "strong_down"
    
    def score(self) -> float:
        """Score from 0-100 for trend position."""
        score = 50
        
        # Price vs 50-day MA (+/- 15 points)
        if self.price_vs_50 == "above":
            score += 15
        else:
            score -= 15
        
        # Price vs 200-day MA (+/- 20 points)
        if self.price_vs_200 == "above":
            score += 20
        else:
            score -= 20
        
        # 50 vs 200 (golden/death cross) (+/- 15 points)
        if self.ma_50_vs_200 == "above":
            score += 15
        else:
            score -= 15
        
        # Recent crossover bonus/penalty
        if self.crossover_type == CrossoverType.GOLDEN_CROSS:
            if self.days_since_crossover and self.days_since_crossover <= 20:
                score += 10  # Recent golden cross is bullish
        elif self.crossover_type == CrossoverType.DEATH_CROSS:
            if self.days_since_crossover and self.days_since_crossover <= 20:
                score -= 10  # Recent death cross is bearish
        
        return max(0, min(100, score))

@dataclass
class VWAPResult:
    """Multi-timeframe VWAP analysis."""
    current_price: float
    daily_vwap: Optional[float]
    weekly_vwap: Optional[float]
    monthly_vwap: Optional[float]
    
    price_vs_daily: Optional[str]    # "above", "below", None
    price_vs_weekly: Optional[str]
    price_vs_monthly: Optional[str]
    
    daily_distance_pct: Optional[float]
    weekly_distance_pct: Optional[float]
    monthly_distance_pct: Optional[float]
    
    def score(self) -> float:
        """
        Score from 0-100 for VWAP position.
        Weekly and monthly weighted more heavily.
        """
        score = 50
        weights_applied = 0
        
        # Daily VWAP (weight: 20%)
        if self.price_vs_daily:
            weights_applied += 0.20
            if self.price_vs_daily == "above":
                score += 10 * 0.20
            else:
                score -= 10 * 0.20
        
        # Weekly VWAP (weight: 35%)
        if self.price_vs_weekly:
            weights_applied += 0.35
            if self.price_vs_weekly == "above":
                score += 25 * 0.35
            else:
                score -= 25 * 0.35
        
        # Monthly VWAP (weight: 45%)
        if self.price_vs_monthly:
            weights_applied += 0.45
            if self.price_vs_monthly == "above":
                score += 25 * 0.45
            else:
                score -= 25 * 0.45
        
        # Normalize if not all VWAPs available
        if weights_applied > 0 and weights_applied < 1:
            adjustment = (score - 50) / weights_applied
            score = 50 + adjustment
        
        return max(0, min(100, score))

@dataclass
class TrendAnalysis:
    """Complete trend and momentum analysis."""
    symbol: str
    timestamp: datetime
    
    # Individual indicator results
    macd: Optional[MACDResult] = None
    rsi: Optional[RSIResult] = None
    ma_cross: Optional[MACrossResult] = None
    vwap: Optional[VWAPResult] = None
    
    # Composite scores
    trend_strength_score: float = 50.0    # 0-100
    price_momentum_score: float = 50.0    # 0-100
    composite_score: float = 50.0         # 0-100 (weighted combination)
    
    # Overall assessment
    trend_direction: TrendDirection = TrendDirection.NEUTRAL
    trend_confirmed: bool = False
    
    # Signals
    signals: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat(),
            "trend_strength_score": self.trend_strength_score,
            "price_momentum_score": self.price_momentum_score,
            "composite_score": self.composite_score,
            "trend_direction": self.trend_direction.value,
            "trend_confirmed": self.trend_confirmed,
            "signals": self.signals,
            "warnings": self.warnings,
            "indicators": {
                "macd_score": self.macd.score() if self.macd else None,
                "rsi_score": self.rsi.score() if self.rsi else None,
                "ma_cross_score": self.ma_cross.score() if self.ma_cross else None,
                "vwap_score": self.vwap.score() if self.vwap else None,
            }
        }


class MomentumAnalyzer:
    """
    Comprehensive momentum and trend analyzer.
    
    Usage:
        analyzer = MomentumAnalyzer()
        
        # From price history
        analysis = analyzer.analyze(symbol, price_bars)
        
        # Check if trend is confirmed for entry
        if analysis.trend_confirmed:
            print("OK to enter")
        
        # Get composite score
        print(f"Trend score: {analysis.composite_score}")
    """
    
    def __init__(self, config: Dict = None):
        """
        Initialize with optional configuration.
        
        Config options:
            macd_fast: Fast EMA period (default: 12)
            macd_slow: Slow EMA period (default: 26)
            macd_signal: Signal line period (default: 9)
            rsi_period: RSI lookback (default: 14)
            trend_confirmation_threshold: Min score to confirm trend (default: 45)
        """
        config = config or {}
        
        # MACD parameters
        self.macd_fast = config.get("macd_fast", 12)
        self.macd_slow = config.get("macd_slow", 26)
        self.macd_signal = config.get("macd_signal", 9)
        
        # RSI parameters
        self.rsi_period = config.get("rsi_period", 14)
        
        # Trend confirmation
        self.trend_confirmation_threshold = config.get("trend_confirmation_threshold", 45)
        
        # Weights for composite score (trend strength vs price momentum)
        self.trend_weight = 0.60
        self.momentum_weight = 0.40
    
    def analyze(self, 
                symbol: str, 
                price_bars: List[PriceBar],
                current_price: float = None) -> TrendAnalysis:
        """
        Perform complete trend and momentum analysis.
        
        Args:
            symbol: Stock ticker
            price_bars: List of PriceBar objects (oldest first)
            current_price: Current price (uses last bar's close if not provided)
        
        Returns:
            TrendAnalysis with all indicators and scores
        """
        if not price_bars:
            return TrendAnalysis(
                symbol=symbol,
                timestamp=datetime.now(),
                warnings=["No price data available"]
            )
        
        # Use current price or last close
        current_price = current_price or price_bars[-1].close
        closes = [bar.close for bar in price_bars]
        
        # Calculate indicators
        macd_result = self._calculate_macd(closes)
        rsi_result = self._calculate_rsi(closes)
        ma_cross_result = self._calculate_ma_cross(closes, current_price)
        vwap_result = self._calculate_vwap(price_bars, current_price)
        
        # Calculate composite scores
        trend_strength = self._calculate_trend_strength(macd_result, ma_cross_result, vwap_result)
        price_momentum = self._calculate_price_momentum(rsi_result, closes)
        
        composite = (trend_strength * self.trend_weight + 
                    price_momentum * self.momentum_weight)
        
        # Determine trend direction
        trend_direction = self._determine_trend_direction(composite)
        
        # Check trend confirmation
        trend_confirmed = composite >= self.trend_confirmation_threshold
        
        # Generate signals and warnings
        signals, warnings = self._generate_signals(
            macd_result, rsi_result, ma_cross_result, vwap_result, composite
        )
        
        return TrendAnalysis(
            symbol=symbol,
            timestamp=datetime.now(),
            macd=macd_result,
            rsi=rsi_result,
            ma_cross=ma_cross_result,
            vwap=vwap_result,
            trend_strength_score=round(trend_strength, 1),
            price_momentum_score=round(price_momentum, 1),
            composite_score=round(composite, 1),
            trend_direction=trend_direction,
            trend_confirmed=trend_confirmed,
            signals=signals,
            warnings=warnings
        )
    
    def _calculate_ema(self, data: List[float], period: int) -> List[float]:
        """
        Calculate EMA, returning a list the same length as data.
        The first (period-1) values are NaN — callers must account for this.
        """
        if len(data) < period:
            return [float('nan')] * len(data)

        multiplier = 2 / (period + 1)
        result = [float('nan')] * (period - 1)
        result.append(sum(data[:period]) / period)  # seed with SMA

        for price in data[period:]:
            result.append((price - result[-1]) * multiplier + result[-1])

        return result
    
    def _calculate_sma(self, data: List[float], period: int) -> List[float]:
        """Calculate Simple Moving Average."""
        if len(data) < period:
            return []
        
        sma = []
        for i in range(period - 1, len(data)):
            sma.append(sum(data[i - period + 1:i + 1]) / period)
        
        return sma
    
    def _calculate_macd(self, closes: List[float]) -> Optional[MACDResult]:
        """Calculate MACD indicator."""
        if len(closes) < self.macd_slow + self.macd_signal:
            return None
        
        # Calculate full-length EMAs (same length as closes, NaN for warm-up bars)
        fast_ema = self._calculate_ema(closes, self.macd_fast)
        slow_ema = self._calculate_ema(closes, self.macd_slow)

        if not fast_ema or not slow_ema:
            return None

        # MACD line: valid only where both EMAs are non-NaN (i.e. from slow period onward)
        import math
        macd_line = [
            f - s
            for f, s in zip(fast_ema, slow_ema)
            if not (math.isnan(f) or math.isnan(s))
        ]

        if not macd_line:
            return None

        # Calculate signal line (EMA of MACD line)
        signal_line = self._calculate_ema(macd_line, self.macd_signal)

        # Drop NaN warm-up from signal line
        signal_valid = [x for x in signal_line if not math.isnan(x)]

        if not signal_valid:
            return None

        # Current values — use last valid entries
        current_macd     = macd_line[-1]
        current_signal   = signal_valid[-1]
        current_histogram = current_macd - current_signal

        # Previous histogram for direction
        if len(macd_line) > 1 and len(signal_valid) > 1:
            prev_histogram = macd_line[-2] - signal_valid[-2]
            
            if abs(current_histogram) > abs(prev_histogram):
                histogram_direction = "expanding"
            elif abs(current_histogram) < abs(prev_histogram):
                histogram_direction = "contracting"
            else:
                histogram_direction = "flat"
        else:
            histogram_direction = "flat"
            prev_histogram = 0
        
        # Trend signal
        if current_histogram > 0 and histogram_direction == "expanding":
            trend_signal = "bullish"
        elif current_histogram < 0 and histogram_direction == "expanding":
            trend_signal = "bearish"
        else:
            trend_signal = "neutral"
        
        # Check for crossover
        crossover = None
        if len(macd_line) >= 2 and len(signal_line) >= 2:
            prev_macd = macd_line[-2]
            prev_signal = signal_line[-2]
            
            if prev_macd <= prev_signal and current_macd > current_signal:
                crossover = "bullish_cross"
            elif prev_macd >= prev_signal and current_macd < current_signal:
                crossover = "bearish_cross"
        
        return MACDResult(
            macd_line=round(current_macd, 4),
            signal_line=round(current_signal, 4),
            histogram=round(current_histogram, 4),
            histogram_direction=histogram_direction,
            trend_signal=trend_signal,
            crossover=crossover
        )
    
    def _calculate_rsi(self, closes: List[float]) -> Optional[RSIResult]:
        """Calculate RSI indicator."""
        if len(closes) < self.rsi_period + 1:
            return None
        
        # Calculate price changes
        changes = [closes[i] - closes[i-1] for i in range(1, len(closes))]
        
        # Separate gains and losses
        gains = [max(0, c) for c in changes]
        losses = [abs(min(0, c)) for c in changes]
        
        # Calculate average gain/loss (Wilder's smoothing)
        avg_gain = sum(gains[:self.rsi_period]) / self.rsi_period
        avg_loss = sum(losses[:self.rsi_period]) / self.rsi_period
        
        for i in range(self.rsi_period, len(gains)):
            avg_gain = (avg_gain * (self.rsi_period - 1) + gains[i]) / self.rsi_period
            avg_loss = (avg_loss * (self.rsi_period - 1) + losses[i]) / self.rsi_period
        
        # Calculate RSI
        if avg_loss == 0:
            rsi = 100
        else:
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
        
        # Determine condition
        if rsi < 30:
            condition = "oversold"
        elif rsi > 70:
            condition = "overbought"
        else:
            condition = "neutral"
        
        # Trend alignment (for uptrend, RSI should be 40-70)
        if 40 <= rsi <= 70:
            trend_alignment = "bullish"
        elif rsi < 40:
            trend_alignment = "bearish"
        else:
            trend_alignment = "neutral"  # Overbought can go either way
        
        return RSIResult(
            rsi=round(rsi, 2),
            condition=condition,
            trend_alignment=trend_alignment,
            divergence=None  # TODO: Implement divergence detection
        )
    
    def _calculate_ma_cross(self, closes: List[float], current_price: float) -> Optional[MACrossResult]:
        """Calculate 50/200 MA crossover analysis."""
        if len(closes) < 200:
            # Try with available data
            if len(closes) < 50:
                return None
        
        # Calculate SMAs
        sma_50_list = self._calculate_sma(closes, 50)
        sma_200_list = self._calculate_sma(closes, 200) if len(closes) >= 200 else []
        
        if not sma_50_list:
            return None
        
        sma_50 = sma_50_list[-1]
        sma_200 = sma_200_list[-1] if sma_200_list else None
        
        # Price position relative to MAs
        price_vs_50 = "above" if current_price > sma_50 else "below"
        price_vs_200 = "above" if sma_200 and current_price > sma_200 else "below" if sma_200 else None
        
        # 50 vs 200
        ma_50_vs_200 = "above" if sma_200 and sma_50 > sma_200 else "below" if sma_200 else None
        
        # Detect crossover
        crossover_type = CrossoverType.NONE
        days_since_crossover = None
        
        if sma_200_list and len(sma_50_list) >= 2 and len(sma_200_list) >= 2:
            # Look back for recent crossover (within 60 days)
            for i in range(min(60, len(sma_200_list) - 1)):
                idx = -(i + 1)
                prev_idx = idx - 1
                
                if prev_idx < -len(sma_50_list) or prev_idx < -len(sma_200_list):
                    break
                
                curr_50 = sma_50_list[idx]
                curr_200 = sma_200_list[idx]
                prev_50 = sma_50_list[prev_idx]
                prev_200 = sma_200_list[prev_idx]
                
                if prev_50 <= prev_200 and curr_50 > curr_200:
                    crossover_type = CrossoverType.GOLDEN_CROSS
                    days_since_crossover = i
                    break
                elif prev_50 >= prev_200 and curr_50 < curr_200:
                    crossover_type = CrossoverType.DEATH_CROSS
                    days_since_crossover = i
                    break
        
        # Determine trend strength
        if price_vs_50 == "above" and price_vs_200 == "above" and ma_50_vs_200 == "above":
            trend_strength = "strong_up"
        elif price_vs_50 == "above" and (price_vs_200 == "above" or ma_50_vs_200 == "above"):
            trend_strength = "up"
        elif price_vs_50 == "below" and price_vs_200 == "below" and ma_50_vs_200 == "below":
            trend_strength = "strong_down"
        elif price_vs_50 == "below" and (price_vs_200 == "below" or ma_50_vs_200 == "below"):
            trend_strength = "down"
        else:
            trend_strength = "neutral"
        
        return MACrossResult(
            price=current_price,
            sma_50=round(sma_50, 2),
            sma_200=round(sma_200, 2) if sma_200 else None,
            price_vs_50=price_vs_50,
            price_vs_200=price_vs_200 or "unknown",
            ma_50_vs_200=ma_50_vs_200 or "unknown",
            crossover_type=crossover_type,
            days_since_crossover=days_since_crossover,
            trend_strength=trend_strength
        )
    
    def _calculate_vwap(self, bars: List[PriceBar], current_price: float) -> Optional[VWAPResult]:
        """Calculate multi-timeframe VWAP."""
        if not bars:
            return None
        
        # Daily VWAP (last day)
        daily_vwap = None
        daily_bars = bars[-1:] if bars else []
        if daily_bars:
            daily_vwap = self._vwap_from_bars(daily_bars)
        
        # Weekly VWAP (last 5 trading days)
        weekly_vwap = None
        weekly_bars = bars[-5:] if len(bars) >= 5 else bars
        if weekly_bars:
            weekly_vwap = self._vwap_from_bars(weekly_bars)
        
        # Monthly VWAP (last 21 trading days)
        monthly_vwap = None
        monthly_bars = bars[-21:] if len(bars) >= 21 else bars
        if monthly_bars:
            monthly_vwap = self._vwap_from_bars(monthly_bars)
        
        # Price position relative to VWAPs
        price_vs_daily = "above" if daily_vwap and current_price > daily_vwap else "below" if daily_vwap else None
        price_vs_weekly = "above" if weekly_vwap and current_price > weekly_vwap else "below" if weekly_vwap else None
        price_vs_monthly = "above" if monthly_vwap and current_price > monthly_vwap else "below" if monthly_vwap else None
        
        # Distance percentages
        daily_dist = ((current_price - daily_vwap) / daily_vwap * 100) if daily_vwap else None
        weekly_dist = ((current_price - weekly_vwap) / weekly_vwap * 100) if weekly_vwap else None
        monthly_dist = ((current_price - monthly_vwap) / monthly_vwap * 100) if monthly_vwap else None
        
        return VWAPResult(
            current_price=current_price,
            daily_vwap=round(daily_vwap, 2) if daily_vwap else None,
            weekly_vwap=round(weekly_vwap, 2) if weekly_vwap else None,
            monthly_vwap=round(monthly_vwap, 2) if monthly_vwap else None,
            price_vs_daily=price_vs_daily,
            price_vs_weekly=price_vs_weekly,
            price_vs_monthly=price_vs_monthly,
            daily_distance_pct=round(daily_dist, 2) if daily_dist else None,
            weekly_distance_pct=round(weekly_dist, 2) if weekly_dist else None,
            monthly_distance_pct=round(monthly_dist, 2) if monthly_dist else None
        )
    
    def _vwap_from_bars(self, bars: List[PriceBar]) -> Optional[float]:
        """Calculate VWAP from a list of price bars."""
        total_volume = sum(bar.volume for bar in bars)
        if total_volume == 0:
            return None
        
        total_vwap_numerator = sum(bar.vwap_numerator for bar in bars)
        return total_vwap_numerator / total_volume
    
    def _calculate_trend_strength(self,
                                  macd: Optional[MACDResult],
                                  ma_cross: Optional[MACrossResult],
                                  vwap: Optional[VWAPResult]) -> float:
        """
        Calculate trend strength score (0-100).
        
        Weights:
        - MACD: 35%
        - MA Cross: 35%
        - VWAP: 30%
        """
        scores = []
        weights = []
        
        if macd:
            scores.append(macd.score())
            weights.append(0.35)
        
        if ma_cross:
            scores.append(ma_cross.score())
            weights.append(0.35)
        
        if vwap:
            scores.append(vwap.score())
            weights.append(0.30)
        
        if not scores:
            return 50.0
        
        # Normalize weights
        total_weight = sum(weights)
        if total_weight == 0:
            return 50.0
        
        weighted_sum = sum(s * w for s, w in zip(scores, weights))
        return weighted_sum / total_weight
    
    def _calculate_price_momentum(self,
                                  rsi: Optional[RSIResult],
                                  closes: List[float]) -> float:
        """
        Calculate price momentum score (0-100).
        
        Components:
        - RSI health: 50%
        - Rate of change (multi-period): 50%
        """
        scores = []
        weights = []
        
        # RSI score
        if rsi:
            scores.append(rsi.score())
            weights.append(0.50)
        
        # Rate of change score
        if len(closes) >= 21:
            roc_score = self._calculate_roc_score(closes)
            scores.append(roc_score)
            weights.append(0.50)
        
        if not scores:
            return 50.0
        
        total_weight = sum(weights)
        weighted_sum = sum(s * w for s, w in zip(scores, weights))
        return weighted_sum / total_weight
    
    def _calculate_roc_score(self, closes: List[float]) -> float:
        """Calculate rate of change score across multiple timeframes."""
        score = 50
        
        # 1-week ROC (5 days)
        if len(closes) >= 5:
            roc_1w = (closes[-1] / closes[-5] - 1) * 100
            if roc_1w > 5:
                score += 10
            elif roc_1w > 2:
                score += 5
            elif roc_1w < -5:
                score -= 10
            elif roc_1w < -2:
                score -= 5
        
        # 1-month ROC (21 days)
        if len(closes) >= 21:
            roc_1m = (closes[-1] / closes[-21] - 1) * 100
            if roc_1m > 10:
                score += 15
            elif roc_1m > 5:
                score += 8
            elif roc_1m < -10:
                score -= 15
            elif roc_1m < -5:
                score -= 8
        
        # 3-month ROC (63 days)
        if len(closes) >= 63:
            roc_3m = (closes[-1] / closes[-63] - 1) * 100
            if roc_3m > 20:
                score += 15
            elif roc_3m > 10:
                score += 8
            elif roc_3m < -20:
                score -= 15
            elif roc_3m < -10:
                score -= 8
        
        return max(0, min(100, score))
    
    def _determine_trend_direction(self, composite_score: float) -> TrendDirection:
        """Map composite score to trend direction."""
        if composite_score >= 70:
            return TrendDirection.STRONG_UP
        elif composite_score >= 55:
            return TrendDirection.UP
        elif composite_score >= 45:
            return TrendDirection.NEUTRAL
        elif composite_score >= 30:
            return TrendDirection.DOWN
        else:
            return TrendDirection.STRONG_DOWN
    
    def _generate_signals(self,
                         macd: Optional[MACDResult],
                         rsi: Optional[RSIResult],
                         ma_cross: Optional[MACrossResult],
                         vwap: Optional[VWAPResult],
                         composite: float) -> Tuple[List[str], List[str]]:
        """Generate trading signals and warnings."""
        signals = []
        warnings = []
        
        # MACD signals
        if macd:
            if macd.crossover == "bullish_cross":
                signals.append("MACD bullish crossover")
            elif macd.crossover == "bearish_cross":
                warnings.append("MACD bearish crossover")
            
            if macd.trend_signal == "bullish":
                signals.append("MACD histogram expanding bullish")
            elif macd.trend_signal == "bearish":
                warnings.append("MACD histogram expanding bearish")
        
        # RSI signals
        if rsi:
            if rsi.condition == "oversold":
                warnings.append(f"RSI oversold ({rsi.rsi:.0f}) - potential bounce or continued weakness")
            elif rsi.condition == "overbought":
                warnings.append(f"RSI overbought ({rsi.rsi:.0f}) - momentum extended")
            elif rsi.trend_alignment == "bullish":
                signals.append(f"RSI in healthy uptrend range ({rsi.rsi:.0f})")
        
        # MA Cross signals
        if ma_cross:
            if ma_cross.crossover_type == CrossoverType.GOLDEN_CROSS:
                signals.append(f"Golden Cross (50>200 MA) {ma_cross.days_since_crossover or 0} days ago")
            elif ma_cross.crossover_type == CrossoverType.DEATH_CROSS:
                warnings.append(f"Death Cross (50<200 MA) {ma_cross.days_since_crossover or 0} days ago")
            
            if ma_cross.trend_strength == "strong_up":
                signals.append("Price above 50 & 200 MA with bullish alignment")
            elif ma_cross.trend_strength == "strong_down":
                warnings.append("Price below 50 & 200 MA with bearish alignment")
        
        # VWAP signals
        if vwap:
            above_count = sum(1 for v in [vwap.price_vs_daily, vwap.price_vs_weekly, vwap.price_vs_monthly] if v == "above")
            below_count = sum(1 for v in [vwap.price_vs_daily, vwap.price_vs_weekly, vwap.price_vs_monthly] if v == "below")
            
            if above_count == 3:
                signals.append("Price above all VWAP levels (daily, weekly, monthly)")
            elif below_count == 3:
                warnings.append("Price below all VWAP levels (daily, weekly, monthly)")
            elif vwap.price_vs_monthly == "below":
                warnings.append("Price below monthly VWAP - institutional selling pressure")
        
        # Composite signals
        if composite >= 70:
            signals.append(f"Strong uptrend confirmed (score: {composite:.0f})")
        elif composite >= 55:
            signals.append(f"Uptrend confirmed (score: {composite:.0f})")
        elif composite <= 30:
            warnings.append(f"Strong downtrend detected (score: {composite:.0f})")
        elif composite <= 45:
            warnings.append(f"Trend not confirmed for entry (score: {composite:.0f})")
        
        return signals, warnings
    
    def get_trend_confirmation_status(self, analysis: TrendAnalysis) -> Dict:
        """
        Get detailed trend confirmation status for entry decision.
        
        Returns dict with:
        - confirmed: bool
        - score: float
        - threshold: float
        - gap: float (how far from threshold)
        - recommendation: str
        """
        gap = analysis.composite_score - self.trend_confirmation_threshold
        
        if analysis.trend_confirmed:
            if analysis.composite_score >= 60:
                recommendation = "Strong trend confirmation - OK to enter"
            else:
                recommendation = "Trend confirmed but marginal - consider smaller position"
        else:
            if gap > -10:
                recommendation = "Close to confirmation - wait for trend improvement"
            else:
                recommendation = "Trend not confirmed - wait or requires product value override"
        
        return {
            "confirmed": analysis.trend_confirmed,
            "score": analysis.composite_score,
            "threshold": self.trend_confirmation_threshold,
            "gap": round(gap, 1),
            "trend_direction": analysis.trend_direction.value,
            "recommendation": recommendation
        }
