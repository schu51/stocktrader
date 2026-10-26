"""
OSINT collection
================
Gathers what can be seen about a company from outside, for the daily OSINT
brief (osint_brief.py). Every source is free and keyless.

  Filings & insiders   SEC EDGAR: new 8-Ks by item, offerings, late filings,
                       activist stakes, merger and tender documents; Form 4
                       open-market buys and sells by officers and directors
  Gov & policy         USAspending contract awards (last 30 days against the
                       fiscal-year pace); Federal Register documents naming the
                       company (rules, Entity List additions, agency actions)
  Alt-data             Wikipedia page views (attention spikes), found through
                       Wikidata's ticker property
  Crowd & market       Google News headline velocity and tone; StockTwits
                       bull/bear tags; FINRA daily short-sale volume

Each collector returns events: {source, kind, date, polarity, text, url}.
Polarity runs -3 (clearly bad) to +3 (clearly good); 0 means worth knowing,
direction unclear. The rules are fixed in code so the score can be measured
(experiments.py); reading what an event means is left to the brief.

Text from filings, headlines and posts is untrusted. It is folded to plain
form and anything addressed to an automated reader is dropped before it is
stored or shown to a model.

Fetchers take an optional session and are injected in tests.
"""

import json
import logging
import re
import statistics
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Callable, Dict, List, Optional

from diamond_evidence import ITEM_NAMES, addressed_to_a_machine, filing_url, plain

logger = logging.getLogger(__name__)

NEW_DAYS = 4                 # "new" = filed or published in the last 4 days (covers a weekend)
INSIDER_DAYS = 30
MAX_FORM4 = 12               # Form 4 documents read per company per run
AWARD_DAYS = 30
NEWS_KEEP = 8                # headlines kept per company for the brief
SHORT_DAYS = 20              # trading days of FINRA files for the short-volume baseline
PAGEVIEW_DAYS = 60

USER_AGENT = "Mozilla/5.0 (compatible; stocktrader-research/1.0)"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
AWARDS_URL = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
FEDREG_URL = "https://www.federalregister.gov/api/v1/documents.json"
WIKIDATA_URL = "https://query.wikidata.org/sparql"
PAGEVIEWS_URL = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/"
                 "all-access/user/{title}/daily/{start}/{end}")
FINRA_URL = "https://cdn.finra.org/equity/regsho/daily/CNMSshvol{day}.txt"
NEWS_URL = "https://news.google.com/rss/search"


def event(source: str, kind: str, day, polarity: int, text: str, url: Optional[str] = None) -> Dict:
    return {"source": source, "kind": kind, "date": str(day)[:10], "polarity": int(polarity),
            "text": plain(text)[:300], "url": url}


def safe_text(text) -> Optional[str]:
    """Plain text, or None if it is addressed to an automated reader."""
    t = plain(text)
    return None if not t or addressed_to_a_machine(t) else t


# ── Filings ──────────────────────────────────────────────────────────────────

# 8-K items with a fixed reading. Everything else is listed at polarity 0.
ITEM_POLARITY = {
    "1.01": 1,   # material agreement signed
    "1.02": -1,  # material agreement ended
    "1.03": -3,  # bankruptcy
    "1.05": -2,  # cybersecurity incident
    "2.05": -1,  # restructuring
    "2.06": -1,  # impairment
    "3.01": -2,  # listing standards notice
    "3.02": -1,  # unregistered share sale (dilution)
    "4.01": -1,  # auditor change
    "4.02": -3,  # prior financials not to be relied on
}
EXTRA_ITEMS = {"1.05": "CYBERSECURITY INCIDENT"}
SKIP_ITEMS = {"7.01", "9.01"}

# Other forms, matched on the form name. (polarity, label)
FORM_RULES = [
    (re.compile(r"^(SC|SCHEDULE) 13D(/A)?$"), 1, "activist-style stake (13D) filed"),
    (re.compile(r"^(SC|SCHEDULE) 13G(/A)?$"), 0, "passive 5%+ stake (13G) filed"),
    (re.compile(r"^(SC TO-T|SC TO-T/A|SC 14D9|SC 14D9/A)$"), 1, "tender offer document"),
    (re.compile(r"^(DEFM14A|PREM14A|S-4|S-4/A)$"), 0, "merger document"),
    (re.compile(r"^(S-1|S-1/A|S-3|S-3/A|S-3ASR|424B[1-5])$"), -1, "securities offering (possible dilution)"),
    (re.compile(r"^NT 10-[KQ](/A)?$"), -2, "LATE periodic report notice"),
    (re.compile(r"^(10-K|10-Q)$"), 0, "periodic report filed"),
]


def filing_rows(submissions: Dict) -> List[Dict]:
    recent = ((submissions or {}).get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    rows = []
    for i, form in enumerate(forms):
        try:
            rows.append({"form": form, "date": recent["filingDate"][i], "accession": recent["accessionNumber"][i],
                         "document": recent["primaryDocument"][i],
                         "items": (recent.get("items") or [""] * len(forms))[i] or ""})
        except (KeyError, IndexError):
            continue
    return rows


def filing_events(submissions: Dict, cik: int, today: date, days: int = NEW_DAYS) -> List[Dict]:
    """Events for filings made in the last `days` days (Form 4s are handled by insider_summary)."""
    since = (today - timedelta(days=days)).isoformat()
    out = []
    for r in filing_rows(submissions):
        if not (since <= r["date"] <= today.isoformat()):
            continue
        url = filing_url(cik, r["accession"], r["document"])
        if r["form"] in ("8-K", "8-K/A"):
            items = [i.strip() for i in str(r["items"]).split(",") if i.strip() and i.strip() not in SKIP_ITEMS]
            if not items:
                continue
            named = [EXTRA_ITEMS.get(i) or ITEM_NAMES.get(i, f"item {i}") for i in items]
            pol = sum(ITEM_POLARITY.get(i, 0) for i in items)
            out.append(event("sec", "8-K", r["date"], max(-3, min(3, pol)), f"8-K: {'; '.join(named)}", url))
            continue
        for pattern, pol, label in FORM_RULES:
            if pattern.match(r["form"]):
                out.append(event("sec", r["form"], r["date"], pol, label, url))
                break
    return out


# ── Insiders (Form 4) ────────────────────────────────────────────────────────

def _xml_text(node, path: str) -> str:
    found = node.find(path)
    return (found.text or "").strip() if found is not None and found.text else ""


def parse_form4(xml_text: str) -> List[Dict]:
    """
    Non-derivative transactions in one Form 4: who, role, code (P buy, S sell,
    ...), shares, price, and whether the 10b5-1 plan box is ticked.
    """
    try:
        root = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    except ET.ParseError:
        return []
    owner = _xml_text(root, "reportingOwner/reportingOwnerId/rptOwnerName")
    rel = root.find("reportingOwner/reportingOwnerRelationship")
    role = ""
    if rel is not None:
        role = _xml_text(rel, "officerTitle") or ("Director" if _xml_text(rel, "isDirector") in ("1", "true") else "") \
            or ("10% owner" if _xml_text(rel, "isTenPercentOwner") in ("1", "true") else "")
    plan = _xml_text(root, "aff10b5One") in ("1", "true")
    out = []
    for tx in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        code = _xml_text(tx, "transactionCoding/transactionCode")
        try:
            shares = float(_xml_text(tx, "transactionAmounts/transactionShares/value") or 0)
            price = float(_xml_text(tx, "transactionAmounts/transactionPricePerShare/value") or 0)
        except ValueError:
            continue
        out.append({"owner": safe_text(owner) or "an insider", "role": safe_text(role) or "",
                    "code": code, "shares": shares, "price": price, "value": shares * price, "plan": plan,
                    "date": _xml_text(tx, "transactionDate/value")[:10]})
    return out


def insider_summary(transactions: List[Dict], today: date) -> Optional[Dict]:
    """30-day open-market buying and selling, and the event it amounts to (or None)."""
    since = (today - timedelta(days=INSIDER_DAYS)).isoformat()
    txs = [t for t in transactions if (t.get("date") or "") >= since]
    buys = [t for t in txs if t["code"] == "P" and t["value"] > 0]
    sells = [t for t in txs if t["code"] == "S" and t["value"] > 0]
    discretionary = [t for t in sells if not t.get("plan")]
    s = {
        "buy_value": round(sum(t["value"] for t in buys)),
        "buyers": sorted({t["owner"] for t in buys}),
        "sell_value": round(sum(t["value"] for t in sells)),
        "sellers": len({t["owner"] for t in sells}),
        "discretionary_sell_value": round(sum(t["value"] for t in discretionary)),
    }
    if len(s["buyers"]) >= 2 or s["buy_value"] >= 250_000:
        pol, text = 2, f"Insider CLUSTER BUY: {len(s['buyers'])} buyer(s), ${s['buy_value']:,} open-market in 30 days"
    elif s["buy_value"] >= 50_000:
        pol, text = 1, f"Insider open-market buy: ${s['buy_value']:,} ({', '.join(s['buyers'][:2])})"
    elif s["discretionary_sell_value"] >= 5_000_000:
        pol, text = -1, (f"Heavy discretionary insider selling: ${s['discretionary_sell_value']:,} "
                         f"outside 10b5-1 plans, {s['sellers']} seller(s)")
    else:
        return {"summary": s, "event": None}
    latest = max(t["date"] for t in (buys or discretionary))
    return {"summary": s, "event": event("sec", "insider", latest, pol, text)}


def form4_documents(submissions: Dict, cik: int, today: date) -> List[str]:
    """Raw-XML URLs of the newest Form 4s from the last INSIDER_DAYS days."""
    since = (today - timedelta(days=INSIDER_DAYS)).isoformat()
    urls = []
    for r in filing_rows(submissions):
        if r["form"] == "4" and r["date"] >= since:
            name = r["document"].split("/")[-1]       # xslF345X05/form4.xml is the rendered view
            urls.append(filing_url(cik, r["accession"], name))
        if len(urls) >= MAX_FORM4:
            break
    return urls


# ── Government & policy ──────────────────────────────────────────────────────

_SUFFIXES = re.compile(r"[,.]|\b(inc|incorporated|corp|corporation|co|company|ltd|plc|holdings|group|the)\b\.?", re.I)


def search_name(name: str) -> str:
    return " ".join(_SUFFIXES.sub(" ", name or "").split())


def award_event(recent: List[Dict], fiscal_total: Optional[float], today: date) -> Optional[Dict]:
    """
    Contract awards in the last 30 days against the company's fiscal-year pace.
    Only a spike is an event: big contractors win every month.
    """
    if not recent:
        return None
    total = sum(float(a.get("Award Amount") or 0) for a in recent)
    pace = (fiscal_total or 0) / 12
    if total < 10_000_000 or (pace and total < 3 * pace):
        return None
    top = max(recent, key=lambda a: float(a.get("Award Amount") or 0))
    agency = safe_text(top.get("Awarding Agency")) or "a federal agency"
    text = (f"Federal awards spike: ${total / 1e6:,.0f}M in {AWARD_DAYS} days vs ${pace / 1e6:,.0f}M/month pace; "
            f"largest from {agency} (name match is loose)")
    return event("usaspending", "awards", today, 1, text)


def fedreg_events(documents: List[Dict]) -> List[Dict]:
    out = []
    for d in documents or []:
        title = safe_text(d.get("title"))
        if not title:
            continue
        agencies = ", ".join(safe_text(a.get("name")) or "" for a in d.get("agencies") or [])[:120]
        pol = -2 if re.search(r"entity list", title, re.I) else 0
        out.append(event("federal_register", d.get("type") or "document", d.get("publication_date"),
                         pol, f"{agencies}: {title}", d.get("html_url")))
    return out


# ── Alt-data: attention ──────────────────────────────────────────────────────

def attention_event(views: List[int], title: str, today: date) -> Optional[Dict]:
    """Wikipedia views over the last 3 days against the prior median. 3x or more is a spike."""
    if len(views) < 30:
        return None
    base = statistics.median(views[:-3]) or 1
    recent = sum(views[-3:]) / 3
    ratio = recent / base
    if ratio < 3 or recent < 500:
        return None
    return event("wikipedia", "attention", today, 0,
                 f"Attention spike: Wikipedia views {ratio:.1f}x normal ({recent:,.0f}/day vs {base:,.0f})",
                 f"https://en.wikipedia.org/wiki/{title}")


# ── Crowd & market ───────────────────────────────────────────────────────────

def parse_news(xml_bytes: bytes, now: datetime) -> List[Dict]:
    """[{title, publisher, published}] from a Google News RSS feed; unsafe titles dropped."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []
    out = []
    for item in root.iter("item"):
        title = safe_text(item.findtext("title"))
        if not title:
            continue
        try:
            published = parsedate_to_datetime(item.findtext("pubDate") or "").astimezone(timezone.utc)
        except Exception:
            continue
        if published > now + timedelta(hours=1):
            continue
        publisher = ""
        if " - " in title:
            title, publisher = title.rsplit(" - ", 1)
        out.append({"title": title, "publisher": publisher, "published": published.isoformat()})
    out.sort(key=lambda r: r["published"], reverse=True)
    return out


def news_event(items: List[Dict], now: datetime) -> Optional[Dict]:
    """Headline velocity: the last 24 hours against the daily average of the prior six days."""
    from news_sentiment import score_headline
    day_ago = now - timedelta(days=1)
    last = [i for i in items if datetime.fromisoformat(i["published"]) >= day_ago]
    prior = [i for i in items if day_ago - timedelta(days=6) <= datetime.fromisoformat(i["published"]) < day_ago]
    pace = len(prior) / 6
    if len(last) < 5 or len(last) < 3 * max(pace, 1):
        return None
    tone = sum(score_headline(i["title"]) for i in last) / len(last)
    pol = 1 if tone >= 0.15 else -1 if tone <= -0.15 else 0
    publishers = len({i["publisher"] for i in last if i["publisher"]})
    return event("news", "velocity", now.date(), pol,
                 f"News surge: {len(last)} headlines in 24h vs {pace:.1f}/day, {publishers} outlets, "
                 f"tone {tone:+.2f}")


def parse_finra(text: str, symbols: set) -> Dict[str, float]:
    """{symbol: short volume / total volume} from one FINRA daily file, for the given symbols."""
    out = {}
    for line in (text or "").splitlines()[1:]:
        parts = line.split("|")
        if len(parts) < 5 or parts[1] not in symbols:
            continue
        try:
            short, total = float(parts[2]), float(parts[4])
        except ValueError:
            continue
        if total > 0:
            out[parts[1]] = short / total
    return out


def short_event(ratios: List[float], today: date) -> Optional[Dict]:
    """Latest short-volume share against its 20-day history. Two deviations above and over 60% is pressure."""
    if len(ratios) < 10:
        return None
    hist, last = ratios[:-1], ratios[-1]
    mean, sd = statistics.mean(hist), statistics.pstdev(hist) or 0.01
    z = (last - mean) / sd
    if z >= 2 and last >= 0.6:
        return event("finra", "short_volume", today, -1,
                     f"Short-volume spike: {last:.0%} of volume vs {mean:.0%} 20-day average (z {z:+.1f})")
    if z <= -2:
        return event("finra", "short_volume", today, 0,
                     f"Short volume collapsed: {last:.0%} vs {mean:.0%} average (z {z:+.1f})")
    return None


def social_event(tags: List[Optional[str]], today: date) -> Optional[Dict]:
    from news_sentiment import SOCIAL_BEARISH, SOCIAL_BULLISH, social_summary
    s = social_summary(tags)
    ratio, n = s.get("st_bull_ratio"), (s.get("st_bullish") or 0) + (s.get("st_bearish") or 0)
    if ratio is None or n < 8:
        return None
    if ratio <= SOCIAL_BEARISH:
        return event("stocktwits", "crowd", today, -1, f"StockTwits crowd sour: {ratio:.0%} bullish of {n} tagged posts")
    if ratio >= 0.95 and n >= 20:
        return event("stocktwits", "crowd", today, 0, f"StockTwits euphoric: {ratio:.0%} bullish of {n} tagged posts")
    return None


# ── Fetchers (network) ───────────────────────────────────────────────────────

def _session():
    import requests
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    return s


def _sec_get(url: str, session):
    from config import SEC_EDGAR_USER_AGENT
    resp = session.get(url, headers={"User-Agent": SEC_EDGAR_USER_AGENT}, timeout=40)
    time.sleep(0.15)                       # SEC asks for 10 requests a second at most
    resp.raise_for_status()
    return resp


def fetch_company_index(session) -> Dict[str, Dict]:
    """{ticker: {cik, name}} from the SEC's ticker file."""
    data = _sec_get(TICKERS_URL, session).json()
    return {str(r["ticker"]).upper(): {"cik": int(r["cik_str"]), "name": r.get("title") or ""}
            for r in data.values()}


def fetch_submissions(cik: int, session) -> Dict:
    return _sec_get(SUBMISSIONS_URL.format(cik=cik), session).json()


def fetch_form4(url: str, session) -> List[Dict]:
    return parse_form4(_sec_get(url, session).text)


def fetch_recent_awards(name: str, today: date, session) -> List[Dict]:
    search = search_name(name)
    if len(search) < 4:
        return []
    body = {"filters": {"recipient_search_text": [search], "award_type_codes": ["A", "B", "C", "D"],
                        "time_period": [{"start_date": (today - timedelta(days=AWARD_DAYS)).isoformat(),
                                         "end_date": today.isoformat()}]},
            "fields": ["Award ID", "Recipient Name", "Award Amount", "Awarding Agency"],
            "sort": "Award Amount", "order": "desc", "limit": 10, "page": 1}
    resp = session.post(AWARDS_URL, json=body, timeout=40)
    resp.raise_for_status()
    return resp.json().get("results") or []


def fetch_fiscal_awards(name: str, today: date, session) -> Optional[float]:
    """Contract dollars in the last complete federal fiscal year, or None."""
    from diamond_evidence import federal_awards, fetch_awards
    totals = federal_awards(name, today, lambda n: fetch_awards(n, session))
    return max(totals.values()) if totals else None


def fetch_fedreg(name: str, today: date, session, days: int = NEW_DAYS) -> List[Dict]:
    search = search_name(name)
    if len(search) < 4:
        return []
    params = {"conditions[term]": f'"{search}"', "per_page": 5, "order": "newest",
              "conditions[publication_date][gte]": (today - timedelta(days=days)).isoformat(),
              "fields[]": ["title", "type", "publication_date", "html_url", "agencies"]}
    resp = session.get(FEDREG_URL, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json().get("results") or []


def fetch_wiki_titles(tickers: List[str], session) -> Dict[str, str]:
    """{ticker: English Wikipedia title} via Wikidata's ticker-symbol qualifier (P249)."""
    if not tickers:
        return {}
    values = " ".join(f'"{t.replace("-", ".")}"' for t in tickers)
    query = f"""SELECT ?ticker ?article WHERE {{
      VALUES ?ticker {{ {values} }}
      ?item p:P414 ?listing . ?listing pq:P249 ?ticker .
      ?article schema:about ?item ; schema:isPartOf <https://en.wikipedia.org/> .
    }}"""
    resp = session.get(WIKIDATA_URL, params={"query": query, "format": "json"}, timeout=60)
    resp.raise_for_status()
    out = {}
    for b in resp.json()["results"]["bindings"]:
        t = b["ticker"]["value"].replace(".", "-")
        out.setdefault(t, b["article"]["value"].rsplit("/", 1)[-1])
    return out


def fetch_pageviews(title: str, today: date, session) -> List[int]:
    start = (today - timedelta(days=PAGEVIEW_DAYS)).strftime("%Y%m%d")
    end = (today - timedelta(days=1)).strftime("%Y%m%d")
    resp = session.get(PAGEVIEWS_URL.format(title=title, start=start, end=end), timeout=20)
    resp.raise_for_status()
    return [int(i["views"]) for i in resp.json().get("items") or []]


def fetch_news(symbol: str, name: str, session) -> List[Dict]:
    q = f'"{search_name(name)}" OR {symbol} stock when:7d' if name else f"{symbol} stock when:7d"
    resp = session.get(NEWS_URL, params={"q": q, "hl": "en-US", "gl": "US", "ceid": "US:en"}, timeout=15)
    resp.raise_for_status()
    return parse_news(resp.content, datetime.now(timezone.utc))


def fetch_stocktwits(symbol: str, session) -> List[Optional[str]]:
    from news_sentiment import parse_stocktwits_tags
    resp = session.get(f"https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json", timeout=15)
    resp.raise_for_status()
    return parse_stocktwits_tags(resp.json())


def fetch_short_history(symbols: List[str], today: date, session, cache: Dict) -> Dict[str, List[float]]:
    """
    {symbol: [short share, oldest first]} over the last SHORT_DAYS trading days.
    `cache` ({YYYYMMDD: {"asked": [symbols], "ratios": {symbol: ratio}}}) is
    updated in place, so a FINRA file is downloaded again only when new
    symbols are wanted from it. Days FINRA has no file for are skipped.
    """
    wanted = set(symbols)
    day, days_found, checked = today, [], 0
    while len(days_found) < SHORT_DAYS and checked < SHORT_DAYS * 2:
        key = day.strftime("%Y%m%d")
        weekday = day.weekday()
        checked += 1
        day -= timedelta(days=1)
        if weekday >= 5:
            continue
        cached = cache.get(key)
        if cached is None or not wanted.issubset(cached.get("asked") or []):
            try:
                resp = session.get(FINRA_URL.format(day=key), timeout=30)
                if resp.status_code in (403, 404):
                    continue                     # holiday, or the day's file not out yet
                resp.raise_for_status()
                asked = wanted | set((cached or {}).get("asked") or [])
                cache[key] = {"asked": sorted(asked), "ratios": parse_finra(resp.text, asked)}
            except Exception as e:
                logger.warning(f"FINRA {key}: {e}")
                continue
        days_found.append(key)
    out = {s: [] for s in symbols}
    for key in sorted(days_found):
        ratios = cache[key]["ratios"]
        for s in symbols:
            if s in ratios:
                out[s].append(ratios[s])
    return out


# ── One company ──────────────────────────────────────────────────────────────

def collect(symbol: str, company: Optional[Dict], today: date, session, context: Dict,
            days: int = NEW_DAYS) -> Dict:
    """
    Everything collected for one company. Never raises: a source that fails is
    listed under "failed" and the rest still count. `context` carries what is
    fetched once per run (Wikipedia titles, short-volume history).
    """
    events: List[Dict] = []
    failed: List[str] = []
    detail: Dict = {}
    name = (company or {}).get("name") or ""

    def attempt(label, fn):
        try:
            return fn()
        except Exception as e:
            logger.warning(f"{symbol}: {label} failed: {e}")
            failed.append(label)
            return None

    if company:
        cik = company["cik"]
        subs = attempt("sec_filings", lambda: fetch_submissions(cik, session))
        if subs:
            events += filing_events(subs, cik, today, days)
            txs: List[Dict] = []
            for url in form4_documents(subs, cik, today):
                txs += attempt("sec_form4", lambda u=url: fetch_form4(u, session)) or []
            ins = insider_summary(txs, today)
            if ins:
                detail["insiders_30d"] = ins["summary"]
                if ins["event"] and ins["event"]["date"] >= (today - timedelta(days=days)).isoformat():
                    events.append(ins["event"])
        recent = attempt("usaspending", lambda: fetch_recent_awards(name, today, session))
        if recent:
            fiscal = attempt("usaspending", lambda: fetch_fiscal_awards(name, today, session))
            ev = award_event(recent, fiscal, today)
            if ev:
                events.append(ev)
        docs = attempt("federal_register", lambda: fetch_fedreg(name, today, session, days))
        events += fedreg_events(docs or [])
    else:
        failed.append("sec_company_lookup")

    title = (context.get("wiki_titles") or {}).get(symbol)
    if title:
        views = attempt("wikipedia", lambda: fetch_pageviews(title, today, session))
        ev = attention_event(views or [], title, today)
        if ev:
            events.append(ev)

    news = attempt("news", lambda: fetch_news(symbol, name, session)) or []
    ev = news_event(news, datetime.now(timezone.utc))
    if ev:
        events.append(ev)
    detail["headlines"] = [{"title": n["title"], "publisher": n["publisher"], "published": n["published"][:16]}
                           for n in news[:NEWS_KEEP]]

    tags = attempt("stocktwits", lambda: fetch_stocktwits(symbol, session))
    if tags:
        ev = social_event(tags, today)
        if ev:
            events.append(ev)

    ratios = (context.get("short_history") or {}).get(symbol) or []
    ev = short_event(ratios, today)
    if ev:
        events.append(ev)
    if ratios:
        detail["short_share_latest"] = round(ratios[-1], 3)

    events.sort(key=lambda e: (abs(e["polarity"]), e["date"]), reverse=True)
    return {"symbol": symbol, "name": name, "events": events, "detail": detail, "failed": sorted(set(failed))}

