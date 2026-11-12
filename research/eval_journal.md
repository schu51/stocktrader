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

## 2026-11-11 — evening review
- The 200-day exit (PR #38/#39) is now the champion: PF 2.70, DD -13.3%, win 64%, 33.4%/yr. Of the crossover tests, death_cross_exit is a near-copy (597 trades, PF 2.63, DD -13.3%), the old sma50_exit is clearly worse (PF 2.29, win 60%, same DD), and golden20 has PF 4.15 / DD -3.3% but only 72 trades and 7.4%/yr (fails enough_trades). The eval does not report the share of backtest trades closed within 3 days; live under the 50-day exit it was 27 of 91 (0% won, -$4,556). grace5 now scores identical to the champion, which says the 200-day exit almost never fires in the first 5 sessions.
- Live: last 20 trades 15% win, PF 0.11; 30d -7.1% vs SPY -2.4%, -11.0% from peak. Tracking gap narrowed to -0.5pt (backtest -6.6%); on the 18 matched trades live beat the backtest (-1.0% vs -2.1%), so the gap is the rules/period, not execution. Stops: mean slip -3.5%, median -0.6%, VCYT outlier; 10 fills, too few to call.
- Changed: added rules_versions "sma200_exit" since 2026-11-12, so the current-rules window restarts with the exit change. No retire/add: only 2 eval rows, list full at 11. grace5 is now a no-op and the first candidate to replace once a named weakness needs the slot.
- Theses last validated 2026-10-31; revalidation now comes from the Saturday macro research analyst (first run 2026-11-14), not the API. The macro_brief error dated 2026-11-07 is the retired API path, not a fault. Expect to learn: whether live 3-day exits drop under the 200-day line, and whether spy3's edge (PF 3.04, DD -10.6%) survives its weak recent period.
