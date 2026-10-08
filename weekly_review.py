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

sys.path.insert(0, str(ROOT))
from macro_thesis import clean_text

PROBATION = 10
FAIL_DAYS = 30
MIN_DATA_COVERAGE = 0.80
MIN_UNIVERSE = 400


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
        "screener": _load("screener.json"), "experiments": _load("experiments.json"), "failed_runs": failed,
        "diamonds": _load("diamonds.json"), "diamond_history": _load("diamond_history.json"),
    }


def _md(value, limit: int = 300) -> str:
    """
    Any value read from a data file, made safe to place in a GitHub issue.
    Allowlist, not denylist: only plain letters, digits and basic punctuation
    survive (clean_text), so mentions, HTML, markdown structure and invisible
    characters cannot. Bare URLs are then defused.
    Everything interpolated into the review goes through this or _num.
    """
    text = clean_text(value, limit, extra="_")
    # "&" could spell an HTML entity (&commat; for a mention); "=" and leading
    # "-" / "+" are block markers. The value is always placed mid-line in a list item.
    text = text.replace("&", "and").replace("=", " ").lstrip("-+ ")
    return text.replace("://", " ").replace("www.", "www ")


def _num(value, default: float = 0.0) -> float:
    """A number from a data file, or `default` if it is missing or not numeric."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _horizon(rows, horizon: int = 10) -> Dict:
    return next((r for r in rows or [] if isinstance(r, dict) and r.get("horizon") == horizon), {})


DIAMOND_TOP = 15


def diamond_section(doc: Optional[Dict], history: Optional[list], today: date) -> List[str]:
    """The diamond watchlist (diamond_run.py): top names, and how each moved since the week before."""
    out = ["## Diamond watchlist"]
    rows = (doc or {}).get("watchlist") if isinstance(doc, dict) else None
    if not rows:
        return out + ["- There is no list yet."]
    day = str(doc.get("generated_at") or "")[:10]
    try:
        age = (today - date.fromisoformat(day)).days
    except ValueError:
        age = None
    if age is None or age > 8:
        out.append(f"- **The list is {age if age is not None else 'an unknown number of'} days old**: "
                   f"this week's screen did not produce one.")
    cov = doc.get("coverage") if isinstance(doc.get("coverage"), dict) else {}
    out.append(f"- {int(_num(cov.get('with_metrics'))):,} companies with eight quarters of figures; "
               f"{int(_num(cov.get('passed_financial_gates'))):,} turning from loss to profit; no trades are placed from this list.")
    earlier = sorted({h.get("date") for h in history or [] if isinstance(h, dict) and str(h.get("date")) < day})
    before = {h["ticker"]: h["rank"] for h in history or []
              if isinstance(h, dict) and earlier and h.get("date") == earlier[-1]}
    out += ["", "| # | Ticker | Score | Since last week | Revenue | Margin change | Value | Company |",
            "|---|---|---|---|---|---|---|---|"]
    for r in rows[:DIAMOND_TOP]:
        if not isinstance(r, dict):
            continue
        ticker, rank = _md(r.get("ticker"), 8), int(_num(r.get("rank")))
        was = before.get(r.get("ticker"))
        move = "new" if was is None else "same" if was == rank else f"up {was - rank}" if was > rank else f"down {rank - was}"
        value = _num(r.get("market_value"))
        size = f"${value / 1e9:.1f}B" if value >= 1e9 else f"${value / 1e6:.0f}M"
        out.append(f"| {rank} | {ticker} | {_num((r.get('score') or {}).get('total')):.1f} | {move} | "
                   f"{_num(r.get('revenue_growth')):+.0%} | {_num(r.get('margin_change')) * 100:+.0f} pts | "
                   f"{size} | {_md(r.get('name'), 40)} |")
    return out


def build_review(data: Dict, today: date) -> Tuple[str, str]:
    """Returns (issue title, markdown body)."""
    week_ago = (today - timedelta(days=7)).isoformat()
    problems: List[str] = []
    out: List[str] = []

    # ── Account ──────────────────────────────────────────────────────────────
    out.append("## Account")
    history = [h for h in (data.get("history") or []) if isinstance(h, dict)]
    if history:
        last = history[-1]
        value = _num(last.get("portfolio_value"))
        before = [h for h in history if str(h.get("date") or "") <= week_ago]
        line = f"- Portfolio value: **${value:,.0f}** (as of {_md(last.get('date'), 20)})"
        base = _num(before[-1].get("portfolio_value")) if before else 0.0
        if base > 0:
            line += f", {value / base - 1:+.1%} since {_md(before[-1].get('date'), 20)}"
        out.append(line)
        week = [h for h in history if str(h.get("date") or "") > week_ago]
        out.append(f"- Trade runs this week: {len(week)}, buy signals {sum(int(_num(h.get('buy_signals'))) for h in week)}, "
                   f"orders submitted {sum(int(_num(h.get('orders_submitted'))) for h in week)}")
        if not week:
            problems.append("No daily trade run was recorded this week.")
    else:
        out.append("- No run history found.")

    # ── Trades ───────────────────────────────────────────────────────────────
    out.append("\n## Trades this week")
    trades = [t for t in (data.get("trades") or []) if isinstance(t, dict)]
    sym = lambda t: _md(t.get("symbol"), 12)
    opened = [t for t in trades if str(t.get("entry_date") or "") > week_ago and t.get("status") != "CANCELLED"]
    closed = [t for t in trades if t.get("status") == "CLOSED" and str(t.get("exit_date") or "") > week_ago]
    unfilled = [t for t in trades if str(t.get("entry_date") or "") > week_ago and t.get("status") == "CANCELLED"]
    out.append(f"- Opened: {', '.join(sym(t) for t in opened) or 'none'}")
    if closed:
        for t in closed:
            out.append(f"- Closed {sym(t)}: {_num(t.get('pnl_pct')):+.1f}% (${_num(t.get('pnl_usd')):+,.0f}), "
                       f"{_md(t.get('exit_reason'), 40) or 'no reason recorded'}")
        out.append(f"- Realized this week: ${sum(_num(t.get('pnl_usd')) for t in closed):+,.0f}")
    else:
        out.append("- Closed: none")
    if unfilled:
        out.append(f"- Orders that never filled: {', '.join(sym(t) for t in unfilled)}")
    # First-month failure rate: closed within 30 days at a loss. Where the strategy loses its money.
    all_closed = [t for t in trades if t.get("status") == "CLOSED" and t.get("hold_days") is not None
                  and t.get("pnl_usd") is not None]
    if all_closed:
        failed = lambda ts: sum(1 for t in ts if _num(t.get("hold_days")) <= FAIL_DAYS and _num(t.get("pnl_usd")) <= 0)
        cutoff = (today - timedelta(days=90)).isoformat()
        recent = [t for t in all_closed if str(t.get("exit_date") or "") > cutoff]
        line = (f"- First-month failure rate (closed within {FAIL_DAYS} days at a loss): "
                f"**{failed(all_closed) / len(all_closed):.0%}** of {len(all_closed)} closed trades")
        if recent:
            line += f"; last 90 days {failed(recent) / len(recent):.0%} of {len(recent)}"
        out.append(line + ". Three-year backtest: 59%.")

    # ── Learning agent ───────────────────────────────────────────────────────
    out.append("\n## Learning agent")
    weights, report = data.get("weights") or {}, data.get("learning_report") or {}
    active, champion = weights.get("active") or {}, weights.get("champion") or {}
    if active:
        out.append(f"- Ranking weights in use: **{_num(active.get('w_rs')):.0%} RS / {_num(active.get('w_thesis')):.0%} thesis** "
                   f"(version {_md(active.get('version'), 10)}, {_md(active.get('state'), 20)})")
        if active.get("state") == "provisional":
            done = sum(1 for t in trades if t.get("status") == "CLOSED" and t.get("weight_version") == active.get("version"))
            baseline = champion.get("mean_pnl")
            bar = f"{_num(baseline):+.2f}% per trade" if baseline is not None else "not recorded yet"
            out.append(f"- Probation: {done} of {PROBATION} closed trades under these weights; "
                       f"baseline to beat ({_num(champion.get('w_rs')):.0%}/{_num(champion.get('w_thesis')):.0%}): {bar}")
    status = report.get("status")
    out.append(f"- This week's decision: {_md(status, 40) or 'no report'} on {int(_num(report.get('trades_so_far')))} closed trades")
    if status == "blocked_by_candidate_evidence":
        ev = report.get("candidate_evidence") or {}
        derived = report.get("derived") or {}
        out.append(f"  - New weights ({_num(derived.get('w_rs')):.0%} RS / {_num(derived.get('w_thesis')):.0%} thesis) were "
                   f"**not applied**: on {int(_num(ev.get('days')))} days of candidate data they picked worse names "
                   f"({_num(ev.get('mean_difference')):+.2%} per pick).")
    if status in ("applied", "promoted", "reverted"):
        out.append(f"  - Weights changed this week ({status}).")
    if not report:
        problems.append("The learning agent produced no report.")

    # ── Candidate evidence ───────────────────────────────────────────────────
    out.append("\n## Candidate evidence")
    co = data.get("candidate_outcomes") or {}
    if co:
        avg, paired = _horizon(co.get("average_candidate")), _horizon(co.get("paired_10_90_vs_60_40"))
        out.append(f"- Dataset: {int(_num(co.get('candidate_days')))} scored candidates over {int(_num(co.get('days')))} trading days")
        if avg:
            out.append(f"- Average candidate vs SPY over 10 days: {_num(avg.get('mean')):+.2%} (t = {_num(avg.get('t')):+.2f})")
        if paired:
            out.append(f"- 10/90 vs 60/40 top picks over 10 days: {_num(paired.get('mean')):+.2%} per pick "
                       f"(t = {_num(paired.get('t')):+.2f}, {int(_num(paired.get('n')))} days). A t near 2 or beyond is needed to mean much.")
    else:
        out.append("- No candidate-outcome report found.")

    # ── Experiments ──────────────────────────────────────────────────────────
    out.append("\n## Experiments")
    registry = data.get("experiments") or {}
    exps = registry.get("experiments") if isinstance(registry.get("experiments"), dict) else {}
    if exps:
        for name, exp in exps.items():
            if not isinstance(exp, dict):
                continue
            ev = exp.get("evidence") if isinstance(exp.get("evidence"), dict) else {}
            kind = exp.get("kind")
            if kind in ("stop_arm", "news_arm"):
                a_key, b_key, a_label, b_label = (("n_atr", "n_fixed", "ATR", "fixed") if kind == "stop_arm"
                                                  else ("n_news", "n_control", "with news", "without"))
                detail = (f"{int(_num(ev.get(a_key)))} {a_label} vs {int(_num(ev.get(b_key)))} {b_label} closed trades")
                if ev.get("difference") is not None:
                    detail += f", difference {_num(ev.get('difference')):+.2f}% per trade (t = {_num(ev.get('t')):+.2f})"
            else:
                detail = f"{int(_num(ev.get('days')))} days of evidence"
                if ev.get("mean") is not None:
                    what = "flagged vs rest" if kind == "bearish_signal" else "fired vs not"
                    detail += f", {what} {_num(ev.get('mean')):+.2%} over 10 days (t = {_num(ev.get('t')):+.2f})"
            out.append(f"- {_md(name, 30)}: **{_md(exp.get('state'), 20)}** — {detail}")
        changed = [c for c in registry.get("changes") or [] if isinstance(c, dict) and str(c.get("date") or "") > week_ago]
        for c in changed:
            out.append(f"- Changed this week: {_md(c.get('experiment'), 30)} {_md(c.get('from'), 20)} to "
                       f"{_md(c.get('to'), 20)} ({_md(c.get('reason'))})")
        review = registry.get("review") if isinstance(registry.get("review"), dict) else {}
        if review.get("due"):
            days = (date.fromisoformat(str(review["due"])[:10]) - today).days
            when = f"in {days} days" if days > 0 else "TODAY" if days == 0 else f"{-days} days OVERDUE"
            out.append(f"- **Decision review due {_md(review.get('due'), 12)} ({when})**: {_md(review.get('what'))}. "
                       f"Revert if: {_md(review.get('revert_if'))}")
        out.append(f"- Buy signals logged so far: {int(_num(registry.get('signals_logged')))}")
        sent = registry.get("sentiment") if isinstance(registry.get("sentiment"), dict) else {}
        if sent.get("rows"):
            out.append(f"- News and social sentiment: {int(_num(sent.get('rows')))} stock-days scanned over "
                       f"{int(_num(sent.get('days')))} days"
                       + ("" if sent.get("enough_history") else " (too little history to judge yet)"))
            for key, label in (("news_score", "news score"), ("st_bull_ratio", "StockTwits bullish share")):
                ev = sent.get(key) if isinstance(sent.get(key), dict) else {}
                if ev.get("ic") is not None:
                    out.append(f"  - {label} vs next 10 days: rank correlation {_num(ev.get('ic')):+.3f} "
                               f"(t = {_num(ev.get('t')):+.2f}, {int(_num(ev.get('days')))} days)")
            for flag, label in (("news_negative_vs_rest", "negative news"), ("social_bearish_vs_rest", "bearish social")):
                neg = sent.get(flag) if isinstance(sent.get(flag), dict) else {}
                if neg.get("mean") is not None:
                    out.append(f"  - stocks with {label} vs the rest: {_num(neg.get('mean')):+.2%} over 10 days "
                               f"(t = {_num(neg.get('t')):+.2f}, {int(_num(neg.get('days')))} days) — the case for puts")
            weights = sent.get("weights") if isinstance(sent.get("weights"), dict) else {}
            if weights:
                out.append(f"  - source weights ({_md(weights.get('basis'), 40)}): news {_num(weights.get('news_score')):.0%}, "
                           f"StockTwits {_num(weights.get('st_bull_ratio')):.0%}")
        ab = registry.get("news_ab") if isinstance(registry.get("news_ab"), dict) else {}
        if ab.get("signals"):
            line = (f"- A/B on every buy signal ({int(_num(ab.get('signals')))} so far, "
                    f"{_num(ab.get('vetoed_share')):.0%} vetoed by news): technical only "
                    f"{_num(ab.get('technical_only')):+.2%} over 10 days vs SPY")
            if ab.get("technical_plus_news") is not None:
                line += f", technical + news {_num(ab.get('technical_plus_news')):+.2%}"
            if ab.get("gain_from_news") is not None:
                line += f" (gain from news {_num(ab.get('gain_from_news')):+.2%} per day with a veto, t = {_num(ab.get('t')):+.2f})"
            out.append(line)
    else:
        out.append("- No experiment registry found.")

    # ── Diamond watchlist ────────────────────────────────────────────────────
    out.append("")
    out += diamond_section(data.get("diamonds"), data.get("diamond_history"), today)

    # ── Macro theses ─────────────────────────────────────────────────────────
    out.append("\n## Macro theses")
    brief = data.get("macro_brief") or {}
    if brief.get("status") in ("error", "partial"):
        problems.append(f"Macro research: {_md(brief.get('error') or brief.get('revalidation_error') or 'failed')}")
    live = brief.get("active") or []
    live = [t for t in live if isinstance(t, dict)]
    out.append(f"- Live: {len(live)}")
    for t in live:
        out.append(f"  - {_md(t.get('id'), 40)} ({float(t.get('conviction') or 0):.2f}): {_md(t.get('theme'))} "
                   f"({', '.join(_md(x, 40) for x in t.get('beneficiary_sectors') or [])})")
    reval = brief.get("revalidation") or {}
    if reval:
        out.append(f"- Re-validated: {len(reval.get('confirmed') or [])} of {int(_num(reval.get('checked')))} confirmed")
        for inv in reval.get("invalidated") or []:
            out.append(f"  - Retired {_md(inv.get('id'), 40)}: {_md(inv.get('evidence'))}")
    out.append(f"- New this week: {int(_num(brief.get('admitted')))} admitted")
    for r in brief.get("rejected") or []:
        out.append(f"  - Rejected \"{_md(r.get('theme'))}\": {_md(r.get('reason'))}")

    # ── Health ───────────────────────────────────────────────────────────────
    screener = data.get("screener") or {}
    coverage = screener.get("data_coverage")
    coverage = _num(coverage) if coverage is not None else None
    if coverage is not None and coverage < MIN_DATA_COVERAGE:
        problems.append(f"Screener: only {coverage:.0%} of the universe has price data — the ticker list is probably corrupt.")
    universe_size = int(_num(screener.get("universe_size"))) if screener else None
    if universe_size is not None and universe_size < MIN_UNIVERSE:
        problems.append(f"Screener: universe is only {universe_size} tickers (expected about 520) — "
                        f"the Finviz scrape failed and the fallback list is in use.")
    for run in data.get("failed_runs") or []:
        problems.append(f"Failed run: {_md(run.get('name'), 60)} at {_md(run.get('createdAt'), 30)}")

    health = ["\n## Health"]
    if screener:
        cov = f"{coverage:.0%}" if coverage is not None else "not recorded"
        health.append(f"- Screener: {int(_num(screener.get('universe_size')))} tickers, price data for {cov}, "
                      f"{int(_num(screener.get('final_count')))} candidates in the latest run")
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
