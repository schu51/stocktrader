# Daily eval — 2026-11-25

Rules live: **sma200_exit** since 2026-11-12

**Alerts**

- Stop exits filled 3.5% below their stop on average (last 10)

## Live trades

| window | trades | win rate | profit factor | avg trade | P&L |
|---|---|---|---|---|---|
| closed today | 1 | 0% | 0.0 | -25.04% | $-709 |
| last 20 trades | 20 | 10% | 0.13 | -7.87% | $-4,255 |
| last 30 days | 25 | 16% | 0.19 | -6.65% | $-4,715 |
| current rules | 1 | 0% | 0.0 | -25.04% | $-709 |
| all time | 97 | 21% | 0.59 | -1.75% | $-5,618 |

Account $105,108, -9.8% from peak. 30 days: -5.7% vs SPY -5.7%. Since current rules: -1.8% vs SPY -5.7%.

Stop exits (last 10): filled -3.5% vs their stop on average, median -0.3%, worst -24.6% (VCYT), 2 gapped more than 2%. Stop known from the filled order for 5 of 10; the rest rebuilt from the entry rule.

Tracking (30 days): live -5.7% vs backtest of the same rules -0.9%.

## Tournament (3-year backtest)

| rules | trades | win rate | PF | annual | max DD | worst month | last 6 mo | streak |
|---|---|---|---|---|---|---|---|---|
| **live** | 869 | 59% | 1.37 | 14.8% | -17.9% | -9.0% | -7.6% | |
| be3 | 1009 | 64% | 1.24 | 8.2% | -18.7% | -8.3% | -6.0% | 0/5 (fails: profit_factor, annual_return, pf_first_half, pf_second_half, walk_forward, not_concentrated, forward: 0 trades / 12 days so far) |
| trail20 | 945 | 59% | 1.33 | 12.8% | -17.5% | -8.5% | -3.0% | 0/5 (fails: profit_factor, pf_first_half, walk_forward, forward: 0 trades / 12 days so far) |
| spy3 | 643 | 62% | 1.62 | 16.9% | -17.7% | -7.5% | -6.1% | 0/5 (fails: pf_second_half, forward: 0 trades / 12 days so far) |
| spy1 | 1080 | 57% | 1.27 | 11.5% | -21.6% | -9.9% | -11.2% | 0/5 (fails: profit_factor, annual_return, drawdown, recent, pf_first_half, pf_second_half, walk_forward, not_concentrated, forward: 0 trades / 12 days so far) |
| earn_exit15 | 935 | 59% | 1.27 | 10.2% | -17.2% | -9.7% | -5.0% | 0/5 (fails: profit_factor, annual_return, pf_first_half, pf_second_half, walk_forward, not_concentrated, forward: 0 trades / 12 days so far) |
| risk40 | 877 | 59% | 1.40 | 14.6% | -17.5% | -8.2% | -8.4% | 0/5 (fails: profit_factor, recent, forward: 0 trades / 12 days so far) |
| pullback | 920 | 59% | 1.29 | 11.2% | -17.8% | -9.5% | -10.7% | 0/5 (fails: profit_factor, annual_return, recent, pf_first_half, pf_second_half, walk_forward, not_concentrated, forward: 0 trades / 12 days so far) |
| death_cross_exit | 870 | 59% | 1.36 | 14.4% | -17.9% | -9.0% | -7.5% | 0/5 (fails: profit_factor, pf_first_half, pf_second_half, forward: 0 trades / 11 days so far) |
| golden20 | 72 | 62% | 1.89 | 3.1% | -5.4% | -2.1% | -2.2% | 0/5 (fails: enough_trades, annual_return, pf_first_half, walk_forward, not_concentrated, forward: 0 trades / 11 days so far) |
| sma50_exit | 907 | 56% | 1.27 | 10.5% | -17.1% | -9.4% | -7.7% | 0/5 (fails: profit_factor, annual_return, win_rate, recent, pf_first_half, pf_second_half, walk_forward, not_concentrated, forward: 0 trades / 11 days so far) |
| cap98_tight | 901 | 57% | 1.60 | 16.4% | -16.8% | -8.2% | -0.6% | 0/5 (fails: pf_first_half, forward: 0 trades / 7 days so far) |

Gates also check: most half-year folds, the edge without the 5 best trades, trades opened since the challenger was added (held-out), and, once all else passes, a +/-10% nudge to each setting.

