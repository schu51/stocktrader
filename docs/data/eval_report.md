# Daily eval — 2026-11-18

Rules live: **sma200_exit** since 2026-11-12

**Alerts**

- Account is 11.3% below its peak
- Stop exits filled 3.7% below their stop on average (last 10)

## Live trades

| window | trades | win rate | profit factor | avg trade | P&L |
|---|---|---|---|---|---|
| closed today | 1 | 100% | – | +0.23% | $5 |
| last 20 trades | 20 | 10% | 0.15 | -6.27% | $-3,656 |
| last 30 days | 23 | 22% | 0.26 | -4.65% | $-3,185 |
| current rules | 0 | | | | |
| all time | 94 | 21% | 0.66 | -1.19% | $-4,124 |

Account $105,764, -11.3% from peak. 30 days: -4.9% vs SPY -4.3%. Since current rules: -1.2% vs SPY -3.9%.

Stop exits (last 10): filled -3.7% vs their stop on average, median -0.8%, worst -24.6% (VCYT), 2 gapped more than 2%. Stop known from the filled order for 2 of 10; the rest rebuilt from the entry rule.

Tracking (30 days): live -4.9% vs backtest of the same rules -0.8%.

## Tournament (3-year backtest)

| rules | trades | win rate | PF | annual | max DD | worst month | last 6 mo | streak |
|---|---|---|---|---|---|---|---|---|
| **live** | 861 | 59% | 1.44 | 17.0% | -17.0% | -8.6% | -10.3% | |
| be3 | 1005 | 64% | 1.33 | 12.2% | -19.6% | -8.3% | -7.2% | 0/5 (fails: profit_factor, annual_return, drawdown, pf_second_half, walk_forward, not_concentrated, forward: 0 trades / 7 days so far) |
| trail20 | 943 | 59% | 1.39 | 14.6% | -18.6% | -9.0% | -9.1% | 0/5 (fails: profit_factor, annual_return, drawdown, pf_first_half, pf_second_half, walk_forward, not_concentrated, forward: 0 trades / 7 days so far) |
| spy3 | 631 | 62% | 1.67 | 18.3% | -17.1% | -5.9% | -6.4% | 0/5 (fails: forward: 0 trades / 7 days so far) |
| spy1 | 1114 | 57% | 1.18 | 7.6% | -22.4% | -8.1% | -11.3% | 0/5 (fails: profit_factor, annual_return, drawdown, win_rate, recent, pf_first_half, pf_second_half, walk_forward, not_concentrated, forward: 0 trades / 7 days so far) |
| earn_exit15 | 935 | 60% | 1.31 | 12.5% | -17.2% | -8.3% | -7.8% | 0/5 (fails: profit_factor, annual_return, pf_first_half, pf_second_half, walk_forward, not_concentrated, forward: 0 trades / 7 days so far) |
| risk40 | 878 | 59% | 1.46 | 16.6% | -16.8% | -8.0% | -9.1% | 0/5 (fails: profit_factor, pf_second_half, forward: 0 trades / 7 days so far) |
| pullback | 963 | 59% | 1.27 | 10.3% | -18.6% | -8.2% | -9.1% | 0/5 (fails: profit_factor, annual_return, drawdown, pf_first_half, pf_second_half, walk_forward, not_concentrated, forward: 0 trades / 7 days so far) |
| death_cross_exit | 863 | 59% | 1.39 | 15.3% | -17.2% | -8.6% | -10.7% | 0/5 (fails: profit_factor, recent, pf_first_half, pf_second_half, walk_forward, not_concentrated, forward: 0 trades / 6 days so far) |
| golden20 | 72 | 58% | 1.28 | 1.1% | -7.2% | -2.9% | -2.1% | 0/5 (fails: enough_trades, profit_factor, annual_return, pf_first_half, walk_forward, not_concentrated, forward: 0 trades / 6 days so far) |
| sma50_exit | 905 | 56% | 1.33 | 13.0% | -17.5% | -7.9% | -9.6% | 0/5 (fails: profit_factor, annual_return, win_rate, pf_first_half, pf_second_half, walk_forward, not_concentrated, forward: 0 trades / 6 days so far) |
| cap98_tight | 901 | 57% | 1.63 | 18.1% | -16.0% | -8.5% | -5.7% | 0/5 (fails: pf_first_half, forward: 0 trades / 2 days so far) |

Gates also check: most half-year folds, the edge without the 5 best trades, trades opened since the challenger was added (held-out), and, once all else passes, a +/-10% nudge to each setting.

