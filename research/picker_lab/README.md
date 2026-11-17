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

## Data the picker has (and lacks)

Until round 3 the picker saw only price and volume: RS is past returns, and
the backtest's thesis score is price/volume too (earnings are off in replays).
The live thesis score does include an earnings check, which may be why it
picked better live than in backtests. Round 3 adds point-in-time fundamentals
(`fundamentals_pit.py`): quarterly EPS and revenue from SEC XBRL frames, each
usable only from its earnings report date (or 60 days after quarter end), so
the replay sees what was public that day. Signals: earnings surprise (SUE),
EPS and revenue growth, revenue acceleration, days since the report.

## Round 6 sources (momentum and swing-trading research, 2026-11-17)

- Earnings surprise + the 3-day announcement return: long-short ~12.5%/yr
  combined (Brandt et al., via Quantpedia "Post-Earnings Announcement Effect").
- Price and earnings momentum each add; winners without earnings support
  reverse more (Chan, Jegadeesh & Lakonishok 1996).
- Nearness to the 52-week high beats past-return momentum and does not
  reverse (George & Hwang 2004).
- Momentum from many small moves persists more than from a few jumps
  ("frog in the pan", Da, Gurun & Warachka).
- Fundamental momentum (profitability trends) lasts 12-18 months vs ~4 for
  price momentum; combined beats either ("Twin Momentum").
- Momentum earns nearly all its premium overnight: 0.95%/month vs 0.11%
  intraday (Lou, Polk & Skouras 2019).
- Volatility-scaled momentum nearly eliminates crashes and roughly doubles
  the Sharpe ratio (Barroso & Santa-Clara; Daniel & Moskowitz).
- Practitioners: Minervini's VCP (shrinking pullbacks, volume dry-up, pivot
  breakout, stop under the last contraction); Kullamägi's breakouts and
  episodic pivots (gap on heavy volume after an earnings beat); a systematic
  replication of his breakouts: CAGR 19%, max DD -21%, 2,382 trades since
  2007, with tight stops and 3-day exits both underperforming.
- CAN SLIM, point-in-time 1999-2023: 23.5%/yr vs 6.9% for the S&P 500,
  before costs.

## Log

- 2026-11-17 — Round 1 written: filter alternatives (none, SPY 3%, momentum
  health, breadth) and picker fixes (weights, RS cap, no sector boost, thesis
  floor, extension cap), each alone, plus two combinations.
- 2026-11-17 — Round 2 (price-only signal and entry): RS measured as of a
  month ago and textbook 12-1 momentum; breakout, pullback, extension-capped
  and tight-base entries; combinations. Motivation: the live RS formula puts
  40% weight on the last quarter including last month, the window where
  winners tend to reverse, and the bot buys the top of the list at the open
  rather than at a defined entry (27 trades closed within 3 days, 0% won).
- 2026-11-17 — Round 3 (fundamentals): SUE, revenue growth, post-earnings
  drift window, and fundamentals in the rank, alone and with 12-1 momentum.
- 2026-11-17 — Round 1 results (champion PF 1.39, CAGR +15.1%, DD -16.9%,
  last folds 1.05 / 0.35). The SPY filter is NOT the fault: turning it off was
  the worst variant (PF 1.20, DD -23.0%) and momentum-health and breadth
  replacements were all worse; SPY 3% raised PF to 1.65 but lost the latest
  folds (0.40 / 0.52). Picker: skipping RS 99 (p_cap98) won, PF 1.64, CAGR
  +19.2%, DD -15.3%, folds 2.29 / 0.66, 9/10 gates. Shifting weight to the
  (price-only) thesis score lost (PF 1.28-1.38). Dropping the sector boost:
  mixed (PF 1.45, DD -20.2%).
- 2026-11-17 — Round 2 results. Tight-base boost (e_tight) passed 10/10
  gates: PF 1.56, CAGR +19.2%, folds 2.02 / 0.50. Textbook 12-1 momentum and
  skip-month RS lost (PF 1.18 / 1.28). Breakout entries: too few trades (97-152)
  and the latest folds collapsed. Pullback and 2-ATR extension cap: no gain.
- 2026-11-17 — Round 3 results (fundamentals). Requiring the latest quarter's
  EPS to be at least the year-ago quarter's (u_sue0) is the best result so
  far: PF 1.86, CAGR +28.2%, DD -13.7%, folds 3.09 / 0.68, 5/6 folds won,
  10/10 gates, PF without the 5 best trades 1.47 (champion 1.17). Fundamentals
  as a rank boost (w_fund) helped less; post-earnings window alone did not
  clear the PF gate; dropping the SPY filter still hurt. CAVEAT: tickers come
  from today's SEC list, so delisted names have no data and any fundamentals
  filter also drops them. Round 4 adds a control (has data, no condition)
  to separate the earnings signal from that bias.
- 2026-11-17 — Round 4 written: controls, combinations of the three winners,
  and sensitivity nudges on each setting.
- 2026-11-17 — Round 4 results (champion PF 1.42, CAGR +16.5%, DD -16.8%,
  folds 1.07 / 0.36). The data-coverage bias is real and sometimes large: the
  has-data universe alone took the live picker from PF 1.42 to 1.48, but took
  cap98+tight from 1.76 (all names) to 2.14. So earnings variants must be
  compared with their has-data twins, not with the champion.
  On that footing: EPS growth (u_sue0, 1.84) beats the has-data base (1.48),
  a real signal; but added to cap98+tight it did not help (1.87 vs 2.14).
  The surprise floor is a cliff: -0.25 gives 1.44, 0 gives 1.84, +0.25 1.78
  (EPS not falling looks like the natural line, but noted as fragile).
  Best risk profile: EPS growth + cap98 + tight + SPY 3% (PF 2.51, DD -10.9%,
  the only variant with both recent folds above 1: 5.66 / 1.35), but it is
  four settings and carries the bias. Bias-free price-only winner: cap98 +
  tight (PF 1.76, CAGR +21.9%, DD -15.6%, folds 2.48 / 0.66, 9/10 gates;
  failed only first-half PF). Sensitivity neighbours (tight 0.3/1.0, cap 97)
  stay above the champion.
- 2026-11-17 — Decision: cap98_tight goes to the daily tournament (two
  settings, no data bias), replacing grace5, which replays identically to the
  champion and so measures nothing. Round 5 re-runs the earnings ideas on the
  has-data universe so each is judged against its fair twin.
- 2026-11-17 — Round 6 written from the research above: 52-week-high
  closeness, steady climb, earnings reaction (alone and with surprise), gross
  margin trend; VCP and earnings-gap entries; buying at the close;
  volatility-scaled sizing. Each alone and on top of cap98_tight.
