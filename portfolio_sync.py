"""
Portfolio Sync
==============
Lightweight data refresh — updates docs/data/latest.json with live Alpaca
positions and account data every 30 minutes during market hours.

Does NOT:  generate signals, run screener, evaluate exits, place orders.
Does:      update account values, positions + P&L, market open status.
Preserves: todays_run, signals, exits, risk_assessment, performers.

Run time: ~5 seconds.
"""

import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
logger = logging.getLogger(__name__)

DOCS_DATA = Path(__file__).parent / "docs" / "data"
LATEST_JSON = DOCS_DATA / "latest.json"
COMPANIES_JSON = DOCS_DATA / "companies.json"


def main():
    # ── Alpaca connection ──────────────────────────────────────────────────
    sys.path.insert(0, str(Path(__file__).parent))
    try:
        from alpaca_broker import AlpacaBroker
        broker = AlpacaBroker()
    except Exception as e:
        logger.error(f"Alpaca connection failed: {e}")
        sys.exit(1)

    # ── Fetch live data ────────────────────────────────────────────────────
    account   = broker.get_account()
    positions = broker.get_positions() or []
    market_open = broker.is_market_open()

    if "error" in account:
        logger.error(f"Account fetch failed: {account['error']}")
        sys.exit(1)

    logger.info(
        f"Fetched: portfolio=${account['portfolio_value']:,.2f} "
        f"| {len(positions)} positions "
        f"| market={'OPEN' if market_open else 'CLOSED'}"
    )

    # ── Load existing latest.json (preserve signals, exits, etc.) ─────────
    existing = {}
    if LATEST_JSON.exists():
        try:
            existing = json.loads(LATEST_JSON.read_text())
        except Exception as e:
            logger.warning(f"Could not read latest.json: {e}")

    # ── Load company metadata cache (no API calls needed) ─────────────────
    companies = {}
    if COMPANIES_JSON.exists():
        try:
            companies = json.loads(COMPANIES_JSON.read_text())
        except Exception:
            pass

    # ── Build updated positions list ───────────────────────────────────────
    updated_positions = []
    total_unrealized = 0.0

    for p in positions:
        sym     = p["symbol"]
        mv      = float(p["market_value"])
        pnl     = float(p["unrealized_pl"])
        pnl_pct = float(p["unrealized_plpc"]) * 100
        total_unrealized += pnl

        pos = {
            "symbol":           sym,
            "qty":              int(p["qty"]),
            "avg_cost":         round(float(p["avg_entry_price"]), 2),
            "current_price":    round(float(p["current_price"]), 2),
            "market_value":     round(mv, 2),
            "unrealized_pnl":   round(pnl, 2),
            "unrealized_pnl_pct": round(pnl_pct, 2),
            "stop_loss":        None,
        }

        # Attach cached company metadata (no fresh fetch)
        if sym in companies:
            pos["company"] = companies[sym]

        # Preserve week_52 data from previous run if available
        prev = next((x for x in existing.get("positions", []) if x.get("symbol") == sym), {})
        if prev.get("company"):
            pos["company"] = {**prev["company"], **pos.get("company", {})}
            # Preserve 52w fields which don't change intraday
            for field in ("week_52_high", "week_52_low", "pct_from_high"):
                if field in prev["company"] and field not in pos.get("company", {}):
                    pos.setdefault("company", {})[field] = prev["company"][field]

        updated_positions.append(pos)

    # ── Build updated account block ────────────────────────────────────────
    updated_account = {
        "portfolio_value":   float(account["portfolio_value"]),
        "cash":              float(account["cash"]),
        "buying_power":      float(account["buying_power"]) * 0.95,
        "long_market_value": float(account["long_market_value"]),
        "unrealized_pnl":    round(total_unrealized, 2),
    }

    # ── Merge: update only live fields, keep everything else ──────────────
    updated = {
        **existing,                              # preserve signals, exits, performers, etc.
        "generated_at":  datetime.now().isoformat(),
        "sync_at":       datetime.now().isoformat(),
        "market_open":   market_open,
        "model_status":  existing.get("model_status", "healthy"),
        "account":       updated_account,
        "positions":     updated_positions,
    }

    # ── Write ──────────────────────────────────────────────────────────────
    DOCS_DATA.mkdir(parents=True, exist_ok=True)
    LATEST_JSON.write_text(json.dumps(updated, indent=2))
    logger.info(f"latest.json updated — {len(updated_positions)} positions, P&L ${total_unrealized:+,.2f}")


if __name__ == "__main__":
    main()
