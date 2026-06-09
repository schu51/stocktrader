# Agent Mesh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build five production agents that close the 16-hour blindspot between daily runs, protect $80k+ of unprotected positions intraday, and give the model situational awareness before and after market hours.

**Architecture:** Shared exit logic extracted to `exit_logic.py`; each agent is a self-contained Python script triggered by a GitHub Actions workflow. Agents write JSON to `docs/data/` which the dashboard reads. All agents use the existing `AlpacaBroker` from `alpaca_broker.py` — no new API clients.

**Tech Stack:** Python 3.12, GitHub Actions (cron + workflow_run), yfinance, existing `AlpacaBroker`, `pytest`

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `exit_logic.py` | Shared stop-price calculation and exit-trigger evaluation |
| Create | `stop_placement.py` | Place missing stop orders at market open |
| Create | `intraday_exit.py` | Monitor and execute exits every 30 min during market hours |
| Create | `postmarket_reconciler.py` | End-of-day reconciliation report |
| Modify | `premarket_scan.py` | Add JSON output to `docs/data/premarket.json` |
| Create | `.github/workflows/premarket.yml` | 6:00 AM PDT Mon–Fri |
| Create | `.github/workflows/stop_placement.yml` | 9:31 AM EDT Mon–Fri (1 min after open) |
| Create | `.github/workflows/intraday_exit.yml` | :15 and :45 past each hour, 9 AM–4 PM EDT |
| Create | `.github/workflows/postmarket.yml` | 4:15 PM EDT Mon–Fri |
| Create | `.github/workflows/failure_alert.yml` | Fires when daily_trade.yml fails |
| Create | `tests/test_exit_logic.py` | Unit tests for shared exit logic |
| Create | `tests/test_stop_placement.py` | Unit tests for stop placement logic |
| Delete | `n8n_trading_workflow.json` | Dead code — replaced by GitHub Actions |
| Delete | `orchestrator.py` | Old data infrastructure, broken relative imports |
| Delete | `demo.py` | Old test script for deleted infrastructure |

---

## Task 1: Cleanup Dead Code

**Files:**
- Delete: `n8n_trading_workflow.json`
- Delete: `orchestrator.py`
- Delete: `demo.py`

- [ ] **Step 1: Verify nothing imports orchestrator or demo**

```bash
grep -r "from orchestrator\|import orchestrator\|from demo\|import demo" *.py
```
Expected: no output (nothing depends on them)

- [ ] **Step 2: Delete the files**

```bash
git rm n8n_trading_workflow.json orchestrator.py demo.py
```

- [ ] **Step 3: Commit**

```bash
git commit -m "Remove dead code: n8n workflow, old orchestrator, demo script"
```

---

## Task 2: Create Shared Exit Logic Module

**Files:**
- Create: `exit_logic.py`
- Create: `tests/test_exit_logic.py`

This module is the foundation for both `stop_placement.py` and `intraday_exit.py`. It must not import from `run_daily_analysis.py` (circular dependency risk).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_exit_logic.py`:

```python
import pytest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))


def test_stop_price_100pct_gain():
    from exit_logic import calculate_stop_price
    stop, tier = calculate_stop_price(current=100.0, pnl_pct=105.0, avg_cost=48.0)
    assert stop == 92.0          # 8% trail: 100 * 0.92
    assert "8%" in tier


def test_stop_price_50pct_gain():
    from exit_logic import calculate_stop_price
    stop, tier = calculate_stop_price(current=80.0, pnl_pct=60.0, avg_cost=50.0)
    assert stop == 72.0          # 10% trail: 80 * 0.90
    assert "10%" in tier


def test_stop_price_25pct_gain():
    from exit_logic import calculate_stop_price
    stop, tier = calculate_stop_price(current=65.0, pnl_pct=30.0, avg_cost=50.0)
    assert stop == round(50.0 * 1.015, 2)   # break-even + 1.5%
    assert "break-even" in tier


def test_stop_price_losing_position():
    from exit_logic import calculate_stop_price
    stop, tier = calculate_stop_price(current=45.0, pnl_pct=-10.0, avg_cost=50.0)
    assert stop == round(50.0 * 0.92, 2)    # 8% below entry cost
    assert "entry" in tier.lower() or "default" in tier.lower()


def test_exit_trigger_below_50ma():
    from exit_logic import check_exit_triggers
    should_exit, trigger, reason = check_exit_triggers(
        symbol="TEST", current_price=95.0, avg_cost=100.0,
        pnl_pct=-5.0, sma50=100.0
    )
    assert should_exit is True
    assert trigger == "PRICE_BELOW_50MA"
    assert "50MA" in reason


def test_exit_trigger_hard_loss():
    from exit_logic import check_exit_triggers
    should_exit, trigger, reason = check_exit_triggers(
        symbol="TEST", current_price=82.0, avg_cost=100.0,
        pnl_pct=-18.0, sma50=70.0   # above 50MA but huge loss
    )
    assert should_exit is True
    assert trigger == "HARD_LOSS_STOP"


def test_no_exit_healthy_position():
    from exit_logic import check_exit_triggers
    should_exit, trigger, reason = check_exit_triggers(
        symbol="TEST", current_price=120.0, avg_cost=100.0,
        pnl_pct=20.0, sma50=110.0
    )
    assert should_exit is False
    assert trigger == ""


def test_no_exit_when_no_sma50():
    from exit_logic import check_exit_triggers
    # If we can't compute 50MA, don't exit on that basis
    should_exit, trigger, reason = check_exit_triggers(
        symbol="TEST", current_price=90.0, avg_cost=100.0,
        pnl_pct=-10.0, sma50=None
    )
    # Should still catch hard loss at -15%+, but not fire on missing 50MA
    assert should_exit is False
```

- [ ] **Step 2: Run tests — verify they all fail**

```bash
cd /Users/alexschumacher/stocktrader && python3 -m pytest tests/test_exit_logic.py -v 2>&1 | head -20
```
Expected: `ModuleNotFoundError: No module named 'exit_logic'`

- [ ] **Step 3: Create `exit_logic.py`**

```python
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
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


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


def fetch_sma50(symbol: str) -> Optional[float]:
    """
    Fetch 50-day SMA for a symbol via yfinance.
    Returns None if data is unavailable or history is too short.
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
```

- [ ] **Step 4: Run tests — verify they all pass**

```bash
cd /Users/alexschumacher/stocktrader && python3 -m pytest tests/test_exit_logic.py -v
```
Expected: 8 tests pass

- [ ] **Step 5: Commit**

```bash
git add exit_logic.py tests/test_exit_logic.py
git commit -m "Add shared exit_logic module with unit tests"
```

---

## Task 3: Stop Placement Agent

**Files:**
- Create: `stop_placement.py`
- Create: `tests/test_stop_placement.py`
- Create: `.github/workflows/stop_placement.yml`

Runs at 9:31 AM EDT (13:31 UTC) Mon–Fri — one minute after market open. Places a stop order in Alpaca for every position that has none. If a stop already exists, skips it.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_stop_placement.py`:

```python
import pytest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))


def make_position(symbol, qty, avg_cost, current_price, pnl_pct):
    return {
        "symbol": symbol,
        "qty": qty,
        "avg_entry_price": avg_cost,
        "current_price": current_price,
        "unrealized_plpc": pnl_pct / 100,  # Alpaca returns decimal
    }


def test_build_protected_set():
    from stop_placement import build_protected_set
    orders = [
        {"side": "sell", "type": "stop", "symbol": "AAPL"},
        {"side": "sell", "type": "limit", "symbol": "MSFT"},  # not a stop
        {"side": "buy",  "type": "stop", "symbol": "TSLA"},   # buy side, ignore
    ]
    protected = build_protected_set(orders)
    assert "AAPL" in protected
    assert "MSFT" not in protected
    assert "TSLA" not in protected


def test_positions_needing_stops():
    from stop_placement import positions_needing_stops
    positions = [
        make_position("AAPL", 10, 100, 120, 20),
        make_position("MSFT", 5,  200, 190, -5),
    ]
    protected = {"AAPL"}
    result = positions_needing_stops(positions, protected)
    assert len(result) == 1
    assert result[0]["symbol"] == "MSFT"


def test_positions_needing_stops_all_protected():
    from stop_placement import positions_needing_stops
    positions = [make_position("AAPL", 10, 100, 120, 20)]
    protected = {"AAPL"}
    result = positions_needing_stops(positions, protected)
    assert result == []


def test_calculate_stop_for_position_winner():
    from stop_placement import calculate_stop_for_position
    pos = make_position("COHU", 17, 24.29, 56.77, 133.7)
    stop, tier = calculate_stop_for_position(pos)
    assert stop == round(56.77 * 0.92, 2)   # 100%+ → 8% trail
    assert "8%" in tier


def test_calculate_stop_for_position_loser():
    from stop_placement import calculate_stop_for_position
    pos = make_position("AMD", 6, 540.69, 491.66, -9.1)
    stop, tier = calculate_stop_for_position(pos)
    assert stop == round(540.69 * 0.92, 2)  # default → 8% below entry
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
cd /Users/alexschumacher/stocktrader && python3 -m pytest tests/test_stop_placement.py -v 2>&1 | head -10
```
Expected: `ModuleNotFoundError: No module named 'stop_placement'`

- [ ] **Step 3: Create `stop_placement.py`**

```python
"""
Stop Placement Agent
====================
Runs at 9:31 AM EDT Mon–Fri (1 minute after market open).
Places a GTC stop order in Alpaca for every open position that has none.

Positions that already have an active stop/stop_limit sell order are skipped.
Stop price is calculated using the same trailing stop tiers as the daily runner.
"""

import logging
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Set, Tuple

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
)
logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(ROOT))


def build_protected_set(open_orders: List[Dict]) -> Set[str]:
    """Return set of symbols that already have an active stop sell order."""
    protected = set()
    for o in open_orders:
        if o.get("side") == "sell" and o.get("type") in ("stop", "stop_limit"):
            sym = o.get("symbol", "")
            if sym:
                protected.add(sym)
    return protected


def positions_needing_stops(
    positions: List[Dict], protected: Set[str]
) -> List[Dict]:
    """Return positions that have no active stop order."""
    return [p for p in positions if p["symbol"] not in protected]


def calculate_stop_for_position(pos: Dict) -> Tuple[float, str]:
    """Calculate stop price for a position using trailing stop tiers."""
    from exit_logic import calculate_stop_price
    current  = float(pos["current_price"])
    avg_cost = float(pos["avg_entry_price"])
    pnl_pct  = float(pos["unrealized_plpc"]) * 100   # Alpaca returns decimal
    return calculate_stop_price(current, pnl_pct, avg_cost)


def main():
    logger.info("=== Stop Placement Agent Starting ===")

    try:
        from alpaca_broker import AlpacaBroker
        broker = AlpacaBroker()
    except Exception as e:
        logger.error(f"Alpaca connection failed: {e}")
        sys.exit(1)

    if not broker.is_market_open():
        logger.info("Market is closed — stop placement skipped")
        return

    positions   = broker.get_positions() or []
    open_orders = broker.get_orders(status="open") or []

    protected = build_protected_set(open_orders)
    to_protect = positions_needing_stops(positions, protected)

    logger.info(
        f"Positions: {len(positions)} total, "
        f"{len(protected)} already protected, "
        f"{len(to_protect)} need stops"
    )

    placed = 0
    failed = 0
    results = []

    for pos in to_protect:
        sym = pos["symbol"]
        qty = int(pos["qty"])

        try:
            stop_price, tier = calculate_stop_for_position(pos)
            result = broker.place_order(
                symbol=sym,
                qty=qty,
                side="sell",
                order_type="stop",
                stop_price=stop_price,
                time_in_force="gtc",
            )
            if "error" not in result:
                placed += 1
                logger.info(f"STOP PLACED: {sym} {qty} shares @ ${stop_price:.2f} ({tier})")
                results.append({"symbol": sym, "stop": stop_price, "tier": tier, "status": "placed"})
            else:
                failed += 1
                logger.warning(f"STOP FAILED: {sym} — {result.get('error')}")
                results.append({"symbol": sym, "status": "failed", "error": result.get("error")})
        except Exception as e:
            failed += 1
            logger.error(f"Error placing stop for {sym}: {e}")
            results.append({"symbol": sym, "status": "error", "error": str(e)})

    # Write result to docs/data for dashboard visibility
    output = {
        "generated_at": datetime.now().isoformat(),
        "positions_checked": len(positions),
        "already_protected": len(protected),
        "stops_placed": placed,
        "stops_failed": failed,
        "results": results,
    }
    out_path = ROOT / "docs" / "data" / "stop_placement.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, indent=2))

    logger.info(f"=== Stop Placement Complete: {placed} placed, {failed} failed ===")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
cd /Users/alexschumacher/stocktrader && python3 -m pytest tests/test_stop_placement.py -v
```
Expected: 5 tests pass

- [ ] **Step 5: Syntax check**

```bash
python3 -m py_compile stop_placement.py && echo "OK"
```

- [ ] **Step 6: Create `.github/workflows/stop_placement.yml`**

```yaml
name: Stop Placement

on:
  schedule:
    - cron: '31 13 * * 1-5'   # 9:31 AM EDT (13:31 UTC) Mon–Fri — 1 min after open
  workflow_dispatch:

jobs:
  place-stops:
    runs-on: ubuntu-latest
    timeout-minutes: 5
    permissions:
      contents: write
    env:
      ALPACA_API_KEY: ${{ secrets.ALPACA_API_KEY }}
      ALPACA_SECRET_KEY: ${{ secrets.ALPACA_SECRET_KEY }}
      ALPACA_PAPER: ${{ secrets.ALPACA_PAPER }}

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v4
        with:
          python-version: '3.12'
          cache: pip

      - run: pip install -r requirements.txt

      - name: Place missing stop orders
        run: python stop_placement.py

      - name: Commit result
        if: always()
        run: |
          git config user.email "aschumacherdesign@gmail.com"
          git config user.name "schu51"
          git add docs/data/stop_placement.json 2>/dev/null || true
          git diff --staged --quiet || git commit -m "Stop placement [skip ci]"
          git pull --rebase origin main
          git push
```

- [ ] **Step 7: Commit**

```bash
git add stop_placement.py tests/test_stop_placement.py .github/workflows/stop_placement.yml
git commit -m "Add stop placement agent — places missing stops at market open"
```

---

## Task 4: Intraday Exit Monitor

**Files:**
- Create: `intraday_exit.py`
- Create: `.github/workflows/intraday_exit.yml`

Runs at :15 and :45 past each hour, 9 AM–4 PM EDT. Checks every position against 50MA and hard-loss trigger. Closes positions that breach exit criteria. Also ensures stops are current.

- [ ] **Step 1: Create `intraday_exit.py`**

```python
"""
Intraday Exit Monitor
=====================
Runs every 30 minutes during market hours (offset from portfolio sync).
Evaluates each open position against exit triggers and closes immediately
when triggered. Also updates trailing stops if they've drifted below tier.

Exit triggers (same as daily runner):
  PRICE_BELOW_50MA  — price crossed below 50-day MA
  HARD_LOSS_STOP    — unrealized loss exceeds 15%

This agent does NOT generate new entry signals — exits only.
"""

import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
)
logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(ROOT))

DOCS_DATA = ROOT / "docs" / "data"
TRADES_FILE = DOCS_DATA / "trades.json"


def _cancel_open_stops(broker, symbol: str):
    """Cancel any existing stop sell orders for a symbol."""
    try:
        orders = broker.get_orders(status="open", symbols=[symbol]) or []
        for o in orders:
            if o.get("side") == "sell" and o.get("type") in ("stop", "stop_limit", "trailing_stop"):
                broker.cancel_order(o["id"])
                logger.info(f"Cancelled stop {o['id']} for {symbol}")
    except Exception as e:
        logger.warning(f"Could not cancel stops for {symbol}: {e}")


def _log_exit(symbol: str, price: float, qty: int, trigger: str):
    """Append exit record to trades.json."""
    try:
        trades = json.loads(TRADES_FILE.read_text()) if TRADES_FILE.exists() else []
        today = datetime.now().strftime("%Y-%m-%d")
        ts    = datetime.now().isoformat()
        for t in reversed(trades):
            if t.get("symbol") == symbol and t.get("status") == "OPEN":
                entry = float(t.get("entry_price", price))
                pnl_pct = ((price - entry) / entry) * 100 if entry else 0
                pnl_usd = (price - entry) * t.get("shares", qty)
                t.update({
                    "status": "CLOSED",
                    "exit_date": today,
                    "exit_ts": ts,
                    "exit_price": round(price, 2),
                    "exit_reason": trigger,
                    "pnl_pct": round(pnl_pct, 2),
                    "pnl_usd": round(pnl_usd, 2),
                    "hold_days": (
                        datetime.now().date() -
                        __import__("datetime").date.fromisoformat(t["entry_date"])
                    ).days if t.get("entry_date") else None,
                })
                break
        TRADES_FILE.write_text(json.dumps(trades, indent=2))
    except Exception as e:
        logger.warning(f"Could not log exit for {symbol}: {e}")


def evaluate_position(broker, pos: Dict) -> Optional[Dict]:
    """
    Evaluate one position. Returns an action dict if an exit should fire,
    None if the position should be held.
    """
    from exit_logic import check_exit_triggers, fetch_sma50, calculate_stop_price

    sym     = pos["symbol"]
    current = float(pos["current_price"])
    avg_cost = float(pos["avg_entry_price"])
    qty     = int(pos["qty"])
    pnl_pct = float(pos["unrealized_plpc"]) * 100

    sma50 = fetch_sma50(sym)

    should_exit, trigger, reason = check_exit_triggers(
        symbol=sym,
        current_price=current,
        avg_cost=avg_cost,
        pnl_pct=pnl_pct,
        sma50=sma50,
    )

    if should_exit:
        return {
            "symbol": sym,
            "trigger": trigger,
            "reason": reason,
            "qty": qty,
            "price": current,
            "pnl_pct": round(pnl_pct, 2),
        }
    return None


def main():
    logger.info("=== Intraday Exit Monitor Starting ===")

    try:
        from alpaca_broker import AlpacaBroker
        broker = AlpacaBroker()
    except Exception as e:
        logger.error(f"Alpaca connection failed: {e}")
        sys.exit(1)

    if not broker.is_market_open():
        logger.info("Market closed — intraday exit monitor skipped")
        return

    positions = broker.get_positions() or []
    logger.info(f"Evaluating {len(positions)} positions")

    exits_triggered = []
    stops_updated   = []

    for pos in positions:
        from exit_logic import calculate_stop_price, fetch_sma50
        sym     = pos["symbol"]
        current = float(pos["current_price"])
        avg_cost = float(pos["avg_entry_price"])
        qty     = int(pos["qty"])
        pnl_pct = float(pos["unrealized_plpc"]) * 100

        # --- Exit evaluation ---
        action = evaluate_position(broker, pos)
        if action:
            logger.warning(
                f"EXIT TRIGGERED: {sym} — {action['trigger']} | {action['reason']} "
                f"| P&L: {pnl_pct:+.1f}%"
            )
            _cancel_open_stops(broker, sym)
            result = broker.close_position(sym)
            action["executed"] = "error" not in result
            action["order_id"] = result.get("id")
            if action["executed"]:
                _log_exit(sym, current, qty, action["trigger"])
            exits_triggered.append(action)
            continue  # Skip stop update for exited position

        # --- Trailing stop update ---
        # Only update if the new stop would be HIGHER than any existing stop
        # (never move a stop down)
        from exit_logic import calculate_stop_price
        new_stop, tier = calculate_stop_price(current, pnl_pct, avg_cost)

        if pnl_pct >= 25:   # Only place trailing stops for profitable positions
            open_orders = broker.get_orders(status="open", symbols=[sym]) or []
            existing_stop = None
            existing_stop_id = None
            for o in open_orders:
                if o.get("side") == "sell" and o.get("type") in ("stop", "stop_limit"):
                    try:
                        existing_stop = float(o.get("stop_price", 0))
                        existing_stop_id = o["id"]
                    except (TypeError, ValueError):
                        pass

            if existing_stop is None or new_stop > existing_stop:
                # Cancel old stop and place updated one
                if existing_stop_id:
                    broker.cancel_order(existing_stop_id)
                result = broker.place_order(
                    symbol=sym, qty=qty, side="sell",
                    order_type="stop", stop_price=new_stop, time_in_force="gtc",
                )
                if "error" not in result:
                    logger.info(f"STOP UPDATED: {sym} → ${new_stop:.2f} ({tier}) | P&L: {pnl_pct:+.1f}%")
                    stops_updated.append({"symbol": sym, "new_stop": new_stop, "tier": tier})

    # Write result
    output = {
        "generated_at": datetime.now().isoformat(),
        "positions_checked": len(positions),
        "exits_triggered": exits_triggered,
        "stops_updated": stops_updated,
        "mode": "EXECUTE",
    }
    out_path = DOCS_DATA / "intraday_exit.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, indent=2))

    logger.info(
        f"=== Intraday Exit Complete: {len(exits_triggered)} exits, "
        f"{len(stops_updated)} stops updated ==="
    )


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Syntax check**

```bash
python3 -m py_compile intraday_exit.py && echo "OK"
```

- [ ] **Step 3: Create `.github/workflows/intraday_exit.yml`**

```yaml
name: Intraday Exit Monitor

on:
  schedule:
    # :15 and :45 past each hour, 9 AM–4 PM EDT (13–20 UTC)
    # Offset from portfolio sync (:00 and :30) to avoid git conflicts
    - cron: '15,45 13-20 * * 1-5'
  workflow_dispatch:

jobs:
  exit-monitor:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    permissions:
      contents: write
    env:
      ALPACA_API_KEY: ${{ secrets.ALPACA_API_KEY }}
      ALPACA_SECRET_KEY: ${{ secrets.ALPACA_SECRET_KEY }}
      ALPACA_PAPER: ${{ secrets.ALPACA_PAPER }}

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v4
        with:
          python-version: '3.12'
          cache: pip

      - run: pip install -r requirements.txt

      - name: Run intraday exit monitor
        run: python intraday_exit.py

      - name: Commit result
        if: always()
        run: |
          git config user.email "aschumacherdesign@gmail.com"
          git config user.name "schu51"
          git add docs/data/intraday_exit.json docs/data/trades.json 2>/dev/null || true
          git diff --staged --quiet || git commit -m "Intraday exit check [skip ci]"
          git pull --rebase origin main
          git push
```

- [ ] **Step 4: Commit**

```bash
git add intraday_exit.py .github/workflows/intraday_exit.yml
git commit -m "Add intraday exit monitor — evaluates exits every 30 min during market hours"
```

---

## Task 5: Premarket Brief Workflow

**Files:**
- Modify: `premarket_scan.py` — add JSON output
- Create: `.github/workflows/premarket.yml`

- [ ] **Step 1: Add JSON output to the end of `premarket_scan.py`**

Find the last line of `premarket_scan.py` (currently `print("=" * 65)`) and append:

```python
# ── Write JSON brief for dashboard ──────────────────────────────────────────
_brief = {
    "generated_at": now_pt.isoformat(),
    "recommendation": recommendation,
    "trend_buckets": {
        "strong": len(strong),
        "mixed":  len(mixed),
        "weak":   len(weak),
    },
    "gap_alerts": gap_flags,
    "avoids":     gap_down_avoid,
    "top_candidates": [
        {
            "ticker":        s["ticker"],
            "price":         s["price"],
            "ret_5d":        s["ret_5d"],
            "ret_20d":       s["ret_20d"],
            "score":         s["score"],
            "gap_up":        s["ticker"] in gap_up_priority,
            "thesis_grade":  thesis_results.get(s["ticker"], {}).get("thesis_grade", "?"),
            "thesis_score":  thesis_results.get(s["ticker"], {}).get("thesis_score", 0),
        }
        for s in top5
    ],
}

import pathlib as _pl
_out = _pl.Path(__file__).parent / "docs" / "data" / "premarket.json"
_out.parent.mkdir(parents=True, exist_ok=True)
_out.write_text(__import__("json").dumps(_brief, indent=2))
print(f"\n  Brief written to {_out}")
```

- [ ] **Step 2: Verify JSON is written on dry run**

```bash
cd /Users/alexschumacher/stocktrader && python3 premarket_scan.py 2>&1 | tail -5
```
Expected: `Brief written to .../docs/data/premarket.json` without errors

```bash
python3 -c "import json; d=json.loads(open('docs/data/premarket.json').read()); print(list(d.keys()))"
```
Expected: `['generated_at', 'recommendation', 'trend_buckets', 'gap_alerts', 'avoids', 'top_candidates']`

- [ ] **Step 3: Create `.github/workflows/premarket.yml`**

```yaml
name: Premarket Brief

on:
  schedule:
    - cron: '0 13 * * 1-5'   # 6:00 AM PDT (13:00 UTC) Mon–Fri
  workflow_dispatch:

jobs:
  premarket:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    permissions:
      contents: write
    env:
      ALPACA_API_KEY: ${{ secrets.ALPACA_API_KEY }}
      ALPACA_SECRET_KEY: ${{ secrets.ALPACA_SECRET_KEY }}
      ALPACA_PAPER: ${{ secrets.ALPACA_PAPER }}

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v4
        with:
          python-version: '3.12'
          cache: pip

      - run: pip install -r requirements.txt

      - name: Run premarket scan
        run: python premarket_scan.py

      - name: Commit brief
        if: always()
        run: |
          git config user.email "aschumacherdesign@gmail.com"
          git config user.name "schu51"
          git add docs/data/premarket.json 2>/dev/null || true
          git diff --staged --quiet || git commit -m "Premarket brief [skip ci]"
          git pull --rebase origin main
          git push
```

- [ ] **Step 4: Commit**

```bash
git add premarket_scan.py .github/workflows/premarket.yml
git commit -m "Add premarket brief workflow — 6 AM PDT daily scan with JSON output"
```

---

## Task 6: Post-Market Reconciler

**Files:**
- Create: `postmarket_reconciler.py`
- Create: `.github/workflows/postmarket.yml`

- [ ] **Step 1: Create `postmarket_reconciler.py`**

```python
"""
Post-Market Reconciler
======================
Runs at 4:15 PM EDT (20:15 UTC) Mon–Fri — 15 minutes after market close.
Reads the day's run data and produces a structured reconciliation report.

Writes docs/data/postmarket.json with:
  - Portfolio summary (value, P&L, winners/losers)
  - Execution summary (signals generated, executed, skipped with reasons)
  - Exit summary (what fired, what's at risk)
  - Positions at risk (deep losses, no stop protection)

Does NOT place orders or modify positions.
"""

import json
import logging
import sys
from datetime import datetime
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
)
logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.resolve()
DOCS_DATA = ROOT / "docs" / "data"


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text()) if path.exists() else {}
    except Exception as e:
        logger.warning(f"Could not read {path}: {e}")
        return {}


def reconcile(latest: dict, trades: list) -> dict:
    """Build the reconciliation report from today's run data."""
    acct      = latest.get("account", {})
    run       = latest.get("todays_run", {})
    exits     = latest.get("exits", {})
    positions = latest.get("positions", [])
    risk      = latest.get("risk_assessment", {})

    # Portfolio summary
    total_unrealized = sum(p.get("unrealized_pnl", 0) for p in positions)
    winners = [p for p in positions if p.get("unrealized_pnl_pct", 0) > 0]
    losers  = [p for p in positions if p.get("unrealized_pnl_pct", 0) < 0]

    # Execution summary
    signals   = run.get("signals", [])
    exec_details = run.get("execution_details", [])
    skipped   = [d for d in exec_details if d.get("status") == "skipped"]
    executed  = [s for s in signals if s.get("executed")]

    # Positions at risk: deep loss OR no stop protection
    at_risk = [
        {
            "symbol":   p["symbol"],
            "pnl_pct":  p.get("unrealized_pnl_pct", 0),
            "value":    p.get("market_value", 0),
            "has_stop": p.get("stop_loss") is not None,
            "risk":     (
                "deep_loss" if p.get("unrealized_pnl_pct", 0) < -8
                else "no_stop" if not p.get("stop_loss")
                else "approaching_loss" if p.get("unrealized_pnl_pct", 0) < -5
                else "ok"
            ),
        }
        for p in sorted(positions, key=lambda x: x.get("unrealized_pnl_pct", 0))
        if p.get("unrealized_pnl_pct", 0) < -5 or not p.get("stop_loss")
    ]

    # Closed trades today
    today = datetime.now().strftime("%Y-%m-%d")
    closed_today = [
        t for t in trades
        if t.get("status") == "CLOSED" and t.get("exit_date") == today
    ]

    return {
        "date":         today,
        "generated_at": datetime.now().isoformat(),
        "portfolio": {
            "value":             acct.get("portfolio_value", 0),
            "cash":              acct.get("cash", 0),
            "total_unrealized":  total_unrealized,
            "positions":         len(positions),
            "winners":           len(winners),
            "losers":            len(losers),
            "risk_level":        risk.get("risk_level", "UNKNOWN"),
            "risk_score":        risk.get("overall_risk_score", 0),
        },
        "execution": {
            "signals_generated": len(signals),
            "orders_executed":   len(executed),
            "orders_skipped":    len(skipped),
            "skip_reasons":      [s.get("reason", "") for s in skipped],
            "candidates_screened": run.get("candidates_screened", 0),
        },
        "exits": {
            "triggered":    len(exits.get("exits_triggered", [])),
            "stops_updated": len(exits.get("stops_updated", [])),
            "detail":       exits.get("exits_triggered", []),
        },
        "closed_today":   closed_today,
        "positions_at_risk": at_risk,
    }


def main():
    logger.info("=== Post-Market Reconciler Starting ===")

    latest = load_json(DOCS_DATA / "latest.json")
    trades = json.loads((DOCS_DATA / "trades.json").read_text()) \
             if (DOCS_DATA / "trades.json").exists() else []

    if not latest:
        logger.error("latest.json not found or empty — cannot reconcile")
        sys.exit(1)

    report = reconcile(latest, trades)

    out_path = DOCS_DATA / "postmarket.json"
    out_path.write_text(json.dumps(report, indent=2))

    logger.info(
        f"Portfolio: ${report['portfolio']['value']:,.2f} | "
        f"Winners: {report['portfolio']['winners']} | "
        f"Losers: {report['portfolio']['losers']} | "
        f"Risk: {report['portfolio']['risk_level']}"
    )
    logger.info(
        f"Execution: {report['execution']['signals_generated']} signals, "
        f"{report['execution']['orders_executed']} executed, "
        f"{report['execution']['orders_skipped']} skipped"
    )
    if report["positions_at_risk"]:
        logger.warning(
            f"Positions at risk: "
            + ", ".join(f"{p['symbol']} ({p['pnl_pct']:+.1f}%)" for p in report["positions_at_risk"][:5])
        )

    logger.info(f"Report written to {out_path}")
    logger.info("=== Post-Market Reconciler Complete ===")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Syntax check and dry run**

```bash
python3 -m py_compile postmarket_reconciler.py && echo "OK"
python3 postmarket_reconciler.py 2>&1 | tail -8
```
Expected: runs without error, writes `docs/data/postmarket.json`

- [ ] **Step 3: Verify output structure**

```bash
python3 -c "
import json
d = json.loads(open('docs/data/postmarket.json').read())
print('Keys:', list(d.keys()))
print('Portfolio value:', d['portfolio']['value'])
print('At risk count:', len(d['positions_at_risk']))
"
```

- [ ] **Step 4: Create `.github/workflows/postmarket.yml`**

```yaml
name: Post-Market Reconciler

on:
  schedule:
    - cron: '15 20 * * 1-5'   # 4:15 PM EDT (20:15 UTC) Mon–Fri
  workflow_dispatch:

jobs:
  reconcile:
    runs-on: ubuntu-latest
    timeout-minutes: 5
    permissions:
      contents: write
    env:
      ALPACA_API_KEY: ${{ secrets.ALPACA_API_KEY }}
      ALPACA_SECRET_KEY: ${{ secrets.ALPACA_SECRET_KEY }}
      ALPACA_PAPER: ${{ secrets.ALPACA_PAPER }}

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v4
        with:
          python-version: '3.12'
          cache: pip

      - run: pip install -r requirements.txt

      - name: Run post-market reconciler
        run: python postmarket_reconciler.py

      - name: Commit report
        if: always()
        run: |
          git config user.email "aschumacherdesign@gmail.com"
          git config user.name "schu51"
          git add docs/data/postmarket.json 2>/dev/null || true
          git diff --staged --quiet || git commit -m "Post-market reconciliation [skip ci]"
          git pull --rebase origin main
          git push
```

- [ ] **Step 5: Commit**

```bash
git add postmarket_reconciler.py .github/workflows/postmarket.yml
git commit -m "Add post-market reconciler — end-of-day report at 4:15 PM EDT"
```

---

## Task 7: Failure Alerter

**Files:**
- Create: `.github/workflows/failure_alert.yml`

Fires when `daily_trade.yml` completes with a failure conclusion. Writes `docs/data/alert.json` so the dashboard can surface it. Uses `on: workflow_run` which is a native GitHub Actions trigger — no polling required.

- [ ] **Step 1: Create `.github/workflows/failure_alert.yml`**

```yaml
name: Daily Trade Failure Alert

on:
  workflow_run:
    workflows: ["Daily Trade Execution"]
    types: [completed]

jobs:
  alert:
    # Only run if the daily trade workflow actually failed
    if: ${{ github.event.workflow_run.conclusion == 'failure' }}
    runs-on: ubuntu-latest
    timeout-minutes: 3
    permissions:
      contents: write

    steps:
      - uses: actions/checkout@v4

      - name: Write failure alert
        run: |
          cat > docs/data/alert.json << EOF
          {
            "alert": true,
            "workflow": "daily_trade",
            "conclusion": "${{ github.event.workflow_run.conclusion }}",
            "run_id": "${{ github.event.workflow_run.id }}",
            "run_url": "${{ github.event.workflow_run.html_url }}",
            "triggered_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
            "message": "Daily trade run failed. Check the Actions tab for details."
          }
          EOF

      - name: Commit alert
        run: |
          git config user.email "aschumacherdesign@gmail.com"
          git config user.name "schu51"
          git add docs/data/alert.json
          git diff --staged --quiet || git commit -m "ALERT: Daily trade run failed [skip ci]"
          git pull --rebase origin main
          git push
```

- [ ] **Step 2: Verify YAML is valid**

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/failure_alert.yml')); print('YAML valid')" 2>/dev/null || python3 -c "print('yaml not installed — check manually')"
```

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/failure_alert.yml
git commit -m "Add failure alert workflow — triggers when daily_trade.yml fails"
```

---

## Task 8: Final Push and Verification

- [ ] **Step 1: Run all tests**

```bash
cd /Users/alexschumacher/stocktrader
python3 -m pytest tests/ -v
```
Expected: all tests pass

- [ ] **Step 2: Verify all new files exist and compile**

```bash
python3 -m py_compile exit_logic.py stop_placement.py intraday_exit.py postmarket_reconciler.py premarket_scan.py && echo "All compile OK"
```

- [ ] **Step 3: Verify workflow files are all present**

```bash
ls .github/workflows/
```
Expected output includes:
```
daily_trade.yml
failure_alert.yml
intraday_exit.yml
postmarket.yml
portfolio_sync.yml
premarket.yml
stop_placement.yml
```

- [ ] **Step 4: Verify deleted files are gone**

```bash
ls n8n_trading_workflow.json orchestrator.py demo.py 2>&1
```
Expected: `No such file or directory` for all three

- [ ] **Step 5: Verify JSON outputs can be read**

```bash
python3 -c "
import json
from pathlib import Path
for f in ['premarket.json', 'postmarket.json', 'stop_placement.json']:
    p = Path('docs/data') / f
    if p.exists():
        d = json.loads(p.read_text())
        print(f'{f}: OK ({len(d)} keys)')
    else:
        print(f'{f}: not yet written (will be created on first run)')
"
```

- [ ] **Step 6: Push everything**

```bash
git push origin main
```

- [ ] **Step 7: Verify full workflow schedule in UTC**

```
06:00 AM PDT = 13:00 UTC  → premarket.yml
09:31 AM EDT = 13:31 UTC  → stop_placement.yml
10:00 AM EDT = 14:00 UTC  → daily_trade.yml (existing)
:15 and :45  13–20 UTC    → intraday_exit.yml (every 30 min during market hours)
:00 and :30  13–22 UTC    → portfolio_sync.yml (existing)
04:15 PM EDT = 20:15 UTC  → postmarket.yml
On failure               → failure_alert.yml
```

---

## Self-Review

**Spec coverage:**
- ✅ Stop placement agent (Task 3)
- ✅ Intraday exit monitor (Task 4)
- ✅ Premarket brief wired up (Task 5)
- ✅ Post-market reconciler (Task 6)
- ✅ Failure alerter (Task 7)
- ✅ n8n / orchestrator / demo removed (Task 1)
- ✅ Shared exit logic extracted (Task 2)
- ✅ Tests for exit logic and stop placement (Tasks 2, 3)

**Placeholder scan:** None found. All steps have exact code, exact commands, exact expected output.

**Type consistency:**
- `calculate_stop_price(current, pnl_pct, avg_cost)` used consistently in exit_logic.py, stop_placement.py, intraday_exit.py
- `check_exit_triggers(symbol, current_price, avg_cost, pnl_pct, sma50)` consistent across exit_logic.py and intraday_exit.py
- `broker.place_order(symbol, qty, side, order_type, stop_price, time_in_force)` matches alpaca_broker.py signature
- `unrealized_plpc` (Alpaca decimal) consistently multiplied by 100 before use

**Known limitation:** `intraday_exit.py` makes one yfinance call per position per run. With 20 positions, that's 20 API calls every 30 minutes — within yfinance's free limits but adds ~20–30 seconds to each run. The 10-minute timeout on the workflow accommodates this.
