"""
Refresh the screener's scan universe
====================================
Writes docs/data/universe.json: the whole liquid US stock market (about
1,000 names trading $50M+ a day on any exchange, market_universe.py), with S&P 500 and Nasdaq-100 members keeping
their GICS sector and the rest taking one from their SEC industry code.
--index-only writes just the two indexes, as before.

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
import identity
import contracts
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
NDX_WIKI_LIST = "https://en.wikipedia.org/wiki/List_of_Nasdaq-100_companies"
NDX_NASDAQ_API = "https://api.nasdaq.com/api/quote/list-type/nasdaq100"

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
    import identity
    return identity.canonical(symbol)


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


def _ndx_from_nasdaq_api() -> Dict[str, str]:
    """Nasdaq's own list (no sectors: names the S&P list lacks come out as "other")."""
    import requests
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                             "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
               "Accept": "application/json, text/plain, */*", "Origin": "https://www.nasdaq.com",
               "Referer": "https://www.nasdaq.com/"}
    resp = requests.get(NDX_NASDAQ_API, headers=headers, timeout=20)
    resp.raise_for_status()
    rows = (((resp.json() or {}).get("data") or {}).get("data") or {}).get("rows") or []
    return {normalize_ticker(r["symbol"]): "other" for r in rows if isinstance(r.get("symbol"), str)}


def fetch_ndx() -> Dict[str, str]:
    for label, fetch in (("Wikipedia Nasdaq-100", lambda: _best_table(_read_wiki_tables(NDX_WIKI), MIN_NDX)),
                         ("Wikipedia Nasdaq-100 list", lambda: _best_table(_read_wiki_tables(NDX_WIKI_LIST), MIN_NDX)),
                         ("Nasdaq API", _ndx_from_nasdaq_api)):
        try:
            members = fetch()
            if len(members) >= MIN_NDX:
                logger.info(f"Nasdaq-100 from {label}: {len(members)}")
                NOTES.append(f"Nasdaq-100 from {label}")
                return members
            _note(f"{label}: only {len(members)} members found")
        except Exception as e:
            _note(f"{label} failed: {e}")
    return {}


def load_previous(path: Path = UNIVERSE_PATH) -> Optional[dict]:
    try:
        return contracts.read(path)
    except Exception:
        return None


def build(sp500: Dict[str, str], ndx: Dict[str, str], previous: Optional[dict]) -> Optional[dict]:
    """Merge the two indexes. None if the S&P 500 list is missing or short."""
    if len(sp500) < MIN_SP500:
        return None

    ndx_source = "fetched"
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


def add_market(result: dict, previous: Optional[dict], assets_fn=None, bars_fn=None, sec_fn=None,
               sic_fetch=None) -> dict:
    """
    Widen `result` (the index lists) to the whole liquid US market (market_universe.py).
    The index members keep their GICS sectors; the rest get one from their SEC SIC code.
    Raises if the market list comes out implausibly short, so the caller can keep the index-only file.
    """
    import requests
    import market_universe as mu
    import screener
    from config import SEC_EDGAR_USER_AGENT
    session = requests.Session()
    assets_fn = assets_fn or (lambda: identity.fetch_alpaca_assets(session))
    bars_fn = bars_fn or (lambda tickers: screener._alpaca_daily_bars(tickers, session=session))
    sec_fn = sec_fn or (lambda: identity.fetch_sec_tickers(session))
    sic_fetch = sic_fetch or (lambda cik: mu.fetch_sic(cik, session, SEC_EDGAR_USER_AGENT))
    gics = {t: s for t, s in result["tickers"].items() if s != "other"}
    tickers, sics, counts = mu.build_market(assets_fn(), bars_fn, sec_fn(), gics,
                                            (previous or {}).get("sic") or {}, sic_fetch)
    for t in result["tickers"]:          # index members without a GICS sector and no SIC match
        tickers.setdefault(t, "other")
    tickers = dict(sorted(tickers.items()))
    if counts["market"] < mu.MIN_MARKET:
        raise RuntimeError(f"market universe has only {counts['market']} names ({counts})")
    out = dict(result)
    out.update({"mode": "market", "count": len(tickers), "tickers": tickers, "sic": sics,
                "index_members": sorted(result["tickers"]),
                "sources": {**result["sources"], **counts}})
    out["notes"] = list(result.get("notes") or []) + [
        f"Whole market: {counts['market']} liquid common stocks ($3+, median "
        f"${mu.MIN_DOLLAR_VOLUME / 1e6:.0f}M+/day, 200+ closes)"]
    return out


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
    parser = argparse.ArgumentParser(description="Refresh the screener universe")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--index-only", action="store_true", help="S&P 500 + Nasdaq-100 only (the old universe)")
    args = parser.parse_args(argv)

    previous = load_previous()
    result = build(fetch_sp500(), fetch_ndx(), previous)
    if result is None:
        logger.error("S&P 500 list could not be fetched — keeping the existing universe file")
        return 1
    if not args.index_only:
        try:
            result = add_market(result, previous)
        except Exception as e:
            logger.error(f"Whole-market universe failed ({e})")
            if (previous or {}).get("mode") == "market":
                logger.error("Keeping last week's whole-market file")
                return 1
            logger.error("Writing the index-only universe instead")

    logger.info(f"Universe: {result['count']} tickers ({result['sources']})")
    if args.dry_run:
        return 0

    UNIVERSE_PATH.parent.mkdir(parents=True, exist_ok=True)
    UNIVERSE_PATH.write_text(json.dumps(result, indent=1) + "\n")
    logger.info(f"Written to {UNIVERSE_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
