"""
News & Social Sentiment
=======================
Free, keyless sentiment for a stock, scored without any LLM:

  news     Google News RSS headlines from the last week, scored with a small
           finance word list (upgrade, beats, lawsuit, downgrade, ...)
  social   StockTwits public stream: recent posts that users tagged
           Bullish or Bearish

Run once a day after the screener; the result for every screener candidate
and held position goes to docs/data/sentiment_log.json. Nothing here trades:
experiments.py scores the log weekly against what each stock did next.

Headlines and posts are untrusted text. They are only counted and matched
against fixed word lists — never stored verbatim, executed, or sent anywhere.

Usage:
    python news_sentiment.py --scan            # score today's screener candidates + holdings
    python news_sentiment.py NVDA XOM          # print scores for symbols
"""

import argparse
import json
import logging
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.resolve()
DOCS_DATA = ROOT / "docs" / "data"
SENTIMENT_LOG = DOCS_DATA / "sentiment_log.json"

NEWS_DAYS = 7
MAX_HEADLINES = 40
MAX_FEED_BYTES = 3_000_000
LOG_KEEP_DAYS = 400
REQUEST_PAUSE = 0.6            # seconds between symbols; StockTwits allows ~200 requests an hour unauthenticated
MAX_SCAN_SYMBOLS = 80
USER_AGENT = "Mozilla/5.0 (compatible; stocktrader-research/1.0)"

# Thresholds for the yes/no signals the experiments score
NEWS_MIN_HEADLINES = 5
NEWS_POSITIVE = 0.10
NEWS_NEGATIVE = -0.10
SOCIAL_MIN_TAGGED = 8
SOCIAL_BULLISH = 0.75

# Finance word lists (in the spirit of Loughran-McDonald: words that are
# positive or negative in a financial headline, not in everyday English).
POSITIVE = frozenset("""
upgrade upgrades upgraded beat beats beating outperform outperforms outperformed raise raises raised
raising surge surges surged surging soar soars soared soaring rally rallies rallied jump jumps jumped
record strong stronger strongest growth grows growing accelerate accelerates accelerating profit
profits profitable gain gains gained bullish buyback buybacks dividend approval approved approves
wins win won breakthrough expand expands expansion boost boosts boosted tops topped exceed exceeds
exceeded rebound rebounds recovery optimistic upside momentum partnership contract awarded overweight
""".split())

NEGATIVE = frozenset("""
downgrade downgrades downgraded miss misses missed missing underperform underperforms cut cuts cutting
slash slashes slashed plunge plunges plunged plunging tumble tumbles tumbled slump slumps slumped sink
sinks sank drop drops dropped fall falls fell falling decline declines declined weak weaker weakness
loss losses lawsuit lawsuits sued sues probe investigation investigated fraud recall recalls layoffs
layoff bankruptcy bankrupt default warning warn warns warned bearish selloff concern concerns risk risks
delay delays delayed halt halted fine fined penalty breach hack hacked resign resigns resigned
shortfall disappointing disappoints disappointed downside underweight crash crashes crashed scandal
subpoena restructuring impairment writedown overvalued bubble
""".split())

NEGATORS = frozenset("not no never without fails fail failed denies deny".split())
_WORD = re.compile(r"[a-z']+")


# ---------------------------------------------------------------------------
# Scoring (pure)
# ---------------------------------------------------------------------------

def score_headline(text: str) -> float:
    """
    -1..+1 for one headline: (positive words - negative words) / matched words.
    A negator in the two words before a match flips it ("fails to beat").
    0.0 when no listed word appears.
    """
    words = _WORD.findall(str(text).lower())
    pos = neg = 0
    for i, w in enumerate(words):
        if w not in POSITIVE and w not in NEGATIVE:
            continue
        positive = w in POSITIVE
        if any(p in NEGATORS for p in words[max(0, i - 2):i]):
            positive = not positive
        pos, neg = (pos + 1, neg) if positive else (pos, neg + 1)
    return (pos - neg) / (pos + neg) if pos + neg else 0.0


def news_summary(headlines: List[str]) -> Dict:
    """Aggregate headline scores. news_score is the mean over headlines that matched any listed word."""
    scores = [score_headline(h) for h in headlines]
    scored = [s for s in scores if s != 0.0]
    return {
        "news_n": len(headlines),
        "news_scored": len(scored),
        "news_score": round(sum(scored) / len(scored), 3) if scored else None,
    }


def social_summary(tags: List[Optional[str]]) -> Dict:
    """StockTwits tags ('Bullish' / 'Bearish' / None per post) to counts and a bullish share."""
    bull, bear = tags.count("Bullish"), tags.count("Bearish")
    return {
        "st_msgs": len(tags), "st_bullish": bull, "st_bearish": bear,
        "st_bull_ratio": round(bull / (bull + bear), 3) if bull + bear else None,
    }


def to_signals(s: Dict) -> Dict:
    """
    Yes/no signals for the experiments. None means "not enough data to say",
    which is different from False.
    """
    out = {"news_positive": None, "news_negative": None, "social_bullish": None}
    if s.get("news_score") is not None and (s.get("news_scored") or 0) >= NEWS_MIN_HEADLINES:
        out["news_positive"] = s["news_score"] >= NEWS_POSITIVE
        out["news_negative"] = s["news_score"] <= NEWS_NEGATIVE
    tagged = (s.get("st_bullish") or 0) + (s.get("st_bearish") or 0)
    if s.get("st_bull_ratio") is not None and tagged >= SOCIAL_MIN_TAGGED:
        out["social_bullish"] = s["st_bull_ratio"] >= SOCIAL_BULLISH
    return out


def parse_rss_titles(xml_bytes: bytes, since: datetime) -> List[str]:
    """Headline text from a Google News RSS document, newest `MAX_HEADLINES`, publisher suffix removed."""
    from email.utils import parsedate_to_datetime
    # Untrusted XML. A feed has no need for a DTD or entity declarations, and
    # those are what XXE and entity-expansion attacks are made of: refuse them
    # outright rather than rely on the parser's defaults.
    if len(xml_bytes) > MAX_FEED_BYTES:
        raise ValueError("feed is larger than expected")
    upper = xml_bytes.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise ValueError("feed contains a DTD or entity declaration")
    titles = []
    for item in ET.fromstring(xml_bytes).findall(".//item"):
        title = (item.findtext("title") or "").strip()
        try:
            published = parsedate_to_datetime(item.findtext("pubDate") or "")
            if published.replace(tzinfo=None) < since:
                continue
        except Exception:
            continue
        if title:
            titles.append(title.rsplit(" - ", 1)[0])
    return titles[:MAX_HEADLINES]


def parse_stocktwits_tags(payload: Dict) -> List[Optional[str]]:
    tags = []
    for message in (payload or {}).get("messages") or []:
        basic = ((message.get("entities") or {}).get("sentiment") or {}).get("basic")
        tags.append(basic if basic in ("Bullish", "Bearish") else None)
    return tags


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

def fetch_headlines(symbol: str, session=None) -> List[str]:
    import requests
    http = session or requests
    resp = http.get("https://news.google.com/rss/search",
                    params={"q": f"{symbol} stock when:{NEWS_DAYS}d", "hl": "en-US", "gl": "US", "ceid": "US:en"},
                    headers={"User-Agent": USER_AGENT}, timeout=15)
    resp.raise_for_status()
    return parse_rss_titles(resp.content, datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=NEWS_DAYS))


def fetch_stocktwits(symbol: str, session=None) -> List[Optional[str]]:
    import requests
    http = session or requests
    resp = http.get(f"https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json",
                    headers={"User-Agent": USER_AGENT}, timeout=15)
    resp.raise_for_status()
    return parse_stocktwits_tags(resp.json())


def sentiment_for(symbol: str, fetch_news=fetch_headlines, fetch_social=fetch_stocktwits) -> Dict:
    """Scores for one symbol. Never raises: a source that fails leaves its fields as None."""
    out = {"news_n": None, "news_scored": None, "news_score": None,
           "st_msgs": None, "st_bullish": None, "st_bearish": None, "st_bull_ratio": None}
    try:
        out.update(news_summary(fetch_news(symbol)))
    except Exception as e:
        logger.warning(f"{symbol}: news fetch failed: {e}")
    try:
        out.update(social_summary(fetch_social(symbol)))
    except Exception as e:
        logger.warning(f"{symbol}: StockTwits fetch failed: {e}")
    out.update(to_signals(out))
    return out


# ---------------------------------------------------------------------------
# Daily scan + log
# ---------------------------------------------------------------------------

def scan_symbols() -> List[str]:
    """Today's screener candidates plus current holdings (long stock)."""
    symbols: List[str] = []
    for name, picker in (("screener.json", lambda d: [c.get("symbol") for c in d.get("candidates") or []]),
                         ("latest.json", lambda d: [p.get("symbol") for p in d.get("positions") or []
                                                    if p.get("asset_class", "us_equity") == "us_equity"])):
        try:
            symbols += [s for s in picker(json.loads((DOCS_DATA / name).read_text())) if s]
        except Exception as e:
            logger.warning(f"Could not read {name}: {e}")
    seen, ordered = set(), []
    for s in symbols:
        if isinstance(s, str) and re.fullmatch(r"[A-Z]{1,5}([.-][A-Z]{1,2})?", s) and s not in seen:
            seen.add(s)
            ordered.append(s)
    return ordered[:MAX_SCAN_SYMBOLS]


def load_log(path: Path = SENTIMENT_LOG) -> List[Dict]:
    try:
        data = json.loads(Path(path).read_text())
        return data if isinstance(data, list) else []
    except Exception:
        return []


def lookup(symbol: str, day: date, path: Path = SENTIMENT_LOG) -> Optional[Dict]:
    """Today's logged sentiment for a symbol, or None if it was not scanned."""
    for row in reversed(load_log(path)):
        if row.get("date") == day.isoformat() and row.get("symbol") == symbol:
            return row
    return None


def scan(symbols: List[str], day: date, path: Path = SENTIMENT_LOG, score=sentiment_for,
         pause: float = REQUEST_PAUSE) -> Dict:
    """Score each symbol and replace today's rows in the log. Returns a small summary."""
    rows = []
    for i, symbol in enumerate(symbols):
        rows.append({"date": day.isoformat(), "symbol": symbol, **score(symbol)})
        if pause and i < len(symbols) - 1:
            time.sleep(pause)

    cutoff = (day - timedelta(days=LOG_KEEP_DAYS)).isoformat()
    kept = [r for r in load_log(path) if r.get("date", "") >= cutoff and r.get("date") != day.isoformat()]
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(kept + rows, separators=(",", ":")))
    os.replace(tmp, path)
    return {
        "symbols": len(rows),
        "with_news": sum(1 for r in rows if r.get("news_score") is not None),
        "with_social": sum(1 for r in rows if r.get("st_bull_ratio") is not None),
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
    parser = argparse.ArgumentParser(description="Free news and social sentiment")
    parser.add_argument("symbols", nargs="*")
    parser.add_argument("--scan", action="store_true", help="score screener candidates + holdings into the log")
    args = parser.parse_args()

    if args.scan:
        symbols = scan_symbols()
        summary = scan(symbols, date.today())
        logger.info(f"Sentiment scan: {summary}")
        # Both sources failing for everything means they are blocking us, not that there is no news
        if symbols and summary["with_news"] == 0 and summary["with_social"] == 0:
            logger.error("No sentiment could be fetched for any symbol")
            return 1
        return 0

    for symbol in args.symbols:
        print(symbol, json.dumps(sentiment_for(symbol.upper())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
