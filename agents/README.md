# Agent instructions

The prompts for the scheduled tasks on Alex's Claude account. They are versioned here so a
change goes through a pull request. Each task reads from and writes to the repo only. No task
places orders, merges pull requests, or writes trading data. Model answers reach trading data
only through the repo's own validation (apply_replies.yml).

The team: four specialists and a manager. Trading stays in code. The research analyst sends the
morning message; the manager sends the evening one; everyone else writes a record
([RECORDS.md](RECORDS.md)) for the manager.

| Task | Schedule (ET) | Reads | Writes |
| --- | --- | --- | --- |
| [Research analyst](research_analyst.md) | weekdays 09:22 | OSINT, premarket, screener, options, big movers | its record + the morning push |
| [Performance & risk](performance_risk.md) | weekdays 16:41 | reconcile, trades, attribution, decisions | its record |
| [Thesis & strategy](thesis_strategy.md) | weekdays 17:37 | daily eval, attribution, theses | challengers, eval journal, its record |
| [Manager](manager.md) | weekdays 18:07, Sat 12:07 | every record + ops status | its record + the evening push |
| [Macro research analyst](macro_research_analyst.md) | Sat 09:23 | research/requests/macro_research.json | research/replies/macro_research.json |
| [Diamond judge](diamond_judge.md) | Sat 08:41 | research/requests/diamond_judge.json | research/replies/diamond_judge.json |
| [Big mover review](big_mover_review.md) | weekdays 17:13 | docs/data/big_movers.json | research/big_mover_reads.json |
