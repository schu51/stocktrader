# Daily eval — 2026-11-16

Rules live: **sma200_exit** since 2026-11-12

**Alerts**

- Account is 10.2% below its peak
- Stop exits filled 3.5% below their stop on average (last 10)

## Live trades

| window | trades | win rate | profit factor | avg trade | P&L |
|---|---|---|---|---|---|
| closed today | 1 | 100% | – | +22.95% | $634 |
| last 20 trades | 20 | 15% | 0.25 | -5.41% | $-3,216 |
| last 30 days | 21 | 19% | 0.26 | -5.08% | $-3,180 |
| current rules | 0 | | | | |
| all time | 92 | 21% | 0.66 | -1.21% | $-4,119 |

Account $107,379, -10.2% from peak. 30 days: -3.5% vs SPY -3.0%. Since current rules: +0.3% vs SPY -2.6%.

Stop exits (last 10): filled -3.5% vs their stop on average, median -0.6%, worst -24.6% (VCYT), 2 gapped more than 2%. Stop known from the filled order for 0 of 10; the rest rebuilt from the entry rule.

Tracking (30 days): live -3.5% vs backtest of the same rules -0.8%.

## Tournament (3-year backtest)

| rules | trades | win rate | PF | annual | max DD | worst month | last 6 mo | streak |
|---|---|---|---|---|---|---|---|---|
| **live** | 874 | 60% | 1.41 | 15.5% | -17.7% | -8.6% | -10.4% | |
| be3 | 1013 | 65% | 1.32 | 11.7% | -19.5% | -8.1% | -7.2% | 0/5 (fails: profit_factor, annual_return, drawdown, pf_second_half, walk_forward, not_concentrated, forward: 0 trades / 5 days so far) |
| trail20 | 941 | 60% | 1.41 | 16.3% | -17.5% | -8.4% | -9.0% | 0/5 (fails: profit_factor, pf_second_half, walk_forward, forward: 0 trades / 5 days so far) |
| spy3 | 642 | 62% | 1.71 | 19.1% | -17.2% | -6.1% | -6.5% | 0/5 (fails: pf_second_half, forward: 0 trades / 5 days so far) |
| spy1 | 1113 | 58% | 1.21 | 9.3% | -22.7% | -8.1% | -12.6% | 0/5 (fails: profit_factor, annual_return, drawdown, recent, pf_first_half, pf_second_half, walk_forward, not_concentrated, forward: 0 trades / 5 days so far) |
| earn_exit15 | 931 | 60% | 1.38 | 15.8% | -17.2% | -8.5% | -7.2% | 0/5 (fails: profit_factor, pf_second_half, walk_forward, not_concentrated, forward: 0 trades / 5 days so far) |
| risk40 | 873 | 59% | 1.50 | 18.1% | -17.0% | -8.1% | -9.1% | 0/5 (fails: profit_factor, pf_second_half, forward: 0 trades / 5 days so far) |
| pullback | 967 | 59% | 1.30 | 11.5% | -18.4% | -8.4% | -8.8% | 0/5 (fails: profit_factor, annual_return, pf_second_half, not_concentrated, forward: 0 trades / 5 days so far) |
| grace5 | 874 | 60% | 1.41 | 15.5% | -17.7% | -8.6% | -10.4% | 0/5 (fails: profit_factor, forward: 0 trades / 5 days so far) |
| death_cross_exit | 875 | 60% | 1.39 | 14.6% | -17.7% | -8.5% | -10.5% | 0/5 (fails: profit_factor, recent, pf_first_half, pf_second_half, walk_forward, not_concentrated, forward: 0 trades / 4 days so far) |
| golden20 | 72 | 60% | 1.38 | 1.4% | -6.5% | -2.9% | -2.1% | 0/5 (fails: enough_trades, profit_factor, annual_return, pf_first_half, walk_forward, not_concentrated, forward: 0 trades / 4 days so far) |
| sma50_exit | 921 | 57% | 1.37 | 14.2% | -17.8% | -8.3% | -9.4% | 0/5 (fails: profit_factor, win_rate, pf_first_half, pf_second_half, walk_forward, not_concentrated, forward: 0 trades / 4 days so far) |

Gates also check: most half-year folds, the edge without the 5 best trades, trades opened since the challenger was added (held-out), and, once all else passes, a +/-10% nudge to each setting.

