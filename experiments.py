"""
Experiments
===========
Runs technical ideas as live trials and retires the ones that do not work,
on fixed rules, without anyone having to decide.

Two kinds of experiment:

  entry_signal   macd_cross, ema21_reclaim, breakout, adx25
                 Every stock the engine wants to buy is logged with all four
                 signals, bought or not, and scored weekly on what it did
                 over the next 10 sessions versus SPY.
                   observing  measured only, never blocks a buy
                   active     also a gate: only buy when the signal fired
                   adopted    a gate that has proven itself (still monitored)
                   dropped    off for good
                 At most one entry gate is on at a time.

  stop_arm       atr_stop
                 A/B test on real positions: new buys are split between a
                 volatility-sized entry stop (2.5 x ATR) and the fixed 8%.
                   trial      50/50 split
                   adopted    every new position
                   dropped    none

State lives in docs/data/experiments.json; the log of buy signals in
docs/data/signal_log.json. The live hook (on_buy_signal) never raises and
never blocks a buy if anything in here fails.

Usage:
    python experiments.py          # weekly: score, update states, save
"""

import hashlib
import json
import logging
import math
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.resolve()
REGISTRY_FILE = ROOT / "docs" / "data" / "experiments.json"
SIGNAL_LOG_FILE = ROOT / "docs" / "data" / "signal_log.json"

HORIZON = 10               # sessions a logged buy signal is scored over
MIN_SIGNAL_DAYS = 40       # paired days of evidence before any decision
MAX_SIGNAL_DAYS = 120      # an experiment that has not proven itself by here is dropped
MIN_ARM_TRADES = 20        # closed trades per stop arm before any decision
MAX_ARM_TRADES = 60
T_ADOPT = 2.0              # evidence strong enough to adopt
T_DROP = -1.0              # evidence bad enough to drop early
MAX_ACTIVE_GATES = 1
LOG_KEEP_DAYS = 400

GATING_STATES = ("active", "adopted")


def default_registry(today: date) -> Dict:
    """Starting states, from the three-year backtest of 2026-10-03 (research/backtest_summary_exp_*.json)."""
    started = today.isoformat()

    def exp(kind, state, note, **params):
        return {"kind": kind, "state": state, "started": started, "params": params, "note": note}
    return {
        "experiments": {
            "atr_stop": exp("stop_arm", "trial", "backtest 139% vs 124% control, same drawdown", multiple=2.5),
            "macd_cross": exp("entry_signal", "active", "backtest 119% vs 124% control, drawdown -13% vs -16%"),
            "ema21_reclaim": exp("entry_signal", "observing", "backtest 100% vs 124% control"),
            "breakout": exp("entry_signal", "observing", "backtest 47% vs 124% control; best per-trade quality"),
            "adx25": exp("entry_signal", "observing", "backtest 55% vs 124% control"),
        },
        "changes": [],
    }


def load_registry(path: Path = REGISTRY_FILE, today: Optional[date] = None) -> Dict:
    try:
        reg = json.loads(Path(path).read_text())
        if isinstance(reg.get("experiments"), dict) and reg["experiments"]:
            return reg
    except Exception:
        pass
    return default_registry(today or date.today())


def _write_json(path: Path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str))
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Live decisions
# ---------------------------------------------------------------------------

def stop_arm(trade_key: str, registry: Dict) -> str:
    """Which entry stop a new position gets. Same key, same answer."""
    state = (registry["experiments"].get("atr_stop") or {}).get("state")
    if state == "adopted":
        return "atr"
    if state == "trial":
        return "atr" if int(hashlib.sha256(trade_key.encode()).hexdigest(), 16) % 2 == 0 else "fixed"
    return "fixed"


def entry_gate(signals: Dict, registry: Dict) -> Optional[str]:
    """Name of a gating experiment this candidate fails, or None. An unknown signal never blocks."""
    for name, exp in registry["experiments"].items():
        if exp.get("kind") == "entry_signal" and exp.get("state") in GATING_STATES:
            if signals.get(name) is False:
                return name
    return None


def on_buy_signal(symbol: str, price_bars, day: date, registry_path: Path = REGISTRY_FILE,
                  log_path: Path = SIGNAL_LOG_FILE) -> Dict:
    """
    Live hook, called for every stock the decision engine wants to buy.
    Logs its signals and returns {"signals", "stop_arm", "stop_dist", "blocked_by"}.
    Never raises: on any failure the buy proceeds under the standing rules.
    """
    try:
        from technical_signals import compute_signals, stop_distance
        signals = compute_signals([b.close for b in price_bars], [b.high for b in price_bars],
                                  [b.low for b in price_bars], [b.volume for b in price_bars])
        registry = load_registry(registry_path, day)
        arm = stop_arm(f"{symbol}:{day.isoformat()}", registry)
        multiple = (registry["experiments"].get("atr_stop", {}).get("params") or {}).get("multiple", 2.5)
        dist = stop_distance(signals["atr_pct"], multiple) if arm == "atr" and signals.get("atr_pct") else None
        if dist is None:
            arm = "fixed"
        blocked_by = entry_gate(signals, registry)

        try:
            log = json.loads(Path(log_path).read_text())
        except Exception:
            log = []
        cutoff = (day - timedelta(days=LOG_KEEP_DAYS)).isoformat()
        log = [r for r in log if r.get("date", "") >= cutoff
               and not (r.get("date") == day.isoformat() and r.get("symbol") == symbol)]
        log.append({"date": day.isoformat(), "symbol": symbol, "price": float(price_bars[-1].close),
                    "signals": signals, "blocked_by": blocked_by})
        _write_json(log_path, log)
        return {"signals": signals, "stop_arm": arm, "stop_dist": dist, "blocked_by": blocked_by}
    except Exception as e:
        logger.warning(f"Experiment hook failed for {symbol}: {e} — buying under the standing rules")
        return {"signals": None, "stop_arm": "fixed", "stop_dist": None, "blocked_by": None}


# ---------------------------------------------------------------------------
# Weekly evaluation
# ---------------------------------------------------------------------------

def _mean_t(values: List[float], overlap: int = 1) -> Tuple[float, float]:
    """Mean and t-statistic; `overlap` shrinks the sample for overlapping return windows."""
    n = len(values)
    if n < 2:
        return (values[0] if values else float("nan")), float("nan")
    mean = sum(values) / n
    sd = math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1))
    if sd == 0:
        return mean, float("nan")
    return mean, mean / (sd / math.sqrt(max(n / overlap, 1.0)))


def signal_evidence(rows, name: str, started: str) -> Dict:
    """Per day: mean 10-session excess return of candidates where the signal fired, minus where it did not."""
    col = f"excess_{HORIZON}d"
    if rows is None or len(rows) == 0 or name not in rows.columns or col not in rows.columns:
        return {"days": 0, "mean": None, "t": None}
    frame = rows[(rows["date"] >= started)].dropna(subset=[name, col])
    diffs = []
    for _, day in frame.groupby("date"):
        fired, quiet = day[day[name].astype(bool)], day[~day[name].astype(bool)]
        if len(fired) and len(quiet):
            diffs.append(float(fired[col].mean() - quiet[col].mean()))
    if not diffs:
        return {"days": 0, "mean": None, "t": None}
    mean, t = _mean_t(diffs, overlap=HORIZON)
    return {"days": len(diffs), "mean": mean, "t": None if math.isnan(t) else t}


def arm_evidence(trades: List[Dict], started: str) -> Dict:
    """Closed trades opened since the trial began, by stop arm."""
    arms = {"atr": [], "fixed": []}
    for t in trades:
        if (t.get("status") == "CLOSED" and t.get("stop_arm") in arms and t.get("pnl_pct") is not None
                and str(t.get("entry_date") or "") >= started):
            arms[t["stop_arm"]].append(float(t["pnl_pct"]))
    out = {"n_atr": len(arms["atr"]), "n_fixed": len(arms["fixed"]), "difference": None, "t": None}
    if min(out["n_atr"], out["n_fixed"]) >= 2:
        (ma, _), (mf, _) = _mean_t(arms["atr"]), _mean_t(arms["fixed"])
        var = lambda xs, m: sum((x - m) ** 2 for x in xs) / (len(xs) - 1)
        se = math.sqrt(var(arms["atr"], ma) / len(arms["atr"]) + var(arms["fixed"], mf) / len(arms["fixed"]))
        out.update(mean_atr=ma, mean_fixed=mf, difference=ma - mf, t=(ma - mf) / se if se > 0 else None)
    return out


def _decide_signal(state: str, ev: Dict, slot_free: bool) -> Tuple[str, str]:
    days, mean, t = ev["days"], ev["mean"], ev["t"]
    if state == "dropped" or days < MIN_SIGNAL_DAYS or mean is None:
        return state, ""
    strong = t is not None and mean > 0 and t >= T_ADOPT
    weak = t is not None and mean < 0 and t <= T_DROP
    if state == "observing":
        if strong and slot_free:
            return "active", f"signal-fired candidates beat the rest by {mean:+.2%} over {days} days (t = {t:.2f})"
        if strong:
            return state, "qualifies, but an entry gate is already active"
        if days >= MAX_SIGNAL_DAYS:
            return "dropped", f"did not show an edge in {days} days ({mean:+.2%}, t = {t if t is None else round(t, 2)})"
        return state, ""
    if weak:
        return "dropped", f"signal-fired candidates did worse by {mean:+.2%} over {days} days (t = {t:.2f})"
    if state == "active":
        if strong:
            return "adopted", f"proved itself: {mean:+.2%} over {days} days (t = {t:.2f})"
        if days >= MAX_SIGNAL_DAYS:
            return "dropped", f"did not prove an edge in {days} days ({mean:+.2%})"
    return state, ""


def _decide_stop_arm(state: str, ev: Dict) -> Tuple[str, str]:
    n = min(ev["n_atr"], ev["n_fixed"])
    if state != "trial" or n < MIN_ARM_TRADES or ev["t"] is None:
        return state, ""
    diff, t = ev["difference"], ev["t"]
    if diff > 0 and t >= T_ADOPT:
        return "adopted", f"ATR-stop trades beat fixed-stop trades by {diff:+.2f}% per trade on {n}+ each (t = {t:.2f})"
    if diff < 0 and t <= T_DROP:
        return "dropped", f"ATR-stop trades did worse by {diff:+.2f}% per trade on {n}+ each (t = {t:.2f})"
    if n >= MAX_ARM_TRADES:
        return "dropped", f"did not beat the fixed stop after {n} trades per arm ({diff:+.2f}%)"
    return state, ""


def evaluate(registry: Dict, rows, trades: List[Dict], today: date) -> Tuple[Dict, List[Dict]]:
    """
    Score every experiment and apply the transition rules.
    `rows`: DataFrame of logged buy signals with forward returns (or a dict of
    such frames keyed by signal name). Returns (registry, changes made).
    """
    changes = []
    exps = registry["experiments"]
    for name, exp in exps.items():
        frame = rows.get(name) if isinstance(rows, dict) else rows
        if exp["kind"] == "entry_signal":
            ev = signal_evidence(frame, name, exp["started"])
            slot_free = sum(1 for e in exps.values()
                            if e["kind"] == "entry_signal" and e["state"] in GATING_STATES) < MAX_ACTIVE_GATES
            new_state, reason = _decide_signal(exp["state"], ev, slot_free)
        else:
            ev = arm_evidence(trades, exp["started"])
            new_state, reason = _decide_stop_arm(exp["state"], ev)

        if exp["state"] == "dropped":
            continue
        exp["evidence"] = {**ev, **({"note": reason} if reason and new_state == exp["state"] else {})}
        exp["last_evaluated"] = today.isoformat()
        if new_state != exp["state"]:
            change = {"date": today.isoformat(), "experiment": name, "from": exp["state"],
                      "to": new_state, "reason": reason}
            exp["state"] = new_state
            changes.append(change)
            registry.setdefault("changes", []).append(change)
    return registry, changes


# ---------------------------------------------------------------------------
# Weekly job
# ---------------------------------------------------------------------------

def scored_log(log: List[Dict]):
    """The signal log as a DataFrame with each signal as a column and forward excess returns attached."""
    import pandas as pd
    from candidate_outcomes import BENCHMARK, forward_return
    from technical_signals import SIGNAL_NAMES
    if not log:
        return pd.DataFrame()
    import yfinance as yf
    symbols = sorted({r["symbol"] for r in log})
    start = (pd.Timestamp(min(r["date"] for r in log)) - pd.Timedelta(days=5)).strftime("%Y-%m-%d")
    raw = yf.download(symbols + [BENCHMARK], start=start, auto_adjust=True, progress=False, threads=True)
    opens, closes = raw["Open"], raw["Close"]
    rows = []
    for r in log:
        sym, day = r["symbol"], date.fromisoformat(r["date"])
        own = bench = None
        if sym in closes.columns:
            own = forward_return(opens[sym].dropna(), closes[sym].dropna(), day, HORIZON)
        bench = forward_return(opens[BENCHMARK].dropna(), closes[BENCHMARK].dropna(), day, HORIZON)
        row = {"date": r["date"], "symbol": sym,
               f"excess_{HORIZON}d": own - bench if own is not None and bench is not None else None}
        row.update({name: (r.get("signals") or {}).get(name) for name in SIGNAL_NAMES})
        rows.append(row)
    return pd.DataFrame(rows)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
    today = date.today()
    registry = load_registry(REGISTRY_FILE, today)
    try:
        log = json.loads(SIGNAL_LOG_FILE.read_text())
    except Exception:
        log = []
    try:
        trades = json.loads((ROOT / "docs" / "data" / "trades.json").read_text())
    except Exception:
        trades = []

    registry, changes = evaluate(registry, scored_log(log), trades, today)
    registry["generated_at"] = datetime.now().isoformat()
    registry["signals_logged"] = len(log)
    _write_json(REGISTRY_FILE, registry)

    for name, exp in registry["experiments"].items():
        logger.info(f"{name}: {exp['state']}  {exp.get('evidence', '')}")
    for c in changes:
        logger.info(f"CHANGE {c['experiment']}: {c['from']} -> {c['to']} — {c['reason']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
