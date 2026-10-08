import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from exit_logic import reconcile_phantom_trades, settle_broker_exits


def _trade(symbol="DE", shares=4, **over):
    base = {"symbol": symbol, "status": "OPEN", "entry_date": "2026-10-06",
            "entry_ts": "2026-10-06T14:04:17.000000", "entry_price": 693.03,
            "shares": shares, "order_id": "buy1", "exit_reason": None}
    base.update(over)
    return base


def _sell(qty=4, price=650.40, at="2026-10-07T15:02:11.123456789Z", kind="stop"):
    return {"side": "sell", "status": "filled", "type": kind, "filled_qty": str(qty),
            "filled_avg_price": str(price), "filled_at": at}


def _settle(trades, held=(), sells=(), bought=(4.0, 692.47)):
    return settle_broker_exits(trades, set(held),
                               filled_sells=lambda sym: sells if sells is None else list(sells),
                               buy_fill=lambda oid: bought)


def test_filled_stop_closes_the_trade_as_a_real_loss():
    trades = [_trade()]
    closed, unresolved = _settle(trades, sells=[_sell()])
    t = trades[0]
    assert closed == [t] and unresolved == []
    assert (t["status"], t["exit_reason"], t["exit_price"]) == ("CLOSED", "BROKER_STOP", 650.4)
    assert t["exit_date"] == "2026-10-07" and t["hold_days"] == 1
    assert t["pnl_pct"] == -6.08 and t["pnl_usd"] == -168.28          # on the real fill, 692.47, not the limit
    # ...and it is no longer something the phantom reconcile can write off
    assert reconcile_phantom_trades(trades, set()) == 0


def test_a_sale_made_by_hand_is_recorded_as_such():
    trades = [_trade()]
    _settle(trades, sells=[_sell(kind="market")])
    assert trades[0]["exit_reason"] == "SOLD_AT_BROKER"


def test_held_and_pending_trades_are_left_alone():
    trades = [_trade(), _trade("AME", pending_exit={"order_id": "s1"})]
    closed, unresolved = _settle(trades, held={"DE"}, sells=[_sell()])
    assert closed == [] and unresolved == []
    assert all(t["status"] == "OPEN" for t in trades)


def test_a_sale_before_this_entry_belongs_to_an_earlier_trade():
    trades = [_trade()]
    closed, unresolved = _settle(trades, sells=[_sell(at="2026-09-30T15:00:00Z")])
    assert closed == [] and unresolved == [trades[0]]
    assert trades[0]["status"] == "OPEN"


def test_an_unfilled_buy_is_still_the_phantom_reconciles_case():
    trades = [_trade()]
    closed, unresolved = _settle(trades, sells=[], bought=(0.0, 0.0))
    assert closed == [] and unresolved == []
    assert reconcile_phantom_trades(trades, set()) == 1
    assert trades[0]["exit_reason"] == "ORDER_NOT_FILLED"


def test_bought_and_gone_with_no_sale_found_stays_open():
    trades = [_trade()]
    closed, unresolved = _settle(trades, sells=[])
    assert closed == [] and unresolved == [trades[0]]


def test_unreadable_orders_never_close_or_cancel():
    trades = [_trade(), _trade("OLD", order_id=None)]
    closed, unresolved = _settle(trades, sells=None, bought=None)
    assert closed == [] and unresolved == trades
    # the caller shields unresolved symbols from the phantom reconcile
    assert reconcile_phantom_trades(trades, {t["symbol"] for t in unresolved}) == 0


def test_a_sale_of_fewer_shares_than_the_trade_does_not_close_it():
    trades = [_trade()]
    closed, unresolved = _settle(trades, sells=[_sell(qty=2)])
    assert closed == [] and unresolved == [trades[0]]


def test_two_fills_are_averaged():
    trades = [_trade()]
    _settle(trades, sells=[_sell(qty=1, price=652.0, kind="market"), _sell(qty=3, price=650.0)])
    t = trades[0]
    assert (t["exit_price"], t["exit_reason"]) == (650.5, "BROKER_STOP")


def test_legacy_trade_without_an_order_id_keeps_the_old_behaviour():
    trades = [_trade(order_id=None)]
    closed, unresolved = _settle(trades, sells=[], bought=None)
    assert closed == [] and unresolved == []
    assert reconcile_phantom_trades(trades, set()) == 1


def test_record_broker_exits_reads_the_account(monkeypatch):
    from exit_logic import record_broker_exits

    class Broker:
        last_orders_ok = True
        def get_orders(self, status="open", symbols=None, **_):
            assert status == "closed" and symbols == ["DE"]
            return [_sell(), {"side": "buy", "status": "filled"},
                    dict(_sell(), status="canceled")]
        def get_order(self, order_id):
            return {"filled_qty": "4", "filled_avg_price": "692.47"}

    trades = [_trade()]
    closed, unresolved = record_broker_exits(Broker(), trades, {"AME"})
    assert [t["symbol"] for t in closed] == ["DE"] and unresolved == []

    class Down(Broker):
        last_orders_ok = False
        def get_orders(self, **_):
            return []

    trades = [_trade()]
    closed, unresolved = record_broker_exits(Down(), trades, set())
    assert closed == [] and unresolved == trades


# ── entry price: the fill, not the limit the order was sent at ───────────────

def _orders(**by_id):
    return lambda oid: by_id[oid]


def test_entry_price_becomes_the_fill_and_the_limit_is_kept():
    from exit_logic import confirm_entry_fills
    trades = [_trade("WAB", shares=13, entry_price=304.49, order_id="b1")]
    n = confirm_entry_fills(trades, _orders(b1={"status": "filled", "filled_qty": "13", "filled_avg_price": "292.93"}))
    t = trades[0]
    assert n == 1 and (t["entry_price"], t["entry_limit"], t["shares"], t["entry_filled"]) == (292.93, 304.49, 13, True)
    # a confirmed trade is not looked up again
    assert confirm_entry_fills(trades, lambda oid: 1 / 0) == 0


def test_partly_filled_buy_that_ended_corrects_the_open_share_count():
    from exit_logic import confirm_entry_fills
    trades = [_trade(shares=10, order_id="b1")]
    confirm_entry_fills(trades, _orders(b1={"status": "expired", "filled_qty": "6", "filled_avg_price": "690.00"}))
    assert (trades[0]["shares"], trades[0]["entry_price"]) == (6, 690.0)


def test_working_unfilled_or_unreadable_orders_are_left_for_later():
    from exit_logic import confirm_entry_fills
    trades = [_trade(order_id="working"), _trade(order_id="dead"), _trade(order_id="broken"),
              _trade(order_id=None), _trade(order_id="x", status="CANCELLED")]
    n = confirm_entry_fills(trades, _orders(
        working={"status": "partially_filled", "filled_qty": "2", "filled_avg_price": "690"},
        dead={"status": "canceled", "filled_qty": "0", "filled_avg_price": None},
        broken={"error": "not found"}))
    assert n == 0
    assert all(t["entry_price"] == 693.03 and "entry_filled" not in t for t in trades)


def test_closed_trade_gets_its_entry_price_but_keeps_its_recorded_result():
    from exit_logic import confirm_entry_fills
    trades = [_trade("CAH", shares=18, entry_price=240.27, order_id="b1", status="CLOSED",
                     pnl_pct=-0.44, pnl_usd=-19.03)]
    confirm_entry_fills(trades, _orders(b1={"status": "filled", "filled_qty": "18", "filled_avg_price": "233.28"}))
    t = trades[0]
    assert (t["entry_price"], t["pnl_pct"], t["pnl_usd"], t["shares"]) == (233.28, -0.44, -19.03, 18)


def test_lookups_are_capped_per_run_newest_first():
    from exit_logic import confirm_entry_fills
    trades = [_trade(f"S{i}", order_id=f"o{i}") for i in range(5)]
    seen = []
    def get(oid):
        seen.append(oid)
        return {"status": "filled", "filled_qty": "4", "filled_avg_price": "690"}
    assert confirm_entry_fills(trades, get, limit=2) == 2 and seen == ["o4", "o3"]
