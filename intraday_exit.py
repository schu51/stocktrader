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


def _log_exit(symbol: str, price: float, qty: int, trigger: str):
    """Mark the matching open trade in trades.json as CLOSED."""
    try:
        trades = json.loads(TRADES_FILE.read_text()) if TRADES_FILE.exists() else []
        today  = datetime.now().strftime("%Y-%m-%d")
        ts     = datetime.now().isoformat()
        for t in reversed(trades):
            if t.get("symbol") == symbol and t.get("status") == "OPEN":
                entry = float(t.get("entry_price", price))
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
        TRADES_FILE.write_text(json.dumps(trades, indent=2))
    except Exception as e:
        logger.warning(f"Could not log exit for {symbol}: {e}")


def evaluate_position(pos: Dict) -> Optional[Dict]:
    """
    Check one position for exit triggers.
    Returns an action dict if the position should be closed, None to hold.
    """
    from exit_logic import check_exit_triggers, fetch_sma50

    sym      = pos["symbol"]
    current  = float(pos["current_price"])
    avg_cost = float(pos["avg_entry_price"])
    pnl_pct  = float(pos["unrealized_plpc"]) * 100

    sma50 = fetch_sma50(sym)

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

    # Find the existing stop price (if any)
    open_orders    = broker.get_orders(status="open", symbols=[sym]) or []
    existing_stop  = None
    existing_stop_id = None
    for o in open_orders:
        if o.get("side") == "sell" and o.get("type") in ("stop", "stop_limit"):
            try:
                existing_stop    = float(o.get("stop_price", 0))
                existing_stop_id = o["id"]
            except (TypeError, ValueError):
                pass

    # Only update if new stop is strictly higher (never lower a stop)
    if existing_stop is None or new_stop > existing_stop:
        if existing_stop_id:
            broker.cancel_order(existing_stop_id)
        result = broker.place_order(
            symbol=sym, qty=qty, side="sell",
            order_type="stop", stop_price=new_stop, time_in_force="gtc",
        )
        if "error" not in result:
            logger.info(f"STOP RAISED: {sym} → ${new_stop:.2f} ({tier}) | P&L: {pnl_pct:+.1f}%")
            return {"symbol": sym, "new_stop": new_stop, "tier": tier, "pnl_pct": round(pnl_pct, 2)}
        else:
            logger.warning(f"Stop update failed for {sym}: {result.get('error')}")
    return None


def main():
    logger.info("=== Intraday Exit Monitor Starting ===")

    try:
        from alpaca_broker import AlpacaBroker
        broker = AlpacaBroker()
    except Exception as e:
        logger.error(f"Alpaca connection failed: {e}")
        sys.exit(1)

    if not broker.is_market_open():
        logger.info("Market closed — intraday exit monitor skipped")
        return

    positions = broker.get_positions() or []
    logger.info(f"Evaluating {len(positions)} positions")

    exits_triggered = []
    stops_updated   = []

    for pos in positions:
        sym = pos["symbol"]

        # --- Exit evaluation ---
        action = evaluate_position(pos)
        if action:
            logger.warning(
                f"EXIT TRIGGERED: {sym} — {action['trigger']} | {action['reason']} "
                f"| P&L: {action['pnl_pct']:+.1f}%"
            )
            _cancel_open_stops(broker, sym)
            result = broker.close_position(sym)
            action["executed"] = "error" not in result
            action["order_id"] = result.get("id")
            if action["executed"]:
                _log_exit(sym, action["price"], action["qty"], action["trigger"])
            exits_triggered.append(action)
            continue  # Skip stop update for exited position

        # --- Trailing stop update (profitable positions only) ---
        stop_update = _update_stop_if_better(broker, pos)
        if stop_update:
            stops_updated.append(stop_update)

    output = {
        "generated_at":     datetime.now().isoformat(),
        "positions_checked": len(positions),
        "exits_triggered":   exits_triggered,
        "stops_updated":     stops_updated,
        "mode":              "EXECUTE",
    }
    out_path = DOCS_DATA / "intraday_exit.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, indent=2))

    logger.info(
        f"=== Intraday Exit Complete: {len(exits_triggered)} exits, "
        f"{len(stops_updated)} stops updated ==="
    )


if __name__ == "__main__":
    main()
