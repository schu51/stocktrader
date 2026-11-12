# Daily eval — 2026-11-12

Rules live: **sma200_exit** since 2026-11-12

**Alerts**

- Account is 10.6% below its peak
- Stop exits filled 3.5% below their stop on average (last 10)

## Live trades

| window | trades | win rate | profit factor | avg trade | P&L |
|---|---|---|---|---|---|
| closed today | 0 | | | | |
| last 20 trades | 20 | 15% | 0.11 | -6.48% | $-3,815 |
| last 30 days | 36 | 22% | 0.31 | -4.09% | $-4,061 |
| current rules | 0 | | | | |
| all time | 91 | 20% | 0.61 | -1.48% | $-4,754 |

Account $106,812, -10.6% from peak. 30 days: -7.9% vs SPY -2.7%. Since current rules: -0.2% vs SPY -0.4%.

Stop exits (last 10): filled -3.5% vs their stop on average, median -0.6%, worst -24.6% (VCYT), 2 gapped more than 2%. Stop known from the filled order for 0 of 10; the rest rebuilt from the entry rule.

Tracking (30 days): live -7.9% vs backtest of the same rules -5.7%.

## Tournament (3-year backtest)

| rules | trades | win rate | PF | annual | max DD | worst month | last 6 mo | streak |
|---|---|---|---|---|---|---|---|---|
| **live** | 600 | 65% | 2.67 | 32.8% | -13.2% | -7.1% | -3.2% | |
| be3 | 689 | 69% | 2.52 | 32.4% | -12.7% | -7.1% | -1.2% | 0/5 (fails: profit_factor, pf_second_half) |
| trail20 | 681 | 64% | 2.18 | 27.5% | -14.0% | -8.3% | +1.3% | 0/5 (fails: profit_factor, annual_return, pf_first_half, pf_second_half) |
| spy3 | 456 | 70% | 3.11 | 30.2% | -10.5% | -6.6% | -5.5% | 0/5 (fails: annual_return, recent, pf_second_half) |
| spy1 | 708 | 62% | 2.43 | 35.4% | -15.0% | -9.9% | -7.4% | 0/5 (fails: profit_factor, drawdown, win_rate, recent, pf_second_half) |
| earn_exit15 | 690 | 65% | 2.45 | 33.0% | -13.3% | -6.5% | +1.1% | 0/5 (fails: profit_factor, pf_first_half, pf_second_half) |
| risk40 | 612 | 64% | 2.48 | 28.8% | -13.0% | -6.7% | -3.4% | 0/5 (fails: profit_factor, annual_return, recent, pf_second_half) |
| pullback | 658 | 63% | 2.73 | 39.4% | -13.9% | -6.5% | +0.8% | 0/5 (fails: profit_factor, win_rate, pf_first_half) |
| grace5 | 600 | 65% | 2.67 | 32.8% | -13.2% | -7.1% | -3.2% | 0/5 (fails: profit_factor) |
| death_cross_exit | 597 | 65% | 2.69 | 32.9% | -13.1% | -7.2% | -2.4% | 0/5 (fails: profit_factor, pf_second_half) |
| golden20 | 72 | 62% | 4.21 | 7.4% | -3.3% | -2.3% | +3.8% | 0/5 (fails: enough_trades, annual_return, win_rate) |
| sma50_exit | 662 | 60% | 2.30 | 30.9% | -13.1% | -6.8% | -3.7% | 0/5 (fails: profit_factor, win_rate, recent, pf_first_half, pf_second_half) |

