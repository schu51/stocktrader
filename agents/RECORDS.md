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
      "notes": ["anything else worth the manager's attention"],
      "recommendations": [
        {"id": "REC-<agent>-<YYYYMMDD>-<n>",
         "title": "short name",
         "change": "what to change, concretely",
         "evidence": ["file:field"], "sample": "e.g. 18 closed trades",
         "test": "how to check it before it goes live (backtest flag, challenger)",
         "priority": "high | medium | low"}
      ]
    }

Recommendations are optional and go to the manager, which gives each a verdict (test / propose /
needs_data / done / reject) and keeps the ledger in its own record under "recommendations" as
{id, from, title, verdict, why, next, first_seen, updated}. The manager and Alex make the final
call; a live rule changes only through a pull request Alex merges.

A call without a horizon cannot be scored, so it does not count. Commit message:
`Agent record <agent> <date> [skip ci]`. A record touches only its own folder.
