"""
Backtest
========
Replays the live strategy day by day over historical daily bars, calling the
repo's own code wherever it can so the test measures the real rules:

  screening   screener.calculate_rs_scores / percentile_rank / get_sector_leaders
              + forward_thesis.score_forward_thesis (price/volume parts only)
  entry       DailyRunner's RSI / Bollinger / sector-cap gates, then
              DecisionEngine.evaluate_entry and momentum size scaling
  exits       exit_logic.check_exit_triggers and calculate_stop_price tiers

Timeline for each session t:
  1. orders decided after t-1's close fill at t's open (plus slippage)
  2. stops and exit triggers are evaluated on t's bar
  3. the screener and decision engine run on data through t's close

What it cannot replay, and therefore leaves out:
  - macro theses (LLM on today's news)            -> multiplier fixed at 1.0
  - the earnings blackout and earnings score     -> no point-in-time earnings data
  - unfilled limit orders                         -> every order fills at the open
  - nothing on survivorship any more: by default the universe is every US
    stock on each day, delisted ones included (market_history.py);
    --universe index replays today's list back, as before

Usage:
    python backtest.py --start 2023-10-01
    python backtest.py --start 2023-10-01 --w-rs 0.1 --w-thesis 0.9
"""

import argparse
import json
import logging
import sys
import tempfile
import time
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from exit_logic import BREAKEVEN_AT_PCT, calculate_stop_price, check_exit_triggers
from config import EntryRules
from technical_signals import (adx, atr_pct, breakout, ema_reclaim, golden_cross_recent, macd_cross_up,  # noqa: F401
                               stop_distance)

ROOT = Path(__file__).parent.resolve()
RESEARCH = ROOT / "research"
CACHE_DIR = ROOT / "trading_data" / "backtest_cache"     # gitignored

LOOKBACK = 252            # the live screener downloads one year of bars
MAX_CANDIDATES = 30       # run_daily_analysis --max-candidates default
MAX_ORDERS_PER_DAY = 8    # run_daily_analysis keeps the top 8 opportunities
SECTOR_LEADER_BOOST = 1.05
BENCHMARK = "SPY"


# ---------------------------------------------------------------------------
# Pure pieces
# ---------------------------------------------------------------------------

def regime_threshold(spy_closes: np.ndarray) -> Tuple[str, int]:
    """Market regime and RS-rank floor — same rule as screener.get_market_regime."""
    price = float(spy_closes[-1])
    sma50 = float(spy_closes[-50:].mean())
    sma200 = float(spy_closes[-200:].mean())
    if price > sma50 > sma200:
        return "bull", 70
    if price > sma200:
        return "neutral", 80
    return "bear", 90


def evaluate_exit(pos: Dict, bar: Dict, sma50: Optional[float]) -> Tuple[Optional[float], str]:
    """
    Decide whether a position exits on this bar. Returns (price, reason) or (None, "").

    A resting stop order fills intraday at the stop price, or at the open if
    the stock gaps through it. Otherwise the close is checked against the
    live exit triggers (hard loss, close below the 50-day average).
    """
    if pos.get("stop_active") and bar["low"] <= pos["stop"]:
        return min(bar["open"], pos["stop"]), "STOP"

    pnl_pct = (bar["close"] / pos["avg_cost"] - 1) * 100
    should_exit, trigger, _ = check_exit_triggers(
        pos["symbol"], bar["close"], pos["avg_cost"], pnl_pct, sma50)
    if should_exit:
        return bar["close"], trigger
    return None, ""


def entry_price(bar: Dict, mode: str = "open") -> float:
    """
    The price a buy fills at on its entry day. "open" is the original model.
    Live buys go out around 10:00 ET at the ask, after the open, so two harsher
    stand-ins: "typical" (high + low + close) / 3, and "adverse", halfway from
    the open to the day's high. Missing data falls back to the open.
    """
    o, h, l, c = (float(bar.get(k, float("nan"))) for k in ("open", "high", "low", "close"))
    if mode == "typical" and h == h and l == l and c == c:
        return round((h + l + c) / 3, 4)
    if mode == "adverse" and h == h:
        return round(o + (h - o) / 2, 4)
    return o


def opening_fill(open_px: float, prev_close: float, limit: float) -> Optional[float]:
    """
    A limit-on-open buy: fills at the open unless the open is more than `limit`
    above the previous close (0 = no limit). Unknown previous close: fills.
    """
    if limit and prev_close == prev_close and prev_close > 0 and open_px > prev_close * (1 + limit):
        return None
    return open_px


def capped_stop(stop_dist: Optional[float], cap: float) -> Optional[float]:
    """Experimental: no entry stop further away than `cap` (0 = off)."""
    if stop_dist is None or not cap:
        return stop_dist
    return min(stop_dist, cap)


def early_loss_rate(trades: pd.DataFrame, days: int = 5, loss: float = -6.0) -> Optional[float]:
    """Share of closed trades that lost `loss` percent or more within `days` days of entry (live check, 2026-10-16)."""
    if trades is None or not len(trades) or "hold_days" not in trades:
        return None
    return round(float(((trades["hold_days"] <= days) & (trades["pnl_pct"] <= loss)).mean()), 4)


def risk_capped_shares(shares: int, price: float, stop_dist: Optional[float], equity: float, risk: float) -> int:
    """
    Experimental: no position may lose more than `risk` (a share of the account)
    if its stop is hit. A jumpy stock has a wide stop, so it gets fewer shares;
    a calm one is left at its normal size. Never increases a position.
    """
    if not risk or not stop_dist or price <= 0:
        return int(shares)
    return max(0, min(int(shares), int(risk * equity / (price * stop_dist))))


def is_stale(pos: Dict, close: float, today, stale_days: int, stale_gain: float) -> bool:
    """
    Experimental: a holding that has had its time and not done much. Held at
    least `stale_days` calendar days with a gain below `stale_gain` percent.
    It has broken no rule (it is above its stop and its 50-day line); the
    question is whether its cash would do better in a fresh candidate.
    """
    if not stale_days:
        return False
    held = (today - pos["entry_date"]).days
    return held >= stale_days and (close / pos["avg_cost"] - 1) * 100 < stale_gain


def pullback_entry_ok(closes: np.ndarray, rising_over: int = 10) -> bool:
    """
    Experimental (2026-11-10): a short-horizon pullback entry for a name the
    50-day room gate turns away. It qualifies when the close is at or above a
    50-day average that is itself rising (higher than `rising_over` sessions
    ago) and the close has just reclaimed its 21-day EMA after a dip below it.
    """
    if len(closes) < 50 + rising_over:
        return False
    sma_now = closes[-50:].mean()
    sma_then = closes[-(50 + rising_over):-rising_over].mean()
    return bool(closes[-1] >= sma_now and sma_now > sma_then and ema_reclaim(closes))


def in_exit_grace(entry_i: Optional[int], i: int, grace_days: int) -> bool:
    """
    Experimental (2026-11-10): the first `grace_days` sessions after a fill skip
    the close-below-50-day exit. The resting stop and the hard-loss exit still apply.
    """
    return bool(grace_days and entry_i is not None and i - entry_i < grace_days)


GAUSS_POLES = 4


def trend_line(closes: np.ndarray, kind: str = "sma50") -> np.ndarray:
    """
    The line a close is tested against for the trend-broken exit, one value per
    session (NaN until there is enough history).

      sma50      50-day simple average — the live rule
      ema50      50-day exponential average (same average lag as sma50, ~25 sessions)
      sma200     200-day simple average: exit only on a close below the long trend
      gaussN     Ehlers 4-pole Gaussian filter of period N. gauss100 has about
                 the same lag as sma50 but is much smoother; gauss50 has about
                 half the lag, so it follows price more closely.
    """
    x = np.asarray(closes, dtype=float)
    out = np.full(len(x), np.nan)
    if kind in ("sma50", "sma200"):
        n = 50 if kind == "sma50" else 200
        if len(x) >= n:
            c = np.cumsum(np.insert(x, 0, 0.0))
            out[n - 1:] = (c[n:] - c[:-n]) / n
        return out
    if kind == "ema50":
        alpha, passes, warm = 2.0 / 51.0, 1, 50
    elif kind.startswith("gauss"):
        period = int(kind[5:])
        beta = (1 - np.cos(2 * np.pi / period)) / (2 ** (1.0 / GAUSS_POLES) - 1)
        alpha, passes, warm = -beta + np.sqrt(beta * beta + 2 * beta), GAUSS_POLES, period
    else:
        raise ValueError(f"unknown trend line: {kind}")
    y = x.copy()
    for _ in range(passes):           # a multi-pole Gaussian is the same one-pole filter applied in series
        f = np.empty(len(y))
        acc = y[0] if len(y) else 0.0
        for k, v in enumerate(y):
            acc = alpha * v + (1 - alpha) * acc
            f[k] = acc
        y = f
    warm = min(warm, LOOKBACK - 60)   # the window holds one year of bars
    out[warm - 1:] = y[warm - 1:]
    return out


def next_stop(pos: Dict, close: float, breakeven_at: float = 0.0, trail_at: float = 0.0,
              trail_pct: float = 0.0) -> float:
    """
    Stop for the next session: the live tier for this gain, never lower than the current stop.

    Experimental ladder (off by default), filling the gap between the entry stop
    and the live 25%-gain break-even tier:
      breakeven_at  once a close is this many percent above cost, the stop rises
                    to cost + 0.5% (a trade that was working can no longer become a loss)
      trail_at / trail_pct  once a close is trail_at percent above cost, the stop
                    trails the close by trail_pct (a fraction, 0.10 = 10%)
    """
    pnl_pct = (close / pos["avg_cost"] - 1) * 100
    tier_stop, _ = calculate_stop_price(close, pnl_pct, pos["avg_cost"])
    if pnl_pct < BREAKEVEN_AT_PCT and pos.get("stop_dist"):
        # ATR-stop variant: the entry stop is volatility-sized instead of a fixed 8%
        tier_stop = round(pos["avg_cost"] * (1 - pos["stop_dist"]), 2)
    if breakeven_at and pnl_pct >= breakeven_at:
        tier_stop = max(tier_stop, round(pos["avg_cost"] * 1.005, 2))
    if trail_at and trail_pct and pnl_pct >= trail_at:
        tier_stop = max(tier_stop, round(close * (1 - trail_pct), 2))
    return max(pos["stop"], tier_stop)


def earnings_within(earnings: Optional[List], start, end) -> bool:
    """True if any earnings date falls in [start, end] (dates, inclusive)."""
    return any(start <= d <= end for d in (earnings or []))


def max_drawdown(equity: pd.Series) -> float:
    """Largest peak-to-trough decline, as a negative fraction (0.0 if none)."""
    return float((equity / equity.cummax() - 1).min())


def cagr(equity: pd.Series) -> float:
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    if years <= 0 or equity.iloc[0] <= 0:
        return float("nan")
    return float((equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1)


def summarize_trades(trades: pd.DataFrame) -> Dict:
    n = len(trades)
    if n == 0:
        return {"trades": 0}
    wins = trades[trades["pnl_usd"] > 0]
    losses = trades[trades["pnl_usd"] <= 0]
    gross_loss = -losses["pnl_usd"].sum()
    total = float(trades["pnl_usd"].sum())
    return {
        "trades": int(n),
        "win_rate": float(len(wins) / n),
        "total_pnl_usd": total,
        "profit_factor": float(wins["pnl_usd"].sum() / gross_loss) if gross_loss > 0 else float("inf"),
        "avg_win_pct": float(wins["pnl_pct"].mean()) if len(wins) else 0.0,
        "avg_loss_pct": float(losses["pnl_pct"].mean()) if len(losses) else 0.0,
        "mean_pnl_pct": float(trades["pnl_pct"].mean()),
        "median_pnl_pct": float(trades["pnl_pct"].median()),
        "median_hold_days": float(trades["hold_days"].median()),
        "pnl_without_best_trade_usd": float(total - trades["pnl_usd"].max()),
        "pnl_without_best_5_usd": float(total - trades["pnl_usd"].nlargest(5).sum()),
    }


# ---------------------------------------------------------------------------
# Screening as of a past date (the repo's own scoring functions)
# ---------------------------------------------------------------------------

def _rs_full_columns(a: np.ndarray, syms: List[str]) -> Dict[str, float]:
    """screener.calculate_rs_scores for columns with no missing values, vectorised."""
    n = a.shape[0]
    if n < 200 or not syms:
        return {}

    def ret(start_idx, end_idx=None):
        s_ = a[max(0, n + start_idx)]
        e_ = a[-1] if end_idx is None else a[max(0, n + end_idx)]
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.where(s_ > 0, e_ / s_ - 1, 0.0)

    rs = 0.40 * ret(-63) + 0.20 * ret(-126, -63) + 0.20 * ret(-189, -126) + 0.20 * ret(-252, -189)
    return {sym: round(float(v), 5) for sym, v in zip(syms, rs)}


def screen_asof(universe: Dict[str, str], closes: pd.DataFrame, volumes: pd.DataFrame,
                spy_closes: np.ndarray, w_rs: float, w_thesis: float,
                top_n: int = 50, min_price: float = 5.0,
                min_avg_volume: int = 500_000) -> List[Dict]:
    """
    The candidate list run_screener would have produced with these bars.
    `closes` / `volumes` hold the trailing year ending on the as-of date.
    """
    from forward_thesis import score_forward_thesis
    from screener import calculate_rs_scores, get_sector_leaders, percentile_rank

    _, threshold = regime_threshold(spy_closes)
    # Fast path (whole-market runs screen ~3,000 names a day): columns with no
    # gap in the window are scored and pre-gated with numpy; the rest go through
    # the screener's own per-ticker code. Same numbers either way
    # (tests/test_backtest.py::test_fast_screen_matches_the_per_ticker_screen).
    cols = [s for s in universe if s in closes.columns]
    arr = closes[cols].to_numpy(dtype=float) if cols else np.empty((0, 0))
    full = ~np.isnan(arr).any(axis=0) if cols else np.zeros(0, dtype=bool)
    raw_scores = _rs_full_columns(arr[:, full], [c for c, f in zip(cols, full) if f]) if len(arr) >= 200 else {}
    gappy = [c for c, f in zip(cols, full) if not f]
    raw_scores.update(calculate_rs_scores(gappy, {"Close": closes[gappy]}) if gappy else {})
    raw_scores = {s: raw_scores[s] for s in cols if s in raw_scores}      # universe order, as before
    rs_ranks = percentile_rank(raw_scores)
    top_sectors = set(get_sector_leaders(rs_ranks, universe)[:3])

    # Pre-gate the gap-free columns on rank, price and trend; anything that could pass is checked below
    maybe = set(gappy)
    if full.any() and len(arr) >= 200:
        a = arr[:, full]
        price, sma50, sma200 = a[-1], a[-50:].mean(axis=0), a[-200:].mean(axis=0)
        ranks = np.array([rs_ranks.get(c, 0) for c, f in zip(cols, full) if f])
        ok = (ranks >= threshold) & (price >= min_price) & (price > sma50) & (sma50 > sma200)
        maybe |= {c for c, keep in zip([c for c, f in zip(cols, full) if f], ok) if keep}

    candidates = []
    for sym in universe:
        if sym not in maybe:
            continue
        price_arr = closes[sym].dropna().values
        if len(price_arr) < 200:
            continue
        rs_rank = rs_ranks.get(sym, 0)
        if rs_rank < threshold:
            continue
        price = float(price_arr[-1])
        if price < min_price:
            continue
        vol_arr = volumes[sym].dropna().values if sym in volumes.columns else None
        if vol_arr is not None and len(vol_arr) and float(vol_arr[-20:].mean()) < min_avg_volume:
            continue
        sma50 = float(price_arr[-50:].mean())
        sma200 = float(price_arr[-200:].mean())
        if not (price > sma50 > sma200):
            continue

        if vol_arr is None or len(vol_arr) < 40:
            vol_arr = np.ones(len(price_arr))
        try:
            thesis = score_forward_thesis(sym, price_arr, vol_arr, include_earnings=False)
            thesis_score = thesis["thesis_score"]
        except Exception:
            thesis_score = 0.0

        sector = universe.get(sym, "other")
        boost = SECTOR_LEADER_BOOST if sector in top_sectors else 1.0
        candidates.append({
            "symbol": sym, "rs_rank": rs_rank, "thesis_score": thesis_score,
            "sector": sector, "sector_leader": sector in top_sectors, "price": price,
            "effective_score": (w_rs * rs_rank + w_thesis * thesis_score) * boost,
        })

    candidates.sort(key=lambda c: c["effective_score"], reverse=True)
    return candidates[:top_n]


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------

class Backtest:
    def __init__(self, universe: Dict[str, str], prices: Dict[str, pd.DataFrame],
                 start: str, end: Optional[str] = None, capital: float = 100_000.0,
                 w_rs: float = 0.60, w_thesis: float = 0.40, slippage_bps: float = 10.0,
                 quiet: bool = True, entry_trigger: str = "none", atr_stop: float = 0.0,
                 min_adx: float = 0.0, min_above_50ma: float = 0.0, min_spy_above_50ma: float = 0.0,
                 exit_line: str = "sma50", exit_confirm: int = 1, drawdown_mode: str = "live",
                 stale_days: int = 0, stale_gain: float = 5.0, stale_needs_cash: bool = False,
                 risk_per_trade: float = 0.0, max_atr_pct: float = 0.0, fill_mode: str = "open",
                 max_stop_dist: float = 0.0, open_limit: float = 0.0,
                 earnings: Optional[Dict[str, List]] = None, earnings_blackout: int = 0,
                 exit_before_earnings: bool = False, earnings_keep_gain: Optional[float] = None,
                 breakeven_at: float = 0.0, trail_at: float = 0.0, trail_pct: float = 0.0,
                 pullback_entry: bool = False, exit_grace_days: int = 0,
                 members: Optional[pd.DataFrame] = None):
        self.universe = universe
        # Survivorship-free runs (market_history.py): who was in the universe on each day.
        # None = every ticker in `universe` on every day (the old, survivors-only replay).
        self.members = members.reindex(index=prices["Close"].index).fillna(False).astype(bool) \
            if members is not None else None
        self.member_counts: List[int] = []
        # Last session with a close, per ticker: a holding past it was delisted or bought out
        closes = prices["Close"]
        last_idx = closes.notna().values[::-1].argmax(axis=0)
        self.last_valid = {sym: len(closes.index) - 1 - int(k) for sym, k in zip(closes.columns, last_idx)}
        self.open, self.high, self.low = prices["Open"], prices["High"], prices["Low"]
        self.close, self.volume = prices["Close"], prices["Volume"]
        self.dates = self.close.index
        self.capital = capital
        self.w_rs, self.w_thesis = w_rs, w_thesis
        self.slip = slippage_bps / 10_000.0
        # Experimental variants (all off = the live rules)
        self.entry_trigger, self.atr_stop, self.min_adx = entry_trigger, atr_stop, min_adx
        self.min_above_50ma = min_above_50ma
        self.min_spy_above_50ma = min_spy_above_50ma
        # Trend-broken exit: which line the close is tested against, and how many
        # consecutive closes below it are needed (live rule: sma50, 1)
        self.exit_line, self.exit_confirm = exit_line, max(1, int(exit_confirm))
        # Stale exit (experimental, off by default): see is_stale. With
        # stale_needs_cash it only fires on a day a buy went unfilled for lack of cash.
        self.stale_days, self.stale_gain, self.stale_needs_cash = int(stale_days), stale_gain, stale_needs_cash
        self.cash_short_today = False
        # Fast stop-outs (experimental, off by default): cap the dollars a stop
        # can cost (risk_capped_shares), or pass on the most volatile candidates
        self.risk_per_trade, self.max_atr_pct = risk_per_trade, max_atr_pct
        # Live-vs-backtest checks (2026-10-16): when in the day a buy fills, and a cap on the entry stop
        self.fill_mode, self.max_stop_dist = fill_mode, max_stop_dist
        self.open_limit = open_limit       # limit-on-open buys: skip an open gapping above the prior close by more
        # Earnings (experimental, 2026-11-10): historical report dates per symbol.
        #   earnings_blackout     no entry with a report within this many calendar days (live rule: 5)
        #   exit_before_earnings  sell at the close of the session before a report, unless the
        #                         holding is up at least earnings_keep_gain percent (None = always sell)
        self.earnings = earnings or {}
        self.earnings_blackout = int(earnings_blackout)
        self.exit_before_earnings, self.earnings_keep_gain = exit_before_earnings, earnings_keep_gain
        # Stop ladder between the entry stop and the 25% break-even tier (see next_stop)
        self.breakeven_at, self.trail_at, self.trail_pct = breakeven_at, trail_at, trail_pct
        # Short-horizon variants (experimental, 2026-11-10; see pullback_entry_ok, in_exit_grace)
        self.pullback_entry, self.exit_grace_days = bool(pullback_entry), int(exit_grace_days)

        first = int(self.dates.searchsorted(pd.Timestamp(start)))
        self.first = max(first, LOOKBACK)
        self.last = (int(self.dates.searchsorted(pd.Timestamp(end), side="right")) - 1
                     if end else len(self.dates) - 1)

        self.cash = capital
        self.positions: Dict[str, Dict] = {}
        self.pending: List[Dict] = []
        self.trades: List[Dict] = []
        self.equity: Dict[pd.Timestamp, float] = {}
        self.cash_share: List[float] = []
        self.counters = {"candidates": 0, "gate_rsi": 0, "gate_bb": 0, "gate_sector": 0,
                         "gate_trigger": 0, "gate_adx": 0, "gate_50ma_room": 0, "gate_volatile": 0, "days_market_weak": 0, "engine_buy": 0, "engine_hold": 0, "orders": 0, "fills": 0,
                         "unfilled_no_cash": 0, "unfilled_at_cap": 0, "gate_earnings": 0,
                         "earnings_exits": 0, "earnings_held": 0,
                         "pullback_entries": 0, "exits_in_grace": 0}

        if quiet:
            logging.disable(logging.WARNING)
        from config import SECTOR_MAP
        from decision_engine import DecisionEngine
        from run_daily_analysis import MIN_CONFIDENCE_DEFAULT, DailyRunner
        self.runner_cls = DailyRunner
        self.min_confidence = MIN_CONFIDENCE_DEFAULT
        self.engine = DecisionEngine(data_orchestrator=None, data_dir=Path(tempfile.mkdtemp()))
        # The risk manager seeds its drawdown watermark from the live account's
        # history; the simulated account starts its own.
        self.engine.risk_manager.high_watermark = capital
        self.engine.risk_manager.daily_start_value = capital
        # Drawdown rule under test. A private copy of the risk settings, so
        # nothing outside this run is changed.
        #   live      whatever config.py says (the throttle), on this run's own history
        #   throttle  the throttle, explicitly
        #   halt      the rule until 2026-10-06: no buys once 15% below the all-time
        #             high. With no buys the account goes to cash and never
        #             climbs back, so the stop was permanent.
        #   off       no drawdown rule at all (to compare entry and exit rules alone)
        from dataclasses import replace
        rm = self.engine.risk_manager
        if drawdown_mode == "halt":
            rm.risk_config = replace(rm.risk_config, drawdown_mode="halt", drawdown_lookback_days=None,
                                     drawdown_peak_since=None)
        elif drawdown_mode == "off":
            rm.risk_config = replace(rm.risk_config, drawdown_mode="halt", drawdown_lookback_days=None,
                                     drawdown_peak_since=None, max_portfolio_drawdown=10.0)
        else:
            rm.risk_config = replace(rm.risk_config, drawdown_peak_since=None,
                                     **({"drawdown_mode": "throttle"} if drawdown_mode == "throttle" else {}))
        rm._values, rm.high_watermark = [], capital
        self.sym_to_config_sector = {s: sec for sec, syms in SECTOR_MAP.items() for s in syms}

    # -- portfolio views ------------------------------------------------------

    def _equity_at(self, i: int) -> float:
        value = self.cash
        for sym, pos in self.positions.items():
            px = self.close[sym].iloc[i]
            value += pos["shares"] * (px if not np.isnan(px) else pos["last_price"])
        return float(value)

    def _portfolio_state(self, i: int, total_value: float):
        from config import PositionStatus
        from models import PortfolioState, Position
        positions, invested, unrealized = {}, 0.0, 0.0
        for sym, pos in self.positions.items():
            px = float(pos["last_price"])
            mv = pos["shares"] * px
            pnl = mv - pos["shares"] * pos["avg_cost"]
            invested += mv
            unrealized += pnl
            positions[sym] = Position(
                symbol=sym, status=PositionStatus.OPEN, shares=pos["shares"],
                avg_cost=pos["avg_cost"], current_price=px, market_value=mv,
                unrealized_pnl=pnl, unrealized_pnl_pct=(px / pos["avg_cost"] - 1) * 100,
                current_allocation=mv / total_value if total_value > 0 else 0.0,
                stop_loss_price=pos["stop"])
        return PortfolioState(
            timestamp=self.dates[i].to_pydatetime(), total_value=total_value, cash=self.cash,
            invested=invested, positions=positions, num_positions=len(positions),
            cash_allocation=self.cash / total_value if total_value > 0 else 0.0,
            available_cash=max(0.0, self.cash) * 0.95, buying_power=max(0.0, self.cash),
            total_unrealized_pnl=unrealized)

    def _sector_allocations(self, total_value: float) -> Dict[str, float]:
        # Same lookup as DailyRunner._get_sector_allocations (hand-coded SECTOR_MAP)
        alloc: Dict[str, float] = {}
        for sym, pos in self.positions.items():
            sector = self.sym_to_config_sector.get(sym, "other")
            alloc[sector] = alloc.get(sector, 0.0) + pos["shares"] * pos["last_price"]
        return {s: v / total_value for s, v in alloc.items()}

    def _bars(self, sym: str, i: int):
        from models import MarketSnapshot
        from momentum import PriceBar
        lo = max(0, i - LOOKBACK + 1)
        frame = pd.DataFrame({
            "o": self.open[sym].iloc[lo:i + 1], "h": self.high[sym].iloc[lo:i + 1],
            "l": self.low[sym].iloc[lo:i + 1], "c": self.close[sym].iloc[lo:i + 1],
            "v": self.volume[sym].iloc[lo:i + 1]}).dropna()
        if len(frame) < 60:
            return None, None
        bars = [PriceBar(date=d.date(), open=float(o), high=float(h), low=float(l),
                         close=float(c), volume=int(v))
                for d, o, h, l, c, v in zip(frame.index, frame["o"], frame["h"],
                                            frame["l"], frame["c"], frame["v"])]
        c = frame["c"].values
        snapshot = MarketSnapshot(
            symbol=sym, timestamp=frame.index[-1].to_pydatetime(),
            current_price=float(c[-1]), previous_close=float(c[-2]),
            day_change_pct=float((c[-1] / c[-2] - 1) * 100),
            day_high=float(frame["h"].iloc[-1]), day_low=float(frame["l"].iloc[-1]),
            week_52_high=float(frame["h"].max()), week_52_low=float(frame["l"].min()),
            volume=int(frame["v"].iloc[-1]), avg_volume=int(frame["v"].tail(20).mean()),
            market_cap=0.0,
            sma_50=float(c[-50:].mean()) if len(c) >= 50 else None,
            sma_200=float(c[-200:].mean()) if len(c) >= 200 else None)
        return snapshot, bars

    # -- one session ----------------------------------------------------------

    def _fill_pending(self, i: int, equity_prev: float):
        max_positions = self.engine.config.portfolio_constraints.max_positions
        min_cash = self.engine.config.portfolio_constraints.min_cash_allocation * equity_prev
        self.cash_short_today = False
        for order in self.pending:
            sym = order["symbol"]
            px = entry_price({"open": self.open[sym].iloc[i], "high": self.high[sym].iloc[i],
                              "low": self.low[sym].iloc[i], "close": self.close[sym].iloc[i]}, self.fill_mode)
            if self.open_limit and opening_fill(float(px), float(self.close[sym].iloc[i - 1]), self.open_limit) is None:
                self.counters["unfilled_gap"] = self.counters.get("unfilled_gap", 0) + 1
                continue
            if sym in self.positions or np.isnan(px):
                continue
            if max_positions is not None and len(self.positions) >= max_positions:
                self.counters["unfilled_at_cap"] += 1
                continue
            fill = float(px) * (1 + self.slip)
            shares = min(order["shares"], int((self.cash - min_cash) // fill))
            if shares < 1:
                self.counters["unfilled_no_cash"] += 1
                self.cash_short_today = True
                continue
            self.cash -= shares * fill
            stop_dist = order.get("stop_dist")
            self.positions[sym] = {
                "symbol": sym, "shares": shares, "avg_cost": fill, "last_price": fill,
                "stop": round(fill * (1 - (stop_dist or 0.08)), 2),
                "stop_active": False,   # stop is placed the next morning
                "stop_dist": stop_dist,
                "entry_date": self.dates[i].date(), "entry_i": i, **order["features"]}
            self.counters["fills"] += 1
        self.pending = []

    def _process_exits(self, i: int):
        for sym in list(self.positions):
            pos = self.positions[sym]
            o, h, l, c = (self.open[sym].iloc[i], self.high[sym].iloc[i],
                          self.low[sym].iloc[i], self.close[sym].iloc[i])
            if np.isnan(c):
                if i > self.last_valid.get(sym, i):
                    # Prices stopped: delisted or bought out. Close at the last price seen.
                    fill = float(pos["last_price"]) * (1 - self.slip)
                    self.cash += pos["shares"] * fill
                    self._record_exit(sym, pos, i, fill, "DELISTED")
                    self.counters["delisted_exits"] = self.counters.get("delisted_exits", 0) + 1
                    del self.positions[sym]
                continue
            if self.exit_line == "sma50" and self.exit_confirm == 1:      # the live rule, unchanged
                history = self.close[sym].iloc[max(0, i - 49):i + 1].dropna().values
                sma50 = float(history.mean()) if len(history) >= 50 else None
            elif self.exit_line == "death_cross":
                # Sell at the close once the 50-day average has been below the 200-day
                # for exit_confirm sessions. Not a price-vs-line test, so the exit
                # line handed on is set just above the close to make it fire.
                history = self.close[sym].iloc[max(0, i - LOOKBACK + 1):i + 1].dropna().values
                n = self.exit_confirm
                s50, s200 = trend_line(history, "sma50"), trend_line(history, "sma200")
                crossed = len(history) >= 200 + n and not np.isnan(s200[-n:]).any() and (s50[-n:] < s200[-n:]).all()
                sma50 = float(c) * 1.000001 if crossed else None
            else:
                history = self.close[sym].iloc[max(0, i - LOOKBACK + 1):i + 1].dropna().values
                line = trend_line(history, self.exit_line)
                n = self.exit_confirm
                sma50 = None
                if len(history) >= n and not np.isnan(line[-n:]).any():
                    # With confirmation, the exit needs every one of the last n closes below the line
                    sma50 = float(line[-1]) if (history[-n:] < line[-n:]).all() else None
            if sma50 is not None and in_exit_grace(pos.get("entry_i"), i, self.exit_grace_days):
                if float(c) < sma50:
                    self.counters["exits_in_grace"] += 1
                sma50 = None
            price, reason = evaluate_exit(pos, {"open": o, "high": h, "low": l, "close": c}, sma50)
            if self.exit_line == "death_cross" and reason == "PRICE_BELOW_50MA":
                reason = "DEATH_CROSS"
            if price is None and (self.cash_short_today or not self.stale_needs_cash) \
                    and is_stale(pos, float(c), self.dates[i].date(), self.stale_days, self.stale_gain):
                price, reason = float(c), "STALE"
            if price is None and self.exit_before_earnings and i + 1 < len(self.dates):
                today, nxt = self.dates[i].date(), self.dates[i + 1].date()
                if any(today < d <= nxt for d in self.earnings.get(sym, [])):
                    gain = (float(c) / pos["avg_cost"] - 1) * 100
                    if self.earnings_keep_gain is not None and gain >= self.earnings_keep_gain:
                        self.counters["earnings_held"] += 1
                    else:
                        price, reason = float(c), "EARNINGS_EXIT"
                        self.counters["earnings_exits"] += 1
            if price is None:
                pos["last_price"] = float(c)
                pos["stop"] = next_stop(pos, float(c), self.breakeven_at, self.trail_at, self.trail_pct)
                pos["stop_active"] = True
                continue
            fill = float(price) * (1 - self.slip)
            self.cash += pos["shares"] * fill
            self._record_exit(sym, pos, i, fill, reason)
            del self.positions[sym]

    def _record_exit(self, sym: str, pos: Dict, i: int, fill: float, reason: str) -> None:
        self.trades.append({
            "symbol": sym, "status": "CLOSED", "entry_date": pos["entry_date"].isoformat(),
            "exit_date": self.dates[i].date().isoformat(), "entry_price": round(pos["avg_cost"], 4),
            "exit_price": round(fill, 4), "shares": pos["shares"], "exit_reason": reason,
            "pnl_usd": round(pos["shares"] * (fill - pos["avg_cost"]), 2),
            "pnl_pct": round((fill / pos["avg_cost"] - 1) * 100, 3),
            "hold_days": (self.dates[i].date() - pos["entry_date"]).days,
            "rs_rank": pos["rs_rank"], "thesis_score": pos["thesis_score"],
            "trend_score": pos["trend_score"], "confidence": pos["confidence"],
            "sector": pos["sector"], "weight_version": 1, "stop_at_exit": pos["stop"]})

    def _decide(self, i: int, total_value: float):
        lo = i - LOOKBACK + 1
        closes, volumes = self.close.iloc[lo:i + 1], self.volume.iloc[lo:i + 1]
        spy = closes[BENCHMARK].dropna().values
        if self.min_spy_above_50ma and len(spy) >= 50 and spy[-1] / spy[-50:].mean() - 1 < self.min_spy_above_50ma:
            # Experimental: no new entries while the index is not clearly above its own 50-day average
            self.counters["days_market_weak"] += 1
            return
        universe = self.universe
        if self.members is not None:
            row = self.members.iloc[i]
            universe = {s: self.universe.get(s, "other") for s in row.index[row.values]}
            self.member_counts.append(len(universe))
        candidates = screen_asof(universe, closes, volumes, spy, self.w_rs, self.w_thesis)
        self.counters["candidates"] += len(candidates)

        portfolio = self._portfolio_state(i, total_value)
        allocations = self._sector_allocations(total_value)
        runner = self.runner_cls
        stub = SimpleNamespace(engine=self.engine, portfolio=portfolio,
                               market_above_50ma=bool(len(spy) >= 50 and spy[-1] > spy[-50:].mean()))
        stub._extract_trend_score = lambda reasons: runner._extract_trend_score(stub, reasons)

        opportunities = []
        for cand in candidates[:MAX_CANDIDATES]:
            sym = cand["symbol"]
            snapshot, bars = self._bars(sym, i)
            if bars is None:
                continue
            # Live pre-entry gates (the earnings blackout is not replayable and is skipped)
            if runner._check_rsi_at_entry(stub, bars)[0]:
                self.counters["gate_rsi"] += 1
                continue
            if runner._check_bb_at_entry(stub, bars)[0]:
                self.counters["gate_bb"] += 1
                continue
            if runner._check_sector_concentration(stub, cand["sector"], allocations)[0]:
                self.counters["gate_sector"] += 1
                continue
            if self.earnings_blackout:
                today = self.dates[i].date()
                if earnings_within(self.earnings.get(sym), today,
                                   today + pd.Timedelta(days=self.earnings_blackout).to_pytimedelta()):
                    self.counters["gate_earnings"] += 1
                    continue

            # Experimental technical gates
            c = np.array([b.close for b in bars]); h = np.array([b.high for b in bars])
            l = np.array([b.low for b in bars]); v = np.array([b.volume for b in bars], dtype=float)
            if self.entry_trigger != "none":
                fired = {"macd": lambda: macd_cross_up(c), "ema21": lambda: ema_reclaim(c),
                         "breakout": lambda: breakout(c, v),
                         "golden": lambda: golden_cross_recent(c)}[self.entry_trigger]()
                if not fired:
                    self.counters["gate_trigger"] += 1
                    continue
            if self.min_above_50ma and len(c) >= 50 and c[-1] / c[-50:].mean() - 1 < self.min_above_50ma:
                # The main exit is a close below the 50-day average: an entry
                # sitting just above it has no room for an ordinary down day.
                if self.pullback_entry and pullback_entry_ok(c):
                    self.counters["pullback_entries"] += 1
                else:
                    self.counters["gate_50ma_room"] += 1
                    continue
            if self.min_adx and not adx(h, l, c) >= self.min_adx:
                self.counters["gate_adx"] += 1
                continue
            if self.max_atr_pct and atr_pct(h, l, c) > self.max_atr_pct:
                self.counters["gate_volatile"] += 1
                continue

            decision = self.engine.evaluate_entry(
                symbol=sym, portfolio=portfolio, research_score=None,
                market_snapshot=snapshot, price_history=bars, auto_fetch=False)
            d = runner._decision_to_dict(stub, decision)
            d["rs_rank"] = cand["rs_rank"]
            d = runner._scale_size_by_momentum(stub, d)
            if decision.action != "BUY":
                self.counters["engine_hold"] += 1
                continue
            self.counters["engine_buy"] += 1
            d["stop_dist"] = capped_stop(stop_distance(atr_pct(h, l, c), self.atr_stop) if self.atr_stop else None,
                                         self.max_stop_dist)
            d["shares"] = risk_capped_shares(d.get("shares") or 0, float(c[-1]), d["stop_dist"] or 0.08,
                                             total_value, self.risk_per_trade)
            d["features"] = {"rs_rank": cand["rs_rank"], "thesis_score": cand["thesis_score"],
                             "trend_score": d.get("trend_score"), "confidence": d.get("confidence"),
                             "sector": cand["sector"]}
            opportunities.append(d)

        opportunities.sort(key=lambda x: (x.get("signal_strength", 0), x.get("trend_score") or 0),
                           reverse=True)
        for d in opportunities[:MAX_ORDERS_PER_DAY]:
            if (d.get("confidence") or 0) < self.min_confidence or (d.get("shares") or 0) <= 0:
                continue
            self.pending.append({"symbol": d["symbol"], "shares": int(d["shares"]),
                                 "features": d["features"], "stop_dist": d.get("stop_dist")})
            self.counters["orders"] += 1

    def run(self, progress_every: int = 50) -> None:
        risk = self.engine.risk_manager
        equity_prev = self.capital
        t0 = time.time()
        for n, i in enumerate(range(self.first, self.last + 1)):
            self._fill_pending(i, equity_prev)
            self._process_exits(i)
            equity = self._equity_at(i)
            self.equity[self.dates[i]] = equity
            self.cash_share.append(self.cash / equity if equity > 0 else 0.0)

            risk.daily_start_value = equity_prev
            risk.update_watermark(equity, self.dates[i].date())
            if i < self.last:
                self._decide(i, equity)
            equity_prev = equity

            if progress_every and n and n % progress_every == 0:
                print(f"  {self.dates[i].date()}  equity ${equity:>11,.0f}  positions {len(self.positions):>2}  "
                      f"closed trades {len(self.trades):>4}  ({time.time() - t0:.0f}s)", flush=True)

    # -- results --------------------------------------------------------------

    def results(self) -> Dict:
        equity = pd.Series(self.equity)
        trades = pd.DataFrame(self.trades)
        spy = self.close[BENCHMARK].loc[equity.index[0]:equity.index[-1]]
        spy_equity = spy / spy.iloc[0] * self.capital
        invested_share = float(1 - np.mean(self.cash_share)) if self.cash_share else float("nan")
        out = {
            "generated_at": datetime.now().isoformat(),
            "start": equity.index[0].date().isoformat(), "end": equity.index[-1].date().isoformat(),
            "sessions": int(len(equity)), "universe": len(self.universe),
            "universe_mode": "market, point in time" if self.members is not None else "today's list",
            "universe_daily_avg": int(np.mean(self.member_counts)) if self.member_counts else None,
            "w_rs": self.w_rs, "w_thesis": self.w_thesis, "slippage_bps": self.slip * 10_000,
            "variant": {"entry_trigger": self.entry_trigger, "atr_stop": self.atr_stop, "min_adx": self.min_adx,
                        "min_above_50ma": self.min_above_50ma, "min_spy_above_50ma": self.min_spy_above_50ma,
                        "exit_line": self.exit_line, "exit_confirm": self.exit_confirm,
                        "stale_days": self.stale_days, "stale_gain": self.stale_gain,
                        "earnings_blackout": self.earnings_blackout,
                        "exit_before_earnings": self.exit_before_earnings,
                        "earnings_keep_gain": self.earnings_keep_gain,
                        "breakeven_at": self.breakeven_at, "trail_at": self.trail_at, "trail_pct": self.trail_pct,
                        "pullback_entry": self.pullback_entry, "exit_grace_days": self.exit_grace_days},
            "stop_exits": stop_slippage(trades),
            "strategy": {"final_equity": float(equity.iloc[-1]),
                         "total_return": float(equity.iloc[-1] / self.capital - 1),
                         "cagr": cagr(equity), "max_drawdown": max_drawdown(equity)},
            "spy_buy_and_hold": {"total_return": float(spy_equity.iloc[-1] / self.capital - 1),
                                 "cagr": cagr(spy_equity), "max_drawdown": max_drawdown(spy_equity)},
            "average_invested_share": invested_share,
            "open_positions_at_end": len(self.positions),
            "closed_trades": summarize_trades(trades) if len(trades) else {"trades": 0},
            "exit_reasons": trades["exit_reason"].value_counts().to_dict() if len(trades) else {},
            "funnel": self.counters,
        }
        return out


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def load_universe(refresh: bool = False) -> Dict[str, str]:
    """
    Today's index members, scraped once per day and saved. Every run that day
    reads the same file, so parallel runs are compared on identical universes
    (concurrent scrapes get throttled and return partial lists).
    """
    from screener import MIN_FINVIZ_UNIVERSE, get_universe
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    saved = CACHE_DIR / f"universe_{date.today().isoformat()}.json"
    if saved.exists() and not refresh:
        return json.loads(saved.read_text())
    universe = get_universe()
    if len(universe) < MIN_FINVIZ_UNIVERSE:
        raise RuntimeError(f"Universe has only {len(universe)} tickers — Finviz scrape failed; not backtesting on it")
    saved.write_text(json.dumps(universe))
    return universe


def load_prices(tickers: List[str], start: str, end: Optional[str]) -> Dict[str, pd.DataFrame]:
    """Daily OHLCV from a year before `start` (the screener needs the lookback). Cached on disk."""
    fields = ("Open", "High", "Low", "Close", "Volume")
    key = f"{start}_{end or date.today().isoformat()}_{len(tickers)}"
    cache = CACHE_DIR / key
    if all((cache / f"{f}.csv").exists() for f in fields):
        return {f: pd.read_csv(cache / f"{f}.csv", index_col=0, parse_dates=True) for f in fields}

    import yfinance as yf
    fetch_from = (pd.Timestamp(start) - pd.Timedelta(days=400)).strftime("%Y-%m-%d")
    raw = yf.download(sorted(set(tickers) | {BENCHMARK}), start=fetch_from, end=end,
                      auto_adjust=True, progress=False, threads=True)
    prices = {f: raw[f] for f in fields}
    cache.mkdir(parents=True, exist_ok=True)
    for f, frame in prices.items():
        frame.to_csv(cache / f"{f}.csv")
    return prices


def load_backtest_data(start: str, end: Optional[str] = None, mode: str = "market"
                       ) -> Tuple[Dict[str, str], Dict[str, pd.DataFrame], Optional[pd.DataFrame]]:
    """
    (universe, prices, members) for a backtest.
      market  every US common stock Alpaca has, delisted ones included, with a
              day-by-day membership table (market_history.py): no survivorship bias
      index   today's universe.json replayed back with yfinance prices (the old way,
              survivors only); members is None
    """
    if mode == "market":
        import market_history
        return market_history.load(start, end)
    universe = load_universe()
    prices = load_prices(list(universe), start, end)
    universe = {s: v for s, v in universe.items() if s in prices["Close"].columns}
    return universe, prices, None


SEC_EARNINGS_CACHE = ROOT / "docs" / "data" / "big_movers_cache.json"
YF_EARNINGS_PER_RUN = 300   # yfinance lookups per run for names the SEC cache lacks (cached, so coverage grows)


def load_earnings(tickers: List[str]) -> Dict[str, List]:
    """
    Earnings report dates per ticker: from the SEC 8-K (item 2.02) dates the
    big-mover job keeps for the whole universe, then yfinance for names it
    lacks (at most YF_EARNINGS_PER_RUN per run), cached on disk. Report dates
    are announced weeks ahead, so using them as of a past session is not
    look-ahead in any way that matters for a blackout. Delisted names usually
    have none, so an earnings rule simply doesn't apply to them.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = CACHE_DIR / "earnings_dates.json"
    saved = json.loads(cache.read_text()) if cache.exists() else {}
    import contracts
    try:
        sec = contracts.read(SEC_EARNINGS_CACHE, default={}).get("earnings") or {}
    except Exception:
        sec = {}
    for sym in tickers:
        dates = (sec.get(sym) or {}).get("dates")
        if dates and sym not in saved:
            saved[sym] = sorted(set(dates))
    missing = [t for t in tickers if t not in saved][:YF_EARNINGS_PER_RUN]
    if missing:
        import yfinance as yf
        for n, sym in enumerate(missing, 1):
            try:
                frame = yf.Ticker(sym).get_earnings_dates(limit=24)
                saved[sym] = sorted({ts.date().isoformat() for ts in frame.index}) if frame is not None else []
            except Exception as e:
                logging.getLogger(__name__).warning(f"{sym}: earnings dates failed: {e}")
                saved[sym] = []
            if n % 50 == 0:
                print(f"  earnings dates: {n}/{len(missing)}", flush=True)
                cache.write_text(json.dumps(saved))
        cache.write_text(json.dumps(saved))
    return {s: [date.fromisoformat(d) for d in v] for s, v in saved.items() if s in set(tickers)}


def stop_slippage(trades: pd.DataFrame) -> Optional[Dict]:
    """How far below their planned level stop exits filled (gaps through the stop)."""
    if trades is None or not len(trades) or "stop_at_exit" not in trades:
        return None
    s = trades[trades["exit_reason"] == "STOP"]
    if not len(s):
        return {"n": 0}
    slip = (s["exit_price"] / s["stop_at_exit"] - 1) * 100
    return {"n": int(len(s)), "mean_slip_pct": float(slip.mean()), "worst_slip_pct": float(slip.min()),
            "share_gapped_over_2pct": float((slip < -2).mean()),
            "mean_pnl_pct": float(s["pnl_pct"].mean()), "worst_pnl_pct": float(s["pnl_pct"].min())}


def learning_fit(trades: pd.DataFrame) -> Optional[Dict]:
    """The learning agent's own regression, run on the simulated closed trades."""
    from learning_stats import derive_weights, ols_fit, zscore
    t = trades.dropna(subset=["rs_rank", "thesis_score", "pnl_pct"])
    if len(t) < 30:
        return None
    X = np.column_stack([zscore(t["rs_rank"].values), zscore(t["thesis_score"].values)])
    fit = ols_fit(X, t["pnl_pct"].values)
    derived = derive_weights(coef_rs=fit["coef"][0], t_rs=fit["t"][0],
                             coef_thesis=fit["coef"][1], t_thesis=fit["t"][1])
    return {"n": int(len(t)), "t_rs": float(fit["t"][0]), "t_thesis": float(fit["t"][1]),
            "coef_rs": float(fit["coef"][0]), "coef_thesis": float(fit["coef"][1]),
            "derived_weights": {"w_rs": derived[0], "w_thesis": derived[1]} if derived else None}


def print_report(r: Dict) -> None:
    s, b, t = r["strategy"], r["spy_buy_and_hold"], r["closed_trades"]
    print(f"\nVariant: {r.get('variant')}")
    print(f"\nBacktest {r['start']} to {r['end']}  ({r['sessions']} sessions, {r['universe']} tickers, "
          f"weights {r['w_rs']:.2f}/{r['w_thesis']:.2f}, slippage {r['slippage_bps']:.0f} bps per side)")
    print(f"  {'':22}{'strategy':>12}{'SPY buy & hold':>18}")
    print(f"  {'total return':22}{s['total_return']:>12.1%}{b['total_return']:>18.1%}")
    print(f"  {'annualized':22}{s['cagr']:>12.1%}{b['cagr']:>18.1%}")
    print(f"  {'max drawdown':22}{s['max_drawdown']:>12.1%}{b['max_drawdown']:>18.1%}")
    if t.get("trades"):
        print(f"\n  closed trades {t['trades']}  |  win rate {t['win_rate']:.0%}  |  avg win {t['avg_win_pct']:+.1f}%  "
              f"avg loss {t['avg_loss_pct']:+.1f}%  |  profit factor {t['profit_factor']:.2f}  |  "
              f"median hold {t['median_hold_days']:.0f} days")
        print(f"  realized P&L ${t['total_pnl_usd']:,.0f}  |  without best trade ${t['pnl_without_best_trade_usd']:,.0f}  |  "
              f"without best 5 ${t['pnl_without_best_5_usd']:,.0f}")
    print(f"  exit reasons: {r['exit_reasons']}")
    print(f"  funnel: {r['funnel']}")
    if r.get("learning_fit"):
        f = r["learning_fit"]
        print(f"  learning regression on {f['n']} simulated trades: t_rs = {f['t_rs']:+.2f}, "
              f"t_thesis = {f['t_thesis']:+.2f}  ->  derived weights {f['derived_weights']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay the strategy over historical daily bars")
    parser.add_argument("--start", required=True, help="first simulated session, YYYY-MM-DD")
    parser.add_argument("--end", default=None)
    parser.add_argument("--capital", type=float, default=100_000.0)
    parser.add_argument("--w-rs", type=float, default=0.60)
    parser.add_argument("--w-thesis", type=float, default=0.40)
    parser.add_argument("--slippage-bps", type=float, default=10.0, help="per side")
    parser.add_argument("--entry-trigger", choices=["none", "macd", "ema21", "breakout", "golden"], default="none",
                        help="experimental: only buy when this signal fired")
    parser.add_argument("--atr-stop", type=float, default=0.0,
                        help="experimental: entry stop at this multiple of 14-day ATR instead of a fixed 8%%")
    parser.add_argument("--min-adx", type=float, default=0.0, help="experimental: require ADX(14) at least this")
    parser.add_argument("--min-above-50ma", type=float, default=0.0,
                        help="experimental: require the close to be at least this fraction above its 50-day average")
    parser.add_argument("--min-spy-above-50ma", type=float, default=EntryRules().min_spy_above_50ma,
                        help="no new entries unless SPY closes at least this fraction above its 50-day average "
                             "(default: the live rule, config.EntryRules; 0 = off)")
    parser.add_argument("--exit-line", choices=["sma50", "ema50", "gauss50", "gauss100", "sma200", "death_cross"], default="sma50",
                        help="experimental: line the close is tested against for the trend-broken exit")
    parser.add_argument("--exit-confirm", type=int, default=1,
                        help="experimental: consecutive closes below the exit line needed to sell")
    parser.add_argument("--drawdown-mode", choices=["live", "throttle", "halt", "off"], default="live",
                        help="drawdown rule: live config (default), throttle, the old permanent halt, or none")
    parser.add_argument("--no-drawdown-halt", action="store_true", help="same as --drawdown-mode off")
    parser.add_argument("--stale-days", type=int, default=0,
                        help="experimental: sell a holding held this many days with a gain under --stale-gain")
    parser.add_argument("--stale-gain", type=float, default=5.0, help="percent gain below which a holding is stale")
    parser.add_argument("--stale-needs-cash", action="store_true",
                        help="stale exit only on a day a buy went unfilled for lack of cash")
    parser.add_argument("--risk-per-trade", type=float, default=0.0,
                        help="experimental: cap what a stop-out can cost, as a share of the account (0.003 = 0.3%%)")
    parser.add_argument("--max-atr-pct", type=float, default=0.0,
                        help="experimental: skip candidates whose ATR(14) exceeds this share of price (0.04 = 4%%)")
    parser.add_argument("--fill", choices=["open", "typical", "adverse"], default="open",
                        help="entry fill price: the open, the day's typical price, or halfway from open to high")
    parser.add_argument("--max-stop-dist", type=float, default=0.0, help="experimental: cap the entry stop distance")
    parser.add_argument("--open-limit", type=float, default=0.0,
                        help="buy at the open only if it is no more than this above the prior close (0 = any)")
    parser.add_argument("--earnings-blackout", type=int, default=0,
                        help="experimental: no entry with an earnings report within this many calendar days")
    parser.add_argument("--exit-before-earnings", action="store_true",
                        help="experimental: sell at the close of the session before an earnings report")
    parser.add_argument("--earnings-keep-gain", type=float, default=None,
                        help="with --exit-before-earnings: keep holdings up at least this percent")
    parser.add_argument("--breakeven-at", type=float, default=0.0,
                        help="experimental: raise the stop to cost+0.5%% once a close is this percent above cost")
    parser.add_argument("--trail-at", type=float, default=0.0,
                        help="experimental: start trailing the stop once a close is this percent above cost")
    parser.add_argument("--trail-pct", type=float, default=0.0, help="trail distance as a fraction (0.10 = 10%%)")
    parser.add_argument("--pullback-entry", action="store_true",
                        help="let a name the 50-day room gate turns away enter on a 21-day EMA reclaim above a rising 50-day average")
    parser.add_argument("--exit-grace-days", type=int, default=0,
                        help="sessions after a fill during which a close below the 50-day line does not exit")
    parser.add_argument("--max-universe", type=int, default=None, help="limit tickers (smoke tests)")
    parser.add_argument("--universe", choices=("market", "index"), default="market",
                        help="market: every US stock incl. delisted, day by day (default); "
                             "index: today's list replayed back (survivors only)")
    parser.add_argument("--tag", default=None, help="suffix for output files")
    args = parser.parse_args()

    universe, prices, members = load_backtest_data(args.start, args.end, args.universe)
    if args.max_universe:
        universe = dict(list(universe.items())[:args.max_universe])
    print(f"Universe: {len(universe)} tickers ({args.universe})")
    earnings = load_earnings(list(universe)) if (args.earnings_blackout or args.exit_before_earnings) else None

    bt = Backtest(universe, prices, args.start, args.end, args.capital,
                  args.w_rs, args.w_thesis, args.slippage_bps, entry_trigger=args.entry_trigger,
                  atr_stop=args.atr_stop, min_adx=args.min_adx, min_above_50ma=args.min_above_50ma,
                  min_spy_above_50ma=args.min_spy_above_50ma,
                  exit_line=args.exit_line, exit_confirm=args.exit_confirm,
                  drawdown_mode="off" if args.no_drawdown_halt else args.drawdown_mode,
                  stale_days=args.stale_days, stale_gain=args.stale_gain, stale_needs_cash=args.stale_needs_cash,
                  risk_per_trade=args.risk_per_trade, max_atr_pct=args.max_atr_pct,
                  fill_mode=args.fill, max_stop_dist=args.max_stop_dist, open_limit=args.open_limit,
                  earnings=earnings, earnings_blackout=args.earnings_blackout,
                  exit_before_earnings=args.exit_before_earnings, earnings_keep_gain=args.earnings_keep_gain,
                  breakeven_at=args.breakeven_at, trail_at=args.trail_at, trail_pct=args.trail_pct,
                  pullback_entry=args.pullback_entry, exit_grace_days=args.exit_grace_days,
                  members=members)
    bt.run()
    result = bt.results()
    trades = pd.DataFrame(bt.trades)
    result["learning_fit"] = learning_fit(trades) if len(trades) else None

    RESEARCH.mkdir(exist_ok=True)
    tag = args.tag or f"{int(args.w_rs * 100)}_{int(args.w_thesis * 100)}"
    trades.to_csv(RESEARCH / f"backtest_trades_{tag}.csv", index=False)
    pd.Series(bt.equity, name="equity").to_csv(RESEARCH / f"backtest_equity_{tag}.csv")
    (RESEARCH / f"backtest_summary_{tag}.json").write_text(json.dumps(result, indent=2, default=str))
    print_report(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
