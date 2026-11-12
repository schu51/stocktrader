import json
import sys
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from diamond_sleeve import MAX_POSITIONS, broken, buy_candidates, judgment_of, plan

TODAY = date(2026, 10, 26)          # a Monday; the list is from Saturday the 24th


def _row(ticker, edge, demand, rank, stale=False, **over):
    j = {"edge": edge, "demand": demand, "red_flags": [], "judged_at": "2026-10-24"}
    if stale:
        j["stale"] = True
    return dict({"ticker": ticker, "name": f"{ticker} Inc", "cik": rank, "rank": rank, "score": {"total": 60.0},
                 "judgment": j, "judgment_rank": rank, "price": 50.0}, **over)


def _doc(rows, day="2026-10-24"):
    return {"generated_at": f"{day}T07:31:00", "watchlist": rows}


def _hist(day, ranks):
    return [{"date": day, "ticker": t, "rank": i + 1, "total": 60.0, "judgment_rank": r} for i, (t, r) in enumerate(ranks.items())]


DOC = _doc([_row("AAA", 7, 7, 1), _row("BBB", 6, 6, 2), _row("CCC", 5, 4, 3), _row("NEW", 7, 6, 4)])
HISTORY = _hist("2026-10-17", {"AAA": 1, "BBB": 2, "CCC": 3, "OLD": 4}) + _hist("2026-10-24", {"AAA": 1, "BBB": 2, "CCC": 3, "NEW": 4})


def test_judgment_is_edge_plus_demand_and_a_stale_one_does_not_count():
    assert judgment_of(_row("A", 7, 6, 1)) == 13.0
    assert judgment_of(_row("A", 7, 6, 1, stale=True)) is None
    assert judgment_of({"ticker": "A"}) is None and judgment_of({"ticker": "A", "judgment": {"edge": "x", "demand": 1}}) is None


def test_a_buy_needs_two_weeks_at_the_top_and_a_judgment_of_ten():
    names = [r["ticker"] for r in buy_candidates(DOC, HISTORY, TODAY)]
    assert names == ["AAA", "BBB"]          # CCC scores 9 of 20; NEW was not on last week's list


def test_nothing_is_bought_from_a_stale_list_or_without_an_earlier_week():
    assert buy_candidates(DOC, HISTORY, date(2026, 11, 5)) == []                       # the list is 12 days old
    assert buy_candidates(DOC, _hist("2026-10-24", {"AAA": 1, "BBB": 2}), TODAY) == []     # first judged list ever
    assert buy_candidates({"watchlist": []}, HISTORY, TODAY) == [] and buy_candidates(None, None, TODAY) == []


def test_only_the_top_names_by_judgment_rank_are_candidates():
    n = MAX_POSITIONS + 4
    rows = [_row(f"T{i}", 7, 7, i) for i in range(1, n)]
    hist = _hist("2026-10-17", {f"T{i}": i for i in range(1, n)}) + _hist("2026-10-24", {f"T{i}": i for i in range(1, n)})
    assert [r["ticker"] for r in buy_candidates(_doc(rows), hist, TODAY)] == [f"T{i}" for i in range(1, MAX_POSITIONS + 1)]


def test_a_holding_is_sold_when_its_thesis_breaks_not_when_it_slips_down_the_list():
    doc = _doc([_row("KEEP", 6, 5, 9), _row("WEAK", 4, 3, 20), _row("AAA", 7, 7, 1)])
    hist = _hist("2026-10-17", {"KEEP": 2, "WEAK": 3, "GONE1": 5}) + _hist("2026-10-24", {"KEEP": 9, "WEAK": 20, "AAA": 1})
    assert broken("KEEP", doc, hist, TODAY) is None                     # slipped to 9th: still held
    assert "fell to 7" in broken("WEAK", doc, hist, TODAY)             # judged 7 of 20: the edge is not there
    assert broken("GONE1", doc, hist, TODAY) is None                    # off the list for one week only
    assert "two" in broken("GONE2", doc, hist, TODAY)                  # on neither list


def test_no_sale_on_a_stale_list_or_a_stale_judgment():
    doc = _doc([_row("WEAK", 4, 3, 20, stale=True)])
    hist = _hist("2026-10-17", {"WEAK": 3}) + _hist("2026-10-24", {"WEAK": 20})
    assert broken("WEAK", doc, hist, TODAY) is None                     # last week's opinion, not a new one
    assert broken("GONE", doc, hist, date(2026, 11, 5)) is None         # list too old to act on
    assert broken("GONE", doc, _hist("2026-10-24", {"WEAK": 1}), TODAY) is None   # only one list exists: not two weeks


def _plan(held=None, others=(), equity=100_000.0, cash=50_000.0, above=lambda s: True, price=lambda s: 50.0, doc=DOC, history=HISTORY):
    return plan(doc, history, held or {}, set(others), equity, cash, TODAY, above, price)


def test_plan_buys_one_slot_each_of_the_eligible_names():
    p = _plan()
    assert [(b["symbol"], b["shares"], b["limit_price"]) for b in p["buys"]] == [("AAA", 53, 50.0), ("BBB", 53, 50.0)]
    assert p["sells"] == [] and p["slot"] == 2666.67                    # 40% of $100k over 15 positions


def test_plan_skips_what_is_already_held_below_its_200_day_or_unpriced():
    p = _plan(held={"AAA": 2500.0}, others={"BBB"})
    assert p["buys"] == [] and {s["symbol"]: s["reason"] for s in p["skipped"]} == {"BBB": "held by the large-cap strategy"}
    p = _plan(above=lambda s: s != "AAA", price=lambda s: None if s == "BBB" else 50.0)
    assert p["buys"] == [] and {s["symbol"] for s in p["skipped"]} == {"AAA", "BBB"}
    p = _plan(above=lambda s: None)                                     # trend unknown: not bought
    assert p["buys"] == []


def test_plan_never_spends_past_the_budget_the_cash_or_its_positions():
    held = {f"H{i}": 2666.67 for i in range(MAX_POSITIONS - 1)}         # every slot but one taken
    doc = _doc([_row("AAA", 7, 7, 1), _row("BBB", 6, 6, 2)] + [_row(h, 6, 6, 5) for h in held])
    hist = HISTORY + _hist("2026-10-17", {h: 5 for h in held}) + _hist("2026-10-24", {h: 5 for h in held})
    p = _plan(held=held, doc=doc, history=hist)
    assert [b["symbol"] for b in p["buys"]] == ["AAA"]                  # one slot left
    assert _plan(cash=1_000.0)["buys"][0]["shares"] == 20               # cash limits the first buy...
    assert len(_plan(cash=1_000.0)["buys"]) == 1                        # ...and leaves nothing for the second
    assert _plan(price=lambda s: 3000.0)["buys"] == []                  # one share costs more than a slot


def test_plan_sells_a_broken_holding_and_reuses_its_slot():
    held = {"OLD": 2500.0, "AAA": 2500.0}
    hist = _hist("2026-10-10", {"OLD": 1}) + _hist("2026-10-17", {"AAA": 1, "BBB": 2}) + _hist("2026-10-24", {"AAA": 1, "BBB": 2})
    p = _plan(held=held, history=hist)
    assert [(s["symbol"], "two" in s["reason"]) for s in p["sells"]] == [("OLD", True)]
    assert [b["symbol"] for b in p["buys"]] == ["BBB"]


# ── execution against a stand-in broker ──────────────────────────────────────

class Broker:
    """Alpaca stand-in: a small account, quotes, and a record of everything sent."""
    def __init__(self, positions=(), cash=50_000.0, equity=100_000.0, is_open=True, fills=True):
        self.positions = {p["symbol"]: p for p in positions}
        self.cash, self.equity, self.is_open, self.fills = cash, equity, is_open, fills
        self.orders, self.sent, self.cancelled, self.closed = {}, [], [], []
        self.last_positions_ok = self.last_orders_ok = True
        self.account_error = False
    def market_clock(self): return self.is_open
    def get_account(self):
        return {"error": "down"} if self.account_error else {"portfolio_value": str(self.equity), "cash": str(self.cash)}
    def get_positions(self): return list(self.positions.values())
    def get_latest_quote(self, symbol): return {"ap": 50.0}
    def get_asset(self, symbol): return {"status": "active", "tradable": True}
    def get_order(self, oid): return self.orders.get(oid, {})
    def get_orders(self, status=None, limit=50, symbols=None):
        live = ("new", "accepted")
        return [o for o in self.orders.values()
                if (o["status"] not in live if status == "closed" else o["status"] in live and o["id"] not in self.cancelled)
                and (not symbols or o["symbol"] in symbols)]
    def cancel_order(self, oid):
        self.cancelled.append(oid)
        if oid in self.orders:
            self.orders[oid]["status"] = "canceled"
        return {"success": True}
    def place_order(self, **kw):
        oid = f"o{len(self.sent) + 1}"
        self.sent.append(kw)
        if kw["side"] == "buy":
            done = self.fills
            self.orders[oid] = {"id": oid, "symbol": kw["symbol"], "side": "buy", "type": "limit", "qty": str(kw["qty"]),
                                "status": "filled" if done else "new", "filled_qty": str(kw["qty"] if done else 0),
                                "client_order_id": kw.get("client_order_id") or "", "filled_at": "2026-10-26T14:31:00Z"}
            if done:
                self.positions[kw["symbol"]] = _pos(kw["symbol"], kw["qty"], kw["limit_price"], kw["limit_price"])
        else:
            self.orders[oid] = {"id": oid, "symbol": kw["symbol"], "side": "sell", "type": kw["order_type"], "status": "new",
                                "qty": str(kw["qty"]), "stop_price": str(kw.get("stop_price"))}
        return {"id": oid}
    def close_position(self, symbol, qty=None):
        self.closed.append(symbol if qty is None else (symbol, qty))
        oid = f"c{len(self.closed)}"
        held = int(float(self.positions[symbol]["qty"]))
        sold = held if qty is None else int(qty)
        self.orders[oid] = {"id": oid, "symbol": symbol, "side": "sell", "type": "market", "status": "filled",
                            "filled_qty": str(sold)}
        if sold >= held:
            del self.positions[symbol]
        else:
            p = self.positions[symbol]
            self.positions[symbol] = _pos(symbol, held - sold, float(p["avg_entry_price"]), float(p["current_price"]))
        return {"id": oid}


def _pos(symbol, qty, cost, price):
    return {"symbol": symbol, "qty": str(qty), "avg_entry_price": str(cost), "current_price": str(price),
            "market_value": str(qty * price), "unrealized_pl": str(qty * (price - cost)),
            "unrealized_plpc": str(price / cost - 1), "side": "long", "asset_class": "us_equity"}


def _files(tmp_path, monkeypatch, trades=(), doc=DOC, history=HISTORY):
    import intraday_exit
    paths = {"list_file": tmp_path / "diamonds.json", "history_file": tmp_path / "diamond_history.json",
             "trades_file": tmp_path / "trades.json"}
    paths["list_file"].write_text(json.dumps(doc)); paths["history_file"].write_text(json.dumps(history))
    paths["trades_file"].write_text(trades if isinstance(trades, str) else json.dumps(list(trades)))
    monkeypatch.setattr(intraday_exit, "TRADES_FILE", paths["trades_file"])
    return paths


def _run(broker, paths, execute=True, above=lambda s: True, report=lambda s: None):
    from diamond_sleeve import run
    return run(broker, TODAY, execute=execute, above_200=above, wait_seconds=0, report_of=report,
               setup=lambda s: {"setup": None}, **paths)


SLEEVE_OPEN = {"symbol": "GONE", "status": "OPEN", "sleeve": "diamond", "entry_date": "2026-09-01", "entry_price": 40.0,
               "shares": 60, "stop_arm": "sleeve", "stop_dist": 0.25}


def test_buys_are_sent_logged_as_the_sleeves_and_given_the_wide_stop(tmp_path, monkeypatch):
    broker = Broker([_pos("AME", 16, 254.0, 250.0)])
    paths = _files(tmp_path, monkeypatch, trades=[{"symbol": "AME", "status": "OPEN", "entry_date": "2026-10-05"}])
    out = _run(broker, paths)
    buys = [o for o in broker.sent if o["side"] == "buy"]
    assert [(o["symbol"], o["qty"], o["order_type"], o["limit_price"], o["time_in_force"]) for o in buys] == \
        [("AAA", 53, "limit", 50.0, "day"), ("BBB", 53, "limit", 50.0, "day")]
    trades = json.loads(paths["trades_file"].read_text())
    mine = [t for t in trades if t.get("sleeve") == "diamond"]
    assert [(t["symbol"], t["status"], t["shares"], t["stop_dist"], t["order_id"]) for t in mine] == \
        [("AAA", "OPEN", 53, 0.25, "o1"), ("BBB", "OPEN", 53, 0.25, "o2")]
    stops = {o["symbol"]: o["stop_price"] for o in broker.sent if o["side"] == "sell" and o["order_type"] == "stop"}
    assert stops["AAA"] == 37.5 and stops["BBB"] == 37.5            # 25% below entry
    assert stops["AME"] == round(254.0 * 0.92, 2)                   # the large-cap holding keeps its own rule
    assert out["failures"] == [] and [b["status"] for b in out["buys"]] == ["submitted", "submitted"]
    assert out["budget"] == 40_000.0 and broker.closed == []


def test_a_dry_run_sends_and_records_nothing(tmp_path, monkeypatch):
    broker = Broker()
    paths = _files(tmp_path, monkeypatch)
    out = _run(broker, paths, execute=False)
    assert broker.sent == [] and json.loads(paths["trades_file"].read_text()) == []
    assert [b["symbol"] for b in out["buys"]] == ["AAA", "BBB"] and out["mode"] == "DRY-RUN"


def test_a_broken_holding_is_sold_and_recorded_once_filled(tmp_path, monkeypatch):
    broker = Broker([_pos("GONE", 60, 40.0, 44.0)])
    broker.orders["s1"] = {"id": "s1", "symbol": "GONE", "side": "sell", "type": "stop", "status": "new", "qty": "60", "stop_price": "30.0"}
    paths = _files(tmp_path, monkeypatch, trades=[SLEEVE_OPEN])      # GONE is on neither list
    out = _run(broker, paths, above=lambda s: False)                 # nothing bought, to isolate the sale
    assert broker.cancelled == ["s1"] and broker.closed == ["GONE"]
    t = json.loads(paths["trades_file"].read_text())[0]
    assert (t["status"], t["exit_reason"], t["pnl_pct"]) == ("CLOSED", "THESIS_BROKEN", 10.0)
    assert out["sells"][0]["status"] == "sold" and out["failures"] == []


def test_a_rejected_sale_is_a_failure_and_the_stop_goes_back(tmp_path, monkeypatch):
    broker = Broker([_pos("GONE", 60, 40.0, 44.0)])
    broker.orders["s1"] = {"id": "s1", "symbol": "GONE", "side": "sell", "type": "stop", "status": "new", "qty": "60", "stop_price": "30.0"}
    broker.close_position = lambda symbol: {"error": "insufficient qty"}
    paths = _files(tmp_path, monkeypatch, trades=[SLEEVE_OPEN])
    out = _run(broker, paths, above=lambda s: False)
    assert json.loads(paths["trades_file"].read_text())[0]["status"] == "OPEN"
    assert "GONE: sell failed" in out["failures"][0]
    assert [(o["symbol"], o["stop_price"]) for o in broker.sent if o["order_type"] == "stop"] == [("GONE", 30.0)]


def test_a_holding_already_being_sold_is_not_sold_twice(tmp_path, monkeypatch):
    broker = Broker([_pos("GONE", 60, 40.0, 44.0)])
    paths = _files(tmp_path, monkeypatch, trades=[dict(SLEEVE_OPEN, pending_exit={"order_id": "x", "trigger": "THESIS_BROKEN"})])
    out = _run(broker, paths, above=lambda s: False)
    assert broker.closed == [] and out["sells"][0]["status"] == "already selling"


@pytest.mark.parametrize("breakage, failure", [
    (lambda b, p: setattr(b, "is_open", False), False),               # weekend or holiday: quiet
    (lambda b, p: setattr(b, "is_open", None), True),
    (lambda b, p: setattr(b, "account_error", True), True),
    (lambda b, p: setattr(b, "last_positions_ok", False), True),
    (lambda b, p: p["trades_file"].write_text("{not json"), True),
    (lambda b, p: p["list_file"].write_text("{not json"), False),     # no list and no holdings: nothing to do
])
def test_anything_unknown_means_nothing_is_sent(tmp_path, monkeypatch, breakage, failure):
    broker = Broker()
    paths = _files(tmp_path, monkeypatch)
    breakage(broker, paths)
    out = _run(broker, paths)
    assert broker.sent == [] and broker.closed == [] and bool(out["failures"]) is failure


def test_an_unfilled_buy_is_logged_and_left_for_the_intraday_check_to_protect(tmp_path, monkeypatch):
    broker = Broker(fills=False)
    paths = _files(tmp_path, monkeypatch)
    out = _run(broker, paths)
    assert [t["symbol"] for t in json.loads(paths["trades_file"].read_text())] == ["AAA", "BBB"]
    assert [o for o in broker.sent if o["side"] == "sell"] == [] and out["failures"] == []


def test_a_buy_still_waiting_to_fill_counts_as_held_so_a_second_run_cannot_buy_it_again(tmp_path, monkeypatch):
    # The first run's buys are logged but have not filled, so the account shows
    # no position. A second run the same day (a re-run, a catch-up) must not
    # read that as two empty slots and unspent budget.
    broker = Broker(fills=False)
    paths = _files(tmp_path, monkeypatch)
    _run(broker, paths)
    assert len([o for o in broker.sent if o["side"] == "buy"]) == 2
    out = _run(broker, paths)
    assert len([o for o in broker.sent if o["side"] == "buy"]) == 2          # nothing sent the second time
    assert len(json.loads(paths["trades_file"].read_text())) == 2 and out["buys"] == []
    assert out["invested"] == 5300.0                                         # the two pending buys count against the budget


def test_pending_buys_fill_slots_and_a_thesis_break_does_not_try_to_sell_shares_not_yet_held(tmp_path, monkeypatch):
    pending = [dict(SLEEVE_OPEN, symbol=f"P{i}", shares=50, entry_price=50.0)
               for i in range(MAX_POSITIONS)]                                # every slot an unfilled buy
    broker = Broker()
    paths = _files(tmp_path, monkeypatch, trades=pending)
    out = _run(broker, paths)
    assert broker.sent == [] and broker.closed == []                         # no slot left; nothing to sell yet
    assert out["sells"] == [] and out["buys"] == [] and out["failures"] == []


def test_a_company_marked_not_to_be_bought_is_never_a_candidate():
    rows = [_row("AAA", 9, 9, 1), _row("BBB", 6, 6, 2)]
    rows[0]["judgment"]["do_not_buy"] = True
    assert [r["ticker"] for r in buy_candidates(_doc(rows), HISTORY, TODAY)] == ["BBB"]
    assert judgment_of(rows[0]) is None


def test_a_do_not_buy_mark_cannot_shield_a_holding_from_being_sold():
    # The mark takes a company out of the buy ranking. It must not also hide a
    # failing judgment, or a company could keep itself in the sleeve by writing
    # to the model.
    weak = _row("WEAK", 3, 3, 20); weak["judgment"]["do_not_buy"] = True
    good = _row("GOOD", 9, 9, 1); good["judgment"]["do_not_buy"] = True
    doc = _doc([weak, good])
    hist = _hist("2026-10-17", {"WEAK": 3, "GOOD": 1}) + _hist("2026-10-24", {"WEAK": 20, "GOOD": 1})
    assert "fell to 6" in broken("WEAK", doc, hist, TODAY)
    assert broken("GOOD", doc, hist, TODAY) is None          # flagged for a human instead (owner decision pending)


def test_the_sleeve_does_not_buy_a_stock_the_large_cap_run_has_ordered_but_not_yet_received(tmp_path, monkeypatch):
    # At 10:30 a large-cap buy sent at 10:00 may not have filled: no position,
    # but the trade log has it. Both strategies in one symbol cannot be told apart.
    broker = Broker()
    paths = _files(tmp_path, monkeypatch, trades=[{"symbol": "AAA", "status": "OPEN", "entry_date": "2026-10-26", "order_id": "x"}])
    out = _run(broker, paths)
    assert [o["symbol"] for o in broker.sent if o["side"] == "buy"] == ["BBB"]
    assert {s["symbol"]: s["reason"] for s in out["skipped"]}["AAA"] == "held by the large-cap strategy"


# ── fixes from the whole-branch review ───────────────────────────────────────

def test_every_sleeve_buy_carries_the_client_order_id_the_broker_keeps(tmp_path, monkeypatch):
    from exit_logic import SLEEVE_ORDER_PREFIX
    broker = Broker()
    paths = _files(tmp_path, monkeypatch)
    _run(broker, paths)
    buys = [o for o in broker.sent if o["side"] == "buy"]
    assert buys and all(o["client_order_id"].startswith(SLEEVE_ORDER_PREFIX + o["symbol"]) for o in buys)


def test_a_sleeve_position_missing_from_the_log_is_adopted_back_not_bought_again(tmp_path, monkeypatch):
    from exit_logic import SLEEVE_ORDER_PREFIX
    broker = Broker([_pos("AAA", 50, 50.0, 51.0)])
    broker.orders["b0"] = {"id": "b0", "symbol": "AAA", "side": "buy", "status": "filled", "filled_qty": "50",
                           "client_order_id": SLEEVE_ORDER_PREFIX + "AAA-1", "filled_at": "2026-10-26T14:31:00Z",
                           "filled_avg_price": "50.0"}
    paths = _files(tmp_path, monkeypatch)                                # the log lost its entry
    out = _run(broker, paths)
    trades = json.loads(paths["trades_file"].read_text())
    aaa = [t for t in trades if t["symbol"] == "AAA"]
    assert len(aaa) == 1 and aaa[0]["sleeve"] == "diamond" and aaa[0]["status"] == "OPEN" and aaa[0]["adopted"] is True
    assert [o["symbol"] for o in broker.sent if o["side"] == "buy"] == ["BBB"]
    assert any("AAA" in a for a in out["alerts"])


def test_a_stop_tighter_than_the_sleeves_is_replaced(tmp_path, monkeypatch):
    broker = Broker([_pos("GONE", 60, 40.0, 44.0)])
    broker.orders["s1"] = {"id": "s1", "symbol": "GONE", "side": "sell", "type": "stop", "status": "new", "qty": "60",
                           "stop_price": "36.80"}                         # the large-cap 8%: a dip would sell a diamond
    doc = _doc([_row("GONE", 7, 7, 1)])
    hist = _hist("2026-10-17", {"GONE": 1}) + _hist("2026-10-24", {"GONE": 1})
    paths = _files(tmp_path, monkeypatch, trades=[SLEEVE_OPEN], doc=doc, history=hist)
    out = _run(broker, paths)
    stops = [o for o in broker.sent if o.get("order_type") == "stop"]
    assert "s1" in broker.cancelled and [(o["symbol"], o["stop_price"]) for o in stops] == [("GONE", 30.0)]
    assert any("GONE" in a and "stop" in a for a in out["alerts"])


def test_a_failed_sale_does_not_free_its_slot(tmp_path, monkeypatch):
    n = MAX_POSITIONS - 1                                                   # every slot full, one of them breaking
    held = [_pos(f"H{i}", 50, 50.0, 50.0) for i in range(n)] + [_pos("GONE", 60, 40.0, 44.0)]
    broker = Broker(held)
    broker.close_position = lambda symbol: {"error": "rejected"}
    trades = [dict(SLEEVE_OPEN, symbol=f"H{i}") for i in range(n)] + [SLEEVE_OPEN]
    doc = _doc([_row("AAA", 7, 7, 1), _row("BBB", 6, 6, 2)] + [_row(f"H{i}", 6, 6, 5) for i in range(n)])
    hist = HISTORY + _hist("2026-10-17", {f"H{i}": 5 for i in range(n)}) + _hist("2026-10-24", {f"H{i}": 5 for i in range(n)})
    paths = _files(tmp_path, monkeypatch, trades=trades, doc=doc, history=hist)
    out = _run(broker, paths)
    assert out["sells"][0]["status"] == "failed"
    assert [o for o in broker.sent if o["side"] == "buy"] == []           # still every slot taken


def test_a_broad_mark_on_a_holding_never_sells_it_even_when_it_repeats():
    # The broad pattern matches ordinary wording ("positioned as an AI
    # provider"), and a filing stays current for months: it opens an issue
    # for a human instead (see the failure test above).
    good = _row("GOOD", 9, 9, 1); good["judgment"]["do_not_buy"] = True
    twice = [dict(h, do_not_buy=True) for h in _hist("2026-10-17", {"GOOD": 1}) + _hist("2026-10-24", {"GOOD": 1})]
    assert broken("GOOD", _doc([good]), twice, TODAY) is None


def test_two_weeks_running_means_two_lists_about_a_week_apart():
    rerun = _hist("2026-10-23", {"AAA": 1, "BBB": 2}) + _hist("2026-10-24", {"AAA": 1, "BBB": 2})    # a re-run the next day
    assert buy_candidates(DOC, rerun, TODAY) == []
    old = _hist("2026-09-19", {"AAA": 1, "BBB": 2}) + _hist("2026-10-24", {"AAA": 1, "BBB": 2})      # five weeks earlier
    assert buy_candidates(DOC, old, TODAY) == []


def test_absence_is_judged_against_the_last_list_of_any_kind():
    # Present last week on a list the judge failed on: not "off two weeks running"
    hist = _hist("2026-10-10", {"OTHER": 1}) + [{"date": "2026-10-17", "ticker": "HELD", "rank": 4, "total": 50.0}] \
        + _hist("2026-10-24", {"AAA": 1})
    assert broken("HELD", _doc([_row("AAA", 7, 7, 1)]), hist, TODAY) is None


# ── second review ────────────────────────────────────────────────────────────

def test_a_rejected_stop_replacement_puts_the_old_one_back_and_is_a_failure(tmp_path, monkeypatch):
    broker = Broker([_pos("GONE", 60, 40.0, 44.0)])
    broker.orders["s1"] = {"id": "s1", "symbol": "GONE", "side": "sell", "type": "stop", "status": "new", "qty": "60",
                           "stop_price": "36.80"}
    real = broker.place_order
    broker.place_order = lambda **kw: {"error": "rejected"} if kw.get("stop_price") == 30.0 else real(**kw)
    doc = _doc([_row("GONE", 7, 7, 1)])
    hist = _hist("2026-10-17", {"GONE": 1}) + _hist("2026-10-24", {"GONE": 1})
    paths = _files(tmp_path, monkeypatch, trades=[SLEEVE_OPEN], doc=doc, history=hist)
    out = _run(broker, paths)
    assert [(o["symbol"], o["stop_price"]) for o in broker.sent if o.get("order_type") == "stop"] == [("GONE", 36.8)]
    assert any("GONE" in f and "stop" in f for f in out["failures"]) and not any("replaced" in a for a in out["alerts"])


def test_only_a_strong_or_self_reported_attempt_can_sell_a_holding():
    weak = _row("WEAK", 9, 9, 1); weak["judgment"].update(do_not_buy=True)                      # broad pattern only
    strong = _row("STRONG", 9, 9, 2); strong["judgment"].update(do_not_buy=True, manipulation=True)
    doc = _doc([weak, strong])
    hist = [dict(h, do_not_buy=True, manipulation=h["ticker"] == "STRONG")
            for h in _hist("2026-10-17", {"WEAK": 1, "STRONG": 2})] + _hist("2026-10-24", {"WEAK": 1, "STRONG": 2})
    assert broken("WEAK", doc, hist, TODAY) is None
    assert "two lists" in broken("STRONG", doc, hist, TODAY)


def test_a_mark_on_a_holding_is_a_failure_so_an_issue_opens(tmp_path, monkeypatch):
    held = _row("AAA", 7, 7, 1); held["judgment"]["do_not_buy"] = True
    doc = _doc([held, _row("BBB", 6, 6, 2)])
    broker = Broker([_pos("AAA", 50, 50.0, 50.0)])
    paths = _files(tmp_path, monkeypatch, trades=[dict(SLEEVE_OPEN, symbol="AAA")], doc=doc)
    out = _run(broker, paths)
    assert any("AAA" in f and "address" in f for f in out["failures"])


def test_both_sale_rules_need_the_earlier_list_to_be_last_weeks():
    strong = _row("S", 9, 9, 1); strong["judgment"].update(do_not_buy=True, manipulation=True)
    doc = _doc([strong])
    flagged = lambda day: [{"date": day, "ticker": "S", "rank": 1, "total": 60.0, "judgment_rank": 1, "do_not_buy": True,
                            "manipulation": True}]
    assert broken("S", doc, flagged("2026-10-23") + flagged("2026-10-24"), TODAY) is None     # a re-run the next day
    assert broken("S", doc, flagged("2026-09-12") + flagged("2026-10-24"), TODAY) is None     # six weeks earlier
    absent = _doc([_row("AAA", 7, 7, 1)])
    assert broken("GONE", absent, _hist("2026-10-23", {"X": 1}) + _hist("2026-10-24", {"AAA": 1}), TODAY) is None
    assert "two weeks" in broken("GONE", absent, _hist("2026-10-17", {"X": 1}) + _hist("2026-10-24", {"AAA": 1}), TODAY)


# ── earnings trims ───────────────────────────────────────────────────────────

def test_earnings_trim_sizes_a_holding_so_a_30pct_gap_costs_half_a_percent():
    from diamond_sleeve import earnings_trim
    soon = date(2026, 10, 28)
    t = earnings_trim("AAA", 60, 50.0, 5.0, 100_000.0, soon, TODAY)
    assert (t["shares"], t["keep"]) == (27, 33)            # 100k * 0.5% / 30% = $1,667 -> 33 shares at $50
    assert earnings_trim("AAA", 60, 50.0, 15.0, 100_000.0, soon, TODAY) is None     # up 15%: held full size
    assert earnings_trim("AAA", 60, 50.0, -10.0, 100_000.0, None, TODAY) is None    # no known report
    assert earnings_trim("AAA", 60, 50.0, 0.0, 100_000.0, date(2026, 11, 2), TODAY) is None   # a week out
    assert earnings_trim("AAA", 60, 50.0, 0.0, 100_000.0, date(2026, 10, 25), TODAY) is None  # already reported
    assert earnings_trim("AAA", 30, 50.0, 0.0, 100_000.0, soon, TODAY) is None      # already small enough
    assert earnings_trim("BIG", 3, 5_000.0, 0.0, 100_000.0, soon, TODAY)["keep"] == 1   # never trimmed to nothing


def _sleeve_holding(symbol="AAA", qty=60, cost=50.0, price=50.0, **trade):
    broker = Broker([_pos(symbol, qty, cost, price)])
    broker.orders["s1"] = {"id": "s1", "symbol": symbol, "side": "sell", "type": "stop", "status": "new",
                           "qty": str(qty), "stop_price": str(round(cost * 0.75, 2))}
    rec = dict({"symbol": symbol, "status": "OPEN", "sleeve": "diamond", "entry_date": "2026-10-19", "entry_price": cost,
                "shares": qty, "stop_arm": "sleeve", "stop_dist": 0.25}, **trade)
    return broker, rec


def test_a_holding_with_a_report_due_is_trimmed_logged_and_its_stop_put_back(tmp_path, monkeypatch):
    broker, rec = _sleeve_holding(price=52.0)
    paths = _files(tmp_path, monkeypatch, trades=[rec])
    out = _run(broker, paths, above=lambda s: False, report=lambda s: date(2026, 10, 27))
    keep = int(100_000 * 0.005 / 0.30 // 52.0)
    assert broker.cancelled == ["s1"] and broker.closed == [("AAA", 60 - keep)]
    assert out["trims"][0]["status"] == "trimmed" and out["failures"] == []
    t = json.loads(paths["trades_file"].read_text())[0]
    assert t["status"] == "OPEN" and t["shares"] == keep
    assert t["trims"][0]["reason"] == "EARNINGS_TRIM" and t["trims"][0]["pnl_usd"] == round((60 - keep) * 2.0, 2)
    stops = [(o["symbol"], o["qty"], o["stop_price"]) for o in broker.sent if o["order_type"] == "stop"]
    assert stops == [("AAA", keep, 37.5)]                  # the sleeve's wide stop, on the shares left


def test_no_trim_when_up_15pct_when_no_report_is_due_or_on_a_dry_run(tmp_path, monkeypatch):
    broker, rec = _sleeve_holding(price=60.0)              # up 20%
    paths = _files(tmp_path, monkeypatch, trades=[rec])
    out = _run(broker, paths, above=lambda s: False, report=lambda s: date(2026, 10, 27))
    assert broker.closed == [] and out["trims"] == []
    broker, rec = _sleeve_holding()
    paths = _files(tmp_path, monkeypatch, trades=[rec])
    assert _run(broker, paths, above=lambda s: False)["trims"] == [] and broker.closed == []
    out = _run(broker, paths, execute=False, above=lambda s: False, report=lambda s: date(2026, 10, 27))
    assert broker.closed == [] and broker.sent == [] and out["trims"][0]["symbol"] == "AAA"


def test_a_holding_is_trimmed_once_per_report(tmp_path, monkeypatch):
    broker, rec = _sleeve_holding(trims=[{"report_date": "2026-10-27", "shares": 27}])
    paths = _files(tmp_path, monkeypatch, trades=[rec])
    out = _run(broker, paths, above=lambda s: False, report=lambda s: date(2026, 10, 27))
    assert broker.closed == [] and out["trims"] == []


def test_a_failed_trim_is_a_failure_and_the_holding_keeps_a_stop(tmp_path, monkeypatch):
    broker, rec = _sleeve_holding()
    broker.close_position = lambda symbol, qty=None: {"error": "insufficient qty"}
    paths = _files(tmp_path, monkeypatch, trades=[rec])
    out = _run(broker, paths, above=lambda s: False, report=lambda s: date(2026, 10, 27))
    assert "AAA: earnings trim failed" in out["failures"][0]
    assert json.loads(paths["trades_file"].read_text())[0]["shares"] == 60
    assert [(o["symbol"], o["qty"]) for o in broker.sent if o["order_type"] == "stop"] == [("AAA", 60)]


def test_a_holding_held_on_a_stale_judgment_is_flagged(tmp_path, monkeypatch):
    # WK 2026-11-07: its Q3 report changed the filings, the re-judge failed, and it
    # stayed held on the October judgment with nothing saying so
    held = _row("AAA", 6.5, 5.5, 1, stale=True)
    doc = _doc([held, _row("BBB", 6, 6, 2)])
    broker = Broker([_pos("AAA", 50, 50.0, 50.0)])
    paths = _files(tmp_path, monkeypatch, trades=[dict(SLEEVE_OPEN, symbol="AAA")], doc=doc)
    out = _run(broker, paths)
    assert any("AAA" in a and "re-judged" in a and "2026-10-24" in a for a in out["alerts"])
    assert not any("BBB" in a and "re-judged" in a for a in out["alerts"])        # not held: no alert
    assert not out["sells"]                                                       # stale never sells


def test_a_holding_that_has_run_has_its_stop_raised_and_its_peak_kept(tmp_path, monkeypatch):
    # VCEL 2026-11-12: +36% with its stop still 25% under entry; the sleeve's ratchet
    # moves it to break-even, and the best price is kept so the stop never falls back
    broker = Broker([_pos("GONE", 60, 40.0, 54.0)])                    # +35%
    broker.orders["s1"] = {"id": "s1", "symbol": "GONE", "side": "sell", "type": "stop", "status": "new", "qty": "60",
                           "stop_price": "30.00"}
    doc = _doc([_row("GONE", 7, 7, 1)])
    hist = _hist("2026-10-17", {"GONE": 1}) + _hist("2026-10-24", {"GONE": 1})
    paths = _files(tmp_path, monkeypatch, trades=[SLEEVE_OPEN], doc=doc, history=hist)
    out = _run(broker, paths)
    stops = [(o["symbol"], o["stop_price"]) for o in broker.sent if o.get("order_type") == "stop"]
    assert "s1" in broker.cancelled and stops == [("GONE", 40.2)]
    assert any("GONE" in a and "below" in a and "40.20" in a for a in out["alerts"])
    logged = [t for t in json.loads(paths["trades_file"].read_text()) if t["symbol"] == "GONE"][0]
    assert logged["peak_price"] == 54.0


def test_a_ratcheted_stop_is_not_lowered_when_the_price_falls_back(tmp_path, monkeypatch):
    broker = Broker([_pos("GONE", 60, 40.0, 45.0)])                    # back to +12.5%
    broker.orders["s1"] = {"id": "s1", "symbol": "GONE", "side": "sell", "type": "stop", "status": "new", "qty": "60",
                           "stop_price": "40.20"}
    doc = _doc([_row("GONE", 7, 7, 1)])
    hist = _hist("2026-10-17", {"GONE": 1}) + _hist("2026-10-24", {"GONE": 1})
    paths = _files(tmp_path, monkeypatch, trades=[dict(SLEEVE_OPEN, peak_price=54.0)], doc=doc, history=hist)
    out = _run(broker, paths)
    assert "s1" not in broker.cancelled and not [o for o in broker.sent if o.get("order_type") == "stop"]
    assert not out["failures"]


# ── 2026-11-12: the sleeve becomes the main bet ─────────────────────────────

def _bars(n=260, base=50.0, drift=0.002, swing=0.04, last_swing=None, last_vol=None):
    import numpy as np
    closes = base * (1 + drift) ** np.arange(n)
    rng = np.full(n, swing)
    if last_swing is not None:
        rng[-10:] = last_swing
    highs, lows = closes * (1 + rng / 2), closes * (1 - rng / 2)
    vols = np.full(n, 1e6)
    if last_vol is not None:
        vols[-1] = last_vol
    return closes, highs, lows, vols


def test_pent_up_is_a_stock_basing_near_its_high_that_is_tightening_or_breaking_out():
    from diamond_sleeve import pent_up
    assert pent_up(*_bars(last_swing=0.015))["setup"] is True               # at its high, range coiling
    assert pent_up(*_bars(last_vol=3e6))["breakout"] is True                # new 20-day high on volume
    assert pent_up(*_bars(last_vol=3e6))["setup"] is True
    plain = pent_up(*_bars())                                               # at its high, nothing else going on
    assert plain["near_high"] is True and plain["setup"] is False
    falling = pent_up(*_bars(drift=-0.003, last_swing=0.015))               # tight, but far under its high
    assert falling["near_high"] is False and falling["setup"] is False
    assert pent_up(*_bars(n=40))["setup"] is None                           # too little history: unknown


def test_a_name_showing_the_setup_is_bought_first_but_none_is_blocked():
    rows = [_row("AAA", 7, 7, 1), _row("BBB", 6, 6, 2), _row("CCC", 6, 6, 3)]
    hist = _hist("2026-10-17", {"AAA": 1, "BBB": 2, "CCC": 3}) + _hist("2026-10-24", {"AAA": 1, "BBB": 2, "CCC": 3})
    marks = {"AAA": {"setup": False}, "BBB": {"setup": None}, "CCC": {"setup": True}}
    p = plan(_doc(rows), hist, {}, set(), 100_000.0, 50_000.0, TODAY, lambda s: True, lambda s: 50.0,
             setup=lambda s: marks[s])
    assert [b["symbol"] for b in p["buys"]] == ["CCC", "AAA", "BBB"]        # setup first, then judgment order
    assert p["buys"][0]["setup"] == {"setup": True} and set(p["setups"]) == {"AAA", "BBB", "CCC"}
    p = plan(_doc(rows), hist, {}, set(), 100_000.0, 2_660.0, TODAY, lambda s: True, lambda s: 50.0,
             setup=lambda s: marks[s])
    assert [b["symbol"] for b in p["buys"]] == ["CCC"]                      # cash for one: the setup gets it


def test_the_step_up_is_suggested_only_when_the_tracker_says_ready(tmp_path):
    from diamond_sleeve import step_up_note
    track = tmp_path / "diamond_track.json"
    def write(status):
        track.write_text(json.dumps({"generated_at": "2026-11-12", "picks": 8, "rows": [],
                                     "verdict": {"status": status, "text": "12 lists ahead"}}))
    write("collecting")
    assert step_up_note(0.40, track) is None
    write("ready")
    assert "40% -> 60%" in step_up_note(0.40, track) and "60% -> 80%" in step_up_note(0.60, track)
    assert step_up_note(0.80, track) is None                                # the last step
    assert step_up_note(0.40, tmp_path / "missing.json") is None
