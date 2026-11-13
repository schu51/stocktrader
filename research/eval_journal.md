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

## 2026-11-12 — evening review
- First day of the 200-day exit live (0 closed trades under it). Live: last 20 trades 15% win, PF 0.11; 30d 36 trades, 22% win, PF 0.31, -7.9% vs SPY -2.7%; -10.6% from peak. Tracking gap widened back to -2.1pt (backtest -5.7%), but on the 18 matched trades live still beat the backtest (-1.0% vs -2.1%): period/rules, not execution. Stops unchanged (10 fills, median -0.6%, VCYT outlier).
- Crossovers vs champion (sma200: PF 2.67, DD -13.2%, win 65%, 600 trades): death_cross_exit PF 2.69 / DD -13.1% / 597 trades, a near-copy that cannot clear the +0.1 PF gate; sma50_exit PF 2.30 / DD -13.1% / win 60% on 662 trades (62 extra trades = the shorter holds the 200-day exit removes); golden20 PF 4.21 / DD -3.3% but 72 trades, 7.4%/yr. The eval still does not report the share closed within 3 days per challenger; live under the 50-day exit it was 27 of 91 (0% won, -$4,556).
- Tournament: all streaks 0/5. spy3 closest (PF 3.11, DD -10.5%) but its last 6 months slid -2.6% → -3.8% → -5.5% over 3 evals and annual return is 2.6pt under the champion. be3 fails PF only by margin (2.52).
- No change: 3 eval rows, nothing near the 10-eval retire rule, no errors, list full at 11. The live entry-distance finding (bought 15%+ above the 50-day won 32% vs 15%) was measured under the old 50-day exit, so it is confounded and not yet a basis for a challenger. grace5 stays the slot to free when a clean weakness appears.
- Theses: all 8 last validated 2026-10-31 (12 days); the Saturday macro analyst (2026-11-14) is the revalidation path, and the 2026-11-07 macro_brief error is the retired API path. Expect to learn: whether trades under the 200-day exit stop closing within 3 days, and whether spy3's recent slide continues.

## 2026-11-13 — evening review (baseline break)
- **Break in eval_history:** PR #48 (merged 2026-11-12 23:00 UTC) made every backtest survivorship-free on a whole-market $50M+/day universe. Champion (sma200_exit) fell from PF 2.67 / 33%/yr / DD -13.2% / 600 trades to PF 1.39 / 15.9%/yr / DD -18.2% / 883 trades. The rules did not change; the old baseline was inflated. Rows before 2026-11-13 are not comparable, so retire streaks and trend start from today. Against SPY: PR #48 puts SPY at ~21%/yr over the same 3 years, so the champion now trails buy-and-hold in backtest.
- Crossovers vs champion (PF 1.39, DD -18.2%, win 59%, 883 trades): death_cross_exit is still a copy (882 trades, PF 1.39, DD -18.2%); sma50_exit now edges the 200-day exit (PF 1.40, DD -17.5%, 16.5%/yr, 923 trades) but wins 56% vs 59%, so the 200-day exit's edge largely disappeared without survivorship bias; golden20 PF 1.63 / DD -6.5% but 74 trades, 2.4%/yr. Share closed within 3 days is still not in the eval output (live under the 50-day exit: 27 of 91, 0% won, -$4,556).
- Tournament: spy3 is the only challenger clearing every gate (PF 1.69, 19.5%/yr, DD -18.2%, worst month -5.9%), streak 1/5. risk40 is next (PF 1.47, DD -17.4%, fails PF gain only). Live: last 20 trades 15% win, PF 0.11; 30d 35 trades PF 0.28, -7.3% vs SPY -3.0%; -10.5% from peak; 0 trades under current rules. Tracking gap -5.2pt, but tonight's backtest is the new universe while trade_attribution's same-window and matched (live -1.0% vs backtest -2.1% on 18 trades) are from the old one, so the gap cannot be split into period vs execution yet.
- No change to challengers.json: one comparable eval row, no retire criterion, no errors, list full at 11. Weak-market losses (neutral regime: 14 trades, -6.5% avg) are already what spy3 tests; gapped stops are 7 trades, under the 10-trade bar.
- Theses: all 8 last validated 2026-10-31 (13 days); the Saturday macro analyst (2026-11-14) is the revalidation path; the 2026-11-07 macro_brief error is the retired API path. Expect to learn: whether spy3 holds all gates for 5 evals on the clean baseline, and whether sma50_exit vs sma200 stays a wash.
