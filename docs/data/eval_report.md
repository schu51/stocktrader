# Daily eval — 2026-11-27

Rules live: **sma200_exit** since 2026-11-12

## Live trades

| window | trades | win rate | profit factor | avg trade | P&L |
|---|---|---|---|---|---|
| closed today | 1 | 100% | – | +0.48% | $26 |
| last 20 trades | 20 | 15% | 0.14 | -7.32% | $-3,996 |
| last 30 days | 26 | 19% | 0.19 | -6.37% | $-4,689 |
| current rules | 1 | 0% | 0.0 | -25.04% | $-709 |
| all time | 98 | 21% | 0.59 | -1.73% | $-5,592 |

Account $105,667, -9.4% from peak. 30 days: -5.0% vs SPY -5.4%. Since current rules: -1.3% vs SPY -4.1%.

Stop exits (last 10): filled -2.8% vs their stop on average, median -0.2%, worst -24.6% (VCYT), 1 gapped more than 2%. Stop known from the filled order for 6 of 10; the rest rebuilt from the entry rule.

Tracking (30 days): live -5.0% vs backtest of the same rules -1.0%.

## Tournament (3-year backtest)

| rules | trades | win rate | PF | annual | max DD | worst month | last 6 mo | streak |
|---|---|---|---|---|---|---|---|---|
| **live** | 869 | 59% | 1.37 | 14.7% | -17.9% | -9.0% | -8.9% | |
| be3 | 1009 | 64% | 1.24 | 8.2% | -18.7% | -8.3% | -6.4% | 0/5 (fails: profit_factor, annual_return, pf_first_half, pf_second_half, walk_forward, not_concentrated, forward: 0 trades / 13 days so far) |
| trail20 | 945 | 59% | 1.33 | 12.8% | -17.5% | -8.5% | -4.0% | 0/5 (fails: profit_factor, pf_first_half, walk_forward, forward: 0 trades / 13 days so far) |
| spy3 | 643 | 62% | 1.62 | 16.9% | -17.7% | -7.5% | -7.4% | 0/5 (fails: pf_second_half, forward: 0 trades / 13 days so far) |
| spy1 | 1083 | 57% | 1.27 | 11.7% | -21.4% | -9.9% | -10.9% | 0/5 (fails: profit_factor, annual_return, drawdown, recent, pf_first_half, pf_second_half, walk_forward, not_concentrated, forward: 0 trades / 13 days so far) |
| earn_exit15 | 936 | 59% | 1.27 | 10.2% | -17.3% | -9.7% | -6.1% | 0/5 (fails: profit_factor, annual_return, pf_first_half, pf_second_half, walk_forward, not_concentrated, forward: 0 trades / 13 days so far) |
| risk40 | 877 | 59% | 1.40 | 14.6% | -17.5% | -8.2% | -9.5% | 0/5 (fails: profit_factor, recent, forward: 0 trades / 13 days so far) |
| pullback | 920 | 59% | 1.29 | 11.1% | -17.8% | -9.5% | -12.1% | 0/5 (fails: profit_factor, annual_return, recent, pf_first_half, pf_second_half, walk_forward, not_concentrated, forward: 0 trades / 13 days so far) |
| death_cross_exit | 870 | 59% | 1.36 | 14.3% | -17.9% | -9.0% | -8.8% | 0/5 (fails: profit_factor, pf_first_half, pf_second_half, not_concentrated, forward: 0 trades / 12 days so far) |
| golden20 | 72 | 62% | 1.89 | 3.1% | -5.4% | -2.1% | -1.2% | 0/5 (fails: enough_trades, annual_return, pf_first_half, walk_forward, not_concentrated, forward: 0 trades / 12 days so far) |
| sma50_exit | 907 | 56% | 1.27 | 10.5% | -17.1% | -9.4% | -9.1% | 0/5 (fails: profit_factor, annual_return, win_rate, recent, pf_first_half, pf_second_half, walk_forward, not_concentrated, forward: 0 trades / 12 days so far) |
| cap98_tight | 896 | 58% | 1.65 | 17.7% | -16.8% | -8.2% | -3.9% | 0/5 (fails: pf_first_half, forward: 0 trades / 8 days so far) |

Gates also check: most half-year folds, the edge without the 5 best trades, trades opened since the challenger was added (held-out), and, once all else passes, a +/-10% nudge to each setting.

