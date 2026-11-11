"""
Scope audit
===========
Checks that every commit to main stayed in its lane: each job may only write
its own files, and code reaches main only through a merged pull request.
Built after a June scheduled task was found pushing straight to main and, on
Nov 9, overwriting the reconcile job's postmarket.json.

A commit is matched to its job by its message (every job commits with a fixed
message) and its changed paths are checked against that job's allowed paths.

  breach    a job wrote outside its paths, or an unknown writer changed shared data
  direct    code changed on main outside a merged pull request

    python scope_audit.py --since "2 days ago"
"""

import argparse
import fnmatch
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).parent.resolve()
D = "docs/data/"

# (job, commit-message pattern, allowed path globs[, needs "[skip ci]"])
# Model answers are pushed WITHOUT [skip ci] on purpose: the push starts apply_replies.yml.
JOBS: List[Tuple] = [
    ("model_requests", r"^Model requests", ("research/requests/*",)),
    ("model_replies", r"^Model replies", ("research/replies/*",), False),
    ("portfolio_sync", r"^Portfolio sync", (D + "latest.json",)),
    ("intraday_exit", r"^Intraday exit check", (D + "intraday_exit.json", D + "sma50_cache.json", D + "trades.json", D + "decisions/*")),
    ("daily_run", r"^Dashboard \+ screener data update", (D + "*",)),
    ("stop_placement", r"^Stop placement", (D + "stop_placement.json", D + "decisions/*")),
    ("premarket", r"^Premarket brief", (D + "premarket.json", D + "sma50_cache.json")),
    ("postmarket", r"^Post-market reconciliation", (D + "postmarket.json",)),
    ("diamond_sleeve", r"^Diamond sleeve", (D + "diamond_sleeve.json", D + "trades.json", D + "decisions/*")),
    ("osint", r"^OSINT (on-demand )?brief", (D + "osint/*", D + "osint_*", D + "osint_brief.md")),
    ("macro_research", r"^Macro research update", (D + "theses.json", D + "macro_brief.json")),
    ("learning", r"^Learning agent update", (D + "weights.json", D + "learning_report.json")),
    ("universe", r"^Refresh screener universe", (D + "universe.json", D + "securities.json")),
    ("experiments", r"^Experiments update", (D + "experiments.json",)),
    ("candidate_outcomes", r"^Candidate outcomes update", (D + "candidate_outcomes.json", "research/candidate_outcomes.csv")),
    ("diamond_screen", r"^Diamond (watchlist update|fast test)", (D + "diamond*", "research/fast_test/*", "research/diamond*")),
    ("daily_eval", r"^Daily eval", (D + "eval_*", D + "eval_report.md")),
    ("eval_task", r"^Eval journal", ("research/challengers.json", "research/eval_journal.md")),
    ("big_movers", r"^Big movers update", (D + "big_movers*",)),
    ("big_mover_reads", r"^Big mover reads", ("research/big_mover_reads.json",)),
    ("options", r"^Options sentiment update", (D + "options_sentiment.json", D + "options/*")),
    ("overnight", r"^Overnight (shadow|split)", (D + "overnight*", "research/overnight*")),
    ("puts", r"^Put (shadow|hedge study)", (D + "put_*", "research/put_*")),
    ("graph", r"^Knowledge graph update", (D + "graph.json",)),
    ("ops_monitor", r"^Ops monitor", (D + "ops_status.json", D + "trade_attribution.json")),
    ("weekly_review", r"^Weekly review data", (D + "weekly_review_*",)),
]


def classify(message: str, paths: List[str], parents: int) -> Optional[Dict]:
    """A finding for one commit, or None if it is in scope."""
    if parents > 1 or message.startswith("Merge pull request #"):
        return None                                   # reviewed: arrived through a pull request
    for job, pattern, allowed, *opt in JOBS:
        needs_skip = opt[0] if opt else True             # job commits carry [skip ci], except model answers
        if re.search(pattern, message) and ("[skip ci]" in message or not needs_skip):
            outside = [p for p in paths if not any(fnmatch.fnmatch(p, g) for g in allowed)]
            if outside:
                return {"kind": "breach", "job": job, "paths": outside[:10]}
            return None
    shared = [p for p in paths if p.startswith(D)]
    if shared:
        return {"kind": "breach", "job": "unknown writer", "paths": shared[:10]}
    return {"kind": "direct", "job": "direct push", "paths": paths[:10]}


def commits(since: str, ref: str = "HEAD", repo: Path = ROOT) -> List[Dict]:
    # --first-parent: what landed on main itself; a PR's own commits sit behind its merge
    out = subprocess.run(["git", "-C", str(repo), "log", "--first-parent", ref, f"--since={since}",
                          "--format=@@%H|%P|%cI|%s", "--name-only", "-m"],
                         capture_output=True, text=True, check=True).stdout
    rows, cur = [], None
    for line in out.splitlines():
        if line.startswith("@@"):
            sha, parents, when, msg = line[2:].split("|", 3)
            cur = {"sha": sha[:8], "parents": len(parents.split()), "at": when, "message": msg, "paths": []}
            rows.append(cur)
        elif line.strip() and cur is not None:
            cur["paths"].append(line.strip())
    return rows


def audit(since: str = "2 days ago", repo: Path = ROOT, ref: str = "HEAD") -> List[Dict]:
    findings = []
    for c in commits(since, ref, repo):
        f = classify(c["message"], c["paths"], c["parents"])
        if f:
            findings.append(dict(f, sha=c["sha"], at=c["at"], message=c["message"][:100]))
    return findings


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Did every commit to main stay in its lane?")
    ap.add_argument("--since", default="2 days ago")
    args = ap.parse_args(argv)
    findings = audit(args.since)
    for f in findings:
        print(f"{f['kind'].upper()} {f['sha']} {f['at']} [{f['job']}] {f['message']} -> {f['paths']}")
    print(f"{sum(f['kind'] == 'breach' for f in findings)} breach(es), {sum(f['kind'] == 'direct' for f in findings)} direct push(es)")
    return 1 if any(f["kind"] == "breach" for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
