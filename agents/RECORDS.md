# Agent records (the output contract)

Every specialist writes one record per run to `docs/data/agents/<agent>/<YYYY-MM-DD>.json`, so its
calls can be scored later against what actually happened (see the eval doc). Shape:

    {
      "agent": "research_analyst | performance_risk | thesis_strategy | big_mover_review",
      "date": "YYYY-MM-DD",
      "generated_at": "ISO time",
      "status": "green | amber | red",
      "headline": "one sentence: the single most important thing",
      "calls": [
        {"subject": "NVDA" | "theme:ai_power" | "thesis:TH-2026-0003",
         "direction": "bullish | bearish | neutral | broken | intact",
         "horizon_days": 5,
         "confidence": "low | medium | high",
         "evidence": ["file:field or event id the call rests on"],
         "why": "one sentence"}
      ],
      "notes": ["anything else worth the manager's attention"]
    }

A call without a horizon cannot be scored, so it does not count. Commit message:
`Agent record <agent> <date> [skip ci]`. A record touches only its own folder.
