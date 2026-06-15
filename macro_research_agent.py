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
    Primary/secondary via Claude's web_search at call time; WSB via Reddit public
    JSON (supplementary). Best-effort: failures degrade the blob, they don't raise.
    """
    import requests
    chunks = []
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
