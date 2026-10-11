"""
Diamond screen: is the judgment worth acting on yet?
====================================================
Stage 2 (diamond_judge.py) cannot be backtested: a model asked about a past
year already knows what happened next. The only honest test is forward. Each
week's list is recorded twice over in diamond_history.json, by the numbers
rank and by the conviction rank, and this module asks what the top names
under each ranking went on to do.

For every weekly list, at 4 weeks (20 sessions) and 13 weeks (63 sessions):
  conviction   average return of the top PICKS names by conviction
  numbers      the same for the top names by the numbers alone
  index        the small-cap index fund (IWM) over the same window

The owner asked (2026-10-11) to be told when there is enough to decide
whether to build the paper sleeve on these scores. The rule, set then:

  collecting      fewer than EARLY_WEEKS lists have a 4-week outcome
  early           a first read at 4 weeks; too soon to act on
  ready           READY_WEEKS lists have a 13-week outcome, and the conviction
                  picks beat BOTH the numbers picks and the index in at least
                  two thirds of them, and on average
  not_supported   READY_WEEKS lists are in and they did not

Weekly lists overlap heavily (mostly the same companies from one week to the
next), so twelve lists are far fewer than twelve independent tests. "Ready"
means the evidence no longer argues against building the sleeve; it does not
mean the judgment is proven.

The weekly review prints the verdict, and raises "ready" and "not_supported"
under Needs attention so they cannot be missed.

Usage:
    python diamond_track.py            # write docs/data/diamond_track.json
"""

import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.resolve()
HISTORY_FILE = ROOT / "docs" / "data" / "diamond_history.json"
OUT_FILE = ROOT / "docs" / "data" / "diamond_track.json"

BENCHMARK = "IWM"
PICKS = 8                     # the sleeve is planned as six to eight positions
HORIZONS = (20, 63)           # sessions: 4 weeks and 13 weeks
EARLY_WEEKS = 8               # lists with a 4-week outcome before a first read
READY_WEEKS = 12              # lists with a 13-week outcome before a decision
WIN_SHARE = 2 / 3             # conviction must beat both alternatives in this share of lists


def picks(history: List[Dict], day: str, key: str, n: int = PICKS) -> List[str]:
    """Tickers of the top `n` names on `day`'s list by `key` ('rank' or 'conviction_rank')."""
    rows = [h for h in history or [] if isinstance(h, dict) and h.get("date") == day
            and isinstance(h.get(key), (int, float)) and not isinstance(h.get(key), bool)]
    return [h["ticker"] for h in sorted(rows, key=lambda h: (h[key], h["ticker"]))[:n]]


def forward_return(dates: List[str], closes: List[float], start: str, sessions: int) -> Optional[float]:
    """Return from the first close on or after `start` to the close `sessions` later. None without the whole window."""
    if not dates or len(dates) != len(closes):
        return None
    first = next((i for i, d in enumerate(dates) if d >= start), None)
    if first is None or first + sessions >= len(closes) or not closes[first] or closes[first] <= 0:
        return None
    return closes[first + sessions] / closes[first] - 1


def _average(tickers: List[str], quotes: Dict[str, dict], day: str, sessions: int) -> Tuple[Optional[float], int]:
    returns = []
    for t in tickers:
        q = quotes.get(t) or {}
        r = forward_return(q.get("dates") or [], q.get("closes") or [], day, sessions)
        if r is not None:
            returns.append(r)
    return (mean(returns) if returns else None), len(returns)


def evaluate(history: List[Dict], quotes: Dict[str, dict], horizons=HORIZONS, n: int = PICKS) -> List[Dict]:
    """One row per (list date, horizon) for which the window has passed and both rankings have priced picks."""
    out = []
    for day in sorted({h.get("date") for h in history or [] if isinstance(h, dict) and isinstance(h.get("date"), str)}):
        by_conviction, by_numbers = picks(history, day, "conviction_rank", n), picks(history, day, "rank", n)
        if not by_conviction:
            continue                                   # a list from before the judgment existed
        index = quotes.get(BENCHMARK) or {}
        for sessions in horizons:
            bench = forward_return(index.get("dates") or [], index.get("closes") or [], day, sessions)
            conviction, n_conviction = _average(by_conviction, quotes, day, sessions)
            numbers, n_numbers = _average(by_numbers, quotes, day, sessions)
            # At least half of each basket priced, or the comparison is of something else
            if bench is None or n_conviction * 2 < len(by_conviction) or n_numbers * 2 < len(by_numbers):
                continue
            out.append({"date": day, "horizon": sessions, "conviction": round(conviction, 4),
                        "numbers": round(numbers, 4), "index": round(bench, 4),
                        "n_conviction": n_conviction, "n_numbers": n_numbers})
    return out


def verdict(rows: List[Dict]) -> Dict:
    """Where the evidence stands: collecting, early, ready or not_supported, with one line saying why."""
    short = [r for r in rows or [] if r.get("horizon") == HORIZONS[0]]
    long = [r for r in rows or [] if r.get("horizon") == HORIZONS[1]]

    def summary(rs):
        ahead = sum(1 for r in rs if r["conviction"] > r["numbers"] and r["conviction"] > r["index"])
        return (ahead, mean(r["conviction"] for r in rs), mean(r["numbers"] for r in rs), mean(r["index"] for r in rs))

    if len(long) >= READY_WEEKS:
        ahead, c, n, i = summary(long)
        text = (f"{len(long)} weekly lists with a 13-week outcome: the top {PICKS} by conviction averaged {c:+.1%}, "
                f"the top {PICKS} by the numbers {n:+.1%}, the small-cap index {i:+.1%}; conviction beat both in "
                f"{ahead} of {len(long)}.")
        ready = ahead >= WIN_SHARE * len(long) and c > max(n, i)
        return {"status": "ready" if ready else "not_supported", "weeks": len(long), "ahead": ahead,
                "text": text + (" Enough to decide on the paper sleeve." if ready
                                else " The judgment has not shown it adds anything: do not build the sleeve on it.")}
    if len(short) >= EARLY_WEEKS:
        ahead, c, n, i = summary(short)
        return {"status": "early", "weeks": len(short), "ahead": ahead,
                "text": (f"Early read, {len(short)} weekly lists with a 4-week outcome: conviction {c:+.1%}, numbers {n:+.1%}, "
                         f"index {i:+.1%}; conviction beat both in {ahead} of {len(short)}. "
                         f"{len(long)} of {READY_WEEKS} lists have the 13-week outcome a decision needs.")}
    return {"status": "collecting", "weeks": len(short), "ahead": None,
            "text": (f"Collecting: {len(short)} of {EARLY_WEEKS} weekly lists have a 4-week outcome (first read), "
                     f"{len(long)} of {READY_WEEKS} have the 13-week outcome a decision needs.")}


def fetch_quotes(tickers: List[str]) -> Dict[str, dict]:
    """Two years of dated daily closes for each ticker that has them."""
    import yfinance as yf
    raw = yf.download(sorted(set(tickers)), period="2y", auto_adjust=True, progress=False, threads=True)["Close"]
    out = {}
    for t in tickers:
        try:
            closes = raw[t].dropna()
            if len(closes):
                out[t] = {"dates": [d.date().isoformat() for d in closes.index], "closes": [float(x) for x in closes.values]}
        except Exception:
            continue
    return out


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
    try:
        history = json.loads(HISTORY_FILE.read_text())
        tickers = sorted({h["ticker"] for h in history if isinstance(h, dict) and isinstance(h.get("ticker"), str)})
        rows = evaluate(history, fetch_quotes(tickers + [BENCHMARK])) if tickers else []
    except Exception as exc:
        print(f"::error::diamond track record not updated: {exc}")
        return 1
    result = {"generated_at": datetime.now().isoformat(), "picks": PICKS, "rows": rows, "verdict": verdict(rows)}
    tmp = OUT_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(result, indent=2))
    os.replace(tmp, OUT_FILE)
    print(result["verdict"]["text"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
