# Daily eval — 2026-11-20

Rules live: **sma200_exit** since 2026-11-12

**Alerts**

- Account is 10.8% below its peak
- Stop exits filled 3.6% below their stop on average (last 10)

## Live trades

| window | trades | win rate | profit factor | avg trade | P&L |
|---|---|---|---|---|---|
| closed today | 0 | | | | |
| last 20 trades | 20 | 10% | 0.14 | -7.06% | $-4,029 |
| last 30 days | 24 | 21% | 0.23 | -5.50% | $-3,790 |
| current rules | 0 | | | | |
| all time | 95 | 21% | 0.63 | -1.44% | $-4,729 |

Account $105,085, -10.8% from peak. 30 days: -5.5% vs SPY -10.3%. Since current rules: -1.8% vs SPY -9.2%.

Stop exits (last 10): filled -3.6% vs their stop on average, median -0.6%, worst -24.6% (VCYT), 2 gapped more than 2%. Stop known from the filled order for 3 of 10; the rest rebuilt from the entry rule.

Tracking (30 days): live -5.5% vs backtest of the same rules -0.9%.

## Tournament (3-year backtest)

| rules | trades | win rate | PF | annual | max DD | worst month | last 6 mo | streak |
|---|---|---|---|---|---|---|---|---|
| **live** | 864 | 59% | 1.41 | 16.2% | -16.9% | -8.6% | -5.5% | |
| be3 | 1005 | 64% | 1.32 | 11.8% | -19.4% | -8.2% | -5.0% | 0/5 (fails: profit_factor, annual_return, drawdown, pf_second_half, walk_forward, not_concentrated, forward: 0 trades / 9 days so far) |
| trail20 | 943 | 59% | 1.39 | 14.6% | -18.6% | -9.0% | -3.9% | 0/5 (fails: profit_factor, drawdown, pf_first_half, walk_forward, not_concentrated, forward: 0 trades / 9 days so far) |
| spy3 | 631 | 62% | 1.67 | 18.3% | -17.0% | -5.9% | -2.6% | 0/5 (fails: forward: 0 trades / 9 days so far) |
| spy1 | 1119 | 57% | 1.19 | 8.2% | -22.3% | -8.0% | -8.4% | 0/5 (fails: profit_factor, annual_return, drawdown, win_rate, recent, pf_first_half, pf_second_half, walk_forward, not_concentrated, forward: 0 trades / 9 days so far) |
| earn_exit15 | 934 | 60% | 1.30 | 11.8% | -17.5% | -8.5% | -2.4% | 0/5 (fails: profit_factor, annual_return, pf_first_half, pf_second_half, walk_forward, not_concentrated, forward: 0 trades / 9 days so far) |
| risk40 | 879 | 59% | 1.48 | 17.4% | -16.4% | -8.1% | -4.5% | 0/5 (fails: profit_factor, forward: 0 trades / 9 days so far) |
| pullback | 963 | 59% | 1.27 | 10.2% | -18.7% | -8.2% | -3.5% | 0/5 (fails: profit_factor, annual_return, drawdown, pf_first_half, pf_second_half, walk_forward, not_concentrated, forward: 0 trades / 9 days so far) |
| death_cross_exit | 863 | 59% | 1.40 | 16.0% | -17.0% | -8.6% | -5.6% | 0/5 (fails: profit_factor, recent, pf_first_half, not_concentrated, forward: 0 trades / 8 days so far) |
| golden20 | 72 | 58% | 1.28 | 1.1% | -7.2% | -2.9% | -2.1% | 0/5 (fails: enough_trades, profit_factor, annual_return, pf_first_half, walk_forward, not_concentrated, forward: 0 trades / 8 days so far) |
| sma50_exit | 905 | 56% | 1.33 | 13.0% | -17.5% | -8.0% | -5.9% | 0/5 (fails: profit_factor, annual_return, win_rate, recent, pf_first_half, pf_second_half, walk_forward, not_concentrated, forward: 0 trades / 8 days so far) |
| cap98_tight | 902 | 58% | 1.63 | 17.5% | -17.1% | -8.1% | -1.4% | 0/5 (fails: pf_first_half, forward: 0 trades / 4 days so far) |

Gates also check: most half-year folds, the edge without the 5 best trades, trades opened since the challenger was added (held-out), and, once all else passes, a +/-10% nudge to each setting.

