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
  - index membership changes                      -> today's universe (survivorship bias)

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

from exit_logic import calculate_stop_price, check_exit_triggers
from technical_signals import (adx, atr_pct, breakout, ema_reclaim, macd_cross_up,  # noqa: F401
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


GAUSS_POLES = 4


def trend_line(closes: np.ndarray, kind: str = "sma50") -> np.ndarray:
    """
    The line a close is tested against for the trend-broken exit, one value per
    session (NaN until there is enough history).

      sma50      50-day simple average — the live rule
      ema50      50-day exponential average (same average lag as sma50, ~25 sessions)
      gaussN     Ehlers 4-pole Gaussian filter of period N. gauss100 has about
                 the same lag as sma50 but is much smoother; gauss50 has about
                 half the lag, so it follows price more closely.
    """
    x = np.asarray(closes, dtype=float)
    out = np.full(len(x), np.nan)
    if kind == "sma50":
        if len(x) >= 50:
            c = np.cumsum(np.insert(x, 0, 0.0))
            out[49:] = (c[50:] - c[:-50]) / 50
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


def next_stop(pos: Dict, close: float) -> float:
    """Stop for the next session: the live tier for this gain, never lower than the current stop."""
    pnl_pct = (close / pos["avg_cost"] - 1) * 100
    tier_stop, _ = calculate_stop_price(close, pnl_pct, pos["avg_cost"])
    if pnl_pct < 25 and pos.get("stop_dist"):
        # ATR-stop variant: the entry stop is volatility-sized instead of a fixed 8%
        tier_stop = round(pos["avg_cost"] * (1 - pos["stop_dist"]), 2)
    return max(pos["stop"], tier_stop)


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

    tickers = list(universe)
    _, threshold = regime_threshold(spy_closes)
    raw_scores = calculate_rs_scores(tickers, {"Close": closes})
    rs_ranks = percentile_rank(raw_scores)
    top_sectors = set(get_sector_leaders(rs_ranks, universe)[:3])

    candidates = []
    for sym in universe:
        if sym not in closes.columns:
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
                 stale_days: int = 0, stale_gain: float = 5.0, stale_needs_cash: bool = False):
        self.universe = universe
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
                         "gate_trigger": 0, "gate_adx": 0, "gate_50ma_room": 0, "days_market_weak": 0, "engine_buy": 0, "engine_hold": 0, "orders": 0, "fills": 0,
                         "unfilled_no_cash": 0, "unfilled_at_cap": 0}

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
            px = self.open[sym].iloc[i]
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
                "entry_date": self.dates[i].date(), **order["features"]}
            self.counters["fills"] += 1
        self.pending = []

    def _process_exits(self, i: int):
        for sym in list(self.positions):
            pos = self.positions[sym]
            o, h, l, c = (self.open[sym].iloc[i], self.high[sym].iloc[i],
                          self.low[sym].iloc[i], self.close[sym].iloc[i])
            if np.isnan(c):
                continue
            if self.exit_line == "sma50" and self.exit_confirm == 1:      # the live rule, unchanged
                history = self.close[sym].iloc[max(0, i - 49):i + 1].dropna().values
                sma50 = float(history.mean()) if len(history) >= 50 else None
            else:
                history = self.close[sym].iloc[max(0, i - LOOKBACK + 1):i + 1].dropna().values
                line = trend_line(history, self.exit_line)
                n = self.exit_confirm
                sma50 = None
                if len(history) >= n and not np.isnan(line[-n:]).any():
                    # With confirmation, the exit needs every one of the last n closes below the line
                    sma50 = float(line[-1]) if (history[-n:] < line[-n:]).all() else None
            price, reason = evaluate_exit(pos, {"open": o, "high": h, "low": l, "close": c}, sma50)
            if price is None and (self.cash_short_today or not self.stale_needs_cash) \
                    and is_stale(pos, float(c), self.dates[i].date(), self.stale_days, self.stale_gain):
                price, reason = float(c), "STALE"
            if price is None:
                pos["last_price"] = float(c)
                pos["stop"] = next_stop(pos, float(c))
                pos["stop_active"] = True
                continue
            fill = float(price) * (1 - self.slip)
            self.cash += pos["shares"] * fill
            self.trades.append({
                "symbol": sym, "status": "CLOSED", "entry_date": pos["entry_date"].isoformat(),
                "exit_date": self.dates[i].date().isoformat(), "entry_price": round(pos["avg_cost"], 4),
                "exit_price": round(fill, 4), "shares": pos["shares"], "exit_reason": reason,
                "pnl_usd": round(pos["shares"] * (fill - pos["avg_cost"]), 2),
                "pnl_pct": round((fill / pos["avg_cost"] - 1) * 100, 3),
                "hold_days": (self.dates[i].date() - pos["entry_date"]).days,
                "rs_rank": pos["rs_rank"], "thesis_score": pos["thesis_score"],
                "trend_score": pos["trend_score"], "confidence": pos["confidence"],
                "sector": pos["sector"], "weight_version": 1})
            del self.positions[sym]

    def _decide(self, i: int, total_value: float):
        lo = i - LOOKBACK + 1
        closes, volumes = self.close.iloc[lo:i + 1], self.volume.iloc[lo:i + 1]
        spy = closes[BENCHMARK].dropna().values
        if self.min_spy_above_50ma and len(spy) >= 50 and spy[-1] / spy[-50:].mean() - 1 < self.min_spy_above_50ma:
            # Experimental: no new entries while the index is not clearly above its own 50-day average
            self.counters["days_market_weak"] += 1
            return
        candidates = screen_asof(self.universe, closes, volumes, spy, self.w_rs, self.w_thesis)
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

            # Experimental technical gates
            c = np.array([b.close for b in bars]); h = np.array([b.high for b in bars])
            l = np.array([b.low for b in bars]); v = np.array([b.volume for b in bars], dtype=float)
            if self.entry_trigger != "none":
                fired = {"macd": lambda: macd_cross_up(c), "ema21": lambda: ema_reclaim(c),
                         "breakout": lambda: breakout(c, v)}[self.entry_trigger]()
                if not fired:
                    self.counters["gate_trigger"] += 1
                    continue
            if self.min_above_50ma and len(c) >= 50 and c[-1] / c[-50:].mean() - 1 < self.min_above_50ma:
                # The main exit is a close below the 50-day average: an entry
                # sitting just above it has no room for an ordinary down day.
                self.counters["gate_50ma_room"] += 1
                continue
            if self.min_adx and not adx(h, l, c) >= self.min_adx:
                self.counters["gate_adx"] += 1
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
            d["stop_dist"] = stop_distance(atr_pct(h, l, c), self.atr_stop) if self.atr_stop else None
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
            "w_rs": self.w_rs, "w_thesis": self.w_thesis, "slippage_bps": self.slip * 10_000,
            "variant": {"entry_trigger": self.entry_trigger, "atr_stop": self.atr_stop, "min_adx": self.min_adx,
                        "min_above_50ma": self.min_above_50ma},
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
    parser.add_argument("--entry-trigger", choices=["none", "macd", "ema21", "breakout"], default="none",
                        help="experimental: only buy when this signal fired")
    parser.add_argument("--atr-stop", type=float, default=0.0,
                        help="experimental: entry stop at this multiple of 14-day ATR instead of a fixed 8%%")
    parser.add_argument("--min-adx", type=float, default=0.0, help="experimental: require ADX(14) at least this")
    parser.add_argument("--min-above-50ma", type=float, default=0.0,
                        help="experimental: require the close to be at least this fraction above its 50-day average")
    parser.add_argument("--min-spy-above-50ma", type=float, default=0.0,
                        help="experimental: no new entries unless SPY closes at least this fraction above its 50-day average")
    parser.add_argument("--exit-line", choices=["sma50", "ema50", "gauss50", "gauss100"], default="sma50",
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
    parser.add_argument("--max-universe", type=int, default=None, help="limit tickers (smoke tests)")
    parser.add_argument("--tag", default=None, help="suffix for output files")
    args = parser.parse_args()

    universe = load_universe()
    if args.max_universe:
        universe = dict(list(universe.items())[:args.max_universe])
    print(f"Universe: {len(universe)} tickers (today's index members)")
    prices = load_prices(list(universe), args.start, args.end)
    universe = {s: sec for s, sec in universe.items() if s in prices["Close"].columns}

    bt = Backtest(universe, prices, args.start, args.end, args.capital,
                  args.w_rs, args.w_thesis, args.slippage_bps, entry_trigger=args.entry_trigger,
                  atr_stop=args.atr_stop, min_adx=args.min_adx, min_above_50ma=args.min_above_50ma,
                  min_spy_above_50ma=args.min_spy_above_50ma,
                  exit_line=args.exit_line, exit_confirm=args.exit_confirm,
                  drawdown_mode="off" if args.no_drawdown_halt else args.drawdown_mode,
                  stale_days=args.stale_days, stale_gain=args.stale_gain, stale_needs_cash=args.stale_needs_cash)
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
