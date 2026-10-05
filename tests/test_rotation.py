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


def test_a_holding_with_a_sell_already_in_flight_is_never_rotated():
    import rotation
    trades = [{**_trade("HOOD", "2026-08-31"), "pending_exit": {"order_id": "sell1"}}, _trade("ANET", "2026-08-07")]
    assert [l["symbol"] for l in rotation.find_laggards(HOLDINGS, trades, RANKS, TODAY)] == ["ANET"]


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
    import intraday_exit, rotation
    monkeypatch.setattr(rotation, "ROTATION_LOG", tmp_path / "rotation_log.json")
    monkeypatch.setattr(intraday_exit, "TRADES_FILE", tmp_path / "trades.json")
    (tmp_path / "trades.json").write_text(json.dumps([{"symbol": "ANET", "status": "OPEN", "shares": 13}]))
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


def test_a_rejected_rotation_sell_frees_no_slot_and_the_stop_goes_back(monkeypatch, tmp_path):
    from run_daily_analysis import DailyRunner
    for broker in (_Broker(close={"error": "insufficient qty"}), _Broker(fill=["accepted", "canceled"])):
        runner, logged = _runner(broker, monkeypatch, tmp_path)
        out = DailyRunner._rotate_out(runner, LAG, ["TRGP"], execute=True, confirm_seconds=0)
        assert out["sold"] == [] and out["failed"][0]["symbol"] == "ANET"
        assert logged == []                                              # not recorded as an exit
        assert [(o["symbol"], o["qty"], o["order_type"]) for o in broker.placed] == [("ANET", 13, "stop")]
        assert not (tmp_path / "rotation_log.json").exists()
    assert broker.cancelled == ["stopA", "sell1"]            # the unfilled sell was cancelled, not left working


def test_a_rotation_sell_whose_outcome_is_unknown_is_tracked_not_guessed(monkeypatch, tmp_path):
    from run_daily_analysis import DailyRunner
    broker = _Broker(fill="pending_cancel")                  # cancel sent, result not known
    runner, logged = _runner(broker, monkeypatch, tmp_path)
    out = DailyRunner._rotate_out(runner, LAG, ["TRGP"], execute=True, confirm_seconds=0)
    assert out["sold"] == [] and "pending exit" in out["failed"][0]["error"]
    assert logged == []
    trade = json.loads((tmp_path / "trades.json").read_text())[0]
    assert trade["status"] == "OPEN" and trade["pending_exit"] == {**trade["pending_exit"], "order_id": "sell1",
                                                                  "trigger": "ROTATED_OUT"}

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


# ── cash is the limit; a position cap is optional ───────────────────────────

def _exec_runner(monkeypatch, cap, cash, total=100_000.0, **attrs):
    import run_daily_analysis as rda

    class Executor:
        def __init__(self, broker): pass
        def execute_decision(self, opp): return {"status": "submitted", "order_id": "o-" + opp["symbol"]}

    monkeypatch.setattr(rda, "OrderExecutor", Executor)
    monkeypatch.setattr(rda, "ALPACA_AVAILABLE", True)
    broker = SimpleNamespace(is_market_open=lambda: True, get_latest_quote=lambda s: {})
    engine = SimpleNamespace(
        config=SimpleNamespace(portfolio_constraints=SimpleNamespace(max_positions=cap, min_cash_allocation=0.05)),
        risk_manager=SimpleNamespace(pre_trade_risk_check=lambda **kw: {"approved": True}))
    # _position_count / _spendable_cash are absent unless given: the gates must fall
    # back to the portfolio, never to "no cap" or "no limit"
    return rda, SimpleNamespace(broker=broker, engine=engine,
                                portfolio=SimpleNamespace(num_positions=18, cash=cash, total_value=total),
                                _log_trade=lambda **kw: None, _active_weight_version=lambda: 1, **attrs)


def _opps(n, cost=1000.0):
    return [{"symbol": s, "confidence": 0.85, "limit_price": cost / 10, "shares": 10, "stop_loss": 92.0}
            for s in ("AAA", "BBB", "CCC", "DDD", "EEE", "FFF")[:n]]


def test_there_is_no_position_cap_by_default():
    from config import PortfolioConstraints
    assert PortfolioConstraints().max_positions is None


def test_without_a_cap_buys_are_limited_only_by_cash_counted_as_orders_go_out(monkeypatch):
    # $8,500 cash on a $100,000 account, 5% reserve: $3,500 to spend, so three $1,000 buys and no fourth
    rda, runner = _exec_runner(monkeypatch, cap=None, cash=8_500.0)
    out = rda.DailyRunner._execute_opportunities(runner, _opps(6), True, 0.65)
    assert out["submitted"] == 3
    assert [d["status"] for d in out["details"]] == ["submitted"] * 3 + ["skipped"] * 3
    assert "not enough cash: needs $1,000, $500 left after the cash reserve" in out["details"][3]["reason"]


def test_with_plenty_of_cash_every_signal_is_bought_however_many_positions_are_held(monkeypatch):
    rda, runner = _exec_runner(monkeypatch, cap=None, cash=60_000.0)
    runner.portfolio.num_positions = 40
    out = rda.DailyRunner._execute_opportunities(runner, _opps(6), True, 0.65)
    assert out["submitted"] == 6


def test_cash_from_a_rotation_can_be_spent_only_if_run_counted_it(monkeypatch):
    rda, runner = _exec_runner(monkeypatch, cap=None, cash=5_000.0, _spendable_cash=2_500.0)   # run() set it after a sale
    out = rda.DailyRunner._execute_opportunities(runner, _opps(4), True, 0.65)
    assert out["submitted"] == 2


def test_a_configured_position_cap_is_counted_as_orders_go_out(monkeypatch):
    # 2026-10-05: six buys in one run took the account from 16 positions to 22 against a cap of 20
    rda, runner = _exec_runner(monkeypatch, cap=20, cash=60_000.0)
    out = rda.DailyRunner._execute_opportunities(runner, _opps(4), True, 0.65)
    assert out["submitted"] == 2
    assert "position cap reached (20/20)" in out["details"][2]["reason"]


def test_laggards_are_sold_to_cover_a_cash_shortfall_weakest_first_no_more_than_needed():
    import rotation
    lag = [{"symbol": "HOOD", "market_value": 2700.0}, {"symbol": "ANET", "market_value": 2700.0},
           {"symbol": "NVDA", "market_value": 4200.0}]
    assert rotation.sells_for_cash(lag, shortfall=0) == 0             # today's buys are already funded
    assert rotation.sells_for_cash(lag, shortfall=-5000) == 0
    assert rotation.sells_for_cash(lag, shortfall=2000) == 1
    assert rotation.sells_for_cash(lag, shortfall=4000) == 2
    assert rotation.sells_for_cash(lag, shortfall=50_000) == 2        # daily limit
    assert rotation.sells_for_cash([], shortfall=4000) == 0
    assert rotation.sells_wanted(n_laggards=3, held=40, cap=None, n_buys=5) == 0     # no cap: count never forces a sale
    assert rotation.planning_count(held=40, cap=None, n_laggards=3) == 40


def test_buys_are_sized_with_laggard_cash_only_when_the_account_is_short():
    import rotation
    lag = [{"symbol": "HOOD", "market_value": 2700.0}, {"symbol": "ANET", "market_value": 2700.0},
           {"symbol": "NVDA", "market_value": 4200.0}]
    assert rotation.planning_proceeds(lag, spendable=30_000.0, total_value=115_000.0) == 0.0      # cash to spare
    assert rotation.planning_proceeds(lag, spendable=1_000.0, total_value=115_000.0) == 5400.0    # two weakest
    assert rotation.planning_proceeds([], spendable=1_000.0, total_value=115_000.0) == 0.0


def _portfolio(n_positions):
    from datetime import datetime
    from models import PortfolioState
    return PortfolioState(timestamp=datetime(2026, 10, 6), total_value=115_000.0, cash=40_000.0, invested=75_000.0,
                          positions={}, num_positions=n_positions, cash_allocation=0.35,
                          available_cash=38_000.0, buying_power=40_000.0, total_unrealized_pnl=0.0)


def test_sizing_and_risk_check_allow_a_buy_at_any_position_count_unless_a_cap_is_configured():
    from dataclasses import replace
    from config import ConvictionTier, DecisionConfig
    from position_sizing import PositionSizer
    from risk_manager import RiskManager
    uncapped = DecisionConfig()
    assert uncapped.portfolio_constraints.max_positions is None
    capped = replace(uncapped, portfolio_constraints=replace(uncapped.portfolio_constraints, max_positions=20))

    def run(config, held):
        size = PositionSizer(config).calculate_position_size("AME", _portfolio(held), ConvictionTier.HIGH,
                                                             current_price=250.0)
        check = RiskManager(config).pre_trade_risk_check("AME", "BUY", 10, 250.0, _portfolio(held))
        limit = [c for c in check["checks"] if c["check"] == "POSITION_LIMIT"][0]["status"]
        return size, limit

    size, limit = run(uncapped, held=45)
    assert limit == "PASSED" and not any("Max positions" in c for c in size.get("constraints_applied", []))
    size, limit = run(capped, held=20)
    assert limit == "FAILED" and any("Max positions" in c for c in size.get("constraints_applied", []))
