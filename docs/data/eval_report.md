# Daily eval — 2026-11-23

Rules live: **sma200_exit** since 2026-11-12

**Alerts**

- Account is 10.3% below its peak
- Stop exits filled 3.6% below their stop on average (last 10)
- Live account -6.1% vs backtest of the same rules -0.9% over 30 days: live bot is not matching its rules

## Live trades

| window | trades | win rate | profit factor | avg trade | P&L |
|---|---|---|---|---|---|
| closed today | 0 | | | | |
| last 20 trades | 20 | 10% | 0.14 | -7.06% | $-4,029 |
| last 30 days | 23 | 17% | 0.22 | -5.80% | $-3,825 |
| current rules | 0 | | | | |
| all time | 95 | 21% | 0.63 | -1.44% | $-4,729 |

Account $104,664, -10.3% from peak. 30 days: -6.1% vs SPY -5.0%. Since current rules: -2.2% vs SPY -4.9%.

Stop exits (last 10): filled -3.6% vs their stop on average, median -0.6%, worst -24.6% (VCYT), 2 gapped more than 2%. Stop known from the filled order for 3 of 10; the rest rebuilt from the entry rule.

Tracking (30 days): live -6.1% vs backtest of the same rules -0.9%.

## Tournament (3-year backtest)

| rules | trades | win rate | PF | annual | max DD | worst month | last 6 mo | streak |
|---|---|---|---|---|---|---|---|---|
| **live** | 863 | 59% | 1.37 | 14.7% | -17.6% | -9.3% | -7.6% | |
| be3 | 1011 | 64% | 1.30 | 10.5% | -18.7% | -8.4% | -6.2% | 0/5 (fails: profit_factor, annual_return, drawdown, pf_second_half, walk_forward, not_concentrated, forward: 0 trades / 10 days so far) |
| trail20 | 952 | 59% | 1.35 | 13.5% | -17.6% | -8.0% | -3.4% | 0/5 (fails: profit_factor, pf_first_half, pf_second_half, walk_forward, not_concentrated, forward: 0 trades / 10 days so far) |
| spy3 | 639 | 62% | 1.61 | 16.7% | -17.7% | -7.3% | -6.2% | 0/5 (fails: pf_second_half, forward: 0 trades / 10 days so far) |
| spy1 | 1099 | 57% | 1.24 | 10.1% | -21.8% | -10.0% | -10.4% | 0/5 (fails: profit_factor, annual_return, drawdown, recent, pf_first_half, pf_second_half, walk_forward, not_concentrated, forward: 0 trades / 10 days so far) |
| earn_exit15 | 937 | 59% | 1.30 | 11.7% | -17.4% | -9.7% | -5.0% | 0/5 (fails: profit_factor, annual_return, pf_first_half, pf_second_half, walk_forward, not_concentrated, forward: 0 trades / 10 days so far) |
| risk40 | 871 | 59% | 1.41 | 14.4% | -17.6% | -8.0% | -8.1% | 0/5 (fails: profit_factor, recent, forward: 0 trades / 10 days so far) |
| pullback | 924 | 59% | 1.30 | 12.2% | -16.6% | -10.1% | -10.5% | 0/5 (fails: profit_factor, annual_return, recent, pf_first_half, pf_second_half, walk_forward, not_concentrated, forward: 0 trades / 10 days so far) |
| death_cross_exit | 867 | 59% | 1.34 | 13.0% | -17.4% | -8.9% | -7.5% | 0/5 (fails: profit_factor, pf_first_half, pf_second_half, walk_forward, not_concentrated, forward: 0 trades / 9 days so far) |
| golden20 | 72 | 62% | 1.89 | 3.1% | -5.4% | -2.1% | -2.2% | 0/5 (fails: enough_trades, annual_return, pf_first_half, walk_forward, not_concentrated, forward: 0 trades / 9 days so far) |
| sma50_exit | 899 | 57% | 1.40 | 16.1% | -16.5% | -9.4% | -9.1% | 0/5 (fails: profit_factor, win_rate, recent, walk_forward, forward: 0 trades / 9 days so far) |
| cap98_tight | 896 | 58% | 1.65 | 18.4% | -15.6% | -6.9% | -1.6% | 0/5 (fails: pf_first_half, forward: 0 trades / 5 days so far) |

Gates also check: most half-year folds, the edge without the 5 best trades, trades opened since the challenger was added (held-out), and, once all else passes, a +/-10% nudge to each setting.

