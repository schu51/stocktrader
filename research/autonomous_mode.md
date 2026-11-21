# Autonomous mode — spec

Written 2026-11-21. Owner: Alex. Status: proposed; step 1 is in this PR.

## Goal

The bot trades on its own, end to end: it finds, sizes, buys, protects and
sells positions with no human in the loop for any trade. Alex owns the
**rules**, not the trades: a rule changes only through a pull request he
merges, as today (lab → challengers → five-eval streak → PR).

## What is already autonomous

- Execution: buys at the 9:15 opening auction (`daily_open.yml`), position
  management at 10:00 (`daily_trade.yml`), stops placed and ratcheted by
  `stop_placement.py` and `exit_logic.py`, intraday exits, the diamond sleeve
  (`diamond_sleeve.py`). `require_confirmation = False`.
- Risk: 3% daily loss limit halts new buys; drawdown throttle shrinks buys
  from 10% down; SPY 2% market filter; sector cap 35%; earnings blackout.
- Learning: daily eval tournament (`daily_eval.py`) and the picker lab
  (`picker_lab.py`), with promotion gates, walk-forward folds and rollback
  alerts.

So "autonomous" is not a new mode to bolt on. It is a change in **what the
bot trades on and how it manages risk**, so that hands-off trading has a
real edge rather than a crowded one.

## Design principle: agents read, rules decide, code executes

- **LLM agents read.** They turn messy documents (contract announcements,
  10-Qs, budget exhibits, news) into structured fields: amounts, dates,
  counterparties, a first profitable quarter, a thesis score. They never
  place orders and never choose a position size.
- **Tested rules decide.** Every buy, size and exit comes from a written rule
  that has passed the lab and the tournament on a survivorship-free replay
  after costs.
- **Code executes** through the existing broker layer and stop sweep.

Why: in a 2026 benchmark, LLM trading agents followed risk instructions
almost exactly (a 30% cash rule cut equity exposure by 30.3 points) but their
stock picks ranked at the 54th percentile against random same-sector swaps,
and no agent had significant alpha (Wang, arXiv 2611.19530, preprint). LLM
agents trading live in Nof1's Alpha Arena US-stock season lost about a third
of their capital. Headline-sentiment LLM strategies decayed from a Sharpe of
6.5 to 1.2 within three years as they spread (Lopez-Lira & Tang).

## Sleeves

Each sleeve is a separate book with its own budget, rules and tracker, like
the diamond sleeve today. Order = rollout order.

### 1. Momentum core, crash-managed (step 1, this PR)

The existing large-cap momentum strategy, with buys scaled by the momentum
factor's own volatility instead of only switched on/off by SPY's trend.

- Evidence: scaling momentum by forecast volatility roughly doubles its
  Sharpe and removes most crashes (Barroso & Santa-Clara 2015; Daniel &
  Moskowitz 2016). Momentum had its worst month on record in several regions
  in July 2026; crowded systematic books have unwound four times since mid-2025.
- Built: `backtest.momentum_factor_returns`, `mom_vol_*` settings, lab
  round 7 (`research/picker_lab/round7.json`). Off by default.
- Next if it survives: graduate one setting (`mom_vol_target`) to
  `research/challengers.json`; on promotion, a live PR computes the same factor
  from the screener's daily bars in `run_daily_analysis.py` and applies the
  scale where buys are sized.
- Later option: trim existing holdings, not just new buys, when the scale
  drops (closer to the papers, more turnover).

### 2. Contract-award sleeve (research first)

Buys small and mid caps after a material, funded U.S. defense contract award.

- Evidence: across 1,477 federal awards 2001–2017, awards worth 10%+ of the
  firm's prior-year sales earned about 1.8% around announcement, with DoD
  awards drifting positive to about 50 days after; the effect scales with
  award size relative to the firm (Ferris, Houston & Rayfield 2024). About a
  quarter of U.S. listed companies have no analyst coverage. Old sample, small
  per-trade edge: an event study must confirm it on 2018+ data before any
  trading code.
- Data: DoD announces awards over $9M each business day (defense.gov
  contracts page; DFARS 205.303); USASpending.gov for history and obligations;
  SEC XBRL revenue via `fundamentals_pit.py`.
- Reader agent: per announcement, extract awardee, parent company and ticker,
  face value, amount obligated at award, contract type (new award,
  modification, option exercise, IDIQ ceiling), period of performance.
- Rule (to be tested): funded amount ÷ trailing-12-month revenue ≥ 10%
  (sensitivity 5% / 20%); market cap under $10B; buy at the next open; hold
  10 / 20 / 35 sessions; disaster stop. IDIQ ceilings and unfunded options do
  not count.
- Gate: event study of abnormal return after announcement by materiality
  bucket, 200+ qualifying events, then a lab round.

### 3. Forced-selling rebound overlay (lab round)

When rule-driven sellers (volatility-targeting funds, CTAs, risk-limited
quants) finish de-risking, buy what they sold and hold days to weeks.

- Evidence: vol-control funds and CTAs (~$1tn) sold ~$108bn in March–April
  2026, with Nomura modelling ~$20bn of buying back once volatility eased;
  squeezed factors in summer 2025 and momentum after July 2026 bounced within
  days to weeks. No tested retail rule exists; this must be built and tested.
- Rule (to be tested): VIX closed at 30+ within the last 10 sessions and has
  fallen 25%+ from its 10-day high, SPY above its 5-day low; buy the
  screener's top names that fell most from their 20-day high; hold 10–20
  sessions; paid for from cash the SPY filter left idle. `^VIX` is already in
  the replay's market data.

### 4. Diamond sleeve on event time (change to an existing sleeve)

Keep the sleeve and its judge, but time entries to the filing that shows the
turn to profit, not to the chart.

- Evidence: anomalies earn ~9.8% annualised in the first month after the
  information is released vs ~2.0% after four months (Bowles et al., JF 2024).
  About 40% of the Russell 2000 has no earnings; small value beat small
  growth by ~8.7 points over the past year.
- Rule (to be tested): buy within 5 sessions of the 10-Q/10-K whose quarter
  is the first profitable one after 4+ losing quarters (from
  `fundamentals_pit.py`), judge score and 200-day rule as today.
- Gate before the budget steps from 40% toward 80%: beat a cost-adjusted
  small-cap value benchmark (IWN) at 13 weeks, not just the small-cap index.

## Guardrails for unattended trading

1. **Restricted list (do next, small PR).** Symbols the bot may never trade,
   read from a repository secret (`RESTRICTED_SYMBOLS`, comma-separated) and
   never committed: this repository is public. Enforced in the broker layer
   before every buy and in the screener, so no agent or rule can override it.
2. **Agents cannot trade.** Only rule code submits orders; agent output is data.
3. **Liquidity cap.** No order above 1% of the stock's 20-day average dollar
   volume (sleeves 2–4 trade smaller names).
4. **Kill switches.** Keep the 3% daily loss halt and the drawdown throttle.
   Add: a broker or data anomaly (fill 3%+ from the expected price, a position
   the records do not know) pauses new buys and opens an issue.
5. **Long only, no leverage, no options** until a sleeve has a paper record.
6. **Paper first.** A sleeve trades live money only after its tracker beats its
   benchmark after costs on paper, starting with a small allocation.
7. **Every change is logged and counted.** Each rule the agents propose is
   another test: judge survivors with walk-forward folds and a deflated Sharpe
   ratio; test any LLM-scored signal only on data after that model's training
   cutoff.

## Success metrics

The bar is the one the tournament already uses: beat SPY after costs with a
smaller drawdown, on a three-year survivorship-free replay and in recent
folds. Top multi-strategy funds returned 10–34% in 2025; Medallion has
compounded ~2.8% a month net. A backtest showing 20%+ a month should be
treated as overfitting or hidden tail risk.

## Rollout

| Step | What | Changes live rules? |
|---|---|---|
| 1 | Momentum-volatility scaling: lab round 7 (this PR) | No |
| 2 | Restricted list guardrail | Yes (adds a block only) |
| 3 | Contract-award event study | No |
| 4 | Rebound overlay lab round | No |
| 5 | Diamond event-time entry lab round | No |
| 6+ | Graduate survivors through challengers → PR | Yes, one at a time |
