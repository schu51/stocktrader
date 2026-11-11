#!/usr/bin/env python3
"""
Put hedge study (research, 2026-11-11)
======================================
What would protective puts have cost, and what would they have saved, if the
bot had bought them on its stocks just before earnings reports?

Why: a stop cannot protect against a gap. VCYT filled 33% under its sleeve
stop on its 2026-11-05 report. The sleeve now trims ahead of reports
(diamond_sleeve.earnings_trim); a put is the other way to cap that loss, and
it keeps the upside. This study prices that choice on past reports. It places
no orders and changes no rules.

Each event is one report date D for one stock:
  window   close of the last session before D  ->  close of the first session
           after D. Report dates carry no time, so this spans both a report
           before D's open and one after D's close.
  put      bought at the window's start, expiring the first Friday on or after
           the window's end, strike k x the stock price (k = 1.00 .. 0.85);
           sold back at the window's end.

Historical option prices are not available to this repo, so puts are priced
with Black-Scholes, and the price is built the way the market builds it ahead
of a report:
  base vol      realized vol of the 60 sessions before the window, report
                windows excluded
  report move   the stock's own average absolute move over its previous
                reports (up to 8, never a later one), times MARKUP. Option
                markets usually price the report move at or above the
                realized average; MARKUP 1.0 / 1.25 / 1.5 brackets that.
  entry vol     sqrt(base^2 + move_sigma^2 / T): the report move is a one-off
                variance added to the option's life
  exit          after the report the move is gone: base vol, remaining life
  spread        buy at mid + half-spread, sell at mid - half-spread; the
                half-spread is SPREAD x premium, at least $0.025 (the tick)

That model is the weak point, so the live put shadow (put_shadow.py) records
real put quotes ahead of reports to check it: compare its premium_pct to this
study's model_premium_pct at the same strike.

Groups:
  diamond       every report of every company the diamond screen has listed
  book          every report of every large-cap stock the bot has traded
  sleeve_held   reports the sleeve actually held a position through
  book_held     reports the large-cap book actually held a position through
Each is set against holding the stock unhedged and against holding half the
shares (what a trim does: half the downside and half the upside).

Caveat: the diamond list is recent, so most of its history is of companies
that later screened well; both the hedged and unhedged legs share that bias.

Writes research/put_hedge_study.json. Usage:
    python put_hedge_study.py
"""

import json
import identity
import contracts
import logging
import math
import statistics as st
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence

ROOT = Path(__file__).parent.resolve()
DOCS = ROOT / "docs" / "data"
OUT = ROOT / "research" / "put_hedge_study.json"
DIAMOND_HISTORY = DOCS / "diamond_history.json"
TRADES = DOCS / "trades.json"

YEARS = 3
RATE = 0.04                         # risk-free, annual
VOL_SESSIONS = 60                   # base vol lookback
MIN_VOL_SESSIONS = 40
PRIOR_REPORTS = 8                   # report moves averaged for the implied move
MIN_PRIOR_REPORTS = 2               # fewer than this: no price, event skipped
STRIKES = (1.00, 0.95, 0.90, 0.85)  # x the stock price at entry
MARKUPS = (1.0, 1.25, 1.5)          # implied report move / realized average
SPREADS = {"diamond": 0.10, "sleeve_held": 0.10, "book": 0.05, "book_held": 0.05}   # half-spread / premium
TICK_HALF = 0.025                   # half of a $0.05 option tick
HEADLINE = {"markup": 1.25, "strike": 0.90}

logger = logging.getLogger(__name__)


# ── pricing (pure) ───────────────────────────────────────────────────────────

def _ncdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs_put(s: float, k: float, t: float, sigma: float, r: float = RATE) -> float:
    """Black-Scholes European put. At or past expiry, or with no vol, its intrinsic value."""
    if t <= 0 or sigma <= 0:
        return max(k - s, 0.0)
    v = sigma * math.sqrt(t)
    d1 = (math.log(s / k) + (r + sigma * sigma / 2) * t) / v
    return k * math.exp(-r * t) * _ncdf(-(d1 - v)) - s * _ncdf(-d1)


def expiry_on_or_after(d: date) -> date:
    """The first Friday on or after d (weekly options expire Fridays)."""
    return d + timedelta(days=(4 - d.weekday()) % 7)


def move_sigma(prior_moves: Sequence[float], markup: float) -> float:
    """Std-dev of the report move the market would price: markup x mean |move|, as a normal's sigma."""
    return markup * st.mean(abs(m) for m in prior_moves) * math.sqrt(math.pi / 2)


def hedge(s0: float, s1: float, d0: date, d1: date, base_vol: float, msig: float,
          strike: float, half_spread: float, r: float = RATE) -> Dict:
    """
    One put over one report window, per share, as fractions of the entry price:
    premium (what was paid), proceeds (what selling it back brought), net.
    """
    k = strike * s0
    exp = expiry_on_or_after(d1)
    t0, t1 = (exp - d0).days / 365, (exp - d1).days / 365
    entry_vol = math.sqrt(base_vol ** 2 + msig ** 2 / t0)
    mid0 = bs_put(s0, k, t0, entry_vol, r)
    ask = mid0 + max(half_spread * mid0, TICK_HALF)
    if t1 <= 0:
        bid = max(k - s1, 0.0)                         # expires in the money: exercised at intrinsic
    else:
        mid1 = bs_put(s1, k, t1, base_vol, r)
        bid = max(mid1 - max(half_spread * mid1, TICK_HALF), 0.0)
    return {"premium": ask / s0, "proceeds": bid / s0, "net": (bid - ask) / s0,
            "model_premium_mid": mid0 / s0, "entry_vol": entry_vol, "days_to_expiry": (exp - d0).days}


# ── events (pure) ────────────────────────────────────────────────────────────

def _window(dates: Sequence[date], report: date):
    """(last session before the report, first session after it) as indexes, or None."""
    before = [i for i, d in enumerate(dates) if d < report]
    after = [i for i, d in enumerate(dates) if d > report]
    if not before or not after:
        return None
    return before[-1], after[0]


def _base_vol(closes: Sequence[float], end: int, skip: set) -> Optional[float]:
    """Annualized close-to-close vol of the VOL_SESSIONS sessions ending at `end`, report windows skipped."""
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(max(1, end - VOL_SESSIONS + 1), end + 1)
            if i not in skip and closes[i - 1] > 0 and closes[i] > 0]
    if len(rets) < MIN_VOL_SESSIONS:
        return None
    return st.stdev(rets) * math.sqrt(252)


def events(symbol: str, dates: Sequence[date], closes: Sequence[float], reports: Iterable[date],
           start: date) -> List[Dict]:
    """
    Every report on or after `start` that has a full window, with the history a
    trader would have had on the window's first day: base vol and earlier moves.
    """
    windows = []
    for rep in sorted(set(reports)):
        w = _window(dates, rep)
        if w and closes[w[0]] > 0 and closes[w[1]] > 0:
            windows.append((rep, *w))
    skip = {i for _, i0, i1 in windows for i in range(i0 + 1, i1 + 1)}
    out, moves = [], []
    for rep, i0, i1 in windows:
        move = closes[i1] / closes[i0] - 1
        if rep >= start and len(moves) >= MIN_PRIOR_REPORTS:
            vol = _base_vol(closes, i0, skip)
            if vol:
                out.append({"symbol": symbol, "report": rep.isoformat(), "d0": dates[i0], "d1": dates[i1],
                            "s0": closes[i0], "s1": closes[i1], "move": move, "base_vol": vol,
                            "prior_moves": moves[-PRIOR_REPORTS:]})
        moves.append(move)
    return out


def held_through(trades: List[Dict], symbol: str, report: date, sleeve: bool) -> bool:
    """True if a trade in `symbol` (sleeve or not) was open across `report`'s window."""
    for t in trades or []:
        if t.get("symbol") != symbol or bool(t.get("sleeve")) != sleeve:
            continue
        try:
            entry = date.fromisoformat(str(t.get("entry_date"))[:10])
            exit_ = date.fromisoformat(str(t["exit_date"])[:10]) if t.get("exit_date") else None
        except ValueError:
            continue
        if entry < report and (exit_ is None or exit_ >= report):
            return True
    return False


# ── summary (pure) ───────────────────────────────────────────────────────────

def _pct(xs: List[float], q: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, max(0, int(round(q * (len(xs) - 1)))))]


def _dist(xs: List[float]) -> Dict:
    return {"mean": round(st.mean(xs), 4), "median": round(st.median(xs), 4),
            "p5": round(_pct(xs, 0.05), 4), "worst": round(min(xs), 4),
            "share_below_-15pct": round(sum(x < -0.15 for x in xs) / len(xs), 3)}


def summarize(evs: List[Dict], half_spread: float) -> Dict:
    """Unhedged, half size and every put configuration over one group's events."""
    if not evs:
        return {"events": 0}
    stock = [e["move"] for e in evs]
    out = {"events": len(evs), "symbols": len({e["symbol"] for e in evs}), "half_spread": half_spread,
           "unhedged": _dist(stock), "half_size": _dist([m / 2 for m in stock]), "puts": {}}
    for mk in MARKUPS:
        for k in STRIKES:
            rows = [hedge(e["s0"], e["s1"], e["d0"], e["d1"], e["base_vol"], move_sigma(e["prior_moves"], mk),
                          k, half_spread) for e in evs]
            hedged = [e["move"] + h["net"] for e, h in zip(evs, rows)]
            cost = [-h["net"] for h in rows]
            tail_saved = _pct(hedged, 0.05) - _pct(stock, 0.05)
            out["puts"][f"markup{mk:g}_strike{k:.2f}"] = {
                "premium_pct_mean": round(st.mean(h["premium"] for h in rows), 4),
                "model_premium_pct_median": round(st.median(h["model_premium_mid"] for h in rows), 4),
                "net_cost_mean": round(st.mean(cost), 4), "net_cost_median": round(st.median(cost), 4),
                "share_put_paid_off": round(sum(h["net"] > 0 for h in rows) / len(rows), 3),
                "hedged": _dist(hedged),
                "p5_improvement": round(tail_saved, 4),
                "cost_per_point_of_p5_saved": round(st.mean(cost) / tail_saved, 3) if tail_saved > 0 else None,
            }
    return out


def verdict(summary: Dict) -> Optional[str]:
    """One line comparing the headline put to half size, on the same events."""
    if not summary.get("events"):
        return None
    p = summary["puts"].get(f"markup{HEADLINE['markup']:g}_strike{HEADLINE['strike']:.2f}")
    un, half = summary["unhedged"], summary["half_size"]
    return (f"{summary['events']} reports: unhedged mean {un['mean']:+.1%}, 5th pct {un['p5']:+.1%}, worst {un['worst']:+.1%}. "
            f"{HEADLINE['strike']:.0%} put (markup {HEADLINE['markup']:g}): costs {p['net_cost_mean']:.1%} a report, "
            f"mean {p['hedged']['mean']:+.1%}, 5th pct {p['hedged']['p5']:+.1%}, worst {p['hedged']['worst']:+.1%}. "
            f"Half size: mean {half['mean']:+.1%}, 5th pct {half['p5']:+.1%}, worst {half['worst']:+.1%}.")


def held_events(evs: List[Dict], half_spread: float) -> List[Dict]:
    """Per-event detail for the reports actually held, at the headline put."""
    out = []
    for e in evs:
        h = hedge(e["s0"], e["s1"], e["d0"], e["d1"], e["base_vol"],
                  move_sigma(e["prior_moves"], HEADLINE["markup"]), HEADLINE["strike"], half_spread)
        out.append({"symbol": e["symbol"], "report": e["report"], "move": round(e["move"], 4),
                    "put_premium": round(h["premium"], 4), "put_net": round(h["net"], 4),
                    "hedged": round(e["move"] + h["net"], 4), "half_size": round(e["move"] / 2, 4)})
    return out


# ── data and run ─────────────────────────────────────────────────────────────

def _load(path: Path, default):
    try:
        return contracts.read(path)
    except Exception:
        return default


def universe(history: List[Dict], trades: List[Dict]) -> Dict[str, List[str]]:
    diamond = sorted({str(r.get("ticker")) for r in history or [] if r.get("ticker")}
                     | {t["symbol"] for t in trades or [] if t.get("sleeve") and t.get("symbol")})
    book = sorted({t["symbol"] for t in trades or [] if not t.get("sleeve") and t.get("symbol")} - set(diamond))
    return {"diamond": diamond, "book": book}


def yahoo_closes(symbols: Iterable[str], start: date) -> Dict[str, tuple]:
    """{symbol: (dates, closes)} split- and dividend-adjusted, from yfinance."""
    import yfinance as yf
    out = {}
    for sym in symbols:
        try:
            h = yf.Ticker(identity.yahoo(sym)).history(start=start.isoformat(), auto_adjust=True)["Close"].dropna()
            if len(h):
                out[sym] = ([ts.date() for ts in h.index], [float(x) for x in h.values])
        except Exception as exc:
            logger.warning(f"{sym}: prices failed ({exc})")
    return out


def yahoo_reports(symbols: List[str]) -> Dict[str, List[date]]:
    from backtest import load_earnings                     # cached with the daily eval's dates
    return load_earnings(symbols)


def run(today: date, history: List[Dict], trades: List[Dict],
        prices: Callable = yahoo_closes, reports: Callable = yahoo_reports) -> Dict:
    start = today - timedelta(days=365 * YEARS)
    groups = universe(history, trades)
    syms = groups["diamond"] + groups["book"]
    px = prices(syms, start - timedelta(days=200))          # room for vol and earlier report moves
    reps = reports(syms)
    by = {"diamond": [], "book": [], "sleeve_held": [], "book_held": []}
    for g, members in groups.items():
        for sym in members:
            if sym not in px:
                continue
            for e in events(sym, *px[sym], reps.get(sym) or [], start):
                by[g].append(e)
                rep = date.fromisoformat(e["report"])
                if held_through(trades, sym, rep, sleeve=True):
                    by["sleeve_held"].append(e)
                if held_through(trades, sym, rep, sleeve=False):
                    by["book_held"].append(e)
    summary = {g: summarize(evs, SPREADS[g]) for g, evs in by.items()}
    return {
        "generated_at": today.isoformat(), "start": start.isoformat(), "end": today.isoformat(),
        "symbols": {g: len(v) for g, v in groups.items()},
        "missing_prices": sorted(set(syms) - set(px)),
        "headline": HEADLINE,
        "verdicts": {g: verdict(s) for g, s in summary.items()},
        "summary": summary,
        "held": {g: held_events(by[g], SPREADS[g]) for g in ("sleeve_held", "book_held")},
        "about": "Modeled puts (Black-Scholes, report move from the stock's own past reports x markup), not "
                 "historical option prices; the put shadow (put_shadow.py) checks the model against real "
                 "quotes. Fractions of the stock price per report window. Research only, no orders.",
    }


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
    out = run(date.today(), _load(DIAMOND_HISTORY, []), _load(TRADES, []))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1, default=str))
    print(json.dumps({"symbols": out["symbols"], "verdicts": out["verdicts"]}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
