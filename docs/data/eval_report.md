# Daily eval — 2026-11-13

Rules live: **sma200_exit** since 2026-11-12

**Alerts**

- Account is 10.5% below its peak
- Stop exits filled 3.5% below their stop on average (last 10)
- Live account -7.3% vs backtest of the same rules -2.1% over 30 days: live bot is not matching its rules

## Live trades

| window | trades | win rate | profit factor | avg trade | P&L |
|---|---|---|---|---|---|
| closed today | 0 | | | | |
| last 20 trades | 20 | 15% | 0.11 | -6.48% | $-3,815 |
| last 30 days | 35 | 20% | 0.28 | -4.40% | $-4,228 |
| current rules | 0 | | | | |
| all time | 91 | 20% | 0.61 | -1.48% | $-4,754 |

Account $106,990, -10.5% from peak. 30 days: -7.3% vs SPY -3.0%. Since current rules: -0.0% vs SPY -0.7%.

Stop exits (last 10): filled -3.5% vs their stop on average, median -0.6%, worst -24.6% (VCYT), 2 gapped more than 2%. Stop known from the filled order for 0 of 10; the rest rebuilt from the entry rule.

Tracking (30 days): live -7.3% vs backtest of the same rules -2.1%.

## Tournament (3-year backtest)

| rules | trades | win rate | PF | annual | max DD | worst month | last 6 mo | streak |
|---|---|---|---|---|---|---|---|---|
| **live** | 883 | 59% | 1.39 | 15.9% | -18.2% | -8.5% | -11.4% | |
| be3 | 1021 | 65% | 1.35 | 13.1% | -19.7% | -8.3% | -8.1% | 0/5 (fails: profit_factor, annual_return, drawdown, pf_second_half) |
| trail20 | 960 | 60% | 1.33 | 14.0% | -17.1% | -7.6% | -11.4% | 0/5 (fails: profit_factor, pf_first_half, pf_second_half) |
| spy3 | 646 | 62% | 1.69 | 19.5% | -18.2% | -5.9% | -8.3% | 1/5 |
| spy1 | 1110 | 58% | 1.24 | 10.6% | -22.4% | -9.4% | -12.5% | 0/5 (fails: profit_factor, annual_return, drawdown, recent, pf_first_half, pf_second_half) |
| earn_exit15 | 924 | 60% | 1.39 | 16.1% | -16.9% | -8.4% | -9.3% | 0/5 (fails: profit_factor, pf_second_half) |
| risk40 | 873 | 59% | 1.47 | 16.6% | -17.4% | -7.7% | -10.8% | 0/5 (fails: profit_factor) |
| pullback | 955 | 58% | 1.39 | 15.5% | -17.8% | -8.9% | -9.7% | 0/5 (fails: profit_factor, pf_second_half) |
| grace5 | 883 | 59% | 1.39 | 15.9% | -18.2% | -8.5% | -11.4% | 0/5 (fails: profit_factor) |
| death_cross_exit | 882 | 59% | 1.39 | 15.8% | -18.2% | -8.6% | -11.4% | 0/5 (fails: profit_factor, pf_first_half, pf_second_half) |
| golden20 | 74 | 61% | 1.63 | 2.4% | -6.5% | -2.9% | -2.1% | 0/5 (fails: enough_trades, annual_return, pf_first_half) |
| sma50_exit | 923 | 56% | 1.40 | 16.5% | -17.5% | -8.0% | -11.2% | 0/5 (fails: profit_factor, win_rate) |

