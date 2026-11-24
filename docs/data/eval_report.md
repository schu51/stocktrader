# Daily eval — 2026-11-24

Rules live: **sma200_exit** since 2026-11-12

**Alerts**

- Stop exits filled 3.5% below their stop on average (last 10)

## Live trades

| window | trades | win rate | profit factor | avg trade | P&L |
|---|---|---|---|---|---|
| closed today | 1 | 0% | 0.0 | -7.80% | $-181 |
| last 20 trades | 20 | 10% | 0.15 | -6.67% | $-3,601 |
| last 30 days | 24 | 17% | 0.21 | -5.88% | $-4,006 |
| current rules | 0 | | | | |
| all time | 96 | 21% | 0.62 | -1.51% | $-4,909 |

Account $106,155, -8.9% from peak. 30 days: -4.8% vs SPY -5.4%. Since current rules: -0.8% vs SPY -5.4%.

Stop exits (last 10): filled -3.5% vs their stop on average, median -0.3%, worst -24.6% (VCYT), 2 gapped more than 2%. Stop known from the filled order for 4 of 10; the rest rebuilt from the entry rule.

Tracking (30 days): live -4.8% vs backtest of the same rules -0.9%.

## Tournament (3-year backtest)

| rules | trades | win rate | PF | annual | max DD | worst month | last 6 mo | streak |
|---|---|---|---|---|---|---|---|---|
| **live** | 871 | 59% | 1.37 | 14.8% | -17.8% | -9.0% | -7.6% | |
| be3 | 1010 | 64% | 1.31 | 10.8% | -18.7% | -8.4% | -6.1% | 0/5 (fails: profit_factor, annual_return, pf_first_half, pf_second_half, walk_forward, not_concentrated, forward: 0 trades / 11 days so far) |
| trail20 | 948 | 59% | 1.32 | 12.4% | -17.3% | -7.9% | -2.9% | 0/5 (fails: profit_factor, annual_return, pf_first_half, walk_forward, not_concentrated, forward: 0 trades / 11 days so far) |
| spy3 | 636 | 62% | 1.57 | 15.0% | -17.7% | -7.3% | -6.3% | 0/5 (fails: forward: 0 trades / 11 days so far) |
| spy1 | 1087 | 57% | 1.28 | 12.0% | -21.4% | -9.9% | -10.2% | 0/5 (fails: profit_factor, annual_return, drawdown, recent, pf_first_half, pf_second_half, walk_forward, not_concentrated, forward: 0 trades / 11 days so far) |
| earn_exit15 | 935 | 59% | 1.38 | 13.8% | -17.0% | -9.7% | -4.2% | 0/5 (fails: profit_factor, walk_forward, not_concentrated, forward: 0 trades / 11 days so far) |
| risk40 | 868 | 59% | 1.44 | 16.2% | -16.6% | -8.1% | -7.9% | 0/5 (fails: profit_factor, recent, forward: 0 trades / 11 days so far) |
| pullback | 913 | 59% | 1.33 | 12.7% | -18.0% | -9.2% | -10.2% | 0/5 (fails: profit_factor, annual_return, recent, pf_first_half, pf_second_half, walk_forward, not_concentrated, forward: 0 trades / 11 days so far) |
| death_cross_exit | 871 | 59% | 1.37 | 14.6% | -17.8% | -9.0% | -7.6% | 0/5 (fails: profit_factor, pf_first_half, forward: 0 trades / 10 days so far) |
| golden20 | 72 | 62% | 1.89 | 3.1% | -5.4% | -2.1% | -2.2% | 0/5 (fails: enough_trades, annual_return, pf_first_half, walk_forward, not_concentrated, forward: 0 trades / 10 days so far) |
| sma50_exit | 899 | 56% | 1.39 | 15.5% | -16.6% | -9.3% | -9.1% | 0/5 (fails: profit_factor, win_rate, recent, pf_first_half, walk_forward, forward: 0 trades / 10 days so far) |
| cap98_tight | 891 | 58% | 1.71 | 19.3% | -16.2% | -7.8% | -0.4% | 0/5 (fails: pf_first_half, forward: 0 trades / 6 days so far) |

Gates also check: most half-year folds, the edge without the 5 best trades, trades opened since the challenger was added (held-out), and, once all else passes, a +/-10% nudge to each setting.

