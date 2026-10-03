"""
Weekly Review
=============
One readable summary of what the system did and decided this week, posted as
a GitHub issue each Saturday so the feedback loop is visible: account and
trades, what the learning agent decided, what the candidate data says, which
macro theses are live, and anything that broke.

Reads only the JSON the other jobs already write. No network, no trading.

Usage:
    python weekly_review.py                         # print title + body
    python weekly_review.py --body-file review.md --title-file title.txt
    python weekly_review.py --failed-runs failed.json
"""

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).parent.resolve()
DOCS_DATA = ROOT / "docs" / "data"

PROBATION = 10
MIN_DATA_COVERAGE = 0.80


def _load(name: str):
    try:
        return json.loads((DOCS_DATA / name).read_text())
    except Exception:
        return None


def load_data(failed_runs_file: str = None) -> Dict:
    failed = []
    if failed_runs_file:
        try:
            failed = json.loads(Path(failed_runs_file).read_text())
        except Exception:
            failed = []
    return {
        "history": _load("history.json"), "trades": _load("trades.json"),
        "weights": _load("weights.json"), "learning_report": _load("learning_report.json"),
        "candidate_outcomes": _load("candidate_outcomes.json"), "macro_brief": _load("macro_brief.json"),
        "screener": _load("screener.json"), "failed_runs": failed,
    }


def _md(value, limit: int = 300) -> str:
    """
    Text written by a model (thesis themes, rejection reasons, evidence) made
    safe to place in a GitHub issue: one line, no mentions, links, HTML or
    markdown structure.
    """
    text = " ".join(str(value if value is not None else "").split())[:limit]
    for ch in "<>`[]#|":
        text = text.replace(ch, "")
    return text.replace("@", "(at)")


def _horizon(rows, horizon: int = 10) -> Dict:
    return next((r for r in rows or [] if r.get("horizon") == horizon), {})


def build_review(data: Dict, today: date) -> Tuple[str, str]:
    """Returns (issue title, markdown body)."""
    week_ago = (today - timedelta(days=7)).isoformat()
    problems: List[str] = []
    out: List[str] = []

    # ── Account ──────────────────────────────────────────────────────────────
    out.append("## Account")
    history = data.get("history") or []
    if history:
        last = history[-1]
        before = [h for h in history if h["date"] <= week_ago]
        line = f"- Portfolio value: **${last['portfolio_value']:,.0f}** (as of {last['date']})"
        if before:
            base = before[-1]["portfolio_value"]
            line += f", {last['portfolio_value'] / base - 1:+.1%} since {before[-1]['date']}"
        out.append(line)
        week = [h for h in history if h["date"] > week_ago]
        out.append(f"- Trade runs this week: {len(week)}, buy signals {sum(h.get('buy_signals') or 0 for h in week)}, "
                   f"orders submitted {sum(h.get('orders_submitted') or 0 for h in week)}")
        if not week:
            problems.append("No daily trade run was recorded this week.")
    else:
        out.append("- No run history found.")

    # ── Trades ───────────────────────────────────────────────────────────────
    out.append("\n## Trades this week")
    trades = data.get("trades") or []
    opened = [t for t in trades if (t.get("entry_date") or "") > week_ago and t.get("status") != "CANCELLED"]
    closed = [t for t in trades if t.get("status") == "CLOSED" and (t.get("exit_date") or "") > week_ago]
    unfilled = [t for t in trades if (t.get("entry_date") or "") > week_ago and t.get("status") == "CANCELLED"]
    out.append(f"- Opened: {', '.join(t['symbol'] for t in opened) or 'none'}")
    if closed:
        for t in closed:
            out.append(f"- Closed {t['symbol']}: {t.get('pnl_pct', 0):+.1f}% (${t.get('pnl_usd', 0):+,.0f}), "
                       f"{t.get('exit_reason') or 'no reason recorded'}")
        out.append(f"- Realized this week: ${sum(t.get('pnl_usd') or 0 for t in closed):+,.0f}")
    else:
        out.append("- Closed: none")
    if unfilled:
        out.append(f"- Orders that never filled: {', '.join(t['symbol'] for t in unfilled)}")

    # ── Learning agent ───────────────────────────────────────────────────────
    out.append("\n## Learning agent")
    weights, report = data.get("weights") or {}, data.get("learning_report") or {}
    active, champion = weights.get("active") or {}, weights.get("champion") or {}
    if active:
        out.append(f"- Ranking weights in use: **{active.get('w_rs', 0):.0%} RS / {active.get('w_thesis', 0):.0%} thesis** "
                   f"(version {active.get('version')}, {active.get('state')})")
        if active.get("state") == "provisional":
            done = sum(1 for t in trades if t.get("status") == "CLOSED" and t.get("weight_version") == active.get("version"))
            baseline = champion.get("mean_pnl")
            bar = f"{baseline:+.2f}% per trade" if baseline is not None else "not recorded yet"
            out.append(f"- Probation: {done} of {PROBATION} closed trades under these weights; "
                       f"baseline to beat ({champion.get('w_rs', 0):.0%}/{champion.get('w_thesis', 0):.0%}): {bar}")
    status = report.get("status")
    out.append(f"- This week's decision: `{status or 'no report'}` on {report.get('trades_so_far', 0)} closed trades")
    if status == "blocked_by_candidate_evidence":
        ev = report.get("candidate_evidence") or {}
        out.append(f"  - New weights {report.get('derived')} were **not applied**: on {ev.get('days')} days of candidate data "
                   f"they picked worse names ({(ev.get('mean_difference') or 0):+.2%} per pick).")
    if status in ("applied", "promoted", "reverted"):
        out.append(f"  - Weights changed this week ({status}).")
    if not report:
        problems.append("The learning agent produced no report.")

    # ── Candidate evidence ───────────────────────────────────────────────────
    out.append("\n## Candidate evidence")
    co = data.get("candidate_outcomes") or {}
    if co:
        avg, paired = _horizon(co.get("average_candidate")), _horizon(co.get("paired_10_90_vs_60_40"))
        out.append(f"- Dataset: {co.get('candidate_days')} scored candidates over {co.get('days')} trading days")
        if avg:
            out.append(f"- Average candidate vs SPY over 10 days: {avg.get('mean', 0):+.2%} (t = {avg.get('t', 0):+.2f})")
        if paired:
            out.append(f"- 10/90 vs 60/40 top picks over 10 days: {paired.get('mean', 0):+.2%} per pick "
                       f"(t = {paired.get('t', 0):+.2f}, {paired.get('n')} days). A t near 2 or beyond is needed to mean much.")
    else:
        out.append("- No candidate-outcome report found.")

    # ── Macro theses ─────────────────────────────────────────────────────────
    out.append("\n## Macro theses")
    brief = data.get("macro_brief") or {}
    if brief.get("status") in ("error", "partial"):
        problems.append(f"Macro research: {_md(brief.get('error') or brief.get('revalidation_error') or 'failed')}")
    live = brief.get("active") or []
    out.append(f"- Live: {len(live)}")
    for t in live:
        out.append(f"  - {_md(t.get('id'), 40)} ({float(t.get('conviction') or 0):.2f}): {_md(t.get('theme'))} "
                   f"({', '.join(_md(x, 40) for x in t.get('beneficiary_sectors') or [])})")
    reval = brief.get("revalidation") or {}
    if reval:
        out.append(f"- Re-validated: {len(reval.get('confirmed') or [])} of {reval.get('checked', 0)} confirmed")
        for inv in reval.get("invalidated") or []:
            out.append(f"  - Retired {_md(inv.get('id'), 40)}: {_md(inv.get('evidence'))}")
    out.append(f"- New this week: {brief.get('admitted', 0)} admitted")
    for r in brief.get("rejected") or []:
        out.append(f"  - Rejected \"{_md(r.get('theme'))}\": {_md(r.get('reason'))}")

    # ── Health ───────────────────────────────────────────────────────────────
    screener = data.get("screener") or {}
    coverage = screener.get("data_coverage")
    if coverage is not None and coverage < MIN_DATA_COVERAGE:
        problems.append(f"Screener: only {coverage:.0%} of the universe has price data — the ticker list is probably corrupt.")
    for run in data.get("failed_runs") or []:
        problems.append(f"Failed run: {run.get('name')} at {run.get('createdAt')}")

    health = ["\n## Health"]
    if screener:
        cov = f"{coverage:.0%}" if coverage is not None else "not recorded"
        health.append(f"- Screener: {screener.get('universe_size')} tickers, price data for {cov}, "
                      f"{screener.get('final_count')} candidates in the latest run")
    health.append(f"- Failed workflow runs this week: {len(data.get('failed_runs') or [])}")
    health.append("- No problems detected." if not problems else f"- {len(problems)} problem(s) listed at the top.")

    head = []
    if problems:
        head = ["## Needs attention"] + [f"- {p}" for p in problems] + [""]
    title = f"{'⚠️ ' if problems else ''}Weekly review {today.isoformat()}"
    return title, "\n".join(head + out + health) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the weekly review")
    parser.add_argument("--failed-runs", help="JSON file from `gh run list --status failure --json name,createdAt`")
    parser.add_argument("--body-file")
    parser.add_argument("--title-file")
    args = parser.parse_args()

    title, body = build_review(load_data(args.failed_runs), date.today())
    if args.body_file:
        Path(args.body_file).write_text(body)
    if args.title_file:
        Path(args.title_file).write_text(title)
    if not (args.body_file or args.title_file):
        print(title + "\n\n" + body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
