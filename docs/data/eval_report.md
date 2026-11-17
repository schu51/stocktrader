# Daily eval — 2026-11-17

Rules live: **sma200_exit** since 2026-11-12

**Alerts**

- Account is 10.7% below its peak
- Stop exits filled 3.6% below their stop on average (last 10)

## Live trades

| window | trades | win rate | profit factor | avg trade | P&L |
|---|---|---|---|---|---|
| closed today | 1 | 0% | 0.0 | -0.40% | $-10 |
| last 20 trades | 20 | 10% | 0.16 | -6.18% | $-3,608 |
| last 30 days | 22 | 18% | 0.26 | -4.87% | $-3,190 |
| current rules | 0 | | | | |
| all time | 93 | 20% | 0.66 | -1.21% | $-4,129 |

Account $106,495, -10.7% from peak. 30 days: -4.3% vs SPY -4.0%. Since current rules: -0.5% vs SPY -3.6%.

Stop exits (last 10): filled -3.6% vs their stop on average, median -0.8%, worst -24.6% (VCYT), 2 gapped more than 2%. Stop known from the filled order for 1 of 10; the rest rebuilt from the entry rule.

Tracking (30 days): live -4.3% vs backtest of the same rules -0.8%.

## Tournament (3-year backtest)

| rules | trades | win rate | PF | annual | max DD | worst month | last 6 mo | streak |
|---|---|---|---|---|---|---|---|---|
| **live** | 872 | 59% | 1.42 | 16.6% | -16.8% | -8.6% | -9.4% | |
| be3 | 1018 | 65% | 1.31 | 11.4% | -19.6% | -8.1% | -7.2% | 0/5 (fails: profit_factor, annual_return, drawdown, pf_second_half, walk_forward, not_concentrated, forward: 0 trades / 6 days so far) |
| trail20 | 950 | 59% | 1.35 | 13.8% | -17.2% | -8.1% | -9.0% | 0/5 (fails: profit_factor, annual_return, pf_first_half, walk_forward, not_concentrated, forward: 0 trades / 6 days so far) |
| spy3 | 643 | 62% | 1.68 | 18.5% | -17.1% | -5.9% | -6.5% | 0/5 (fails: forward: 0 trades / 6 days so far) |
| spy1 | 1113 | 57% | 1.21 | 9.5% | -22.8% | -8.2% | -12.7% | 0/5 (fails: profit_factor, annual_return, drawdown, win_rate, recent, pf_first_half, pf_second_half, walk_forward, not_concentrated, forward: 0 trades / 6 days so far) |
| earn_exit15 | 940 | 60% | 1.28 | 11.3% | -17.5% | -8.5% | -7.6% | 0/5 (fails: profit_factor, annual_return, pf_first_half, pf_second_half, walk_forward, not_concentrated, forward: 0 trades / 6 days so far) |
| risk40 | 878 | 59% | 1.49 | 17.5% | -16.1% | -7.6% | -9.0% | 0/5 (fails: profit_factor, forward: 0 trades / 6 days so far) |
| pullback | 950 | 59% | 1.29 | 11.0% | -19.0% | -8.2% | -9.2% | 0/5 (fails: profit_factor, annual_return, drawdown, pf_first_half, pf_second_half, walk_forward, not_concentrated, forward: 0 trades / 6 days so far) |
| death_cross_exit | 877 | 59% | 1.41 | 16.3% | -16.9% | -8.5% | -9.4% | 0/5 (fails: profit_factor, pf_first_half, pf_second_half, walk_forward, not_concentrated, forward: 0 trades / 5 days so far) |
| golden20 | 72 | 60% | 1.38 | 1.4% | -6.5% | -2.9% | -2.1% | 0/5 (fails: enough_trades, profit_factor, annual_return, pf_first_half, walk_forward, not_concentrated, forward: 0 trades / 5 days so far) |
| sma50_exit | 915 | 56% | 1.41 | 16.1% | -16.2% | -8.1% | -9.1% | 0/5 (fails: profit_factor, win_rate, pf_first_half, pf_second_half, walk_forward, not_concentrated, forward: 0 trades / 5 days so far) |
| cap98_tight | 906 | 58% | 1.65 | 19.3% | -15.6% | -7.7% | -7.8% | 0/5 (fails: pf_first_half, forward: 0 trades / 1 days so far) |

Gates also check: most half-year folds, the edge without the 5 best trades, trades opened since the challenger was added (held-out), and, once all else passes, a +/-10% nudge to each setting.

