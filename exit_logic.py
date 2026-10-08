"""
Exit Logic
==========
Shared stop-price calculation and exit-trigger evaluation.
Used by stop_placement.py and intraday_exit.py.

Trailing stop tiers (mirror run_daily_analysis.py _evaluate_exits):
  pnl >= 100%  →  8% trail below current price
  pnl >= 50%   → 10% trail below current price
  pnl >= 25%   →  break-even + 1.5%
  pnl < 25%    →  8% below avg_cost (default protection for entries)

Exit triggers:
  PRICE_BELOW_50MA  →  current price crossed below 50-day MA
  HARD_LOSS_STOP    →  unrealized loss exceeds 15%
"""

import logging
import re
from datetime import date, datetime, timezone
from typing import Optional, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)


def is_long_equity(pos: dict) -> bool:
    """
    True for a long stock position — the only kind the stop tiers, the 50-day
    exit and the long decision engine understand. A short or an option must
    never be passed to them: a sell stop would add to a short, and "price
    below its 50-day average" is the profitable direction for a short or a put.
    Missing fields mean an older long-stock record; a bad quantity means no.
    """
    try:
        qty = float(pos.get("qty"))
    except (TypeError, ValueError):
        return False
    return (qty > 0
            and pos.get("side", "long") == "long"
            and pos.get("asset_class", "us_equity") == "us_equity")


def untradable_reason(asset: Optional[dict]) -> Optional[str]:
    """
    Why Alpaca will not take an order for this asset, or None if it trades normally.

    A holding whose stock was delisted or acquired (CTLP) stays in the account but
    rejects every order, so it can be neither protected with a stop nor sold. A
    failed lookup returns None: when in doubt the holding is treated as tradable.
    """
    if not isinstance(asset, dict) or "error" in asset:
        return None
    status = asset.get("status")
    if status and status != "active":
        return f"asset is {status} on Alpaca (delisted or acquired)"
    if asset.get("tradable") is False:
        return "asset is not tradable on Alpaca"
    return None


def find_untradable(positions: list, protected, get_asset) -> dict:
    """
    {symbol: reason} for long stock holdings Alpaca will take no order for.

    Only holdings without a live stop are looked up — a working stop order already
    proves the asset trades — so a normal run costs zero or one extra request.
    """
    stuck = {}
    for p in positions or []:
        sym = p.get("symbol")
        if not sym or sym in protected or not is_long_equity(p):
            continue
        try:
            reason = untradable_reason(get_asset(sym))
        except Exception:
            reason = None
        if reason:
            stuck[sym] = reason
    return stuck


SALE_FILLED, SALE_DEAD, SALE_PARTIAL, SALE_WORKING = "filled", "dead", "partial", "working"
_ENDED_UNFILLED = ("canceled", "expired", "rejected")


def sale_status(broker, order_id: Optional[str], wait_seconds: float = 20, poll: float = 2) -> str:
    """
    Where a sell order stands. An accepted sell is not yet a sale.

      filled   every share sold
      dead     the order ended with nothing sold — the shares are still held
      partial  the order ended with some shares sold
      working  anything else: still open, or its status cannot be read

    "working" is the unknown case. It must be tracked until it settles
    (intraday_exit.settle_pending_exits), never assumed to be sold or unsold.
    """
    import time
    if not order_id:
        return SALE_WORKING
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            order = broker.get_order(order_id) or {}
        except Exception:
            order = {}
        status = order.get("status")
        if status == "filled":
            return SALE_FILLED
        if status in _ENDED_UNFILLED:
            # "Nothing sold" needs an explicit, readable zero. A missing or
            # unreadable filled quantity is unknown, not zero.
            try:
                sold = float(order["filled_qty"])
            except (KeyError, TypeError, ValueError):
                return SALE_WORKING
            return SALE_PARTIAL if sold > 0 else SALE_DEAD
        if time.monotonic() >= deadline:
            return SALE_WORKING
        time.sleep(poll)


def split_positions(positions: list) -> Tuple[list, list]:
    """(long stock positions, everything else — shorts, options, other assets)."""
    longs = [p for p in positions or [] if is_long_equity(p)]
    others = [p for p in positions or [] if not is_long_equity(p)]
    return longs, others


def calculate_stop_price(
    current: float,
    pnl_pct: float,
    avg_cost: float,
) -> Tuple[float, str]:
    """
    Calculate the appropriate stop price for a position.

    Args:
        current:  Current market price
        pnl_pct:  Unrealized P&L as a percentage (e.g. 63.5 for +63.5%)
        avg_cost: Average entry price

    Returns:
        (stop_price, tier_description)
    """
    if pnl_pct >= 100:
        return round(current * 0.92, 2), "100%+ gain → 8% trail"
    elif pnl_pct >= 50:
        return round(current * 0.90, 2), "50%+ gain → 10% trail"
    elif pnl_pct >= 25:
        return round(avg_cost * 1.015, 2), "25%+ gain → break-even+1.5%"
    else:
        return round(avg_cost * 0.92, 2), "default → 8% below entry"


def check_exit_triggers(
    symbol: str,
    current_price: float,
    avg_cost: float,
    pnl_pct: float,
    sma50: Optional[float],
) -> Tuple[bool, str, str]:
    """
    Evaluate whether a position should be exited.

    Args:
        symbol:        Stock ticker (for logging)
        current_price: Current market price
        avg_cost:      Average entry price
        pnl_pct:       Unrealized P&L as a percentage (e.g. -10.1)
        sma50:         50-day simple moving average (None if unavailable)

    Returns:
        (should_exit: bool, trigger: str, reason: str)
        trigger is empty string when should_exit is False.
    """
    # Hard loss: down more than 15% — thesis was wrong at entry
    if pnl_pct < -15:
        return (
            True,
            "HARD_LOSS_STOP",
            f"Down {pnl_pct:.1f}% — entry thesis invalidated",
        )

    # Price crossed below 50MA — momentum trend broken
    if sma50 is not None and current_price < sma50:
        pct_below = (sma50 - current_price) / sma50 * 100
        return (
            True,
            "PRICE_BELOW_50MA",
            f"${current_price:.2f} is {pct_below:.1f}% below 50MA ${sma50:.2f}",
        )

    return False, "", ""


MAX_RECONCILE_SHARE = 0.5   # cancelling more than half of all open trades at once is not "unfilled orders"
MIN_OPEN_FOR_SHARE_CHECK = 4


def safe_to_reconcile(trades: list, held_symbols: set, positions_verified: bool) -> Tuple[bool, str]:
    """
    Guard for reconcile_phantom_trades. Reconciliation trusts "the account does
    not hold it" as proof an order never filled, so it must not run when the
    position list is unknown (no broker, API error) or implausible: a failed
    positions call looks exactly like an empty account and would cancel every
    open trade in the log.
    """
    if not positions_verified:
        return False, "positions could not be verified with the broker"
    open_trades = [t for t in trades if t.get("status") == "OPEN"]
    would_cancel = [t for t in open_trades if t.get("symbol") not in held_symbols]
    if (len(open_trades) >= MIN_OPEN_FOR_SHARE_CHECK
            and len(would_cancel) > MAX_RECONCILE_SHARE * len(open_trades)):
        return False, (f"would cancel {len(would_cancel)} of {len(open_trades)} open trades — "
                       f"more likely a data problem than unfilled orders")
    return True, ""


def reconcile_phantom_trades(trades: list, held_symbols: set) -> int:
    """
    Mark OPEN trades for symbols no longer held as CANCELLED, in place.

    A BUY is logged as OPEN as soon as the order is *submitted* (Alpaca
    accepts a DAY limit order), not when it actually fills. If the limit
    price is never reached, Alpaca cancels the order at end of day, but
    trades.json is never told — leaving a phantom OPEN entry that is
    never closed. If a symbol is logged OPEN but the account holds zero
    shares of it, that order never filled.

    Returns the number of trades reconciled.
    """
    reconciled = 0
    for t in trades:
        # A trade with a sell in flight is not an unfilled buy: it settles through
        # intraday_exit.settle_pending_exits, with its real exit recorded.
        if t.get("status") == "OPEN" and t.get("symbol") not in held_symbols and not t.get("pending_exit"):
            t["status"] = "CANCELLED"
            t["exit_reason"] = "ORDER_NOT_FILLED"
            reconciled += 1
    return reconciled


BROKER_STOP, SOLD_AT_BROKER = "BROKER_STOP", "SOLD_AT_BROKER"
_NEW_YORK = ZoneInfo("America/New_York")
_STOP_ORDER_TYPES = ("stop", "stop_limit", "trailing_stop")


def _order_time(value) -> Optional[datetime]:
    """An Alpaca timestamp (2026-10-07T15:02:11.123456789Z) or a trade-log one, as naive UTC."""
    if not value:
        return None
    try:
        text = re.sub(r"(\.\d{6})\d+", r"\1", str(value)).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    # Trade-log timestamps carry no zone; they are written on the Actions runner, which is UTC
    return parsed.astimezone(timezone.utc).replace(tzinfo=None) if parsed.tzinfo else parsed


def settle_broker_exits(trades: list, held_symbols: set, filled_sells, buy_fill) -> Tuple[list, list]:
    """
    Close OPEN trades that the broker sold without the bot: a stop order that
    filled, or a sale made by hand. Edits `trades` in place.

    A stop sits at the broker and fills on its own, so nothing tells
    trades.json. The position just disappears, and reconcile_phantom_trades
    would then write the trade off as a buy that never filled — a real loss
    recorded as no trade at all.

      filled_sells(symbol)  the symbol's filled sell orders, or None if they could not be read
      buy_fill(order_id)    (filled quantity, average fill price) of the buy, or None if unknown

    Returns (closed, unresolved). `unresolved` are trades whose sale could not
    be found or read although the buy filled, or cannot be shown not to have;
    they stay OPEN and must not be cancelled as unfilled.
    """
    closed, unresolved = [], []
    for t in trades:
        sym = t.get("symbol")
        if t.get("status") != "OPEN" or sym in held_symbols or t.get("pending_exit"):
            continue
        bought = buy_fill(t["order_id"]) if t.get("order_id") else None
        if bought is not None and bought[0] <= 0:
            continue                                # the buy never filled: reconcile_phantom_trades' case
        sells = filled_sells(sym)
        entered = _order_time(t.get("entry_ts") or t.get("entry_date"))
        since = []
        for o in sells or []:
            at = _order_time(o.get("filled_at"))
            try:
                qty, price = float(o["filled_qty"]), float(o["filled_avg_price"])
            except (KeyError, TypeError, ValueError):
                continue
            if at and qty > 0 and (entered is None or at >= entered):
                since.append((at, qty, price, o.get("type")))
        shares = float(t.get("shares") or 0)
        sold = sum(q for _, q, _, _ in since)
        if sells is None or not since or sold < shares:
            if sells is None or t.get("order_id"):    # could not look, or it was bought (or may have been) and is gone
                unresolved.append(t)
            continue

        exit_price = sum(q * p for _, q, p, _ in since) / sold
        last_at = max(at for at, _, _, _ in since)
        biggest = max(since, key=lambda row: row[1])
        basis = bought[1] if bought and bought[1] else float(t.get("entry_price") or 0)
        exit_day = last_at.replace(tzinfo=timezone.utc).astimezone(_NEW_YORK).date()
        try:
            hold = (exit_day - date.fromisoformat(t["entry_date"])).days
        except (KeyError, TypeError, ValueError):
            hold = 0
        t.update({
            "status":      "CLOSED",
            "exit_date":   exit_day.isoformat(),
            "exit_ts":     last_at.isoformat(),
            "exit_price":  round(exit_price, 2),
            "exit_reason": BROKER_STOP if biggest[3] in _STOP_ORDER_TYPES else SOLD_AT_BROKER,
            "pnl_pct":     round((exit_price - basis) / basis * 100, 2) if basis else 0,
            "pnl_usd":     round((exit_price - basis) * shares, 2) if basis else 0,
            "hold_days":   hold,
        })
        closed.append(t)
    return closed, unresolved


def record_broker_exits(broker, trades: list, held_symbols: set) -> Tuple[list, list]:
    """settle_broker_exits against the live account. Reads orders only; the caller saves `trades`."""
    sells_cache = {}

    def filled_sells(symbol):
        if symbol not in sells_cache:
            try:
                orders = broker.get_orders(status="closed", symbols=[symbol])
                ok = getattr(broker, "last_orders_ok", False) is True
            except Exception:
                orders, ok = [], False
            sells_cache[symbol] = [o for o in orders if o.get("side") == "sell"
                                   and o.get("status") == "filled"] if ok else None
        return sells_cache[symbol]

    def buy_fill(order_id):
        try:
            order = broker.get_order(order_id) or {}
            return float(order["filled_qty"]), float(order.get("filled_avg_price") or 0)
        except Exception:
            return None

    return settle_broker_exits(trades, held_symbols, filled_sells, buy_fill)


def fetch_sma50(symbol: str) -> Optional[float]:
    """
    Fetch 50-day SMA for a single symbol via yfinance.
    Returns None if data is unavailable or history is too short.

    Prefer get_sma50_map() for multiple symbols — it batches into one call.
    """
    try:
        import yfinance as yf
        hist = yf.Ticker(symbol).history(period="1y")
        closes = hist["Close"].values
        if len(closes) < 50:
            logger.warning(f"{symbol}: insufficient history for 50MA ({len(closes)} bars)")
            return None
        return float(closes[-50:].mean())
    except Exception as e:
        logger.warning(f"{symbol}: could not fetch 50MA — {e}")
        return None


# ── Daily 50MA cache ─────────────────────────────────────────────────────────
# The intraday exit monitor runs ~14×/day. Without caching it would make one
# yfinance call per position per run (20 × 14 = 280 calls/day). Instead we
# build a date-stamped cache once and every subsequent run reads it.

from pathlib import Path as _Path

_SMA50_CACHE = _Path(__file__).parent / "docs" / "data" / "sma50_cache.json"


def _today_str() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d")


def load_sma50_cache() -> dict:
    """Return today's cached {symbol: sma50}, or empty dict if stale/missing."""
    import json
    try:
        if _SMA50_CACHE.exists():
            data = json.loads(_SMA50_CACHE.read_text())
            if data.get("date") == _today_str():
                return data.get("values", {})
    except Exception as e:
        logger.warning(f"Could not read SMA50 cache: {e}")
    return {}


def _write_sma50_cache(values: dict):
    """Write the SMA50 cache atomically (temp file + rename)."""
    import json, os
    payload = {"date": _today_str(), "values": values}
    try:
        _SMA50_CACHE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _SMA50_CACHE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        os.replace(tmp, _SMA50_CACHE)   # atomic on POSIX
    except Exception as e:
        logger.warning(f"Could not write SMA50 cache: {e}")


def get_sma50_map(symbols: list) -> dict:
    """
    Return {symbol: sma50} for the given symbols, using a date-stamped cache.

    On the first call of the day (or when symbols are missing from the cache),
    performs a SINGLE batched yfinance download for the uncached symbols,
    rather than one call per symbol. Subsequent calls read the cache.

    Symbols with insufficient history map to None.
    """
    symbols = list(dict.fromkeys(symbols))   # dedupe, preserve order
    cache = load_sma50_cache()

    missing = [s for s in symbols if s not in cache]
    if missing:
        logger.info(f"SMA50 cache miss for {len(missing)} symbols — batch fetching")
        try:
            import yfinance as yf
            data = yf.download(
                missing, period="1y", auto_adjust=True,
                progress=False, threads=True,
            )
            closes = data["Close"]
            for sym in missing:
                try:
                    if hasattr(closes, "columns") and sym in closes.columns:
                        series = closes[sym].dropna().values
                    elif not hasattr(closes, "columns"):
                        series = closes.dropna().values   # single-symbol frame
                    else:
                        cache[sym] = None
                        continue
                    cache[sym] = float(series[-50:].mean()) if len(series) >= 50 else None
                except Exception:
                    cache[sym] = None
        except Exception as e:
            logger.warning(f"Batch SMA50 fetch failed: {e}")
            for sym in missing:
                cache.setdefault(sym, None)
        _write_sma50_cache(cache)

    return {s: cache.get(s) for s in symbols}
