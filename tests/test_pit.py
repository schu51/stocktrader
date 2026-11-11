"""Point-in-time history: what the bot knew, when."""
import gzip
import json
import subprocess

import pytest

import pit


def test_as_of_returns_the_version_known_at_that_moment(tmp_path):
    s = pit.Store(tmp_path)
    s.add("screener.json", json.dumps({"generated_at": "2026-10-02T18:57", "v": 1}).encode(), "2026-10-02T23:00:00Z")
    s.add("screener.json", json.dumps({"generated_at": "2026-10-03T13:16", "v": 2}).encode(), "2026-10-03T13:20:00Z")
    assert s.as_of("screener.json", "2026-10-03T09:15:00-04:00")["v"] == 1     # 13:15 UTC: the morning screen not yet out
    assert s.as_of("screener.json", "2026-10-03T09:21:00-04:00")["v"] == 2
    assert s.as_of("screener.json", "2026-10-01") is None
    row = s.version_at("screener.json", "2026-10-04")
    assert row["event_at"] == "2026-10-03T13:16"


def test_the_same_content_is_stored_once(tmp_path):
    s = pit.Store(tmp_path)
    raw = b'{"generated_at": "x"}'
    assert s.add("a.json", raw, "2026-10-01T00:00:00Z") is True
    assert s.add("a.json", raw, "2026-10-02T00:00:00Z") is False
    assert len(s.index("a.json")) == 1 and len(list(tmp_path.rglob("*.gz"))) == 1


def test_a_damaged_version_is_reported_not_returned(tmp_path):
    s = pit.Store(tmp_path)
    s.add("a.json", b'{"v": 1}', "2026-10-01T00:00:00Z")
    blob = next(tmp_path.rglob("*.gz"))
    blob.write_bytes(gzip.compress(b'{"v": 2}'))
    with pytest.raises(ValueError):
        pit.Store(tmp_path).as_of("a.json", "2026-10-02")


def test_versions_in_a_window(tmp_path):
    s = pit.Store(tmp_path)
    for d in ("01", "02", "03"):
        s.add("t.json", json.dumps({"d": d}).encode(), f"2026-10-{d}T12:00:00Z")
    assert [v["data"]["d"] for v in s.versions("t.json", "2026-10-02", "2026-10-03T23:59:59Z")] == ["02", "03"]


def test_backfill_reads_every_committed_version(tmp_path):
    repo = tmp_path / "repo"
    (repo / "docs" / "data").mkdir(parents=True)
    git = lambda *a, **env: subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True,
                                           env={"GIT_AUTHOR_DATE": env.get("d", ""), "GIT_COMMITTER_DATE": env.get("d", ""),
                                                "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                                                "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin"})
    git("init", "-q")
    for i, d in enumerate(("2026-10-01T10:00:00+00:00", "2026-10-02T10:00:00+00:00")):
        (repo / "docs" / "data" / "trades.json").write_text(json.dumps([{"n": i}]))
        git("add", "-A")
        git("commit", "-q", "-m", f"c{i}", d=d)
    s = pit.Store(tmp_path / "store")
    assert pit.backfill(s, repo=repo) == 2
    assert s.as_of("trades.json", "2026-10-01T12:00:00Z") == [{"n": 0}]
    assert s.as_of("trades.json", "2026-10-03") == [{"n": 1}]
    assert pit.backfill(s, repo=repo) == 0           # idempotent


def test_snapshot_skips_caches(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "screener.json").write_text("{}")
    (data / "osint_cache.json").write_text("{}")
    s = pit.Store(tmp_path / "store")
    assert pit.snapshot(s, data) == 1 and s.files() == ["screener.json"]
