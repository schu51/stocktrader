# Swing post vs the live momentum rules — findings (2026-11-12)

Source: r/swingtrading, "How Trading Really Works, part 3" (u/30RITUALS). Full table:
`research/swing_post_study.md` (three-year replay from 2023-11-12, 520 tickers, live costs;
`python swing_post_study.py`). Live rules: +32.8% a year, max drawdown -12.4%, 589 positions,
65% won, profit factor 2.72.

**No idea from the post clears every promotion gate.** Two come close (7 of 8 gates):

- **breadth50** (no buys unless half the universe is above its 50-day average): +34.6% a year,
  profit factor 2.89, same drawdown. Fails only the last-6-months gate (-4.8% vs -2.8%). The gate
  fired on just 18 days: the live SPY filter already keeps the bot out on most weak days.
- **adr4_8** (only stocks moving 4-8% a day on average): +24.6% a year with only 26% of the
  account invested (live: 60%), drawdown -9.3% vs -12.4%, profit factor 2.93, average win 17.1%.
  Fails on annual return because two-thirds of the cash sits idle. Worth testing as a rank boost
  or with larger positions rather than as a hard filter.

**Hurt:**
- **swing_exit** (sell a third at +1R, trail the rest on the 20-day EMA): +23.2% a year, profit
  factor 1.85. Average win falls from 13.4% to 9.4% — it sells the big winners too early. Same with
  half off at 1R.
- **tight30** (strong tight-base rank boost): +23.4%, PF 1.99. tight15 is neutral (+32.7%, PF 2.43).
- **vix15** (no buys with the VIX at 15 or above): blocked 222 days, +16.6% a year (2025: +4.3%).
  vix20 is neutral.
- **post_all** (every idea together at the post's numbers): 12 positions in three years. The rules
  almost never line up on this universe.
- **post_lite** (tight30 + ext3 + swing_exit + qqq20): +14.7%, PF 1.47.

**Neutral:** ext3 (fired 67 times; the live RSI and Bollinger gates already block stretched names),
qqq20 (19 days), vix20 (19 days).

Next: run breadth50 and an ADR rank boost as daily-eval challengers.
