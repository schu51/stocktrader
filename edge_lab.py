"""
Edge Lab
========
Is there a real edge here, or just market exposure? This runs a few simple,
well-documented strategies and the live rules over the same survivorship-free
history (market_history.py: every US stock day by day, delisted ones
included) and holds every one of them to the same strict bar.

Strategies (all long-only, rebalanced at month-end closes, costs on turnover):
  spy               buy and hold SPY (benchmark)
  ew_universe       every stock in the day's universe, equal weight (benchmark:
                    the same stocks with no picking)
  trend_spy         SPY while it closes above its 200-day average at month-end,
                    otherwise the defensive asset (IEF if Alpaca has it, else cash)
  mom_decile        12-1 month momentum: the top 10% of the day's universe by
                    return from 12 months ago to 1 month ago, equal weight
  mom_top50         the same ranking, top 50 names only (a size a bot can hold)
  mom_decile_trend  mom_decile while SPY is above its 200-day, else defensive
  champion          the live rules (daily_eval.live_rules) replayed by backtest.py

Earnings drift (post-earnings-announcement drift: prices keep moving for weeks
in the direction of an earnings surprise). Each month-end, among covered stocks
whose latest quarterly report is at most DRIFT_FRESH_DAYS old, the top 10% by
  drift_sue         standardized unexpected earnings: the quarter's EPS change on
                    a year earlier, over the spread of earlier such changes
                    (fundamentals_pit.py, SEC XBRL, dated when it became public)
  drift_ear         the earnings announcement return: the stock's move from two
                    days before the report to the day after, minus SPY's
  drift_sue_ear     both: SUE rank + announcement-return rank
Coverage: SEC fundamentals and report dates exist only for companies still
listed today, so these three are drawn from SURVIVORS. They are judged against
ew_covered (the same survivors, equal weight) instead of ew_universe, which
cancels most of that bias; what remains favours them a little.

"Real edge" is not "beat SPY by a bit". A strategy that beats SPY by holding
more market risk (beta), or by holding smaller stocks than SPY does, has no
edge; and with several strategies tested at once, one will look good by luck.
So every strategy is judged on its alpha from monthly returns regressed on
BOTH passive alternatives, SPY and the equal-weight universe (CAPM alpha
against SPY alone is reported too), and passes only if ALL of these hold
(EDGE_BAR):
  - alpha at least 4%/yr
  - alpha t-stat (Newey-West, 3 lags) at least 3.0: the multiple-testing bar
    finance research uses for new factors, not the usual 2.0
  - alpha positive in at least 70% of calendar years
  - still at least 2%/yr alpha with t >= 2 at double the trading costs
  - max drawdown no more than 5 points worse than SPY's
  - Sharpe at least SPY's + 0.2

Places no orders and changes no rules. Writes research/edge_lab/results.json
and research/edge_lab/report.md.

Usage:
    python edge_lab.py                     # 2017-01-01 to today, all strategies
    python edge_lab.py --start 2020-01-01 --skip-champion
"""

import argparse
import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.resolve()
OUT_DIR = ROOT / "research" / "edge_lab"
BENCHMARK = "SPY"
DEFENSIVE = "IEF"                      # 7-10y Treasuries; cash (0%) when Alpaca has no bars
NOT_STOCKS = {"SPY", "QQQ", "^VIX", "IEF", "BIL"}
BENCHMARKS = ("spy", "ew_universe", "ew_covered")    # reported, never judged
DRIFT_FRESH_DAYS = 60                  # a report older than this carries no drift signal
FUNDAMENTALS_FIRST_YEAR = 2014         # SUE needs ~3 years of quarters before 2017

MOM_LOOKBACK = 252                     # 12 months
MOM_SKIP = 21                          # skip the latest month (short-term reversal)
TREND_SMA = 200
COST_BPS = 10.0                        # per side, per traded dollar (backtest.py's slippage default)
NW_LAGS = 3

EDGE_BAR = {
    "min_alpha": 0.04,                 # annualized CAPM alpha vs SPY
    "min_alpha_t": 3.0,
    "min_positive_years": 0.70,
    "stress_min_alpha": 0.02,          # at 2x costs
    "stress_min_alpha_t": 2.0,
    "max_dd_worse_than_spy": 0.05,
    "min_sharpe_over_spy": 0.20,
}


# ── portfolio mechanics ──────────────────────────────────────────────────────

def month_end_dates(index: pd.DatetimeIndex) -> List[pd.Timestamp]:
    """The last trading day of each month in `index`."""
    s = pd.Series(index, index=index)
    return list(s.groupby([index.year, index.month]).max())


def daily_returns(closes: pd.DataFrame) -> pd.DataFrame:
    """Close-to-close returns. A stock whose prices stop earns 0 after its last close
    (held as cash at that price, as backtest.py closes delisted positions)."""
    return closes.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan).fillna(0.0)


def simulate(returns: pd.DataFrame, targets: Dict[pd.Timestamp, Dict[str, float]],
             start: pd.Timestamp, cost_bps: float = COST_BPS, capital: float = 100_000.0) -> pd.Series:
    """
    Daily equity of a portfolio that is set to `targets[d]` (weights summing to
    <= 1, the rest in cash at 0%) at the close of each rebalance day d and
    left to drift in between. Costs: cost_bps per side on every traded dollar.
    """
    days = returns.index[returns.index >= start]
    equity = capital
    cash = capital
    vals: Dict[str, float] = {}
    out = {}
    for d in days:
        if vals:
            r = returns.loc[d]
            for s in vals:
                vals[s] *= 1.0 + float(r.get(s, 0.0))
            equity = sum(vals.values()) + cash
        if d in targets:
            want = {s: w * equity for s, w in targets[d].items() if w > 0}
            traded = sum(abs(want.get(s, 0.0) - vals.get(s, 0.0)) for s in set(want) | set(vals))
            equity -= traded * cost_bps / 1e4
            vals = {s: w * equity for s, w in targets[d].items() if w > 0}
            cash = equity - sum(vals.values())
        out[d] = equity
    return pd.Series(out, dtype=float)


def momentum_scores(closes: pd.DataFrame, i: int, members: Optional[pd.Series],
                    lookback: int = MOM_LOOKBACK, skip: int = MOM_SKIP) -> pd.Series:
    """12-1 momentum at row i, using only closes up to row i. Members only, when given."""
    if i - lookback < 0:
        return pd.Series(dtype=float)
    past, recent = closes.iloc[i - lookback], closes.iloc[i - skip]
    score = (recent / past - 1.0).replace([np.inf, -np.inf], np.nan)
    score = score[closes.iloc[i].notna()]
    if members is not None:
        score = score[members.reindex(score.index).fillna(False).astype(bool)]
    score = score.drop(labels=[s for s in NOT_STOCKS if s in score.index], errors="ignore")
    return score.dropna()


def above_trend(spy: pd.Series, d: pd.Timestamp, n: int = TREND_SMA) -> bool:
    hist = spy.loc[:d].dropna()
    if len(hist) < n:
        return True
    return float(hist.iloc[-1]) > float(hist.iloc[-n:].mean())


def momentum_targets(closes: pd.DataFrame, members: Optional[pd.DataFrame], start: pd.Timestamp,
                     top_frac: Optional[float] = 0.10, top_n: Optional[int] = None,
                     trend: bool = False, defensive: Optional[str] = None) -> Dict:
    spy = closes[BENCHMARK]
    targets = {}
    pos = {d: k for k, d in enumerate(closes.index)}
    for d in month_end_dates(closes.index):
        if d < start:
            continue
        if trend and not above_trend(spy, d):
            targets[d] = {defensive: 1.0} if defensive else {}
            continue
        m = members.loc[d] if members is not None else None
        score = momentum_scores(closes, pos[d], m)
        if score.empty:
            targets[d] = {}
            continue
        k = top_n if top_n else max(1, int(round(len(score) * top_frac)))
        picks = score.nlargest(k).index
        targets[d] = {s: 1.0 / len(picks) for s in picks}
    return targets


def equal_weight_targets(closes: pd.DataFrame, members: Optional[pd.DataFrame], start: pd.Timestamp,
                         covered: Optional[set] = None) -> Dict:
    """Every stock in the day's universe (or its covered part), equal weight: the same stocks, no picking."""
    targets = {}
    for d in month_end_dates(closes.index):
        if d < start:
            continue
        live = covered_members(closes, members, d, covered)
        targets[d] = {x: 1.0 / len(live) for x in live} if live else {}
    return targets


def report_positions(index: pd.DatetimeIndex, report_dates: Dict[str, List]) -> Dict[str, np.ndarray]:
    """{ticker: sorted row positions of the first trading day on or after each report date}."""
    out = {}
    for sym, dates in (report_dates or {}).items():
        ts = pd.DatetimeIndex(sorted({pd.Timestamp(d) for d in dates or []}))
        pos = index.searchsorted(ts)
        pos = pos[pos < len(index)]
        if len(pos):
            out[sym] = np.unique(pos)
    return out


def ear_scorer(closes: pd.DataFrame, positions: Dict[str, np.ndarray],
               fresh_days: int = DRIFT_FRESH_DAYS) -> Callable[[str, int], Optional[float]]:
    """
    score(sym, i): the latest report's announcement return (close two sessions
    before the report day to the close after it, minus SPY's), using only
    closes through row i, or None if no report is fresh or the window is incomplete.
    """
    idx = closes.index
    c = closes.to_numpy(dtype=float)
    col = {s: k for k, s in enumerate(closes.columns)}
    spy = c[:, col[BENCHMARK]]

    def score(sym: str, i: int) -> Optional[float]:
        pos, k = positions.get(sym), col.get(sym)
        if pos is None or k is None:
            return None
        usable = pos[pos + 1 <= i]                    # the day after the report must be known by i
        if not len(usable):
            return None
        t = int(usable[-1])
        if t < 2 or (idx[i] - idx[t]).days > fresh_days:
            return None
        a, b = c[t - 2, k], c[t + 1, k]
        if not (np.isfinite(a) and np.isfinite(b)) or a <= 0:
            return None
        return float(b / a - 1.0 - (spy[t + 1] / spy[t - 2] - 1.0))
    return score


def sue_scorer(lookup: Callable, index: pd.DatetimeIndex,
               fresh_days: int = DRIFT_FRESH_DAYS) -> Callable[[str, int], Optional[float]]:
    """score(sym, i): SUE of the latest quarter public by row i's date, if at most fresh_days old."""
    def score(sym: str, i: int) -> Optional[float]:
        f = lookup(sym, index[i].date())
        if not f or f.get("sue") is None or f.get("days_since") is None or f["days_since"] > fresh_days:
            return None
        return float(f["sue"])
    return score


def combined_scorer(*scorers: Callable) -> Callable[[str, int], Optional[float]]:
    """Marker for drift_targets: rank each scorer separately and add the ranks (all must score)."""
    def score(sym: str, i: int):
        vals = [f(sym, i) for f in scorers]
        return None if any(v is None for v in vals) else vals
    score.n_parts = len(scorers)
    return score


def drift_targets(closes: pd.DataFrame, members: Optional[pd.DataFrame], start: pd.Timestamp,
                  scorer: Callable, covered: set, top_frac: float = 0.10, min_names: int = 10) -> Dict:
    """Each month-end: covered members with a fresh score, top `top_frac` equal weight (cash if under min_names)."""
    targets = {}
    pos = {d: k for k, d in enumerate(closes.index)}
    for d in month_end_dates(closes.index):
        if d < start:
            continue
        i = pos[d]
        names = [x for x in covered_members(closes, members, d, covered)]
        scored = {x: scorer(x, i) for x in names}
        scored = {x: v for x, v in scored.items() if v is not None}
        if len(scored) < min_names:
            targets[d] = {}
            continue
        if getattr(scorer, "n_parts", 0):
            parts = pd.DataFrame(scored, index=range(scorer.n_parts)).T
            ranking = parts.rank(pct=True).sum(axis=1)
        else:
            ranking = pd.Series(scored)
        k = max(1, int(round(len(ranking) * top_frac)))
        picks = ranking.nlargest(k).index
        targets[d] = {x: 1.0 / len(picks) for x in picks}
    return targets


def covered_members(closes: pd.DataFrame, members: Optional[pd.DataFrame], d: pd.Timestamp,
                    covered: Optional[set]) -> List[str]:
    """The day's universe (priced, member), limited to `covered` when given."""
    live = closes.loc[d].dropna().index
    if members is not None:
        m = members.loc[d].reindex(live).fillna(False).astype(bool)
        live = live[m.values]
    return [x for x in live if x not in NOT_STOCKS and (covered is None or x in covered)]


def trend_targets(closes: pd.DataFrame, start: pd.Timestamp, defensive: Optional[str]) -> Dict:
    spy = closes[BENCHMARK]
    return {d: ({BENCHMARK: 1.0} if above_trend(spy, d) else ({defensive: 1.0} if defensive else {}))
            for d in month_end_dates(closes.index) if d >= start}


# ── statistics ───────────────────────────────────────────────────────────────

def monthly_returns(equity: pd.Series) -> pd.Series:
    m = equity.groupby([equity.index.year, equity.index.month]).last()
    m.index = [pd.Timestamp(year=y, month=mo, day=1) for y, mo in m.index]
    return m.pct_change().dropna()


def newey_west_ols(y: np.ndarray, x: np.ndarray, lags: int = NW_LAGS) -> Dict:
    """OLS y = a + x @ b (x one or more columns) with Newey-West (Bartlett) standard errors."""
    x = np.asarray(x, dtype=float)
    x = x[:, None] if x.ndim == 1 else x
    X = np.column_stack([np.ones(len(y)), x])
    n = len(y)
    xtx_inv = np.linalg.pinv(X.T @ X)
    coef = xtx_inv @ X.T @ y
    e = y - X @ coef
    S = (X * e[:, None]).T @ (X * e[:, None])
    for L in range(1, min(lags, n - 1) + 1):
        w = 1.0 - L / (lags + 1.0)
        G = (X[L:] * e[L:, None]).T @ (X[:-L] * e[:-L, None])
        S += w * (G + G.T)
    cov = xtx_inv @ S @ xtx_inv
    se = np.sqrt(np.maximum(np.diag(cov), 0.0))
    t = np.divide(coef, se, out=np.full_like(coef, np.nan), where=se > 1e-12)
    return {"alpha": float(coef[0]), "betas": [float(b) for b in coef[1:]], "beta": float(coef[1]),
            "alpha_t": float(t[0]), "resid": e}


def max_drawdown(equity: pd.Series) -> float:
    return float((equity / equity.cummax() - 1.0).min())


def stats(equity: pd.Series, spy_equity: pd.Series, universe_equity: Optional[pd.Series] = None) -> Dict:
    """
    Return, risk and alpha from monthly returns.
      beta, capm_alpha, capm_alpha_t   against SPY alone
      alpha, alpha_t, positive_years   against SPY AND the equal-weight universe
                                       (when given): what is left after both
                                       passive alternatives, i.e. the stock picking
    Alphas are annualized (x12).
    """
    r = monthly_returns(equity)
    factors = {"spy": monthly_returns(spy_equity)}
    if universe_equity is not None:
        factors["universe"] = monthly_returns(universe_equity)
    df = pd.concat({"r": r, **factors}, axis=1, join="inner").dropna()
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    cagr = (equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1 if years > 0 else float("nan")
    vol = float(r.std() * np.sqrt(12))
    sharpe = float(r.mean() * 12 / vol) if vol > 0 else float("nan")
    out = {"cagr": float(cagr), "vol": vol, "sharpe": sharpe, "max_drawdown": max_drawdown(equity),
           "months": int(len(df))}
    if len(df) < 12:
        return out
    capm = newey_west_ols(df["r"].values, df["spy"].values)
    out.update(beta=capm["beta"], capm_alpha=capm["alpha"] * 12, capm_alpha_t=capm["alpha_t"])
    cols = [c for c in ("spy", "universe") if c in df]
    fit = newey_west_ols(df["r"].values, df[cols].values) if len(cols) > 1 else capm
    out.update(alpha=fit["alpha"] * 12, alpha_t=fit["alpha_t"],
               loadings={c: round(b, 3) for c, b in zip(cols, fit["betas"])})
    monthly_alpha = pd.Series(fit["alpha"] + fit["resid"], index=df.index)
    by_year = monthly_alpha.groupby(monthly_alpha.index.year).agg(["sum", "count"])
    by_year = by_year[by_year["count"] >= 6]["sum"]
    out["alpha_by_year"] = {int(y): round(float(v), 4) for y, v in by_year.items()}
    out["positive_years"] = float((by_year > 0).mean()) if len(by_year) else float("nan")
    return out


def verdict(s: Dict, stress: Optional[Dict], spy: Dict, bar: Dict = EDGE_BAR) -> Dict:
    """Which parts of the edge bar a strategy clears. `edge` is True only if all do."""
    def ok(v, cond):
        return bool(v is not None and not (isinstance(v, float) and np.isnan(v)) and cond(v))
    checks = {
        "alpha": ok(s.get("alpha"), lambda v: v >= bar["min_alpha"]),
        "alpha_t": ok(s.get("alpha_t"), lambda v: v >= bar["min_alpha_t"]),
        "consistency": ok(s.get("positive_years"), lambda v: v >= bar["min_positive_years"]),
        "survives_2x_costs": bool(stress) and ok(stress.get("alpha"), lambda v: v >= bar["stress_min_alpha"])
                             and ok(stress.get("alpha_t"), lambda v: v >= bar["stress_min_alpha_t"]),
        "drawdown": ok(s.get("max_drawdown"), lambda v: v >= spy["max_drawdown"] - bar["max_dd_worse_than_spy"]),
        "sharpe": ok(s.get("sharpe"), lambda v: v >= spy["sharpe"] + bar["min_sharpe_over_spy"]),
    }
    return {"edge": all(checks.values()), "checks": checks}


# ── running it ───────────────────────────────────────────────────────────────

def strategies(closes: pd.DataFrame, members: Optional[pd.DataFrame], start: pd.Timestamp,
               defensive: Optional[str], events: Optional[Dict] = None) -> Dict[str, Callable[[], Dict]]:
    out = {
        "spy": lambda: {d: {BENCHMARK: 1.0} for d in month_end_dates(closes.index) if d >= start},
        "ew_universe": lambda: equal_weight_targets(closes, members, start),
        "trend_spy": lambda: trend_targets(closes, start, defensive),
        "mom_decile": lambda: momentum_targets(closes, members, start, top_frac=0.10),
        "mom_top50": lambda: momentum_targets(closes, members, start, top_frac=None, top_n=50),
        "mom_decile_trend": lambda: momentum_targets(closes, members, start, top_frac=0.10,
                                                     trend=True, defensive=defensive),
    }
    out.update(drift_strategies(closes, members, start, events))
    return out


def drift_strategies(closes: pd.DataFrame, members: Optional[pd.DataFrame], start: pd.Timestamp,
                     events: Optional[Dict]) -> Dict[str, Callable[[], Dict]]:
    """
    events: {"report_dates": {ticker: [dates]}, "sue": fundamentals_pit.Lookup or None}.
    Covered = tickers with report dates (and fundamentals, when SUE is available).
    """
    if not events or not events.get("report_dates"):
        return {}
    positions = report_positions(closes.index, events["report_dates"])
    covered = set(positions)
    lookup = events.get("sue")
    if lookup is not None:
        covered &= set(getattr(lookup, "table", {}) or {})
    if not covered:
        return {}
    ear = ear_scorer(closes, positions)
    out = {"ew_covered": lambda: equal_weight_targets(closes, members, start, covered),
           "drift_ear": lambda: drift_targets(closes, members, start, ear, covered)}
    if lookup is not None:
        sue = sue_scorer(lookup, closes.index)
        out["drift_sue"] = lambda: drift_targets(closes, members, start, sue, covered)
        out["drift_sue_ear"] = lambda: drift_targets(closes, members, start, combined_scorer(sue, ear), covered)
    return out


def evaluate(closes: pd.DataFrame, members: Optional[pd.DataFrame], start: pd.Timestamp,
             defensive: Optional[str] = None, extra: Optional[Dict[str, pd.Series]] = None,
             cost_bps: float = COST_BPS, extra_stress: Optional[Dict[str, pd.Series]] = None,
             events: Optional[Dict] = None) -> Dict:
    """Run every strategy at cost_bps and 2x, compute stats and verdicts.
    `extra` adds precomputed equity curves (the champion); `extra_stress` their 2x-cost runs."""
    rets = daily_returns(closes)
    curves, stress_curves = {}, {}
    for name, make in strategies(closes, members, start, defensive, events).items():
        t = make()
        curves[name] = simulate(rets, t, start, cost_bps)
        stress_curves[name] = simulate(rets, t, start, cost_bps * 2)
    spy_eq, ew_eq = curves["spy"], curves["ew_universe"]
    for name, eq in (extra or {}).items():
        curves[name] = eq.loc[eq.index >= start]
    for name, eq in (extra_stress or {}).items():
        stress_curves[name] = eq.loc[eq.index >= start]
    spy = stats(spy_eq, spy_eq)

    def run_stats(eq: pd.Series, name: str) -> Dict:
        on = lambda bench: bench.reindex(eq.index).ffill()
        # A benchmark is not regressed on itself and the other benchmark. Drift strategies
        # pick from survivors only, so their no-picking benchmark is the same survivors.
        if name in BENCHMARKS:
            return stats(eq, on(spy_eq))
        picking_bench = curves["ew_covered"] if name.startswith("drift_") else ew_eq
        return stats(eq, on(spy_eq), on(picking_bench))

    results = {}
    for name, eq in curves.items():
        eq = eq.dropna()
        if len(eq) < 30:
            results[name] = {"error": "not enough history"}
            continue
        s = run_stats(eq, name)
        st = run_stats(stress_curves[name].dropna(), name) if name in stress_curves else None
        if st is not None:
            s["at_2x_costs"] = {k: st.get(k) for k in ("cagr", "alpha", "alpha_t")}
        if name not in BENCHMARKS:
            s["verdict"] = verdict(s, st, spy)
        results[name] = s
    return results


def champion_equity(start: str, universe, prices, members, slippage_bps: float = COST_BPS,
                    earnings=None) -> pd.Series:
    """The live rules replayed by backtest.py over the same history."""
    import backtest as bt
    import daily_eval
    earnings = earnings if earnings is not None else bt.load_earnings(list(universe))
    b = bt.Backtest(universe, prices, start, None, earnings=earnings, members=members,
                    slippage_bps=slippage_bps, **daily_eval.live_rules())
    b.run(progress_every=250)
    return pd.Series(b.equity, dtype=float)


def drift_events(earnings: Dict[str, List]) -> Dict:
    """Report dates plus point-in-time SUE (fundamentals_pit, built from SEC frames since
    FUNDAMENTALS_FIRST_YEAR). Without the SEC build, only the announcement-return strategy runs."""
    report_dates = {s: v for s, v in (earnings or {}).items() if v}
    lookup = None
    try:
        import fundamentals_pit
        table = fundamentals_pit.load_or_build(date.today(), earnings, first_year=FUNDAMENTALS_FIRST_YEAR)
        lookup = fundamentals_pit.Lookup(table)
    except Exception as e:
        logger.warning(f"Fundamentals build failed ({e}); drift_sue and drift_sue_ear are skipped")
    return {"report_dates": report_dates, "sue": lookup}


def add_defensive(prices: Dict[str, pd.DataFrame], fetch_from: str) -> Optional[str]:
    """Add the defensive ETF's closes from Alpaca. None (cash) if that fails."""
    try:
        import requests
        import market_history
        bars = market_history.fetch_bars([DEFENSIVE], fetch_from, requests.Session())
        prices["Close"] = prices["Close"].assign(**{DEFENSIVE: bars["Close"][DEFENSIVE].reindex(prices["Close"].index)})
        return DEFENSIVE
    except Exception as e:
        logger.warning(f"No {DEFENSIVE} bars ({e}); the defensive leg holds cash at 0%")
        return None


def report(results: Dict, meta: Dict) -> str:
    def pct(v):
        return "—" if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v * 100:+.1f}%"

    lines = [f"# Edge lab — {meta['date']}", "",
             f"History {meta['start']} to {meta['end']}, survivorship-free, costs {meta['cost_bps']:.0f} bps per side. "
             f"Defensive asset: {meta['defensive'] or 'cash at 0%'}.", "",
             "Alpha is what is left after BOTH passive alternatives: SPY and the equal-weight universe "
             "(the same stocks, no picking). CAPM alpha is against SPY alone, for reference.", "",
             "drift_* strategies pick from companies still listed today (SEC data covers no delisted "
             "companies), so their no-picking benchmark is ew_covered, the same survivors equal weight. "
             "A little survivorship bias remains in their favour.", "",
             "A real edge needs ALL of: alpha >= 4%/yr, alpha t >= 3.0, alpha positive in >= 70% of years, "
             "alpha >= 2%/yr with t >= 2 at 2x costs, drawdown within 5 pts of SPY, Sharpe >= SPY + 0.2.", "",
             "| strategy | CAGR | Sharpe | max DD | beta (SPY) | CAPM alpha | alpha/yr | alpha t | years + | 2x-cost alpha | edge? |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, s in results.items():
        if "error" in s:
            lines.append(f"| {name} | {s['error']} ||||||||||")
            continue
        v = s.get("verdict")
        failed = [k for k, okk in (v or {}).get("checks", {}).items() if not okk]
        edge = "benchmark" if v is None else ("**YES**" if v["edge"] else "no (" + ", ".join(failed) + ")")
        lines.append(f"| {name} | {pct(s['cagr'])} | {s['sharpe']:.2f} | {pct(s['max_drawdown'])} | "
                     f"{s.get('beta', float('nan')):.2f} | {pct(s.get('capm_alpha'))} | "
                     f"{'—' if v is None else pct(s.get('alpha'))} | "
                     f"{'—' if v is None else format(s.get('alpha_t', float('nan')), '.2f')} | "
                     f"{'—' if v is None else pct(s.get('positive_years'))} | "
                     f"{'—' if v is None else pct((s.get('at_2x_costs') or {}).get('alpha'))} | {edge} |")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Hold simple strategies and the live rules to a strict edge bar")
    p.add_argument("--start", default="2017-01-01")
    p.add_argument("--end", default=None)
    p.add_argument("--cost-bps", type=float, default=COST_BPS)
    p.add_argument("--skip-champion", action="store_true")
    p.add_argument("--skip-drift", action="store_true", help="skip the earnings-drift strategies (no SEC build)")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    import backtest as bt
    import market_history
    universe, prices, members = bt.load_backtest_data(args.start, args.end, "market")
    fetch_from = (pd.Timestamp(args.start) - pd.Timedelta(days=market_history.LOOKBACK_EXTRA_DAYS)).strftime("%Y-%m-%d")
    champ = champ_stress = None
    earnings = bt.load_earnings(list(universe)) if not (args.skip_champion and args.skip_drift) else {}
    if not args.skip_champion:
        champ = champion_equity(args.start, universe, prices, members, args.cost_bps, earnings)
        champ_stress = champion_equity(args.start, universe, prices, members, args.cost_bps * 2, earnings)
    defensive = add_defensive(prices, fetch_from)
    closes = prices["Close"]
    start = closes.index[closes.index >= pd.Timestamp(args.start)][0]
    events = None if args.skip_drift else drift_events(earnings)
    results = evaluate(closes, members, start, defensive, {"champion": champ} if champ is not None else None,
                       args.cost_bps, {"champion": champ_stress} if champ_stress is not None else None, events)
    meta = {"date": date.today().isoformat(), "generated_at": datetime.now().isoformat(),
            "start": str(start.date()), "end": str(closes.index[-1].date()), "cost_bps": args.cost_bps,
            "defensive": defensive, "edge_bar": EDGE_BAR,
            "drift_coverage": None if not events else {
                "tickers_with_report_dates": len(events.get("report_dates") or {}),
                "tickers_with_fundamentals": len(getattr(events.get("sue"), "table", {}) or {}),
                "sue": events.get("sue") is not None}}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "results.json").write_text(json.dumps({**meta, "results": results}, indent=2, default=float))
    (OUT_DIR / "report.md").write_text(report(results, meta))
    print(report(results, meta))
    return 0


if __name__ == "__main__":
    sys.exit(main())
