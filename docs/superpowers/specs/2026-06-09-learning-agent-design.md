# Learning Agent — Design Spec
**Date:** 2026-06-09
**Branch:** `feature/learning-agent`
**Status:** Approved for implementation

---

## Overview

A self-improving agent that adjusts the screener's ranking weights based on the
realized outcomes of closed trades. It uses outcome regression on entry features
to determine which signal — RS rank or thesis score — actually predicted returns,
then re-weights the blend accordingly.

The agent **auto-applies** changes (no human in the loop per cycle) but is bounded
by three guardrails: a minimum sample size, a statistical-significance gate, and
auto-rollback on degradation. It can only touch **ranking weights** — never the
safety gates, stops, or position sizing. A bad change costs opportunity, not capital.

This is **Phase 1**. The architecture is built so that more tuning surface
(entry filters, then exit/risk params) can be unlocked in later phases once the
mechanism is proven, without a rewrite.

---

## Scope

**In scope (Phase 1):**
- The screener ranking blend: `effective_score = w_rs × rs_rank + w_thesis × thesis_score`
- Currently hardcoded at `w_rs = 0.60`, `w_thesis = 0.40`

**Explicitly out of scope (future phases):**
- Sector boost (1.05) — stays fixed; folding it in adds a third noisy variable
- Entry filters (RSI 78, BB 0.90, thesis grade gating)
- Exit/risk params (stop tiers, trailing %, −15% hard loss, sizing multipliers)
- The RS sub-formula weights (0.40/0.20/0.20/0.20 quarterly)

---

## Prerequisite: Entry-Log Instrumentation (Phase 0)

The regression needs `rs_rank`, `thesis_score`, `thesis_grade`, and `sector`
logged at entry. The current `_log_trade` stores only `trend_score` and
`confidence`. This must be fixed first.

**Cold start:** The agent can only learn from trades logged *after* this change.
The 15 currently-open positions lack these fields and are excluded. The learning
clock starts at zero from the instrumentation date.

**Change to `run_daily_analysis.py` `_log_trade` BUY block** — add four fields:

```python
trades.append({
    # ... existing fields ...
    "rs_rank":        rs_rank,        # new
    "thesis_score":   thesis_score,   # new
    "thesis_grade":   thesis_grade,   # new
    "sector":         sector,         # new
    "weight_version": weight_version, # new — see Rollback section
    # ... existing fields ...
})
```

These values are already available in the opportunity dict at the call site
(`opp.get("rs_rank")`, `opp.get("thesis_score")`, etc.) — they just need to be
passed through to `_log_trade` and stored.

---

## Architecture & Data Flow

New files: `learning_agent.py`, `.github/workflows/learning.yml`,
`tests/test_learning_agent.py`. New data files: `docs/data/weights.json`
(versioned weight sets), `docs/data/learning_report.json` (audit trail).

```
trades.json (closed, fully-instrumented trades)
   │
   ▼
learning_agent.py   (weekly, Saturday 12:00 UTC)
   │
   ├─ 1. Load closed trades with weight_version set (post-instrumentation)
   ├─ 2. Sample-size gate: >= 30 closed trades? else report "accumulating", exit
   ├─ 3. Rollback check FIRST: is the current provisional weight set due for
   │        judgment (>= 10 of its own closed trades)?
   │        - provisional mean_pnl >= champion mean_pnl → promote provisional
   │        - provisional mean_pnl <  champion mean_pnl → revert + lock rejected
   │        If a provisional is still on probation (<10 trades), exit (no stacking)
   ├─ 4. Regress pnl_pct ~ z(rs_rank) + z(thesis_score)  (pure-numpy OLS)
   ├─ 5. Significance gate: both 95% CIs exclude zero?
   │        - both significant      → weights from |coefficients|, normalized
   │        - one significant       → significant feature dominant, other floored 0.10
   │        - neither significant   → no change, report "no_significance"
   ├─ 6. Lock check: derived weights within ±0.03 of a rejected set? → skip
   ├─ 7. Write new provisional weights to weights.json (versioned, timestamped)
   └─ 8. Write learning_report.json (status, stats, what changed and why)
   │
   ▼
screener.py reads weights.json at runtime (falls back to 0.60/0.40 if absent)
```

### Key design choices

- **Weights live in `weights.json`, not `config.py`.** The agent never rewrites
  source code — it writes a data file the screener reads. Each weight set is
  versioned with the trade sample it was derived from, which is what makes
  rollback and auditing possible.
- **Standardized coefficients.** RS rank (1–99) and thesis score (0–100) are
  z-scored before regression so their coefficients are comparable, then the
  absolute magnitudes are normalized to sum to 1 for the blend.
- **Rollback is checked before deriving.** A provisional weight on probation
  must be judged before any new change is considered — never stack an unproven
  change on another.

---

## weights.json Schema

```json
{
  "active": {
    "version": 3,
    "w_rs": 0.67,
    "w_thesis": 0.33,
    "state": "provisional",
    "applied_at": "2026-07-15T12:00:00",
    "derived_from_trades": 34,
    "trades_under_this_version": 4
  },
  "champion": {
    "version": 2,
    "w_rs": 0.58,
    "w_thesis": 0.42,
    "state": "champion",
    "promoted_at": "2026-07-01T12:00:00",
    "mean_pnl": 6.2,
    "n_trades": 22
  },
  "rejected": [
    { "w_rs": 0.85, "w_thesis": 0.15, "rejected_at": "2026-06-22T12:00:00", "mean_pnl": -1.4 }
  ],
  "history": [
    { "version": 1, "w_rs": 0.60, "w_thesis": 0.40, "note": "seed default" }
  ]
}
```

`active` is what the screener reads. When `active.state == "provisional"` and it
accumulates ≥10 trades, the next agent run judges it: promote (becomes champion)
or revert (champion restored to active, this set appended to `rejected`).

---

## The Rollback Mechanism (primary safety net)

Because per-cycle weight steps are **unbounded** (user decision), rollback carries
the safety load.

**Trade tagging:** `_log_trade` stamps `weight_version` = `active.version` onto
every new trade. This is the attribution key — it ties each realized outcome to
the weight set that ranked it.

**Champion / provisional states:**
- The current proven weight set is the **champion**.
- A newly derived set is applied as **provisional** — live, but on probation.
- Provisional must accumulate **≥10 of its own closed trades** before judgment.
  Until then, the agent makes no further changes.

**Rollback trigger:**
```
mean_pnl(provisional's closed trades) vs mean_pnl(champion's closed trades)
   provisional >= champion  → promote provisional to champion
   provisional <  champion  → revert active to champion, append provisional to rejected
```

**Lock against oscillation:** A reverted set is recorded in `rejected`. If a later
cycle derives weights within ±0.03 of any rejected set, the agent skips applying
(logs `"status": "locked_skip"`). Each bad direction is paid for once.

**Worst-case bound:** at most 10 trades of underperformance before a forced revert,
and that direction can never be retried. Since scope is ranking-only, those 10
trades still passed every entry filter and stop — opportunity cost, not lost capital.

---

## Statistical Gates & Regression Method

Two gates must both pass before new weights are derived.

**1. Sample-size gate:** ≥30 closed, fully-instrumented trades since the
instrumentation date. Below 30 → report `"accumulating"`, exit, weights untouched.

**2. Significance gate:** Fit `pnl_pct ~ z(rs_rank) + z(thesis_score)` via OLS.
Both predictors' 95% CIs must exclude zero.
- Both significant → weights from `|coefficients|`, normalized to sum 1.
- One significant → significant feature dominant, other floored at **0.10**
  (never zeroed — a feature isn't permanently discarded on one regime's data).
- Neither significant → no change, report `"no_significance"`.

**Pure-numpy OLS** (no scipy/statsmodels — neither is installed, and adding scipy
slows every CI run ~30MB):
```
X  = [1, z(rs_rank), z(thesis_score)]      # design matrix with intercept
β  = (XᵀX)⁻¹ Xᵀ y                          # coefficients
RSS = Σ(y − Xβ)²
σ² = RSS / (n − k)                          # k = 3 params
SE = √diag(σ² (XᵀX)⁻¹)                      # standard errors
t  = β / SE                                 # t-statistic per coefficient
```
Significance: `|t| > 2.05` (conservative t-critical for df≥27, two-sided p<0.05).
The sample-size gate guarantees n≥30 so df≥27 always holds.

**Weight derivation example:** `z(rs_rank)` coef = 0.8, `z(thesis_score)` coef = 0.4
→ `|0.8| / (0.8+0.4) = 0.67`, `0.4/1.2 = 0.33` → `w_rs=0.67, w_thesis=0.33`.

---

## Screener Integration

One change to `run_screener()` in `screener.py` — read weights at the top:

```python
def _load_ranking_weights():
    """Read learned weights; fall back to defaults if absent or malformed."""
    try:
        import json
        from pathlib import Path
        p = Path(__file__).parent / "docs" / "data" / "weights.json"
        if p.exists():
            active = json.loads(p.read_text()).get("active", {})
            w_rs = float(active.get("w_rs", 0.60))
            w_thesis = float(active.get("w_thesis", 0.40))
            if abs((w_rs + w_thesis) - 1.0) < 0.01:   # sanity: must sum to ~1
                return w_rs, w_thesis
    except Exception:
        pass
    return 0.60, 0.40

# In the candidate loop, replace the hardcoded blend:
w_rs, w_thesis = _load_ranking_weights()   # computed once before the loop
effective_score = (w_rs * rs_rank + w_thesis * thesis_score) * sector_boost
```

Everything else in the screener is untouched.

---

## Cadence & Failure Handling

**Cadence:** Weekly, Saturday 12:00 UTC — after the trading week closes, so it
sees a full week of newly-closed trades and never races the live agents.

**Failure handling:** Every exit path writes `learning_report.json` with an
explicit `status`:
- `accumulating` — under 30 trades
- `probation` — provisional still gathering its 10 trades, no action
- `no_significance` — gates failed, no change
- `applied` — new provisional weights live
- `promoted` — provisional became champion
- `reverted` — rollback fired, champion restored
- `locked_skip` — derived weights matched a rejected set
- `error` — exception; weights.json untouched

The agent never leaves weights undefined: any exception leaves `weights.json`
as-is and the screener keeps using the last good set.

---

## Testing (TDD, pure functions)

`tests/test_learning_agent.py`:
- `test_ols_recovers_known_slope` — synthetic data with known relationship; OLS recovers it
- `test_significance_gate_rejects_noise` — random P&L vs features → no significant coefficient
- `test_weight_derivation_normalizes` — coefficients → weights sum to 1, floored at 0.10
- `test_weight_derivation_one_significant` — one feature significant → other floored at 0.10
- `test_rollback_reverts_underperformer` — provisional mean < champion over 10 trades → reverts
- `test_rollback_promotes_outperformer` — provisional mean ≥ champion → promotes
- `test_lock_prevents_re_derivation` — derived weights within ±0.03 of rejected → skipped
- `test_sample_size_gate` — <30 trades → status "accumulating", weights untouched
- `test_screener_falls_back_when_no_weights_file` — absent weights.json → 0.60/0.40
- `test_screener_falls_back_on_malformed` — weights that don't sum to 1 → 0.60/0.40

---

## Phased Expansion (future, not this build)

The architecture supports unlocking more surface without a rewrite:
- **Phase 2:** add sector boost and entry-filter thresholds to the regression
  (multivariate, same OLS + significance + rollback machinery).
- **Phase 3:** exit/risk params — highest risk, would require the rollback window
  to also track max-drawdown, not just mean P&L.

Each phase reuses the same weights.json + champion/provisional + rollback design.

---

## Success Criteria

- Phase 0 instrumentation logs all four entry features on every new trade
- Agent runs weekly without error, always leaving weights.json in a valid state
- Below 30 trades: agent reports `accumulating`, never changes weights
- A provisional weight that underperforms over 10 trades is automatically reverted
  and locked
- The screener reads learned weights and falls back cleanly when they're absent
- All unit tests pass, including synthetic-data regression recovery
- After enough cycles: weight changes are traceable in weights.json history with
  the trade sample and statistics that justified each one
