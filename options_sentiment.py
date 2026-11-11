"""
Options sentiment
=================
What the options market says about a stock's direction, recorded every
weekday after the close and then checked against what the stock did next,
over short (1 and 5 sessions) and long (20 and 60 sessions) horizons.

Free data has no option history, so nothing here can be backtested: the
evidence builds up from today. Each signal is measured only and never blocks
or triggers a buy until it has proven itself (experiments.py: options_bullish
and options_bearish, auto_promote off).

Per stock, from Alpaca's option chain (snapshots: daily volume, quotes, IV,
delta; contracts: open interest, as of the prior session):

  Short-term (expiries within 45 days)
    pc_volume           put volume / call volume
    call_premium_share  share of the day's premium (volume x mid) spent on calls
    unusual_call_share  share of call volume in contracts trading more than
                        their open interest (new positions, not churn)
    skew                ~25-delta put IV minus ~25-delta call IV, nearest
                        monthly expiry (puts bid up = fear)
    term                near ATM IV minus 3-month+ ATM IV (> 0: an event priced in)
  Long-term (expiries 46-365 days)
    pc_oi_long          put / call open interest: where longer money is positioned
    pc_volume_long      put / call volume

Fixed-rule flags (so they can be measured):
  options_bullish   short-term calls dominate: pc_volume <= 0.5, calls take
                    65%+ of the premium, and no fear skew (<= 5 vol points)
  options_bearish   puts dominate (pc_volume >= 1.2 and calls under 40% of the
                    premium) or fear skew >= 12 vol points
  unusual_calls     35%+ of call volume opens new positions, 1,000+ calls
  options_score     -3..+3 sum of the short- and long-term tilts

Coverage: holdings, the screener's top candidates, the big-mover watch list
and today's movers, plus a random control sample of the universe so the
evidence compares against ordinary stocks, not only the ones the bot likes.

Writes docs/data/options_sentiment.json (today's reads + the evidence so far)
and docs/data/options/YYYY-MM.json (one row per stock per day, forward
returns filled in as they complete; split by month so a daily commit only
rewrites the last few months).

Usage:
    python options_sentiment.py            # snapshot today, score past rows
    python options_sentiment.py --no-snapshot   # only re-score the evidence
"""

import argparse
import json
import identity
import contracts
import logging
import math
import os
import random
import re
import statistics as st
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.resolve()
DOCS = ROOT / "docs" / "data"
OUT_FILE = DOCS / "options_sentiment.json"
LOG_DIR = DOCS / "options"         # one file per month: options/YYYY-MM.json

OPTIONS_URL = "https://data.alpaca.markets/v1beta1/options"
FEED = os.getenv("OPTIONS_FEED", "indicative")

SHORT_DAYS = 45
LONG_DAYS = 365
STRIKE_BAND = (0.5, 1.5)        # strikes considered, as a multiple of the stock price
MIN_VOLUME = 500                # contracts traded (calls + puts) for a short-term read
MIN_OI = 1000                   # open interest for a long-term read
MAX_SYMBOLS = 120
CONTROL = 40
CANDIDATES = 25
HORIZONS = (1, 5, 20, 60)
LOG_KEEP_DAYS = 500
REQUEST_PAUSE = 0.3             # free plan: 200 requests a minute
LOOKUP_DAYS = 4                 # a buy reads the latest snapshot at most this old

BULL_PC, BULL_PREMIUM, BULL_SKEW = 0.5, 0.65, 0.05
BEAR_PC, BEAR_PREMIUM, BEAR_SKEW = 1.2, 0.40, 0.12
UNUSUAL_SHARE, UNUSUAL_MIN_CALLS = 0.35, 1000
LONG_BULL_OI, LONG_BEAR_OI = 0.6, 1.4

_TICKER = re.compile(r"^[A-Z]{1,5}(-[A-Z]{1,2})?$")


# ---------------------------------------------------------------------------
# Metrics (pure)
# ---------------------------------------------------------------------------

def parse_occ(symbol: str) -> Optional[Dict]:
    """'VCEL261120P00055000' -> {root, expiry, type, strike}."""
    try:
        tail = symbol[-15:]
        return {"root": symbol[:-15], "expiry": date(2000 + int(tail[:2]), int(tail[2:4]), int(tail[4:6])),
                "type": tail[6], "strike": int(tail[7:]) / 1000}
    except (ValueError, IndexError):
        return None


def merge_chain(snapshots: Dict[str, Dict], oi: Dict[str, float]) -> List[Dict]:
    """Contracts as {type, strike, expiry, volume, oi, iv, delta, mid} from snapshots + open interest."""
    out = []
    for occ, s in (snapshots or {}).items():
        p = parse_occ(occ)
        if not p or p["type"] not in ("C", "P"):
            continue
        q = (s or {}).get("latestQuote") or {}
        bid, ask = q.get("bp") or 0, q.get("ap") or 0
        trade = ((s or {}).get("latestTrade") or {}).get("p")
        mid = (bid + ask) / 2 if bid and ask else (trade or 0)
        out.append({"type": p["type"], "strike": p["strike"], "expiry": p["expiry"],
                    "volume": float(((s or {}).get("dailyBar") or {}).get("v") or 0),
                    "oi": float(oi.get(occ) or 0), "iv": (s or {}).get("impliedVolatility"),
                    "delta": ((s or {}).get("greeks") or {}).get("delta"), "mid": float(mid or 0)})
    return out


def _ratio(a: float, b: float, nd: int = 3) -> Optional[float]:
    return round(a / b, nd) if b else None


def _mean(xs: List[float]) -> Optional[float]:
    xs = [x for x in xs if x is not None and not math.isnan(x)]
    return sum(xs) / len(xs) if xs else None


def _atm_iv(contracts: List[Dict], price: float) -> Optional[float]:
    """Mean IV of the call and put nearest the money."""
    ivs = []
    for t in ("C", "P"):
        side = [c for c in contracts if c["type"] == t and c.get("iv")]
        if side:
            ivs.append(min(side, key=lambda c: abs(c["strike"] - price))["iv"])
    return _mean(ivs)


def _skew(contracts: List[Dict], price: float) -> Optional[float]:
    """~25-delta put IV minus ~25-delta call IV (by moneyness if deltas are missing)."""
    puts = [c["iv"] for c in contracts if c["type"] == "P" and c.get("iv") and (
        -0.35 <= c["delta"] <= -0.15 if c.get("delta") is not None else 0.88 <= c["strike"] / price <= 0.95)]
    calls = [c["iv"] for c in contracts if c["type"] == "C" and c.get("iv") and (
        0.15 <= c["delta"] <= 0.35 if c.get("delta") is not None else 1.05 <= c["strike"] / price <= 1.12)]
    p, c = _mean(puts), _mean(calls)
    return round(p - c, 4) if p is not None and c is not None else None


def chain_metrics(contracts: List[Dict], price: float, today: date) -> Dict:
    """The short- and long-term readings for one stock's chain."""
    def dte(c):
        return (c["expiry"] - today).days

    short = [c for c in contracts if 0 <= dte(c) <= SHORT_DAYS]
    long_ = [c for c in contracts if SHORT_DAYS < dte(c) <= LONG_DAYS]

    def sums(cs, t):
        side = [c for c in cs if c["type"] == t]
        return (sum(c["volume"] for c in side), sum(c["oi"] for c in side),
                sum(c["volume"] * c["mid"] * 100 for c in side))

    cv, coi, cprem = sums(short, "C")
    pv, poi, pprem = sums(short, "P")
    lcv, lcoi, _ = sums(long_, "C")
    lpv, lpoi, _ = sums(long_, "P")
    unusual = sum(c["volume"] for c in short if c["type"] == "C" and c["volume"] >= 100 and c["volume"] > c["oi"])

    # skew at the expiry nearest 30 days (20-60), term against the nearest 90+ day expiry
    monthly = sorted({c["expiry"] for c in contracts if 20 <= dte(c) <= 60}, key=lambda e: abs((e - today).days - 30))
    near = sorted({c["expiry"] for c in contracts if dte(c) >= 7})
    far = sorted({c["expiry"] for c in contracts if dte(c) >= 90})
    at = lambda e: [c for c in contracts if c["expiry"] == e]
    skew = _skew(at(monthly[0]), price) if monthly else None
    iv_near = _atm_iv(at(near[0]), price) if near else None
    iv_far = _atm_iv(at(far[0]), price) if far else None

    liquid = cv + pv >= MIN_VOLUME
    liquid_long = lcoi + lpoi >= MIN_OI
    return {
        "price": round(price, 2),
        "call_volume": int(cv), "put_volume": int(pv),
        "pc_volume": _ratio(pv, cv) if liquid else None,
        "call_premium_share": _ratio(cprem, cprem + pprem) if liquid and cprem + pprem > 0 else None,
        "unusual_call_share": _ratio(unusual, cv) if liquid else None,
        "pc_oi": _ratio(poi, coi),
        "skew": skew,
        "atm_iv": round(iv_near, 4) if iv_near is not None else None,
        "term": round(iv_near - iv_far, 4) if iv_near is not None and iv_far is not None else None,
        "pc_volume_long": _ratio(lpv, lcv) if lcv + lpv >= MIN_VOLUME / 5 else None,
        "pc_oi_long": _ratio(lpoi, lcoi) if liquid_long else None,
    }


def flags(m: Dict) -> Dict:
    """The fixed-rule reads. None = not enough data, never a guess."""
    pc, prem, skew, unusual = m.get("pc_volume"), m.get("call_premium_share"), m.get("skew"), m.get("unusual_call_share")
    oi_long = m.get("pc_oi_long")
    have_short = pc is not None and prem is not None
    bull = have_short and pc <= BULL_PC and prem >= BULL_PREMIUM and (skew is None or skew <= BULL_SKEW)
    bear = (have_short and pc >= BEAR_PC and prem <= BEAR_PREMIUM) or (skew is not None and skew >= BEAR_SKEW)
    score = 0
    if have_short:
        score += (pc <= 0.6) - (pc >= BEAR_PC)
        score += (prem >= BULL_PREMIUM) - (prem <= BEAR_PREMIUM)
    if skew is not None:
        score -= skew >= BEAR_SKEW
    if unusual is not None and m.get("call_volume", 0) >= UNUSUAL_MIN_CALLS:
        score += unusual >= UNUSUAL_SHARE
    if oi_long is not None:
        score += (oi_long <= LONG_BULL_OI) - (oi_long >= LONG_BEAR_OI)
    known = have_short or skew is not None or oi_long is not None
    return {
        "options_bullish": bool(bull) if have_short else None,
        "options_bearish": bool(bear) if (have_short or skew is not None) else None,
        "unusual_calls": (unusual >= UNUSUAL_SHARE and m.get("call_volume", 0) >= UNUSUAL_MIN_CALLS)
                         if unusual is not None else None,
        "long_tilt": (None if oi_long is None else
                      "bullish" if oi_long <= LONG_BULL_OI else "bearish" if oi_long >= LONG_BEAR_OI else "neutral"),
        "options_score": int(max(-3, min(3, score))) if known else None,
    }


def read_line(sym: str, m: Dict, f: Dict) -> str:
    """One plain sentence a person can read on a phone."""
    if f.get("options_score") is None:
        return f"{sym}: too little options activity to read"
    bits = []
    if m.get("pc_volume") is not None:
        bits.append(f"{m['put_volume']:,} puts vs {m['call_volume']:,} calls")
    if m.get("call_premium_share") is not None:
        bits.append(f"{m['call_premium_share']:.0%} of premium on calls")
    if f.get("unusual_calls"):
        bits.append(f"{m['unusual_call_share']:.0%} of calls opening new positions")
    if m.get("skew") is not None:
        bits.append(f"skew {m['skew'] * 100:+.0f} vol pts")
    if f.get("long_tilt"):
        bits.append(f"long-dated OI {f['long_tilt']}")
    tilt = "bullish" if f["options_score"] > 0 else "bearish" if f["options_score"] < 0 else "neutral"
    return f"{sym} {tilt} ({f['options_score']:+d}): " + ", ".join(bits)


# ---------------------------------------------------------------------------
# Evidence (pure)
# ---------------------------------------------------------------------------

def _t(xs: List[float]) -> Optional[float]:
    if len(xs) < 3:
        return None
    sd = st.stdev(xs)
    return round(st.mean(xs) / (sd / math.sqrt(len(xs))), 2) if sd > 0 else None


def _spearman(xs: List[float], ys: List[float]) -> Optional[float]:
    if len(xs) < 5:
        return None

    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        for pos, i in enumerate(order):
            r[i] = pos
        return r
    rx, ry = ranks(xs), ranks(ys)
    mx, my = st.mean(rx), st.mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return num / den if den else None


# Metric -> sign that makes "higher = more bullish"
IC_METRICS = {"options_score": 1, "pc_volume": -1, "call_premium_share": 1, "unusual_call_share": 1,
              "skew": -1, "pc_oi_long": -1, "term": -1}


def evidence(rows: List[Dict]) -> Dict:
    """
    For each flag and horizon: mean excess return (vs SPY) of flagged stocks,
    of the unflagged, and of the control group, with t-statistics. For each
    metric: the average daily rank correlation (IC) with forward excess return.
    """
    out = {"rows": len(rows), "days": len({r["date"] for r in rows}), "flags": {}, "ic": {}, "jump_next_session": {}}
    for flag, want in (("options_bullish", True), ("options_bearish", True), ("unusual_calls", True)):
        row = {}
        for h in HORIZONS:
            key = f"x{h}"
            on = [r["fwd"][key] for r in rows if r.get(flag) is want and key in (r.get("fwd") or {})]
            off = [r["fwd"][key] for r in rows if r.get(flag) is (not want) and key in (r.get("fwd") or {})]
            if not on and not off:
                continue
            row[f"d{h}"] = {"n": len(on), "mean": round(st.mean(on), 4) if on else None, "t": _t(on),
                            "rest_mean": round(st.mean(off), 4) if off else None,
                            "edge": round(st.mean(on) - st.mean(off), 4) if on and off else None}
        if row:
            out["flags"][flag] = row
    by_day: Dict[str, List[Dict]] = {}
    for r in rows:
        by_day.setdefault(r["date"], []).append(r)
    for metric, sign in IC_METRICS.items():
        row = {}
        for h in HORIZONS:
            key, ics = f"x{h}", []
            for d in sorted(by_day):
                pts = [(sign * r[metric], r["fwd"][key]) for r in by_day[d]
                       if r.get(metric) is not None and key in (r.get("fwd") or {})]
                ic = _spearman([a for a, _ in pts], [b for _, b in pts])
                if ic is not None:
                    ics.append(ic)
            if ics:
                row[f"d{h}"] = {"days": len(ics), "mean_ic": round(st.mean(ics), 3), "t": _t(ics)}
        if row:
            out["ic"][metric] = row
    for flag in ("unusual_calls", "options_bullish"):
        on = [r for r in rows if r.get(flag) is True and "r1" in (r.get("fwd") or {})]
        rest = [r for r in rows if r.get(flag) is False and "r1" in (r.get("fwd") or {})]
        rate = lambda rs: round(sum(r["fwd"]["r1"] >= 0.20 for r in rs) / len(rs), 4) if rs else None
        out["jump_next_session"][flag] = {"n": len(on), "rate": rate(on), "rest_rate": rate(rest)}
    return out


def forward(rows: List[Dict], opens, closes, bench: str = "SPY") -> None:
    """Fill each row's forward returns (next open to h sessions later; raw and vs SPY) once complete."""
    from candidate_outcomes import forward_return
    first = closes.index[0].date() if len(closes.index) else None
    for r in rows:
        sym = r["symbol"]
        fwd = r.setdefault("fwd", {})
        if all(f"x{h}" in fwd for h in HORIZONS) or sym not in closes.columns or bench not in closes.columns:
            continue
        if first is None or date.fromisoformat(r["date"]) < first:
            continue          # older than the bars: the entry session is not in them
        day = date.fromisoformat(r["date"])
        for h in HORIZONS:
            if f"x{h}" in fwd:
                continue
            own = forward_return(opens[sym].dropna(), closes[sym].dropna(), day, h)
            ref = forward_return(opens[bench].dropna(), closes[bench].dropna(), day, h)
            if own is None or ref is None:
                continue
            fwd[f"x{h}"] = round(own - ref, 4)
            if h == 1:
                fwd["r1"] = round(own, 4)


def read_log(log_dir: Path = None, since: Optional[date] = None) -> List[Dict]:
    """Every logged row (or those from `since`'s month on), oldest first."""
    log_dir = Path(log_dir or LOG_DIR)
    rows = []
    for f in sorted(log_dir.glob("????-??.json")):
        if since and f.stem < since.strftime("%Y-%m"):
            continue
        rows += [r for r in _read(f, []) if isinstance(r, dict) and r.get("date") and r.get("symbol")]
    return sorted(rows, key=lambda r: (r["date"], r["symbol"]))


def write_log(rows: List[Dict], log_dir: Path = None) -> None:
    """Rows into their month files; a month with no rows left is removed."""
    log_dir = Path(log_dir or LOG_DIR)
    months: Dict[str, List[Dict]] = {}
    for r in rows:
        months.setdefault(r["date"][:7], []).append(r)
    for f in log_dir.glob("????-??.json"):
        if f.stem not in months:
            f.unlink()
    for m, rs in months.items():
        _write(log_dir / f"{m}.json", sorted(rs, key=lambda r: (r["date"], r["symbol"])))


def lookup(symbol: str, day: date, log_dir: Path = None) -> Optional[Dict]:
    """The latest snapshot of `symbol` taken before `day` (after a close) within LOOKUP_DAYS."""
    rows = read_log(log_dir, since=day - timedelta(days=LOOKUP_DAYS))
    lo = (day - timedelta(days=LOOKUP_DAYS)).isoformat()
    for r in reversed(rows):
        if r.get("symbol") == symbol and lo <= r.get("date", "") < day.isoformat():
            return r
    return None


# ---------------------------------------------------------------------------
# Data (Alpaca; reads only)
# ---------------------------------------------------------------------------

def _headers() -> Dict[str, str]:
    key, secret = os.getenv("ALPACA_API_KEY", ""), os.getenv("ALPACA_SECRET_KEY", "")
    if not key or not secret:
        raise RuntimeError("ALPACA_API_KEY / ALPACA_SECRET_KEY not set")
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}


def _trading_url() -> str:
    paper = os.getenv("ALPACA_PAPER", "true").strip().lower() not in ("false", "0", "no")
    return "https://paper-api.alpaca.markets" if paper else "https://api.alpaca.markets"


def _get_pages(url: str, params: Dict, key: str, session) -> List:
    out, token = [], None
    while True:
        q = dict(params, **({"page_token": token} if token else {}))
        resp = None
        for attempt in range(4):
            resp = session.get(url, params=q, headers=_headers(), timeout=30)
            if resp.status_code == 429 or resp.status_code >= 500:
                time.sleep(2 * (attempt + 1))
                continue
            break
        resp.raise_for_status()
        body = resp.json() or {}
        items = body.get(key) or {}
        out.extend(items.items() if isinstance(items, dict) else items)
        token = body.get("next_page_token")
        time.sleep(REQUEST_PAUSE)
        if not token:
            return out


def fetch_chain(symbol: str, price: float, today: date, session) -> List[Dict]:
    """One stock's contracts within a year and half to one-and-a-half times the price."""
    alp = identity.alpaca(symbol)
    window = {"expiration_date_gte": today.isoformat(),
              "expiration_date_lte": (today + timedelta(days=LONG_DAYS)).isoformat(),
              "strike_price_gte": round(price * STRIKE_BAND[0], 2),
              "strike_price_lte": round(price * STRIKE_BAND[1], 2)}
    snaps = dict(_get_pages(f"{OPTIONS_URL}/snapshots/{alp}", {"feed": FEED, "limit": 1000, **window},
                            "snapshots", session))
    contracts = _get_pages(f"{_trading_url()}/v2/options/contracts",
                           {"underlying_symbols": alp, "limit": 10000, "status": "active", **window},
                           "option_contracts", session)
    oi = {}
    for c in contracts:
        try:
            oi[c["symbol"]] = float(c.get("open_interest") or 0)
        except (TypeError, ValueError, KeyError):
            continue
    return merge_chain(snaps, oi)


def fetch_bars(symbols: List[str], lookback_days: int):
    import screener
    return screener._alpaca_daily_bars(symbols, lookback_days=lookback_days)


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def _read(path: Path, default):
    try:
        return contracts.read(Path(path))
    except Exception:
        return default


def _write(path: Path, payload) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, separators=(",", ":"), default=str))
    tmp.replace(path)


def coverage(today: date, data_dir: Path = DOCS) -> List[tuple]:
    """(symbol, group): holdings, top candidates, big-mover watch list and movers, then a control sample."""
    picked: Dict[str, str] = {}

    def add(syms, group):
        for s in syms:
            if isinstance(s, str) and _TICKER.match(s):
                picked.setdefault(s, group)

    latest = _read(data_dir / "latest.json", {})
    add([p.get("symbol") for p in latest.get("positions") or []
         if p.get("asset_class", "us_equity") == "us_equity"], "holding")
    screen = _read(data_dir / "screener.json", {})
    add([c.get("symbol") for c in (screen.get("candidates") or [])[:CANDIDATES]], "candidate")
    movers = _read(data_dir / "big_movers.json", {})
    add([w.get("symbol") for w in movers.get("watch") or []], "watch")
    add([e.get("symbol") for e in movers.get("today") or []], "mover")
    universe = sorted((_read(data_dir / "universe.json", {}).get("tickers") or {}).keys())
    rest = [s for s in universe if s not in picked and _TICKER.match(s)]
    control = random.Random(today.toordinal()).sample(rest, min(CONTROL, len(rest)))
    room = max(0, MAX_SYMBOLS - len(control))
    return list(picked.items())[:room] + [(s, "control") for s in control]


def run(today: date, snapshot: bool = True, chain_fn: Callable = fetch_chain,
        bars_fn: Callable = fetch_bars, symbols: Optional[List[tuple]] = None, session=None) -> Dict:
    log = read_log()
    symbols = symbols if symbols is not None else coverage(today)
    need = sorted({s for s, _ in symbols} | {r["symbol"] for r in log} | {"SPY"})
    data = bars_fn(need, 200)
    if not data or "Close" not in data:
        raise RuntimeError("no price bars")
    closes, opens = data["Close"], data.get("Open", data["Close"])

    reads, failed = [], []
    if snapshot and not any(r["date"] == today.isoformat() for r in log):
        if session is None:
            import requests
            session = requests.Session()
        for sym, group in symbols:
            if sym not in closes.columns or closes[sym].dropna().empty:
                failed.append({"symbol": sym, "error": "no price"})
                continue
            price = float(closes[sym].dropna().iloc[-1])
            try:
                m = chain_metrics(chain_fn(sym, price, today, session), price, today)
            except Exception as e:
                failed.append({"symbol": sym, "error": str(e)[:160]})
                continue
            f = flags(m)
            row = {"date": today.isoformat(), "symbol": sym, "group": group, "feed": FEED, **m, **f}
            log.append(row)
            reads.append({"symbol": sym, "group": group, "line": read_line(sym, m, f), **f})

    cutoff = (today - timedelta(days=LOG_KEEP_DAYS)).isoformat()
    log = [r for r in log if r["date"] >= cutoff]
    forward(log, opens, closes)
    order = {"holding": 0, "candidate": 1, "watch": 2, "mover": 3, "control": 4}
    reads.sort(key=lambda r: (order.get(r["group"], 5), -abs(r.get("options_score") or 0), r["symbol"]))
    report = {
        "date": today.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "feed": FEED, "covered": len(reads), "failed": failed,
        "reads": reads,
        "evidence": evidence(log),
        "note": "Measured only: options signals never block or trigger a buy until experiments.py promotes them.",
    }
    write_log(log)
    _write(OUT_FILE, report)
    logger.info(f"Options snapshot: {len(reads)} read, {len(failed)} failed; {len(log)} rows logged")
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Daily options sentiment snapshot and evidence")
    ap.add_argument("--no-snapshot", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    from zoneinfo import ZoneInfo
    run(datetime.now(ZoneInfo("America/New_York")).date(), snapshot=not args.no_snapshot)
    return 0


if __name__ == "__main__":
    sys.exit(main())
