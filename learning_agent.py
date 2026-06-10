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


def closed_instrumented_trades(trades: List[Dict]) -> List[Dict]:
    """Closed trades that carry weight_version (i.e. post-instrumentation)."""
    return [
        t for t in trades
        if t.get("status") == "CLOSED"
        and t.get("weight_version") is not None
        and t.get("pnl_pct") is not None
        and t.get("rs_rank") is not None
        and t.get("thesis_score") is not None
    ]


def mean_pnl_for_version(trades: List[Dict], version: int) -> Optional[float]:
    """Mean pnl_pct of closed trades tagged with the given weight version."""
    vals = [t["pnl_pct"] for t in trades if t.get("weight_version") == version]
    if not vals:
        return None
    return sum(vals) / len(vals)


def judge_provisional(weights: Dict, trades: List[Dict]):
    """
    Evaluate a provisional weight set against its champion.

    Returns (updated_weights, action) where action is one of:
      "probation" — provisional has < PROBATION trades; no change
      "promoted"  — provisional >= champion mean pnl; becomes champion
      "reverted"  — provisional < champion mean pnl; champion restored, set locked
    """
    active = weights["active"]
    if active.get("state") != "provisional":
        return weights, "none"

    prov_version = active["version"]
    prov_trades  = [t for t in trades if t.get("weight_version") == prov_version]

    if len(prov_trades) < PROBATION:
        return weights, "probation"

    prov_mean = mean_pnl_for_version(trades, prov_version)
    champ_mean = weights["champion"].get("mean_pnl")
    if champ_mean is None or prov_mean >= champ_mean:
        weights["champion"] = {
            "version":  active["version"],
            "w_rs":     active["w_rs"],
            "w_thesis": active["w_thesis"],
            "state":    "champion",
            "mean_pnl": prov_mean,
            "n_trades": len(prov_trades),
            "promoted_at": datetime.now().isoformat(),
        }
        weights["active"] = dict(weights["champion"])
        return weights, "promoted"
    else:
        weights["rejected"].append({
            "w_rs":       active["w_rs"],
            "w_thesis":   active["w_thesis"],
            "rejected_at": datetime.now().isoformat(),
            "mean_pnl":   prov_mean,
        })
        champ = weights["champion"]
        weights["active"] = {
            "version":  champ["version"],
            "w_rs":     champ["w_rs"],
            "w_thesis": champ["w_thesis"],
            "state":    "champion",
            "applied_at": datetime.now().isoformat(),
            "derived_from_trades": champ.get("n_trades", 0),
        }
        return weights, "reverted"
