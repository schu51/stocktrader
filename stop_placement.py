"""
Stop Placement Agent
====================
Runs at 9:31 AM EDT Mon–Fri (1 minute after market open).
Places a GTC stop order in Alpaca for every open position that has none.

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

    positions   = broker.get_positions() or []
    open_orders = broker.get_orders(status="open") or []

    protected  = build_protected_set(open_orders)
    to_protect = positions_needing_stops(positions, protected)

    logger.info(
        f"Positions: {len(positions)} total, "
        f"{len(protected)} already protected, "
        f"{len(to_protect)} need stops"
    )

    placed  = 0
    failed  = 0
    untradable = 0
    results = []
    trades  = load_trades()

    for pos in to_protect:
        sym = pos["symbol"]
        qty = int(pos["qty"])

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

    output = {
        "generated_at":     datetime.now().isoformat(),
        "positions_checked": len(positions),
        "already_protected": len(protected),
        "stops_placed":      placed,
        "stops_failed":      failed,
        "untradable":        untradable,
        "results":           results,
    }
    out_path = ROOT / "docs" / "data" / "stop_placement.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, indent=2))

    logger.info(f"=== Stop Placement Complete: {placed} placed, {failed} failed, {untradable} untradable ===")


if __name__ == "__main__":
    main()
