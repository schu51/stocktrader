"""
Workflow dispatcher — decides which workflows are due right now.

GitHub's own `schedule:` cron fires hours late and drops most slots, so an
external scheduler (cron-job.org) triggers dispatcher.yml every 15 minutes
and this module starts whichever workflows belong to that 15-minute slot.

All times are America/New_York, so the timetable follows the market across
daylight-saving changes without edits.

Usage:
    python dispatcher.py                       # print workflows due now
    python dispatcher.py --now 2026-10-05T14:00:11Z   # inspect another time (never starts anything)
    python dispatcher.py --run                 # also start them via `gh workflow run`
    python dispatcher.py --selftest            # start one harmless workflow to prove dispatch works
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
SLOT_MINUTES = 15

WEEKDAYS = range(0, 5)   # Mon–Fri
SATURDAY = 5


# Started by --selftest to prove the dispatcher can start workflows.
# Must never be a workflow that places or changes orders.
SELFTEST_WORKFLOW = "learning.yml"


def _minutes(hhmm: str) -> int:
    hh, mm = hhmm.split(":")
    return int(hh) * 60 + int(mm)


# (workflow file, days, first slot, last slot, repeat every N minutes) — ET.
# Order matters: it is the order workflows are started within a slot.
TIMETABLE = [
    ("premarket.yml",      WEEKDAYS,   "09:00", "09:00", None),
    ("stop_placement.yml", WEEKDAYS,   "09:30", "09:30", None),  # at the open
    ("daily_trade.yml",    WEEKDAYS,   "10:00", "10:00", None),
    ("intraday_exit.yml",  WEEKDAYS,   "09:45", "15:45", 30),    # :15/:45, offset from sync
    ("postmarket.yml",     WEEKDAYS,   "16:15", "16:15", None),
    ("portfolio_sync.yml", WEEKDAYS,   "09:00", "18:30", 30),    # :00/:30
    ("learning.yml",       [SATURDAY], "08:00", "08:00", None),
    ("macro_research.yml", [SATURDAY], "08:30", "08:30", None),  # after learning
]


def due_workflows(now: datetime) -> list:
    """Workflow files due in the 15-minute slot containing `now` (tz-aware)."""
    local = now.astimezone(ET)
    slot = (local.hour * 60 + local.minute) // SLOT_MINUTES * SLOT_MINUTES

    due = []
    for workflow, days, first, last, every in TIMETABLE:
        if local.weekday() not in days:
            continue
        start, end = _minutes(first), _minutes(last)
        if not start <= slot <= end:
            continue
        if every and (slot - start) % every:
            continue
        due.append(workflow)
    return due


def slot_start(now: datetime) -> datetime:
    """Start of the 15-minute slot containing `now`, in UTC."""
    utc = now.astimezone(timezone.utc)
    return utc.replace(minute=utc.minute // SLOT_MINUTES * SLOT_MINUTES, second=0, microsecond=0)


def already_started(workflow: str, since: datetime) -> bool:
    """True if `workflow` already has a run created at or after `since`."""
    result = subprocess.run(
        ["gh", "run", "list", "--workflow", workflow, "--limit", "1", "--json", "databaseId",
         "--created", f">={since:%Y-%m-%dT%H:%M:%SZ}"],
        capture_output=True, text=True, check=True,
    )
    return bool(json.loads(result.stdout or "[]"))


def start(workflow: str, ref: str) -> bool:
    return subprocess.run(["gh", "workflow", "run", workflow, "--ref", ref]).returncode == 0


def dispatch(due: list, since: datetime, ref: str,
             already_started=already_started, start=start) -> int:
    """Start each due workflow at most once per slot. Returns the failure count.

    A second trigger in the same slot (cron-job.org retry, manual test run)
    must not start the trade job twice. If the check itself fails the workflow
    is skipped: a missed run is recoverable, a duplicate order is not.
    """
    failed = 0
    for workflow in due:
        try:
            if already_started(workflow, since):
                print(f"skip {workflow}: already started in this slot")
                continue
        except Exception as exc:
            print(f"::error::could not check {workflow} for an existing run, not starting it: {exc}")
            failed += 1
            continue
        if not start(workflow, ref):
            print(f"::error::failed to start {workflow}")
            failed += 1
    return failed


def parse_now(value: str) -> datetime:
    """Parse an ISO timestamp as GitHub prints it (e.g. 2026-10-05T14:00:11Z)."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def main() -> int:
    parser = argparse.ArgumentParser(description="Start the workflows due in this 15-minute slot")
    parser.add_argument("--now", help="ISO timestamp to evaluate instead of the current time")
    parser.add_argument("--at", help="trigger time of this run (set by dispatcher.yml from the GitHub API)")
    parser.add_argument("--run", action="store_true", help="start due workflows with `gh workflow run`")
    parser.add_argument("--ref", default="main", help="branch to run workflows on")
    parser.add_argument("--selftest", action="store_true",
                        help=f"start {SELFTEST_WORKFLOW} (no orders) through the normal dispatch path")
    args = parser.parse_args()

    if args.run and args.now:
        parser.error("--now is for inspection only and cannot be combined with --run")

    stamp = args.now or args.at
    now = parse_now(stamp) if stamp else datetime.now(timezone.utc)
    due = due_workflows(now)
    print(f"{now.astimezone(ET):%a %Y-%m-%d %H:%M:%S %Z} -> {', '.join(due) or 'nothing due'}")

    if args.selftest:
        print(f"self-test: starting {SELFTEST_WORKFLOW} only")
        return 1 if dispatch([SELFTEST_WORKFLOW], slot_start(now), args.ref) else 0

    failed = dispatch(due, slot_start(now), args.ref) if args.run else 0
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
