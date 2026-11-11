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

Account $107,034, -11.0% from peak. 30 days: -7.1% vs SPY -2.4%. Since current rules: +0.6% vs SPY +0.9%.

Stop exits (last 10): filled -3.5% vs their stop on average, median -0.6%, worst -24.6% (VCYT), 2 gapped more than 2%. Stop known from the filled order for 0 of 10; the rest rebuilt from the entry rule.

Tracking (30 days): live -7.1% vs backtest of the same rules -6.6%.

## Tournament (3-year backtest)

| rules | trades | win rate | PF | annual | max DD | worst month | last 6 mo | streak |
|---|---|---|---|---|---|---|---|---|
| **live** | 604 | 64% | 2.70 | 33.4% | -13.3% | -7.1% | -0.2% | |
| be3 | 692 | 68% | 2.54 | 32.5% | -12.7% | -7.1% | +1.6% | 0/5 (fails: profit_factor, pf_second_half) |
| trail20 | 681 | 64% | 2.20 | 28.0% | -13.8% | -8.8% | +3.3% | 0/5 (fails: profit_factor, annual_return, pf_first_half, pf_second_half) |
| spy3 | 452 | 69% | 3.04 | 29.5% | -10.6% | -6.4% | -3.8% | 0/5 (fails: annual_return, recent, pf_second_half) |
| spy1 | 704 | 62% | 2.35 | 34.5% | -15.1% | -8.9% | -6.4% | 0/5 (fails: profit_factor, drawdown, win_rate, recent, pf_second_half) |
| earn_exit15 | 692 | 66% | 2.41 | 32.0% | -13.9% | -6.4% | +3.6% | 0/5 (fails: profit_factor, pf_first_half, pf_second_half) |
| risk40 | 611 | 64% | 2.45 | 28.8% | -13.2% | -6.7% | -1.6% | 0/5 (fails: profit_factor, annual_return, recent, pf_first_half, pf_second_half) |
| pullback | 663 | 62% | 2.63 | 37.8% | -14.7% | -7.3% | +3.0% | 0/5 (fails: profit_factor, drawdown, win_rate, pf_first_half) |
| grace5 | 604 | 64% | 2.70 | 33.4% | -13.3% | -7.1% | -0.2% | 0/5 (fails: profit_factor) |
| death_cross_exit | 597 | 64% | 2.63 | 31.9% | -13.2% | -7.1% | -0.2% | 0/5 (fails: profit_factor, recent, pf_first_half, pf_second_half) |
| golden20 | 72 | 61% | 4.15 | 7.4% | -3.3% | -2.3% | +3.5% | 0/5 (fails: enough_trades, annual_return, win_rate) |
| sma50_exit | 663 | 60% | 2.29 | 30.4% | -13.1% | -6.9% | -1.4% | 0/5 (fails: profit_factor, annual_return, win_rate, recent, pf_first_half, pf_second_half) |

