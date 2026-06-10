# Learning Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a weekly self-tuning agent that adjusts the screener's ranking weights (`w_rs / w_thesis`) based on regression of realized trade outcomes against entry features, bounded by sample-size, significance, and auto-rollback guardrails.

**Architecture:** A standalone `learning_agent.py` reads closed trades from `trades.json`, runs a pure-numpy OLS regression of `pnl_pct` on standardized entry features, and writes versioned weights to `weights.json` which the screener reads at runtime. Champion/provisional states plus a 10-trade probation window provide auto-rollback. The agent never edits source code — only the `weights.json` data file.

**Tech Stack:** Python 3.12, numpy (already a dependency), pytest, GitHub Actions cron. No scipy/statsmodels.

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `learning_stats.py` | Pure functions: OLS regression, significance test, weight derivation. No I/O. |
| Create | `learning_agent.py` | Orchestration: load trades, run gates, manage champion/provisional, write weights.json. |
| Create | `tests/test_learning_stats.py` | Unit tests for the pure stat functions. |
| Create | `tests/test_learning_agent.py` | Unit tests for gates, rollback, lock, fallback. |
| Create | `.github/workflows/learning.yml` | Weekly Saturday 12:00 UTC trigger. |
| Modify | `run_daily_analysis.py` | Phase 0: merge entry features into opp dict + extend `_log_trade`. |
| Modify | `screener.py` | Read `weights.json` for the ranking blend; fall back to 0.60/0.40. |

**Why split `learning_stats.py` from `learning_agent.py`:** the statistics are pure
functions (data in, numbers out) and must be unit-tested with synthetic data where
the true relationship is known. The agent is I/O and state management. Keeping them
separate means the math can be verified in isolation.

---

## Task 1: Phase 0 — Instrument the Entry Log

**Files:**
- Modify: `run_daily_analysis.py` (candidate loop ~437, `_log_trade` ~977, BUY call site ~1609)

The opp dict built by `_decision_to_dict` does NOT carry `rs_rank`, `thesis_score`,
`thesis_grade`, or `sector`. These live in the `candidate` dict (from screener.json).
We merge them into `decision_dict` while `candidate` is in scope, then pass them
through `_log_trade`.

- [ ] **Step 1: Merge entry features into the opportunity dict**

In `run_daily_analysis.py`, find (around line 437-440):

```python
                    decision_dict = self._decision_to_dict(decision)

                    # Apply momentum score scaling to position size
                    decision_dict = self._scale_size_by_momentum(decision_dict)
```

Replace with:

```python
                    decision_dict = self._decision_to_dict(decision)

                    # Carry screener entry features through for trade logging
                    # (the learning agent regresses outcomes on these).
                    decision_dict["rs_rank"]      = candidate.get("rs_rank")
                    decision_dict["thesis_score"] = candidate.get("thesis_score")
                    decision_dict["thesis_grade"] = candidate.get("thesis_grade")
                    decision_dict["sector"]       = candidate.get("sector")

                    # Apply momentum score scaling to position size
                    decision_dict = self._scale_size_by_momentum(decision_dict)
```

- [ ] **Step 2: Extend the `_log_trade` signature**

Find (line 977-980):

```python
    def _log_trade(self, action: str, symbol: str, shares: int, price: float,
                   stop_loss: float = None, trend_score: int = None,
                   confidence: float = None, exit_reason: str = None,
                   trade_id: str = None):
```

Replace with:

```python
    def _log_trade(self, action: str, symbol: str, shares: int, price: float,
                   stop_loss: float = None, trend_score: int = None,
                   confidence: float = None, exit_reason: str = None,
                   trade_id: str = None, rs_rank: int = None,
                   thesis_score: float = None, thesis_grade: str = None,
                   sector: str = None, weight_version: int = None):
```

- [ ] **Step 3: Store the new fields in the BUY trade record**

Find the `trades.append({` block at line 1006 and locate these lines:

```python
                    "trend_score": trend_score,
                    "confidence":  confidence,
```

Replace with:

```python
                    "trend_score":    trend_score,
                    "confidence":     confidence,
                    "rs_rank":        rs_rank,
                    "thesis_score":   thesis_score,
                    "thesis_grade":   thesis_grade,
                    "sector":         sector,
                    "weight_version": weight_version,
```

- [ ] **Step 4: Pass the features at the BUY call site**

Find (line 1609-1617):

```python
                    self._log_trade(
                        action="BUY",
                        symbol=symbol,
                        shares=shares,
                        price=limit_price,
                        stop_loss=opp.get("stop_loss"),
                        trend_score=opp.get("trend_score"),
                        confidence=confidence,
                    )
```

Replace with:

```python
                    self._log_trade(
                        action="BUY",
                        symbol=symbol,
                        shares=shares,
                        price=limit_price,
                        stop_loss=opp.get("stop_loss"),
                        trend_score=opp.get("trend_score"),
                        confidence=confidence,
                        rs_rank=opp.get("rs_rank"),
                        thesis_score=opp.get("thesis_score"),
                        thesis_grade=opp.get("thesis_grade"),
                        sector=opp.get("sector"),
                        weight_version=self._active_weight_version(),
                    )
```

- [ ] **Step 5: Add the `_active_weight_version` helper**

Add this method to the `DailyRunner` class, right before `_log_trade` (before line 977):

```python
    def _active_weight_version(self) -> int:
        """Read the active ranking-weight version for trade tagging. Defaults to 1."""
        try:
            wf = Path(__file__).parent / "docs" / "data" / "weights.json"
            if wf.exists():
                return int(json.loads(wf.read_text()).get("active", {}).get("version", 1))
        except Exception:
            pass
        return 1
```

- [ ] **Step 6: Verify it compiles and runs dry**

```bash
cd /Users/alexschumacher/stocktrader && python3 -m py_compile run_daily_analysis.py && echo "OK"
```
Expected: `OK`

- [ ] **Step 7: Commit**

```bash
git add run_daily_analysis.py
git commit -m "Phase 0: instrument entry log with rs_rank, thesis_score, grade, sector, weight_version"
```

---

## Task 2: Pure-Numpy OLS Regression

**Files:**
- Create: `learning_stats.py`
- Create: `tests/test_learning_stats.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_learning_stats.py`:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np


def test_ols_recovers_known_slope():
    # y = 2*x1 + 0*x2 + noise; expect coef[x1] strong, coef[x2] ~ 0
    from learning_stats import ols_fit
    rng = np.random.default_rng(0)
    n = 200
    x1 = rng.normal(0, 1, n)
    x2 = rng.normal(0, 1, n)
    y = 2.0 * x1 + rng.normal(0, 0.5, n)
    X = np.column_stack([x1, x2])
    result = ols_fit(X, y)
    # coefficients exclude the intercept; index 0 = x1, 1 = x2
    assert result["coef"][0] > 1.5
    assert abs(result["coef"][1]) < 0.5
    # x1 significant, x2 not
    assert abs(result["t"][0]) > 2.05
    assert abs(result["t"][1]) < 2.05


def test_ols_handles_intercept():
    from learning_stats import ols_fit
    rng = np.random.default_rng(1)
    n = 100
    x = rng.normal(0, 1, n)
    y = 5.0 + 3.0 * x + rng.normal(0, 0.3, n)
    X = x.reshape(-1, 1)
    result = ols_fit(X, y)
    assert result["coef"][0] > 2.5    # slope ~3
    assert result["intercept"] > 4.0  # intercept ~5


def test_zscore_standardizes():
    from learning_stats import zscore
    x = np.array([10.0, 20.0, 30.0, 40.0])
    z = zscore(x)
    assert abs(z.mean()) < 1e-9
    assert abs(z.std() - 1.0) < 1e-9


def test_zscore_constant_column():
    # A constant feature has zero variance — must not divide by zero
    from learning_stats import zscore
    x = np.array([5.0, 5.0, 5.0])
    z = zscore(x)
    assert np.all(z == 0.0)


def test_derive_weights_both_significant():
    from learning_stats import derive_weights
    # both significant: coefficients 0.8 and 0.4 → 0.67 / 0.33
    w_rs, w_thesis = derive_weights(coef_rs=0.8, t_rs=3.0, coef_thesis=0.4, t_thesis=2.5)
    assert abs(w_rs - 0.667) < 0.01
    assert abs(w_thesis - 0.333) < 0.01
    assert abs((w_rs + w_thesis) - 1.0) < 1e-9


def test_derive_weights_one_significant_floors_other():
    from learning_stats import derive_weights
    # only rs significant → thesis floored at 0.10, rs gets 0.90
    w_rs, w_thesis = derive_weights(coef_rs=0.9, t_rs=4.0, coef_thesis=0.5, t_thesis=1.0)
    assert abs(w_thesis - 0.10) < 1e-9
    assert abs(w_rs - 0.90) < 1e-9


def test_derive_weights_neither_significant_returns_none():
    from learning_stats import derive_weights
    result = derive_weights(coef_rs=0.2, t_rs=1.0, coef_thesis=0.1, t_thesis=0.5)
    assert result is None
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
cd /Users/alexschumacher/stocktrader && python3 -m pytest tests/test_learning_stats.py -v 2>&1 | head -10
```
Expected: `ModuleNotFoundError: No module named 'learning_stats'`

- [ ] **Step 3: Create `learning_stats.py`**

```python
"""
Learning Stats
==============
Pure functions for the learning agent: OLS regression, significance testing,
and weight derivation. No I/O, no state — fully unit-testable with synthetic data.

Uses numpy only (no scipy/statsmodels). The significance threshold is a
conservative t-critical of 2.05, valid for df >= 27 (guaranteed by the agent's
>= 30 sample-size gate).
"""

import numpy as np
from typing import Dict, Optional, Tuple

T_CRITICAL = 2.05   # two-sided p<0.05 for df>=27 (conservative)
WEIGHT_FLOOR = 0.10  # a feature is never weighted below this once in the model


def zscore(x: np.ndarray) -> np.ndarray:
    """Standardize to mean 0, std 1. Constant columns return all zeros."""
    x = np.asarray(x, dtype=float)
    sd = x.std()
    if sd == 0:
        return np.zeros_like(x)
    return (x - x.mean()) / sd


def ols_fit(X: np.ndarray, y: np.ndarray) -> Dict:
    """
    Ordinary least squares with an intercept.

    Args:
        X: (n, k) design matrix of predictors (no intercept column)
        y: (n,) outcome vector

    Returns:
        {
            "intercept": float,
            "coef": np.ndarray (k,),   # slope per predictor
            "se":   np.ndarray (k,),   # standard error per predictor
            "t":    np.ndarray (k,),   # t-statistic per predictor
            "n":    int,
        }
    """
    X = np.asarray(X, dtype=float)
    y = np.asarray(y, dtype=float)
    if X.ndim == 1:
        X = X.reshape(-1, 1)

    n, k = X.shape
    # Design matrix with intercept column
    Xd = np.column_stack([np.ones(n), X])     # (n, k+1)

    XtX = Xd.T @ Xd
    XtX_inv = np.linalg.inv(XtX)
    beta = XtX_inv @ Xd.T @ y                  # (k+1,)

    residuals = y - Xd @ beta
    rss = float(residuals @ residuals)
    dof = n - (k + 1)
    sigma2 = rss / dof if dof > 0 else float("inf")

    var_beta = sigma2 * np.diag(XtX_inv)       # (k+1,)
    se = np.sqrt(np.maximum(var_beta, 0.0))
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where(se > 0, beta / se, 0.0)

    return {
        "intercept": float(beta[0]),
        "coef": beta[1:],
        "se":   se[1:],
        "t":    t[1:],
        "n":    n,
    }


def derive_weights(
    coef_rs: float, t_rs: float,
    coef_thesis: float, t_thesis: float,
) -> Optional[Tuple[float, float]]:
    """
    Convert regression coefficients into (w_rs, w_thesis) summing to 1.

    Significance is |t| > T_CRITICAL.
      - both significant → proportional to |coef|, normalized
      - one significant  → significant feature gets (1 - floor), other = floor
      - neither          → None (no change)
    """
    rs_sig     = abs(t_rs) > T_CRITICAL
    thesis_sig = abs(t_thesis) > T_CRITICAL

    if rs_sig and thesis_sig:
        a, b = abs(coef_rs), abs(coef_thesis)
        total = a + b
        if total == 0:
            return None
        return (a / total, b / total)
    elif rs_sig:
        return (1.0 - WEIGHT_FLOOR, WEIGHT_FLOOR)
    elif thesis_sig:
        return (WEIGHT_FLOOR, 1.0 - WEIGHT_FLOOR)
    else:
        return None
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
cd /Users/alexschumacher/stocktrader && python3 -m pytest tests/test_learning_stats.py -v
```
Expected: 7 tests pass

- [ ] **Step 5: Commit**

```bash
git add learning_stats.py tests/test_learning_stats.py
git commit -m "Add pure-numpy OLS regression and weight derivation with tests"
```

---

## Task 3: Weights File Management

**Files:**
- Create: `learning_agent.py` (weights I/O portion)
- Create: `tests/test_learning_agent.py` (weights + fallback portion)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_learning_agent.py`:

```python
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))


def test_default_weights_when_absent(tmp_path):
    from learning_agent import load_weights
    wf = tmp_path / "weights.json"     # does not exist
    w = load_weights(wf)
    assert w["active"]["w_rs"] == 0.60
    assert w["active"]["w_thesis"] == 0.40
    assert w["active"]["version"] == 1


def test_load_weights_roundtrip(tmp_path):
    from learning_agent import load_weights, save_weights
    wf = tmp_path / "weights.json"
    data = {
        "active": {"version": 2, "w_rs": 0.7, "w_thesis": 0.3, "state": "provisional"},
        "champion": {"version": 1, "w_rs": 0.6, "w_thesis": 0.4, "state": "champion"},
        "rejected": [],
        "history": [],
    }
    save_weights(wf, data)
    loaded = load_weights(wf)
    assert loaded["active"]["version"] == 2
    assert loaded["active"]["w_rs"] == 0.7


def test_is_locked_detects_rejected(tmp_path):
    from learning_agent import is_locked
    rejected = [{"w_rs": 0.85, "w_thesis": 0.15}]
    # within tolerance ±0.03 → locked
    assert is_locked(0.86, 0.14, rejected) is True
    # outside tolerance → not locked
    assert is_locked(0.70, 0.30, rejected) is False


def test_is_locked_empty_list():
    from learning_agent import is_locked
    assert is_locked(0.6, 0.4, []) is False
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
cd /Users/alexschumacher/stocktrader && python3 -m pytest tests/test_learning_agent.py -v 2>&1 | head -10
```
Expected: `ModuleNotFoundError: No module named 'learning_agent'`

- [ ] **Step 3: Create `learning_agent.py` with the weights I/O functions**

```python
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
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
cd /Users/alexschumacher/stocktrader && python3 -m pytest tests/test_learning_agent.py -v
```
Expected: 4 tests pass

- [ ] **Step 5: Commit**

```bash
git add learning_agent.py tests/test_learning_agent.py
git commit -m "Add learning agent weights I/O: load/save/lock with tests"
```

---

## Task 4: Trade Loading and the Rollback State Machine

**Files:**
- Modify: `learning_agent.py` (add trade loading + rollback functions)
- Modify: `tests/test_learning_agent.py` (add rollback tests)

- [ ] **Step 1: Add the failing tests**

Append to `tests/test_learning_agent.py`:

```python
def _trade(symbol, pnl, weight_version, rs_rank=80, thesis_score=60, status="CLOSED"):
    return {
        "symbol": symbol, "status": status, "pnl_pct": pnl,
        "weight_version": weight_version, "rs_rank": rs_rank,
        "thesis_score": thesis_score,
    }


def test_closed_instrumented_trades_filters():
    from learning_agent import closed_instrumented_trades
    trades = [
        _trade("A", 5.0, 1),
        _trade("B", -2.0, 1, status="OPEN"),         # open — excluded
        {"symbol": "C", "status": "CLOSED", "pnl_pct": 3.0},  # no weight_version — excluded
        _trade("D", 4.0, 2),
    ]
    result = closed_instrumented_trades(trades)
    assert len(result) == 2
    assert {t["symbol"] for t in result} == {"A", "D"}


def test_mean_pnl_for_version():
    from learning_agent import mean_pnl_for_version
    trades = [_trade("A", 10.0, 2), _trade("B", 20.0, 2), _trade("C", 5.0, 1)]
    assert mean_pnl_for_version(trades, 2) == 15.0
    assert mean_pnl_for_version(trades, 1) == 5.0
    assert mean_pnl_for_version(trades, 99) is None   # no trades


def test_rollback_reverts_underperformer():
    from learning_agent import judge_provisional
    weights = {
        "active":   {"version": 2, "w_rs": 0.8, "w_thesis": 0.2, "state": "provisional"},
        "champion": {"version": 1, "w_rs": 0.6, "w_thesis": 0.4, "state": "champion",
                     "mean_pnl": 8.0},
        "rejected": [], "history": [],
    }
    # 10 provisional trades averaging 3.0 — below champion's 8.0 → revert
    trades = [_trade(f"P{i}", 3.0, 2) for i in range(10)]
    result, action = judge_provisional(weights, trades)
    assert action == "reverted"
    assert result["active"]["version"] == 1          # champion restored
    assert len(result["rejected"]) == 1
    assert result["rejected"][0]["w_rs"] == 0.8


def test_rollback_promotes_outperformer():
    from learning_agent import judge_provisional
    weights = {
        "active":   {"version": 2, "w_rs": 0.8, "w_thesis": 0.2, "state": "provisional"},
        "champion": {"version": 1, "w_rs": 0.6, "w_thesis": 0.4, "state": "champion",
                     "mean_pnl": 8.0},
        "rejected": [], "history": [],
    }
    trades = [_trade(f"P{i}", 12.0, 2) for i in range(10)]   # 12.0 > 8.0 → promote
    result, action = judge_provisional(weights, trades)
    assert action == "promoted"
    assert result["champion"]["version"] == 2
    assert result["active"]["state"] == "champion"


def test_judge_provisional_still_on_probation():
    from learning_agent import judge_provisional
    weights = {
        "active":   {"version": 2, "w_rs": 0.8, "w_thesis": 0.2, "state": "provisional"},
        "champion": {"version": 1, "w_rs": 0.6, "w_thesis": 0.4, "state": "champion",
                     "mean_pnl": 8.0},
        "rejected": [], "history": [],
    }
    trades = [_trade(f"P{i}", 3.0, 2) for i in range(5)]   # only 5 < PROBATION
    result, action = judge_provisional(weights, trades)
    assert action == "probation"
    assert result["active"]["version"] == 2               # unchanged
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
cd /Users/alexschumacher/stocktrader && python3 -m pytest tests/test_learning_agent.py -v 2>&1 | tail -10
```
Expected: failures with "cannot import name 'closed_instrumented_trades'"

- [ ] **Step 3: Add the functions to `learning_agent.py`**

Append to `learning_agent.py` (before any `main`):

```python
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
    # If champion has no recorded mean (first ever comparison), promote provisional.
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
        # Revert: champion becomes active again, provisional is rejected + locked
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
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
cd /Users/alexschumacher/stocktrader && python3 -m pytest tests/test_learning_agent.py -v
```
Expected: all tests pass (8 total in this file)

- [ ] **Step 5: Commit**

```bash
git add learning_agent.py tests/test_learning_agent.py
git commit -m "Add trade loading and champion/provisional rollback state machine"
```

---

## Task 5: The Agent Main Orchestration

**Files:**
- Modify: `learning_agent.py` (add `run` + `main`)
- Modify: `tests/test_learning_agent.py` (add end-to-end gate tests)

- [ ] **Step 1: Add the failing tests**

Append to `tests/test_learning_agent.py`:

```python
def test_run_accumulating_under_min_sample(tmp_path, monkeypatch):
    import learning_agent as la
    wf = tmp_path / "weights.json"
    # 20 trades — under MIN_SAMPLE of 30
    trades = [_trade(f"T{i}", 5.0, 1) for i in range(20)]
    report = la.run(trades, wf)
    assert report["status"] == "accumulating"
    assert report["trades_so_far"] == 20
    # weights untouched (still default)
    assert la.load_weights(wf)["active"]["version"] == 1


def test_run_applies_new_weights(tmp_path):
    import numpy as np
    import learning_agent as la
    wf = tmp_path / "weights.json"
    # Build 40 trades where rs_rank strongly predicts pnl, thesis does not
    rng = np.random.default_rng(3)
    trades = []
    for i in range(40):
        rs = float(rng.uniform(50, 99))
        th = float(rng.uniform(0, 100))
        pnl = 0.3 * rs + rng.normal(0, 2)    # rs drives pnl
        trades.append(_trade(f"T{i}", pnl, 1, rs_rank=rs, thesis_score=th))
    report = la.run(trades, wf)
    assert report["status"] == "applied"
    new = la.load_weights(wf)["active"]
    assert new["version"] == 2
    assert new["w_rs"] > new["w_thesis"]      # rs should dominate
    assert new["state"] == "provisional"


def test_run_no_significance_keeps_weights(tmp_path):
    import numpy as np
    import learning_agent as la
    wf = tmp_path / "weights.json"
    # pnl is pure noise — neither feature significant
    rng = np.random.default_rng(4)
    trades = [
        _trade(f"T{i}", float(rng.normal(0, 5)), 1,
               rs_rank=float(rng.uniform(50, 99)),
               thesis_score=float(rng.uniform(0, 100)))
        for i in range(40)
    ]
    report = la.run(trades, wf)
    assert report["status"] == "no_significance"
    assert la.load_weights(wf)["active"]["version"] == 1
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
cd /Users/alexschumacher/stocktrader && python3 -m pytest tests/test_learning_agent.py::test_run_applies_new_weights -v 2>&1 | tail -5
```
Expected: `AttributeError: module 'learning_agent' has no attribute 'run'`

- [ ] **Step 3: Add `run` and `main` to `learning_agent.py`**

Append to `learning_agent.py`:

```python
def _write_report(report: Dict):
    try:
        DOCS_DATA.mkdir(parents=True, exist_ok=True)
        REPORT_FILE.write_text(json.dumps(report, indent=2))
    except Exception as e:
        logger.warning(f"Could not write report: {e}")


def run(trades: List[Dict], weights_path: Path = WEIGHTS_FILE) -> Dict:
    """
    Core agent logic (pure enough to test): given the full trades list and a
    weights file path, run the gates and rollback, persist weights, return the
    report dict.
    """
    import numpy as np
    from learning_stats import ols_fit, zscore, derive_weights

    weights = load_weights(weights_path)
    instrumented = closed_instrumented_trades(trades)

    report = {
        "generated_at": datetime.now().isoformat(),
        "trades_so_far": len(instrumented),
    }

    # --- Rollback check FIRST: judge any provisional on probation ---
    if weights["active"].get("state") == "provisional":
        weights, action = judge_provisional(weights, instrumented)
        if action == "probation":
            report["status"] = "probation"
            save_weights(weights_path, weights)
            _write_report(report)
            return report
        if action in ("promoted", "reverted"):
            report["status"] = action
            report["active_version"] = weights["active"]["version"]
            save_weights(weights_path, weights)
            _write_report(report)
            return report

    # --- Sample-size gate ---
    if len(instrumented) < MIN_SAMPLE:
        report["status"] = "accumulating"
        save_weights(weights_path, weights)   # ensure file exists with defaults
        _write_report(report)
        return report

    # --- Regression ---
    rs   = np.array([t["rs_rank"] for t in instrumented], dtype=float)
    th   = np.array([t["thesis_score"] for t in instrumented], dtype=float)
    pnl  = np.array([t["pnl_pct"] for t in instrumented], dtype=float)
    X = np.column_stack([zscore(rs), zscore(th)])
    fit = ols_fit(X, pnl)

    derived = derive_weights(
        coef_rs=fit["coef"][0], t_rs=fit["t"][0],
        coef_thesis=fit["coef"][1], t_thesis=fit["t"][1],
    )

    if derived is None:
        report["status"] = "no_significance"
        report["t_rs"] = float(fit["t"][0])
        report["t_thesis"] = float(fit["t"][1])
        save_weights(weights_path, weights)
        _write_report(report)
        return report

    w_rs, w_thesis = derived

    # --- Lock check ---
    if is_locked(w_rs, w_thesis, weights["rejected"]):
        report["status"] = "locked_skip"
        report["derived"] = {"w_rs": w_rs, "w_thesis": w_thesis}
        save_weights(weights_path, weights)
        _write_report(report)
        return report

    # --- Apply new provisional weights ---
    new_version = weights["active"]["version"] + 1
    weights["active"] = {
        "version":  new_version,
        "w_rs":     round(w_rs, 4),
        "w_thesis": round(w_thesis, 4),
        "state":    "provisional",
        "applied_at": datetime.now().isoformat(),
        "derived_from_trades": len(instrumented),
        "trades_under_this_version": 0,
    }
    weights["history"].append({
        "version": new_version, "w_rs": round(w_rs, 4), "w_thesis": round(w_thesis, 4),
        "t_rs": float(fit["t"][0]), "t_thesis": float(fit["t"][1]),
        "applied_at": datetime.now().isoformat(),
    })
    save_weights(weights_path, weights)

    report["status"] = "applied"
    report["active_version"] = new_version
    report["new_weights"] = {"w_rs": round(w_rs, 4), "w_thesis": round(w_thesis, 4)}
    report["t_rs"] = float(fit["t"][0])
    report["t_thesis"] = float(fit["t"][1])
    _write_report(report)
    return report


def main():
    logger.info("=== Learning Agent Starting ===")
    trades = json.loads(TRADES_FILE.read_text()) if TRADES_FILE.exists() else []
    report = run(trades)
    logger.info(f"Status: {report['status']} | instrumented trades: {report['trades_so_far']}")
    if report.get("new_weights"):
        logger.info(f"New weights: {report['new_weights']}")
    logger.info("=== Learning Agent Complete ===")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run all tests — verify they pass**

```bash
cd /Users/alexschumacher/stocktrader && python3 -m pytest tests/test_learning_agent.py tests/test_learning_stats.py -v
```
Expected: all pass

- [ ] **Step 5: Dry run against real trades.json**

```bash
cd /Users/alexschumacher/stocktrader && python3 learning_agent.py 2>&1 | tail -5
```
Expected: runs without error; status is `accumulating` (few closed instrumented trades yet). Then verify weights.json was created:

```bash
python3 -c "import json; print(json.loads(open('docs/data/weights.json').read())['active'])"
```
Expected: shows the version-1 default active weights.

- [ ] **Step 6: Commit**

```bash
git add learning_agent.py tests/test_learning_agent.py docs/data/weights.json docs/data/learning_report.json
git commit -m "Add learning agent orchestration: gates, regression, apply, report"
```

---

## Task 6: Screener Integration

**Files:**
- Modify: `screener.py` (read weights.json for the ranking blend)
- Modify: `tests/test_learning_agent.py` (add screener fallback tests)

- [ ] **Step 1: Add the failing tests**

Append to `tests/test_learning_agent.py`:

```python
def test_screener_weights_loader_default(tmp_path, monkeypatch):
    import screener
    # Point the loader at a non-existent file → defaults
    monkeypatch.setattr(screener, "_WEIGHTS_PATH", tmp_path / "nope.json")
    w_rs, w_thesis = screener._load_ranking_weights()
    assert (w_rs, w_thesis) == (0.60, 0.40)


def test_screener_weights_loader_reads_active(tmp_path, monkeypatch):
    import json, screener
    wf = tmp_path / "weights.json"
    wf.write_text(json.dumps({"active": {"w_rs": 0.7, "w_thesis": 0.3}}))
    monkeypatch.setattr(screener, "_WEIGHTS_PATH", wf)
    assert screener._load_ranking_weights() == (0.7, 0.3)


def test_screener_weights_loader_rejects_bad_sum(tmp_path, monkeypatch):
    import json, screener
    wf = tmp_path / "weights.json"
    wf.write_text(json.dumps({"active": {"w_rs": 0.7, "w_thesis": 0.7}}))  # sums to 1.4
    monkeypatch.setattr(screener, "_WEIGHTS_PATH", wf)
    assert screener._load_ranking_weights() == (0.60, 0.40)
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
cd /Users/alexschumacher/stocktrader && python3 -m pytest tests/test_learning_agent.py::test_screener_weights_loader_default -v 2>&1 | tail -5
```
Expected: `AttributeError: module 'screener' has no attribute '_load_ranking_weights'`

- [ ] **Step 3: Add the loader to `screener.py`**

Near the top of `screener.py`, after the imports (after `import numpy as np`), add:

```python
_WEIGHTS_PATH = Path(__file__).parent / "docs" / "data" / "weights.json"


def _load_ranking_weights():
    """
    Read learned ranking weights from weights.json. Fall back to the 0.60/0.40
    default if the file is absent, malformed, or the weights don't sum to ~1.
    """
    try:
        if _WEIGHTS_PATH.exists():
            active = json.loads(_WEIGHTS_PATH.read_text()).get("active", {})
            w_rs = float(active.get("w_rs", 0.60))
            w_thesis = float(active.get("w_thesis", 0.40))
            if abs((w_rs + w_thesis) - 1.0) < 0.01:
                return w_rs, w_thesis
    except Exception:
        pass
    return 0.60, 0.40
```

- [ ] **Step 4: Use the loader in `run_screener`**

In `screener.py`, find the candidate-scoring line:

```python
        effective_score = (0.60 * rs_rank + 0.40 * thesis_score) * sector_boost
```

Replace with:

```python
        effective_score = (w_rs * rs_rank + w_thesis * thesis_score) * sector_boost
```

Then, immediately before the `for sym in universe:` candidate loop that contains
that line, add (once, outside the loop):

```python
    w_rs, w_thesis = _load_ranking_weights()
    logger.info(f"Ranking weights: RS={w_rs:.2f}, thesis={w_thesis:.2f}")
```

- [ ] **Step 5: Run tests — verify they pass**

```bash
cd /Users/alexschumacher/stocktrader && python3 -m pytest tests/test_learning_agent.py -v -k screener
```
Expected: 3 screener tests pass

- [ ] **Step 6: Verify screener still compiles and the blend works**

```bash
cd /Users/alexschumacher/stocktrader && python3 -m py_compile screener.py && echo "OK"
```
Expected: `OK`

- [ ] **Step 7: Commit**

```bash
git add screener.py tests/test_learning_agent.py
git commit -m "Screener reads learned ranking weights with safe fallback to 0.60/0.40"
```

---

## Task 7: Weekly Workflow

**Files:**
- Create: `.github/workflows/learning.yml`

- [ ] **Step 1: Create the workflow**

```yaml
name: Learning Agent

on:
  schedule:
    - cron: '0 12 * * 6'   # Saturday 12:00 UTC — after the trading week
  workflow_dispatch:

jobs:
  learn:
    runs-on: ubuntu-latest
    timeout-minutes: 5
    permissions:
      contents: write

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v4
        with:
          python-version: '3.12'
          cache: pip

      - run: pip install -r requirements.txt

      - name: Run learning agent
        run: python learning_agent.py

      - name: Commit weights + report
        if: always()
        run: |
          git config user.email "aschumacherdesign@gmail.com"
          git config user.name "schu51"
          git add docs/data/weights.json docs/data/learning_report.json 2>/dev/null || true
          git diff --staged --quiet || git commit -m "Learning agent update [skip ci]"
          git pull --rebase origin main
          git push
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/learning.yml
git commit -m "Add weekly learning agent workflow — Saturday 12:00 UTC"
```

---

## Task 8: Final Verification and Push

- [ ] **Step 1: Run the full test suite**

```bash
cd /Users/alexschumacher/stocktrader && python3 -m pytest tests/ -v
```
Expected: all tests pass (exit_logic 8 + stop_placement 8 + learning_stats 7 + learning_agent ~14)

- [ ] **Step 2: Compile-check everything**

```bash
cd /Users/alexschumacher/stocktrader && python3 -m py_compile learning_stats.py learning_agent.py screener.py run_daily_analysis.py && echo "All compile OK"
```
Expected: `All compile OK`

- [ ] **Step 3: Verify the agent leaves weights.json valid in every status**

```bash
cd /Users/alexschumacher/stocktrader && python3 -c "
import json
from pathlib import Path
w = json.loads(Path('docs/data/weights.json').read_text())
assert 'active' in w and 'champion' in w and 'rejected' in w and 'history' in w
a = w['active']
assert abs((a['w_rs'] + a['w_thesis']) - 1.0) < 0.01
print('weights.json valid:', a)
"
```
Expected: prints valid active weights summing to 1.

- [ ] **Step 4: Push**

```bash
cd /Users/alexschumacher/stocktrader && git pull --rebase origin main && git push origin main
```

---

## Self-Review

**Spec coverage:**
- ✅ Phase 0 instrumentation (Task 1) — adds rs_rank, thesis_score, thesis_grade, sector, weight_version
- ✅ Pure-numpy OLS, significance gate, weight derivation (Task 2)
- ✅ weights.json schema + load/save/lock (Task 3)
- ✅ Champion/provisional rollback, 10-trade probation (Task 4)
- ✅ Sample-size gate, regression, apply, report statuses (Task 5)
- ✅ Screener reads weights with fallback (Task 6)
- ✅ Weekly Saturday cadence (Task 7)
- ✅ Tests for every gate, rollback, lock, fallback

**Placeholder scan:** None. All steps carry exact code and commands.

**Type consistency:**
- `ols_fit(X, y)` returns `{"intercept", "coef", "se", "t", "n"}` — consumed consistently in Task 5
- `derive_weights(coef_rs, t_rs, coef_thesis, t_thesis)` → `(w_rs, w_thesis)` or `None` — consistent across Tasks 2 and 5
- `judge_provisional(weights, trades)` → `(weights, action)` where action ∈ {probation, promoted, reverted, none} — consistent Tasks 4, 5
- `weights.json` keys (`active`/`champion`/`rejected`/`history`, each with `w_rs`/`w_thesis`/`version`) — consistent across all tasks and the screener loader
- `weight_version` stamped in Task 1, read in Task 4's filter, matches `mean_pnl_for_version`

**Known limitation:** The agent compares a provisional's mean P&L to the champion's
stored `mean_pnl`. On the very first promotion (champion has `mean_pnl: None`), the
provisional is promoted unconditionally — acceptable, since version 1 is the
unmeasured seed default and any data-derived weight that cleared both gates is a
defensible replacement. Documented in `judge_provisional`.
