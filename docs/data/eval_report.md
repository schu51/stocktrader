# Daily eval — 2026-11-26

Rules live: **sma200_exit** since 2026-11-12

**Alerts**

- Stop exits filled 3.5% below their stop on average (last 10)

## Live trades

| window | trades | win rate | profit factor | avg trade | P&L |
|---|---|---|---|---|---|
| closed today | 0 | | | | |
| last 20 trades | 20 | 10% | 0.13 | -7.87% | $-4,255 |
| last 30 days | 25 | 16% | 0.19 | -6.65% | $-4,715 |
| current rules | 1 | 0% | 0.0 | -25.04% | $-709 |
| all time | 97 | 21% | 0.59 | -1.75% | $-5,618 |

Account $105,672, -9.4% from peak. 30 days: -5.6% vs SPY -6.4%. Since current rules: -1.3% vs SPY -5.7%.

Stop exits (last 10): filled -3.5% vs their stop on average, median -0.3%, worst -24.6% (VCYT), 2 gapped more than 2%. Stop known from the filled order for 5 of 10; the rest rebuilt from the entry rule.

Tracking (30 days): live -5.6% vs backtest of the same rules -0.9%.

## Tournament (3-year backtest)

| rules | trades | win rate | PF | annual | max DD | worst month | last 6 mo | streak |
|---|---|---|---|---|---|---|---|---|
| **live** | 867 | 59% | 1.37 | 14.7% | -17.9% | -9.0% | -7.6% | |
| be3 | 1006 | 64% | 1.24 | 8.2% | -18.7% | -8.3% | -6.0% | 0/5 (fails: profit_factor, annual_return, pf_first_half, pf_second_half, walk_forward, not_concentrated, forward: 0 trades / 12 days so far) |
| trail20 | 948 | 59% | 1.32 | 12.1% | -17.5% | -8.5% | -4.1% | 0/5 (fails: profit_factor, annual_return, pf_first_half, walk_forward, not_concentrated, forward: 0 trades / 12 days so far) |
| spy3 | 640 | 62% | 1.61 | 16.6% | -17.7% | -7.5% | -6.2% | 0/5 (fails: pf_second_half, forward: 0 trades / 12 days so far) |
| spy1 | 1082 | 57% | 1.27 | 11.7% | -22.0% | -9.9% | -11.4% | 0/5 (fails: profit_factor, annual_return, drawdown, recent, pf_first_half, pf_second_half, walk_forward, not_concentrated, forward: 0 trades / 12 days so far) |
| earn_exit15 | 932 | 59% | 1.26 | 9.8% | -17.3% | -10.2% | -4.7% | 0/5 (fails: profit_factor, annual_return, pf_first_half, pf_second_half, walk_forward, not_concentrated, forward: 0 trades / 12 days so far) |
| risk40 | 876 | 59% | 1.40 | 14.7% | -17.5% | -8.2% | -8.4% | 0/5 (fails: profit_factor, recent, forward: 0 trades / 12 days so far) |
| pullback | 921 | 59% | 1.26 | 10.4% | -17.8% | -9.5% | -10.1% | 0/5 (fails: profit_factor, annual_return, recent, pf_first_half, pf_second_half, walk_forward, not_concentrated, forward: 0 trades / 12 days so far) |
| death_cross_exit | 868 | 59% | 1.36 | 14.3% | -17.9% | -9.0% | -7.5% | 0/5 (fails: profit_factor, pf_first_half, pf_second_half, not_concentrated, forward: 0 trades / 11 days so far) |
| golden20 | 72 | 62% | 1.89 | 3.1% | -5.4% | -2.1% | -2.2% | 0/5 (fails: enough_trades, annual_return, pf_first_half, walk_forward, not_concentrated, forward: 0 trades / 11 days so far) |
| sma50_exit | 899 | 56% | 1.34 | 13.2% | -16.9% | -9.4% | -9.2% | 0/5 (fails: profit_factor, win_rate, recent, pf_first_half, walk_forward, not_concentrated, forward: 0 trades / 11 days so far) |
| cap98_tight | 899 | 58% | 1.65 | 17.6% | -16.8% | -8.2% | -0.9% | 0/5 (fails: pf_first_half, forward: 0 trades / 7 days so far) |

Gates also check: most half-year folds, the edge without the 5 best trades, trades opened since the challenger was added (held-out), and, once all else passes, a +/-10% nudge to each setting.

