# Options Long Calls — Design Spec
**Date:** 2026-06-08  
**Branch:** `feature/options-long-calls`  
**Status:** Approved for implementation

---

## Overview

Add a long-call options execution path alongside the existing equity momentum model. The screener and signal logic are unchanged. The top N highest-conviction signals are routed to options instead of equity when implied volatility conditions are favorable. Everything below equity execution on main is untouched — the branch diff is purely additive.

---

## Architecture

### New files

| File | Purpose |
|------|---------|
| `options_broker.py` | Alpaca options API wrapper: fetch chain, IV rank, place/close orders |
| `options_executor.py` | Signal → contract selection → order placement, daily re-evaluation |
| `docs/data/options_trades.json` | Options position log, separate from `trades.json` |

### Modified files

| File | Change |
|------|--------|
| `run_daily_analysis.py` | Add `--options-top N` flag (default 3). Routes top N signals through options path before equity execution. No other changes. |

No other files on main are modified. The equity path is fully preserved.

---

## Signal Routing

The screener produces candidates ranked by `effective_score`. When `--options-top N` is passed:

```
opportunities (sorted by effective_score, descending)
  │
  ├─ [0..N-1]  top N signals
  │               └─ IV rank check
  │                   ├─ IV rank ≤ 50th percentile → options_executor
  │                   └─ IV rank > 50th percentile → falls back to equity executor
  │
  └─ [N..end]  remaining signals → equity executor (unchanged)
```

**Fallback rule:** If the options executor fails for any reason (no valid chain, budget too small, API error), the signal falls through to the equity executor automatically. No signal is silently dropped.

**No doubling up:** If a symbol already has an open options position, it is skipped by the options executor and routed to equity.

---

## Chain Selection

When a signal passes the IV rank gate:

1. **Expiration:** Query Alpaca `/v2/options/contracts` for all call expirations between 25–50 DTE. Select the expiration closest to 37 DTE (midpoint of the 30–45 target window). If no expiration exists in the 25–50 DTE window, skip options and fall back to equity.

2. **Strike:** From the selected expiration, pick the call strike nearest to the current underlying bid price (ATM). Targets ~0.45–0.55 delta without requiring a live Greeks feed at selection time.

3. **No DTE tie-breaking preference for longer expiration.** The thesis is momentum-based and actively monitored — extra time does not improve outcomes if the thesis deteriorates. Pick closest to 37 DTE, period.

---

## Position Sizing

```
contracts = floor(portfolio_value × 0.015 / (contract_premium × 100))
```

- **Risk per trade:** 1.5% of portfolio in premium (between conservative 1% and aggressive 2% practitioner consensus)
- **Minimum:** 1 contract. If the budget cannot cover 1 contract, skip options and fall back to equity
- **No momentum multiplier.** The equity model's `_scale_size_by_momentum` does not apply. The leverage inherent in options makes further amplification inappropriate.
- **No Kelly criterion.** Premium paid = max loss. Simple percentage sizing is sufficient.

---

## Exit Rules

On every daily run, `options_executor.py` re-evaluates each open options position against the following checks in order. First triggered check closes the position.

| Priority | Trigger | Action | Rationale |
|----------|---------|--------|-----------|
| 1 | Earnings within 5 days | Close | IV crush post-earnings kills calls even on favorable moves |
| 2 | 50% loss on premium | Close | Hard stop — thesis was wrong at entry |
| 3 | Underlying breaks 50MA | Close | Trend invalidated — same hard exit as equity model |
| 4 | RS rank drops > 15 pts | Close | Momentum gone — the core entry signal has reversed |
| 5 | Thesis grade drops to D | Close | Setup quality, accumulation, or earnings trajectory deteriorated |
| 6 | 100% gain on premium | Close | Take profit — 2× premium is the target |
| 7 | 21 DTE reached | Close | Theta cliff — decay accelerates non-linearly past this point |

**Earnings rule applies to existing positions, not just new entries.** The equity model blocks entries within the blackout window; the options model also closes existing positions before that window.

**No "wait for recovery" logic.** If the thesis invalidates, the position closes that session. This is a momentum model — it does not hold deteriorating positions hoping for reversals.

---

## IV Rank

IV rank is calculated as:

```
iv_rank = (current_iv - iv_52w_low) / (iv_52w_high - iv_52w_low)
```

- Fetched via Alpaca's options chain data or a supplementary yfinance IV lookup
- Gate threshold: **50th percentile (iv_rank > 0.50 → skip options)**
- If IV rank data is unavailable for a symbol, skip options and fall back to equity (fail safe)

---

## Position Tracking

`docs/data/options_trades.json` stores each position:

```json
{
  "contract_symbol": "NVDA260718C00950000",
  "underlying": "NVDA",
  "status": "OPEN",
  "entry_date": "2026-06-09",
  "expiration": "2026-07-18",
  "dte_at_entry": 39,
  "strike": 950.0,
  "contracts": 2,
  "premium_paid": 18.50,
  "total_cost": 3700.0,
  "underlying_price_at_entry": 948.20,
  "iv_at_entry": 0.32,
  "iv_rank_at_entry": 0.38,
  "exit_date": null,
  "exit_premium": null,
  "exit_reason": null,
  "pnl_usd": null,
  "pnl_pct": null
}
```

---

## What This Branch Does Not Include

- Spreads, covered calls, or any multi-leg strategy
- Short options or credit strategies
- Options on indices or ETFs
- Live Greeks feed (delta, theta tracked at entry only via strike proximity)
- Dashboard UI changes (options trades visible in `options_trades.json` only)

These are explicitly out of scope for v1. The equity model's dashboard is unchanged.

---

## Branch Workflow

1. Build and paper trade on `feature/options-long-calls`
2. Run alongside main — both models execute in the same daily session via `--options-top 3`
3. Validate over 4–6 weeks: win rate, average premium gain/loss, IV rank filter effectiveness
4. PR into main when validated

---

## Success Criteria

- Options executor runs without errors alongside equity executor
- IV rank gate correctly filters high-IV entries (verify against manually checked IV data)
- All 7 exit rules fire correctly in paper trading
- Zero silent failures — every signal either executes or logs a clear fallback reason
- After 20+ closed options trades: win rate and average P&L are tracked and comparable to equity path
