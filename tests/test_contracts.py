"""Data contracts and security identity."""
import json
import re
from pathlib import Path

import pytest

import contracts
import identity

ROOT = Path(__file__).parent.parent
RAW_JSON_LOADS_CEILING = 16     # may only fall: new code reads shared files through contracts.read


def test_every_committed_shared_file_has_a_contract_and_keeps_it():
    assert contracts.check() == {}


def test_no_new_raw_json_loads_in_the_bot():
    n = sum(len(re.findall(r"json\.loads?\(", f.read_text()))
            for f in ROOT.glob("*.py") if f.name != "contracts.py")
    assert n <= RAW_JSON_LOADS_CEILING, \
        f"{n} raw json.load(s) calls (ceiling {RAW_JSON_LOADS_CEILING}): read docs/data files with contracts.read"


def test_a_renamed_field_breaks_the_full_contract_and_a_wrong_type_breaks_reading(tmp_path):
    trade = {"trade_id": "t1", "symbol": "X", "status": "OPEN", "entry_date": "2026-11-10",
             "entry_ts": "2026-11-10T14:30:00", "entry_price": 10.0, "shares": 5}
    p = tmp_path / "trades.json"
    assert contracts.validate(p, [trade]) == []
    renamed = dict(trade)
    renamed["entry"] = renamed.pop("entry_price")
    assert any("entry_price" in e for e in contracts.validate(p, [renamed]))
    assert contracts.validate(p, [renamed], full=False) == []          # the reader handles its own default
    p.write_text(json.dumps([dict(trade, shares="five")]))
    with pytest.raises(contracts.ContractError):
        contracts.read(p)
    with pytest.raises(ValueError):                                     # existing corrupt-file handlers catch it
        contracts.read(p)


def test_the_legacy_postmarket_overwrite_would_have_been_caught(tmp_path):
    legacy = {"date": "2026-11-09", "generated_at": "x", "type": "post_market_review",
              "thesis_health": {}, "pattern_findings": []}
    problems = contracts.validate(tmp_path / "postmarket.json", legacy)
    assert any("portfolio" in p for p in problems)


def test_missing_file_gives_the_default_but_a_bad_one_raises(tmp_path):
    assert contracts.read(tmp_path / "trades.json", default=[]) == []
    (tmp_path / "trades.json").write_text("{not json")
    with pytest.raises(ValueError):
        contracts.read(tmp_path / "trades.json", default=[])


def test_write_keeps_the_data_and_records_a_breach(tmp_path, monkeypatch):
    monkeypatch.setattr(contracts, "VIOLATIONS_FILE", tmp_path / "contract_violations.json")
    p = tmp_path / "screener.json"
    problems = contracts.write(p, {"generated_at": "x"})
    assert problems and json.loads(p.read_text()) == {"generated_at": "x"}
    log = json.loads((tmp_path / "contract_violations.json").read_text())
    assert log[0]["file"] == "screener.json"


def test_contracts_are_found_by_name_including_month_files(tmp_path):
    assert contracts.contract_for(tmp_path / "options" / "2026-11.json") is not None
    assert contracts.contract_for(tmp_path / "osint" / "2026-11-11.json") is not None
    assert contracts.contract_for(tmp_path / "osint" / "ondemand" / "x.json") is not None
    assert contracts.contract_for(tmp_path / "nobody_knows.json") is None


@pytest.mark.parametrize("raw,canon,alp", [
    ("BRK.B", "BRK-B", "BRK.B"), ("brk-b", "BRK-B", "BRK.B"), ("BF/B", "BF-B", "BF.B"),
    (" aapl ", "AAPL", "AAPL"), (None, "", ""),
])
def test_spellings(raw, canon, alp):
    assert identity.canonical(raw) == canon and identity.alpaca(raw) == alp and identity.yahoo(raw) == canon


def test_is_ticker_rejects_options_and_paths():
    assert identity.is_ticker("BRK.B") and identity.is_ticker("brk-b") and identity.is_ticker("AAPL")
    assert not identity.is_ticker("AMD261218P00400000") and not identity.is_ticker("../etc") and not identity.is_ticker("")


def test_registry_keeps_ids_across_a_ticker_change(tmp_path, monkeypatch):
    sec = {"FB": {"cik": 1326801, "name": "Meta"}, "META": {"cik": 1326801, "name": "Meta"},
           "BRK.B": {"cik": 1067983, "name": "Berkshire"}}
    day1 = identity.merge({}, [{"id": "a1", "symbol": "FB", "exchange": "NASDAQ", "name": "Meta"},
                               {"id": "b1", "symbol": "BRK.B", "exchange": "NYSE", "name": "Berkshire B"},
                               {"id": "x", "symbol": "XYZ", "exchange": "OTC"}], sec, "2022-06-01")
    assert set(day1) == {"a1", "b1"} and day1["b1"]["ticker"] == "BRK-B" and day1["b1"]["cik"] == 1067983
    day2 = identity.merge(day1, [{"id": "a1", "symbol": "META", "exchange": "NASDAQ", "name": "Meta"}], sec, "2022-06-09")
    assert [t["ticker"] for t in day2["a1"]["tickers"]] == ["FB", "META"]
    assert day2["a1"]["tickers"][0]["until"] == "2022-06-09" and day2["b1"]["active"] is False

    path = tmp_path / "securities.json"
    path.write_text(json.dumps({"generated_at": "x", "securities": day2}))
    monkeypatch.setattr(identity, "REGISTRY_FILE", path)
    identity.reload()
    reg = identity._registry()
    assert reg["by_ticker"]["META"] == "a1" and reg["by_old_ticker"]["FB"] == "a1"


def test_lookups_use_the_registry(tmp_path, monkeypatch):
    path = tmp_path / "securities.json"
    path.write_text(json.dumps({"generated_at": "x", "securities": {
        "a1": {"ticker": "META", "cik": 1326801, "active": True,
               "tickers": [{"ticker": "FB", "since": None, "until": "2022-06-09"}, {"ticker": "META", "since": "2022-06-09"}]}}}))
    monkeypatch.setattr(identity, "REGISTRY_FILE", path)
    identity.reload()
    assert identity.security_id("META") == "a1" and identity.security_id("fb") == "a1"
    assert identity.resolve("FB") == "META" and identity.cik("META") == 1326801
    assert identity.security_id("NOPE") is None and identity.resolve("nope") == "NOPE"


def test_new_paths_get_their_own_git_add_line():
    # `git add a b missing/` adds NOTHING when one path is missing: a new folder on the
    # same line as trades.json would silently skip committing the trade log.
    for wf in (ROOT / ".github" / "workflows").glob("*.yml"):
        for line in wf.read_text().splitlines():
            if "git add" in line and ("decisions/" in line or "securities.json" in line):
                assert line.strip().split()[2:3] and len([w for w in line.split() if w.startswith("docs/")]) == 1, \
                    f"{wf.name}: {line.strip()}"
