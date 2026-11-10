# Eval journal

One dated entry per evening from the "Stocktrader daily eval & iterate" review:
what the daily eval showed, what changed in research/challengers.json and why.

## 2026-11-10 — loop started
- Live rules now: break-even stop from +5%, no new buys unless SPY is 2% above its 50-day average (rules_versions: breakeven5_spy2).
- First eval: live rules backtest 60% win rate, PF 2.32, 31.0%/yr, max DD -13.1%; live account -11.5% from peak, last 20 trades 15% win rate (old rules).
- Six challengers seeded from the 2026-11-10 research rounds (be3, trail20, spy3, spy1, earn_exit15, risk40). None clears the gates yet; spy3 is closest (PF 2.84, DD -10.1%) but fails on the last 6 months and the second half.
- Watch: whether live trades under the new rules track their backtest, and whether stop exits fill nearer their stops.

## 2026-11-10 — evening review
- Eval ran; new rules (breakeven5_spy2) have 0 closed trades yet, so nothing to judge live. Last 20 trades (old rules) 15% win, PF 0.11; 30d account -8.0% vs SPY -3.7%, -11.8% from peak.
- Stops: mean fill -3.5% below stop but median -0.6%; one outlier (VCYT -24.6%) drives it, 2 of 10 gapped >2%. Only 10 fills, too few to call a pattern beyond earnings-style gaps (earn_exit15 already tests that).
- Tracking gap live vs backtest -2.3% over 30d: worth watching, not yet alarming.
- Tournament: champion PF 2.32 / DD -13.1%; spy3 best (PF 2.84, DD -10.1%) but fails recent + second-half PF; all streaks 0/5.
- No change: list is at max (8), and no challenger has the history to retire. pullback and grace5 (committed 21:44 UTC) did not appear in tonight's eval; check they score tomorrow, and count it as an error if they are missing again.
