"""
Learning Agent
==============
Weekly self-tuning agent for the screener's ranking weights.

Reads closed, fully-instrumented trades from trades.json, regresses realized
pnl_pct on standardized entry features (rs_rank, thesis_score), and writes
versioned weights to weights.json. Champion/provisional states plus a 10-trade
probation window provide auto-rollback.

Guardrails:
  - sample-size gate: >= 30 closed instrumented trades
  - significance gate: both coefficients' |t| > 2.05
  - auto-rollback: a provisional weight underperforming the champion over its
    first 10 trades is reverted and locked
  - unbounded steps (per user decision) — rollback carries the safety load

The agent never edits source code — only weights.json.
"""

import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
logger = logging.getLogger(__name__)

ROOT        = Path(__file__).parent.resolve()
DOCS_DATA   = ROOT / "docs" / "data"
WEIGHTS_FILE = DOCS_DATA / "weights.json"
TRADES_FILE  = DOCS_DATA / "trades.json"
REPORT_FILE  = DOCS_DATA / "learning_report.json"

MIN_SAMPLE      = 30     # closed instrumented trades before any change
PROBATION       = 10     # provisional trades before judgment
LOCK_TOLERANCE  = 0.03   # derived weights within this of a rejected set are skipped


def default_weights() -> Dict:
    return {
        "active":   {"version": 1, "w_rs": 0.60, "w_thesis": 0.40,
                     "state": "champion", "applied_at": None,
                     "derived_from_trades": 0},
        "champion": {"version": 1, "w_rs": 0.60, "w_thesis": 0.40,
                     "state": "champion", "mean_pnl": None, "n_trades": 0},
        "rejected": [],
        "history":  [{"version": 1, "w_rs": 0.60, "w_thesis": 0.40, "note": "seed default"}],
    }


def load_weights(path: Path = WEIGHTS_FILE) -> Dict:
    """Load weights.json, or return the seed default if absent/malformed."""
    try:
        if path.exists():
            data = json.loads(path.read_text())
            if "active" in data and "w_rs" in data["active"]:
                return data
    except Exception as e:
        logger.warning(f"Could not read weights.json ({e}) — using default")
    return default_weights()


def save_weights(path: Path, data: Dict):
    """Write weights.json atomically (temp + rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, path)


def is_locked(w_rs: float, w_thesis: float, rejected: List[Dict]) -> bool:
    """True if (w_rs, w_thesis) is within LOCK_TOLERANCE of any rejected set."""
    for r in rejected:
        if (abs(w_rs - r["w_rs"]) <= LOCK_TOLERANCE
                and abs(w_thesis - r["w_thesis"]) <= LOCK_TOLERANCE):
            return True
    return False
