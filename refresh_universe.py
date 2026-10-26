"""
Refresh the screener's scan universe
====================================
Writes docs/data/universe.json: every S&P 500 and Nasdaq-100 member with its
sector, in the screener's sector keys.

Index membership changes a few times a quarter, so the screener reads this
saved file every morning instead of scraping a website at 9:15 (the Finviz
scrape it used to depend on is blocked from GitHub's servers, which silently
shrank the universe to the 82-name fallback list).

Sources, in order:
  S&P 500     Wikipedia constituents table, then the datasets/s-and-p-500-companies
              CSV on GitHub
  Nasdaq-100  Wikipedia components table; if it can't be read, the Nasdaq-only
              names from the previous file are carried over

The file is only rewritten when the new list passes the size checks, so a bad
fetch leaves last week's universe in place.

Usage:
    python refresh_universe.py             # fetch and write
    python refresh_universe.py --dry-run   # fetch and print counts only
"""

import argparse
import io
import json
import re
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

UNIVERSE_PATH = Path(__file__).parent / "docs" / "data" / "universe.json"

SP500_WIKI = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
SP500_CSV = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"
NDX_WIKI = "https://en.wikipedia.org/wiki/Nasdaq-100"

MIN_SP500 = 480
MIN_NDX = 95

# GICS sector name -> sector key used throughout the screener
GICS_SECTOR_NORM: Dict[str, str] = {
    "Information Technology": "technology",
    "Health Care":            "healthcare",
    "Financials":             "financials",
    "Consumer Discretionary": "consumer_cyclical",
    "Industrials":            "industrials",
    "Communication Services": "communication_services",
    "Consumer Staples":       "consumer_defensive",
    "Energy":                 "energy",
    "Materials":              "basic_materials",
    "Real Estate":            "real_estate",
    "Utilities":              "utilities",
}

# ICB industry name (the Nasdaq-100 Wikipedia table) -> same sector keys
ICB_SECTOR_NORM: Dict[str, str] = {
    "Technology":             "technology",
    "Telecommunications":     "communication_services",
    "Health Care":            "healthcare",
    "Financials":             "financials",
    "Real Estate":            "real_estate",
    "Consumer Discretionary": "consumer_cyclical",
    "Consumer Staples":       "consumer_defensive",
    "Industrials":            "industrials",
    "Basic Materials":        "basic_materials",
    "Energy":                 "energy",
    "Utilities":              "utilities",
}

_SECTOR_COLUMNS = (("GICS Sector", GICS_SECTOR_NORM), ("ICB Industry", ICB_SECTOR_NORM),
                   ("Industry", ICB_SECTOR_NORM), ("Sector", GICS_SECTOR_NORM))

# What went wrong in this run, written into the file (run logs are not always at hand)
NOTES: list = []


def _note(msg: str) -> None:
    logger.warning(msg)
    NOTES.append(msg[:300])


_HEADERS = {"User-Agent": "stocktrader-universe-refresh/1.0 (github.com/schu51/stocktrader)"}


def normalize_ticker(symbol: str) -> str:
    """Class shares as the screener writes them: BRK.B -> BRK-B."""
    return str(symbol).strip().upper().replace(".", "-")


def _members_from_table(df, ticker_cols=("Symbol", "Ticker")) -> Dict[str, str]:
    """
    {ticker: sector key} from an index components table. The S&P 500 table
    labels sectors with GICS, the Nasdaq-100 table with ICB; a table with no
    recognised sector column still yields its tickers, as "other".
    """
    # Headers carry footnote markers on Wikipedia ("ICB Industry[14]")
    cols = {re.sub(r"\[[^\]]*\]", "", str(c)).strip(): c for c in df.columns}
    tcol = next((cols[c] for c in ticker_cols if c in cols), None)
    if tcol is None:
        return {}
    scol, norm = next(((cols[c], m) for c, m in _SECTOR_COLUMNS if c in cols), (None, {}))
    sectors = df[scol] if scol is not None else [None] * len(df)
    out = {}
    for sym, sector in zip(df[tcol], sectors):
        if not isinstance(sym, str) or not sym.strip():
            continue
        name = str(sector).strip() if sector is not None else ""
        out[normalize_ticker(sym)] = norm.get(name) or GICS_SECTOR_NORM.get(name) or "other"
    return out


def _read_wiki_tables(url: str):
    import pandas as pd
    import requests
    resp = requests.get(url, headers=_HEADERS, timeout=20)
    resp.raise_for_status()
    return pd.read_html(io.StringIO(resp.text))


def _best_table(tables, minimum: int) -> Dict[str, str]:
    best: Dict[str, str] = {}
    for df in tables:
        members = _members_from_table(df)
        if len(members) > len(best):
            best = members
    return best if len(best) >= minimum else {}


def fetch_sp500() -> Dict[str, str]:
    try:
        members = _best_table(_read_wiki_tables(SP500_WIKI), MIN_SP500)
        if members:
            logger.info(f"S&P 500 from Wikipedia: {len(members)}")
            NOTES.append("S&P 500 from Wikipedia")
            return members
        _note("Wikipedia S&P 500 table too small or not found")
    except Exception as e:
        _note(f"Wikipedia S&P 500 failed: {e}")

    try:
        import pandas as pd
        import requests
        resp = requests.get(SP500_CSV, headers=_HEADERS, timeout=20)
        resp.raise_for_status()
        members = _members_from_table(pd.read_csv(io.StringIO(resp.text)))
        if len(members) >= MIN_SP500:
            logger.info(f"S&P 500 from GitHub datasets CSV: {len(members)}")
            NOTES.append("S&P 500 from GitHub datasets CSV")
            return members
        _note(f"S&P 500 CSV had only {len(members)} rows")
    except Exception as e:
        _note(f"S&P 500 CSV failed: {e}")
    return {}


def fetch_ndx() -> Dict[str, str]:
    try:
        tables = _read_wiki_tables(NDX_WIKI)
        members = _best_table(tables, MIN_NDX)
        if members:
            logger.info(f"Nasdaq-100 from Wikipedia: {len(members)}")
            return members
        _note("Wikipedia Nasdaq-100 table too small or not found "
                       f"(tables seen: {[list(map(str, t.columns))[:6] for t in tables if len(t) > 20][:6]})")
    except Exception as e:
        _note(f"Wikipedia Nasdaq-100 failed: {e}")
    return {}


def load_previous(path: Path = UNIVERSE_PATH) -> Optional[dict]:
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def build(sp500: Dict[str, str], ndx: Dict[str, str], previous: Optional[dict]) -> Optional[dict]:
    """Merge the two indexes. None if the S&P 500 list is missing or short."""
    if len(sp500) < MIN_SP500:
        return None

    ndx_source = "wikipedia"
    if len(ndx) < MIN_NDX:
        prev_ndx = (previous or {}).get("nasdaq100", [])
        prev_tickers = (previous or {}).get("tickers", {})
        ndx = {t: prev_tickers.get(t, "other") for t in prev_ndx}
        ndx_source = "carried over" if ndx else "missing"

    prev_tickers = (previous or {}).get("tickers", {})
    ndx = {t: (prev_tickers.get(t, "other") if s == "other" else s) for t, s in ndx.items()}
    tickers = {**ndx, **sp500}          # S&P sector wins on overlap
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sources": {"sp500": len(sp500), "nasdaq100": len(ndx), "nasdaq100_source": ndx_source},
        "count": len(tickers),
        "notes": list(NOTES),
        "nasdaq100": sorted(ndx),
        "tickers": dict(sorted(tickers.items())),
    }


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
    parser = argparse.ArgumentParser(description="Refresh the screener universe")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    previous = load_previous()
    result = build(fetch_sp500(), fetch_ndx(), previous)
    if result is None:
        logger.error("S&P 500 list could not be fetched — keeping the existing universe file")
        return 1

    logger.info(f"Universe: {result['count']} tickers ({result['sources']})")
    if args.dry_run:
        return 0

    UNIVERSE_PATH.parent.mkdir(parents=True, exist_ok=True)
    UNIVERSE_PATH.write_text(json.dumps(result, indent=1) + "\n")
    logger.info(f"Written to {UNIVERSE_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
