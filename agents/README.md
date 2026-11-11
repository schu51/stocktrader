# Agent instructions

The prompts for the scheduled tasks on Alex's Claude account. They are versioned here so a
change goes through a pull request. Each task reads from and writes to the repo only. No task
places orders, merges pull requests, or writes trading data. Model answers reach trading data
only through the repo's own validation (apply_replies.yml).

| Task | Schedule (ET) | Reads | Writes |
| --- | --- | --- | --- |
| [Macro research analyst](macro_research_analyst.md) | Sat 09:23 | research/requests/macro_research.json | research/replies/macro_research.json |
| [Diamond judge](diamond_judge.md) | Sat 08:41 | research/requests/diamond_judge.json | research/replies/diamond_judge.json |
| [Big mover review](big_mover_review.md) | weekdays 17:13 | docs/data/big_movers.json | research/big_mover_reads.json |
| OSINT morning brief (existing) | weekdays 08:42 | docs/data/osint_latest.json | push notification only |
| Pre-market brief, post-market review (existing) | weekdays | docs/data/* | push notification only |
| Daily eval & iterate (existing) | weekdays 17:37 | eval files | research/challengers.json, research/eval_journal.md |
