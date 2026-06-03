"""
Portfolio Sync
==============
Lightweight data refresh — updates docs/data/latest.json with live Alpaca
positions and account data every 30 minutes during market hours.

Does NOT:  generate signals, run screener, evaluate exits, place orders.
Does:      update account values, positions + P&L, market open status.
Preserves: todays_run, signals, exits, risk_assessment, performers.

Run time: ~5 seconds. Never exits with code 1 — failures are logged but
the workflow step always succeeds so [skip ci] commits still land.
"""

import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
logger = logging.getLogger(__name__)

# Repo root → allows importing alpaca_broker regardless of working directory
ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(ROOT))

DOCS_DATA    = ROOT / "docs" / "data"
LATEST_JSON  = DOCS_DATA / "latest.json"
COMPANIES_JSON = DOCS_DATA / "companies.json"


def main():
    logger.info("Portfolio sync starting")
    logger.info(f"Working dir: {os.getcwd()}")
    logger.info(f"Script root: {ROOT}")
    logger.info(f"ALPACA_API_KEY set: {bool(os.getenv('ALPACA_API_KEY'))}")
    logger.info(f"ALPACA_SECRET_KEY set: {bool(os.getenv('ALPACA_SECRET_KEY'))}")

    # ── Alpaca connection ──────────────────────────────────────────────────
    try:
        from alpaca_broker import AlpacaBroker
        broker = AlpacaBroker()
        logger.info(f"Alpaca connected: {broker.mode.value} mode")
    except Exception as e:
        logger.error(f"Alpaca connection failed: {e}")
        logger.error("Sync aborted — latest.json not updated")
        return  # exit cleanly, don't crash the workflow step

    # ── Fetch live data ────────────────────────────────────────────────────
    try:
        account     = broker.get_account()
        positions   = broker.get_positions() or []
        market_open = broker.is_market_open()
    except Exception as e:
        logger.error(f"Alpaca data fetch failed: {e}")
        return

    if "error" in account:
        logger.error(f"Account error: {account['error']}")
        return

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

    # ── Load company metadata cache ────────────────────────────────────────
    companies = {}
    if COMPANIES_JSON.exists():
        try:
            companies = json.loads(COMPANIES_JSON.read_text())
        except Exception:
            pass

    # ── Build updated positions list ───────────────────────────────────────
    updated_positions = []
    total_unrealized  = 0.0

    for p in positions:
        sym     = p["symbol"]
        mv      = float(p["market_value"])
        pnl     = float(p["unrealized_pl"])
        pnl_pct = float(p["unrealized_plpc"]) * 100
        total_unrealized += pnl

        pos = {
            "symbol":             sym,
            "qty":                int(p["qty"]),
            "avg_cost":           round(float(p["avg_entry_price"]), 2),
            "current_price":      round(float(p["current_price"]), 2),
            "market_value":       round(mv, 2),
            "unrealized_pnl":     round(pnl, 2),
            "unrealized_pnl_pct": round(pnl_pct, 2),
            "stop_loss":          None,
        }

        # Attach cached company metadata (no fresh API calls)
        if sym in companies:
            pos["company"] = dict(companies[sym])

        # Preserve 52w fields from previous run (don't change intraday)
        prev = next((x for x in existing.get("positions", []) if x.get("symbol") == sym), {})
        if prev.get("company"):
            merged = {**prev["company"], **pos.get("company", {})}
            for field in ("week_52_high", "week_52_low", "pct_from_high"):
                if field in prev["company"]:
                    merged[field] = prev["company"][field]
            pos["company"] = merged

        updated_positions.append(pos)

    # ── Build updated account block ────────────────────────────────────────
    updated_account = {
        "portfolio_value":   float(account["portfolio_value"]),
        "cash":              float(account["cash"]),
        "buying_power":      float(account["buying_power"]) * 0.95,
        "long_market_value": float(account["long_market_value"]),
        "unrealized_pnl":    round(total_unrealized, 2),
    }

    # ── Merge: update live fields, keep everything else unchanged ──────────
    updated = {
        **existing,
        "generated_at": datetime.now().isoformat(),
        "sync_at":      datetime.now().isoformat(),
        "market_open":  market_open,
        "model_status": existing.get("model_status", "healthy"),
        "account":      updated_account,
        "positions":    updated_positions,
    }

    # ── Write ──────────────────────────────────────────────────────────────
    DOCS_DATA.mkdir(parents=True, exist_ok=True)
    LATEST_JSON.write_text(json.dumps(updated, indent=2))
    logger.info(
        f"latest.json updated — {len(updated_positions)} positions, "
        f"P&L ${total_unrealized:+,.2f}"
    )


if __name__ == "__main__":
    main()
