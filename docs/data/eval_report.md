# Daily eval — 2026-11-11

Rules live: **breakeven5_spy2** since 2026-11-10

**Alerts**

- Account is 11.0% below its peak
- Stop exits filled 3.5% below their stop on average (last 10)

## Live trades

| window | trades | win rate | profit factor | avg trade | P&L |
|---|---|---|---|---|---|
| closed today | 0 | | | | |
| last 20 trades | 20 | 15% | 0.11 | -6.48% | $-3,815 |
| last 30 days | 36 | 22% | 0.31 | -4.09% | $-4,061 |
| current rules | 0 | | | | |
| all time | 91 | 20% | 0.61 | -1.48% | $-4,754 |

Account $107,034, -11.0% from peak. 30 days: -7.1% vs SPY -2.3%. Since current rules: +0.6% vs SPY +1.1%.

Stop exits (last 10): filled -3.5% vs their stop on average, median -0.6%, worst -24.6% (VCYT), 2 gapped more than 2%. Stop known from the filled order for 0 of 10; the rest rebuilt from the entry rule.

Tracking (30 days): live -7.1% vs backtest of the same rules -5.6%.

## Tournament (3-year backtest)

| rules | trades | win rate | PF | annual | max DD | worst month | last 6 mo | streak |
|---|---|---|---|---|---|---|---|---|
| **live** | 658 | 59% | 2.31 | 31.1% | -13.3% | -6.2% | -1.2% | |
| be3 | 739 | 66% | 2.29 | 30.6% | -13.3% | -6.6% | +1.6% | 0/5 (fails: profit_factor, pf_second_half) |
| trail20 | 698 | 60% | 2.15 | 28.9% | -13.3% | -7.5% | +2.2% | 0/5 (fails: profit_factor, annual_return, pf_first_half, pf_second_half) |
| spy3 | 488 | 65% | 2.84 | 29.5% | -10.2% | -4.7% | -2.6% | 0/5 (fails: recent) |
| spy1 | 779 | 58% | 2.10 | 30.9% | -14.9% | -9.5% | -7.3% | 0/5 (fails: profit_factor, drawdown, recent, pf_second_half) |
| earn_exit15 | 743 | 61% | 2.08 | 26.6% | -12.9% | -5.8% | +1.6% | 0/5 (fails: profit_factor, annual_return, pf_first_half, pf_second_half) |
| risk40 | 664 | 60% | 2.32 | 28.7% | -13.4% | -6.2% | +0.4% | 0/5 (fails: profit_factor, annual_return, pf_second_half) |
| pullback | 758 | 55% | 2.32 | 34.5% | -13.2% | -6.6% | -1.8% | 0/5 (fails: profit_factor, win_rate, recent, pf_first_half, pf_second_half) |
| grace5 | 662 | 60% | 2.19 | 28.9% | -13.8% | -6.8% | -1.6% | 0/5 (fails: profit_factor, annual_return, recent, pf_first_half, pf_second_half) |
| death_cross_exit | 596 | 64% | 2.65 | 31.9% | -13.2% | -7.1% | -0.2% | 1/5 |
| sma200_exit | 603 | 65% | 2.72 | 33.4% | -13.3% | -7.1% | -0.2% | 1/5 |
| golden20 | 72 | 61% | 4.37 | 7.7% | -3.2% | -1.2% | +3.5% | 0/5 (fails: enough_trades, annual_return) |

