"""
Intraday Exit Monitor
=====================
Runs every 30 minutes during market hours (offset from portfolio sync at :00/:30).
Evaluates each open position against exit triggers and closes immediately
when triggered. Also updates trailing stops when price rises above current tier.

Exit triggers (same logic as daily runner _evaluate_exits):
  HARD_LOSS_STOP    — unrealized loss exceeds 15%
  PRICE_BELOW_50MA  — current price crossed below 50-day moving average

This agent does NOT generate new entry signals — exits and stop management only.
"""

import json
import logging
import sys
from datetime import datetime, date
from pathlib import Path
from typing import Dict, List, Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
)
logger = logging.getLogger(__name__)

ROOT       = Path(__file__).parent.resolve()
DOCS_DATA  = ROOT / "docs" / "data"
TRADES_FILE = DOCS_DATA / "trades.json"

sys.path.insert(0, str(ROOT))


EXIT_CONFIRM_SECONDS = 20   # how long to wait for a market sell to report filled


def _rewrite_trades(change) -> bool:
    """Apply `change(trades)` to trades.json with an atomic write. False if it could not be done."""
    try:
        import os
        trades = json.loads(TRADES_FILE.read_text()) if TRADES_FILE.exists() else []
        change(trades)
        tmp = TRADES_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(trades, indent=2))
        os.replace(tmp, TRADES_FILE)
        return True
    except Exception as e:
        logger.error(f"Could not update trades.json: {e}")
        return False


def mark_pending_exit(symbol: str, order_id: Optional[str], trigger: str, price: float, pnl_pct: float) -> bool:
    """
    Record on the open trade that a sell has been sent but has not been seen to
    fill. The trade stays OPEN — it is not closed on a guess — and the sell is
    followed up on every later run until it settles.
    """
    def change(trades):
        for t in reversed(trades):
            if t.get("symbol") == symbol and t.get("status") == "OPEN":
                t["pending_exit"] = {"order_id": order_id, "trigger": trigger, "price": price,
                                     "pnl_pct": pnl_pct, "sent": datetime.now().isoformat()}
                return
    return _rewrite_trades(change)


PENDING_EXIT_MAX_MINUTES = 45   # a sell still unsettled after this is given up on and the exit re-evaluated


def _pending_age_minutes(pending: Dict) -> Optional[float]:
    try:
        return (datetime.now() - datetime.fromisoformat(pending["sent"])).total_seconds() / 60
    except (KeyError, TypeError, ValueError):
        return None


def settle_pending_exits(broker) -> List[Dict]:
    """
    Follow up sells that had not filled when they were last checked.
      filled   the trade is closed with the exit that triggered it
      dead     nothing sold; the mark is cleared and the position is managed as usual
      partial  the shares sold are taken off the trade, which stays open
      working  left as it is, and reported
      expired  still unsettled after PENDING_EXIT_MAX_MINUTES (or untraceable):
               the order is cancelled and the mark cleared, so the position is
               evaluated and protected again instead of being skipped for ever
    Returns one row per pending exit: {"symbol", "order_id", "status"}. A row
    whose trade-log update failed keeps status "working" and carries "error".
    """
    from exit_logic import SALE_DEAD, SALE_FILLED, SALE_PARTIAL, SALE_WORKING, sale_status
    try:
        trades = json.loads(TRADES_FILE.read_text()) if TRADES_FILE.exists() else []
    except Exception as e:
        logger.error(f"Could not read trades.json to settle pending exits: {e}")
        return [{"symbol": None, "order_id": None, "status": "working", "error": str(e)}]
    out = []
    for t in [t for t in trades if t.get("status") == "OPEN" and t.get("pending_exit")]:
        sym, pe = t["symbol"], t["pending_exit"]
        oid = pe.get("order_id")
        status = sale_status(broker, oid, wait_seconds=0)
        age = _pending_age_minutes(pe)
        if status == SALE_WORKING and (not oid or age is None or age > PENDING_EXIT_MAX_MINUTES):
            # Give up on it rather than skip this position indefinitely
            try:
                if oid:
                    broker.cancel_order(oid)
            except Exception:
                pass
            status = sale_status(broker, oid, wait_seconds=0)
            if status == SALE_WORKING:
                status = "expired"
        row = {"symbol": sym, "order_id": oid, "status": status}

        price, sold = float(pe.get("price") or 0), None
        if status in (SALE_FILLED, SALE_PARTIAL):
            try:
                order = broker.get_order(oid) or {}
                sold = int(float(order["filled_qty"]))
                price = float(order.get("filled_avg_price") or price)
            except Exception:
                sold = None
        if status == SALE_PARTIAL and not sold:
            row.update(status=SALE_WORKING, error="partly filled, quantity unreadable")   # unknown: keep tracking
            out.append(row)
            continue
        if status == SALE_WORKING:
            out.append(row)
            continue

        def change(all_trades, sym=sym, oid=oid, status=status, price=price, sold=sold, pe=pe):
            for x in reversed(all_trades):
                if x.get("symbol") == sym and x.get("status") == "OPEN" \
                        and (x.get("pending_exit") or {}).get("order_id") == oid:
                    x.pop("pending_exit", None)
                    if status == SALE_FILLED:      # closed and unmarked in the same write
                        entry = float(x.get("entry_price") or price or 0)
                        pnl_pct = pe.get("pnl_pct")
                        if pnl_pct is None:
                            pnl_pct = ((price - entry) / entry) * 100 if entry else 0
                        today = datetime.now().strftime("%Y-%m-%d")
                        try:
                            hold = (date.fromisoformat(today) - date.fromisoformat(x.get("entry_date", today))).days
                        except Exception:
                            hold = 0
                        x.update({"status": "CLOSED", "exit_date": today, "exit_ts": datetime.now().isoformat(),
                                  "exit_price": round(price, 2), "exit_reason": pe.get("trigger") or "EXIT",
                                  "pnl_pct": round(pnl_pct, 2),
                                  "pnl_usd": round((pnl_pct / 100.0) * entry * x.get("shares", sold or 0), 2),
                                  "hold_days": hold})
                    elif status == SALE_PARTIAL:
                        x["shares"] = max(0, int(x.get("shares", 0)) - sold)
                    return
            raise LookupError(f"no open trade for {sym} carries pending exit {oid}")

        if not _rewrite_trades(change):
            row.update(status=SALE_WORKING, error="trade log could not be updated")
        elif status == SALE_FILLED:
            logger.info(f"PENDING EXIT SETTLED: {sym} filled @ ${price:.2f}")
        elif status == SALE_PARTIAL:
            logger.error(f"PENDING EXIT PARTLY FILLED: {sym} sold {sold} share(s); the rest is still held")
            row["sold"] = sold
        else:
            logger.warning(f"PENDING EXIT {status.upper()}: {sym} — still held, back under normal management")
        out.append(row)
    return out


def record_broker_stops(broker, held_symbols: set) -> List[Dict]:
    """
    Close trades whose shares a broker-side order sold (exit_logic.settle_broker_exits).
    Returns one row per trade closed. Trades it cannot explain are left OPEN
    and logged; the daily run raises the alert for those.
    """
    from exit_logic import record_broker_exits
    found = {}

    def change(trades):
        found["closed"], found["unresolved"] = record_broker_exits(broker, trades, held_symbols)

    if not _rewrite_trades(change):
        return []
    for t in found.get("closed", []):
        logger.warning(f"BROKER EXIT RECORDED: {t['symbol']} {t['exit_reason']} "
                       f"@ ${t['exit_price']} | P&L: {t['pnl_pct']:+.1f}%")
    if found.get("unresolved"):
        logger.error("No longer held and no sale found: "
                     + ", ".join(sorted({t["symbol"] for t in found["unresolved"]})))
    return [{"symbol": t["symbol"], "trigger": t["exit_reason"], "price": t["exit_price"],
             "pnl_pct": t["pnl_pct"], "exit_date": t["exit_date"]} for t in found.get("closed", [])]


def _cancel_open_stops(broker, symbol: str):
    """Cancel any existing stop sell orders for a symbol."""
    try:
        orders = broker.get_orders(status="open", symbols=[symbol]) or []
        for o in orders:
            if o.get("side") == "sell" and o.get("type") in ("stop", "stop_limit", "trailing_stop"):
                broker.cancel_order(o["id"])
                logger.info(f"Cancelled stop {o['id']} for {symbol}")
    except Exception as e:
        logger.warning(f"Could not cancel stops for {symbol}: {e}")


def _log_exit(symbol: str, price: float, qty: int, trigger: str,
              realized_pnl_pct: float = None):
    """Mark the matching open trade in trades.json as CLOSED.

    realized_pnl_pct is the Alpaca-basis P&L the exit trigger fired on; when
    provided it is recorded directly instead of recomputing from the logged
    entry_price (which diverges from Alpaca's blended avg cost and would
    corrupt the pnl_pct the learning agent regresses on).
    """
    try:
        trades = json.loads(TRADES_FILE.read_text()) if TRADES_FILE.exists() else []
        today  = datetime.now().strftime("%Y-%m-%d")
        ts     = datetime.now().isoformat()
        for t in reversed(trades):
            if t.get("symbol") == symbol and t.get("status") == "OPEN":
                entry = float(t.get("entry_price", price))
                if realized_pnl_pct is not None:
                    pnl_pct = realized_pnl_pct
                    pnl_usd = (realized_pnl_pct / 100.0) * entry * t.get("shares", qty) if entry else 0
                else:
                    pnl_pct = ((price - entry) / entry) * 100 if entry else 0
                    pnl_usd = (price - entry) * t.get("shares", qty)
                entry_date = t.get("entry_date", today)
                try:
                    hold_days = (date.fromisoformat(today) - date.fromisoformat(entry_date)).days
                except Exception:
                    hold_days = 0
                t.update({
                    "status":      "CLOSED",
                    "exit_date":   today,
                    "exit_ts":     ts,
                    "exit_price":  round(price, 2),
                    "exit_reason": trigger,
                    "pnl_pct":     round(pnl_pct, 2),
                    "pnl_usd":     round(pnl_usd, 2),
                    "hold_days":   hold_days,
                })
                break
        # Atomic write: temp file + rename, so a concurrent reader never sees
        # a half-written trades.json (intraday_exit and the daily runner can
        # both touch this file).
        import os
        tmp = TRADES_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(trades, indent=2))
        os.replace(tmp, TRADES_FILE)
    except Exception as e:
        logger.warning(f"Could not log exit for {symbol}: {e}")


def evaluate_position(pos: Dict, sma50: Optional[float]) -> Optional[Dict]:
    """
    Check one position for exit triggers.
    Returns an action dict if the position should be closed, None to hold.

    sma50 is supplied by the caller (from the batched daily cache) rather than
    fetched per-position, to avoid one yfinance call per position per run.
    """
    from exit_logic import check_exit_triggers

    sym      = pos["symbol"]
    current  = float(pos["current_price"])
    avg_cost = float(pos["avg_entry_price"])
    pnl_pct  = float(pos["unrealized_plpc"]) * 100

    should_exit, trigger, reason = check_exit_triggers(
        symbol=sym,
        current_price=current,
        avg_cost=avg_cost,
        pnl_pct=pnl_pct,
        sma50=sma50,
    )

    if should_exit:
        return {
            "symbol":  sym,
            "trigger": trigger,
            "reason":  reason,
            "qty":     int(pos["qty"]),
            "price":   current,
            "pnl_pct": round(pnl_pct, 2),
        }
    return None


def _update_stop_if_better(broker, pos: Dict):
    """
    Place or raise the trailing stop for a profitable position.
    Never moves a stop downward — only updates if the new stop would be higher.
    """
    from exit_logic import calculate_stop_price

    sym      = pos["symbol"]
    current  = float(pos["current_price"])
    avg_cost = float(pos["avg_entry_price"])
    pnl_pct  = float(pos["unrealized_plpc"]) * 100
    qty      = int(pos["qty"])

    # Only manage trailing stops for positions up 25%+
    if pnl_pct < 25:
        return

    new_stop, tier = calculate_stop_price(current, pnl_pct, avg_cost)

    # Find the existing stops (a position can carry more than one, see stop_placement)
    open_orders = broker.get_orders(status="open", symbols=[sym]) or []
    if not getattr(broker, "last_orders_ok", True):
        # Unknown is not "no stop": cancelling and replacing blind could drop the one that exists
        logger.warning(f"Could not read open orders for {sym} — stop left as it is")
        return {"symbol": sym, "status": "failed", "error": "could not read open orders"}
    from stop_placement import stop_coverage
    cover         = stop_coverage(open_orders).get(sym) or {"qty": 0, "stop": None, "ids": []}
    existing_stop = cover["stop"]

    # Only update if new stop is strictly higher (never lower a stop)
    if existing_stop is None or new_stop > existing_stop:
        for order_id in cover["ids"]:
            broker.cancel_order(order_id)
        result = broker.place_order(
            symbol=sym, qty=qty, side="sell",
            order_type="stop", stop_price=new_stop, time_in_force="gtc",
        )
        if "error" not in result:
            logger.info(f"STOP RAISED: {sym} → ${new_stop:.2f} ({tier}) | P&L: {pnl_pct:+.1f}%")
            return {"symbol": sym, "new_stop": new_stop, "tier": tier, "pnl_pct": round(pnl_pct, 2)}
        logger.warning(f"Stop update failed for {sym}: {result.get('error')}")
        # The old stop is already cancelled. Put it back rather than leave the
        # position with none; the sweep in main() is the second line of defence.
        restored = False
        if existing_stop is not None:
            back = broker.place_order(symbol=sym, qty=qty, side="sell", order_type="stop",
                                      stop_price=existing_stop, time_in_force="gtc")
            restored = "error" not in back
            if not restored:
                logger.error(f"{sym}: could not restore the previous stop at ${existing_stop:.2f}: {back.get('error')}")
        return {"symbol": sym, "status": "failed", "error": result.get("error"), "restored": restored}
    return None


def main():
    logger.info("=== Intraday Exit Monitor Starting ===")

    try:
        from alpaca_broker import AlpacaBroker
        broker = AlpacaBroker()
    except Exception as e:
        logger.error(f"Alpaca connection failed: {e}")
        sys.exit(1)

    is_open = broker.market_clock()
    if is_open is None:
        # "Could not ask" is not "closed": skipping quietly would leave exits and stops unchecked
        logger.error("Could not read the market clock from Alpaca — intraday exit monitor did not run")
        sys.exit(1)
    if not is_open:
        logger.info("Market closed — intraday exit monitor skipped")
        return

    from exit_logic import SALE_DEAD, SALE_FILLED, sale_status, split_positions
    held_now = broker.get_positions() or []
    positions_ok = getattr(broker, "last_positions_ok", False) is True
    positions, unmanaged = split_positions(held_now)
    logger.info(f"Evaluating {len(positions)} long stock positions")
    if unmanaged:
        logger.info(f"Leaving {len(unmanaged)} short/option position(s) alone: "
                    f"{[p['symbol'] for p in unmanaged]}")

    # Build 50MA for all held symbols in ONE batched call (cached for the day)
    from exit_logic import get_sma50_map
    sma_map = get_sma50_map([p["symbol"] for p in positions])

    exits_triggered = []
    stops_updated   = []
    stop_failures   = []   # an exit or stop that could not be carried out; fails the run

    # Sells from an earlier run that had not filled when it finished
    pending = settle_pending_exits(broker)
    in_flight = {p["symbol"] for p in pending if p["status"] == "working" and p.get("symbol")}
    for p in pending:
        if p["status"] in ("working", "partial", "expired"):
            stop_failures.append({"symbol": p["symbol"], "status": f"exit_{p['status']}",
                                  "error": p.get("error") or f"sell order {p['order_id']} has not fully filled"})

    # Stops that filled at the broker since the last run: nothing else records them
    broker_exits = record_broker_stops(broker, {p["symbol"] for p in held_now}) if positions_ok else []

    for pos in positions:
        sym = pos["symbol"]
        if sym in in_flight:
            continue   # its sell is still working; a second one would only be rejected

        # --- Exit evaluation ---
        action = evaluate_position(pos, sma_map.get(sym))
        if action:
            logger.warning(
                f"EXIT TRIGGERED: {sym} — {action['trigger']} | {action['reason']} "
                f"| P&L: {action['pnl_pct']:+.1f}%"
            )
            _cancel_open_stops(broker, sym)
            result = broker.close_position(sym)
            action["executed"] = "error" not in result
            action["order_id"] = result.get("id")
            # An accepted sell is not a sale. The trade is recorded closed only
            # once the order has filled. A sell that has not filled yet is left
            # working — cancelling it would undo the exit — and is tracked on the
            # trade until a later run sees how it ended.
            sale = sale_status(broker, action["order_id"], wait_seconds=EXIT_CONFIRM_SECONDS) \
                if action["executed"] else SALE_DEAD
            action["confirmed"] = sale == SALE_FILLED
            action["sale"] = sale
            if sale == SALE_FILLED:
                _log_exit(sym, action["price"], action["qty"], action["trigger"],
                          realized_pnl_pct=action["pnl_pct"])
            elif sale == SALE_DEAD:
                # Still held, and its stops were just cancelled. The sweep below
                # puts a stop back; the failed exit itself fails the run.
                why = result.get("error") or "sell order ended without filling"
                logger.error(f"EXIT FAILED: {sym} — {why}")
                stop_failures.append({"symbol": sym, "status": "exit_failed", "error": why})
            else:
                logger.error(f"EXIT NOT CONFIRMED: {sym} — sell order {action['order_id']} is still working")
                tracked = mark_pending_exit(sym, action["order_id"], action["trigger"],
                                            action["price"], action["pnl_pct"])
                in_flight.add(sym)
                stop_failures.append({"symbol": sym, "status": "exit_unconfirmed",
                                      "error": "sell order accepted but not yet filled" + (
                                          "" if tracked else "; AND it could not be recorded on the trade — "
                                          "check this position by hand")})
            exits_triggered.append(action)
            continue  # Skip stop update for exited position

        # --- Trailing stop update (profitable positions only) ---
        stop_update = _update_stop_if_better(broker, pos)
        if stop_update and stop_update.get("status") == "failed":
            stop_failures.append(stop_update)
        elif stop_update:
            stops_updated.append(stop_update)

    # Blanket rule: no long position without a stop for all its shares. Catches a
    # buy that filled after the daily run finished, or only partly at the time.
    stops_placed = []
    try:
        from stop_placement import place_missing_stops
        # Skip what was sold and what has a sell still working (its shares are
        # reserved). A sell that ended unfilled is still held and needs its stop back.
        sweep = place_missing_stops(
            broker, skip={a["symbol"] for a in exits_triggered if a.get("confirmed")} | in_flight)
        stops_placed = [r for r in sweep["results"] if r.get("status") == "placed"]
        stop_failures += [r for r in sweep["results"] if r.get("status") in ("failed", "error")]
        if sweep.get("error"):
            stop_failures.append({"status": "error", "error": sweep["error"]})
    except Exception as e:
        logger.error(f"Stop sweep failed: {e}")
        stop_failures.append({"status": "error", "error": str(e)})

    output = {
        "generated_at":     datetime.now().isoformat(),
        "positions_checked": len(positions),
        "exits_triggered":   exits_triggered,
        "stops_updated":     stops_updated,
        "stops_placed":      stops_placed,
        "stop_failures":     stop_failures,
        "broker_exits":      broker_exits,
        "mode":              "EXECUTE",
    }
    out_path = DOCS_DATA / "intraday_exit.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, indent=2))

    logger.info(
        f"=== Intraday Exit Complete: {len(exits_triggered)} exits, "
        f"{len(stops_updated)} stops updated, {len(stops_placed)} placed, {len(stop_failures)} stop failures ==="
    )
    # A position that may be without a stop must reach a human: fail the workflow
    # (after the result file is written, so the commit step still records it).
    if stop_failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
