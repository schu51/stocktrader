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
from datetime import datetime, timedelta, timezone
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
    # Saturday chain — each step feeds the next
    ("diamond_screen.yml",     [SATURDAY], "07:30", "07:30", None),  # small/mid-cap inflection watchlist (no trades)
    ("candidate_outcomes.yml", [SATURDAY], "08:00", "08:00", None),  # evidence the learning agent checks
    ("experiments.yml",        [SATURDAY], "08:15", "08:15", None),  # promote / keep / drop technical trials
    ("learning.yml",           [SATURDAY], "08:30", "08:30", None),
    ("macro_research.yml",     [SATURDAY], "09:00", "09:00", None),
    ("weekly_review.yml",      [SATURDAY], "09:30", "09:30", None),  # summarizes all of the above
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


CATCHUP_SLOTS = 3    # a once-a-day job is still started up to 45 minutes after a missed trigger
REPEAT_CATCHUP_SLOTS = 1   # a repeating job only from the slot just before this one


def catch_up(now: datetime) -> list:
    """
    Workflows a missed trigger skipped, as (workflow, start of the slot it was
    due in), oldest first: once-a-day workflows from the last CATCHUP_SLOTS
    slots, repeating ones from the last REPEAT_CATCHUP_SLOTS.

    One trigger from cron-job.org can time out or get a 500 from GitHub.
    Without this, a failure at 10:00 would skip the whole day's trade run, and
    a failure on an exit-check slot would leave an hour between checks.
    Repeating jobs get the shorter window because they come round again on
    their own. Whether a job already ran is decided by dispatch(), which
    checks for a run since the job's own slot.
    """
    once_a_day = {workflow for workflow, _, _, _, every in TIMETABLE if every is None}
    local = now.astimezone(ET)
    current = set(due_workflows(now))
    missed, seen = [], set()
    for k in range(CATCHUP_SLOTS, 0, -1):
        earlier = local - timedelta(minutes=SLOT_MINUTES * k)
        if earlier.date() != local.date():
            continue
        for workflow in due_workflows(earlier):
            if workflow not in once_a_day and k > REPEAT_CATCHUP_SLOTS:
                continue
            if workflow not in current and workflow not in seen:
                seen.add(workflow)
                missed.append((workflow, slot_start(earlier)))
    return missed


def run_due(now: datetime, ref: str, already_started=already_started, start=start) -> int:
    """Start any workflow a missed trigger skipped, then this slot's workflows. Returns failures."""
    failed = 0
    for workflow, since in catch_up(now):
        failed += dispatch([workflow], since, ref, already_started=already_started, start=start)
    failed += dispatch(due_workflows(now), slot_start(now), ref, already_started=already_started, start=start)
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

    missed = catch_up(now)
    if missed:
        print("catch-up candidates (started only if they have not run): "
              + ", ".join(f"{wf} due {since.astimezone(ET):%H:%M}" for wf, since in missed))
    failed = run_due(now, args.ref) if args.run else 0
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
