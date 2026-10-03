"""
Workflow dispatcher — decides which workflows are due right now.

GitHub's own `schedule:` cron fires hours late and drops most slots, so an
external scheduler (cron-job.org) triggers dispatcher.yml every 15 minutes
and this module starts whichever workflows belong to that 15-minute slot.

All times are America/New_York, so the timetable follows the market across
daylight-saving changes without edits.

Usage:
    python dispatcher.py                       # print workflows due now
    python dispatcher.py --now 2026-10-05T14:00:11Z
    python dispatcher.py --run                 # also start them via `gh workflow run`
"""

import argparse
import subprocess
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
SLOT_MINUTES = 15

WEEKDAYS = range(0, 5)   # Mon–Fri
SATURDAY = 5


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


def parse_now(value: str) -> datetime:
    """Parse an ISO timestamp as GitHub prints it (e.g. 2026-10-05T14:00:11Z)."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def main() -> int:
    parser = argparse.ArgumentParser(description="Start the workflows due in this 15-minute slot")
    parser.add_argument("--now", help="ISO timestamp to evaluate instead of the current time")
    parser.add_argument("--run", action="store_true", help="start due workflows with `gh workflow run`")
    parser.add_argument("--ref", default="main", help="branch to run workflows on")
    args = parser.parse_args()

    now = parse_now(args.now) if args.now else datetime.now(timezone.utc)
    due = due_workflows(now)
    print(f"{now.astimezone(ET):%a %Y-%m-%d %H:%M:%S %Z} -> {', '.join(due) or 'nothing due'}")

    failed = 0
    if args.run:
        for workflow in due:
            result = subprocess.run(["gh", "workflow", "run", workflow, "--ref", args.ref])
            if result.returncode != 0:
                print(f"::error::failed to start {workflow}")
                failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
