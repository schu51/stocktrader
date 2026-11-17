"""
Picker & filter lab
===================
Replays the live rules ("champion") and a batch of variants of the stock
picker and the market filter over the same three-year, survivorship-free
history daily_eval.py uses, and scores each variant against the champion with
the tournament's own gates (daily_eval.gates). Places no orders and changes no
rules: it only writes research/picker_lab/results/.

Unlike a tournament challenger, a lab variant may change any number of
settings: the lab is for finding out what works; a winner graduates to
research/challengers.json (at most two settings) to earn promotion there.

    python picker_lab.py --variants research/picker_lab/round1.json --tag round1

Variants file: {"round": "...", "question": "...", "variants": [{"name", "why", "change": {...}}]}
"""

import argparse
import inspect
import json
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

import daily_eval as de

ROOT = Path(__file__).parent.resolve()
OUT = ROOT / "research" / "picker_lab" / "results"
logger = logging.getLogger("picker_lab")

# Settings a lab variant must not touch: they are run plumbing, not rules
NOT_RULES = {"self", "universe", "prices", "start", "end", "capital", "quiet", "earnings", "members"}


def rule_knobs() -> set:
    import backtest as bt
    return set(inspect.signature(bt.Backtest.__init__).parameters) - NOT_RULES


def check_variant(v: Dict, knobs: Optional[set] = None) -> Optional[str]:
    """Why a variant is invalid, or None."""
    knobs = rule_knobs() if knobs is None else knobs
    if not v.get("name"):
        return "no name"
    change = v.get("change")
    if not isinstance(change, dict) or not change:
        return "no change"
    bad = set(change) - knobs
    return f"unknown settings {sorted(bad)}" if bad else None


def recent_folds(folds: List[Dict], n: int = 2) -> List[Dict]:
    """The last n folds that have trades (the last one is often a stub with none)."""
    return [f for f in folds or [] if f["trades"]][-n:]


def verdict(r: Dict, champ: Dict) -> Dict:
    """Gates against the champion plus the lab's own read: is it better, and in the recent regime?"""
    g = de.gates(r, champ)
    fw = de.fold_wins(r.get("folds"), champ.get("folds"))
    rf, cf = recent_folds(r.get("folds")), recent_folds(champ.get("folds"))
    pf = lambda xs: [x["profit_factor"] for x in xs]
    recent_better = bool(rf and cf and len(rf) == len(cf) and
                         all((a or 0) >= (b or 0) for a, b in zip(pf(rf), pf(cf))))
    return {"gates": g, "gates_passed": sum(g.values()), "gates_total": len(g),
            "folds_won": fw, "recent_folds": rf, "recent_folds_champion": cf,
            "recent_regime_better": recent_better,
            "pf_gain": round((r.get("profit_factor") or 0) - (champ.get("profit_factor") or 0), 3),
            "drawdown_change": round((r.get("max_drawdown") or 0) - (champ.get("max_drawdown") or 0), 4)}


def rank_key(row: Dict):
    """Robust first: gates passed, folds won, recent regime, then profit factor."""
    v = row.get("verdict") or {}
    return (-(v.get("gates_passed") or 0), -((v.get("folds_won") or {}).get("won") or 0),
            -int(bool(v.get("recent_regime_better"))), -(row.get("profit_factor") or 0))


def to_markdown(res: Dict) -> str:
    c = res["champion"]
    pct = lambda x: "—" if x is None else f"{x * 100:+.1f}%"
    num = lambda x: "—" if x is None else f"{x:.2f}"
    share = lambda x: "—" if x is None else f"{x * 100:.0f}%"
    lines = [f"# Picker lab — {res['tag']} ({res['date']})", "",
             res.get("question") or "", "",
             f"Replay {res['start']} → {res['date']}, survivorship-free universe. "
             f"Champion = live rules: PF {num(c.get('profit_factor'))}, win {share(c.get('win_rate'))}, "
             f"CAGR {pct(c.get('cagr'))}, max DD {pct(c.get('max_drawdown'))}, {c.get('trades')} trades. "
             f"Recent folds PF: {', '.join(num(f['profit_factor']) for f in recent_folds(c.get('folds')))}.", "",
             "| Variant | Trades | Win | PF | CAGR | Max DD | Recent folds PF | Folds won | Gates | Recent regime better |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for row in res["variants"]:
        if row.get("error"):
            lines.append(f"| {row['name']} | error: {row['error']} | | | | | | | | |")
            continue
        v = row["verdict"]
        lines.append(
            f"| {row['name']} | {row['trades']} | {share(row.get('win_rate'))} | {num(row.get('profit_factor'))} | "
            f"{pct(row.get('cagr'))} | {pct(row.get('max_drawdown'))} | "
            f"{', '.join(num(f['profit_factor']) for f in v['recent_folds'])} | "
            f"{v['folds_won']['won']}/{v['folds_won']['counted']} | {v['gates_passed']}/{v['gates_total']} | "
            f"{'yes' if v['recent_regime_better'] else 'no'} |")
    lines += ["", "Ranked robust-first: gates passed, half-year folds won, both recent folds at least as good, "
              "then profit factor. Survivorship-free replay; 10 bps slippage per side. "
              "A winner still has to earn promotion in the daily tournament."]
    return "\n".join(lines) + "\n"


def run(variants_file: Path, tag: str, today: Optional[date] = None) -> Dict:
    import backtest as bt
    import contracts
    today = today or date.today()
    spec = contracts.read(variants_file)
    variants = spec.get("variants") or []
    knobs = rule_knobs()

    start = (pd.Timestamp(today) - pd.DateOffset(years=de.YEARS)).date().isoformat()
    end = (today + timedelta(days=1)).isoformat()
    universe, prices, members = bt.load_backtest_data(start, end, de.UNIVERSE_MODE)
    earnings = bt.load_earnings(list(universe))
    champ_args = de.live_rules()

    def replay(args: Dict):
        b = bt.Backtest(universe, prices, start, None, earnings=earnings, members=members, **args)
        b.run(progress_every=0)
        return pd.Series(b.equity), pd.DataFrame(b.trades), dict(b.counters)

    eq, tr, counters = replay(champ_args)
    champion = {"rules": champ_args, **de.summarize_run(eq, tr, today), "counters": counters}
    rows = []
    for v in variants:
        why = check_variant(v, knobs)
        if why:
            rows.append({"name": v.get("name") or "?", "error": why})
            continue
        try:
            e, t, cnt = replay({**champ_args, **v["change"]})
        except Exception as ex:
            logger.exception(f"variant {v['name']} failed")
            rows.append({"name": v["name"], "error": str(ex)[:200]})
            continue
        r = de.summarize_run(e, t, today)
        rows.append({"name": v["name"], "why": v.get("why"), "change": v["change"], **r,
                     "counters": cnt, "verdict": verdict(r, champion)})
        logger.info(f"{v['name']}: PF {r.get('profit_factor')} trades {r.get('trades')}")
    rows.sort(key=rank_key)
    return {"tag": tag, "date": today.isoformat(), "generated_at": datetime.now().isoformat(timespec="seconds"),
            "round": spec.get("round"), "question": spec.get("question"), "start": start,
            "champion": champion, "variants": rows}


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="Replay picker and filter variants against the live rules")
    p.add_argument("--variants", required=True, help="variants JSON file")
    p.add_argument("--tag", required=True, help="name for the results files")
    p.add_argument("--date", default=None)
    a = p.parse_args(argv)
    res = run(Path(a.variants), a.tag, date.fromisoformat(a.date) if a.date else None)
    OUT.mkdir(parents=True, exist_ok=True)
    stem = f"{res['date']}_{a.tag}"
    (OUT / f"{stem}.json").write_text(json.dumps(res, indent=1, default=str))
    (OUT / f"{stem}.md").write_text(to_markdown(res))
    print(to_markdown(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
