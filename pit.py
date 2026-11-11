"""
Point-in-time history
=====================
Answers "what did the bot know at 9:15 on Oct 3?" for every shared file.

The store keeps every distinct version of every file in docs/data, each with
two times:
  known_at   when the bot had it: the commit that published it (or, for a
             live snapshot, the moment it was taken)
  event_at   what the file says it describes: its generated_at or date field

It lives on its own branch, `history`, so main stays small, and only ever
gains files (one gzip per version), so git never rewrites it.

    import pit
    store = pit.Store(".history")                     # a checkout of the history branch
    screen = store.as_of("screener.json", "2026-10-03T09:15:00-04:00")
    for v in store.versions("trades.json", "2026-10-01", "2026-10-31"): ...

Layout:
  <file path under docs/data>/index.jsonl            one line per version: known_at, event_at, sha, blob
  <file path under docs/data>/YYYY-MM/<sha>.json.gz  the version itself

Filling it:
  python pit.py --backfill --store .history          every version from main's git history (needs full history)
  python pit.py --snapshot --store .history          today's files, after the day's jobs (pit.yml, 18:45 ET)

A version is stored once (by content hash). Reading checks the hash, so a
damaged blob is reported, never silently returned.
"""

import argparse
import gzip
import hashlib
import json
import logging
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.resolve()
DOCS_DATA = ROOT / "docs" / "data"
DATA_PREFIX = "docs/data/"
SKIP = ("big_movers_cache.json", "osint_cache.json", "contract_violations.json")   # caches, not knowledge


def _ts(value, end_of_day: bool = True) -> datetime:
    """Any ISO time (or a date: its end, or its start if end_of_day=False) as aware UTC; naive = UTC."""
    if isinstance(value, datetime):
        dt = value
    else:
        s = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s if "T" in s or " " in s else s + ("T23:59:59" if end_of_day else "T00:00:00"))
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def event_at(data: Any) -> Optional[str]:
    if isinstance(data, dict):
        v = data.get("generated_at") or data.get("date")
        return str(v) if v else None
    return None


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self._index: Dict[str, List[Dict]] = {}

    def _dir(self, name: str) -> Path:
        return self.root / name

    def index(self, name: str) -> List[Dict]:
        if name not in self._index:
            f = self._dir(name) / "index.jsonl"
            rows = [json.loads(l) for l in f.read_text().splitlines() if l.strip()] if f.exists() else []
            self._index[name] = sorted(rows, key=lambda r: r["known_at"])
        return self._index[name]

    def files(self) -> List[str]:
        return sorted(str(p.parent.relative_to(self.root).as_posix()) for p in self.root.rglob("index.jsonl"))

    def add(self, name: str, raw: bytes, known_at, event: Optional[str] = None) -> bool:
        """Store one version. False if this exact content is already the latest version known by then."""
        sha = hashlib.sha256(raw).hexdigest()[:16]
        known = _ts(known_at).isoformat(timespec="seconds")
        rows = self.index(name)
        prior = [r for r in rows if r["known_at"] <= known]
        if prior and prior[-1]["sha"] == sha:
            return False
        if any(r["sha"] == sha and r["known_at"] == known for r in rows):
            return False
        blob = f"{known[:7]}/{sha}.json.gz"
        path = self._dir(name) / blob
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(gzip.compress(raw, mtime=0))
        if event is None:
            try:
                event = event_at(json.loads(raw))
            except Exception:
                event = None
        row = {"known_at": known, "event_at": event, "sha": sha, "blob": blob}
        with open(self._dir(name) / "index.jsonl", "a") as f:
            f.write(json.dumps(row) + "\n")
        rows.append(row)
        rows.sort(key=lambda r: r["known_at"])
        return True

    def _load(self, name: str, row: Dict) -> Any:
        raw = gzip.decompress((self._dir(name) / row["blob"]).read_bytes())
        if hashlib.sha256(raw).hexdigest()[:16] != row["sha"]:
            raise ValueError(f"{name} version {row['sha']} is damaged")
        return json.loads(raw)

    def version_at(self, name: str, when) -> Optional[Dict]:
        """Index row of the version the bot had at `when` (the latest known_at <= when)."""
        t = _ts(when).isoformat(timespec="seconds")
        best = None
        for r in self.index(name):
            if r["known_at"] <= t:
                best = r
            else:
                break
        return best

    def as_of(self, name: str, when, default: Any = None) -> Any:
        """The contents of `name` as the bot knew them at `when`."""
        row = self.version_at(name, when)
        return self._load(name, row) if row else default

    def versions(self, name: str, start=None, end=None) -> Iterator[Dict]:
        """Every version known between start and end: {known_at, event_at, sha, data}."""
        lo = _ts(start, end_of_day=False).isoformat(timespec="seconds") if start else ""
        hi = _ts(end).isoformat(timespec="seconds") if end else "9999"
        for r in self.index(name):
            if lo <= r["known_at"] <= hi:
                yield dict(r, data=self._load(name, r))


# ---------------------------------------------------------------------------
# Filling
# ---------------------------------------------------------------------------

def snapshot(store: Store, data_dir: Path = DOCS_DATA, now: Optional[datetime] = None) -> int:
    """Today's shared files into the store, known now."""
    now = now or datetime.now(timezone.utc)
    added = 0
    for f in sorted(Path(data_dir).rglob("*.json")):
        name = f.relative_to(data_dir).as_posix()
        if f.name in SKIP:
            continue
        added += store.add(name, f.read_bytes(), now)
    return added


def backfill(store: Store, repo: Path = ROOT, ref: str = "HEAD", since: Optional[str] = None) -> int:
    """Every version of every shared file in git history, known at its commit time."""
    args = ["git", "-C", str(repo), "log", "--reverse", "--format=%H %cI", "--name-only", ref]
    if since:
        args.insert(4, f"--since={since}")
    out = subprocess.run(args + ["--", DATA_PREFIX], capture_output=True, text=True, check=True).stdout
    added, commit, when = 0, None, None
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.split(" ")
        if len(parts) == 2 and len(parts[0]) == 40:
            commit, when = parts
            continue
        if not line.startswith(DATA_PREFIX) or not line.endswith(".json"):
            continue
        name = line[len(DATA_PREFIX):]
        if Path(name).name in SKIP:
            continue
        shown = subprocess.run(["git", "-C", str(repo), "show", f"{commit}:{line}"], capture_output=True)
        if shown.returncode != 0:
            continue                       # deleted in this commit
        added += store.add(name, shown.stdout, when)
    return added


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Point-in-time history of the shared data files")
    ap.add_argument("--store", default=".history")
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--since")
    ap.add_argument("--update", action="store_true", help="backfill commits newer than the store's latest version")
    ap.add_argument("--snapshot", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    store = Store(args.store)
    if args.backfill:
        logger.info(f"Backfill: {backfill(store, since=args.since)} versions added")
    if args.update:
        latest = max((r["known_at"] for f in store.files() for r in store.index(f)), default=None)
        since = (_ts(latest) - timedelta(days=1)).isoformat() if latest else None
        logger.info(f"Update since {since}: {backfill(store, since=since)} versions added")
    if args.snapshot:
        logger.info(f"Snapshot: {snapshot(store)} versions added")
    return 0


if __name__ == "__main__":
    sys.exit(main())
