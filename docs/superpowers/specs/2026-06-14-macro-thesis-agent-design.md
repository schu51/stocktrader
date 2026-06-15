# Macro Thesis Agent — Design Spec
**Date:** 2026-06-14
**Branch:** `feature/macro-thesis-agent`
**Status:** Approved for implementation

---

## Overview

A weekly LLM research agent that gives the momentum model the layer it
structurally lacks: a *view on why* a move compounds, not just *that* it is
moving. Inspired by thesis-driven investors (notably Leopold Aschenbrenner's
Situational Awareness fund — a macro thesis that AI is power/compute
constrained, expressed by buying underpriced *second-order* beneficiaries like
Bloom Energy rather than the crowded leaders like Nvidia).

The agent reasons over macro/economic/policy news (plus retail sentiment as a
minor inverse signal) to produce structured, falsifiable theses. Each thesis
names beneficiary sectors and the crowded leaders it avoids. The screener then
applies a **bounded conviction-scaled multiplier** to candidates whose sector
matches a live thesis — a tilt, never a gate.

This is a **weighting** layer, consistent with the learned-weights and
forward-thesis machinery already in the model. It can only tilt ranking within
±10–25%; it never touches entry filters, stops, sizing, or execution. If the
whole layer breaks, the model degrades gracefully to exactly what it is today.

---

## Why this exists (the realization)

The screener detects *what* is moving (RS rank, MA alignment) with zero view on
*why* it will continue. It cannot distinguish an early-innings structural move
(compounds) from a late-stage crowded chase (reverts). Thesis-driven investors
have the opposite: a narrative causal chain about how the world changes, mapped
to specific — often non-obvious — beneficiaries, exited on thesis invalidation
rather than price noise. This agent supplies that missing causal layer.

---

## Scope

**In scope:**
- A new `macro_research_agent.py` (weekly) that maintains `docs/data/theses.json`
- A bounded macro multiplier read by `screener.py` and applied to `effective_score`
- A `macro_research.yml` workflow (Saturday)

**Explicitly out of scope:**
- Gating the universe (rejected in favor of weighting — lower overhead, nimbler)
- Touching entry filters, stops, position sizing, or execution
- Auto-tuning the multiplier constants (left for the learning agent later)
- Trading on WallStreetBets / retail sentiment as a buy signal (strictly a
  crowding/radar input — see Sources)

---

## Architecture & Data Flow

New files: `macro_research_agent.py`, `.github/workflows/macro_research.yml`,
`tests/test_macro_thesis.py`. New data files: `docs/data/theses.json` (the
register) and `docs/data/macro_brief.json` (audit/dashboard).

```
WEEKLY (Saturday)
macro_research_agent.py
   ├─ 1. Load active theses from theses.json
   ├─ 2. RE-VALIDATE each active thesis: fetch current evidence, check its
   │        invalidation_condition and horizon → retire any broken/expired
   ├─ 3. GENERATE new candidate theses: Claude API reasons over fetched
   │        primary/secondary/supplementary sources
   ├─ 4. GUARD: reject theses that fail any gate (sources, falsifiability,
   │        second-order, sector-key validity)
   ├─ 5. Write surviving + accepted theses to theses.json (versioned, sourced)
   └─ 6. Write macro_brief.json (status + full reasoning + sources)

DAILY screener.py
   reads theses.json → for each candidate, apply macro_multiplier() to
   effective_score. Absent/stale/malformed register → ×1.0 (no change).
```

### Key design choices

- **Theses live in `theses.json` (data file), never in code** — same pattern as
  `weights.json`. Versioned, timestamped, source-cited.
- **Mandatory source grounding (anti-hallucination):** every factual claim in a
  thesis traces to a fetched URL. A thesis with no verifiable primary-tier
  source is rejected. The agent reasons over fetched article content, not model
  memory.
- **Graceful degradation:** the macro layer can only tilt ±10–25%. Any failure
  leaves the screener behaving exactly as it does today.

---

## Thesis Schema (`theses.json`)

```json
{
  "id": "TH-2026-0042",
  "theme": "AI compute buildout is power-constrained",
  "causal_chain": "Frontier training scales faster than grid capacity → power generation/equipment becomes the binding constraint → underpriced power enablers re-rate",
  "beneficiary_sectors": ["utilities", "energy", "industrials"],
  "second_order": true,
  "consensus_names_excluded": ["NVDA", "MSFT"],
  "conviction": 0.70,
  "conviction_breakdown": {
    "source_corroboration": 0.8,
    "causal_directness":    0.7,
    "non_consensus":        0.6,
    "invalidation_clarity": 0.7
  },
  "invalidation_condition": "Top-4 hyperscaler AI capex guidance declines QoQ, OR grid-interconnect queue clears materially",
  "horizon": "2026-12-31",
  "sources": ["https://...", "https://..."],
  "created_at": "2026-06-14",
  "last_validated": "2026-06-14",
  "status": "active"
}
```

**Schema-enforced guards** (a thesis missing/failing any of these is rejected):
- `second_order: true` and a non-empty `consensus_names_excluded` — the agent
  must name the crowded leaders it is avoiding and point at the underpriced
  enabler. "Buy NVDA" fails this.
- `invalidation_condition` (non-empty) and `horizon` (valid future date) — no
  falsifiable kill-condition → rejected.
- `sources` must contain ≥1 primary-tier URL — Reddit-only or empty → rejected.
- `beneficiary_sectors` must use `config.SECTOR_MAP` keys (technology, energy,
  utilities, financials, industrials, healthcare, consumer_cyclical,
  consumer_defensive, communication_services, basic_materials, real_estate).

---

## Conviction

Conviction is **not** a gestalt number — it is the weighted average of four
0–1 sub-scores, each with a one-line justification stored in
`conviction_breakdown`:

| Sub-score | Measures |
|-----------|----------|
| `source_corroboration` | How many independent high-signal sources converge on the causal claim (1 blog ≠ 5 independent reports) |
| `causal_directness` | Is the mechanism direct + near-term, or speculative and many steps removed? |
| `non_consensus` | How underpriced/uncrowded the beneficiary is — **WSB euphoria on the beneficiary lowers this; no chatter + real macro evidence raises it** |
| `invalidation_clarity` | Is the kill-condition sharp and measurable? |

`conviction = mean(sub-scores)` for v1 (equal weights). A thesis with one
source, a speculative multi-step chain, on a crowded name, with a vague
kill-condition scores near zero and is filtered by the conviction floor (0.35).

---

## Sources (tiered)

| Tier | Sources | Role |
|------|---------|------|
| **Primary** (drives theses) | Financial/economic news, Fed/macro data, earnings commentary, industry reports | Causal-chain evidence. A thesis must cite ≥1 primary source. |
| **Secondary** (context) | Sector/policy/regulatory/government news | Structural tailwinds/headwinds, supply constraints, policy shifts |
| **Supplementary** (one input) | r/wallstreetbets + broad retail sentiment | **Inverse crowding signal + emerging-theme radar only.** Can dampen conviction or flag a name for exclusion; can never originate or boost a thesis. |

**Hard rule:** WSB alone can never support a thesis. Its contribution is
confined to the `non_consensus` sub-score and `consensus_names_excluded`.
Reddit is one supplementary source among many, not a core driver.

Retrieval: web search + fetch for primary/secondary; Reddit public JSON
(`reddit.com/r/wallstreetbets/top.json`) for the supplementary signal.

---

## The Multiplier (in `screener.py`)

Applied once, after the existing `effective_score` is computed:

```
def macro_multiplier(candidate, live_theses):
    # 1. Crowded-leader exclusion wins first
    if candidate.symbol in union(t.consensus_names_excluded for live t):
        return 0.90
    # 2. Beneficiary-sector match
    matches = [t for t in live_theses if candidate.sector in t.beneficiary_sectors]
    if not matches:
        return 1.0
    # 3. Scale by the STRONGEST matching thesis's conviction (no stacking)
    best = max(matches, key=lambda t: t.conviction)
    return 1.0 + best.conviction * 0.25     # conviction 1.0 → 1.25

effective_score *= macro_multiplier        # bounded [0.90, 1.25]
```

- **Exclusion precedence:** a crowded leader is dampened even if its sector is
  otherwise supported. Crowding always beats theme membership.
- **Strongest-match, no stacking:** two supported themes don't compound; takes
  the highest-conviction view. Hard cap 1.25.
- **Neutral default:** no live thesis, or absent/stale register → ×1.0.
- A thesis is "live" only when `status == "active"` and `horizon` is in the
  future and `last_validated` is within 14 days.
- The `0.25` span and `0.90` floor are constants the learning agent could tune
  later, once there is closed-trade evidence the tilt helps.

---

## Cadence & Failure Handling

**Cadence:** Weekly, Saturday (after the trading week). `workflow_dispatch` for
manual runs.

**Failure handling (graceful degradation):**
- Claude API error / no sources fetched / malformed output → `theses.json`
  untouched; screener uses the last good register.
- Zero valid theses produced → register ages; expired theses retire, nothing
  added.
- `theses.json` absent, stale (>14 days), or malformed → screener applies ×1.0
  to everything. The macro layer can never break the screener.
- Every run writes `macro_brief.json` with a status (`validated`, `generated`,
  `retired`, `no_change`, `error`) plus full reasoning and sources for audit.

---

## Testing (pure logic, not the LLM)

`tests/test_macro_thesis.py`:
- `test_macro_multiplier_excluded_name` — excluded symbol → 0.90
- `test_macro_multiplier_beneficiary` — sector match, conviction 0.8 → 1.20
- `test_macro_multiplier_strongest_match` — two matches → higher conviction, no stacking
- `test_macro_multiplier_no_match` → 1.0
- `test_macro_multiplier_bounds` — conviction 1.0 caps at 1.25; never exceeds bounds
- `test_macro_multiplier_exclusion_precedence` — excluded name in a supported sector → 0.90
- `test_thesis_rejected_no_sources` — empty/Reddit-only sources → rejected
- `test_thesis_rejected_no_invalidation` — missing kill-condition → rejected
- `test_thesis_rejected_not_second_order` — no consensus_names_excluded → rejected
- `test_thesis_rejected_bad_sector_key` — sector not in SECTOR_MAP → rejected
- `test_conviction_is_mean_of_subscores` — conviction == mean(breakdown)
- `test_conviction_floor_filters` — conviction < 0.35 → thesis filtered
- `test_stale_register_neutral` — theses.json >14 days old → all multipliers 1.0
- `test_expired_thesis_retired` — thesis past horizon → status retired, not applied
- `test_screener_neutral_when_register_absent` — no theses.json → ×1.0

The LLM generation is non-deterministic and not unit-tested; the **gates** it
must pass and the **multiplier math** are fully tested with synthetic theses.

---

## Phased Relationship to Other Agents

- **Composes with learned weights:** macro multiplier is applied on top of the
  `w_rs / w_thesis` blend. Independent layers.
- **Future learning-agent integration:** once closed-trade data exists, the
  learning agent could tune the `0.25` span / `0.90` floor / conviction
  sub-score weights — same champion/provisional/rollback discipline. Not in v1.

---

## Success Criteria

- Agent runs weekly without error, always leaving `theses.json` valid.
- Every accepted thesis cites ≥1 real primary-tier source, names a second-order
  beneficiary, excludes the crowded leaders, and has a falsifiable invalidation
  condition + future horizon.
- WSB can only dampen/flag — never originate or boost a thesis.
- Screener applies a bounded [0.90, 1.25] tilt and degrades to ×1.0 on any
  failure or stale register.
- All multiplier-math and gate tests pass.
- macro_brief.json gives a full audit trail of every thesis the model acts on.
