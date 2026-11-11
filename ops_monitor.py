"""
Ops monitor
===========
Watches the machinery, not the market. Each weekday at 10:45 and 17:15 ET it
checks that the jobs ran, their outputs are fresh, the model account is
alive, the shared files keep their contracts and every commit stayed in its
lane. It writes docs/data/ops_status.json and fails (opening an issue) when
anything is red. Places no orders, calls no model.

Built after the agent-eval review found the macro research job down for four
days on API credit, the intraday exit check failing 1 run in 15, and a
scheduled task overwriting another job's file, all without anyone noticing.

Checks
  jobs        trading-path workflow failed in the last 24h: red; any other: amber;
              a weekday job that was due today and never ran: red (trading) / amber
  freshness   each shared output dated today once its job's time has passed
  model       the API account behind macro research and the diamond judge; theses
              not re-checked in 8 days
  contracts   contracts.check(): every shared file matches its contract
  scope       scope_audit: no job wrote outside its lane (direct code pushes: amber)
  issues      failure issues left open more than 2 days
"""

import argparse
import json
import logging
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

import contracts

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.resolve()
DOCS_DATA = ROOT / "docs" / "data"
OUT_FILE = DOCS_DATA / "ops_status.json"
ET = ZoneInfo("America/New_York")
REPO = "schu51/stocktrader"

TRADING_PATH = {"daily_open.yml", "daily_trade.yml", "intraday_exit.yml", "stop_placement.yml",
                "diamond_sleeve.yml", "postmarket.yml", "portfolio_sync.yml", "dispatcher.yml"}

# file -> (ET time after which it must be dated today, weekdays)
FRESH = {
    "osint_latest.json": "08:45",
    "premarket.json": "09:15",
    "screener.json": "09:45",
    "postmarket.json": "16:45",
    "options_sentiment.json": "16:45",
    "big_movers.json": "17:00",
    "eval_latest.json": "17:15",
}
THESIS_MAX_AGE_DAYS = 8
ISSUE_MAX_AGE_DAYS = 2
RANK = {"green": 0, "amber": 1, "red": 2}


def _check(name: str, status: str, detail: str, items: Optional[list] = None) -> Dict:
    return {"name": name, "status": status, "detail": detail, **({"items": items} if items else {})}


def _gh(args: List[str]):
    out = subprocess.run(["gh", "api", *args], capture_output=True, text=True, check=True, timeout=60).stdout
    return json.loads(out or "null")


# ── checks (pure, given their inputs) ────────────────────────────────────────

def check_jobs(runs: List[Dict], due_today: List[str]) -> Dict:
    """runs: [{workflow (file name), conclusion, created_at}] from the last 24h."""
    failed = sorted({r["workflow"] for r in runs if r.get("conclusion") == "failure"})
    ran = {r["workflow"] for r in runs}
    missing = sorted(set(due_today) - ran)
    red = [w for w in failed + missing if w in TRADING_PATH]
    items = [f"{w}: failed" for w in failed] + [f"{w}: due today, never ran" for w in missing]
    if red:
        return _check("jobs", "red", f"{len(red)} trading-path job(s) failed or missed", items)
    if items:
        return _check("jobs", "amber", f"{len(items)} job(s) failed or missed", items)
    return _check("jobs", "green", f"{len(ran)} workflows ran in the last 24h, none failed")


def file_date(data) -> Optional[str]:
    if not isinstance(data, dict):
        return None
    for k in ("sync_at", "generated_at", "date"):
        v = data.get(k)
        if v:
            try:
                dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
                if dt.tzinfo:
                    dt = dt.astimezone(ET)
                return dt.date().isoformat()
            except ValueError:
                return str(v)[:10]
    return None


def check_freshness(now: datetime, read: Callable[[str], Optional[dict]]) -> Dict:
    local = now.astimezone(ET)
    if local.weekday() >= 5:
        return _check("freshness", "green", "weekend: nothing expected")
    stale = []
    for name, after in FRESH.items():
        hh, mm = map(int, after.split(":"))
        if (local.hour, local.minute) < (hh, mm):
            continue
        d = file_date(read(name))
        if d != local.date().isoformat():
            stale.append(f"{name}: last {d or 'never'} (due by {after} ET)")
    trading = [s for s in stale if s.split(":")[0] in ("screener.json", "postmarket.json")]
    if trading:
        return _check("freshness", "red", f"{len(stale)} output(s) not from today", stale)
    if stale:
        return _check("freshness", "amber", f"{len(stale)} output(s) not from today", stale)
    return _check("freshness", "green", "every output due by now is from today")


def check_model(macro_brief: Optional[dict], theses: Optional[dict], today: date) -> Dict:
    items = []
    err = str((macro_brief or {}).get("error") or "") + " " + str((macro_brief or {}).get("revalidation_error") or "")
    status = "green"
    if "credit balance" in err.lower():
        status, items = "red", ["Anthropic API account out of credit: macro research and the diamond judge cannot run"]
    elif (macro_brief or {}).get("status") == "error":
        status, items = "amber", [f"macro research failed: {err.strip()[:160]}"]
    old = []
    for t in (theses or {}).get("theses") or []:
        lv = t.get("last_validated") or t.get("created_at")
        try:
            age = (today - date.fromisoformat(str(lv)[:10])).days
        except ValueError:
            continue
        if age > THESIS_MAX_AGE_DAYS:
            old.append(f"{t.get('id')}: last checked {age} days ago")
    if old:
        items.append(f"{len(old)} live thesis(es) not re-checked in {THESIS_MAX_AGE_DAYS} days but still used in ranking")
        status = max(status, "amber", key=RANK.get)
    return _check("model", status, items[0] if items else "model-backed jobs healthy", items or None)


def check_contracts(problems: Dict[str, List[str]]) -> Dict:
    if problems:
        return _check("contracts", "red", f"{len(problems)} shared file(s) off contract",
                      [f"{f}: {p[0]}" for f, p in list(problems.items())[:10]])
    return _check("contracts", "green", "every shared file matches its contract")


def check_scope(findings: List[Dict]) -> Dict:
    breaches = [f for f in findings if f["kind"] == "breach"]
    direct = [f for f in findings if f["kind"] == "direct"]
    items = [f"{f['kind']}: {f['sha']} [{f['job']}] {f['message'][:60]} -> {', '.join(f['paths'][:3])}" for f in breaches + direct]
    if breaches:
        return _check("scope", "red", f"{len(breaches)} write(s) outside a job's lane", items)
    if direct:
        return _check("scope", "amber", f"{len(direct)} code change(s) pushed to main without a pull request", items)
    return _check("scope", "green", "every commit stayed in its lane")


def check_issues(issues: List[Dict], now: datetime) -> Dict:
    old = []
    for i in issues:
        try:
            age = (now - datetime.fromisoformat(i["created_at"].replace("Z", "+00:00"))).days
        except Exception:
            continue
        if age > ISSUE_MAX_AGE_DAYS and "🚨" in i.get("title", ""):
            old.append(f"#{i['number']} {i['title']} ({age} days)")
    if old:
        return _check("issues", "amber", f"{len(old)} failure issue(s) open more than {ISSUE_MAX_AGE_DAYS} days", old)
    return _check("issues", "green", "no stale failure issues")


# ── run ──────────────────────────────────────────────────────────────────────

def due_today(now: datetime) -> List[str]:
    """Once-a-day weekday workflows whose slot has passed today (with 30 minutes' grace)."""
    from dispatcher import TIMETABLE, _minutes
    local = now.astimezone(ET)
    mins = local.hour * 60 + local.minute - 30
    return [wf for wf, days, first, _last, every in TIMETABLE
            if local.weekday() in days and every is None and _minutes(first) <= mins]


def fetch_runs(now: datetime) -> List[Dict]:
    since = (now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    out = []
    for page in range(1, 6):
        data = _gh([f"repos/{REPO}/actions/runs?per_page=100&page={page}&created=>={since}"])
        rows = (data or {}).get("workflow_runs") or []
        out += [{"workflow": Path(r.get("path") or "").name, "conclusion": r.get("conclusion"),
                 "created_at": r.get("created_at")} for r in rows]
        if len(rows) < 100:
            break
    return out


def fetch_issues() -> List[Dict]:
    return [{"number": i["number"], "title": i["title"], "created_at": i["created_at"]}
            for i in _gh([f"repos/{REPO}/issues?state=open&per_page=100"]) or [] if "pull_request" not in i]


def run(now: Optional[datetime] = None, runs_fn=fetch_runs, issues_fn=fetch_issues,
        audit_fn=None, data_dir: Path = DOCS_DATA) -> Dict:
    now = now or datetime.now(timezone.utc)
    read = lambda name: contracts.read(data_dir / name, default=None)
    checks = []

    def guarded(name, fn):
        try:
            checks.append(fn())
        except Exception as e:
            checks.append(_check(name, "amber", f"check could not run: {str(e)[:160]}"))

    guarded("jobs", lambda: check_jobs(runs_fn(now), due_today(now)))
    guarded("freshness", lambda: check_freshness(now, lambda n: _safe(read, n)))
    guarded("model", lambda: check_model(_safe(read, "macro_brief.json"), _safe(read, "theses.json"),
                                         now.astimezone(ET).date()))
    guarded("contracts", lambda: check_contracts(contracts.check(data_dir)))
    if audit_fn is None:
        import scope_audit
        audit_fn = lambda: scope_audit.audit("2 days ago")
    guarded("scope", lambda: check_scope(audit_fn()))
    guarded("issues", lambda: check_issues(issues_fn(), now))

    status = max((c["status"] for c in checks), key=RANK.get, default="green")
    return {"generated_at": now.isoformat(timespec="seconds"), "status": status,
            "red": [c["name"] for c in checks if c["status"] == "red"],
            "amber": [c["name"] for c in checks if c["status"] == "amber"], "checks": checks}


def _safe(read, name):
    try:
        return read(name)
    except Exception:
        return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Is the machinery healthy?")
    ap.add_argument("--no-fail", action="store_true", help="exit 0 even when red")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    report = run()
    contracts.write(OUT_FILE, report)
    for c in report["checks"]:
        print(f"[{c['status'].upper():5}] {c['name']}: {c['detail']}")
        for i in c.get("items") or []:
            print(f"          - {i}")
    print(f"OVERALL: {report['status'].upper()}")
    return 1 if report["status"] == "red" and not args.no_fail else 0


if __name__ == "__main__":
    sys.exit(main())
