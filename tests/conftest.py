"""Keep tests from writing decision records or contract breaches into the repo's docs/data."""
import pytest


@pytest.fixture(autouse=True)
def _isolate_shared_records(tmp_path, monkeypatch):
    import contracts
    import decisions
    monkeypatch.setattr(decisions, "DECISIONS_DIR", tmp_path / "_decisions")
    monkeypatch.setattr(contracts, "VIOLATIONS_FILE", tmp_path / "_contract_violations.json")
    decisions._cache.clear()
    decisions._pending.clear()
    yield
