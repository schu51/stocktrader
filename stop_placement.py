"""
Stop Placement Agent
====================
Every long stock position carries a GTC stop order in Alpaca for all its shares.
Runs at the open Mon–Fri, and place_missing_stops() is also called by the daily
run right after it buys and by every intraday exit check.

Positions that already have an active stop/stop_limit sell order are skipped.
Stop price is calculated using the same trailing stop tiers as the daily runner.
"""

import logging
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
)
logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(ROOT))

from exit_logic import untradable_reason  # noqa: E402


def build_protected_set(open_orders: List[Dict]) -> Set[str]:
    """Return set of symbols that already have an active stop sell order."""
    protected = set()
    for o in open_orders:
        if o.get("side") == "sell" and o.get("type") in ("stop", "stop_limit"):
            sym = o.get("symbol", "")
            if sym:
                protected.add(sym)
    return protected


def positions_needing_stops(
    positions: List[Dict], protected: Set[str]
) -> List[Dict]:
    """Return long stock positions that have no active stop order (shorts and options are left alone)."""
    from exit_logic import is_long_equity
    return [p for p in positions if is_long_equity(p) and p["symbol"] not in protected]


STOP_DIST_BOUNDS = (0.04, 0.15)   # same clamp as technical_signals.stop_distance


def open_trade_for(symbol: str, trades: Optional[List[Dict]]) -> Optional[Dict]:
    """The most recent OPEN trade-log entry for a symbol, if any."""
    for t in reversed(trades or []):
        if t.get("symbol") == symbol and t.get("status") == "OPEN":
            return t
    return None


def calculate_stop_for_position(pos: Dict, trade: Optional[Dict] = None) -> Tuple[float, str]:
    """
    Calculate stop price for a position using trailing stop tiers.

    If the position's trade-log entry is in the ATR arm of the stop experiment
    (experiments.py), its entry stop — the under-25%-gain tier — is its own
    volatility-sized distance instead of the fixed 8%. Higher tiers are unchanged.
    """
    from exit_logic import calculate_stop_price
    current  = float(pos["current_price"])
    avg_cost = float(pos["avg_entry_price"])
    pnl_pct  = float(pos["unrealized_plpc"]) * 100   # Alpaca returns decimal
    try:
        dist = float(trade["stop_dist"]) if trade and trade.get("stop_arm") == "atr" else None
    except (TypeError, ValueError, KeyError):
        dist = None
    if dist is not None and pnl_pct < 25 and STOP_DIST_BOUNDS[0] <= dist <= STOP_DIST_BOUNDS[1]:
        return round(avg_cost * (1 - dist), 2), f"ATR stop → {dist:.1%} below entry"
    return calculate_stop_price(current, pnl_pct, avg_cost)


def load_trades() -> List[Dict]:
    """Trade log, or an empty list if it cannot be read (stops then use the default tiers)."""
    try:
        return json.loads((ROOT / "docs" / "data" / "trades.json").read_text())
    except Exception as e:
        logger.warning(f"Could not read trade log ({e}) — using default stop tiers")
        return []


STOP_ORDER_TYPES = ("stop", "stop_limit", "trailing_stop")


def stop_coverage(open_orders: List[Dict]) -> Dict[str, Dict]:
    """Per symbol: shares covered by live sell stops, the highest stop price, and the order ids."""
    cover: Dict[str, Dict] = {}
    for o in open_orders or []:
        if o.get("side") != "sell" or o.get("type") not in STOP_ORDER_TYPES or not o.get("symbol"):
            continue
        c = cover.setdefault(o["symbol"], {"qty": 0, "stop": None, "ids": []})
        try:
            c["qty"] += int(float(o.get("qty") or 0))
        except (TypeError, ValueError):
            pass
        try:
            price = float(o.get("stop_price"))
            c["stop"] = price if c["stop"] is None else max(c["stop"], price)
        except (TypeError, ValueError):
            pass
        if o.get("id"):
            c["ids"].append(o["id"])
    return cover


def uncovered_positions(positions: List[Dict], open_orders: List[Dict], skip: Optional[Set[str]] = None) -> List[Dict]:
    """
    Long stock positions whose live stops cover fewer shares than are held.
    A position bought in several fills can end up with a stop on only the first
    fill; that counts as unprotected, not protected.
    """
    from exit_logic import is_long_equity
    cover = stop_coverage(open_orders)
    out = []
    for p in positions or []:
        if not is_long_equity(p) or p["symbol"] in (skip or ()):
            continue
        if cover.get(p["symbol"], {}).get("qty", 0) < int(float(p["qty"])):
            out.append(p)
    return out


def place_missing_stops(broker, trades: Optional[List[Dict]] = None, skip: Optional[Set[str]] = None) -> Dict:
    """
    The blanket rule: every long stock position carries a stop for all its shares.

    Called right after the daily run submits its buys, on every intraday check,
    and at the open, so a position is never left without one for longer than the
    gap between those runs. A position with a stop on only part of its shares has
    that stop replaced by one for the full quantity, never at a lower price.
    `skip`: symbols being sold in this same run.
    """
    positions   = broker.get_positions() or []
    open_orders = broker.get_orders(status="open", limit=500) or []   # every stop, not the first 50
    cover       = stop_coverage(open_orders)
    to_protect  = uncovered_positions(positions, open_orders, skip)
    trades      = load_trades() if trades is None else trades

    placed, failed, untradable, results = 0, 0, 0, []

    for pos in to_protect:
        sym = pos["symbol"]
        qty = int(float(pos["qty"]))

        # A delisted holding rejects every order: report it as stuck, not as a
        # stop that failed, so a real failure is not lost among the daily repeats.
        try:
            reason = untradable_reason(broker.get_asset(sym))
        except Exception:
            reason = None
        if reason:
            untradable += 1
            logger.warning(f"UNTRADABLE: {sym} {qty}sh — {reason}; no stop possible, close it with the broker")
            results.append({"symbol": sym, "status": "untradable", "reason": reason,
                            "market_value": pos.get("market_value")})
            continue

        try:
            stop_price, tier = calculate_stop_for_position(pos, open_trade_for(sym, trades))
            partial = cover.get(sym)
            if partial:
                # Replace a stop that covers only some of the shares; keep the higher price
                if partial["stop"] is not None and partial["stop"] > stop_price:
                    stop_price, tier = round(partial["stop"], 2), "kept existing stop price"
                for order_id in partial["ids"]:
                    broker.cancel_order(order_id)
            result = broker.place_order(
                symbol=sym,
                qty=qty,
                side="sell",
                order_type="stop",
                stop_price=stop_price,
                time_in_force="gtc",
            )
            if "error" not in result:
                placed += 1
                logger.info(f"STOP PLACED: {sym} {qty}sh @ ${stop_price:.2f} ({tier})")
                results.append({"symbol": sym, "stop": stop_price, "tier": tier, "status": "placed"})
            else:
                failed += 1
                logger.warning(f"STOP FAILED: {sym} — {result.get('error')}")
                results.append({"symbol": sym, "status": "failed", "error": result.get("error")})
        except Exception as e:
            failed += 1
            logger.error(f"Error placing stop for {sym}: {e}")
            results.append({"symbol": sym, "status": "error", "error": str(e)})

    from exit_logic import is_long_equity
    longs = [p for p in positions if is_long_equity(p)]
    return {
        "generated_at":      datetime.now().isoformat(),
        "positions_checked": len(positions),
        "already_protected": len(longs) - len(to_protect) - len([p for p in longs if p["symbol"] in (skip or ())]),
        "stops_placed":      placed,
        "stops_failed":      failed,
        "untradable":        untradable,
        "results":           results,
    }


def main():
    logger.info("=== Stop Placement Agent Starting ===")

    try:
        from alpaca_broker import AlpacaBroker
        broker = AlpacaBroker()
    except Exception as e:
        logger.error(f"Alpaca connection failed: {e}")
        sys.exit(1)

    if not broker.is_market_open():
        logger.info("Market is closed — stop placement skipped")
        return

    output = place_missing_stops(broker)
    out_path = ROOT / "docs" / "data" / "stop_placement.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, indent=2))

    logger.info(f"=== Stop Placement Complete: {output['stops_placed']} placed, "
                f"{output['stops_failed']} failed, {output['untradable']} untradable ===")


if __name__ == "__main__":
    main()
