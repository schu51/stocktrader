import json
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).parent.parent))

TODAY = date(2026, 10, 6)


def _trade(symbol, entry, status="OPEN"):
    return {"symbol": symbol, "status": status, "entry_date": entry}


TRADES = [_trade("ANET", "2026-08-07"), _trade("HOOD", "2026-08-31"), _trade("NVDA", "2026-09-21"),
          _trade("SHOP", "2026-10-01"), _trade("AMD", "2026-09-10"), _trade("OLD", "2026-06-01", "CLOSED")]
RANKS = {"ANET": 41, "HOOD": 33, "NVDA": 45, "SHOP": 20, "AMD": 99, "VLO": 97, "CTLP": 5}
HOLDINGS = {s: {"pnl_pct": 5.0} for s in ("ANET", "HOOD", "NVDA", "SHOP", "AMD", "VLO", "CTLP")}


# ── which holdings count as laggards ─────────────────────────────────────────

def test_laggards_are_long_held_holdings_that_fell_out_of_the_top_half_weakest_first():
    import rotation
    lag = rotation.find_laggards(HOLDINGS, TRADES, RANKS, TODAY, exclude={"CTLP"})
    assert [(l["symbol"], l["rs_rank"]) for l in lag] == [("HOOD", 33), ("ANET", 41)]
    # NVDA (15 days) and SHOP (5 days) are weak but too new; AMD is strong;
    # VLO has no trade record so its holding period is unknown; CTLP cannot be sold


def test_the_buffer_keeps_a_holding_that_is_below_the_entry_bar_but_still_in_the_top_half():
    import rotation
    ranks = {**RANKS, "ANET": 55, "HOOD": 50}          # under the 70 needed to buy, not under 50
    assert rotation.find_laggards(HOLDINGS, TRADES, ranks, TODAY) == []


def test_unknown_is_never_weak():
    import rotation
    assert rotation.find_laggards(HOLDINGS, TRADES, None, TODAY) == []
    assert rotation.find_laggards(HOLDINGS, TRADES, {}, TODAY) == []
    assert rotation.find_laggards(HOLDINGS, TRADES, {"AMD": 99}, TODAY) == []          # ANET has no rank today
    assert rotation.find_laggards(HOLDINGS, [_trade("ANET", "not a date")], RANKS, TODAY) == []


def test_ranks_are_used_only_from_a_complete_screen_made_today():
    import rotation
    good = {"generated_at": "2026-10-06T10:01:00", "data_coverage": 0.99, "rs_ranks": {"ANET": 41, "X": "bad"}}
    assert rotation.load_ranks(good, TODAY) == {"ANET": 41}
    assert rotation.load_ranks({**good, "generated_at": "2026-10-05T10:01:00"}, TODAY) is None   # yesterday's
    assert rotation.load_ranks({**good, "data_coverage": 0.13}, TODAY) is None                  # scraper broken
    assert rotation.load_ranks({k: v for k, v in good.items() if k != "data_coverage"}, TODAY) is None
    assert rotation.load_ranks({k: v for k, v in good.items() if k != "rs_ranks"}, TODAY) is None
    assert rotation.load_ranks(None, TODAY) is None


# ── how many to sell ─────────────────────────────────────────────────────────

def test_sells_only_what_the_cap_and_todays_buys_need():
    import rotation
    cap = 20
    assert rotation.sells_wanted(n_laggards=3, held=16, cap=cap, n_buys=3) == 0      # room for all three
    assert rotation.sells_wanted(n_laggards=3, held=20, cap=cap, n_buys=0) == 0      # full, nothing to buy
    assert rotation.sells_wanted(n_laggards=3, held=20, cap=cap, n_buys=1) == 1      # one swap
    assert rotation.sells_wanted(n_laggards=3, held=19, cap=cap, n_buys=3) == 2      # one free slot, two more needed
    assert rotation.sells_wanted(n_laggards=3, held=20, cap=cap, n_buys=5) == 2      # daily limit
    assert rotation.sells_wanted(n_laggards=3, held=22, cap=cap, n_buys=0) == 2      # over the cap: back toward it
    assert rotation.sells_wanted(n_laggards=0, held=22, cap=cap, n_buys=4) == 0      # no laggards, nothing forced


def test_buys_are_sized_for_a_slot_rotation_can_free():
    import rotation
    assert rotation.planning_count(held=20, cap=20, n_laggards=1) == 19      # full, one laggard: plan for the swap
    assert rotation.planning_count(held=20, cap=20, n_laggards=0) == 20      # full, nothing to rotate: no buys
    assert rotation.planning_count(held=22, cap=20, n_laggards=5) == 22      # two sells still leave no room
    assert rotation.planning_count(held=16, cap=20, n_laggards=3) == 16      # already room


# ── selling ──────────────────────────────────────────────────────────────────

class _Broker:
    def __init__(self, close=None, fill="filled"):
        self.orders = [{"id": "stopA", "symbol": "ANET", "side": "sell", "type": "stop", "qty": "13", "stop_price": "176.88"}]
        self.cancelled, self.placed, self.closed = [], [], []
        self._close, self._fill, self.clock = close or {"id": "sell1"}, fill, True
    def get_orders(self, status=None, limit=50, symbols=None):
        return [o for o in self.orders if o["id"] not in self.cancelled]
    def cancel_order(self, oid): self.cancelled.append(oid); return {"success": True}
    def close_position(self, sym): self.closed.append(sym); return self._close
    def get_order(self, oid):
        status = self._fill.pop(0) if isinstance(self._fill, list) and len(self._fill) > 1 else self._fill
        return {"status": status[0] if isinstance(status, list) else status}
    def market_clock(self): return self.clock
    def get_positions(self):
        return [{"symbol": "ANET", "qty": 13.0, "avg_entry_price": 192.26, "current_price": 208.12, "unrealized_plpc": 0.0825}]
    def get_asset(self, sym): return {"status": "active", "tradable": True}
    def place_order(self, **kw): self.placed.append(kw); return {"id": "restored"}


def _runner(broker, monkeypatch, tmp_path):
    import rotation
    monkeypatch.setattr(rotation, "ROTATION_LOG", tmp_path / "rotation_log.json")
    logged = []
    pos = SimpleNamespace(shares=13, current_price=208.12, unrealized_pnl_pct=8.25)
    runner = SimpleNamespace(broker=broker, portfolio=SimpleNamespace(positions={"ANET": pos}),
                             _log_trade=lambda *a, **k: logged.append((a, k)))
    return runner, logged


LAG = [{"symbol": "ANET", "rs_rank": 41, "hold_days": 60, "pnl_pct": 8.25}]


def test_rotation_cancels_the_stop_sells_and_records_why(monkeypatch, tmp_path):
    from run_daily_analysis import DailyRunner
    broker = _Broker()
    runner, logged = _runner(broker, monkeypatch, tmp_path)
    out = DailyRunner._rotate_out(runner, LAG, ["TRGP"], execute=True, confirm_seconds=0)
    assert out["sold"] == ["ANET"] and out["failed"] == []
    assert broker.cancelled == ["stopA"] and broker.closed == ["ANET"]
    assert logged[0][0][:2] == ("EXIT", "ANET") and logged[0][1]["exit_reason"] == "ROTATED_OUT"
    row = json.loads((tmp_path / "rotation_log.json").read_text())[0]
    assert (row["symbol"], row["rs_rank"], row["hold_days"], row["replacements"]) == ("ANET", 41, 60, ["TRGP"])


def test_dry_run_plans_but_sells_nothing(monkeypatch, tmp_path):
    from run_daily_analysis import DailyRunner
    broker = _Broker()
    runner, logged = _runner(broker, monkeypatch, tmp_path)
    out = DailyRunner._rotate_out(runner, LAG, ["TRGP"], execute=False)
    assert out == {"sold": [], "failed": [], "planned": ["ANET"]}
    assert broker.cancelled == [] and broker.closed == [] and logged == []


def test_a_rejected_or_unfilled_rotation_sell_frees_no_slot_and_the_stop_goes_back(monkeypatch, tmp_path):
    from run_daily_analysis import DailyRunner
    for broker in (_Broker(close={"error": "insufficient qty"}), _Broker(fill="accepted")):
        runner, logged = _runner(broker, monkeypatch, tmp_path)
        out = DailyRunner._rotate_out(runner, LAG, ["TRGP"], execute=True, confirm_seconds=0)
        assert out["sold"] == [] and out["failed"][0]["symbol"] == "ANET"
        assert logged == []                                              # not recorded as an exit
        assert [(o["symbol"], o["qty"], o["order_type"]) for o in broker.placed] == [("ANET", 13, "stop")]
        assert not (tmp_path / "rotation_log.json").exists()
    assert broker.cancelled == ["stopA", "sell1"]            # the unfilled sell was cancelled, not left working


def test_a_sell_that_fills_while_being_cancelled_still_counts_as_sold(monkeypatch, tmp_path):
    from run_daily_analysis import DailyRunner
    broker = _Broker(fill=["accepted", "filled"])
    runner, logged = _runner(broker, monkeypatch, tmp_path)
    out = DailyRunner._rotate_out(runner, LAG, ["TRGP"], execute=True, confirm_seconds=0)
    assert out["sold"] == ["ANET"] and len(logged) == 1 and broker.placed == []


def test_rotation_needs_the_market_confirmed_open_like_a_buy_does(monkeypatch, tmp_path):
    from run_daily_analysis import DailyRunner
    for clock, why in ((False, "market closed"), (None, "could not confirm the market is open")):
        broker = _Broker()
        broker.clock = clock
        runner, _ = _runner(broker, monkeypatch, tmp_path)
        out = DailyRunner._rotate_out(runner, LAG, ["TRGP"], execute=True, confirm_seconds=0)
        assert out["sold"] == [] and out["skipped"] == why
        assert broker.cancelled == [] and broker.closed == []       # stops untouched


def test_rotation_does_nothing_when_open_orders_cannot_be_read(monkeypatch, tmp_path):
    from run_daily_analysis import DailyRunner
    broker = _Broker()
    broker.last_orders_ok = False
    runner, _ = _runner(broker, monkeypatch, tmp_path)
    out = DailyRunner._rotate_out(runner, LAG, ["TRGP"], execute=True, confirm_seconds=0)
    assert out["sold"] == [] and broker.cancelled == [] and broker.closed == []


# ── position cap ─────────────────────────────────────────────────────────────

def test_position_cap_is_counted_as_orders_go_out(monkeypatch):
    # 2026-10-05: six buys in one run took the account from 16 positions to 22 against a cap of 20
    import run_daily_analysis as rda

    class Executor:
        def __init__(self, broker): pass
        def execute_decision(self, opp): return {"status": "submitted", "order_id": "o-" + opp["symbol"]}

    monkeypatch.setattr(rda, "OrderExecutor", Executor)
    monkeypatch.setattr(rda, "ALPACA_AVAILABLE", True)
    broker = SimpleNamespace(is_market_open=lambda: True, get_latest_quote=lambda s: {})
    engine = SimpleNamespace(
        config=SimpleNamespace(portfolio_constraints=SimpleNamespace(max_positions=20)),
        risk_manager=SimpleNamespace(pre_trade_risk_check=lambda **kw: {"approved": True}))
    # _position_count is deliberately absent: the gate must fall back to the portfolio, never to "no cap"
    runner = SimpleNamespace(broker=broker, engine=engine, portfolio=SimpleNamespace(num_positions=18),
                             _log_trade=lambda **kw: None, _active_weight_version=lambda: 1)
    opps = [{"symbol": s, "confidence": 0.85, "limit_price": 100.0, "shares": 10, "stop_loss": 92.0}
            for s in ("AAA", "BBB", "CCC", "DDD")]
    out = rda.DailyRunner._execute_opportunities(runner, opps, True, 0.65)
    assert out["submitted"] == 2
    assert [(d["symbol"], d["status"]) for d in out["details"]] == \
        [("AAA", "submitted"), ("BBB", "submitted"), ("CCC", "skipped"), ("DDD", "skipped")]
    assert "position cap reached (20/20)" in out["details"][2]["reason"]
