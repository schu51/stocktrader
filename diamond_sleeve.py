"""
Diamond sleeve
==============
Buys and holds the best of the diamond watchlist in the paper account: a
handful of small and mid-sized companies whose finances are turning from loss
to profit and whose filings Claude judges to show a real edge and visible
demand (diamond_run.py, diamond_judge.py).

Why it exists, and what it rests on. The numbers screen alone matched a
small-cap index. On the two most recent past lists, judged only from filings
public at the time (research/fast_test/, 2026-10-11), the top third by
judgment returned +25% against +4% for the bottom third and 0% for the index,
among companies under $10 billion. That is 34 companies over one stretch of
the market: enough to trade on paper, not proof. This sleeve is the test.

Rules (owner decisions, 2026-10-08 and 2026-10-11):
  Budget   20% of the account (exit_logic.SLEEVE_SHARE), at most
           MAX_POSITIONS equal slots.
  Rank     by the judgment alone, edge + demand out of 20. Blending in the
           numbers score made the ordering worse in the test.
  Buy      in the top MAX_POSITIONS by judgment on two weekly lists running,
           judged MIN_JUDGMENT or better on current filings, and trading
           above its 200-day average. Never a stock the large-cap strategy
           already holds: the broker keeps one position per symbol.
  Sell     when the thesis breaks: a fresh judgment below SELL_BELOW, or the
           company absent from two weekly lists running (its numbers stopped
           qualifying, or it faded). Slipping down the list is not a reason.
           A holding whose filings appear to address the model opens an issue
           for a human (the check can match ordinary wording); it is sold only
           for an unmistakable attempt to steer the score, or the model's own
           report of one, on last week's list as well as this week's.
  Stop     one price exit, a disaster stop SLEEVE_STOP below entry that never
           trails. Placed by the same sweep that protects every position
           (stop_placement.place_missing_stops).

Everything the large-cap bot does to a position (50-day exit, trailing tiers,
rotation, stale exit, caps) skips sleeve holdings: see exit_logic's sleeve
section. A holding is the sleeve's when its trade-log entry carries
"sleeve": "diamond".

Ownership. A holding is the sleeve's if its open trade-log entry says so, or
if the broker shows its latest filled buy carried the sleeve's client order
id (SLEEVE_ORDER_PREFIX): each job reads its own checkout of the log, which
can lag or lose an entry in a merge. A sleeve holding missing from the log
is adopted back. A buy not yet filled holds its slot and budget.

Fails closed. No list, a stale list, an unreadable trade log or order list,
an unreadable account, a closed market: nothing is bought or sold, and
holdings keep the stops they have at the broker. A sale that fails does not
free its slot; a stop tighter than the sleeve's is replaced.

Usage:
    python diamond_sleeve.py             # dry run: print the plan, send nothing
    python diamond_sleeve.py --execute   # send the orders
"""

import argparse
import json
import logging
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set

from exit_logic import DIAMOND_SLEEVE, SLEEVE_ORDER_PREFIX, SLEEVE_SHARE, SLEEVE_STOP

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.resolve()
DOCS_DATA = ROOT / "docs" / "data"
LIST_FILE = DOCS_DATA / "diamonds.json"
HISTORY_FILE = DOCS_DATA / "diamond_history.json"
TRADES_FILE = DOCS_DATA / "trades.json"
OUT_FILE = DOCS_DATA / "diamond_sleeve.json"

MAX_POSITIONS = 8
MIN_JUDGMENT = 10.0          # edge + demand, of 20, to be bought
SELL_BELOW = 8.0             # a fresh judgment under this: the thesis is not there
LIST_MAX_AGE_DAYS = 9        # the list is weekly; older than this, nothing is decided on it
FILL_WAIT_SECONDS = 60       # how long to wait for buys to fill before placing their stops
SELL_CONFIRM_SECONDS = 20


# ── rules (pure) ─────────────────────────────────────────────────────────────

def judgment_of(row: Dict) -> Optional[float]:
    """Edge plus demand for a watchlist row, if it has a judgment made on its current filings."""
    j = (row or {}).get("judgment")
    if not isinstance(j, dict) or j.get("stale") or j.get("do_not_buy"):
        return None                  # do_not_buy: its filings addressed the model (diamond_judge._guard)
    edge, demand = j.get("edge"), j.get("demand")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or v != v for v in (edge, demand)):
        return None
    return float(edge) + float(demand)


def _list_day(doc: Dict) -> Optional[str]:
    day = str((doc or {}).get("generated_at") or "")[:10]
    try:
        date.fromisoformat(day)
        return day
    except ValueError:
        return None


def _fresh(doc: Dict, today: date) -> bool:
    day = _list_day(doc)
    return bool(day) and 0 <= (today - date.fromisoformat(day)).days <= LIST_MAX_AGE_DAYS


LIST_GAP_DAYS = (5, 9)       # "two weekly lists running": the earlier one 5 to 9 days before


def _last_weeks_list(history: List[Dict], day: str) -> Optional[str]:
    """The latest earlier list of any kind, if it is last week's (LIST_GAP_DAYS before `day`). Otherwise None."""
    previous = _previous_day(history, day, judged_only=False)
    if previous is None or not day:
        return None
    gap = (date.fromisoformat(day) - date.fromisoformat(previous)).days
    return previous if LIST_GAP_DAYS[0] <= gap <= LIST_GAP_DAYS[1] else None


def _previous_day(history: List[Dict], before: str, judged_only: bool = True) -> Optional[str]:
    """The latest earlier list: judged ones only for buying, any for the absence rule."""
    days = {h.get("date") for h in history or [] if isinstance(h, dict) and isinstance(h.get("date"), str)
            and h["date"] < before and (h.get("judgment_rank") is not None or not judged_only)}
    return max(days) if days else None


def buy_candidates(doc: Dict, history: List[Dict], today: date) -> List[Dict]:
    """Watchlist rows the sleeve may buy today, best judgment first."""
    if not _fresh(doc, today):
        return []
    day = _list_day(doc)
    previous = _previous_day(history, day)
    if previous is None:
        return []                                    # one judged list is not "two weeks running"
    gap = (date.fromisoformat(day) - date.fromisoformat(previous)).days
    if not LIST_GAP_DAYS[0] <= gap <= LIST_GAP_DAYS[1]:
        return []                                    # a re-run the next day, or a list weeks old, is not last week's
    top_before = {h["ticker"] for h in history if isinstance(h, dict) and h.get("date") == previous
                  and isinstance(h.get("judgment_rank"), (int, float)) and h["judgment_rank"] <= MAX_POSITIONS}
    out = []
    for row in doc.get("watchlist") or []:
        rank, score = row.get("judgment_rank"), judgment_of(row)
        if (isinstance(rank, (int, float)) and not isinstance(rank, bool) and rank <= MAX_POSITIONS
                and score is not None and score >= MIN_JUDGMENT and row.get("ticker") in top_before):
            out.append(row)
    return sorted(out, key=lambda r: (r["judgment_rank"], r.get("ticker") or ""))


def broken(symbol: str, doc: Dict, history: List[Dict], today: date) -> Optional[str]:
    """Why a holding's thesis has broken, or None to keep holding it. Unknown is never a reason to sell."""
    if not _fresh(doc, today):
        return None
    row = next((r for r in doc.get("watchlist") or [] if r.get("ticker") == symbol), None)
    if row is not None:
        j = row.get("judgment") if isinstance(row.get("judgment"), dict) else {}
        # Read here without the do-not-buy mark: that mark keeps a company out
        # of the buy ranking and must not also hide a failing judgment.
        score = judgment_of(dict(row, judgment={k: v for k, v in j.items() if k != "do_not_buy"}))
        if score is not None and score < SELL_BELOW:
            return f"judgment fell to {score:g} of 20 on its latest filings"
        # A do-not-buy mark on a holding opens an issue for a human (run's
        # failures). It sells only if it is an unmistakable attempt to steer
        # the score, and was there on last week's list too.
        if j.get("manipulation") and not j.get("stale"):
            previous = _last_weeks_list(history, _list_day(doc))
            if previous and any(isinstance(h, dict) and h.get("date") == previous and h.get("ticker") == symbol
                                and h.get("manipulation") for h in history):
                return "its filings tried to steer the model's score on two lists running"
        return None
    previous = _last_weeks_list(history, _list_day(doc))
    if previous is None:
        return None
    on_previous = any(isinstance(h, dict) and h.get("date") == previous and h.get("ticker") == symbol for h in history)
    return None if on_previous else "off the watchlist two weeks running"


def plan(doc: Dict, history: List[Dict], held: Dict[str, float], other_holdings: Set[str], equity: float,
         cash: float, today: date, above_200: Callable[[str], Optional[bool]],
         price_of: Callable[[str], Optional[float]], pending: Set[str] = frozenset()) -> Dict:
    """
    What the sleeve should do today. `held`: {symbol: market value} of its own
    positions. `other_holdings`: symbols the large-cap strategy holds. `cash`:
    what the account can actually spend. Sells are decided first, and a sold
    holding's slot and budget are free for today's buys. `pending`: holdings
    whose buy has not filled yet; they keep their slot and cannot be sold.
    """
    budget = SLEEVE_SHARE * equity
    slot = round(budget / MAX_POSITIONS, 2)
    sells = []
    for symbol in sorted(held):
        if symbol in pending:
            continue                                 # no shares to sell; the slot stays taken
        reason = broken(symbol, doc, history, today)
        if reason:
            sells.append({"symbol": symbol, "reason": reason})
    selling = {s["symbol"] for s in sells}
    keeping = {s: v for s, v in held.items() if s not in selling}

    room, slots, cash = budget - sum(keeping.values()), MAX_POSITIONS - len(keeping), float(cash)
    buys, skipped = [], []
    for row in buy_candidates(doc, history, today):
        symbol = row["ticker"]
        if symbol in held:
            continue
        if symbol in other_holdings:
            skipped.append({"symbol": symbol, "reason": "held by the large-cap strategy"})
            continue
        if slots <= 0:
            skipped.append({"symbol": symbol, "reason": "all positions filled"})
            continue
        trend = above_200(symbol)
        if trend is not True:
            skipped.append({"symbol": symbol, "reason": "below its 200-day average" if trend is False
                            else "200-day average unknown"})
            continue
        price = price_of(symbol)
        if not price or price <= 0:
            skipped.append({"symbol": symbol, "reason": "no price"})
            continue
        shares = int(min(slot, room, cash) // price)
        if shares < 1:
            skipped.append({"symbol": symbol, "reason": "not enough budget or cash for one share"})
            continue
        buys.append({"symbol": symbol, "shares": shares, "limit_price": round(price, 2),
                     "judgment": judgment_of(row), "judgment_rank": row["judgment_rank"], "name": row.get("name")})
        room, cash, slots = room - shares * price, cash - shares * price, slots - 1
    return {"budget": round(budget, 2), "slot": slot, "sells": sells, "buys": buys, "skipped": skipped}


# ── execution ────────────────────────────────────────────────────────────────

def _load(path: Path):
    return json.loads(path.read_text())


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str))
    os.replace(tmp, path)


def trend_above_200(symbol: str) -> Optional[bool]:
    """True if the last close is above the 200-day average; None if it cannot be worked out."""
    try:
        import yfinance as yf
        closes = yf.Ticker(symbol).history(period="1y")["Close"].dropna().values
        if len(closes) < 200:
            return None
        return bool(closes[-1] > closes[-200:].mean())
    except Exception:
        return None


def ask_price(broker, symbol: str) -> Optional[float]:
    """The price a buy limit must meet to fill: the ask."""
    try:
        quote = broker.get_latest_quote(symbol) or {}
        ask = float(quote.get("ap", 0) or quote.get("ask_price", 0) or 0)
        return ask if ask > 0 else None
    except Exception:
        return None


def log_buy(symbol: str, shares: int, price: float, order_id: Optional[str], buy: Dict, trades_file: Path) -> None:
    """Append the sleeve's buy to the trade log, tagged so every large-cap rule leaves it alone."""
    now = datetime.now()
    trades = _load(trades_file) if trades_file.exists() else []
    trades.append({
        "trade_id": f"DMD-{now.strftime('%Y%m%d%H%M%S')}-{symbol}", "symbol": symbol, "status": "OPEN",
        "sleeve": DIAMOND_SLEEVE, "entry_date": now.strftime("%Y-%m-%d"), "entry_ts": now.isoformat(),
        "entry_price": round(price, 2), "shares": shares, "stop_loss": round(price * (1 - SLEEVE_STOP), 2),
        "stop_arm": "sleeve", "stop_dist": SLEEVE_STOP, "order_id": order_id,
        "judgment": buy.get("judgment"), "judgment_rank": buy.get("judgment_rank"),
        "exit_date": None, "exit_price": None, "exit_reason": None, "pnl_usd": None, "pnl_pct": None, "hold_days": None,
    })
    _write(trades_file, trades)


def adopt(symbol: str, pos: Dict, trades_file: Path) -> None:
    """Re-record a sleeve holding the trade log has lost (the broker's buy order says it is the sleeve's)."""
    trades = _load(trades_file) if trades_file.exists() else []
    cost = float(pos["avg_entry_price"])
    trades.append({
        "trade_id": f"DMD-ADOPTED-{datetime.now():%Y%m%d%H%M%S}-{symbol}", "symbol": symbol, "status": "OPEN",
        "sleeve": DIAMOND_SLEEVE, "adopted": True, "entry_date": date.today().isoformat(), "entry_ts": datetime.now().isoformat(),
        "entry_price": round(cost, 2), "shares": int(float(pos["qty"])), "stop_loss": round(cost * (1 - SLEEVE_STOP), 2),
        "stop_arm": "sleeve", "stop_dist": SLEEVE_STOP, "order_id": None,
        "exit_date": None, "exit_price": None, "exit_reason": None, "pnl_usd": None, "pnl_pct": None, "hold_days": None,
    })
    _write(trades_file, trades)


def run(broker, today: date, execute: bool, above_200=trend_above_200, wait_seconds: float = FILL_WAIT_SECONDS,
        list_file: Path = None, history_file: Path = None, trades_file: Path = None) -> Dict:
    """One pass of the sleeve. Returns the result document; `failures` non-empty means a human should look."""
    list_file, history_file, trades_file = list_file or LIST_FILE, history_file or HISTORY_FILE, trades_file or TRADES_FILE
    out = {"generated_at": datetime.now().isoformat(), "mode": "EXECUTE" if execute else "DRY-RUN",
           "holdings": [], "sells": [], "buys": [], "skipped": [], "failures": [], "alerts": [], "note": None}

    def stop(note: str, failure: bool = False) -> Dict:
        out["note"] = note
        if failure:
            out["failures"].append(note)
        (logger.error if failure else logger.info)(note)
        return out

    # The trade log says which positions are the sleeve's. Unreadable: do nothing.
    from exit_logic import read_sleeve_symbols, split_positions
    sleeve = read_sleeve_symbols(trades_file)
    if sleeve is None:
        return stop("trade log unreadable: the sleeve cannot tell its own positions; nothing done", failure=True)
    try:
        doc, history = _load(list_file), (_load(history_file) if history_file.exists() else [])
    except Exception as exc:
        return stop(f"no readable watchlist ({exc}); nothing done", failure=bool(sleeve))

    is_open = broker.market_clock()
    if is_open is None:
        return stop("could not read the market clock; nothing done", failure=True)
    if not is_open:
        return stop("market closed; nothing done")

    account = broker.get_account()
    positions = broker.get_positions() or []
    if not isinstance(account, dict) or "error" in account or account.get("portfolio_value") is None \
            or getattr(broker, "last_positions_ok", True) is False:
        return stop("could not read the account or its positions; nothing done", failure=True)
    equity, cash = float(account["portfolio_value"]), max(0.0, float(account.get("cash") or 0))
    longs, _ = split_positions(positions)
    by_symbol = {p["symbol"]: p for p in longs}
    trades = _load(trades_file) if trades_file.exists() else []

    # The broker's record of who bought each holding (client order ids). A
    # sleeve buy the log has lost is adopted back; a symbol both records claim
    # is left to a human.
    from exit_logic import lookup_sleeve_buys
    explained = {t.get("symbol") for t in trades if t.get("status") == "OPEN"}
    seen, unknown = lookup_sleeve_buys(broker, set(by_symbol) - explained)
    if unknown:
        return stop(f"could not read orders for {', '.join(sorted(unknown))} to tell whose they are; nothing done",
                    failure=True)
    large_cap_logged = {t.get("symbol") for t in trades if t.get("status") == "OPEN" and not t.get("sleeve")}
    for symbol in sorted(seen - sleeve):
        if symbol in large_cap_logged:
            out["alerts"].append(f"{symbol}: the broker shows a sleeve buy but the log has it as a large-cap trade; left alone")
            continue
        if execute:
            adopt(symbol, by_symbol[symbol], trades_file)
            trades = _load(trades_file)
        out["alerts"].append(f"{symbol}: a sleeve holding missing from the trade log was adopted back")
        sleeve = sleeve | {symbol}
    in_flight = {t["symbol"] for t in trades if t.get("status") == "OPEN" and t.get("sleeve") and t.get("pending_exit")}
    held = {s: float(by_symbol[s]["market_value"]) for s in sleeve if s in by_symbol}
    pending = set()
    # A buy logged but not yet filled has no position, and still holds its slot
    # and its share of the budget: otherwise a second run the same day would
    # buy it again, past eight positions and past 20%. If the order dies
    # unfilled, the daily run's reconciliation cancels the trade and frees it.
    for t in trades:
        if t.get("status") == "OPEN" and t.get("sleeve") and t.get("symbol") not in by_symbol:
            pending.add(t["symbol"])
            try:
                held[t["symbol"]] = held.get(t["symbol"], 0.0) + float(t.get("shares") or 0) * float(t.get("entry_price") or 0)
            except (TypeError, ValueError):
                held[t["symbol"]] = held.get(t["symbol"], 0.0)
    # The large-cap strategy's holdings, including a buy it has sent that has not
    # filled yet: no position at the broker, but one symbol cannot be shared.
    others = (set(by_symbol) - sleeve) | {t.get("symbol") for t in trades
                                          if t.get("status") == "OPEN" and not t.get("sleeve") and t.get("symbol")}
    out["equity"], out["holdings"] = equity, [
        {"symbol": s, "market_value": round(v, 2),
         **({"pnl_pct": round(float(by_symbol[s]["unrealized_plpc"]) * 100, 2)} if s in by_symbol else {"pending": True})}
        for s, v in sorted(held.items())]

    for row in doc.get("watchlist") or []:
        j = row.get("judgment") if isinstance(row.get("judgment"), dict) else {}
        if row.get("ticker") in held and j.get("do_not_buy") and not j.get("stale"):
            out["failures"].append(f"{row['ticker']}: its filings appear to address the model; review this holding "
                                   f"(sold automatically only if it is an unmistakable attempt, two weeks running)")

    todo = plan(doc, history, held, others, equity, cash, today, above_200, lambda s: ask_price(broker, s),
                pending=pending)
    out.update(budget=todo["budget"], slot=todo["slot"], skipped=todo["skipped"],
               invested=round(sum(held.values()), 2), list_date=_list_day(doc))
    if not execute:
        out["sells"], out["buys"] = todo["sells"], todo["buys"]
        return stop("dry run: nothing sent")

    from exit_logic import SALE_DEAD, SALE_FILLED, clear_stops_for_sale, sale_status
    import intraday_exit
    from stop_placement import place_missing_stops, stop_coverage

    # Sells first: a sale is recorded only once it has filled (the same rule as every other exit)
    for sell in todo["sells"]:
        symbol, pos = sell["symbol"], by_symbol[sell["symbol"]]
        if symbol in in_flight:
            out["sells"].append(dict(sell, status="already selling"))
            continue
        try:
            cleared, why = clear_stops_for_sale(broker, symbol)
            if not cleared:
                raise RuntimeError(why)
            result = broker.close_position(symbol)
            if "error" in result:
                raise RuntimeError(result.get("error"))
            sale = sale_status(broker, result.get("id"), wait_seconds=SELL_CONFIRM_SECONDS if wait_seconds else 0)
            pnl_pct = float(pos["unrealized_plpc"]) * 100
            if sale == SALE_FILLED:
                intraday_exit._log_exit(symbol, float(pos["current_price"]), int(float(pos["qty"])),
                                        "THESIS_BROKEN", realized_pnl_pct=pnl_pct)
                out["sells"].append(dict(sell, status="sold", pnl_pct=round(pnl_pct, 2)))
            elif sale == SALE_DEAD:
                raise RuntimeError("sell order ended without filling")
            else:
                intraday_exit.mark_pending_exit(symbol, result.get("id"), "THESIS_BROKEN", float(pos["current_price"]), pnl_pct)
                out["sells"].append(dict(sell, status="sent, not yet filled"))
                out["failures"].append(f"{symbol}: sell sent but not confirmed; tracked as a pending exit")
        except Exception as exc:
            out["sells"].append(dict(sell, status="failed", error=str(exc)))
            out["failures"].append(f"{symbol}: sell failed ({exc})")

    # Buys are worked out again now: only a sale that filled frees a slot and its budget
    sold = {s["symbol"] for s in out["sells"] if s.get("status") == "sold"}
    still_held = {k: v for k, v in held.items() if k not in sold}
    todo = plan(doc, history, still_held, others, equity, cash, today, above_200, lambda s: ask_price(broker, s),
                pending=set(still_held))
    out["skipped"] = todo["skipped"]

    # A stop on a holding tighter than its disaster level (the large-cap rule
    # caught it before its record arrived) would sell a diamond on a dip: replace it
    open_orders = broker.get_orders(status="open", limit=500) or []
    cover = stop_coverage(open_orders) if getattr(broker, "last_orders_ok", True) else {}
    for symbol in sorted(set(still_held) - pending):
        pos, trade = by_symbol.get(symbol), next((t for t in reversed(trades) if t.get("symbol") == symbol
                                                  and t.get("status") == "OPEN" and t.get("sleeve")), {})
        live = (cover.get(symbol) or {}).get("stop")
        if not pos or live is None:
            continue
        try:
            dist = float(trade.get("stop_dist") or SLEEVE_STOP)
        except (TypeError, ValueError):
            dist = SLEEVE_STOP
        level = round(float(pos["avg_entry_price"]) * (1 - dist), 2)
        if live > level + 0.01:
            from exit_logic import replace_stop
            moved = replace_stop(broker, symbol, int(float(pos["qty"])), level, previous=live)
            if moved["status"] == "replaced":
                out["alerts"].append(f"{symbol}: a stop at {live:.2f} was tighter than the sleeve's {level:.2f}; replaced")
            else:
                back = {True: "the old stop was put back", False: "the old stop could NOT be put back",
                        None: "nothing was changed"}[moved.get("restored")]
                out["failures"].append(f"{symbol}: a stop at {live:.2f} is tighter than the sleeve's {level:.2f} and "
                                       f"could not be replaced ({moved['error']}); {back}")

    # Buys: a limit at the ask, good for the day, tagged so the broker records whose it is
    sent = []
    for buy in todo["buys"]:
        try:
            result = broker.place_order(symbol=buy["symbol"], qty=buy["shares"], side="buy", order_type="limit",
                                        limit_price=buy["limit_price"], time_in_force="day",
                                        client_order_id=f"{SLEEVE_ORDER_PREFIX}{buy['symbol']}-{datetime.now():%Y%m%d%H%M%S}")
            if "error" in result:
                raise RuntimeError(result.get("error"))
            log_buy(buy["symbol"], buy["shares"], buy["limit_price"], result.get("id"), buy, trades_file)
            sent.append(result.get("id"))
            out["buys"].append(dict(buy, status="submitted", order_id=result.get("id")))
        except Exception as exc:
            out["buys"].append(dict(buy, status="failed", error=str(exc)))
            out["failures"].append(f"{buy['symbol']}: buy failed ({exc})")

    # Stops: wait briefly for the buys to fill, then the same sweep that
    # protects every position places the sleeve's wide stop on each new one.
    # A buy still unfilled gets its stop from the intraday check once it fills.
    if sent or any(s.get("status") == "failed" for s in out["sells"]):
        import time
        finished = ("filled", "canceled", "expired", "rejected", "done_for_day", "replaced")
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            if all((broker.get_order(i) or {}).get("status") in finished for i in sent):
                break
            time.sleep(3)
        sweep = place_missing_stops(broker, trades=_load(trades_file))
        out["stops_placed"] = [r["symbol"] for r in sweep.get("results", []) if r.get("status") == "placed"]
        if sweep.get("error") or sweep.get("stops_failed"):
            out["failures"].append(f"stop sweep: {sweep.get('error') or 'a stop could not be placed'}")
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run the diamond sleeve")
    parser.add_argument("--execute", action="store_true", help="send orders (default: print the plan only)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
    try:
        from alpaca_broker import AlpacaBroker
        broker = AlpacaBroker()
    except Exception as exc:
        print(f"::error::Alpaca connection failed: {exc}")
        return 1
    out = run(broker, date.today(), execute=args.execute)
    _write(OUT_FILE, out)
    print(json.dumps({k: out.get(k) for k in ("mode", "note", "budget", "invested", "sells", "buys", "skipped", "failures")},
                     indent=2, default=str))
    return 1 if out["failures"] else 0


if __name__ == "__main__":
    sys.exit(main())
