# Daily eval — 2026-11-19

Rules live: **sma200_exit** since 2026-11-12

**Alerts**

- Account is 11.0% below its peak
- Stop exits filled 3.6% below their stop on average (last 10)

## Live trades

| window | trades | win rate | profit factor | avg trade | P&L |
|---|---|---|---|---|---|
| closed today | 1 | 0% | 0.0 | -25.08% | $-605 |
| last 20 trades | 20 | 10% | 0.14 | -7.06% | $-4,029 |
| last 30 days | 24 | 21% | 0.23 | -5.50% | $-3,790 |
| current rules | 0 | | | | |
| all time | 95 | 21% | 0.63 | -1.44% | $-4,729 |

Account $105,111, -11.0% from peak. 30 days: -5.4% vs SPY -6.6%. Since current rules: -1.8% vs SPY -5.8%.

Stop exits (last 10): filled -3.6% vs their stop on average, median -0.6%, worst -24.6% (VCYT), 2 gapped more than 2%. Stop known from the filled order for 3 of 10; the rest rebuilt from the entry rule.

Tracking (30 days): live -5.4% vs backtest of the same rules -0.9%.

## Tournament (3-year backtest)

| rules | trades | win rate | PF | annual | max DD | worst month | last 6 mo | streak |
|---|---|---|---|---|---|---|---|---|
| **live** | 860 | 59% | 1.44 | 17.1% | -17.3% | -8.6% | -7.2% | |
| be3 | 1005 | 64% | 1.32 | 11.8% | -19.5% | -8.2% | -5.8% | 0/5 (fails: profit_factor, annual_return, drawdown, pf_second_half, walk_forward, not_concentrated, forward: 0 trades / 8 days so far) |
| trail20 | 943 | 59% | 1.39 | 14.6% | -18.6% | -9.0% | -4.9% | 0/5 (fails: profit_factor, annual_return, drawdown, pf_first_half, pf_second_half, walk_forward, not_concentrated, forward: 0 trades / 8 days so far) |
| spy3 | 631 | 62% | 1.67 | 18.3% | -17.1% | -5.9% | -3.3% | 0/5 (fails: forward: 0 trades / 8 days so far) |
| spy1 | 1119 | 57% | 1.19 | 8.1% | -22.4% | -8.0% | -9.0% | 0/5 (fails: profit_factor, annual_return, drawdown, win_rate, recent, pf_first_half, pf_second_half, walk_forward, not_concentrated, forward: 0 trades / 8 days so far) |
| earn_exit15 | 933 | 60% | 1.30 | 11.9% | -17.3% | -8.5% | -2.8% | 0/5 (fails: profit_factor, annual_return, pf_first_half, pf_second_half, walk_forward, not_concentrated, forward: 0 trades / 8 days so far) |
| risk40 | 880 | 59% | 1.48 | 17.5% | -16.7% | -8.1% | -5.3% | 0/5 (fails: profit_factor, pf_second_half, forward: 0 trades / 8 days so far) |
| pullback | 963 | 59% | 1.27 | 10.3% | -18.7% | -8.2% | -4.3% | 0/5 (fails: profit_factor, annual_return, drawdown, pf_first_half, pf_second_half, walk_forward, not_concentrated, forward: 0 trades / 8 days so far) |
| death_cross_exit | 861 | 59% | 1.39 | 15.4% | -16.9% | -8.6% | -7.3% | 0/5 (fails: profit_factor, recent, pf_first_half, pf_second_half, walk_forward, not_concentrated, forward: 0 trades / 7 days so far) |
| golden20 | 72 | 58% | 1.28 | 1.1% | -7.2% | -2.9% | -2.1% | 0/5 (fails: enough_trades, profit_factor, annual_return, pf_first_half, walk_forward, not_concentrated, forward: 0 trades / 7 days so far) |
| sma50_exit | 905 | 56% | 1.33 | 13.0% | -17.5% | -7.9% | -6.6% | 0/5 (fails: profit_factor, annual_return, win_rate, pf_first_half, pf_second_half, walk_forward, not_concentrated, forward: 0 trades / 7 days so far) |
| cap98_tight | 896 | 58% | 1.70 | 19.4% | -16.2% | -8.8% | +0.4% | 0/5 (fails: pf_first_half, forward: 0 trades / 3 days so far) |

Gates also check: most half-year folds, the edge without the 5 best trades, trades opened since the challenger was added (held-out), and, once all else passes, a +/-10% nudge to each setting.

