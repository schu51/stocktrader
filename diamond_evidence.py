"""
Diamond screen, stage 2: the evidence pack
==========================================
For each company on the watchlist, gathers what an analyst would read before
deciding whether the turn in its numbers is real: what the business is, what
management says about the quarter, what contracts it has announced, and what
can be seen from outside (federal awards, news and social tone).

The numbers screen (diamond_screen.py) finds companies whose finances are
turning. It cannot tell a company with a product customers cannot replace from
one having a good year. diamond_judge.py asks Claude to make that call from
this pack and nothing else.

Sources, all free and keyless:
  SEC EDGAR       latest 10-K (the business section), latest 10-Q (management's
                  discussion), and 8-Ks from the last 180 days. An 8-K with
                  item 1.01 is a material agreement: a contract win.
  USAspending     federal contract dollars this fiscal year against last, by
                  recipient name. Only meaningful for government sellers, and
                  the name match is loose, so it is labelled as such.
  news_sentiment  headline and StockTwits tone (counts only, no text).

Everything fetched here is untrusted text. It is trimmed to fixed lengths and
handed to the model fenced as data; nothing in it is ever executed or stored
beyond the excerpt.

Every fetch fails soft: a document that cannot be read leaves that part of
the pack empty, and the judge is told what is missing.
"""

import html
import re
import time
import unicodedata
from datetime import date, timedelta
from typing import Callable, Dict, List, Optional

BUSINESS_CHARS = 9000        # of the 10-K's business section
MDNA_CHARS = 9000            # of the 10-Q's management discussion
AGREEMENT_CHARS = 2500       # of each material-agreement 8-K
MAX_AGREEMENTS = 3
EVENT_DAYS = 180             # how far back 8-Ks are read

ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{folder}/{name}"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
AWARDS_URL = "https://api.usaspending.gov/api/v2/search/spending_over_time/"

# 8-K items worth a word to the judge. 1.01 is the one that matters most here.
ITEM_NAMES = {
    "1.01": "MATERIAL AGREEMENT signed", "1.02": "material agreement ended", "1.03": "bankruptcy",
    "2.01": "acquisition or disposal completed", "2.02": "results announced", "2.03": "new debt",
    "2.05": "restructuring", "2.06": "impairment", "3.01": "listing notice", "3.02": "unregistered share sale",
    "4.01": "auditor change", "4.02": "PRIOR FINANCIALS NOT TO BE RELIED ON", "5.02": "officer or director change",
    "5.07": "shareholder vote", "7.01": "disclosure", "8.01": "other event", "9.01": "exhibits",
}
QUIET_ITEMS = {"7.01"}       # left out of the line: every earnings release carries it


def strip_html(document: str) -> str:
    """A filing's text: tags, scripts, styles and the inline-XBRL header removed, whitespace collapsed."""
    text = re.sub(r"(?is)<(script|style|ix:header)\b.*?</\1>", " ", document or "")
    text = html.unescape(re.sub(r"(?s)<[^>]+>", " ", text)).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def section(text: str, heading: str, limit: int, fallback_at: float = 0.02) -> str:
    """
    `limit` characters starting at a section's heading. The first match of
    `heading` (a regex) is the table of contents, so the second is taken. Filings
    split and restyle heading words, so the regex should match only the item
    number. If there is no second match, start a fixed way into the document
    (`fallback_at`, a share of its length) rather than return nothing.
    """
    if not text:
        return ""
    starts = [m.start() for m in re.finditer(heading, text, flags=re.I)]
    start = starts[1] if len(starts) > 1 else int(len(text) * fallback_at)
    return text[start:start + limit]


# Phrases that address a reader who is a model, not an investor. A filing is
# written by the company being judged; one that tries to instruct the judge is
# trying to buy itself a score, and the sleeve trades on that score.
_MACHINE_ADDRESS = re.compile(
    r"ignore (all |any |the )?(previous|prior|above|earlier) (instructions|prompts?|text)"
    r"|disregard (all |any |the )?(previous|prior|above|earlier)"
    r"|(as|you are) (now )?an? (ai|artificial intelligence|large language model|language model|llm)\b"
    r"|note to (the )?(ai|llm|model|automated|language model)"
    r"|(system|developer) prompt"
    r"|you (must|should|shall) (assign|give|rate|score|output|respond|reply)"
    r"|respond with an? (edge|demand|score|rating)"
    r"|(assign|give) (it |this company |us )?an? (edge|demand) (score )?of",
    re.I)
# Broad on purpose: the mark blocks a buy, raises an alert on a holding, and
# sells only when it repeats on two lists running (diamond_sleeve.broken), so
# a false alarm costs little and a miss could cost a bought score. Ordinary
# wording ("positioned as an AI company") will sometimes trip it.


def plain(text) -> str:
    """
    Text in one plain form: look-alike characters folded to ordinary ones
    (NFKC), invisible formatting and control characters removed, whitespace
    collapsed. The guard below checks this form and the model is shown this
    form, so a phrase cannot reach one and be hidden from the other.
    """
    if not text:
        return ""
    folded = unicodedata.normalize("NFKC", str(text))
    kept = "".join(" " if c.isspace() else c for c in folded if c.isspace() or unicodedata.category(c) not in ("Cf", "Cc", "Co", "Cs"))
    return " ".join(kept.split())


class EvidenceUnavailable(Exception):
    """A filing the company has made could not be fetched, so a pack would judge it on less than it has said."""


def addressed_to_a_machine(text) -> bool:
    """True if the text contains wording aimed at an automated reader rather than an investor."""
    return bool(_MACHINE_ADDRESS.search(plain(text)))


def recent_filings(submissions: Dict, today: date) -> Dict:
    """
    The latest 10-K and 10-Q, and the 8-Ks of the last EVENT_DAYS days, newest
    first, as they stood on `today`. Nothing filed after `today` is returned,
    so a past date gives what was public then.
    """
    out = {"10-K": None, "10-Q": None, "8-K": []}
    recent = ((submissions or {}).get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    since = (today - timedelta(days=EVENT_DAYS)).isoformat()
    for i, form in enumerate(forms):
        try:
            row = {"date": recent["filingDate"][i], "accession": recent["accessionNumber"][i],
                   "document": recent["primaryDocument"][i], "items": (recent.get("items") or [""] * len(forms))[i] or ""}
        except (KeyError, IndexError):
            continue
        if row["date"] > today.isoformat():
            continue                    # filed after the date being judged from: not public then
        if form in ("10-K", "10-Q") and out[form] is None:
            out[form] = row
        elif form == "8-K" and row["date"] >= since:
            out["8-K"].append(row)
    return out


def eight_k_lines(events: List[Dict]) -> List[str]:
    """One plain line per 8-K: its date and what it reported."""
    lines = []
    for e in events or []:
        items = [i.strip() for i in str(e.get("items") or "").split(",") if i.strip() and i.strip() not in QUIET_ITEMS]
        named = [ITEM_NAMES.get(i, f"item {i}") for i in items]
        lines.append(f"{e.get('date')}: {'; '.join(named) or 'no items listed'}")
    return lines


def filing_url(cik: int, accession: str, name: str) -> str:
    return ARCHIVE_URL.format(cik=int(cik), folder=str(accession).replace("-", ""), name=name)


def federal_awards(name: str, today: date, fetch: Callable[[str], List[Dict]]) -> Optional[Dict]:
    """
    Federal contract dollars awarded to recipients matching `name`, this fiscal
    year and last (the federal year ends 30 September). None if it cannot be
    read or nothing matches. The match is on the name, so it can include an
    unrelated company with a similar one: a hint, not a figure.
    """
    year = today.year + (today.month >= 10)
    try:
        by_year = {str(r["time_period"]["fiscal_year"]): float(r["aggregated_amount"]) for r in fetch(name) or []}
    except Exception:
        return None
    now, before = by_year.get(str(year - 1)), by_year.get(str(year - 2))
    # The year just ended and the one before it: both complete, so they compare fairly
    if not now and not before:
        return None
    return {f"fiscal_{year - 1}": now or 0.0, f"fiscal_{year - 2}": before or 0.0}


NUMBER_KEYS = ("market_value", "ttm_revenue", "revenue_growth", "q_growth_now", "q_growth_2q_ago", "ttm_op_margin",
               "prior_ttm_op_margin", "ttm_gross_margin", "gross_margin_change", "rd_share", "backlog_growth",
               "prepaid_growth", "shares_growth", "runway_years")


def build_pack(row: Dict, submissions: Dict, today: date, fetch_doc: Callable[[int, str, str], str],
               awards: Optional[Dict] = None, sentiment: Optional[Dict] = None) -> Dict:
    """
    Everything the judge reads for one watchlist row. `fetch_doc(cik, accession,
    name)` returns a filing document's HTML. `basis` names the filings the pack
    was built from: when it has not changed, last week's judgment still stands.
    """
    cik = row.get("cik")
    filings = recent_filings(submissions, today)

    def text_of(filing: Optional[Dict]) -> str:
        # A filing that exists but cannot be fetched is not an empty filing: a
        # pack judged without it could score low and sell a holding on that.
        if not filing:
            return ""
        try:
            return plain(strip_html(fetch_doc(cik, filing["accession"], filing["document"])))
        except Exception as exc:
            raise EvidenceUnavailable(f"filing {filing['accession']} could not be fetched ({exc})")

    business = section(text_of(filings["10-K"]), r"item\s*1(?![0-9AB])\s*\.", BUSINESS_CHARS)
    mdna = section(text_of(filings["10-Q"]), r"item\s*2\s*\.", MDNA_CHARS, fallback_at=0.4)
    agreements = []
    for event in filings["8-K"]:
        if len(agreements) >= MAX_AGREEMENTS:
            break
        if "1.01" in [i.strip() for i in event["items"].split(",")]:
            text = text_of(event)
            if text:
                agreements.append({"date": event["date"], "text": text[:AGREEMENT_CHARS]})

    return {
        "ticker": row.get("ticker"), "name": row.get("name"),
        "industry": (submissions or {}).get("sicDescription") or "",
        "basis": "|".join([(filings["10-K"] or {}).get("accession", "none"),
                           (filings["10-Q"] or {}).get("accession", "none"), str(len(filings["8-K"]))]),
        "numbers": {k: row.get(k) for k in NUMBER_KEYS},
        "business": business, "mdna": mdna,
        "events": eight_k_lines(filings["8-K"]), "agreements": agreements,
        "federal_awards": awards, "sentiment": sentiment,
        "suspicious": any(addressed_to_a_machine(t) for t in [business, mdna] + [a["text"] for a in agreements]),
    }


# ── fetchers (network; injected in tests) ────────────────────────────────────

def _sec_get(url: str, session=None):
    import requests
    from config import SEC_EDGAR_USER_AGENT
    resp = (session or requests).get(url, headers={"User-Agent": SEC_EDGAR_USER_AGENT}, timeout=60)
    time.sleep(0.2)
    resp.raise_for_status()
    return resp


def fetch_submissions(cik: int, session=None) -> Dict:
    return _sec_get(SUBMISSIONS_URL.format(cik=int(cik)), session).json()


def fetch_document(cik: int, accession: str, name: str, session=None) -> str:
    return _sec_get(filing_url(cik, accession, name), session).text


_SUFFIXES = re.compile(r"[,.]|\b(inc|incorporated|corp|corporation|co|company|ltd|plc|holdings|group)\b\.?", re.I)


def fetch_awards(name: str, session=None) -> List[Dict]:
    """Contract awards by federal fiscal year for recipients whose name matches."""
    import requests
    search = " ".join(_SUFFIXES.sub(" ", name or "").split())
    if len(search) < 4:
        return []
    body = {"group": "fiscal_year", "filters": {
        "recipient_search_text": [search], "award_type_codes": ["A", "B", "C", "D"],
        "time_period": [{"start_date": f"{date.today().year - 3}-10-01", "end_date": date.today().isoformat()}]}}
    resp = (session or requests).post(AWARDS_URL, json=body, timeout=40)
    resp.raise_for_status()
    return resp.json().get("results") or []
