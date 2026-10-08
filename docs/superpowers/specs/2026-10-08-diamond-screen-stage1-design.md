# Diamond screen, stage 1: universe, inflection screen, watchlist

Date: 2026-10-08. Status: awaiting owner review.

## Why

The large-cap bot buys strength in the S&P 500 and Nasdaq 100. It cannot find
a smaller company early. The owner wants to find "diamonds in the rough":
small and mid-sized companies where three things line up, as they did for
Palantir before it became a household name:

1. **Financial inflection.** Losses shrinking quarter after quarter until they
   cross into profit, with revenue growth holding or speeding up.
2. **A real edge.** Technology or a product customers cannot easily replace.
3. **Visible demand.** Contracts, clients and revenue gain: backlog, prepaid
   revenue, customer wins, and federal awards for companies that sell to
   government.

The agreed end state is an automated paper sleeve: 20% of the account, six to
eight long conviction holds, sold when the thesis breaks. It is built in four
stages. This document specifies stage 1 only.

| Stage | Delivers | Trades? |
|---|---|---|
| **1 (this spec)** | Universe, numbers-only inflection screen, weekly watchlist | No |
| 2 | Evidence packs and AI judgment of edge and demand | No |
| 3 | The sleeve, and its separation from the large-cap bot | Paper |
| 4 | Tracking against benchmarks, review date | Paper |

Stage 1 gives the owner a real list of companies to react to before anything
is bought, and it is the only stage that can be honestly backtested: an AI
model already knows which companies went on to win.

## What stage 1 does

Once a week it produces a ranked list of about 40 companies showing a
financial inflection, with every number behind each rank visible.

### 1. Universe (`diamond_universe.py`)

- **Source:** the SEC's `company_tickers_exchange.json`, NYSE and Nasdaq
  listings only.
- **Common shares only:** drop tickers that are warrants, units, rights or
  preferred shares (suffix patterns), and keep one ticker per company (CIK).
- **Size:** market value $300M to $10B. Market value = shares outstanding
  (SEC `dei:EntityCommonStockSharesOutstanding`, latest) x last close.
- **Tradable:** median daily dollar volume over 60 sessions of at least $5M,
  and price at least $5.
- **Excluded industries:** SIC 6000-6799 (banks, insurers, property trusts,
  investment vehicles). "Losses turning to profit" does not mean the same
  thing for them. SIC comes from the SEC submissions endpoint, fetched only
  for companies that pass size and volume, and cached in
  `docs/data/diamond_universe.json` (refreshed when older than 30 days).
- Companies with no revenue drop out in the screen, not here.

Prices and volume come from yfinance in one bulk download, as the backtest
already does.

### 2. Financial data (`diamond_data.py`)

All from the SEC XBRL "frames" API: one request returns one figure for every
company for one period. No key; a contact email is required in the
User-Agent (see Open items).

| Figure | Tags (first available wins) | Kind |
|---|---|---|
| Revenue | `RevenueFromContractWithCustomerExcludingAssessedTax`, `Revenues` | per quarter |
| Operating income | `OperatingIncomeLoss` | per quarter |
| Gross profit | `GrossProfit`, else revenue minus `CostOfRevenue` | per quarter |
| R&D | `ResearchAndDevelopmentExpense` | per quarter, shown only |
| Cash | `CashAndCashEquivalentsAtCarryingValue` + `ShortTermInvestments` | point in time |
| Shares | `dei:EntityCommonStockSharesOutstanding` | point in time |
| Backlog | `RevenueRemainingPerformanceObligation` | point in time |
| Prepaid revenue | `ContractWithCustomerLiability`, `ContractWithCustomerLiabilityCurrent`, `DeferredRevenueCurrent` | point in time |

Eight quarters are needed per company (four recent, four a year earlier).

Two known traps, both handled explicitly and both covered by tests:

- **Fourth quarters.** Companies report the full year in the 10-K, not Q4
  alone, so the quarterly frame for a fiscal Q4 is usually empty. Q4 is
  derived as the annual figure minus the three reported quarters. If any of
  the three is missing, Q4 is unknown, not zero.
- **Latest quarter.** Companies file at different times. Each company is
  scored on its own latest reported quarter; a company whose latest quarter
  ended more than 160 days ago is dropped as stale.

Unknown is never zero. A company missing a figure the gates need is dropped
and counted in the coverage report; a company missing an optional figure
(backlog, prepaid revenue) is scored on the rest without penalty.

### 3. Inflection screen (`diamond_screen.py`)

Pure functions over the data above. No network.

**Gates** (fail any one and the company is out; each failure is counted):

| Gate | Rule |
|---|---|
| Has revenue | Trailing-twelve-month (TTM) revenue at least $50M |
| Growing | TTM revenue up at least 15% on the year before |
| Turning | TTM operating margin improved at least 5 points on the year before |
| Near or past break-even | TTM operating margin now at least -15% |
| Was not already rich | TTM operating margin a year ago at most +5% |
| Edge proxy | TTM gross margin at least 40%, or up at least 3 points on the year |
| Runway | Operating income positive, or cash covers at least 2 years of TTM operating loss |
| Dilution | Shares outstanding up at most 10% on the year |

**Score** (0-100) for companies that pass, so they can be ranked:

| Part | Weight | Measures |
|---|---|---|
| Profit inflection | 40 | Size of the TTM margin improvement; how many of the last four quarters improved on the one before; bonus if the latest quarter is profitable and the same quarter a year ago was not |
| Revenue | 25 | TTM growth; bonus if the latest quarter's year-on-year growth is higher than two quarters earlier |
| Edge proxy | 15 | Gross margin level and its change |
| Demand | 10 | Backlog growth and prepaid-revenue growth, each compared with revenue growth. If neither is reported, this weight is spread across the other parts |
| Safety | 10 | Runway and low dilution beyond the gate minimum |

Every threshold and weight above is a starting value. They are constants at
the top of the file, and the first real list is expected to change them.

### 4. Output

- `docs/data/diamonds.json`: run date, the top 40 with every component score
  and the raw figures behind it, gate failure counts, and a coverage report
  (companies in the universe, with usable data, passing gates).
- `docs/data/diamond_history.json`: one row per company per week (rank,
  score, parts), so a company's trend over weeks can be read. Capped at 104
  weeks.
- A "Diamond watchlist" section in the Saturday weekly review issue: the top
  15 with score, change in rank since last week, and the three headline
  numbers (margin change, revenue growth, market value).

No dashboard page in this stage.

### 5. Schedule

`diamond_screen.yml`, started by the dispatcher on Saturday at 07:30 ET,
before the rest of the Saturday chain so the weekly review can include it.
Expected run time under 15 minutes (about 80 SEC requests, one bulk price
download, SIC lookups only for new names).

### 6. Historical check (`--as-of`)

The screen accepts `--as-of YYYYQn` and runs on the data that existed then.
Used for two things before the screen is trusted:

- **Palantir check.** Run as of 2023 Q1 and 2023 Q2 with the size cap lifted.
  Palantir should pass the gates and rank near the top. If it does not, the
  gates do not capture the pattern the owner described and are wrong.
- **Hit rate.** Run as of several past quarters and report what the top 40
  did over the following 12 months against the Russell 2000 ETF (IWM).

Limit, stated in the output: the ticker list is today's, so companies that
have since been delisted or acquired are missing and past results look
better than they were.

## Failure handling

The screen fails closed, like the large-cap screener's coverage check:

- If fewer than 2,500 companies have revenue in the latest quarter's data,
  or price data is missing for more than 20% of the universe, the run writes
  no new list, leaves last week's in place, exits non-zero, and the existing
  "Open issue on failure" step reports it.
- SEC requests are rate-limited to 5 a second and retried with backoff; a
  frame that cannot be fetched fails the run rather than being treated as
  empty.
- Filing data is numeric only in this stage. No filing text is read.

## Testing

Unit tests with small fixtures, fetchers injected (the pattern used
throughout this repo):

- Q4 derivation, including a missing quarter giving unknown.
- Latest-quarter selection and the stale cut-off.
- Each gate at its boundary, and unknown inputs failing a gate without error.
- Scoring, including redistribution of the demand weight.
- Universe filters: share-class suffixes, one ticker per CIK, SIC exclusion,
  size and volume bounds.
- Coverage guard: a short frame fails the run and keeps the old file.
- Weekly review section renders with and without a previous week.

## Out of scope for stage 1

- Reading filing text, AI judgment, contract and client extraction (stage 2).
- Federal awards from USAspending (stage 2: it needs company-name matching,
  and it only applies to government sellers).
- News and social trend for these companies (stage 2).
- Any order, any change to the large-cap bot's trading, the sleeve tag
  (stage 3).

## Open items for the owner

1. **SEC contact email.** The SEC requires a real contact address in the
   User-Agent. The repo currently sends a placeholder. It should be set as
   the `SEC_EDGAR_USER_AGENT` repository variable.
2. **Starting thresholds.** The gates above are a first guess at "trending
   from negative to positive". The Palantir check is the test of whether
   they are right.
