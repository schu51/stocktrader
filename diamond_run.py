# diamond_run.py
"""
Diamond screen: weekly run
==========================
Builds the watchlist of small and mid-sized companies whose finances are
turning from loss to profit. Nothing here trades.

Order of work, cheapest first:
  1. every company's figures from the SEC (diamond_data)      ~130 requests
  2. financial gates and score (diamond_screen)               no network
  3. price, size and liquidity for the survivors only         one download
  4. industry code for what is left                           one request each

Fails closed: if the SEC data or the prices look incomplete, nothing is
written and last week's list stays in place.

Usage:
    python diamond_run.py                # build this week's list
    python diamond_run.py --top 60
    python diamond_run.py --dry-run      # print the list, write nothing

Checking the screen against the past (writes nothing):
    python diamond_run.py --as-of 2023Q1 --no-size-cap --find PLTR
"""

import argparse
import calendar
import json
import logging
import os
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional

from diamond_data import company_metrics, load_financials
from diamond_screen import GATES, gate_failures, score
from diamond_universe import (excluded_sic, fetch_listings, fetch_sic, listed_companies,
                              size_and_volume, size_failures)

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.resolve()
OUT_FILE = ROOT / "docs" / "data" / "diamonds.json"
HISTORY_FILE = ROOT / "docs" / "data" / "diamond_history.json"

MIN_COMPANIES_WITH_REVENUE = 2500   # fewer than this for the reference quarter: the SEC data is incomplete
MAX_MISSING_PRICE_SHARE = 0.20
MIN_LISTED = 3000                   # NYSE and Nasdaq common stocks number about 6,000
MIN_WITH_METRICS = 1500             # about 2,000 to 2,500 have eight usable quarters
HISTORY_WEEKS = 104
TOP = 40


class CoverageError(Exception):
    """The inputs look incomplete; a list built from them could not be trusted."""


def build_watchlist(cutoff: date, fins: Dict[int, dict], coverage: dict, listings: Dict[int, dict],
                    prices: Callable[[List[str]], Dict[str, dict]], sic: Callable[[int], Optional[int]],
                    top: int = TOP, data_cutoff: Optional[date] = None) -> dict:
    """
    The diamonds.json document. Raises CoverageError rather than return a list
    from partial data. `data_cutoff` (historical runs) is the last quarter end
    a company may be judged on; prices are still taken at `cutoff`.
    """
    reporting = coverage.get("companies_with_revenue") or 0
    if reporting < MIN_COMPANIES_WITH_REVENUE:
        raise CoverageError(f"only {reporting} companies report revenue for {coverage.get('reference_quarter')} "
                            f"(need {MIN_COMPANIES_WITH_REVENUE}): the SEC data looks incomplete")
    if len(listings) < MIN_LISTED:
        raise CoverageError(f"only {len(listings)} companies listed (need {MIN_LISTED}): the SEC ticker file looks incomplete")

    gate_counts, with_metrics, passed = Counter(), 0, []
    for cik, listing in listings.items():
        try:
            m = company_metrics(fins.get(cik), data_cutoff or cutoff)
        except Exception as exc:                       # one company's odd data must not stop the list
            logger.warning(f"{listing['ticker']}: metrics failed ({exc})")
            m = None
        if m is None:
            continue
        with_metrics += 1
        failed = gate_failures(m)
        gate_counts.update(failed)
        if not failed:
            passed.append((cik, listing, m))

    if with_metrics < MIN_WITH_METRICS:
        # A whole figure missing from the SEC data (every operating-income frame
        # empty, say) shows up here, not in the revenue count above
        raise CoverageError(f"only {with_metrics} companies have eight quarters of figures "
                            f"(need {MIN_WITH_METRICS}): the SEC data looks incomplete")

    tickers = [listing["ticker"] for _, listing, _ in passed]
    quotes = prices(tickers) if tickers else {}
    missing = [t for t in tickers if t not in quotes]
    if tickers and len(missing) / len(tickers) > MAX_MISSING_PRICE_SHARE:
        raise CoverageError(f"no price data for {len(missing)} of {len(tickers)} companies: "
                            f"the price download looks incomplete")

    size_counts, sized = Counter(), []
    for cik, listing, m in passed:
        quote = quotes.get(listing["ticker"]) or {}
        s = size_and_volume(m.get("shares"), quote.get("closes"), quote.get("volumes"))
        failed = size_failures(s)
        size_counts.update(failed)
        if not failed:
            sized.append((cik, listing, m, s))

    rows, excluded = [], 0
    for cik, listing, m, s in sized:
        code = sic(cik)
        if excluded_sic(code):
            excluded += 1
            continue
        rows.append({"ticker": listing["ticker"], "name": listing["name"], "cik": cik, "sic": code,
                     "score": score(m), **s, **m})
    if not rows:
        # Every past quarter checked produced 15 to 30 names. None at all means
        # something upstream is wrong, and must not replace last week's list.
        raise CoverageError("the list came out empty: not written over last week's")
    rows.sort(key=lambda r: (-r["score"]["total"], r["ticker"]))
    rows = rows[:top]
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank

    return {
        "generated_at": datetime.now().isoformat(),
        "cutoff": cutoff.isoformat(),
        "data_through": (data_cutoff or cutoff).isoformat(),
        "reference_quarter": coverage.get("reference_quarter"),
        "coverage": {"companies_with_data": len(fins), "companies_with_revenue": reporting,
                     "listed": len(listings), "with_metrics": with_metrics,
                     "passed_financial_gates": len(passed), "priced": len(tickers) - len(missing),
                     "passed_size": len(sized), "excluded_industry": excluded, "watchlist": len(rows)},
        "gate_failures": {g: gate_counts.get(g, 0) for g in GATES},
        "size_failures": dict(size_counts),
        "watchlist": rows,
    }


def update_history(history: list, doc: dict) -> list:
    """One row per company per run date, so its trend can be read. A re-run on the same date replaces that date."""
    day = str(doc.get("generated_at") or "")[:10]
    kept = [h for h in (history or []) if isinstance(h, dict) and h.get("date") != day]
    kept += [{"date": day, "ticker": r["ticker"], "rank": r["rank"], "total": r["score"]["total"]}
             for r in doc.get("watchlist") or []]
    dates = sorted({h["date"] for h in kept})[-HISTORY_WEEKS:]
    return sorted((h for h in kept if h["date"] in dates), key=lambda h: (h["date"], h["rank"]))


def fetch_prices(tickers: List[str]) -> Dict[str, dict]:
    """Six months of daily closes and volumes for each ticker that has them."""
    import yfinance as yf
    if not tickers:
        return {}
    raw = yf.download(sorted(set(tickers)), period="6mo", auto_adjust=True, progress=False, threads=True,
                      group_by="column")
    out = {}
    for t in tickers:
        try:
            closes = raw["Close"][t].dropna() if len(tickers) > 1 else raw["Close"].dropna().squeeze()
            volumes = raw["Volume"][t].reindex(closes.index) if len(tickers) > 1 \
                else raw["Volume"].reindex(closes.index).squeeze()
            if len(closes):
                out[t] = {"closes": [float(x) for x in closes.values], "volumes": [float(x) for x in volumes.values]}
        except Exception:
            continue
    return out


FILING_LAG_DAYS = 60          # by then nearly every company has filed for the quarter
BENCHMARK = "IWM"             # Russell 2000 ETF


def as_of_cutoff(text: str) -> date:
    """'2023Q1' -> the date by which that quarter's filings are public."""
    try:
        year, quarter = int(text[:4]), int(text[5])
        if text[4].upper() != "Q" or not 1 <= quarter <= 4 or len(text) != 6:
            raise ValueError
    except (ValueError, IndexError):
        raise ValueError(f"--as-of wants a quarter such as 2023Q1, not {text!r}")
    month = quarter * 3
    return date(year, month, calendar.monthrange(year, month)[1]) + timedelta(days=FILING_LAG_DAYS)


def _download_closes(tickers: List[str], start: date, end: date):
    import yfinance as yf
    raw = yf.download(sorted(set(tickers)), start=start.isoformat(), end=(end + timedelta(days=1)).isoformat(),
                      auto_adjust=True, progress=False, threads=True)
    return raw["Close"]


def forward_returns(tickers: List[str], start: date, months: int = 12, download=None) -> Dict[str, float]:
    """Total return over the window for each ticker with a close at both ends."""
    import pandas as pd
    end = (pd.Timestamp(start) + pd.DateOffset(months=months)).date()
    closes = (download or _download_closes)(tickers, start, end)
    out = {}
    for t in tickers:
        if t not in getattr(closes, "columns", []):
            continue
        series = closes[t].loc[pd.Timestamp(start):pd.Timestamp(end)]
        if len(series) < 2 or series.iloc[[0, -1]].isna().any() or series.iloc[0] <= 0:
            continue
        out[t] = round(float(series.iloc[-1] / series.iloc[0] - 1), 4)
    return out


def unsplit(quote: dict, cutoff: date, splits) -> dict:
    """
    Prices and volumes as they were on `cutoff`. Yahoo's history is adjusted for
    every later stock split (NVDA on 2023-05-30 reads 40, not 401), while the
    share count in a filing is as reported then; multiplying the two would
    understate the market value tenfold. `splits` is [(date, ratio), ...];
    None (unknown) leaves the quote as it is.
    """
    if not splits:
        return quote
    factor = 1.0
    for day, ratio in splits:
        try:
            if date.fromisoformat(str(day)[:10]) > cutoff and float(ratio) > 0:
                factor *= float(ratio)
        except (TypeError, ValueError):
            continue
    if factor == 1.0:
        return quote
    return {"closes": [c * factor for c in quote["closes"]], "volumes": [v / factor for v in quote["volumes"]]}


def explain(ticker: str, cutoff: date, fins: Dict[int, dict], listings: Dict[int, dict]) -> str:
    """Why one company is, or is not, a candidate: its gate failures and score."""
    cik = next((c for c, v in listings.items() if v["ticker"] == ticker.upper()), None)
    if cik is None:
        return f"{ticker}: no listing on NYSE or Nasdaq as common stock"
    m = company_metrics(fins.get(cik), cutoff)
    if m is None:
        return f"{ticker}: eight consecutive quarters of revenue and operating income could not be established"
    failed = gate_failures(m)
    shown = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in m.items()}
    return (f"{ticker}: {'FAILS ' + ', '.join(failed) if failed else 'passes every gate'} | "
            f"score {score(m)} | {shown}")


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str))
    os.replace(tmp, path)


def print_list(doc: dict) -> None:
    c = doc["coverage"]
    print(f"\nDiamond watchlist as of {doc['cutoff']}: {c['listed']} listed, {c['with_metrics']} with eight "
          f"quarters, {c['passed_financial_gates']} turning, {c['passed_size']} the right size, {c['watchlist']} listed below")
    print(f"gate failures: {doc['gate_failures']}  size: {doc['size_failures']}")
    print(f"{'#':>3} {'ticker':7}{'score':>6}{'value $M':>10}{'rev gr':>8}{'margin':>8}{'was':>8}{'gross':>7}  name")
    for r in doc["watchlist"]:
        gross = f"{r['ttm_gross_margin'] * 100:5.0f}%" if r.get("ttm_gross_margin") is not None else "    --"
        print(f"{r['rank']:>3} {r['ticker']:7}{r['score']['total']:>6.1f}{r['market_value'] / 1e6:>10,.0f}"
              f"{r['revenue_growth'] * 100:>7.0f}%{r['ttm_op_margin'] * 100:>7.1f}%{r['prior_ttm_op_margin'] * 100:>7.1f}%"
              f"{gross:>7}  {r['name'][:34]}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Build the diamond watchlist (no trades)")
    parser.add_argument("--top", type=int, default=TOP)
    parser.add_argument("--dry-run", action="store_true", help="print the list, write nothing")
    parser.add_argument("--as-of", help="run on the data filed by a past quarter, e.g. 2023Q1; writes nothing")
    parser.add_argument("--no-size-cap", action="store_true", help="historical checks: lift the $10B ceiling")
    parser.add_argument("--find", action="append", default=[], help="explain one ticker (repeatable)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")

    cutoff = as_of_cutoff(args.as_of) if args.as_of else date.today()
    # A historical run prices the list 60 days after the quarter, when its
    # filings were public, but may only see quarters that had ended by then.
    data_cutoff = cutoff - timedelta(days=FILING_LAG_DAYS) if args.as_of else None
    if args.no_size_cap:
        import diamond_universe
        diamond_universe.MAX_MARKET_VALUE = float("inf")

    def prices(tickers):
        if not args.as_of:
            return fetch_prices(tickers)
        # The market value on that date needs the price as it was then (see unsplit)
        import yfinance as yf
        raw = yf.download(sorted(set(tickers)), start=(cutoff - timedelta(days=200)).isoformat(),
                          end=(cutoff + timedelta(days=1)).isoformat(), auto_adjust=False, progress=False, threads=True)
        out = {}
        for t in tickers:
            try:
                closes = raw["Close"][t].dropna()
                if len(closes):
                    quote = {"closes": [float(x) for x in closes.values],
                             "volumes": [float(x) for x in raw["Volume"][t].reindex(closes.index).values]}
                    try:
                        history = yf.Ticker(t).splits
                        splits = [(str(day)[:10], float(ratio)) for day, ratio in history.items()]
                    except Exception:
                        splits = None
                    out[t] = unsplit(quote, cutoff, splits)
            except Exception:
                continue
        return out
    try:
        fins, coverage = load_financials(cutoff)
        listings = listed_companies(fetch_listings())
        sic_cache: Dict[int, Optional[int]] = {}
        doc = build_watchlist(cutoff, fins, coverage, listings, prices,
                              lambda cik: sic_cache.setdefault(cik, fetch_sic(cik)), top=args.top,
                              data_cutoff=data_cutoff)
    except Exception as exc:
        # CoverageError, a frame that would not load, the ticker file missing:
        # in every case last week's list is better than a partial one.
        print(f"::error::diamond screen did not run: {exc}")
        logger.error(f"Diamond screen failed, last week's list left in place: {exc}")
        return 1

    print_list(doc)
    for ticker in args.find:
        print(explain(ticker, data_cutoff or cutoff, fins, listings))
    if args.as_of:
        picks = [r["ticker"] for r in doc["watchlist"]]
        returns = forward_returns(picks + [BENCHMARK], cutoff)
        got = sorted(returns[t] for t in picks if t in returns)
        if got:
            print(f"\n12 months after {cutoff}: {len(got)} of {len(picks)} priced | "
                  f"average {sum(got) / len(got):+.0%} | median {got[len(got) // 2]:+.0%} | "
                  f"best {got[-1]:+.0%} | worst {got[0]:+.0%} | {BENCHMARK} {returns.get(BENCHMARK, float('nan')):+.0%}")
        print("Limits: today's ticker list (companies since delisted or acquired are missing, which flatters "
              "the result); the SEC figures include later restatements; and a few smaller companies' "
              "annual reports arrive after the 60 days assumed here.")
        return 0
    if args.dry_run:
        return 0
    try:
        history = json.loads(HISTORY_FILE.read_text()) if HISTORY_FILE.exists() else []
    except Exception:
        history = []
    _write(OUT_FILE, doc)
    _write(HISTORY_FILE, update_history(history, doc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
