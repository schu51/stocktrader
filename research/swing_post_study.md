# Swing post study — 2026-11-12

3-year replay from 2023-11-12 on 520 tickers, same costs as the daily eval. Each row changes the live rules as shown. Position stats count a partial sale and the rest once.

| variant | annual | max DD | worst month | last 6 mo | positions | pos win | pos PF | invested | gates |
|---|---|---|---|---|---|---|---|---|---|
| **live (champion)** | +32.8% | -12.4% | -6.8% | -2.8% | 589 | +65% | 2.715 | 60% | – |
| tight15 | +32.7% | -13.2% | -7.7% | -4.1% | 662 | +59% | 2.434 | 64% | 4/8 |
| tight30 | +23.4% | -14.4% | -8.2% | -7.5% | 704 | +58% | 1.993 | 62% | 1/8 |
| adr4_8 | +24.6% | -9.3% | -6.2% | -1.7% | 294 | +67% | 2.926 | 26% | 7/8 |
| adr3 | +29.9% | -12.5% | -8.5% | -3.4% | 448 | +67% | 2.734 | 43% | 4/8 |
| ext3 | +32.8% | -12.5% | -7.4% | -1.8% | 588 | +64% | 2.715 | 60% | 6/8 |
| swing_exit | +23.2% | -11.7% | -7.9% | +1.4% | 801 | +65% | 1.854 | 44% | 4/8 |
| swing_exit_half | +22.5% | -11.1% | -7.7% | +2.2% | 805 | +65% | 1.826 | 42% | 4/8 |
| qqq20 | +31.9% | -12.1% | -7.1% | -3.7% | 586 | +64% | 2.681 | 58% | 5/8 |
| vix15 | +16.6% | -9.9% | -5.0% | -2.5% | 327 | +61% | 2.448 | 36% | 4/8 |
| vix20 | +32.6% | -12.4% | -6.8% | -2.9% | 581 | +65% | 2.7 | 61% | 5/8 |
| breadth50 | +34.6% | -12.6% | -7.6% | -4.8% | 590 | +66% | 2.886 | 59% | 7/8 |
| post_all | +0.3% | -1.0% | -0.4% | +0.0% | 12 | +75% | 2.159 | 1% | 4/8 |
| post_lite | +14.7% | -12.2% | -10.4% | -4.5% | 904 | +61% | 1.472 | 47% | 3/8 |

Gates are the daily eval's promotion gates against the champion (all must pass, 5 evals in a row, before a rule change is proposed).

## Variant settings

- **tight15**: `{"tight_boost": 0.15}`
- **tight30**: `{"tight_boost": 0.3}`
- **adr4_8**: `{"min_adr": 4.0, "max_adr": 8.0}`
- **adr3**: `{"min_adr": 3.0}`
- **ext3**: `{"max_ext_atr20": 3.0}`
- **swing_exit**: `{"swing_exit": true}`
- **swing_exit_half**: `{"swing_exit": true, "partial_size": 0.5}`
- **qqq20**: `{"qqq_above_ema20": true}`
- **vix15**: `{"max_vix": 15.0}`
- **vix20**: `{"max_vix": 20.0}`
- **breadth50**: `{"min_breadth": 0.5}`
- **post_all**: `{"entry_trigger": "breakout", "tight_boost": 0.3, "min_adr": 4.0, "max_adr": 8.0, "max_ext_atr20": 3.0, "swing_exit": true, "qqq_above_ema20": true, "max_vix": 15.0, "min_breadth": 0.5}`
- **post_lite**: `{"tight_boost": 0.3, "max_ext_atr20": 3.0, "swing_exit": true, "qqq_above_ema20": true}`

Live rules: `{"exit_line": "sma200", "w_rs": 0.8, "w_thesis": 0.2, "atr_stop": 2.5, "min_above_50ma": 0.06, "stale_days": 60, "stale_gain": 10.0, "min_spy_above_50ma": 0.02, "earnings_blackout": 5}`
