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
"""

import argparse
import json
import logging
import os
import sys
from collections import Counter
from datetime import date, datetime
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
HISTORY_WEEKS = 104
TOP = 40


class CoverageError(Exception):
    """The inputs look incomplete; a list built from them could not be trusted."""


def build_watchlist(cutoff: date, fins: Dict[int, dict], coverage: dict, listings: Dict[int, dict],
                    prices: Callable[[List[str]], Dict[str, dict]], sic: Callable[[int], Optional[int]],
                    top: int = TOP) -> dict:
    """The diamonds.json document. Raises CoverageError rather than return a list from partial data."""
    reporting = coverage.get("companies_with_revenue") or 0
    if reporting < MIN_COMPANIES_WITH_REVENUE:
        raise CoverageError(f"only {reporting} companies report revenue for {coverage.get('reference_quarter')} "
                            f"(need {MIN_COMPANIES_WITH_REVENUE}): the SEC data looks incomplete")

    gate_counts, with_metrics, passed = Counter(), 0, []
    for cik, listing in listings.items():
        try:
            m = company_metrics(fins.get(cik), cutoff)
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
    rows.sort(key=lambda r: (-r["score"]["total"], r["ticker"]))
    rows = rows[:top]
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank

    return {
        "generated_at": datetime.now().isoformat(),
        "cutoff": cutoff.isoformat(),
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
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")

    cutoff = date.today()
    try:
        fins, coverage = load_financials(cutoff)
        listings = listed_companies(fetch_listings())
        sic_cache: Dict[int, Optional[int]] = {}
        doc = build_watchlist(cutoff, fins, coverage, listings, fetch_prices,
                              lambda cik: sic_cache.setdefault(cik, fetch_sic(cik)), top=args.top)
    except Exception as exc:
        # CoverageError, a frame that would not load, the ticker file missing:
        # in every case last week's list is better than a partial one.
        print(f"::error::diamond screen did not run: {exc}")
        logger.error(f"Diamond screen failed, last week's list left in place: {exc}")
        return 1

    print_list(doc)
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
