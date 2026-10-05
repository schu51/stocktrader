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

    from exit_logic import confirm_sale, split_positions
    positions, unmanaged = split_positions(broker.get_positions() or [])
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

    for pos in positions:
        sym = pos["symbol"]

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
            # once the order has filled; one that does not fill is cancelled.
            action["confirmed"] = action["executed"] and confirm_sale(
                broker, action["order_id"], wait_seconds=EXIT_CONFIRM_SECONDS)
            if action["confirmed"]:
                _log_exit(sym, action["price"], action["qty"], action["trigger"],
                          realized_pnl_pct=action["pnl_pct"])
            else:
                # Still held, and its stops were just cancelled. The sweep below
                # puts a stop back; the failed exit itself fails the run.
                why = result.get("error") if not action["executed"] else "sell order accepted but not filled"
                logger.error(f"EXIT FAILED: {sym} — {why}")
                stop_failures.append({"symbol": sym, "error": why,
                                      "status": "exit_unconfirmed" if action["executed"] else "exit_failed"})
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
        # Skip only what was actually sold: a failed or unfilled exit is still held and needs its stop back
        sweep = place_missing_stops(broker, skip={a["symbol"] for a in exits_triggered if a.get("confirmed")})
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
