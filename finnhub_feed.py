"""
Finnhub feed (free tier) for the big-mover job and the catalyst watch list.

Two things the bot's other sources don't give it:
  1. Earnings calendar: the scheduled report date for every US company in a
     window, in one request. big_movers.py otherwise estimates the next report
     from last year's 8-K dates; a scheduled date beats a guess.
  2. Company news: a second headline source beside Google News, used to explain
     why a stock closed 20%+ up.

Free tier: 60 requests/minute, key from https://finnhub.io/register, stored as
the GitHub secret FINNHUB_API_KEY. With no key every function returns empty
and the bot behaves exactly as before.

Headlines are untrusted text: they pass through osint_collect.safe_text, the
same filter as the Google News headlines.
"""

import logging
import os
import time
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://finnhub.io/api/v1"
PAUSE = 1.1          # seconds between requests: stays well under 60/minute
TIMEOUT = 20


def api_key() -> str:
    return os.getenv("FINNHUB_API_KEY", "").strip()


def _get(path: str, params: Dict, key: str, session=None) -> Optional[object]:
    if not key:
        return None
    try:
        resp = (session or requests).get(f"{BASE_URL}/{path}", params={**params, "token": key},
                                         timeout=TIMEOUT)
        if resp.status_code == 429:
            logger.warning("Finnhub rate limit hit")
            return None
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning(f"Finnhub {path} failed: {e}")
        return None
    finally:
        time.sleep(PAUSE)


def parse_calendar(payload, start: date, end: date) -> Dict[str, Dict]:
    """{SYMBOL: {date, hour, eps_estimate}} for reports in [start, end]; earliest date wins."""
    out: Dict[str, Dict] = {}
    rows = (payload or {}).get("earningsCalendar") if isinstance(payload, dict) else None
    for row in rows or []:
        sym = str(row.get("symbol") or "").strip().upper()
        try:
            d = date.fromisoformat(str(row.get("date"))[:10])
        except ValueError:
            continue
        if not sym or not (start <= d <= end):
            continue
        if sym in out and out[sym]["date"] <= d.isoformat():
            continue
        out[sym] = {"date": d.isoformat(), "hour": row.get("hour") or "",
                    "eps_estimate": row.get("epsEstimate")}
    return out


def earnings_calendar(start: date, end: date, key: Optional[str] = None, session=None) -> Dict[str, Dict]:
    """Scheduled US earnings reports in [start, end]; {} without a key or on failure."""
    key = api_key() if key is None else key
    payload = _get("calendar/earnings", {"from": start.isoformat(), "to": end.isoformat()}, key, session)
    return parse_calendar(payload, start, end)


def parse_news(payload, now: datetime) -> List[Dict]:
    """[{title, publisher, published}] newest first, same shape as osint_collect.parse_news."""
    from osint_collect import safe_text
    out = []
    for item in payload if isinstance(payload, list) else []:
        title = safe_text(item.get("headline"))
        ts = item.get("datetime")
        if not title or not isinstance(ts, (int, float)) or ts <= 0:
            continue
        published = datetime.fromtimestamp(ts, tz=timezone.utc)
        if published > now + timedelta(hours=1):
            continue
        out.append({"title": title, "publisher": safe_text(item.get("source")) or "",
                    "published": published.isoformat(), "source": "finnhub"})
    out.sort(key=lambda r: r["published"], reverse=True)
    return out


def company_news(symbol: str, start: date, end: date, key: Optional[str] = None,
                 session=None) -> List[Dict]:
    """Company headlines between start and end; [] without a key or on failure."""
    key = api_key() if key is None else key
    payload = _get("company-news", {"symbol": symbol, "from": start.isoformat(), "to": end.isoformat()},
                   key, session)
    return parse_news(payload, datetime.now(timezone.utc))


def merge_headlines(primary: List[Dict], extra: List[Dict]) -> List[Dict]:
    """Primary headlines plus extra ones whose title isn't already there, newest first."""
    seen = {" ".join(h.get("title", "").lower().split()) for h in primary}
    merged = list(primary)
    for h in extra:
        k = " ".join(h.get("title", "").lower().split())
        if k and k not in seen:
            seen.add(k)
            merged.append(h)
    merged.sort(key=lambda r: str(r.get("published", "")), reverse=True)
    return merged
