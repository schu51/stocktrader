"""
Daily Eval
==========
The bot's evening report card and the experiment loop (2026-11-10).

Runs after the post-market reconcile, weekdays. Three parts:

  1. Live scorecard: closed trades, win rate, profit factor, expectancy, how far
     stop exits filled below their stop, account vs SPY, drawdown. Over the
     last 20 trades, the last 30 days and since the current rules went live
     (research/challengers.json "rules_versions").

  2. Tournament: the live rules ("champion", read from the same config the bot
     trades on) and every challenger in research/challengers.json are replayed
     by backtest.py over the last three years. A challenger is "promotable"
     when it clears every gate in GATES against the champion; after
     PROMOTE_STREAK straight evals it is "ready to propose" and the evening
     Claude review opens a pull request for Alex. Nothing here changes the
     live rules.

  3. Tracking: the live account against the champion's own backtest over the
     same 30 days. A large gap means the live bot is not doing what the rules
     say (fills, missed stops, data faults), not that the rules are bad.

Outputs: docs/data/eval_latest.json, docs/data/eval_history.json (one row per
day), docs/data/eval_report.md. Places no orders.

    python daily_eval.py                  # full eval
    python daily_eval.py --live-only      # scorecard only (no market data needed)
"""

import argparse
import json
import contracts
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.resolve()
DOCS = ROOT / "docs" / "data"
CHALLENGERS = ROOT / "research" / "challengers.json"
LATEST, HISTORY, REPORT = DOCS / "eval_latest.json", DOCS / "eval_history.json", DOCS / "eval_report.md"

YEARS = 3
RECENT_MONTHS = 6
PROMOTE_STREAK = 5          # straight evals a challenger must clear every gate
MAX_CHANGED_KNOBS = 2       # a challenger may differ from the live rules in at most this many settings
ALLOWED_KNOBS = {           # backtest.Backtest arguments a challenger may change
    "breakeven_at", "trail_at", "trail_pct", "min_spy_above_50ma", "min_above_50ma", "atr_stop",
    "exit_before_earnings", "earnings_keep_gain", "earnings_blackout", "risk_per_trade", "max_atr_pct",
    "stale_days", "stale_gain", "exit_confirm", "exit_line", "w_rs", "w_thesis", "open_limit", "min_adx",
    "pullback_entry", "exit_grace_days", "entry_trigger",
}
# Promotion gates, challenger vs champion on the same three-year replay
GATES = {
    "min_trades": 200,              # enough closed trades to mean anything
    "profit_factor_gain": 0.10,     # at least this much better
    "max_cagr_loss": 0.02,          # annual return no more than 2 points lower
    "max_drawdown_loss": 0.01,      # max drawdown no more than 1 point deeper
    "max_win_rate_loss": 0.02,      # win rate no more than 2 points lower
}
ALERTS = {
    "drawdown": -0.10,              # account 10% below its peak
    "stop_slip_pct": -3.0,          # last 10 stop exits filled 3%+ below their stops on average
    "tracking_gap": 0.05,           # live vs champion backtest, last 30 days, 5 points apart
}


# ── live scorecard ───────────────────────────────────────────────────────────

def trade_stats(trades: List[Dict]) -> Dict:
    """Win rate, profit factor and expectancy of closed trades (pnl_usd / pnl_pct)."""
    t = [x for x in trades if x.get("pnl_usd") is not None]
    if not t:
        return {"trades": 0}
    wins = [x for x in t if x["pnl_usd"] > 0]
    losses = [x for x in t if x["pnl_usd"] <= 0]
    gross_loss = -sum(x["pnl_usd"] for x in losses)
    return {
        "trades": len(t),
        "win_rate": round(len(wins) / len(t), 3),
        "profit_factor": round(sum(x["pnl_usd"] for x in wins) / gross_loss, 2) if gross_loss > 0 else None,
        "avg_win_pct": round(float(np.mean([x["pnl_pct"] for x in wins])), 2) if wins else None,
        "avg_loss_pct": round(float(np.mean([x["pnl_pct"] for x in losses])), 2) if losses else None,
        "expectancy_pct": round(float(np.mean([x["pnl_pct"] for x in t])), 2),
        "pnl_usd": round(sum(x["pnl_usd"] for x in t), 2),
    }


def stop_at_fill(trade: Dict) -> Tuple[Optional[float], str]:
    """
    The broker stop a BROKER_STOP exit filled against, and how it is known.

    "recorded": the filled order's own stop_price (exit_logic.settle_broker_exits,
    from 2026-11-10). Older trades only logged the decision engine's stop, which
    was not the one at the broker, so their stop is rebuilt from the entry rule
    (stop_placement.entry_stop_dist, or the sleeve's own distance): "estimated".
    """
    if trade.get("exit_stop"):
        return float(trade["exit_stop"]), "recorded"
    entry = trade.get("entry_price")
    if not entry:
        return None, ""
    if trade.get("sleeve"):
        try:
            return float(entry) * (1 - float(trade["stop_dist"])), "estimated"
        except (KeyError, TypeError, ValueError):
            return None, ""
    from stop_placement import entry_stop_dist
    return float(entry) * (1 - entry_stop_dist(trade)), "estimated"


def stop_fill_stats(trades: List[Dict], last: int = 10) -> Dict:
    """
    How far the last `last` broker stop exits filled below the stop that was live.

    An estimated stop the fill is above was raised by a trailing tier after entry
    (a sell stop cannot fill above its stop), so it is not the live one: those
    trades are left out rather than counted as fills above the stop.
    """
    rows = []
    for x in trades:
        if x.get("exit_reason") != "BROKER_STOP" or not x.get("exit_price"):
            continue
        stop, how = stop_at_fill(x)
        if not stop:
            continue
        slip = (x["exit_price"] / stop - 1) * 100
        if how == "estimated" and slip > 0.5:
            continue
        rows.append((x.get("exit_date") or "", x["symbol"], slip, how))
    rows = sorted(rows)[-last:]
    if not rows:
        return {"n": 0}
    slip = [r[2] for r in rows]
    worst = min(rows, key=lambda r: r[2])
    return {"n": len(rows), "mean_slip_pct": round(float(np.mean(slip)), 2),
            "median_slip_pct": round(float(np.median(slip)), 2),
            "worst_slip_pct": round(worst[2], 2),
            "gapped_over_2pct": sum(s < -2 for s in slip),
            "recorded": sum(r[3] == "recorded" for r in rows),
            "worst": worst[1]}


def account_stats(history: List[Dict], spy: Optional[pd.Series], since: date) -> Dict:
    """Account return since `since` (from the daily run log) vs SPY, and drawdown from the peak."""
    rows = [h for h in history if h.get("portfolio_value")]
    if not rows:
        return {}
    eq = pd.Series({pd.Timestamp(h["date"]): float(h["portfolio_value"]) for h in rows}).sort_index()
    out = {"value": round(float(eq.iloc[-1]), 2), "as_of": eq.index[-1].date().isoformat(),
           "drawdown_from_peak": round(float(eq.iloc[-1] / eq.max() - 1), 4)}
    window = eq[eq.index >= pd.Timestamp(since)]
    before = eq[eq.index < pd.Timestamp(since)]
    base = before.iloc[-1] if len(before) else (window.iloc[0] if len(window) else None)
    if base is not None and len(window):
        out["return"] = round(float(window.iloc[-1] / base - 1), 4)
        if spy is not None and len(spy):
            s = spy.dropna()
            s0 = s[s.index < pd.Timestamp(since)]
            s1 = s[s.index <= window.index[-1]]
            if len(s0) and len(s1):
                out["spy_return"] = round(float(s1.iloc[-1] / s0.iloc[-1] - 1), 4)
    return out


def live_scorecard(trades: List[Dict], history: List[Dict], today: date, rules_since: date,
                   spy: Optional[pd.Series] = None) -> Dict:
    closed = sorted([t for t in trades if t.get("status") == "CLOSED"], key=lambda t: t.get("exit_date") or "")
    d30 = (today - timedelta(days=30)).isoformat()
    return {
        "open_positions": sum(1 for t in trades if t.get("status") == "OPEN"),
        "closed_today": trade_stats([t for t in closed if t.get("exit_date") == today.isoformat()]),
        "last_20_trades": trade_stats(closed[-20:]),
        "last_30_days": trade_stats([t for t in closed if (t.get("exit_date") or "") >= d30]),
        "current_rules": trade_stats([t for t in closed if (t.get("entry_date") or "") >= rules_since.isoformat()]),
        "all_time": trade_stats(closed),
        "stop_fills": stop_fill_stats(closed),
        "account_30d": account_stats(history, spy, today - timedelta(days=30)),
        "account_current_rules": account_stats(history, spy, rules_since),
    }


# ── tournament ───────────────────────────────────────────────────────────────

def live_rules() -> Dict:
    """Backtest arguments for the rules the bot trades on today, read from the live config."""
    from config import EntryRules
    import rotation
    weights = contracts.read((DOCS / "weights.json")).get("active") or {}
    exps = contracts.read((DOCS / "experiments.json")).get("experiments") or {}
    atr = exps.get("atr_stop") or {}
    room = exps.get("ma50_room") or {}
    rules = EntryRules()
    return {
        "w_rs": float(weights.get("w_rs", 0.6)), "w_thesis": float(weights.get("w_thesis", 0.4)),
        "atr_stop": float((atr.get("params") or {}).get("multiple", 2.5)) if atr.get("state") == "adopted" else 0.0,
        "min_above_50ma": 0.06 if room.get("state") == "adopted" else 0.0,
        "stale_days": rotation.STALE_DAYS, "stale_gain": rotation.STALE_GAIN_PCT,
        "min_spy_above_50ma": rules.min_spy_above_50ma,
        "earnings_blackout": rules.earnings_blackout_days,
    }


def check_challenger(change: Dict) -> Optional[str]:
    """Why a challenger is invalid, or None."""
    bad = set(change) - ALLOWED_KNOBS
    if bad:
        return f"unknown settings {sorted(bad)}"
    knobs = {k for k in change if k not in ("trail_pct", "earnings_keep_gain", "stale_gain", "w_thesis")}
    if len(knobs) > MAX_CHANGED_KNOBS:
        return f"changes {len(knobs)} settings (max {MAX_CHANGED_KNOBS})"
    return None


def summarize_run(equity: pd.Series, trades: pd.DataFrame, today: date) -> Dict:
    """Three-year, half-by-half and recent results of one replay."""
    from backtest import cagr, max_drawdown, summarize_trades
    def ret(a, b=None):
        e = equity[equity.index >= pd.Timestamp(a)]
        if b is not None:
            e = e[e.index < pd.Timestamp(b)]
        prev = equity[equity.index < pd.Timestamp(a)]
        base = prev.iloc[-1] if len(prev) else (e.iloc[0] if len(e) else np.nan)
        return round(float(e.iloc[-1] / base - 1), 4) if len(e) else None
    mid = equity.index[0] + (equity.index[-1] - equity.index[0]) / 2
    recent = (pd.Timestamp(today) - pd.DateOffset(months=RECENT_MONTHS)).date()
    t = summarize_trades(trades) if len(trades) else {"trades": 0}
    halves = {}
    for name, part in (("first_half", trades[trades["entry_date"] < mid.date().isoformat()] if len(trades) else trades),
                       ("second_half", trades[trades["entry_date"] >= mid.date().isoformat()] if len(trades) else trades)):
        s = summarize_trades(part) if len(part) else {"trades": 0}
        halves[name] = {"trades": s.get("trades", 0), "profit_factor": s.get("profit_factor"),
                        "win_rate": s.get("win_rate")}
    m = equity.resample("ME").last().pct_change().dropna()
    return {
        "trades": t.get("trades", 0), "win_rate": t.get("win_rate"), "profit_factor": t.get("profit_factor"),
        "avg_win_pct": t.get("avg_win_pct"), "avg_loss_pct": t.get("avg_loss_pct"),
        "cagr": round(cagr(equity), 4), "max_drawdown": round(max_drawdown(equity), 4),
        "worst_month": round(float(m.min()), 4) if len(m) else None,
        "avg_month": round(float(m.mean()), 4) if len(m) else None,
        "recent_return": ret(recent), "last_30d_return": ret(today - timedelta(days=30)),
        **halves,
    }


def gates(ch: Dict, champ: Dict) -> Dict[str, bool]:
    """Every promotion gate, challenger vs champion. All True = promotable today."""
    pf = lambda r: r.get("profit_factor") or 0.0
    out = {
        "enough_trades": ch["trades"] >= GATES["min_trades"],
        "profit_factor": pf(ch) >= pf(champ) + GATES["profit_factor_gain"],
        "annual_return": ch["cagr"] >= champ["cagr"] - GATES["max_cagr_loss"],
        "drawdown": ch["max_drawdown"] >= champ["max_drawdown"] - GATES["max_drawdown_loss"],
        "win_rate": (ch["win_rate"] or 0) >= (champ["win_rate"] or 0) - GATES["max_win_rate_loss"],
        "recent": (ch["recent_return"] or 0) >= (champ["recent_return"] or 0),
    }
    # Better in both halves of the three years, not one lucky stretch
    for half in ("first_half", "second_half"):
        out[f"pf_{half}"] = (ch[half]["profit_factor"] or 0) >= (champ[half]["profit_factor"] or 0)
    return out


def streaks(today_ok: Dict[str, bool], previous: Optional[Dict]) -> Dict[str, int]:
    """Consecutive evals each challenger has been promotable (reset on a miss or a new name)."""
    before = ((previous or {}).get("tournament") or {}).get("streaks") or {}
    return {name: (before.get(name, 0) + 1 if ok else 0) for name, ok in today_ok.items()}


def run_tournament(today: date, challengers: List[Dict], previous: Optional[Dict]) -> Dict:
    import backtest as bt
    start = (pd.Timestamp(today) - pd.DateOffset(years=YEARS)).date().isoformat()
    end = (today + timedelta(days=1)).isoformat()     # yfinance end is exclusive: include today's close
    universe = bt.load_universe()
    prices = bt.load_prices(list(universe), start, end)
    universe = {s: v for s, v in universe.items() if s in prices["Close"].columns}
    earnings = bt.load_earnings(list(universe))
    champ_args = live_rules()

    def replay(args: Dict):
        b = bt.Backtest(universe, prices, start, None, earnings=earnings, **args)
        b.run(progress_every=0)
        return pd.Series(b.equity), pd.DataFrame(b.trades)

    eq, tr = replay(champ_args)
    champion = {"rules": champ_args, **summarize_run(eq, tr, today)}
    spy = prices["Close"][bt.BENCHMARK]
    results, ok = {}, {}
    for c in challengers:
        why = check_challenger(c.get("change") or {})
        if why:
            results[c["name"]] = {"error": why}
            continue
        try:
            e, t = replay({**champ_args, **c["change"]})
        except Exception as ex:
            logger.exception(f"challenger {c['name']} failed")
            results[c["name"]] = {"error": str(ex)}
            continue
        r = summarize_run(e, t, today)
        g = gates(r, champion)
        ok[c["name"]] = all(g.values())
        results[c["name"]] = {"change": c["change"], "why": c.get("why"), **r, "gates": g,
                              "promotable_today": ok[c["name"]]}
    st = streaks(ok, previous)
    ready = sorted([n for n, k in st.items() if k >= PROMOTE_STREAK],
                   key=lambda n: -(results[n].get("profit_factor") or 0))
    return {"start": start, "champion": champion, "challengers": results, "streaks": st,
            "ready_to_propose": ready, "promote_streak": PROMOTE_STREAK, "gates": GATES,
            "_champion_equity": eq, "_spy": spy}


# ── assemble ─────────────────────────────────────────────────────────────────

def tracking(live: Dict, champion_equity: Optional[pd.Series], today: date) -> Dict:
    """Live account vs the champion's own backtest, last 30 days."""
    acc = live.get("account_30d") or {}
    if champion_equity is None or "return" not in acc:
        return {}
    e = champion_equity
    since = pd.Timestamp(today - timedelta(days=30))
    base = e[e.index < since]
    if not len(base):
        return {}
    bt_ret = float(e.iloc[-1] / base.iloc[-1] - 1)
    return {"live_return": acc["return"], "backtest_return": round(bt_ret, 4),
            "gap": round(acc["return"] - bt_ret, 4)}


def alerts(ev: Dict) -> List[str]:
    out = []
    live = ev["live"]
    dd = (live.get("account_30d") or {}).get("drawdown_from_peak")
    if dd is not None and dd <= ALERTS["drawdown"]:
        out.append(f"Account is {-dd:.1%} below its peak")
    sf = live.get("stop_fills") or {}
    if sf.get("n", 0) >= 3 and sf["mean_slip_pct"] <= ALERTS["stop_slip_pct"]:
        out.append(f"Stop exits filled {-sf['mean_slip_pct']:.1f}% below their stop on average (last {sf['n']})")
    tg = ev.get("tracking") or {}
    if tg.get("gap") is not None and abs(tg["gap"]) >= ALERTS["tracking_gap"]:
        out.append(f"Live account {tg['live_return']:+.1%} vs backtest of the same rules {tg['backtest_return']:+.1%} "
                   f"over 30 days: live bot is not matching its rules")
    for name in (ev.get("tournament") or {}).get("ready_to_propose") or []:
        out.append(f"Challenger '{name}' has beaten the live rules for {PROMOTE_STREAK}+ straight evals: ready to propose")
    for name, r in ((ev.get("tournament") or {}).get("challengers") or {}).items():
        if r.get("error"):
            out.append(f"Challenger '{name}' could not run: {r['error']}")
    return out


def pct(x, d=1):
    return "n/a" if x is None else f"{x:+.{d}%}"


def to_markdown(ev: Dict) -> str:
    L = ev["live"]
    s = lambda k: L.get(k) or {}
    def line(name, st):
        if not st.get("trades"):
            return f"| {name} | 0 | | | | |"
        return (f"| {name} | {st['trades']} | {st['win_rate']:.0%} | {st['profit_factor'] if st['profit_factor'] is not None else '–'} "
                f"| {st['expectancy_pct']:+.2f}% | ${st['pnl_usd']:,.0f} |")
    out = [f"# Daily eval — {ev['date']}", "", f"Rules live: **{ev['rules_version']['name']}** since {ev['rules_version']['since']}", ""]
    if ev["alerts"]:
        out += ["**Alerts**", ""] + [f"- {a}" for a in ev["alerts"]] + [""]
    out += ["## Live trades", "", "| window | trades | win rate | profit factor | avg trade | P&L |", "|---|---|---|---|---|---|",
            line("closed today", s("closed_today")), line("last 20 trades", s("last_20_trades")),
            line("last 30 days", s("last_30_days")), line("current rules", s("current_rules")),
            line("all time", s("all_time")), ""]
    a30, ac = s("account_30d"), s("account_current_rules")
    out += [f"Account ${a30.get('value', 0):,.0f}, {pct(a30.get('drawdown_from_peak'))} from peak. "
            f"30 days: {pct(a30.get('return'))} vs SPY {pct(a30.get('spy_return'))}. "
            f"Since current rules: {pct(ac.get('return'))} vs SPY {pct(ac.get('spy_return'))}.", ""]
    sf = s("stop_fills")
    if sf.get("n"):
        out += [f"Stop exits (last {sf['n']}): filled {sf['mean_slip_pct']:+.1f}% vs their stop on average, "
                f"median {sf['median_slip_pct']:+.1f}%, worst {sf['worst_slip_pct']:+.1f}% ({sf['worst']}), "
                f"{sf['gapped_over_2pct']} gapped more than 2%. Stop known from the filled order for "
                f"{sf['recorded']} of {sf['n']}; the rest rebuilt from the entry rule.", ""]
    tg = ev.get("tracking") or {}
    if tg:
        out += [f"Tracking (30 days): live {pct(tg['live_return'])} vs backtest of the same rules {pct(tg['backtest_return'])}.", ""]
    t = ev.get("tournament")
    if t:
        c = t["champion"]
        out += ["## Tournament (3-year backtest)", "",
                "| rules | trades | win rate | PF | annual | max DD | worst month | last 6 mo | streak |",
                "|---|---|---|---|---|---|---|---|---|",
                f"| **live** | {c['trades']} | {c['win_rate']:.0%} | {c['profit_factor']:.2f} | {c['cagr']:.1%} | "
                f"{c['max_drawdown']:.1%} | {c['worst_month']:.1%} | {pct(c['recent_return'])} | |"]
        for name, r in t["challengers"].items():
            if r.get("error"):
                out.append(f"| {name} | error: {r['error']} | | | | | | | |")
                continue
            failed = [g for g, ok in r["gates"].items() if not ok]
            out.append(f"| {name} | {r['trades']} | {r['win_rate']:.0%} | {r['profit_factor']:.2f} | {r['cagr']:.1%} | "
                       f"{r['max_drawdown']:.1%} | {r['worst_month']:.1%} | {pct(r['recent_return'])} | "
                       f"{t['streaks'].get(name, 0)}/{t['promote_streak']}{'' if not failed else ' (fails: ' + ', '.join(failed) + ')'} |")
        out.append("")
    return "\n".join(out) + "\n"


def _read(path: Path, default):
    try:
        return contracts.read(path)
    except Exception:
        return default


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Daily eval: live scorecard + backtest tournament")
    p.add_argument("--live-only", action="store_true", help="skip the backtest tournament")
    p.add_argument("--date", default=None, help="eval date (default today)")
    args = p.parse_args(argv)
    today = date.fromisoformat(args.date) if args.date else date.today()

    cfg = _read(CHALLENGERS, {})
    version = (cfg.get("rules_versions") or [{"since": today.isoformat(), "name": "unknown"}])[-1]
    challengers = (cfg.get("challengers") or [])[: int(cfg.get("max_challengers", 6))]
    previous = _read(LATEST, None)

    tournament, spy, champ_eq = None, None, None
    if not args.live_only:
        tournament = run_tournament(today, challengers, previous)
        spy, champ_eq = tournament.pop("_spy"), tournament.pop("_champion_equity")

    live = live_scorecard(_read(DOCS / "trades.json", []), _read(DOCS / "history.json", []), today,
                          date.fromisoformat(version["since"]), spy)
    ev = {"date": today.isoformat(), "generated_at": datetime.now().isoformat(timespec="seconds"),
          "rules_version": version, "live": live, "tracking": tracking(live, champ_eq, today),
          "tournament": tournament}
    ev["alerts"] = alerts(ev)

    DOCS.mkdir(parents=True, exist_ok=True)
    LATEST.write_text(json.dumps(ev, indent=1, default=str))
    REPORT.write_text(to_markdown(ev))
    hist = [h for h in _read(HISTORY, []) if h.get("date") != ev["date"]]
    row = {"date": ev["date"], "rules": version["name"],
           "live_last20": live["last_20_trades"], "live_current_rules": live["current_rules"],
           "account": live["account_30d"], "stop_fills": live["stop_fills"], "tracking": ev["tracking"],
           "alerts": ev["alerts"]}
    if tournament:
        row["champion"] = {k: tournament["champion"][k] for k in
                           ("trades", "win_rate", "profit_factor", "cagr", "max_drawdown", "recent_return")}
        row["challengers"] = {n: {k: r.get(k) for k in ("win_rate", "profit_factor", "cagr", "max_drawdown",
                                                         "recent_return", "promotable_today")}
                              for n, r in tournament["challengers"].items()}
    HISTORY.write_text(json.dumps((hist + [row])[-400:], indent=1, default=str))
    print(REPORT.read_text())
    return 0


if __name__ == "__main__":
    sys.exit(main())
