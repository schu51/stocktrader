# Daily eval — 2026-11-10

Rules live: **breakeven5_spy2** since 2026-11-10

**Alerts**

- Account is 11.5% below its peak
- Stop exits filled 4.4% below their stop on average (last 10)

## Live trades

| window | trades | win rate | profit factor | avg trade | P&L |
|---|---|---|---|---|---|
| closed today | 0 | | | | |
| last 20 trades | 20 | 15% | 0.11 | -6.48% | $-3,815 |
| last 30 days | 36 | 22% | 0.31 | -4.09% | $-4,061 |
| current rules | 0 | | | | |
| all time | 91 | 20% | 0.61 | -1.48% | $-4,754 |

Account $106,397, -11.5% from peak. 30 days: -7.7% vs SPY -5.6%. Since current rules: n/a vs SPY n/a.

Stop exits (last 10): filled -4.4% vs their stop on average, worst -33.0% (VCYT), 5 gapped more than 2%.

Tracking (30 days): live -7.7% vs backtest of the same rules -5.8%.

## Tournament (3-year backtest)

| rules | trades | win rate | PF | annual | max DD | worst month | last 6 mo | streak |
|---|---|---|---|---|---|---|---|---|
| **live** | 660 | 60% | 2.32 | 31.0% | -13.1% | -6.3% | -1.3% | |
| be3 | 746 | 65% | 2.18 | 27.4% | -13.2% | -6.7% | +2.9% | 0/5 (fails: profit_factor, annual_return, pf_second_half) |
| trail20 | 698 | 60% | 2.18 | 29.3% | -12.8% | -7.5% | +2.2% | 0/5 (fails: profit_factor, pf_first_half, pf_second_half) |
| spy3 | 490 | 66% | 2.84 | 29.7% | -10.1% | -4.7% | -2.6% | 0/5 (fails: recent, pf_second_half) |
| spy1 | 773 | 58% | 2.13 | 32.1% | -14.5% | -9.9% | -7.5% | 0/5 (fails: profit_factor, drawdown, recent, pf_second_half) |
| earn_exit15 | 743 | 61% | 2.10 | 26.8% | -12.8% | -5.8% | +2.0% | 0/5 (fails: profit_factor, annual_return, pf_first_half, pf_second_half) |
| risk40 | 665 | 60% | 2.30 | 28.7% | -13.4% | -6.1% | +0.3% | 0/5 (fails: profit_factor, annual_return, pf_second_half) |

