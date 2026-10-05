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
  - a buy signal needs its slot (the position cap is full), or
  - the account holds more positions than the cap, to get back to it.
At most MAX_ROTATIONS_PER_DAY a day. Unknown is never "weak": a holding with
no rank today, no entry date, or on a day the screener's data is incomplete
is left alone.

Every rotation is appended to docs/data/rotation_log.json with the rank,
days held and gain at the time, so what the sold names and their
replacements did afterwards can be compared.
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
MIN_RANK_COVERAGE = 0.80      # below this the screener's universe is suspect (see screener.MIN_DATA_COVERAGE)
EXIT_REASON = "ROTATED_OUT"


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
    """Days since the latest OPEN trade-log entry for the symbol, or None if there is none."""
    for t in reversed(list(trades or [])):
        if t.get("symbol") == symbol and t.get("status") == "OPEN":
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
            out.append({"symbol": sym, "rs_rank": rank, "hold_days": days, "pnl_pct": (h or {}).get("pnl_pct")})
    return sorted(out, key=lambda x: (x["rs_rank"], x["symbol"]))


def planning_count(held: int, cap: int, n_laggards: int) -> int:
    """
    The position count buys should be sized against. When the cap is full but
    rotation can free a slot, the engine must see that slot or it sizes every
    buy at zero and there is never a replacement to rotate for.
    """
    room = min(n_laggards, MAX_ROTATIONS_PER_DAY)
    return held - room if held >= cap and held - room < cap else held


def sells_wanted(n_laggards: int, held: int, cap: int, n_buys: int) -> int:
    """How many laggards to sell: enough to get back under the cap and seat today's buys, no more."""
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
