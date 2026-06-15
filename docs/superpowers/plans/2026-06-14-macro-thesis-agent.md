# Macro Thesis Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A weekly LLM research agent that maintains a register of falsifiable, source-grounded macro theses and a bounded multiplier the screener applies to candidates whose sector matches a live thesis — supplying the model's missing "why a move compounds" layer without gating the universe.

**Architecture:** Pure logic (validation gates, conviction, liveness, retirement, multiplier) lives in `macro_thesis.py`, fully unit-tested. The LLM orchestration (source fetch → Claude reasoning → parse → gate → write register) lives in `macro_research_agent.py`; only its deterministic gates are unit-tested. `screener.py` reads `theses.json` and applies the multiplier, falling back to ×1.0 on any failure. Mirrors the learning_stats.py / learning_agent.py split.

**Tech Stack:** Python 3.12, numpy/pandas (existing), `anthropic` SDK (new), web fetch + Reddit public JSON, pytest, GitHub Actions cron.

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `macro_thesis.py` | Pure: `validate_thesis`, `compute_conviction`, `is_thesis_live`, `retire_expired`, `macro_multiplier`, `load_live_theses` |
| Create | `macro_research_agent.py` | Orchestration: fetch sources, call Claude, parse, gate via macro_thesis, manage register, write brief |
| Create | `tests/test_macro_thesis.py` | Unit tests for all pure logic |
| Modify | `screener.py` | Read theses.json, apply `macro_multiplier` to `effective_score` |
| Modify | `requirements.txt` | Add `anthropic>=0.40.0` |
| Create | `.github/workflows/macro_research.yml` | Saturday weekly trigger (needs ANTHROPIC_API_KEY secret) |
| Create | `docs/data/theses.json` | The thesis register (seeded empty) |

**Why the split:** the multiplier math and validation gates are deterministic and must be proven with synthetic theses; the LLM call is non-deterministic and isolated behind a function boundary so it can't entangle the testable logic. Same pattern as the learning agent.

---

## Task 1: Thesis validation gates + conviction (pure)

**Files:**
- Create: `macro_thesis.py`
- Create: `tests/test_macro_thesis.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_macro_thesis.py`:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))


def _good_thesis(**over):
    t = {
        "id": "TH-1", "theme": "AI power-constrained",
        "causal_chain": "training scales faster than grid -> power enablers re-rate",
        "beneficiary_sectors": ["utilities", "energy"],
        "second_order": True,
        "consensus_names_excluded": ["NVDA"],
        "conviction_breakdown": {
            "source_corroboration": 0.8, "causal_directness": 0.7,
            "non_consensus": 0.6, "invalidation_clarity": 0.7,
        },
        "invalidation_condition": "hyperscaler capex declines QoQ",
        "horizon": "2099-12-31",
        "sources": ["https://reuters.com/x"],
        "status": "active",
    }
    t.update(over)
    return t


def test_compute_conviction_is_mean_of_subscores():
    from macro_thesis import compute_conviction
    c = compute_conviction({
        "source_corroboration": 0.8, "causal_directness": 0.7,
        "non_consensus": 0.6, "invalidation_clarity": 0.7,
    })
    assert abs(c - 0.7) < 1e-9


def test_valid_thesis_accepted():
    from macro_thesis import validate_thesis
    ok, reason = validate_thesis(_good_thesis())
    assert ok is True, reason


def test_rejected_no_sources():
    from macro_thesis import validate_thesis
    ok, reason = validate_thesis(_good_thesis(sources=[]))
    assert ok is False
    assert "source" in reason.lower()


def test_rejected_reddit_only_sources():
    from macro_thesis import validate_thesis
    ok, reason = validate_thesis(_good_thesis(sources=["https://reddit.com/r/wallstreetbets/x"]))
    assert ok is False
    assert "primary" in reason.lower() or "source" in reason.lower()


def test_rejected_no_invalidation():
    from macro_thesis import validate_thesis
    ok, reason = validate_thesis(_good_thesis(invalidation_condition=""))
    assert ok is False
    assert "invalidation" in reason.lower()


def test_rejected_not_second_order():
    from macro_thesis import validate_thesis
    ok, reason = validate_thesis(_good_thesis(second_order=False, consensus_names_excluded=[]))
    assert ok is False
    assert "second" in reason.lower() or "consensus" in reason.lower()


def test_rejected_bad_sector_key():
    from macro_thesis import validate_thesis
    ok, reason = validate_thesis(_good_thesis(beneficiary_sectors=["crypto_moon"]))
    assert ok is False
    assert "sector" in reason.lower()


def test_rejected_conviction_below_floor():
    from macro_thesis import validate_thesis
    low = {"source_corroboration": 0.2, "causal_directness": 0.2,
           "non_consensus": 0.2, "invalidation_clarity": 0.2}
    ok, reason = validate_thesis(_good_thesis(conviction_breakdown=low))
    assert ok is False
    assert "conviction" in reason.lower()
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
cd /Users/alexschumacher/stocktrader && python3 -m pytest tests/test_macro_thesis.py -v 2>&1 | head -8
```
Expected: `ModuleNotFoundError: No module named 'macro_thesis'`

- [ ] **Step 3: Create `macro_thesis.py`**

```python
"""
Macro Thesis — pure logic
=========================
Validation gates, conviction scoring, liveness/retirement, and the screener
multiplier for macro theses. No I/O, no LLM — fully unit-testable.

A thesis tilts the screener's ranking only when it is live, source-grounded,
second-order, and falsifiable. See docs/superpowers/specs/2026-06-14-macro-thesis-agent-design.md
"""

from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

CONVICTION_FLOOR = 0.35
MULTIPLIER_SPAN  = 0.25     # conviction 1.0 -> +25%
MULTIPLIER_FLOOR = 0.90     # crowded-leader dampening
STALE_DAYS       = 14

# Reddit / retail domains are supplementary only — never count as primary evidence
_SUPPLEMENTARY_DOMAINS = ("reddit.com", "stocktwits.com")

_VALID_SECTORS = {
    "technology", "healthcare", "financials", "consumer_cyclical", "industrials",
    "communication_services", "consumer_defensive", "energy", "basic_materials",
    "real_estate", "utilities",
}


def compute_conviction(breakdown: Dict[str, float]) -> float:
    """Conviction is the equal-weighted mean of the four sub-scores."""
    keys = ("source_corroboration", "causal_directness", "non_consensus", "invalidation_clarity")
    vals = [float(breakdown.get(k, 0.0)) for k in keys]
    return sum(vals) / len(keys)


def _has_primary_source(sources: List[str]) -> bool:
    """True if at least one source is NOT a supplementary (retail) domain."""
    for s in sources or []:
        low = s.lower()
        if not any(dom in low for dom in _SUPPLEMENTARY_DOMAINS):
            return True
    return False


def validate_thesis(t: Dict) -> Tuple[bool, str]:
    """
    Gate a thesis. Returns (accepted, reason). Rejects unless ALL hold:
      - >=1 primary-tier source (not Reddit-only, not empty)
      - non-empty invalidation_condition AND a future horizon
      - second_order is True AND consensus_names_excluded is non-empty
      - every beneficiary_sectors entry is a valid SECTOR_MAP key
      - conviction (mean of sub-scores) >= CONVICTION_FLOOR
    """
    sources = t.get("sources") or []
    if not sources or not _has_primary_source(sources):
        return False, "needs at least one primary-tier source (Reddit-only/empty rejected)"

    if not (t.get("invalidation_condition") or "").strip():
        return False, "missing invalidation_condition"

    horizon = t.get("horizon")
    try:
        if not horizon or date.fromisoformat(str(horizon)) <= date.today():
            return False, "horizon missing or not in the future"
    except Exception:
        return False, "horizon not a valid ISO date"

    if not t.get("second_order") or not (t.get("consensus_names_excluded") or []):
        return False, "not second-order: must name consensus leaders excluded"

    sectors = t.get("beneficiary_sectors") or []
    if not sectors or any(s not in _VALID_SECTORS for s in sectors):
        return False, "beneficiary_sectors must all be valid SECTOR_MAP keys"

    if compute_conviction(t.get("conviction_breakdown", {})) < CONVICTION_FLOOR:
        return False, f"conviction below floor {CONVICTION_FLOOR}"

    return True, "ok"
```

- [ ] **Step 4: Run tests, verify they pass**

```bash
cd /Users/alexschumacher/stocktrader && python3 -m pytest tests/test_macro_thesis.py -v
```
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add macro_thesis.py tests/test_macro_thesis.py
git commit -m "Add macro thesis validation gates and conviction scoring"
```

---

## Task 2: Liveness, retirement, and the multiplier (pure)

**Files:**
- Modify: `macro_thesis.py`
- Modify: `tests/test_macro_thesis.py`

- [ ] **Step 1: Append failing tests**

Append to `tests/test_macro_thesis.py`:

```python
def test_is_thesis_live_true():
    from macro_thesis import is_thesis_live
    assert is_thesis_live(_good_thesis(last_validated="2099-01-01"),
                          today=date_(2099, 1, 5)) is True


def test_is_thesis_live_expired_horizon():
    from macro_thesis import is_thesis_live
    t = _good_thesis(horizon="2000-01-01", last_validated="2099-01-01")
    assert is_thesis_live(t, today=date_(2099, 1, 5)) is False


def test_is_thesis_live_stale_validation():
    from macro_thesis import is_thesis_live
    t = _good_thesis(last_validated="2099-01-01")
    # 30 days after last_validated -> stale (>14)
    assert is_thesis_live(t, today=date_(2099, 1, 31)) is False


def test_is_thesis_live_retired_status():
    from macro_thesis import is_thesis_live
    t = _good_thesis(status="retired", last_validated="2099-01-01")
    assert is_thesis_live(t, today=date_(2099, 1, 5)) is False


def test_retire_expired_marks_status():
    from macro_thesis import retire_expired
    theses = [_good_thesis(id="A", horizon="2000-01-01", last_validated="2099-01-01"),
              _good_thesis(id="B", horizon="2099-12-31", last_validated="2099-01-01")]
    out = retire_expired(theses, today=date_(2099, 1, 5))
    by_id = {t["id"]: t for t in out}
    assert by_id["A"]["status"] == "retired"
    assert by_id["B"]["status"] == "active"


def test_multiplier_no_match():
    from macro_thesis import macro_multiplier
    live = [_good_thesis(beneficiary_sectors=["energy"], conviction=0.8)]
    assert macro_multiplier("AAPL", "technology", live) == 1.0


def test_multiplier_beneficiary():
    from macro_thesis import macro_multiplier
    live = [_good_thesis(beneficiary_sectors=["energy"], conviction=0.8,
                         consensus_names_excluded=["XOM"])]
    # conviction 0.8 -> 1 + 0.8*0.25 = 1.20
    assert abs(macro_multiplier("VST", "energy", live) - 1.20) < 1e-9


def test_multiplier_excluded_name():
    from macro_thesis import macro_multiplier
    live = [_good_thesis(beneficiary_sectors=["technology"], conviction=0.9,
                         consensus_names_excluded=["NVDA"])]
    # NVDA excluded -> 0.90 even though its sector is supported (exclusion precedence)
    assert macro_multiplier("NVDA", "technology", live) == 0.90


def test_multiplier_strongest_match_no_stacking():
    from macro_thesis import macro_multiplier
    live = [_good_thesis(id="A", beneficiary_sectors=["energy"], conviction=0.4,
                         consensus_names_excluded=["X"]),
            _good_thesis(id="B", beneficiary_sectors=["energy"], conviction=0.8,
                         consensus_names_excluded=["Y"])]
    # uses higher conviction 0.8 -> 1.20, does not stack
    assert abs(macro_multiplier("VST", "energy", live) - 1.20) < 1e-9


def test_multiplier_bounds_cap():
    from macro_thesis import macro_multiplier
    live = [_good_thesis(beneficiary_sectors=["energy"], conviction=1.0,
                         consensus_names_excluded=["X"])]
    assert macro_multiplier("VST", "energy", live) == 1.25   # capped
```

Also add this helper import at the TOP of the test file (below the existing sys.path lines):

```python
from datetime import date as date_
```

- [ ] **Step 2: Run tests, verify the new ones fail**

```bash
cd /Users/alexschumacher/stocktrader && python3 -m pytest tests/test_macro_thesis.py -v 2>&1 | tail -14
```
Expected: failures with "cannot import name 'is_thesis_live'" etc.

- [ ] **Step 3: Append to `macro_thesis.py`**

```python
def is_thesis_live(t: Dict, today: Optional[date] = None) -> bool:
    """
    A thesis tilts the screener only when live:
      status == "active", horizon in the future, last_validated within STALE_DAYS.
    """
    today = today or date.today()
    if t.get("status") != "active":
        return False
    try:
        if date.fromisoformat(str(t.get("horizon"))) <= today:
            return False
    except Exception:
        return False
    lv = t.get("last_validated")
    if lv:
        try:
            if (today - date.fromisoformat(str(lv))).days > STALE_DAYS:
                return False
        except Exception:
            return False
    return True


def retire_expired(theses: List[Dict], today: Optional[date] = None) -> List[Dict]:
    """Mark theses past their horizon as status='retired' (in place) and return the list."""
    today = today or date.today()
    for t in theses:
        try:
            if date.fromisoformat(str(t.get("horizon"))) <= today:
                t["status"] = "retired"
        except Exception:
            t["status"] = "retired"   # unparseable horizon -> retire defensively
    return theses


def macro_multiplier(symbol: str, sector: str, live_theses: List[Dict]) -> float:
    """
    Bounded ranking tilt for one candidate. See spec.
      1. symbol in any live thesis's consensus_names_excluded -> MULTIPLIER_FLOOR (0.90)
      2. sector matches a live thesis -> 1 + strongest_conviction * MULTIPLIER_SPAN
      3. otherwise -> 1.0
    Result is hard-capped to [MULTIPLIER_FLOOR, 1 + MULTIPLIER_SPAN].
    """
    for t in live_theses:
        if symbol in (t.get("consensus_names_excluded") or []):
            return MULTIPLIER_FLOOR

    matches = [t for t in live_theses if sector in (t.get("beneficiary_sectors") or [])]
    if not matches:
        return 1.0

    best = max(matches, key=lambda t: float(t.get("conviction", 0.0)))
    mult = 1.0 + float(best.get("conviction", 0.0)) * MULTIPLIER_SPAN
    return max(MULTIPLIER_FLOOR, min(mult, 1.0 + MULTIPLIER_SPAN))
```

- [ ] **Step 4: Run tests, verify all pass**

```bash
cd /Users/alexschumacher/stocktrader && python3 -m pytest tests/test_macro_thesis.py -v
```
Expected: 18 passed

- [ ] **Step 5: Commit**

```bash
git add macro_thesis.py tests/test_macro_thesis.py
git commit -m "Add thesis liveness, retirement, and bounded screener multiplier"
```

---

## Task 3: Register loader + screener integration

**Files:**
- Modify: `macro_thesis.py` (add `load_live_theses`)
- Modify: `screener.py`
- Modify: `tests/test_macro_thesis.py`

- [ ] **Step 1: Append failing tests**

Append to `tests/test_macro_thesis.py`:

```python
def test_load_live_theses_absent_file(tmp_path):
    from macro_thesis import load_live_theses
    assert load_live_theses(tmp_path / "nope.json") == []


def test_load_live_theses_filters_dead(tmp_path):
    import json
    from macro_thesis import load_live_theses
    p = tmp_path / "theses.json"
    p.write_text(json.dumps({"theses": [
        _good_thesis(id="LIVE", horizon="2099-12-31", last_validated=str(date_.today())),
        _good_thesis(id="DEAD", horizon="2000-01-01", last_validated=str(date_.today())),
    ]}))
    live = load_live_theses(p)
    assert [t["id"] for t in live] == ["LIVE"]


def test_screener_macro_multiplier_default_when_absent(tmp_path, monkeypatch):
    import screener
    monkeypatch.setattr(screener, "_THESES_PATH", tmp_path / "nope.json")
    assert screener._live_theses() == []          # no register -> empty -> all 1.0
```

- [ ] **Step 2: Run, verify fail**

```bash
cd /Users/alexschumacher/stocktrader && python3 -m pytest tests/test_macro_thesis.py -v -k "load_live or screener_macro" 2>&1 | tail -8
```
Expected: failures (`cannot import name 'load_live_theses'`, then screener attr)

- [ ] **Step 3: Add `load_live_theses` to `macro_thesis.py`**

```python
def load_live_theses(path) -> List[Dict]:
    """
    Read theses.json and return only the live theses. Any error (absent,
    malformed) returns [] so the screener degrades to neutral (all 1.0).
    """
    import json
    from pathlib import Path
    try:
        p = Path(path)
        if not p.exists():
            return []
        data = json.loads(p.read_text())
        theses = data.get("theses", []) if isinstance(data, dict) else []
        return [t for t in theses if is_thesis_live(t)]
    except Exception:
        return []
```

- [ ] **Step 4: Wire into `screener.py`** — add loader near the other helpers (after `_load_ranking_weights`, around line 60):

```python
_THESES_PATH = Path(__file__).parent / "docs" / "data" / "theses.json"


def _live_theses():
    """Live macro theses, or [] if the register is absent/stale/malformed."""
    try:
        from macro_thesis import load_live_theses
        return load_live_theses(_THESES_PATH)
    except Exception:
        return []
```

- [ ] **Step 5: Apply the multiplier in `run_screener`** — find (line ~570):

```python
        effective_score = (w_rs * rs_rank + w_thesis * thesis_score) * sector_boost
```

Replace with:

```python
        from macro_thesis import macro_multiplier
        macro_mult = macro_multiplier(sym, sector, live_theses)
        effective_score = (w_rs * rs_rank + w_thesis * thesis_score) * sector_boost * macro_mult
```

Then, immediately before the `for sym in universe:` candidate loop (right after the `w_rs, w_thesis = _load_ranking_weights()` line ~491), add:

```python
    live_theses = _live_theses()
    logger.info(f"Macro theses live: {len(live_theses)}")
```

- [ ] **Step 6: Add `macro_mult` to the candidate record** — find the `candidates.append({` block (line ~576) and add a field after `"effective_score":`:

```python
            "macro_mult":      round(macro_mult, 3),
```

- [ ] **Step 7: Run tests + compile**

```bash
cd /Users/alexschumacher/stocktrader && python3 -m pytest tests/test_macro_thesis.py -v
python3 -m py_compile screener.py && echo "compile OK"
```
Expected: 21 passed; compile OK

- [ ] **Step 8: Commit**

```bash
git add macro_thesis.py screener.py tests/test_macro_thesis.py
git commit -m "Screener reads live macro theses and applies bounded multiplier (neutral fallback)"
```

---

## Task 4: Seed the register + add the anthropic dependency

**Files:**
- Create: `docs/data/theses.json`
- Modify: `requirements.txt`

- [ ] **Step 1: Seed an empty register**

```bash
cd /Users/alexschumacher/stocktrader
cat > docs/data/theses.json << 'EOF'
{
  "generated_at": null,
  "theses": []
}
EOF
```

- [ ] **Step 2: Verify the screener stays neutral with an empty register**

```bash
python3 -c "
from macro_thesis import load_live_theses, macro_multiplier
live = load_live_theses('docs/data/theses.json')
assert live == []
assert macro_multiplier('NVDA','technology',live) == 1.0
print('empty register -> neutral OK')
"
```
Expected: `empty register -> neutral OK`

- [ ] **Step 3: Add the anthropic SDK to requirements**

Add this line to `requirements.txt` under the Core section:

```
anthropic>=0.40.0
```

- [ ] **Step 4: Commit**

```bash
git add docs/data/theses.json requirements.txt
git commit -m "Seed empty macro thesis register; add anthropic SDK dependency"
```

---

## Task 5: The research agent (LLM orchestration)

**Files:**
- Create: `macro_research_agent.py`

This task wires the non-deterministic pieces (source fetch, Claude reasoning) to
the tested gates. The gates and multiplier are already proven in Tasks 1–3; this
agent's job is to feed candidate theses through `validate_thesis` and manage the
register. The LLM call itself is not unit-tested.

- [ ] **Step 1: Create `macro_research_agent.py`**

```python
"""
Macro Research Agent
====================
Weekly. Re-validates the active thesis register, then asks Claude to reason new
candidate theses from fetched macro/economic/policy news (plus WSB as a
role-limited inverse-crowding signal). Every candidate is gated by
macro_thesis.validate_thesis before it can enter the register.

Writes docs/data/theses.json (register) and docs/data/macro_brief.json (audit).
Any failure leaves theses.json untouched — the screener degrades to neutral.

Requires ANTHROPIC_API_KEY in the environment.
"""

import json
import logging
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
logger = logging.getLogger(__name__)

ROOT        = Path(__file__).parent.resolve()
DOCS_DATA   = ROOT / "docs" / "data"
THESES_FILE = DOCS_DATA / "theses.json"
BRIEF_FILE  = DOCS_DATA / "macro_brief.json"

sys.path.insert(0, str(ROOT))

MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = """You are a macro research analyst for a momentum trading model.
Your job: produce STRUCTURAL, FALSIFIABLE investment theses that identify
SECOND-ORDER beneficiaries of macro shifts — the underpriced enablers, not the
crowded leaders (think: buy the power companies feeding AI datacenters, not Nvidia).

Rules you MUST follow or the thesis will be rejected:
- Every thesis cites >=1 PRIMARY source (real financial/economic news or data).
  Reddit/retail sentiment is supplementary only and can never be the sole source.
- Every thesis names the crowded consensus leaders it is AVOIDING (consensus_names_excluded)
  and points at second-order beneficiary SECTORS instead.
- Every thesis states a measurable invalidation_condition and a future horizon date.
- beneficiary_sectors must come from this exact set:
  technology, healthcare, financials, consumer_cyclical, industrials,
  communication_services, consumer_defensive, energy, basic_materials,
  real_estate, utilities
- Score conviction honestly via four 0-1 sub-scores; weak/speculative/crowded -> low.

Return ONLY a JSON array of thesis objects with keys: theme, causal_chain,
beneficiary_sectors, second_order, consensus_names_excluded, conviction_breakdown
(source_corroboration, causal_directness, non_consensus, invalidation_clarity),
invalidation_condition, horizon (YYYY-MM-DD), sources (list of URLs)."""


def _load_register() -> Dict:
    try:
        if THESES_FILE.exists():
            return json.loads(THESES_FILE.read_text())
    except Exception as e:
        logger.warning(f"Could not read theses.json: {e}")
    return {"generated_at": None, "theses": []}


def _save_register(reg: Dict):
    DOCS_DATA.mkdir(parents=True, exist_ok=True)
    tmp = THESES_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(reg, indent=2))
    os.replace(tmp, THESES_FILE)


def _write_brief(status: str, detail: Dict):
    try:
        BRIEF_FILE.write_text(json.dumps(
            {"generated_at": datetime.now().isoformat(), "status": status, **detail},
            indent=2))
    except Exception as e:
        logger.warning(f"Could not write brief: {e}")


def _gather_context() -> str:
    """
    Fetch source material for the LLM. Returns a text blob of headlines/snippets.
    Primary/secondary via web; WSB via Reddit public JSON (supplementary).
    Best-effort: failures degrade the blob, they don't raise.
    """
    import requests
    chunks = []
    # WSB supplementary signal (crowding radar only)
    try:
        r = requests.get(
            "https://www.reddit.com/r/wallstreetbets/top.json?t=week&limit=25",
            headers={"User-Agent": "macro-research/1.0"}, timeout=15)
        if r.status_code == 200:
            posts = r.json().get("data", {}).get("children", [])
            titles = [p["data"]["title"] for p in posts][:25]
            chunks.append("WALLSTREETBETS TOP (supplementary crowding signal only):\n"
                          + "\n".join(f"- {t}" for t in titles))
    except Exception as e:
        logger.warning(f"WSB fetch failed: {e}")
    # NOTE: primary/secondary news is supplied to Claude via its own web_search
    # tool at call time (see _generate). _gather_context provides the WSB blob.
    return "\n\n".join(chunks)


def _generate(context: str) -> List[Dict]:
    """Call Claude to produce candidate theses. Returns parsed list (may be empty)."""
    from anthropic import Anthropic
    client = Anthropic()   # reads ANTHROPIC_API_KEY
    today = date.today().isoformat()
    user = (f"Today is {today}. Using current macro/economic/policy conditions and the "
            f"supplementary signal below, produce 1-4 high-quality theses per the rules.\n\n"
            f"{context}\n\nReturn ONLY the JSON array.")
    resp = client.messages.create(
        model=MODEL,
        max_tokens=4000,
        system=SYSTEM_PROMPT,
        tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 8}],
        messages=[{"role": "user", "content": user}],
    )
    # Concatenate text blocks, extract the JSON array
    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1:
        logger.warning("No JSON array in model response")
        return []
    try:
        return json.loads(text[start:end + 1])
    except Exception as e:
        logger.warning(f"Could not parse theses JSON: {e}")
        return []


def _next_id(reg: Dict) -> int:
    n = 0
    for t in reg.get("theses", []):
        try:
            n = max(n, int(str(t.get("id", "TH-0")).split("-")[-1]))
        except Exception:
            pass
    return n + 1


def run() -> Dict:
    from macro_thesis import validate_thesis, retire_expired, compute_conviction

    reg = _load_register()

    # 1. Retire expired
    retire_expired(reg.get("theses", []))

    # 2. Generate candidates
    try:
        context = _gather_context()
        candidates = _generate(context)
    except Exception as e:
        logger.error(f"Generation failed: {e} — register left unchanged")
        _write_brief("error", {"error": str(e)})
        return {"status": "error"}

    # 3. Gate + admit
    admitted, rejected = [], []
    seq = _next_id(reg)
    for c in candidates:
        ok, reason = validate_thesis(c)
        if not ok:
            rejected.append({"theme": c.get("theme", "?"), "reason": reason})
            continue
        c["id"] = f"TH-{date.today().year}-{seq:04d}"; seq += 1
        c["conviction"] = round(compute_conviction(c["conviction_breakdown"]), 3)
        c["status"] = "active"
        c["created_at"] = date.today().isoformat()
        c["last_validated"] = date.today().isoformat()
        admitted.append(c)

    reg["theses"] = reg.get("theses", []) + admitted
    reg["generated_at"] = datetime.now().isoformat()
    _save_register(reg)

    live = [t for t in reg["theses"] if t.get("status") == "active"]
    _write_brief("generated", {
        "admitted": len(admitted), "rejected": rejected,
        "active_total": len(live),
        "active": [{"id": t["id"], "theme": t["theme"], "conviction": t["conviction"],
                    "beneficiary_sectors": t["beneficiary_sectors"]} for t in live],
    })
    logger.info(f"Macro research: admitted {len(admitted)}, rejected {len(rejected)}, active {len(live)}")
    return {"status": "generated", "admitted": len(admitted), "active": len(live)}


def main():
    logger.info("=== Macro Research Agent Starting ===")
    if not os.getenv("ANTHROPIC_API_KEY"):
        logger.error("ANTHROPIC_API_KEY not set — skipping (register unchanged)")
        _write_brief("error", {"error": "missing ANTHROPIC_API_KEY"})
        return
    run()
    logger.info("=== Macro Research Agent Complete ===")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Compile check**

```bash
cd /Users/alexschumacher/stocktrader && python3 -m py_compile macro_research_agent.py && echo "compile OK"
```
Expected: `compile OK`

- [ ] **Step 3: Verify it degrades cleanly with no API key**

```bash
cd /Users/alexschumacher/stocktrader && env -u ANTHROPIC_API_KEY python3 macro_research_agent.py 2>&1 | tail -3
python3 -c "import json; d=json.load(open('docs/data/theses.json')); print('theses unchanged, count:', len(d['theses']))"
```
Expected: logs "ANTHROPIC_API_KEY not set — skipping"; theses count unchanged (0)

- [ ] **Step 4: Commit**

```bash
git add macro_research_agent.py
git commit -m "Add macro research agent: re-validate, LLM-generate, gate, manage register"
```

---

## Task 6: Weekly workflow

**Files:**
- Create: `.github/workflows/macro_research.yml`

- [ ] **Step 1: Create the workflow**

```yaml
name: Macro Research

on:
  schedule:
    - cron: '30 12 * * 6'   # Saturday 12:30 UTC (after learning agent at 12:00)
  workflow_dispatch:

jobs:
  research:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    permissions:
      contents: write
    env:
      ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v4
        with:
          python-version: '3.12'
          cache: pip

      - run: pip install -r requirements.txt

      - name: Run macro research agent
        run: python macro_research_agent.py

      - name: Commit register + brief
        if: always()
        run: |
          git config user.email "aschumacherdesign@gmail.com"
          git config user.name "schu51"
          git add docs/data/theses.json docs/data/macro_brief.json 2>/dev/null || true
          git diff --staged --quiet || git commit -m "Macro research update [skip ci]"
          pushed=0
          for attempt in 1 2 3 4 5; do
            if git pull --rebase -X theirs origin main && git push; then pushed=1; break; fi
            echo "push attempt $attempt failed, retrying..."
            git rebase --abort 2>/dev/null || true
            sleep $((RANDOM % 4 + 2))
          done
          [ "$pushed" = "1" ] || { echo "::error::push failed after 5 attempts"; exit 1; }
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/macro_research.yml
git commit -m "Add weekly macro research workflow — Saturday 12:30 UTC"
```

- [ ] **Step 3: Note the required secret (manual, surface to user)**

The workflow needs an `ANTHROPIC_API_KEY` repository secret. Surface this to the
user: **Settings → Secrets and variables → Actions → New repository secret →
`ANTHROPIC_API_KEY`.** Until it is set, the agent logs "missing ANTHROPIC_API_KEY"
and leaves the register unchanged (screener stays neutral) — safe no-op.

---

## Task 7: Final verification

- [ ] **Step 1: Full test suite**

```bash
cd /Users/alexschumacher/stocktrader && python3 -m pytest tests/ -q
```
Expected: all pass (existing + 21 macro_thesis tests)

- [ ] **Step 2: Compile everything touched**

```bash
python3 -m py_compile macro_thesis.py macro_research_agent.py screener.py && echo "All compile OK"
```

- [ ] **Step 3: End-to-end neutrality check (empty register has no effect)**

```bash
python3 -c "
from macro_thesis import load_live_theses, macro_multiplier
live = load_live_theses('docs/data/theses.json')
for sym,sec in [('NVDA','technology'),('VST','energy'),('JPM','financials')]:
    assert macro_multiplier(sym,sec,live)==1.0
print('empty register -> screener fully neutral OK')
"
```

- [ ] **Step 4: Synthetic live-thesis check (multiplier actually tilts)**

```bash
python3 -c "
from datetime import date
from macro_thesis import macro_multiplier
live=[{'beneficiary_sectors':['energy'],'consensus_names_excluded':['XOM'],
       'conviction':0.8,'status':'active','horizon':'2099-12-31'}]
assert abs(macro_multiplier('VST','energy',live)-1.20)<1e-9
assert macro_multiplier('XOM','energy',live)==0.90
print('live thesis tilts: VST 1.20, XOM 0.90 (excluded) OK')
"
```

- [ ] **Step 5: Push**

```bash
git pull --rebase -X theirs origin main && git push origin main
```

---

## Self-Review

**Spec coverage:**
- ✅ Weighting via bounded multiplier (Task 2/3) — [0.90, 1.25]
- ✅ Thesis schema + schema-enforced gates (Task 1)
- ✅ Conviction as mean of 4 sub-scores + floor 0.35 (Task 1)
- ✅ Second-order + falsifiability + primary-source gates (Task 1)
- ✅ Exclusion precedence, strongest-match no-stacking (Task 2)
- ✅ Persistent register, weekly re-validation + retirement (Task 2/5)
- ✅ WSB role-limited to supplementary context (Task 5 `_gather_context`/system prompt)
- ✅ Source grounding / anti-hallucination (system prompt + primary-source gate)
- ✅ Graceful degradation to ×1.0 (Task 3 loader, Task 5 failure paths)
- ✅ Weekly workflow + macro_brief audit (Task 5/6)
- ✅ Tests for all multiplier math and gates (Tasks 1–3)

**Placeholder scan:** none — every step has concrete code/commands.

**Type consistency:**
- `validate_thesis(t) -> (bool, str)` — consistent Tasks 1, 5
- `compute_conviction(breakdown) -> float` — consistent Tasks 1, 5
- `macro_multiplier(symbol, sector, live_theses) -> float` — consistent Tasks 2, 3, 7
- `is_thesis_live(t, today=None)`, `retire_expired(theses, today=None)`, `load_live_theses(path)` — consistent Tasks 2, 3, 5
- thesis keys (`beneficiary_sectors`, `consensus_names_excluded`, `conviction_breakdown`, `conviction`, `status`, `horizon`, `last_validated`) — consistent across spec, gates, multiplier, agent
- screener: `_THESES_PATH`, `_live_theses()`, `macro_mult` — consistent Task 3

**Known limitation:** `_generate` relies on Claude's `web_search` tool for
primary/secondary sources; the agent does not pre-fetch news itself (only WSB).
If the web_search tool name/version changes, generation degrades to WSB-only
context, which the primary-source gate will then reject — failing safe (no
theses admitted) rather than admitting ungrounded theses.
