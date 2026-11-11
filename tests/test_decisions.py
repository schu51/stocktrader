"""Decision records: every order recorded with its reason, rules and inputs."""
import json
from datetime import datetime, timezone

import decisions


def _fake_broker(monkeypatch, response):
    from alpaca_broker import AlpacaBroker
    b = AlpacaBroker.__new__(AlpacaBroker)
    monkeypatch.setattr(b, "_request", lambda *a, **k: response, raising=False)
    return b


def test_every_order_call_is_recorded_with_the_callers_reason(monkeypatch):
    b = _fake_broker(monkeypatch, {"id": "o1", "status": "accepted"})
    decisions.why("NVDA", "price 3% below the 50-day average", rule="below_50ma", sma50=181.2)
    b.close_position("NVDA")
    b.place_order(symbol="AMD", qty=10, side="sell", order_type="stop", stop_price=150.0, time_in_force="gtc")
    b.cancel_order("o9")
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows = decisions.read_day(day)
    assert [r["kind"] for r in rows] == ["sell", "stop", "cancel"]
    sell = rows[0]
    assert sell["symbol"] == "NVDA" and sell["why"]["rule"] == "below_50ma" and sell["why"]["facts"]["sma50"] == 181.2
    assert sell["output"]["order_id"] == "o1"
    assert rows[1]["why"] is None                       # no reason given: still recorded
    assert "code" in sell["rule_version"] and isinstance(sell["inputs"], dict)


def test_a_reason_is_used_once(monkeypatch):
    b = _fake_broker(monkeypatch, {"id": "o1"})
    decisions.why("NVDA", "first")
    b.close_position("NVDA")
    b.close_position("NVDA")
    rows = decisions.read_day(datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    assert rows[0]["why"]["reason"] == "first" and rows[1]["why"] is None


def test_a_failed_order_is_recorded_with_its_error(monkeypatch):
    b = _fake_broker(monkeypatch, {"error": "insufficient qty"})
    b.close_position("WK", qty=5)
    row = decisions.read_day(datetime.now(timezone.utc).strftime("%Y-%m-%d"))[0]
    assert row["output"]["error"] == "insufficient qty" and row["output"]["request"]["qty"] == 5


def test_recording_never_breaks_an_order(monkeypatch):
    b = _fake_broker(monkeypatch, {"id": "o1"})
    monkeypatch.setattr(decisions, "record", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("disk full")))
    assert b.close_position("NVDA") == {"id": "o1"}


def test_inputs_pin_each_shared_file_by_content(tmp_path):
    (tmp_path / "screener.json").write_text(json.dumps({"generated_at": "2026-11-11T14:16:04"}))
    pins = decisions.inputs(tmp_path)
    assert pins["screener.json"]["at"] == "2026-11-11T14:16:04" and len(pins["screener.json"]["sha"]) == 12
    assert "latest.json" not in pins


def test_one_file_per_job_per_day(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_WORKFLOW", "Intraday Exit Monitor")
    decisions.record("sell", "X", {"order_id": "1"}, reason={"reason": "r", "rule": "x", "facts": {}},
                     decisions_dir=tmp_path, now=datetime(2026, 11, 11, 15, 0, tzinfo=timezone.utc))
    assert (tmp_path / "2026-11-11" / "intraday_exit_monitor.json").exists()


def test_records_meet_their_contract():
    import contracts
    decisions.record("rank", "*", {"ranked": []}, reason={"reason": "daily", "rule": "rank", "facts": {}})
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    f = next((decisions.DECISIONS_DIR / day).glob("*.json"))
    assert contracts.validate(f, json.loads(f.read_text())) == []
