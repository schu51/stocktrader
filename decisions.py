"""
Decision records
================
Every order the bot sends, and the daily ranking, leaves a record of what was
decided, why, under which rules and from which inputs. This is what lets a
live trade be replayed and compared with the backtest, and what the agent
evals score against.

How records are made:
  * The broker records every order itself (alpaca_broker.AlpacaBroker: place,
    close, cancel), so no order goes unrecorded even where a caller adds no reason.
  * A caller that knows WHY says so just before the order:
        decisions.why("NVDA", "price 3.1% below the 50-day average", rule="below_50ma", sma50=181.2)
    and that reason, rule and facts are attached to the next order for that symbol.
  * The screener records the day's ranking (decisions.record("rank", ...)).

Each record:
  at            when (UTC)
  job           the workflow or script that made it
  kind          buy | sell | stop | cancel | rank | ...
  symbol
  output        what was sent and what came back (order id, status or error)
  why           {reason, rule, facts} from the caller, if given
  rule_version  {code: git sha, rules: live rules version, gates: active entry
                gates, weights: ranking weights version}
  inputs        every shared input file as it stood: {file: {at, sha}}, so the
                exact version can be found in git history or the history store

Files: docs/data/decisions/YYYY-MM-DD/<job>.json. One file per job per day,
so two jobs running at once never write the same file.

Recording never raises: a decision that cannot be recorded is logged and the
order still goes through.
"""

import hashlib
import json
import logging
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.resolve()
DOCS_DATA = ROOT / "docs" / "data"
DECISIONS_DIR = DOCS_DATA / "decisions"
CHALLENGERS = ROOT / "research" / "challengers.json"

# Inputs worth pinning on every decision (the ones decisions are made from)
INPUT_FILES = ("screener.json", "latest.json", "trades.json", "experiments.json", "theses.json",
               "weights.json", "universe.json", "premarket.json", "sma50_cache.json",
               "osint_latest.json", "options_sentiment.json", "big_movers.json")

_pending: Dict[str, Dict] = {}
_cache: Dict[str, Any] = {}


def job_name() -> str:
    wf = os.getenv("GITHUB_WORKFLOW") or Path(os.getenv("DECISIONS_JOB") or "local").stem
    return re.sub(r"[^a-z0-9]+", "_", wf.lower()).strip("_") or "local"


def why(symbol: str, reason: str, rule: Optional[str] = None, **facts) -> None:
    """Attach a reason to the next order for `symbol`."""
    _pending[str(symbol).upper()] = {"reason": reason, "rule": rule,
                                     "facts": {k: _plain(v) for k, v in facts.items()}}


def _plain(v):
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v][:50]
    if isinstance(v, dict):
        return {str(k): _plain(x) for k, x in list(v.items())[:50]}
    try:
        return float(v)
    except Exception:
        return str(v)[:200]


def _file_pin(path: Path) -> Optional[Dict]:
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    at = None
    try:
        import contracts
        data = contracts.read(path)
        if isinstance(data, dict):
            at = data.get("generated_at") or data.get("date")
    except Exception:
        pass
    return {"at": at, "sha": hashlib.sha256(raw).hexdigest()[:12]}


def inputs(data_dir: Path = None) -> Dict[str, Dict]:
    """Pins of the shared input files as they stand now (taken once per run)."""
    key = str(data_dir or DOCS_DATA)
    if key not in _cache:
        d = Path(data_dir or DOCS_DATA)
        _cache[key] = {f: p for f in INPUT_FILES if (p := _file_pin(d / f))}
    return _cache[key]


def rule_version() -> Dict:
    """Which code and which live rules made the decision."""
    if "rules" in _cache:
        return _cache["rules"]
    import contracts
    out: Dict[str, Any] = {"code": (os.getenv("GITHUB_SHA") or "")[:7] or None}
    if not out["code"]:
        try:
            out["code"] = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                                         capture_output=True, text=True, timeout=5).stdout.strip() or None
        except Exception:
            pass
    try:
        versions = contracts.read(CHALLENGERS).get("rules_versions") or []
        out["rules"] = (versions[-1] or {}).get("name") if versions else None
    except Exception:
        out["rules"] = None
    try:
        exps = contracts.read(DOCS_DATA / "experiments.json").get("experiments") or {}
        out["gates"] = sorted(n for n, e in exps.items() if e.get("state") in ("active", "adopted"))
    except Exception:
        out["gates"] = None
    try:
        out["weights"] = (contracts.read(DOCS_DATA / "weights.json").get("active") or {}).get("version")
    except Exception:
        out["weights"] = None
    _cache["rules"] = out
    return out


def record(kind: str, symbol: str, output: Dict, reason: Optional[Dict] = None,
           decisions_dir: Path = None, now: Optional[datetime] = None) -> Optional[Dict]:
    """Append one decision record. Never raises."""
    try:
        import contracts
        now = now or datetime.now(timezone.utc)
        rec = {"at": now.isoformat(timespec="seconds"), "job": job_name(), "kind": kind,
               "symbol": str(symbol).upper(), "output": _plain(output),
               "why": reason if reason is not None else _pending.pop(str(symbol).upper(), None),
               "rule_version": rule_version(), "inputs": inputs()}
        path = Path(decisions_dir or DECISIONS_DIR) / now.strftime("%Y-%m-%d") / f"{rec['job']}.json"
        rows = contracts.read(path, default=[])
        rows.append(rec)
        contracts.write(path, rows, indent=1)
        return rec
    except Exception as e:
        logger.error(f"Decision for {symbol} not recorded: {e}")
        return None


def record_order(action: str, symbol: str, request: Dict, result: Any) -> None:
    """Called by the broker after every order call."""
    side = str(request.get("side") or "").lower()
    otype = str(request.get("type") or request.get("order_type") or "").lower()
    if action == "cancel":
        kind = "cancel"
    elif action == "close":
        kind = "sell"
    elif otype in ("stop", "stop_limit") and side == "sell":
        kind = "stop"
    else:
        kind = side or action
    res = result if isinstance(result, dict) else {"result": str(result)[:200]}
    err = (res.get("error") or res.get("message")) if ("error" in res or "message" in res) else None
    out = {"request": request, "order_id": res.get("id"), "status": res.get("status"), "error": err}
    record(kind, symbol or "*", out)


def read_day(day: str, decisions_dir: Path = None) -> List[Dict]:
    """All records for a day (YYYY-MM-DD), every job, in time order."""
    import contracts
    d = Path(decisions_dir or DECISIONS_DIR) / day
    rows = []
    for f in sorted(d.glob("*.json")) if d.exists() else []:
        rows += contracts.read(f, default=[])
    return sorted(rows, key=lambda r: r.get("at", ""))
