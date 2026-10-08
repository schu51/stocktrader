"""
Rotation
========
Sells a holding that has stopped keeping pace, so a stronger candidate can
take its slot. Without this a stock drifting up 1% a month above its 50-day
average passes every exit rule and keeps its slot indefinitely.

The measure is the screener's RS rank: where the stock's 3-12 month return
sits among every stock in the universe (99 = top 1%). A new position needs a
rank of 70 or better. A holding is a laggard when its rank has fallen below
50 — it is no longer in the top half — and it has been held at least 20 days.

Reference models this follows:
  - Clenow, "Stocks on the Move": rank the index by momentum, hold the top
    names, sell any holding that drops out of the top 20% of the ranking.
  - MSCI Momentum Index buffer rules: a holding keeps its place while it stays
    inside a band wider than the entry band, so names near the line are not
    churned. Here: enter at 70, leave below 50.

A laggard is only sold when that does something:
  - today's buy signals need more cash than the account can spend while
    keeping its cash reserve — the normal case, since cash is the limit; or
  - a position cap is configured (it is not by default) and is full or exceeded.
At most MAX_ROTATIONS_PER_DAY a day. Unknown is never "weak": a holding with
no rank today, no entry date, or on a day the screener's data is incomplete
is left alone.

Every rotation is appended to docs/data/rotation_log.json with the rank,
days held and gain at the time, so what the sold names and their
replacements did afterwards can be compared.

Stale exit (since 2026-10-08)
-----------------------------
A second, simpler rule that needs no cash shortfall: a holding kept
STALE_DAYS (60) with a gain under STALE_GAIN_PCT (10%) is sold. It has broken
no rule, but it has had two months and done little. In the three-year
backtest the 50 positions this sold at an average +7.4% finished at +7.0%
when held instead, 24 days later; the account did no worse at any setting
near this one (research/backtest_summary_stale_*.json). The size of the gain
is not established. Review with the other rules on 2026-12-05.
"""

import json
import logging
import os
from datetime import date
from pathlib import Path
from typing import Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.resolve()
ROTATION_LOG = ROOT / "docs" / "data" / "rotation_log.json"

MIN_HOLD_DAYS = 20            # calendar days, the same patience the thesis-failed exit uses
ROTATE_BELOW_RANK = 50        # buffer under the entry bar (RS rank 70)
MAX_ROTATIONS_PER_DAY = 2
ONE_POSITION = 0.05           # share of the account a full-size position takes (sizing target, high conviction)
MIN_RANK_COVERAGE = 0.80      # below this the screener's universe is suspect (see screener.MIN_DATA_COVERAGE)
EXIT_REASON = "ROTATED_OUT"

STALE_DAYS = 60               # calendar days held
STALE_GAIN_PCT = 10.0         # gain below this after STALE_DAYS is "not going anywhere"
MAX_STALE_PER_DAY = 3         # not in the backtest: a brake in case a data fault makes many holdings look stale
STALE_REASON = "STALE"


def load_ranks(screener: Optional[Dict], today: date) -> Optional[Dict[str, int]]:
    """
    {symbol: RS rank} from today's screen, or None if it cannot be trusted:
    not from today, incomplete price data, or written by a screener that does
    not publish ranks. None means no rotation today.
    """
    if not isinstance(screener, dict):
        return None
    if (screener.get("generated_at") or "")[:10] != today.isoformat():
        return None
    coverage = screener.get("data_coverage")
    if not isinstance(coverage, (int, float)) or coverage < MIN_RANK_COVERAGE:
        return None
    ranks = screener.get("rs_ranks")
    if not isinstance(ranks, dict) or not ranks:
        return None
    return {s: int(r) for s, r in ranks.items() if isinstance(r, (int, float)) and not isinstance(r, bool)}


def held_days(symbol: str, trades: Iterable[Dict], today: date) -> Optional[int]:
    """
    Days since the latest OPEN trade-log entry for the symbol. None if there is
    none, or if that trade already has a sell in flight (pending_exit): it must
    not be sold a second time.
    """
    for t in reversed(list(trades or [])):
        if t.get("symbol") == symbol and t.get("status") == "OPEN":
            if t.get("pending_exit"):
                return None
            try:
                return (today - date.fromisoformat(str(t.get("entry_date"))[:10])).days
            except (TypeError, ValueError):
                return None
    return None


def find_laggards(holdings: Dict[str, Dict], trades: Iterable[Dict], ranks: Optional[Dict[str, int]],
                  today: date, exclude: Iterable[str] = ()) -> List[Dict]:
    """
    Holdings eligible to be rotated out, weakest rank first.
    `holdings`: {symbol: {"pnl_pct": ...}}. `exclude`: untradable or already-exited symbols.
    """
    if not ranks:
        return []
    trades = list(trades or [])
    skip = set(exclude or ())
    out = []
    for sym, h in (holdings or {}).items():
        rank = ranks.get(sym)
        days = held_days(sym, trades, today)
        if sym in skip or rank is None or days is None:
            continue
        if days >= MIN_HOLD_DAYS and rank < ROTATE_BELOW_RANK:
            out.append({"symbol": sym, "rs_rank": rank, "hold_days": days, "pnl_pct": (h or {}).get("pnl_pct"),
                        "market_value": (h or {}).get("market_value")})
    return sorted(out, key=lambda x: (x["rs_rank"], x["symbol"]))


def find_stale(holdings: Dict[str, Dict], trades: Iterable[Dict], today: date,
               exclude: Iterable[str] = (), ranks: Optional[Dict[str, int]] = None) -> List[Dict]:
    """
    Holdings to sell under the stale rule, smallest gain first. Unknown is
    never stale: no entry date, a sell already in flight, or no readable gain
    leaves the holding alone. `ranks` only adds the RS rank to the record.
    """
    trades = list(trades or [])
    skip = set(exclude or ())
    out = []
    for sym, h in (holdings or {}).items():
        days = held_days(sym, trades, today)
        pnl = (h or {}).get("pnl_pct")
        if sym in skip or days is None or not isinstance(pnl, (int, float)) or isinstance(pnl, bool) or pnl != pnl:
            continue
        if days >= STALE_DAYS and pnl < STALE_GAIN_PCT:
            out.append({"symbol": sym, "rs_rank": (ranks or {}).get(sym), "hold_days": days, "pnl_pct": pnl,
                        "market_value": (h or {}).get("market_value")})
    return sorted(out, key=lambda x: (x["pnl_pct"], x["symbol"]))


def stale_allowance(trades: Iterable[Dict], today: date) -> int:
    """
    How many more stale sales may be sent today. MAX_STALE_PER_DAY is a limit
    on the day, not on one run: the daily job can run more than once (a manual
    re-run, a catch-up), and each run would otherwise get a fresh allowance.
    Counts stale exits already recorded today and stale sells still in flight.
    """
    day = today.isoformat()
    used = 0
    for t in trades or []:
        pending = t.get("pending_exit") or {}
        if t.get("exit_reason") == STALE_REASON and str(t.get("exit_date") or "")[:10] == day:
            used += 1
        elif t.get("status") == "OPEN" and pending.get("trigger") == STALE_REASON \
                and str(pending.get("sent") or "")[:10] == day:
            used += 1
    return max(0, MAX_STALE_PER_DAY - used)


def _value(laggard: Dict) -> float:
    try:
        return max(0.0, float(laggard.get("market_value") or 0))
    except (TypeError, ValueError):
        return 0.0


def planning_proceeds(laggards: List[Dict], spendable: float, total_value: float) -> float:
    """
    Cash to add when sizing today's buys: what selling today's laggards would
    raise — but only when the account cannot otherwise fund one full position
    (ONE_POSITION of its value). With cash to spare nothing is added, so sizing
    is untouched. Without this, a cash-short engine sizes every buy at zero and
    there is never a replacement to rotate for.
    """
    if total_value <= 0 or spendable >= ONE_POSITION * total_value:
        return 0.0
    return sum(_value(l) for l in laggards[:MAX_ROTATIONS_PER_DAY])


def sells_for_cash(laggards: List[Dict], shortfall: float) -> int:
    """How many laggards (weakest first) must be sold to cover a cash shortfall. 0 if there is none."""
    if shortfall <= 0:
        return 0
    raised = 0.0
    for n, lag in enumerate(laggards[:MAX_ROTATIONS_PER_DAY], start=1):
        raised += _value(lag)
        if raised >= shortfall:
            return n
    return min(len(laggards), MAX_ROTATIONS_PER_DAY)


def planning_count(held: int, cap: Optional[int], n_laggards: int) -> int:
    """
    The position count buys should be sized against, when a position cap is
    configured. When the cap is full but rotation can free a slot, the engine
    must see that slot or it sizes every buy at zero.
    """
    if cap is None:
        return held
    room = min(n_laggards, MAX_ROTATIONS_PER_DAY)
    return held - room if held >= cap and held - room < cap else held


def sells_wanted(n_laggards: int, held: int, cap: Optional[int], n_buys: int) -> int:
    """With a position cap configured: laggards to sell to get back under it and seat today's buys."""
    if cap is None:
        return 0
    over = max(0, held - cap)
    free = max(0, cap - held)
    return max(0, min(n_laggards, MAX_ROTATIONS_PER_DAY, over + max(0, n_buys - free)))


def record(entries: List[Dict], path: Optional[Path] = None) -> None:
    """Append rotations to the log (atomic write). A log that cannot be written is logged, not raised."""
    if not entries:
        return
    try:
        path = Path(path or ROTATION_LOG)
        try:
            log = json.loads(path.read_text())
            if not isinstance(log, list):
                log = []
        except Exception:
            log = []
        log.extend(entries)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(log, indent=2, default=str))
        os.replace(tmp, path)
    except Exception as e:
        logger.warning(f"Could not write the rotation log: {e}")
