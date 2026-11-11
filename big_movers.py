"""
Big movers
==========
Learns from every big one-day jump (+20% or more), not only from the stocks
the bot already liked. Started after CoreWeave rose ~16-20% on 2026-11-11
after its Q3 report while sitting near the bottom of the RS ranks: the
momentum screener can never see a move like that coming, so this module asks
what such moves have in common beforehand and whether that is predictable.

Each weekday after the close it:

  1. Finds every stock in the universe (S&P 500 + Nasdaq-100) that closed 20%+
     above the prior close, plus the day's top market-wide gainers from Alpaca
     (price >= $5, liquid) so moves outside the universe are learned from too.
  2. Records what each one looked like at the PRIOR close (no lookahead): RS
     rank, 1- and 3-month return, distance from the 52-week high, trend, the
     volume pattern, whether it passed the momentum gate, and whether an
     earnings report was due.
  3. Collects the evidence for why it moved - SEC filings (8-K items, merger and
     tender documents) and the day's headlines - and gives it a catalyst label
     by fixed rules (earnings, M&A, regulatory, index, analyst, contract,
     sector move, market move, unknown). The evening "big mover review"
     scheduled task reads the evidence, writes the plain-language why, and may
     correct the label in research/big_mover_reads.json; corrected labels win.
  4. Tests precursor patterns against EVERY stock-day in the universe, not only
     the winners: how often did "earnings due and down 15%+ in a month" lead to
     a 20% day, against the base rate? Learning only from the jumps would find
     patterns that are just as common among the stocks that went nowhere.
  5. Measures what happened AFTER each jump (next open to 5/10/20 sessions
     later, net of SPY): whether chasing a jump pays, by catalyst.
  6. Writes a forward watch list - stocks matching a pattern that has earned
     its place (enough cases, 3x+ the base rate) with earnings expected in the
     next week - which the screener publishes as `catalyst_watch`. The list is
     WATCH-ONLY: it places no orders and changes no live rule. Each day the
     previous list is scored, so the lane has to prove itself live before any
     pull request proposes trading it.

Earnings dates come from SEC 8-K item 2.02 filings ("results of operations"),
so the history is keyless. A future date is estimated from the same quarter a
year earlier and is labelled an estimate.

Usage:
    python big_movers.py                      # daily run (after the close)
    python big_movers.py --backfill-days 365  # rebuild the log from a year of history
    python big_movers.py --dry-run            # print, write nothing

Text from filings and headlines is untrusted: it is folded to plain text and
anything addressed to an automated reader is dropped before it is stored.
"""

import argparse
import json
import logging
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.resolve()
DOCS_DATA = ROOT / "docs" / "data"
LATEST_FILE = DOCS_DATA / "big_movers.json"
LOG_FILE = DOCS_DATA / "big_movers_log.json"
CACHE_FILE = DOCS_DATA / "big_movers_cache.json"
READS_FILE = ROOT / "research" / "big_mover_reads.json"

JUMP = 0.20                  # a "big mover": close 20%+ above the prior close
MIN_PRICE = 5.0              # prior close; below this a 20% day is noise
MIN_DOLLAR_VOLUME = 10e6     # 20-day average, prior day
LOOKBACK_DAYS = 760          # calendar days of bars: a year to warm the 252-day features, a year to learn on
SECTOR_MOVE_PCT = 0.08       # a same-sector stock up this much counts toward a sector move
SECTOR_MOVE_MIN = 3          # ... and this many of them make it one
MARKET_MOVE_PCT = 0.025      # SPY up this much: a market-wide day
MOMENTUM_GATE_RS = 70        # screener's floor RS rank (raised further in weak markets)
DRIFT_HORIZONS = (5, 10, 20)
EARNINGS_CACHE_DAYS = 5      # SEC filing list refreshed after this many days
EARNINGS_AHEAD_DAYS = 7      # watch window: report expected within a week
MARKET_MOVERS_URL = "https://data.alpaca.markets/v1beta1/screener/stocks/movers"
MARKET_MOVERS_MAX = 30
LOG_KEEP_DAYS = 800

# A pattern goes on the watch list only with this much evidence behind it
WATCH_MIN_DAYS = 50          # stock-days matching the pattern
WATCH_MIN_HITS = 5           # of which became 20% days
WATCH_MIN_LIFT = 3.0         # hit rate against the all-days base rate
WATCH_MAX = 15

CATALYSTS = ("mna", "earnings", "regulatory", "index", "analyst", "contract",
             "short_squeeze", "sector_move", "market_move", "unknown")


# ---------------------------------------------------------------------------
# Features (pure)
# ---------------------------------------------------------------------------

def features(data: Dict[str, pd.DataFrame]) -> Dict[str, pd.DataFrame]:
    """
    State of every stock at each day's close (row t = known at t's close).
    Shift by one row to get the state BEFORE a session.
    """
    c = data["Close"]
    v = data["Volume"].reindex_like(c)
    high = data.get("High")
    high = c if high is None else high.reindex_like(c)

    r = lambda a, b: c.shift(a) / c.shift(b) - 1
    rs_raw = 0.40 * (c / c.shift(63) - 1) + 0.20 * r(63, 126) + 0.20 * r(126, 189) + 0.20 * r(189, 252)
    rs_rank = (rs_raw.rank(axis=1, pct=True, method="max") * 100).round().clip(1, 99)

    sma50 = c.rolling(50, min_periods=50).mean()
    sma200 = c.rolling(200, min_periods=200).mean()
    avg_vol = v.shift(1).rolling(20, min_periods=15).mean()
    return {
        "close": c,
        "rs_rank": rs_rank,
        "ret_1m": c / c.shift(21) - 1,
        "ret_3m": c / c.shift(63) - 1,
        "dist_52w_high": c / high.rolling(252, min_periods=200).max() - 1,
        "above_50": c > sma50,
        "trend_up": (c > sma50) & (sma50 > sma200),
        "momentum_gate": (rs_rank >= MOMENTUM_GATE_RS) & (c > sma50) & (sma50 > sma200),
        "vol_surge": v / avg_vol,
        "dollar_volume": (c * v).rolling(20, min_periods=15).mean(),
    }


def day_moves(data: Dict[str, pd.DataFrame]) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """(session return, gap at the open, eligible) — eligible uses the prior day only."""
    c = data["Close"]
    prev = c.shift(1)
    ret = c / prev - 1
    o = data.get("Open")
    gap = (o.reindex_like(c) / prev - 1) if o is not None else ret * np.nan
    feats_dv = (c * data["Volume"].reindex_like(c)).rolling(20, min_periods=15).mean().shift(1)
    eligible = (prev >= MIN_PRICE) & (feats_dv >= MIN_DOLLAR_VOLUME)
    return ret, gap, eligible


def jump_mask(data: Dict[str, pd.DataFrame], threshold: float = JUMP) -> pd.DataFrame:
    ret, _, eligible = day_moves(data)
    return (ret >= threshold) & eligible


def earnings_panel(index: pd.DatetimeIndex, columns, earnings: Dict[str, List[str]]) -> pd.DataFrame:
    """
    True on the session that reacts to an earnings report: the 8-K 2.02 filing
    day (before-the-open report) and the session after it (after-the-close
    report). A filing on a non-trading day maps to the next session.
    """
    panel = pd.DataFrame(False, index=index, columns=columns)
    if len(index) == 0:
        return panel
    for sym in columns:
        for d in earnings.get(sym) or []:
            pos = index.searchsorted(pd.Timestamp(d))
            if pos < len(index):
                panel.iat[pos, panel.columns.get_loc(sym)] = True
    return panel | panel.shift(1, fill_value=False)


def pre_move(feats: Dict[str, pd.DataFrame], sym: str, day: pd.Timestamp) -> Dict:
    """What `sym` looked like at the close before `day`."""
    c = feats["close"]
    pos = c.index.get_loc(day)
    if pos == 0:
        return {}

    def val(name, kind=float, nd=3):
        x = feats[name][sym].iloc[pos - 1]
        if pd.isna(x):
            return None
        return bool(x) if kind is bool else (int(x) if kind is int else round(float(x), nd))

    return {"rs_rank": val("rs_rank", int), "ret_1m": val("ret_1m"), "ret_3m": val("ret_3m"),
            "dist_52w_high": val("dist_52w_high"), "trend_up": val("trend_up", bool),
            "momentum_gate": val("momentum_gate", bool), "vol_surge": val("vol_surge", float, 2)}


# ---------------------------------------------------------------------------
# Catalyst (pure)
# ---------------------------------------------------------------------------

_MNA_FORMS = re.compile(r"^(SC TO-T|SC TO-T/A|SC 14D9|SC 14D9/A|DEFM14A|PREM14A|S-4|S-4/A|425)$")
HEADLINE_RULES = [
    ("mna", re.compile(r"\b(to be acquired|agrees? to be (bought|acquired)|buyout|takeover (bid|offer)|"
                       r"take[- ]private|go[- ]private|merger agreement|all[- ]cash deal|per share in cash|"
                       r"acquisition offer|bid for)\b", re.I)),
    ("earnings", re.compile(r"\b(earnings|quarterly results|results beat|beats? estimates|q[1-4] (results|revenue)|"
                            r"third[- ]quarter|second[- ]quarter|first[- ]quarter|fourth[- ]quarter|"
                            r"raises? (guidance|outlook|forecast)|guidance)\b", re.I)),
    ("regulatory", re.compile(r"\b(fda|approval|approved|phase [123]|trial (data|results)|clinical|ftc|doj|"
                              r"antitrust|court rul\w*|verdict)\b", re.I)),
    ("index", re.compile(r"\b(s&p 500|nasdaq[- ]100|index inclusion|join(s|ing)? the s&p|added to the)\b", re.I)),
    ("analyst", re.compile(r"\b(upgrade[sd]?|price target|initiat\w+ (coverage|at)|outperform|overweight)\b", re.I)),
    ("contract", re.compile(r"\b(contract|award(ed)?|partnership|selected by|deal with|agreement with|"
                            r"strategic investment|stake in)\b", re.I)),
    ("short_squeeze", re.compile(r"\bshort squeeze\b", re.I)),
]


def classify(day: date, filings: List[Dict], headlines: List[Dict], earnings_reaction: bool,
             sector_movers: int, spy_ret: Optional[float]) -> Tuple[str, List[str]]:
    """
    A catalyst label and the evidence for it, by fixed rules. Filings are
    {form, date, items}; headlines are {title, published}. Only evidence from
    the three days up to `day` counts.
    """
    since = (day - timedelta(days=3)).isoformat()
    until = day.isoformat()
    recent = [f for f in filings if since <= str(f.get("date", "")) <= until]
    titles = [h["title"] for h in headlines
              if since <= str(h.get("published", ""))[:10] <= until and h.get("title")]
    evidence: List[str] = []

    for f in recent:
        if _MNA_FORMS.match(str(f.get("form", ""))):
            evidence.append(f"{f['date']}: {f['form']} filed (merger/tender document)")
    if evidence:
        return "mna", evidence + titles[:3]

    found = {}
    for t in titles:
        for label, rx in HEADLINE_RULES:
            if rx.search(t):
                found.setdefault(label, []).append(t)
                break

    if "mna" in found:
        return "mna", found["mna"][:3]
    if earnings_reaction:
        filed = [f"{f['date']}: 8-K results announced (item 2.02)" for f in recent
                 if f.get("form", "").startswith("8-K") and "2.02" in str(f.get("items", ""))]
        return "earnings", (filed or ["earnings report due this session"]) + found.get("earnings", [])[:2]
    for label in ("earnings", "regulatory", "index", "analyst", "short_squeeze"):
        if label in found:
            return label, found[label][:3]
    material = [f for f in recent if f.get("form", "").startswith("8-K") and "1.01" in str(f.get("items", ""))]
    if "contract" in found or material:
        return "contract", [f"{f['date']}: 8-K material agreement (item 1.01)" for f in material] + found.get("contract", [])[:2]
    if sector_movers >= SECTOR_MOVE_MIN:
        return "sector_move", [f"{sector_movers} other stocks in the sector rose {SECTOR_MOVE_PCT:.0%}+ the same day"]
    if spy_ret is not None and spy_ret >= MARKET_MOVE_PCT:
        return "market_move", [f"SPY {spy_ret:+.1%} the same day"]
    return "unknown", titles[:3]


# ---------------------------------------------------------------------------
# Learning (pure)
# ---------------------------------------------------------------------------

def patterns(pre: Dict[str, pd.DataFrame], earn: pd.DataFrame) -> Dict[str, Tuple[str, pd.DataFrame]]:
    """
    Precursor patterns, each a boolean panel of stock-days KNOWN BEFORE the
    session (`pre` = features shifted one row; earnings dates are announced in
    advance). Name -> (plain description, mask).
    """
    down = pre["ret_1m"] <= -0.15
    far = pre["dist_52w_high"] <= -0.40
    gate = pre["momentum_gate"].fillna(False).astype(bool)
    surge = pre["vol_surge"] >= 2
    no_e = ~earn
    return {
        "earnings": ("earnings report due", earn),
        "earnings_beaten_down": ("earnings due, down 15%+ in a month", earn & down),
        "earnings_far_from_high": ("earnings due, 40%+ below the 52-week high", earn & far),
        "earnings_leader": ("earnings due, passes the momentum gate", earn & gate),
        "beaten_down": ("down 15%+ in a month, no earnings", no_e & down),
        "volume_surge": ("volume 2x+ normal the day before, no earnings", no_e & surge),
        "momentum_leader": ("passes the momentum gate, no earnings", no_e & gate),
    }


def precursor_table(pats: Dict[str, Tuple[str, pd.DataFrame]], jumps: pd.DataFrame,
                    ret: pd.DataFrame, eligible: pd.DataFrame,
                    since: Optional[pd.Timestamp] = None) -> Dict:
    """Hit rate of each pattern against the base rate of all eligible stock-days."""
    rows = eligible.index if since is None else eligible.index[eligible.index >= since]
    elig = eligible.loc[rows].fillna(False).astype(bool)
    j = jumps.loc[rows].fillna(False).astype(bool) & elig
    total_days, total_hits = int(elig.values.sum()), int(j.values.sum())
    base = total_hits / total_days if total_days else 0.0
    out = []
    for name, (desc, mask) in pats.items():
        m = mask.reindex(index=rows, columns=elig.columns).fillna(False).astype(bool) & elig
        n, hits = int(m.values.sum()), int((m & j).values.sum())
        rate = hits / n if n else 0.0
        r = ret.loc[rows].values[m.values]
        r = r[~np.isnan(r)]
        out.append({"pattern": name, "description": desc, "days": n, "hits": hits,
                    "hit_rate": round(rate, 5), "lift": round(rate / base, 2) if base else None,
                    "share_of_all_jumps": round(hits / total_hits, 3) if total_hits else None,
                    "avg_day_return": round(float(r.mean()), 4) if len(r) else None,
                    "watchable": n >= WATCH_MIN_DAYS and hits >= WATCH_MIN_HITS
                                 and base > 0 and rate / base >= WATCH_MIN_LIFT})
    out.sort(key=lambda x: -(x["lift"] or 0))
    return {"stock_days": total_days, "jumps": total_hits, "base_rate": round(base, 5), "patterns": out}


def drift(events: List[Dict], data: Dict[str, pd.DataFrame], spy: Optional[pd.Series]) -> List[Dict]:
    """Fill each event's return from the next open to `h` sessions later, net of SPY."""
    c, o = data["Close"], data.get("Open")
    for ev in events:
        sym, day = ev["symbol"], pd.Timestamp(ev["date"])
        if sym not in c.columns or day not in c.index:
            continue
        pos = c.index.get_loc(day)
        if pos + 1 >= len(c.index):
            continue
        entry = (o[sym] if o is not None and sym in o.columns else c[sym]).iloc[pos + 1]
        spy_entry = spy.iloc[pos + 1] if spy is not None else None
        out = dict(ev.get("drift") or {})
        for h in DRIFT_HORIZONS:
            if pos + h >= len(c.index) or pd.isna(entry) or entry <= 0:
                continue
            exit_ = c[sym].iloc[pos + h]
            if pd.isna(exit_):
                continue
            r = float(exit_ / entry - 1)
            if spy is not None and spy_entry and not pd.isna(spy.iloc[pos + h]):
                r -= float(spy.iloc[pos + h] / spy_entry - 1)
            out[f"d{h}"] = round(r, 4)
        if out:
            ev["drift"] = out
    return events


def drift_table(events: List[Dict]) -> Dict:
    """Average excess return after a jump, by catalyst: does chasing it pay?"""
    out = {}
    for cat in CATALYSTS + ("all",):
        evs = events if cat == "all" else [e for e in events if e.get("catalyst") == cat]
        row = {"events": len(evs)}
        for h in DRIFT_HORIZONS:
            vals = [e["drift"][f"d{h}"] for e in evs if f"d{h}" in (e.get("drift") or {})]
            if vals:
                row[f"d{h}"] = {"n": len(vals), "mean": round(float(np.mean(vals)), 4),
                                "median": round(float(np.median(vals)), 4),
                                "win": round(sum(v > 0 for v in vals) / len(vals), 2)}
        if evs:
            out[cat] = row
    return out


def profile(events: List[Dict]) -> Dict:
    """What the jumpers looked like beforehand, and how many the momentum screener could have held."""
    uni = [e for e in events if e.get("in_universe")]
    pre = [e.get("pre") or {} for e in uni]
    vals = lambda k: [p[k] for p in pre if p.get(k) is not None]
    med = lambda k: round(float(np.median(vals(k))), 3) if vals(k) else None
    cats = {}
    for e in events:
        cats[e.get("catalyst", "unknown")] = cats.get(e.get("catalyst", "unknown"), 0) + 1
    gate = vals("momentum_gate")
    return {"events": len(events), "in_universe": len(uni),
            "catalysts": dict(sorted(cats.items(), key=lambda kv: -kv[1])),
            "passed_momentum_gate": round(sum(gate) / len(gate), 2) if gate else None,
            "median_rs_rank": med("rs_rank"), "median_ret_1m": med("ret_1m"),
            "median_dist_52w_high": med("dist_52w_high"),
            "earnings_share": round(cats.get("earnings", 0) / len(events), 2) if events else None}


def expected_earnings(dates: List[str], today: date, ahead: int = EARNINGS_AHEAD_DAYS) -> Optional[str]:
    """
    Estimated next report inside (today, today + ahead], from the same quarter
    a year earlier (52 weeks on), else the last report + 13 weeks. None if a
    report was already filed in the last 30 days or nothing fits the window.
    """
    ds = sorted(date.fromisoformat(d) for d in dates or [])
    if not ds or (today - ds[-1]).days <= 30:
        return None
    guesses = [d + timedelta(days=364) for d in ds] + [ds[-1] + timedelta(days=91)]
    hits = [g for g in guesses if today < g <= today + timedelta(days=ahead)]
    return min(hits).isoformat() if hits else None


def watch_list(table: Dict, state: Dict[str, pd.Series], expected: Dict[str, str],
               sectors: Dict[str, str]) -> List[Dict]:
    """
    Stocks whose state at today's close matches a watchable pattern. Patterns
    that need an earnings report use the estimated date. Watch-only.
    """
    good = [p for p in table.get("patterns", []) if p.get("watchable")]
    if not good:
        return []
    out = {}
    for sym in state["close"].index:
        e = sym in expected
        down = (state["ret_1m"].get(sym) or 0) <= -0.15
        far = (state["dist_52w_high"].get(sym) or 0) <= -0.40
        gate = bool(state["momentum_gate"].get(sym)) if not pd.isna(state["momentum_gate"].get(sym)) else False
        surge = (state["vol_surge"].get(sym) or 0) >= 2
        matches = {"earnings": e, "earnings_beaten_down": e and down, "earnings_far_from_high": e and far,
                   "earnings_leader": e and gate, "beaten_down": not e and down,
                   "volume_surge": not e and surge, "momentum_leader": not e and gate}
        for p in good:
            if matches.get(p["pattern"]) and (sym not in out or p["hit_rate"] > out[sym]["hit_rate"]):
                out[sym] = {"symbol": sym, "pattern": p["pattern"], "why": p["description"],
                            "hit_rate": p["hit_rate"], "lift": p["lift"],
                            "earnings_expected": expected.get(sym), "sector": sectors.get(sym, "other"),
                            "ret_1m": _r(state["ret_1m"].get(sym)), "rs_rank": _i(state["rs_rank"].get(sym))}
    ranked = sorted(out.values(), key=lambda x: (-x["hit_rate"], x["symbol"]))
    return ranked[:WATCH_MAX]


def score_watch(previous: List[Dict], ret_today: pd.Series) -> List[Dict]:
    """Yesterday's watch list against today's session: the live record of the lane."""
    out = []
    for w in previous or []:
        r = ret_today.get(w["symbol"])
        if r is None or pd.isna(r):
            continue
        out.append({"symbol": w["symbol"], "pattern": w["pattern"], "ret": round(float(r), 4),
                    "hit": bool(r >= JUMP)})
    return out


def _r(x, nd=3):
    return None if x is None or pd.isna(x) else round(float(x), nd)


def _i(x):
    return None if x is None or pd.isna(x) else int(x)


# ---------------------------------------------------------------------------
# Fetching (injected in tests)
# ---------------------------------------------------------------------------

def fetch_bars(tickers: List[str], lookback_days: int) -> Optional[Dict[str, pd.DataFrame]]:
    import screener
    return screener._alpaca_daily_bars(tickers, lookback_days=lookback_days)


def fetch_market_movers(session=None) -> List[str]:
    """Alpaca's top gainers today (any exchange-listed stock), price >= $5."""
    import screener
    headers = screener._alpaca_headers()
    if headers is None:
        return []
    if session is None:
        import requests
        session = requests.Session()
    resp = session.get(MARKET_MOVERS_URL, params={"top": 50}, headers=headers, timeout=20)
    resp.raise_for_status()
    rows = (resp.json() or {}).get("gainers") or []
    return [str(r["symbol"]).replace(".", "-") for r in rows
            if (r.get("price") or 0) >= MIN_PRICE
            and re.match(r"^[A-Z]{1,5}(\.[A-Z])?$", str(r.get("symbol", "")))][:MARKET_MOVERS_MAX]


def earnings_dates(submissions: Dict) -> List[str]:
    from osint_collect import filing_rows
    return sorted({r["date"] for r in filing_rows(submissions)
                   if r["form"] in ("8-K", "8-K/A") and "2.02" in str(r["items"])})


SUBMISSIONS_PAGE_URL = "https://data.sec.gov/submissions/{name}"


def fetch_filing_history(cik: int, session, since: str) -> Dict:
    """
    A company's submissions with enough history to reach `since`. The main
    file holds only the latest ~1,000 filings, which for a large company full
    of Form 4s can be under a year; older pages are listed under filings.files.
    """
    import osint_collect as oc
    subs = oc.fetch_submissions(cik, session)
    recent = ((subs.get("filings") or {}).get("recent")) or {}
    dates = recent.get("filingDate") or []
    if dates and min(dates) <= since:
        return subs
    for page in (subs.get("filings") or {}).get("files") or []:
        if str(page.get("filingTo", "")) < since:
            continue
        try:
            older = oc._sec_get(SUBMISSIONS_PAGE_URL.format(name=page["name"]), session).json()
        except Exception as e:
            logger.warning(f"Older SEC filings page {page.get('name')} failed: {e}")
            continue
        for k, v in older.items():
            if isinstance(v, list) and isinstance(recent.get(k), list):
                recent[k] = recent[k] + v
    return subs


def recent_filings(submissions: Dict, since: str) -> List[Dict]:
    from osint_collect import filing_rows
    return [{"form": r["form"], "date": r["date"], "items": r["items"]}
            for r in filing_rows(submissions) if r["date"] >= since]


def refresh_filings(symbols: List[str], today: date, cache: Dict, index: Dict, session,
                    fetch: Callable, force: set = frozenset(), keep_since: str = "",
                    history_since: str = "") -> Dict[str, List[Dict]]:
    """
    Earnings dates into cache["earnings"] (refreshed every few days, or for
    `force` symbols every run), and each force symbol's recent filings.
    """
    earn = cache.setdefault("earnings", {})
    recent = {}
    for sym in symbols:
        entry = earn.get(sym) or {}
        stale = not entry.get("fetched") or \
            (today - date.fromisoformat(entry["fetched"])).days >= EARNINGS_CACHE_DAYS
        company = index.get(sym) or index.get(sym.replace("-", "."))
        if not company or not (stale or sym in force):
            continue
        try:
            subs = fetch(company["cik"], session, history_since)
        except Exception as e:
            logger.warning(f"SEC filings for {sym} failed: {e}")
            continue
        earn[sym] = {"fetched": today.isoformat(), "dates": earnings_dates(subs)}
        if sym in force:
            recent[sym] = recent_filings(subs, keep_since)
    return recent


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def _read(path: Path, default):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=1, default=str))
    tmp.replace(path)


def build_events(days: List[pd.Timestamp], data, feats, earn_panel, ret, gap, eligible,
                 sectors: Dict[str, str], universe: set, filings: Dict[str, List[Dict]],
                 headlines: Dict[str, List[Dict]], reads: Dict) -> List[Dict]:
    """One event per (stock, day) that closed 20%+ up, with its pre-move state and catalyst."""
    events = []
    spy = data["Close"]["SPY"] if "SPY" in data["Close"].columns else None
    for day in days:
        row = ret.loc[day]
        hit = row[(row >= JUMP) & eligible.loc[day].fillna(False).astype(bool)].index
        movers_by_sector: Dict[str, int] = {}
        for s, r in row.items():
            if s in universe and not pd.isna(r) and r >= SECTOR_MOVE_PCT:
                movers_by_sector[sectors.get(s, "other")] = movers_by_sector.get(sectors.get(s, "other"), 0) + 1
        spy_ret = None
        if spy is not None:
            p = spy.index.get_loc(day)
            spy_ret = float(spy.iloc[p] / spy.iloc[p - 1] - 1) if p > 0 else None
        for sym in hit:
            if sym == "SPY":
                continue
            d = day.date()
            sector = sectors.get(sym, "other")
            others = movers_by_sector.get(sector, 0) - (1 if sym in universe else 0)
            cat, evidence = classify(d, filings.get(sym, []), headlines.get(sym, []),
                                     bool(earn_panel[sym].loc[day]) if sym in earn_panel.columns else False,
                                     others if sector != "other" else 0, spy_ret)
            key = f"{sym}@{d.isoformat()}"
            read = reads.get(key) or {}
            events.append({
                "symbol": sym, "date": d.isoformat(), "ret": round(float(row[sym]), 4),
                "gap": _r(gap.loc[day, sym], 4) if sym in gap.columns else None,
                "in_universe": sym in universe, "sector": sector,
                "pre": pre_move(feats, sym, day),
                "catalyst": read.get("catalyst") if read.get("catalyst") in CATALYSTS else cat,
                "catalyst_rule": cat, "evidence": evidence,
                "why": read.get("why"), "lesson": read.get("lesson"),
            })
    return events


def merge_log(log: Dict, new: List[Dict], today: date, reads: Dict) -> Dict:
    """Add new events (one per stock-day; a rerun replaces), drop old ones, apply reads."""
    by_key = {f"{e['symbol']}@{e['date']}": e for e in log.get("events", [])}
    for e in new:
        old = by_key.get(f"{e['symbol']}@{e['date']}")
        if old and old.get("drift"):
            e["drift"] = old["drift"]
        by_key[f"{e['symbol']}@{e['date']}"] = e
    cutoff = (today - timedelta(days=LOG_KEEP_DAYS)).isoformat()
    events = sorted((e for e in by_key.values() if e["date"] >= cutoff), key=lambda e: (e["date"], e["symbol"]))
    for e in events:
        read = reads.get(f"{e['symbol']}@{e['date']}") or {}
        if read.get("catalyst") in CATALYSTS:
            e["catalyst"] = read["catalyst"]
        for k in ("why", "lesson"):
            if read.get(k):
                e[k] = read[k]
    log["events"] = events
    return log


def run(today: Optional[date] = None, backfill_days: int = 0, dry_run: bool = False,
        bars_fn=fetch_bars, movers_fn=fetch_market_movers, news_fn=None,
        index_fn=None, submissions_fn=None, universe_fn=None) -> Dict:
    import osint_collect as oc
    import screener
    universe_fn = universe_fn or screener.get_universe
    index_fn = index_fn or oc.fetch_company_index
    submissions_fn = submissions_fn or fetch_filing_history
    news_fn = news_fn or oc.fetch_news

    sectors = universe_fn()
    universe = set(sectors)
    try:
        extra = [s for s in movers_fn() if s not in universe] if not backfill_days else []
    except Exception as e:
        logger.warning(f"Market movers unavailable: {e}")
        extra = []

    log = _read(LOG_FILE, {"events": [], "watch_log": []})
    prev_latest = _read(LATEST_FILE, {})
    cache = _read(CACHE_FILE, {})
    reads = _read(READS_FILE, {})

    # Out-of-universe names logged in the last month need bars for their drift
    recent_outside = sorted({e["symbol"] for e in log.get("events", [])
                             if not e.get("in_universe")
                             and e["date"] >= (date.today() - timedelta(days=40)).isoformat()})
    lookback = LOOKBACK_DAYS + backfill_days
    tickers = sorted(universe | {"SPY"} | set(extra) | set(recent_outside))
    data = bars_fn(tickers, lookback)
    if not data or "Close" not in data or data["Close"].empty:
        raise RuntimeError("no price bars — cannot look for big movers")

    c = data["Close"]
    last = c.index[-1]
    today = today or last.date()
    ret, gap, eligible = day_moves(data)
    if backfill_days:
        days = [d for d in c.index if d.date() > today - timedelta(days=backfill_days)]
    else:
        days = [last]

    # Which stocks moved: needed before the filings fetch (their recent filings are read every run)
    movers = sorted({s for d in days for s in ret.columns
                     if s != "SPY" and not pd.isna(ret.at[d, s]) and ret.at[d, s] >= JUMP
                     and bool(eligible.at[d, s])})

    try:
        session = oc._session()
        index = index_fn(session)
    except Exception as e:
        logger.error(f"SEC ticker index failed: {e}")
        session, index = None, {}
    since = (days[0].date() - timedelta(days=5)).isoformat()
    filings = refresh_filings(sorted(universe | set(movers)), today, cache, index, session,
                              submissions_fn, force=set(movers), keep_since=since,
                              history_since=c.index[0].date().isoformat())
    earn_dates = {s: (cache.get("earnings", {}).get(s) or {}).get("dates", []) for s in c.columns}
    earn_panel = earnings_panel(c.index, c.columns, earn_dates)

    headlines = {}
    if not backfill_days and session is not None:
        for sym in movers:
            try:
                name = (index.get(sym) or {}).get("name", "")
                headlines[sym] = news_fn(sym, name, session)
            except Exception as e:
                logger.warning(f"Headlines for {sym} failed: {e}")

    feats = features(data)
    new = build_events(days, data, feats, earn_panel, ret, gap, eligible, sectors,
                       universe, filings, headlines, reads)
    log = merge_log(log, new, today, reads)
    spy = c["SPY"] if "SPY" in c.columns else None
    drift(log["events"], data, spy)

    # Learning: patterns against every universe stock-day in the bars we have
    uni_cols = [s for s in c.columns if s in universe]
    pre = {k: v[uni_cols].shift(1) for k, v in feats.items()}
    jumps = jump_mask(data)[uni_cols]
    table = precursor_table(patterns(pre, earn_panel[uni_cols]), jumps, ret[uni_cols], eligible[uni_cols],
                            since=c.index[min(len(c.index) - 1, 252)])

    # Forward watch list: state at today's close, earnings expected in the next week
    state = {k: v[uni_cols].iloc[-1] for k, v in feats.items()}
    expected = {s: d for s in uni_cols if (d := expected_earnings(earn_dates.get(s), today))}
    watch = watch_list(table, state, expected, sectors)

    scored = []
    if not backfill_days and prev_latest.get("date") and prev_latest["date"] < today.isoformat():
        scored = score_watch(prev_latest.get("watch"), ret.loc[last])
        if scored:
            log.setdefault("watch_log", []).append({"date": today.isoformat(), "results": scored})
    wl = [r for day in log.get("watch_log", []) for r in day["results"]]
    watch_record = {"days": len(log.get("watch_log", [])), "names": len(wl),
                    "hits": sum(r["hit"] for r in wl),
                    "avg_next_day": round(float(np.mean([r["ret"] for r in wl])), 4) if wl else None}

    today_events = [e for e in log["events"] if e["date"] == today.isoformat()]
    latest = {
        "date": today.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "threshold": JUMP,
        "today": today_events,
        "watch": watch,
        "watch_scored_today": scored,
        "watch_record": watch_record,
        "lessons": {
            "profile_1y": profile([e for e in log["events"]
                                   if e["date"] >= (today - timedelta(days=365)).isoformat()]),
            "precursors": table,
            "after_the_jump": drift_table(log["events"]),
        },
        "note": "Watch-only: this list places no orders and changes no live rule.",
    }
    if dry_run:
        print(json.dumps({k: latest[k] for k in ("date", "today", "watch", "watch_record")}, indent=1, default=str))
        print(json.dumps(latest["lessons"]["precursors"]["patterns"], indent=1))
        return latest
    _write(LATEST_FILE, latest)
    _write(LOG_FILE, log)
    _write(CACHE_FILE, cache)
    logger.info(f"{len(today_events)} big movers on {today}; watch list {len(watch)}; "
                f"{len(log['events'])} events logged")
    return latest


def load_watch(path: Path = LATEST_FILE, max_age_days: int = 4) -> List[Dict]:
    """The latest watch list for the screener, or [] if missing or stale."""
    payload = _read(path, {})
    try:
        age = (date.today() - date.fromisoformat(payload["date"])).days
    except Exception:
        return []
    return (payload.get("watch") or []) if age <= max_age_days else []


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--backfill-days", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run(backfill_days=args.backfill_days, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
