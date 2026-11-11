#!/usr/bin/env python3
"""
Overnight shadow test (research, 2026-11-10)
============================================
A live test of the overnight split without trading: every weekday evening
it notes which stocks would be held tonight and their closing price, and the
next evening it settles them at that morning's opening price. No orders are
placed and nothing the bot does is changed.

Why a shadow and not orders: the backtest (overnight_split.py, 2023-11 to
2026-11) found the held book earned +17 bp a night and ~0 by day, and that an
overnight-only trader lives or dies on cost per side (+30%/yr at 2.5 bp, noise
at 5 bp, negative at 10). Paper fills are simulated, so paper orders could not
measure that cost; real auction prices and real spreads can be recorded.

Three groups, each equal-weighted:
  book        the large-cap strategy's holdings at the close (its own picks,
              the backtest's exact hypothesis)
  sleeve      the diamond sleeve's holdings (small and mid caps)
  candidates  the top CANDIDATES screener names held by neither

Prices: SIP daily bars (the free plan serves the consolidated feed 15 minutes
late; this runs well after that). Spreads: the median quoted half-spread in the
seconds before the close and just after the open, from SIP quotes. A closing
or opening auction order pays less than crossing that spread, so the measured
half-spread is a ceiling on cost per side, not an estimate of it.

Earnings: a night is flagged near_report when the stock reports on the night's
first day (after the close) or its last day (before the open).

Writes docs/data/overnight_shadow.json. Usage:
    python overnight_shadow.py              # settle last night, record tonight
"""

import json
import identity
import contracts
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
OUT = DOCS / "overnight_shadow.json"
SCREENER = DOCS / "screener.json"
TRADES = DOCS / "trades.json"
ET = ZoneInfo("America/New_York")
CANDIDATES = 5
COSTS_BPS = (0.0, 2.5, 5.0, 10.0)          # per side; a night pays twice
DATA_URL = "https://data.alpaca.markets/v2/stocks"
QUOTE_WINDOW_SECONDS = 15                  # before the close, after the open

logger = logging.getLogger(__name__)


# ── rules (pure) ─────────────────────────────────────────────────────────────

def select(positions: List[Dict], trades: List[Dict], screener: Dict, n: int = CANDIDATES) -> List[Dict]:
    """Tonight's shadow holdings: [{symbol, group}]. One group per symbol."""
    sleeve = {t.get("symbol") for t in trades or [] if isinstance(t, dict)
              and t.get("status") == "OPEN" and t.get("sleeve") and t.get("symbol")}
    held, out = set(), []
    for p in positions or []:
        sym = p.get("symbol")
        try:
            long_stock = float(p.get("qty") or 0) > 0 and p.get("asset_class", "us_equity") == "us_equity"
        except (TypeError, ValueError):
            long_stock = False
        if not sym or not long_stock:
            continue
        held.add(sym)
        out.append({"symbol": sym, "group": "sleeve" if sym in sleeve else "book"})
    picked = 0
    for c in (screener or {}).get("candidates") or []:
        sym = identity.alpaca(c.get("symbol"))
        if picked >= n or not sym or sym in held:
            continue
        out.append({"symbol": sym, "group": "candidates"})
        held.add(sym)
        picked += 1
    return out


def settle(pending: Dict, closes: Dict[str, float], opens: Dict[str, float],
           open_spreads: Dict[str, Optional[float]], day: str) -> List[Dict]:
    """
    Close out last night's shadow holdings at `day`'s open. `closes` and `opens`
    come from one dividend- and split-adjusted query, so an ex-date or a split
    overnight is not read as a move. A symbol missing either price is dropped.
    """
    nights = []
    for s in pending.get("holdings") or []:
        sym, close, opn = s["symbol"], closes.get(s["symbol"]), opens.get(s["symbol"])
        if not close or not opn or close <= 0 or opn <= 0:
            continue
        nights.append({"from": pending["date"], "to": day, "symbol": sym, "group": s["group"],
                       "close": close, "open": opn, "ret_bps": round((opn / close - 1) * 1e4, 2),
                       "close_half_spread_bps": s.get("close_half_spread_bps"),
                       "open_half_spread_bps": open_spreads.get(sym), "near_report": bool(s.get("near_report"))})
    return nights


def _stats(per_night: List[float], cost_bps: float = 0.0) -> Dict:
    net = [r - 2 * cost_bps for r in per_night]
    if not net:
        return {"nights": 0}
    sd = st.stdev(net) if len(net) > 1 else float("nan")
    comp = math.prod(1 + r / 1e4 for r in net) - 1
    return {"nights": len(net), "mean_bps": round(st.mean(net), 2), "median_bps": round(st.median(net), 2),
            "t": round(st.mean(net) / (sd / math.sqrt(len(net))), 2) if len(net) > 1 and sd > 0 else None,
            "share_positive": round(sum(r > 0 for r in net) / len(net), 3), "total_return": round(comp, 4)}


def summarize(nights: List[Dict]) -> Dict:
    """Per group: equal-weighted book return per night, gross and net of each cost, all nights and ex-earnings."""
    out = {}
    for group in sorted({n["group"] for n in nights}):
        rows = [n for n in nights if n["group"] == group]
        def per_night(rs):
            by = {}
            for n in rs:
                by.setdefault((n["from"], n["to"]), []).append(n["ret_bps"])
            return [st.mean(v) for _, v in sorted(by.items())]
        allr, exr = per_night(rows), per_night([n for n in rows if not n["near_report"]])
        spreads = [x for n in rows for x in (n.get("close_half_spread_bps"), n.get("open_half_spread_bps")) if x is not None]
        measured = round(st.median(spreads), 2) if spreads else None
        out[group] = {
            "symbol_nights": len(rows),
            "median_half_spread_bps": measured,
            "gross": _stats(allr), "gross_ex_earnings": _stats(exr),
            "net": {f"{c:g}bps_per_side": _stats(allr, c) for c in COSTS_BPS},
            "net_at_measured_half_spread": _stats(allr, measured) if measured is not None else None,
        }
    return out


# ── data (Alpaca SIP, 15 minutes delayed on the free plan) ───────────────────

def _headers() -> Dict[str, str]:
    key, secret = os.getenv("ALPACA_API_KEY", ""), os.getenv("ALPACA_SECRET_KEY", "")
    if not key or not secret:
        raise RuntimeError("ALPACA_API_KEY / ALPACA_SECRET_KEY not set")
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}


def _get_all(path: str, params: Dict, key: str) -> Dict[str, List]:
    import requests
    out, token = {}, None
    while True:
        q = dict(params, **({"page_token": token} if token else {}))
        for attempt in range(4):
            resp = requests.get(f"{DATA_URL}/{path}", params=q, headers=_headers(), timeout=30)
            if resp.status_code == 429 or resp.status_code >= 500:
                time.sleep(2 * (attempt + 1))
                continue
            break
        resp.raise_for_status()
        body = resp.json()
        for sym, rows in (body.get(key) or {}).items():
            out.setdefault(sym, []).extend(rows or [])
        token = body.get("next_page_token")
        if not token:
            return out


def daily_bars(symbols: Iterable[str], start: date, end: date) -> Dict[str, Dict[str, Dict]]:
    """{symbol: {YYYY-MM-DD: {"o":..., "c":...}}} from SIP daily bars, split- and dividend-adjusted."""
    syms = sorted(set(symbols))
    if not syms:
        return {}
    # The free plan refuses SIP data from the last 15 minutes: end the request before that
    stop = min(datetime.combine(end + timedelta(days=1), datetime.min.time(), timezone.utc),
               datetime.now(timezone.utc) - timedelta(minutes=16))
    got = _get_all("bars", {"symbols": ",".join(syms), "timeframe": "1Day", "feed": "sip", "adjustment": "all",
                            "start": start.isoformat(), "end": stop.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "limit": 10000}, "bars")
    return {s: {str(b["t"])[:10]: b for b in rows} for s, rows in got.items()}


def half_spreads(symbols: Iterable[str], start: datetime, end: datetime) -> Dict[str, Optional[float]]:
    """Median quoted half-spread in bp over a short window, per symbol (None if no usable quote)."""
    syms = sorted(set(symbols))
    if not syms:
        return {}
    try:
        got = _get_all("quotes", {"symbols": ",".join(syms), "feed": "sip", "limit": 10000,
                                  "start": start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                                  "end": end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")}, "quotes")
    except Exception as exc:
        logger.warning(f"quotes unavailable ({exc}); spreads not recorded")
        return {s: None for s in syms}
    out = {}
    for s in syms:
        vals = []
        for q in got.get(s, []):
            bid, ask = float(q.get("bp") or 0), float(q.get("ap") or 0)
            if bid > 0 and ask > bid:
                vals.append((ask - bid) / 2 / ((ask + bid) / 2) * 1e4)
        out[s] = round(st.median(vals), 2) if vals else None
    return out


def report_soon(symbol: str, day: date, next_day: date) -> bool:
    """True if the next known report falls on `day` (after the close) or `next_day` (before the open)."""
    try:
        from diamond_sleeve import next_report
        r = next_report(symbol, day)
        return r is not None and day <= r <= next_day
    except Exception:
        return False


# ── run ──────────────────────────────────────────────────────────────────────

def _load(path: Path, default):
    try:
        return contracts.read(path)
    except Exception:
        return default


def run(now: datetime, positions: List[Dict], trades: List[Dict], screener: Dict, state: Dict,
        bars: Callable = daily_bars, spreads: Callable = half_spreads,
        reports: Callable = report_soon) -> Dict:
    """One evening: settle the pending night if its next session has traded, then record tonight's."""
    today = now.astimezone(ET).date()
    state = dict(state or {})
    nights = list(state.get("nights") or [])
    pending = state.get("pending")
    notes = []

    # Settle: the first session after the pending date that has bars
    if pending:
        pdate = date.fromisoformat(pending["date"])
        syms = [h["symbol"] for h in pending.get("holdings") or []]
        b = bars(syms, pdate, today)
        days = sorted({d for rows in b.values() for d in rows if d > pending["date"]})
        if days:
            first = days[0]
            opens = {s: float(b[s][first]["o"]) for s in syms if s in b and first in b[s]}
            closes = {s: float(b[s][pending["date"]]["c"]) for s in syms if s in b and pending["date"] in b[s]}
            o_at = datetime.combine(date.fromisoformat(first), datetime.min.time(), ET).replace(hour=9, minute=30)
            sp = spreads(syms, o_at + timedelta(seconds=1), o_at + timedelta(seconds=1 + QUOTE_WINDOW_SECONDS))
            new = settle(pending, closes, opens, sp, first)
            nights += new
            notes.append(f"settled {len(new)} of {len(syms)} from {pending['date']} at {first}'s open")
            pending = None
        else:
            notes.append(f"pending night from {pending['date']} has no next session yet")

    # Record tonight: only on a day that traded, and only once
    if pending is None:
        picks = select(positions, trades, screener)
        b = bars([p["symbol"] for p in picks], today, today)
        closes = {s: float(rows[today.isoformat()]["c"]) for s, rows in b.items() if today.isoformat() in rows}
        if closes:
            c_at = datetime.combine(today, datetime.min.time(), ET).replace(hour=16)
            sp = spreads(list(closes), c_at - timedelta(seconds=QUOTE_WINDOW_SECONDS), c_at)
            nxt = today + timedelta(days=3 if today.weekday() == 4 else 1)
            pending = {"date": today.isoformat(), "holdings": [
                {**p, "close": closes[p["symbol"]], "close_half_spread_bps": sp.get(p["symbol"]),
                 "near_report": reports(p["symbol"], today, nxt)}
                for p in picks if p["symbol"] in closes]}
            notes.append(f"recorded {len(pending['holdings'])} holdings at {today}'s close")
        else:
            notes.append(f"no bars for {today}: market closed, nothing recorded")

    return {"generated_at": datetime.now(timezone.utc).isoformat(), "started": state.get("started") or today.isoformat(),
            "notes": notes, "pending": pending, "summary": summarize(nights), "nights": nights,
            "about": "shadow test, no orders: tonight's holdings at the close, settled at the next open. "
                     "Half-spread is a ceiling on auction cost per side. See overnight_shadow.py."}


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
    from alpaca_broker import AlpacaBroker
    broker = AlpacaBroker()
    positions = broker.get_positions()
    if getattr(broker, "last_positions_ok", True) is False or positions is None:
        print("::error::could not read positions; nothing recorded")
        return 1
    out = run(datetime.now(timezone.utc), positions, _load(TRADES, []), _load(SCREENER, {}), _load(OUT, {}))
    OUT.write_text(json.dumps(out, indent=2, default=str))
    print(json.dumps({"notes": out["notes"], "summary": out["summary"]}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
