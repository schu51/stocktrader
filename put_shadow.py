#!/usr/bin/env python3
"""
Put shadow test (research, 2026-11-11)
======================================
A live check of protective puts around earnings, without trading. Each
weekday shortly before the close it looks at every stock the bot holds with a
report due before the next session's close. For each it records the puts it
would buy (real quotes from Alpaca's option chain), then after the report it
records what selling them back would have brought. No orders are placed and
nothing the bot does is changed: it reads positions and quotes only.

Why: put_hedge_study.py prices puts with a model because the repo has no
historical option prices. This records the real ones, so after a few dozen
reports it shows (a) what the market actually charges ahead of a report, to
check the model's MARKUP, (b) the real spreads, and (c) what a put would have
done for the position, in dollars, at the contract sizes the account can
actually buy. One contract covers 100 shares and a sleeve slot is about $2.6k,
so many holdings can only be over- or under-hedged; the dollar columns show
what that does.

Timing: a report on D lands before D's open or after D's close. A stock is
opened on the last session before D, at this run's quotes, and settled on the
first session after D, at that run's quotes. The puts expire on the first
Friday on or after the settle day. Strikes: the listed strike nearest each of
1.00 / 0.95 / 0.90 / 0.85 x the stock price. Buys at the ask, sells at the
bid; an expired put settles at its intrinsic value.

Quotes: the free plan's option feed is "indicative" (derived from OPRA, not
the exchange quote itself); OPTIONS_FEED=opra switches feeds on a paid plan.
The feed is recorded with every quote.

Writes docs/data/put_shadow.json. Usage:
    python put_shadow.py              # settle reports that have passed, open new ones
"""

import json
import logging
import math
import os
import statistics as st
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional
from zoneinfo import ZoneInfo

ROOT = Path(__file__).parent.resolve()
DOCS = ROOT / "docs" / "data"
OUT = DOCS / "put_shadow.json"
TRADES = DOCS / "trades.json"
ET = ZoneInfo("America/New_York")
STRIKES = (1.00, 0.95, 0.90, 0.85)
HEADLINE_STRIKE = 0.90
EXPIRY_SEARCH_DAYS = 14                  # look this far past the settle day for a listed expiry
STALE_DAYS = 10                          # an open record this far past its settle day is settled at intrinsic
OPTIONS_URL = "https://data.alpaca.markets/v1beta1/options"
STOCKS_URL = "https://data.alpaca.markets/v2/stocks"
FEED = os.getenv("OPTIONS_FEED", "indicative")

logger = logging.getLogger(__name__)


# ── rules (pure) ─────────────────────────────────────────────────────────────

def next_session(d: date) -> date:
    """The next weekday (market holidays are not modelled; a holiday only delays a settle by a day)."""
    d += timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def due_to_open(today: date, report: Optional[date]) -> bool:
    """Today is the last session before the report: it lands after today's close or before the next open."""
    return report is not None and today < report <= next_session(today)


def parse_occ(symbol: str) -> Optional[Dict]:
    """'VCEL261120P00055000' -> {root, expiry, type, strike}."""
    try:
        tail = symbol[-15:]
        return {"root": symbol[:-15], "expiry": date(2000 + int(tail[:2]), int(tail[2:4]), int(tail[4:6])),
                "type": tail[6], "strike": int(tail[7:]) / 1000}
    except (ValueError, IndexError):
        return None


def pick_puts(chain: Dict[str, Dict], price: float, settle_on: date) -> Dict[str, Optional[Dict]]:
    """
    From a put chain {occ: {bid, ask, iv}}, the earliest expiry on or after the
    settle day, then for each target strike the listed strike nearest it with a
    two-sided quote. {target: contract or None}.
    """
    rows = []
    for occ, q in (chain or {}).items():
        p = parse_occ(occ)
        if not p or p["type"] != "P" or p["expiry"] < settle_on:
            continue
        bid, ask = q.get("bid"), q.get("ask")
        if not ask or ask <= 0 or bid is None or bid < 0 or ask < bid:
            continue
        rows.append({**p, "occ": occ, "bid": float(bid), "ask": float(ask), "iv": q.get("iv")})
    if not rows:
        return {f"{k:.2f}": None for k in STRIKES}
    expiry = min(r["expiry"] for r in rows)
    listed = [r for r in rows if r["expiry"] == expiry]
    out = {}
    for k in STRIKES:
        best = min(listed, key=lambda r: (abs(r["strike"] - k * price), r["strike"]))
        mid = (best["bid"] + best["ask"]) / 2
        out[f"{k:.2f}"] = {"occ": best["occ"], "strike": best["strike"], "expiry": expiry.isoformat(),
                           "bid": best["bid"], "ask": best["ask"], "iv": best["iv"],
                           "premium_pct": round(best["ask"] / price, 5),
                           "half_spread_pct_of_premium": round((best["ask"] - mid) / mid, 4) if mid > 0 else None}
    return out


def contracts_for(shares: float) -> int:
    """Nearest whole number of contracts (100 shares each), at least one."""
    return max(1, int(round(shares / 100)))


def open_record(today: date, holding: Dict, report: date, price: float, puts: Dict) -> Dict:
    n = contracts_for(holding["shares"])
    return {"symbol": holding["symbol"], "group": holding["group"], "shares": holding["shares"],
            "report": report.isoformat(), "opened": today.isoformat(),
            "settle_on": next_session(report).isoformat(), "s0": price, "feed": FEED,
            "contracts": n, "coverage": round(n * 100 / holding["shares"], 3), "puts": puts}


def settle_record(rec: Dict, today: date, price: float, quotes: Dict[str, Dict]) -> Dict:
    """Close a record at today's stock price and put bids (intrinsic once expired or unquoted)."""
    s0, n, shares = rec["s0"], rec["contracts"], rec["shares"]
    out = dict(rec, settled=today.isoformat(), s1=price, move=round(price / s0 - 1, 5),
               stock_pnl_usd=round(shares * (price - s0), 2), results={})
    for k, p in (rec.get("puts") or {}).items():
        if not p:
            continue
        intrinsic = max(p["strike"] - price, 0.0)
        q = quotes.get(p["occ"]) or {}
        expired = today > date.fromisoformat(p["expiry"])
        if expired or q.get("bid") is None:
            proceeds, how = intrinsic, "expired" if expired else "no quote: intrinsic"
        else:
            proceeds, how = max(float(q["bid"]), intrinsic), "bid"     # under intrinsic: exercise instead
        net = proceeds - p["ask"]
        out["results"][k] = {"proceeds": proceeds, "how": how, "net_pct": round(net / s0, 5),
                             "hedged_pct": round(price / s0 - 1 + net / s0, 5),
                             "put_pnl_usd": round(n * 100 * net, 2),
                             "position_pnl_usd": round(shares * (price - s0) + n * 100 * net, 2)}
    return out


def summarize(settled: List[Dict]) -> Dict:
    out = {}
    for group in sorted({r["group"] for r in settled}):
        rows = [r for r in settled if r["group"] == group]
        g = {"reports": len(rows), "move_mean": round(st.mean(r["move"] for r in rows), 4),
             "move_worst": round(min(r["move"] for r in rows), 4),
             "coverage_median": round(st.median(r["coverage"] for r in rows), 2),
             "stock_pnl_usd": round(sum(r["stock_pnl_usd"] for r in rows), 2), "strikes": {}}
        for k in (f"{x:.2f}" for x in STRIKES):
            have = [r for r in rows if k in r.get("results", {})]
            if not have:
                continue
            res = [r["results"][k] for r in have]
            puts = [r["puts"][k] for r in have]
            ivs = [p["iv"] for p in puts if p.get("iv")]
            spreads = [p["half_spread_pct_of_premium"] for p in puts if p.get("half_spread_pct_of_premium") is not None]
            g["strikes"][k] = {
                "reports": len(have),
                "premium_pct_mean": round(st.mean(p["premium_pct"] for p in puts), 4),
                "iv_median": round(st.median(ivs), 3) if ivs else None,
                "half_spread_pct_of_premium_median": round(st.median(spreads), 3) if spreads else None,
                "net_pct_mean": round(st.mean(x["net_pct"] for x in res), 4),
                "share_paid_off": round(sum(x["net_pct"] > 0 for x in res) / len(res), 3),
                "hedged_mean": round(st.mean(x["hedged_pct"] for x in res), 4),
                "hedged_worst": round(min(x["hedged_pct"] for x in res), 4),
                "put_pnl_usd": round(sum(x["put_pnl_usd"] for x in res), 2),
                "position_pnl_usd": round(sum(x["position_pnl_usd"] for x in res), 2),
            }
        out[group] = g
    return out


# ── data (Alpaca market data; reads only) ────────────────────────────────────

def _headers() -> Dict[str, str]:
    key, secret = os.getenv("ALPACA_API_KEY", ""), os.getenv("ALPACA_SECRET_KEY", "")
    if not key or not secret:
        raise RuntimeError("ALPACA_API_KEY / ALPACA_SECRET_KEY not set")
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}


def _get_pages(url: str, params: Dict) -> Dict[str, Dict]:
    import requests
    out, token = {}, None
    while True:
        q = dict(params, **({"page_token": token} if token else {}))
        for attempt in range(4):
            resp = requests.get(url, params=q, headers=_headers(), timeout=30)
            if resp.status_code == 429 or resp.status_code >= 500:
                time.sleep(2 * (attempt + 1))
                continue
            break
        resp.raise_for_status()
        body = resp.json()
        out.update(body.get("snapshots") or {})
        token = body.get("next_page_token")
        if not token:
            return out


def _quote(snap: Dict) -> Dict:
    q = (snap or {}).get("latestQuote") or {}
    return {"bid": q.get("bp"), "ask": q.get("ap"), "iv": (snap or {}).get("impliedVolatility")}


def put_chain(underlying: str, price: float, settle_on: date) -> Dict[str, Dict]:
    """Puts from 70% to 105% of the price, expiring within EXPIRY_SEARCH_DAYS of the settle day."""
    snaps = _get_pages(f"{OPTIONS_URL}/snapshots/{underlying}", {
        "feed": FEED, "type": "put", "limit": 1000,
        "expiration_date_gte": settle_on.isoformat(),
        "expiration_date_lte": (settle_on + timedelta(days=EXPIRY_SEARCH_DAYS)).isoformat(),
        "strike_price_gte": round(price * 0.70, 2), "strike_price_lte": round(price * 1.05, 2)})
    return {occ: _quote(s) for occ, s in snaps.items()}


def option_quotes(symbols: Iterable[str]) -> Dict[str, Dict]:
    syms = sorted(set(symbols))
    out = {}
    for i in range(0, len(syms), 100):
        snaps = _get_pages(f"{OPTIONS_URL}/snapshots", {"feed": FEED, "symbols": ",".join(syms[i:i + 100])})
        out.update({occ: _quote(s) for occ, s in snaps.items()})
    return out


def stock_prices(symbols: Iterable[str]) -> Dict[str, float]:
    """Latest trade per symbol (IEX: real time on the free plan)."""
    import requests
    syms = sorted(set(symbols))
    if not syms:
        return {}
    resp = requests.get(f"{STOCKS_URL}/snapshots", params={"symbols": ",".join(syms), "feed": "iex"},
                        headers=_headers(), timeout=30)
    resp.raise_for_status()
    out = {}
    for s, snap in (resp.json() or {}).items():
        p = ((snap or {}).get("latestTrade") or {}).get("p")
        if p and float(p) > 0:
            out[s] = float(p)
    return out


def report_date(symbol: str, today: date) -> Optional[date]:
    try:
        from diamond_sleeve import next_report
        return next_report(symbol, today)
    except Exception:
        return None


def holdings(positions: List[Dict], trades: List[Dict]) -> List[Dict]:
    """Long stock positions: [{symbol, shares, group}] with group 'sleeve' or 'book'."""
    sleeve = {t.get("symbol") for t in trades or [] if isinstance(t, dict)
              and t.get("status") == "OPEN" and t.get("sleeve") and t.get("symbol")}
    out = []
    for p in positions or []:
        try:
            qty = float(p.get("qty") or 0)
        except (TypeError, ValueError):
            continue
        if p.get("symbol") and qty > 0 and p.get("asset_class", "us_equity") == "us_equity":
            out.append({"symbol": p["symbol"], "shares": qty, "group": "sleeve" if p["symbol"] in sleeve else "book"})
    return out


# ── run ──────────────────────────────────────────────────────────────────────

def _load(path: Path, default):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def run(now: datetime, positions: List[Dict], trades: List[Dict], state: Dict,
        chain: Callable = put_chain, quotes: Callable = option_quotes,
        prices: Callable = stock_prices, reports: Callable = report_date) -> Dict:
    today = now.astimezone(ET).date()
    state = dict(state or {})
    open_, settled, notes = list(state.get("open") or []), list(state.get("settled") or []), []

    # Settle what has passed its report
    due = [r for r in open_ if date.fromisoformat(r["settle_on"]) <= today]
    if due:
        px = prices([r["symbol"] for r in due])
        q = quotes([p["occ"] for r in due for p in (r.get("puts") or {}).values() if p])
        for r in due:
            stale = (today - date.fromisoformat(r["settle_on"])).days >= STALE_DAYS
            if r["symbol"] not in px:
                if not stale:
                    notes.append(f"{r['symbol']}: no stock price to settle; retry next run")
                    continue
                notes.append(f"{r['symbol']}: no price {STALE_DAYS} days after settle day; dropped")
                open_.remove(r)
                continue
            settled.append(settle_record(r, today, px[r["symbol"]], q))
            open_.remove(r)
            notes.append(f"{r['symbol']}: settled ({r['report']} report)")

    # Open stocks whose report lands before the next session's close
    tracked = {(r["symbol"], r["report"]) for r in open_ + settled}
    hold = holdings(positions, trades)
    dues = []
    for h in hold:
        rep = reports(h["symbol"], today)
        if due_to_open(today, rep) and (h["symbol"], rep.isoformat()) not in tracked:
            dues.append((h, rep))
    if dues:
        px = prices([h["symbol"] for h, _ in dues])
        for h, rep in dues:
            price = px.get(h["symbol"])
            if not price:
                notes.append(f"{h['symbol']}: no stock price; not opened")
                continue
            try:
                puts = pick_puts(chain(h["symbol"], price, next_session(rep)), price, next_session(rep))
            except Exception as exc:
                notes.append(f"{h['symbol']}: option chain failed ({exc}); not opened")
                continue
            if not any(puts.values()):
                notes.append(f"{h['symbol']}: no listed puts with a quote; not opened")
                continue
            open_.append(open_record(today, h, rep, price, puts))
            notes.append(f"{h['symbol']}: opened ahead of its {rep.isoformat()} report")
    notes.append(f"{len(hold)} holdings checked, {len(dues)} with a report before the next close")

    return {"generated_at": datetime.now(timezone.utc).isoformat(), "started": state.get("started") or today.isoformat(),
            "headline_strike": f"{HEADLINE_STRIKE:.2f}", "notes": notes, "summary": summarize(settled),
            "open": open_, "settled": settled,
            "about": "shadow test, no orders: puts a holding would buy before a report (ask), sold back after "
                     "it (bid). Contracts are whole (100 shares), so coverage is rarely exact. See put_shadow.py."}


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
    from alpaca_broker import AlpacaBroker
    broker = AlpacaBroker()
    positions = broker.get_positions()
    if getattr(broker, "last_positions_ok", True) is False or positions is None:
        print("::error::could not read positions; nothing recorded")
        return 1
    out = run(datetime.now(timezone.utc), positions, _load(TRADES, []), _load(OUT, {}))
    OUT.write_text(json.dumps(out, indent=2, default=str))
    print(json.dumps({"notes": out["notes"], "summary": out["summary"]}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
