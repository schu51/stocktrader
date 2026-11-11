import sys
from datetime import date, datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

POSITIONS = [{"symbol": "VCEL", "qty": "64", "asset_class": "us_equity"},
             {"symbol": "KO", "qty": "250", "asset_class": "us_equity"},
             {"symbol": "OPT", "qty": "1", "asset_class": "us_option"}]
TRADES = [{"symbol": "VCEL", "status": "OPEN", "sleeve": "diamond"}, {"symbol": "KO", "status": "OPEN"}]


def test_holdings_tag_the_sleeve_and_skip_options():
    from put_shadow import holdings
    assert holdings(POSITIONS, TRADES) == [{"symbol": "VCEL", "shares": 64.0, "group": "sleeve"},
                                           {"symbol": "KO", "shares": 250.0, "group": "book"}]


def test_a_report_is_opened_on_the_last_session_before_it():
    from put_shadow import due_to_open
    wed, thu, fri, mon = date(2026, 11, 11), date(2026, 11, 12), date(2026, 11, 13), date(2026, 11, 16)
    assert due_to_open(wed, thu)                 # tomorrow: lands after tonight's close or before the open
    assert not due_to_open(wed, wed)             # today: may already have gapped at the open
    assert not due_to_open(wed, fri)             # too early
    assert due_to_open(fri, mon) and not due_to_open(fri, None)


def test_occ_symbols_parse():
    from put_shadow import parse_occ
    assert parse_occ("VCEL261120P00055000") == {"root": "VCEL", "expiry": date(2026, 11, 20), "type": "P", "strike": 55.0}
    assert parse_occ("BRK.B261120P00500500")["strike"] == 500.5
    assert parse_occ("junk") is None


CHAIN = {
    "VCEL261106P00055000": {"bid": 0.5, "ask": 0.7, "iv": 1.1},          # expires before the settle day
    "VCEL261120P00060000": {"bid": 4.0, "ask": 4.4, "iv": 0.9},
    "VCEL261120P00055000": {"bid": 1.6, "ask": 1.8, "iv": 0.95},
    "VCEL261120P00052500": {"bid": 0.9, "ask": 1.1, "iv": 1.0},
    "VCEL261120P00050000": {"bid": 0.5, "ask": 0.6, "iv": 1.05},
    "VCEL261127P00050000": {"bid": 0.9, "ask": 1.0, "iv": 0.8},          # later expiry: not used
    "VCEL261120P00045000": {"bid": None, "ask": 0.2, "iv": None},        # one-sided: skipped
}


def test_puts_are_the_first_expiry_after_the_report_at_the_nearest_strikes():
    from put_shadow import pick_puts
    p = pick_puts(CHAIN, 59.0, date(2026, 11, 13))
    assert {k: v["occ"][-8:] for k, v in p.items()} == {
        "1.00": "00060000", "0.95": "00055000", "0.90": "00052500", "0.85": "00050000"}
    assert all(v["expiry"] == "2026-11-20" for v in p.values())
    assert p["0.90"]["premium_pct"] == pytest.approx(1.1 / 59, abs=1e-5)
    assert p["0.90"]["half_spread_pct_of_premium"] == pytest.approx(0.1, abs=1e-3)
    assert all(v is None for v in pick_puts({}, 59.0, date(2026, 11, 13)).values())


def _run(day, state, price, chain=CHAIN, quotes=None, reports=None):
    from put_shadow import run
    now = datetime(*map(int, day.split("-")), 20, 50, tzinfo=timezone.utc)      # 15:50 ET in November
    return run(now, POSITIONS, TRADES, state,
               chain=lambda sym, px, settle: chain if sym == "VCEL" else {},
               quotes=lambda occs: quotes or {},
               prices=lambda syms: {s: price for s in syms},
               reports=reports or (lambda s, d: date(2026, 11, 12) if s == "VCEL" else None))


def test_open_before_the_report_then_settle_after_it_in_dollars():
    first = _run("2026-11-11", {}, 59.0)
    assert [r["symbol"] for r in first["open"]] == ["VCEL"] and first["settled"] == []
    rec = first["open"][0]
    assert rec["settle_on"] == "2026-11-13" and rec["contracts"] == 1 and rec["coverage"] == pytest.approx(1.562, abs=1e-3)
    # report day: nothing to settle yet, and not opened twice
    mid = _run("2026-11-12", first, 50.0)
    assert len(mid["open"]) == 1 and mid["settled"] == []
    # settle day: VCEL gapped to 40; the 52.50 put bids 12.40
    q = {"VCEL261120P00052500": {"bid": 12.4, "ask": 12.8}, "VCEL261120P00060000": {"bid": 19.9, "ask": 20.3}}
    done = _run("2026-11-13", mid, 40.0, quotes=q)
    assert done["open"] == [] and len(done["settled"]) == 1
    r = done["settled"][0]
    assert r["move"] == pytest.approx(40 / 59 - 1, abs=1e-5)
    res = r["results"]["0.90"]
    assert res["proceeds"] == 12.5 and res["how"] == "bid"                 # bid under intrinsic: exercise
    assert res["put_pnl_usd"] == pytest.approx(100 * (12.5 - 1.1))
    assert res["position_pnl_usd"] == pytest.approx(64 * (40 - 59) + 100 * 11.4)   # one contract over-covers 64 shares
    assert r["results"]["0.85"]["how"] == "no quote: intrinsic" and r["results"]["0.85"]["proceeds"] == 10.0
    s = done["summary"]["sleeve"]
    assert s["reports"] == 1 and s["strikes"]["0.90"]["share_paid_off"] == 1.0


def test_an_expired_put_settles_at_intrinsic_and_a_missing_price_waits():
    from put_shadow import settle_record
    rec = {"symbol": "X", "group": "book", "shares": 100, "s0": 100.0, "contracts": 1, "coverage": 1.0,
           "report": "2026-11-12", "settle_on": "2026-11-13",
           "puts": {"0.90": {"occ": "X261113P00090000", "strike": 90.0, "expiry": "2026-11-13", "ask": 1.0,
                             "premium_pct": 0.01, "iv": 0.5, "half_spread_pct_of_premium": 0.1}}}
    out = settle_record(rec, date(2026, 11, 16), 80.0, {"X261113P00090000": {"bid": 0.0}})
    assert out["results"]["0.90"]["how"] == "expired" and out["results"]["0.90"]["proceeds"] == 10.0
    first = _run("2026-11-11", {}, 59.0)
    from put_shadow import run
    later = run(datetime(2026, 11, 13, 20, 50, tzinfo=timezone.utc), POSITIONS, TRADES, first,
                chain=lambda *a: {}, quotes=lambda o: {}, prices=lambda syms: {}, reports=lambda s, d: None)
    assert len(later["open"]) == 1 and "retry" in later["notes"][0]


def test_no_chain_means_nothing_opened_and_it_says_why():
    out = _run("2026-11-11", {}, 59.0, chain={})
    assert out["open"] == [] and "no listed puts" in out["notes"][0]


def test_the_shadow_never_touches_orders():
    src = (Path(__file__).parent.parent / "put_shadow.py").read_text()
    for call in ("submit_order", "place_order", "cancel_order", "replace_order", "close_position"):
        assert call not in src
