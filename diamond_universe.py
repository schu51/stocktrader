# diamond_universe.py
"""
Diamond screen: which companies are eligible
============================================
Listing, size, liquidity and industry filters for the diamond screen.

These run AFTER the financial gates (diamond_screen.py), on the few hundred
companies that pass them, so prices and industry codes are fetched for
hundreds of companies rather than thousands.

  Listed    NYSE or Nasdaq common shares; one ticker per company
  Size      $300M to $10B: small enough that the market can still be wrong,
            large enough to trade and to have several years of filings
  Liquid    median daily dollar volume of $5M over 60 sessions, price >= $5
  Industry  not SIC 6000-6799 (banks, insurers, property trusts, investment
            vehicles): "losses turning to profit" means something else there
"""

import re
import time
from statistics import median
from typing import Dict, List, Optional

MIN_MARKET_VALUE = 300e6
MAX_MARKET_VALUE = 10e9
MIN_DOLLAR_VOLUME = 5e6
MIN_PRICE = 5.0
VOLUME_SESSIONS = 60
EXCHANGES = ("NYSE", "Nasdaq")
EXCLUDED_SIC = (6000, 6799)

LISTINGS_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"

# Not common stock: a dash suffix for warrants, units, rights or a preferred
# series; or a five-letter Nasdaq ticker whose fifth letter marks the same,
# confirmed by the security's name so that UBER is not taken for a right.
_DASH_SUFFIX = re.compile(r"-(W|WT|WS|U|UN|R|RT|P[A-Z]?)$")
_FIFTH_LETTER = {"W": ("warrant",), "U": ("unit",), "R": ("right",)}


def is_common_share(ticker: str, name: str) -> bool:
    ticker, name = (ticker or "").upper(), (name or "").lower()
    if not ticker or _DASH_SUFFIX.search(ticker) or "preferred" in name:
        return False
    if len(ticker) == 5 and ticker[-1] in _FIFTH_LETTER:
        return not any(word in name for word in _FIFTH_LETTER[ticker[-1]])
    return True


def listed_companies(rows: List[list]) -> Dict[int, Dict]:
    """{cik: listing} from the SEC's ticker file. The first ticker listed for a company is kept."""
    out: Dict[int, Dict] = {}
    for row in rows or []:
        try:
            cik, name, ticker, exchange = row[0], row[1], row[2], row[3]
        except (IndexError, TypeError):
            continue
        if not isinstance(cik, int) or exchange not in EXCHANGES or cik in out:
            continue
        if is_common_share(ticker, name):
            out[cik] = {"ticker": ticker.upper(), "name": name, "exchange": exchange}
    return out


def size_and_volume(shares: Optional[float], closes: List[float], volumes: List[float]) -> Optional[Dict]:
    """Price, market value and median dollar volume, or None if any of them cannot be worked out."""
    try:
        pairs = [(float(c), float(v)) for c, v in zip(closes or [], volumes or [])
                 if c == c and v == v and c > 0][-VOLUME_SESSIONS:]
        if len(pairs) < VOLUME_SESSIONS or not shares or shares != shares or shares <= 0:
            return None
        price = pairs[-1][0]
        return {"price": price, "market_value": float(shares) * price,
                "dollar_volume": median(c * v for c, v in pairs)}
    except (TypeError, ValueError):
        return None


def size_failures(s: Optional[Dict]) -> List[str]:
    if not s:
        return ["price_unknown"]
    failed = []
    if s["market_value"] < MIN_MARKET_VALUE:
        failed.append("too_small")
    if s["market_value"] > MAX_MARKET_VALUE:
        failed.append("too_big")
    if s["dollar_volume"] < MIN_DOLLAR_VOLUME:
        failed.append("illiquid")
    if s["price"] < MIN_PRICE:
        failed.append("penny")
    return failed


def excluded_sic(sic) -> bool:
    """True for the financial industries. An unknown code is not excluded."""
    try:
        return EXCLUDED_SIC[0] <= int(sic) <= EXCLUDED_SIC[1]
    except (TypeError, ValueError):
        return False


def _get(url: str, session=None):
    import requests
    from config import SEC_EDGAR_USER_AGENT
    resp = (session or requests).get(url, headers={"User-Agent": SEC_EDGAR_USER_AGENT}, timeout=40)
    time.sleep(0.2)
    resp.raise_for_status()
    return resp.json()


def fetch_listings(session=None) -> List[list]:
    return _get(LISTINGS_URL, session).get("data") or []


def fetch_sic(cik: int, session=None) -> Optional[int]:
    """The company's industry code from its SEC record. None if it cannot be read."""
    try:
        return int(_get(SUBMISSIONS_URL.format(cik=cik), session).get("sic"))
    except Exception:
        return None
