"""
Post-Market Reconciler
======================
Runs at 4:15 PM EDT (20:15 UTC) Mon–Fri — 15 minutes after market close.
Reads the day's run data and produces a structured reconciliation report.

Writes docs/data/postmarket.json with:
  - Portfolio summary (value, P&L, winners/losers, risk level)
  - Execution summary (signals generated, executed, skipped with reasons)
  - Exit summary (what fired today)
  - Positions at risk (deep losses, unprotected positions)
  - Closed trades today

Does NOT place orders or modify positions.
"""

import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
)
logger = logging.getLogger(__name__)

ROOT      = Path(__file__).parent.resolve()
DOCS_DATA = ROOT / "docs" / "data"


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text()) if path.exists() else {}
    except Exception as e:
        logger.warning(f"Could not read {path}: {e}")
        return {}


def reconcile(latest: dict, trades: list) -> dict:
    """Build the reconciliation report from today's run data."""
    acct      = latest.get("account", {})
    run       = latest.get("todays_run", {})
    exits     = latest.get("exits", {})
    positions = latest.get("positions", [])
    risk      = latest.get("risk_assessment", {})

    # Portfolio summary
    total_unrealized = sum(p.get("unrealized_pnl", 0) for p in positions)
    winners = [p for p in positions if p.get("unrealized_pnl_pct", 0) > 0]
    losers  = [p for p in positions if p.get("unrealized_pnl_pct", 0) < 0]

    # Execution summary
    signals      = run.get("signals", [])
    exec_details = run.get("execution_details", [])
    skipped      = [d for d in exec_details if d.get("status") == "skipped"]
    executed     = [s for s in signals if s.get("executed")]

    # Positions at risk: deep loss OR missing stop protection
    at_risk = [
        {
            "symbol":   p["symbol"],
            "pnl_pct":  p.get("unrealized_pnl_pct", 0),
            "value":    p.get("market_value", 0),
            "has_stop": p.get("stop_loss") is not None,
            "risk":     (
                "deep_loss"        if p.get("unrealized_pnl_pct", 0) < -8
                else "no_stop"     if not p.get("stop_loss")
                else "approaching" if p.get("unrealized_pnl_pct", 0) < -5
                else "ok"
            ),
        }
        for p in sorted(positions, key=lambda x: x.get("unrealized_pnl_pct", 0))
        if p.get("unrealized_pnl_pct", 0) < -5 or not p.get("stop_loss")
    ]

    # Closed trades today
    today = datetime.now().strftime("%Y-%m-%d")
    closed_today = [
        t for t in trades
        if t.get("status") == "CLOSED" and t.get("exit_date") == today
    ]

    return {
        "date":         today,
        "generated_at": datetime.now().isoformat(),
        "portfolio": {
            "value":            acct.get("portfolio_value", 0),
            "cash":             acct.get("cash", 0),
            "total_unrealized": total_unrealized,
            "positions":        len(positions),
            "winners":          len(winners),
            "losers":           len(losers),
            "risk_level":       risk.get("risk_level", "UNKNOWN"),
            "risk_score":       risk.get("overall_risk_score", 0),
        },
        "execution": {
            "signals_generated":   len(signals),
            "orders_executed":     len(executed),
            "orders_skipped":      len(skipped),
            "skip_reasons":        [s.get("reason", "") for s in skipped],
            "candidates_screened": run.get("candidates_screened", 0),
        },
        "exits": {
            "triggered":     len(exits.get("exits_triggered", [])),
            "stops_updated": len(exits.get("stops_updated", [])),
            "detail":        exits.get("exits_triggered", []),
        },
        "closed_today":      closed_today,
        "positions_at_risk": at_risk,
    }


def main():
    logger.info("=== Post-Market Reconciler Starting ===")

    latest = load_json(DOCS_DATA / "latest.json")
    trades_path = DOCS_DATA / "trades.json"
    trades = json.loads(trades_path.read_text()) if trades_path.exists() else []

    if not latest:
        logger.error("latest.json not found or empty — cannot reconcile")
        sys.exit(1)

    report = reconcile(latest, trades)

    out_path = DOCS_DATA / "postmarket.json"
    out_path.write_text(json.dumps(report, indent=2))

    logger.info(
        f"Portfolio: ${report['portfolio']['value']:,.2f} | "
        f"Winners: {report['portfolio']['winners']} | "
        f"Losers: {report['portfolio']['losers']} | "
        f"Risk: {report['portfolio']['risk_level']}"
    )
    logger.info(
        f"Execution: {report['execution']['signals_generated']} signals, "
        f"{report['execution']['orders_executed']} executed, "
        f"{report['execution']['orders_skipped']} skipped"
    )
    if report["positions_at_risk"]:
        logger.warning(
            "Positions at risk: "
            + ", ".join(
                f"{p['symbol']} ({p['pnl_pct']:+.1f}%)"
                for p in report["positions_at_risk"][:5]
            )
        )
    if report["closed_today"]:
        for t in report["closed_today"]:
            logger.info(
                f"CLOSED TODAY: {t['symbol']} | "
                f"P&L: {t.get('pnl_pct', 0):+.1f}% | "
                f"Reason: {t.get('exit_reason', 'UNKNOWN')}"
            )

    logger.info(f"Report written to {out_path}")
    logger.info("=== Post-Market Reconciler Complete ===")


if __name__ == "__main__":
    main()
