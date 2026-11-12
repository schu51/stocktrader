"""
Data contracts
==============
Every file the bot shares between jobs (docs/data) has a contract here: the
fields its readers rely on, their types, and which may be null. A job reads
and writes those files through this module, so a renamed or missing field is
caught where it happens instead of turning into a silent default downstream.

    import contracts
    data = contracts.read(path)             # parse + validate; ContractError if the shape is wrong
    data = contracts.read(path, default=[]) # a MISSING file gives the default; a bad one still raises
    contracts.write(path, data)             # validate, then write atomically

The contract is found from the file's name (screener.json, options/2026-11.json),
so a test that points a job at a temporary path is checked the same way.

Two levels:
  full     what the writer promises: every required field, right types. Checked
           by write(), by `python contracts.py --check` every evening and by the
           test suite on every committed file. A renamed field fails here.
  reader   what a reader needs: the right structure and types. A wrong type or a
           list where a dict should be raises at read time; a missing field is
           left to the reader's own default (the full check reports it).

What happens on a bad shape:
  read   ContractError, a ValueError like a JSON syntax error, so every existing
         "could not read X" fallback handles it exactly as it handles a corrupt file.
  write  The data is written anyway (a trade record is never dropped), the breach
         is appended to docs/data/contract_violations.json, and the error is logged.
         `python contracts.py --check` fails while any breach is unacknowledged or
         any shared file does not match its contract; daily_eval.yml runs it every
         evening, so a breach opens an issue the same day.

Contracts accept extra fields (adding is safe) but not missing required fields
or wrong types (renaming and retyping are not). Each one was checked against
the last 60 committed versions of its file.

Tests: tests/test_contracts.py validates every committed file and keeps the
number of raw json.load calls from growing (it may only fall).
"""

import argparse
import fnmatch
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Type, Union

from pydantic import BaseModel, ConfigDict, RootModel, ValidationError

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.resolve()
DOCS_DATA = ROOT / "docs" / "data"
VIOLATIONS_FILE = DOCS_DATA / "contract_violations.json"
VIOLATIONS_KEEP = 200

_MISSING = object()


class ContractError(ValueError):
    """A shared data file does not match its contract."""


class Open(BaseModel):
    """Base: extra fields allowed, required ones enforced."""
    model_config = ConfigDict(extra="allow")


Num = float          # pydantic accepts an int where a float is declared
Opt = Optional


# ---------------------------------------------------------------------------
# Trading path (strict: these feed orders)
# ---------------------------------------------------------------------------

class MarketRegime(Open):
    regime: str
    rs_threshold: int


class Candidate(Open):
    symbol: str
    rs_rank: int
    rs_score: Num
    thesis_score: Num
    thesis_grade: str
    setup_reason: str
    effective_score: Num
    macro_mult: Num
    sector: str
    sector_leader: bool
    trend: str
    price: Num
    sma50: Num
    sma200: Num
    ret_1m: Opt[Num] = None
    ret_3m: Opt[Num] = None
    ret_6m: Opt[Num] = None
    source: str
    priority: int


class Screener(Open):
    generated_at: str
    market_regime: MarketRegime
    universe_size: int
    rs_qualifying: int
    quality_pass: int
    tech_gate_pass: int
    final_count: int
    sector_leaders: List[str]
    rejection_stats: Dict[str, int]
    candidates: List[Candidate]
    rs_ranks: Dict[str, int] = {}
    data_coverage: Opt[Num] = None
    price_source: Opt[str] = None
    catalyst_watch: List[Dict[str, Any]] = []


class Position(Open):
    symbol: str
    qty: Num
    avg_cost: Num
    current_price: Num
    market_value: Num
    unrealized_pnl: Num
    unrealized_pnl_pct: Num
    stop_loss: Opt[Num] = None


class Account(Open):
    portfolio_value: Num
    cash: Num
    buying_power: Num


class Latest(Open):
    generated_at: str
    run_id: str
    model_status: str
    account: Account
    positions: List[Position]
    todays_run: Dict[str, Any]
    exits: Dict[str, Any]
    risk_assessment: Dict[str, Any]
    persistent_performers: List[Any]


class Trade(Open):
    trade_id: str
    symbol: str
    status: str
    entry_date: str
    entry_ts: str
    entry_price: Num
    shares: Num
    stop_loss: Opt[Num] = None
    exit_date: Opt[str] = None
    exit_price: Opt[Num] = None
    exit_reason: Opt[str] = None
    pnl_usd: Opt[Num] = None
    pnl_pct: Opt[Num] = None
    hold_days: Opt[int] = None


class Trades(RootModel[List[Trade]]):
    pass


class HistoryRow(Open):
    date: str
    run_id: str
    mode: str
    candidates_screened: int
    buy_signals: int
    orders_submitted: int
    portfolio_value: Num
    status: str


class History(RootModel[List[HistoryRow]]):
    pass


class Experiment(Open):
    kind: str
    state: str
    started: str
    params: Dict[str, Any] = {}
    note: Opt[str] = None


class Experiments(Open):
    experiments: Dict[str, Experiment]
    changes: List[Dict[str, Any]] = []


class SignalRow(Open):
    date: str
    symbol: str
    price: Num
    signals: Opt[Dict[str, Any]] = None
    blocked_by: Opt[str] = None


class SignalLog(RootModel[List[SignalRow]]):
    pass


class Thesis(Open):
    id: str
    theme: str
    causal_chain: str
    beneficiary_sectors: List[str]
    consensus_names_excluded: List[str]
    conviction: Num
    invalidation_condition: str
    horizon: str
    status: str
    created_at: str
    last_validated: str
    sources: List[str]


class Theses(Open):
    generated_at: Opt[str] = None
    theses: List[Thesis]


class Weights(Open):
    version: int
    w_rs: Num
    w_thesis: Num
    state: str


class WeightsFile(Open):
    active: Weights
    champion: Dict[str, Any]
    history: List[Dict[str, Any]]
    rejected: List[Dict[str, Any]]


class Universe(Open):
    generated_at: str
    count: int
    sources: Dict[str, Any]
    nasdaq100: List[str]
    tickers: Dict[str, str]


class RotationRow(Open):
    date: str
    symbol: str
    reason: str


class RotationLog(RootModel[List[RotationRow]]):
    pass


class StopPlacement(Open):
    generated_at: str
    positions_checked: int
    already_protected: int
    stops_placed: int
    stops_failed: int
    results: List[Dict[str, Any]]


class IntradayExit(Open):
    generated_at: str
    positions_checked: int
    exits_triggered: List[Any]
    stops_updated: List[Any]
    stops_placed: List[Any]
    stop_failures: List[Any]
    broker_exits: List[Any]
    mode: str


class Postmarket(Open):
    """The 16:15 reconcile's output (postmarket_reconciler.py) — no other writer."""
    date: str
    generated_at: str
    portfolio: Dict[str, Any]
    execution: Dict[str, Any]
    exits: Dict[str, Any]
    closed_today: List[Any]
    positions_at_risk: List[Any]


class Premarket(Open):
    generated_at: str
    recommendation: str
    trend_buckets: Dict[str, int]
    gap_alerts: List[Any]
    avoids: List[Any]
    gap_up_priority: List[str]
    top_candidates: List[Any]


# ---------------------------------------------------------------------------
# Research and reporting (shape: required top-level fields)
# ---------------------------------------------------------------------------

def _doc(name: str, **fields) -> Type[BaseModel]:
    """A model requiring the given top-level fields (any type unless given)."""
    from pydantic import create_model
    spec = {k: ((v, ...) if v is not None else (Any, ...)) for k, v in fields.items()}
    return create_model(name, __base__=Open, **spec)


def _rows(name: str, *required: str) -> Type[BaseModel]:
    row = _doc(name + "Row", **{k: None for k in required})
    return RootModel[List[row]]


class Keyed(RootModel[Dict[str, Dict[str, Any]]]):
    """{SYMBOL: {...}}"""


class AnyDict(RootModel[Dict[str, Any]]):
    pass


class AnyList(RootModel[List[Any]]):
    pass


def _req(*keys):
    return {k: None for k in keys}


Securities = _doc("Securities", generated_at=str, securities=Dict[str, Dict[str, Any]])
Violations = _rows("Violation", "at", "file", "errors")


# Contract per file name pattern, matched in order (relative to docs/data).
CONTRACTS: List[tuple] = [
    ("screener.json", Screener),
    ("latest.json", Latest),
    ("trades.json", Trades),
    ("history.json", History),
    ("experiments.json", Experiments),
    ("signal_log.json", SignalLog),
    ("theses.json", Theses),
    ("weights.json", WeightsFile),
    ("universe.json", Universe),
    ("rotation_log.json", RotationLog),
    ("stop_placement.json", StopPlacement),
    ("intraday_exit.json", IntradayExit),
    ("postmarket.json", Postmarket),
    ("premarket.json", Premarket),
    ("securities.json", Securities),
    ("contract_violations.json", Violations),
    ("big_movers.json", _doc("BigMovers", **_req("date", "generated_at", "today", "watch", "watch_record", "lessons"))),
    ("big_movers_cache.json", _doc("BigMoversCache", earnings=Dict[str, Any])),
    ("big_movers_log.json", _doc("BigMoversLog", events=List[Dict[str, Any]])),
    ("candidate_outcomes.json", _doc("CandidateOutcomes", **_req("generated_at", "ic", "weights", "days"))),
    ("companies.json", Keyed),
    ("diamond_history.json", _rows("DiamondHistory", "date", "rank", "ticker", "total")),
    ("diamond_judgments.json", Keyed),
    ("diamond_sleeve.json", _doc("DiamondSleeve", **_req("generated_at", "mode", "holdings", "buys", "sells"))),
    ("diamond_track.json", _doc("DiamondTrack", **_req("generated_at", "picks", "rows", "verdict"))),
    ("diamonds.json", _doc("Diamonds", **_req("generated_at", "watchlist"))),
    ("eval_history.json", _rows("EvalHistory", "date", "champion", "challengers", "live_last20", "tracking")),
    ("eval_latest.json", _doc("EvalLatest", **_req("date", "generated_at", "live", "tracking", "tournament", "alerts"))),
    ("intraday.json", _doc("Intraday", **_req("date", "points"))),
    ("learning_report.json", _doc("LearningReport", **_req("generated_at", "status", "trades_so_far"))),
    ("macro_brief.json", _doc("MacroBrief", **_req("generated_at", "status"))),
    ("osint_cache.json", AnyDict),
    ("osint_latest.json", _doc("OsintLatest", **_req("date", "generated_at", "companies", "summary"))),
    ("osint_log.json", _rows("OsintLog", "date", "symbol", "osint_score")),
    ("osint_watchlist.json", _doc("OsintWatchlist", tickers=None)),
    ("osint/ondemand/*.json", AnyDict),
    ("osint/*.json", _doc("OsintArchive", **_req("date", "companies"))),
    ("options_sentiment.json", _doc("OptionsSentiment", **_req("date", "generated_at", "reads", "evidence"))),
    ("options/*.json", _rows("OptionsLog", "date", "symbol", "group")),
    ("overnight_shadow.json", _doc("OvernightShadow", **_req("generated_at", "pending", "summary"))),
    ("put_shadow.json", AnyDict),
    ("sentiment_log.json", _rows("SentimentLog", "date", "symbol", "news_score", "st_bull_ratio")),
    ("sma50_cache.json", _doc("Sma50Cache", **_req("date", "values"))),
    ("themes.json", _doc("Themes", themes=Dict[str, Dict[str, Any]])),
    ("weekly_review_*.json", _doc("WeeklyReview", **_req("date", "generated_at", "type"))),
    ("requests/*.json", _doc("ModelRequests", date=str, answer_to=str)),
    ("replies/*.json", _doc("ModelReplies", date=str, replies=Dict[str, Any])),
    ("graph.json", _doc("Graph", generated_at=str, nodes=Dict[str, Dict[str, Any]], edges=List[Dict[str, Any]])),
    ("ops_status.json", _doc("OpsStatus", generated_at=str, status=str, checks=List[Dict[str, Any]])),
    ("trade_attribution.json", _doc("TradeAttribution", generated_at=str, findings=List[str], summary=Dict[str, Any], trades=List[Dict[str, Any]])),
    ("decisions/*/*.json", _rows("Decision", "at", "job", "kind", "symbol", "rule_version", "inputs", "output")),
    ("agents/*/*.json", AnyDict),
]


def contract_for(path: Union[str, Path]) -> Optional[Type[BaseModel]]:
    """The contract for a shared file, by its name under docs/data (or its basename)."""
    p = Path(path)
    try:
        rel = p.resolve().relative_to(DOCS_DATA).as_posix()
    except ValueError:
        # outside docs/data (a test's tmp path): match by the trailing parts
        rel = "/".join(p.parts[-3:])
    # Deeper patterns first: decisions/<date>/diamond_sleeve.json is a decision
    # log, not the sleeve's result file, though its name matches that too
    # (2026-11-12, the sleeve's first decision log).
    for pattern, model in sorted(CONTRACTS, key=lambda c: -c[0].count("/")):
        if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(rel, "*/" + pattern) or fnmatch.fnmatch(p.name, pattern):
            return model
    return None


def validate(path: Union[str, Path], data: Any, full: bool = True) -> List[str]:
    """
    Problems with `data` as the contents of `path` ([] = matches, or no contract).
    full=True  the whole contract: what the writer promises (nightly check, CI, write()).
    full=False what a reader needs: the right structure and types; a missing field
               is the reader's own default to handle, so only full checks catch it.
    """
    model = contract_for(path)
    if model is None:
        return []
    try:
        model.model_validate(data)
        return []
    except ValidationError as e:
        errs = [err for err in e.errors() if full or err["type"] != "missing"]
        return [f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in errs[:20]]


def read(path: Union[str, Path], default: Any = _MISSING) -> Any:
    """Parse and validate a shared file. Missing + default given -> default."""
    path = Path(path)
    if not path.exists() and default is not _MISSING:
        return default
    data = json.loads(path.read_text())
    problems = validate(path, data, full=False)
    if problems:
        raise ContractError(f"{path.name} does not match its contract: " + "; ".join(problems[:5]))
    return data


def _record(path: Path, problems: List[str]) -> None:
    try:
        log = json.loads(VIOLATIONS_FILE.read_text()) if VIOLATIONS_FILE.exists() else []
    except Exception:
        log = []
    log.append({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "file": path.name,
                "job": os.getenv("GITHUB_WORKFLOW", ""), "errors": problems[:10]})
    VIOLATIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    VIOLATIONS_FILE.write_text(json.dumps(log[-VIOLATIONS_KEEP:], indent=1))


def write(path: Union[str, Path], data: Any, indent: Optional[int] = 2, compact: bool = False) -> List[str]:
    """
    Validate, then write atomically. The data is written even when it breaks the
    contract (never lose a record); the breach is logged and recorded.
    Returns the problems found ([] = clean).
    """
    path = Path(path)
    problems = validate(path, data)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    text = json.dumps(data, separators=(",", ":"), default=str) if compact else json.dumps(data, indent=indent, default=str)
    tmp.write_text(text)
    os.replace(tmp, path)
    if problems:
        logger.error(f"CONTRACT BREACH writing {path.name}: {problems[:3]}")
        try:
            _record(path, problems)
        except Exception as e:
            logger.error(f"Could not record the contract breach: {e}")
    return problems


def check(data_dir: Path = DOCS_DATA) -> Dict[str, List[str]]:
    """Every shared file: problems by file. A file with no contract is a problem too."""
    out = {}
    for f in sorted(data_dir.rglob("*.json")):
        rel = f.relative_to(data_dir).as_posix()
        if contract_for(f) is None:
            out[rel] = ["no contract: add one to contracts.CONTRACTS"]
            continue
        try:
            data = json.loads(f.read_text())
        except Exception as e:
            out[rel] = [f"not valid JSON: {e}"]
            continue
        problems = validate(f, data)
        if problems:
            out[rel] = problems
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Validate every shared data file against its contract")
    ap.add_argument("--check", action="store_true", help="exit 1 on any breach (files or the violations log)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    problems = check()
    for f, ps in problems.items():
        for p in ps:
            print(f"{f}: {p}")
    recent = []
    try:
        recent = json.loads(VIOLATIONS_FILE.read_text()) if VIOLATIONS_FILE.exists() else []
        today = datetime.now(timezone.utc).date().isoformat()
        recent = [v for v in recent if v.get("at", "")[:10] == today]
    except Exception:
        pass
    for v in recent:
        print(f"breach today in {v.get('job') or 'a job'} writing {v['file']}: {v['errors'][:3]}")
    print(f"{len(problems)} file(s) off contract, {len(recent)} breach(es) recorded today")
    return 1 if args.check and (problems or recent) else 0


if __name__ == "__main__":
    sys.exit(main())
