# Picker & filter lab

Started 2026-11-17 to find out why the stock picker and the SPY market filter
have not been working, and to build better versions, one evidence-backed
round at a time.

## Faults found so far (2026-11-17)

From `research/candidate_outcomes.csv` (3,314 screener candidates,
2026-06-03 → 2026-11-13, raw N-day returns, not the bot's exits) and
`eval_latest.json`:

- **RS rank works backwards in the recent regime.** 20-day return by RS rank:
  below 90 −0.5%, 90–94 −3.5%, 95–98 −9.4%, 99 −15.3%. The picker ranks on
  80% RS, so it buys the most extended names first.
- **The thesis score carries the signal.** Thesis 55+ +0.4% vs under 45 −7.1%
  over 20 days. The top 3 by thesis made +1.7%; the top 3 by the live ranking
  lost −4.7%.
- **The leader-sector boost hurts.** Sector leaders −6.2% vs others −1.9%.
- **The SPY filter guards the wrong risk.** Candidates lost money in every
  SPY regime. They did least badly when SPY was *below* its 50-day average.
  The losses came from momentum reversing, which SPY's trend does not detect.
- **But the 3-year backtest disagrees in part.** RS-led weights beat
  thesis-led ones there (60/40 PF 1.77 vs 10/90 PF 1.37, 520-name universe),
  and stricter SPY filters raised PF (spy1 1.21 → spy2 1.41 → spy3 1.71). The
  champion's half-year folds: 1.86, 1.30, 0.54, 2.48, 1.05, **0.39** (latest).
  The edge has worked over three years and broken in the last six months.
- **Reporting:** with the filter on, the run summary reports "0 candidates
  screened", which hides the reason there are no buys.

So the question is not "RS or thesis" but **what holds up in both regimes**.
Every variant is therefore judged on the full three years *and* on the two
most recent folds.

## The loop

1. Write a round in `round<N>.json`: one question, variants that each test
   one fault (later rounds combine the survivors).
2. Run `picker_lab.yml` (Actions → Picker Lab → Run workflow, file + tag).
   It replays the live rules and every variant and writes
   `results/<date>_<tag>.json|md`.
3. Read the results. Kill what fails the gates or the recent folds. Combine
   or refine what survives into the next round. Record the reasoning in this
   file's log.
4. A variant that survives two rounds graduates to `research/challengers.json`
   (at most two changed settings) to earn promotion through the daily
   tournament's five-day streak and forward window. Live rules change only via
   a PR Alex merges.

Guard rails: three-year survivorship-free replay, 10 bps slippage per side,
judged on gates + walk-forward folds + recent folds + PF without the 5 best
trades. Avoid fitting to the last five months alone.

## Log

- 2026-11-17 — Round 1 written: filter alternatives (none, SPY 3%, momentum
  health, breadth) and picker fixes (weights, RS cap, no sector boost, thesis
  floor, extension cap), each alone, plus two combinations.
