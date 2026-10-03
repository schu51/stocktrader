"""
Candidate Outcomes
==================
Learns from every candidate the screener scored, not only the trades taken.

Each daily screener snapshot (docs/data/screener.json, one per trading day in
git history) lists ~12 candidates with rs_rank and thesis_score. This module
attaches what each candidate did next — forward return over 5/10/20 sessions,
net of SPY — and measures whether the scores predicted it.

No lookahead: a snapshot is generated during or after its trading day, so the
simulated entry is the NEXT session's open.

Usage:
    python candidate_outcomes.py            # build research/candidate_outcomes.csv and print the report

Needs full git history (the snapshots are read from past commits).
"""

import json
import math
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

ROOT = Path(__file__).parent.resolve()
SNAPSHOT_PATH = "docs/data/screener.json"
OUT_FILE = ROOT / "research" / "candidate_outcomes.csv"
REPORT_FILE = ROOT / "docs" / "data" / "candidate_outcomes.json"

HORIZONS = (5, 10, 20)        # trading sessions held
BENCHMARK = "SPY"
SECTOR_LEADER_BOOST = 1.05    # same boost the screener applies
MIN_NAMES_PER_DAY = 5


# ---------------------------------------------------------------------------
# Pure functions
# ---------------------------------------------------------------------------

def forward_return(opens: pd.Series, closes: pd.Series,
                   snapshot_date: date, horizon: int) -> Optional[float]:
    """
    Return from the first session's open AFTER snapshot_date to the close
    `horizon` sessions later (horizon=1 is that same session's close).
    None if the window has not completed yet.
    """
    later = opens.index[opens.index.date > snapshot_date]
    if len(later) == 0:
        return None
    entry_pos = opens.index.get_loc(later[0])
    exit_pos = entry_pos + horizon - 1
    if exit_pos >= len(closes):
        return None
    entry, exit_ = opens.iloc[entry_pos], closes.iloc[exit_pos]
    if pd.isna(entry) or pd.isna(exit_) or entry <= 0:
        return None
    return float(exit_ / entry - 1)


def daily_ic(df: pd.DataFrame, score_col: str, ret_col: str,
             min_names: int = MIN_NAMES_PER_DAY) -> pd.Series:
    """Spearman rank correlation between a score and forward return, per day."""
    out = {}
    for day, g in df.dropna(subset=[score_col, ret_col]).groupby("date"):
        if len(g) < min_names or g[score_col].nunique() < 2 or g[ret_col].nunique() < 2:
            continue
        out[day] = g[score_col].rank().corr(g[ret_col].rank())
    return pd.Series(out, dtype=float)


def top_k_edge(df: pd.DataFrame, w_rs: float, k: int, ret_col: str,
               min_names: int = MIN_NAMES_PER_DAY) -> pd.Series:
    """
    Per day: mean return of the k candidates ranked highest by
    (w_rs * rs_rank + (1 - w_rs) * thesis_score) * sector boost,
    minus the mean return of all that day's candidates.
    """
    out = {}
    for day, g in df.dropna(subset=["rs_rank", "thesis_score", ret_col]).groupby("date"):
        if len(g) < max(min_names, k + 1):
            continue
        boost = g["sector_leader"].fillna(False).astype(bool).map({True: SECTOR_LEADER_BOOST, False: 1.0})
        composite = (w_rs * g["rs_rank"] + (1 - w_rs) * g["thesis_score"]) * boost
        picks = composite.sort_values(ascending=False, kind="stable").index[:k]
        out[day] = g.loc[picks, ret_col].mean() - g[ret_col].mean()
    return pd.Series(out, dtype=float)


def summarize(series: pd.Series, horizon: int) -> Dict:
    """
    Mean and t-statistic of a daily series. Forward windows of `horizon`
    sessions overlap, so consecutive days are not independent: the effective
    sample is n / horizon, which shrinks t by sqrt(horizon).
    """
    s = series.dropna()
    n = len(s)
    if n < 2:
        return {"n": n, "mean": float("nan"), "t": float("nan")}
    sd = s.std(ddof=1)
    n_eff = max(n / horizon, 1.0)
    t = float(s.mean() / (sd / math.sqrt(n_eff))) if sd > 0 else float("nan")
    return {"n": n, "mean": float(s.mean()), "t": t}


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------

def load_snapshots() -> pd.DataFrame:
    """One row per (day, candidate) from the last screener snapshot committed each day."""
    log = subprocess.check_output(
        ["git", "log", "--format=%H %ad", "--date=format:%Y-%m-%d", "--", SNAPSHOT_PATH],
        cwd=ROOT, text=True).splitlines()
    last_commit = {}
    for line in log:                       # newest first: first seen is the day's last commit
        sha, day = line.split()
        last_commit.setdefault(day, sha)

    rows: List[Dict] = []
    for day, sha in sorted(last_commit.items()):
        try:
            snap = json.loads(subprocess.check_output(
                ["git", "show", f"{sha}:{SNAPSHOT_PATH}"], cwd=ROOT, text=True))
        except Exception:
            continue
        for c in snap.get("candidates") or []:
            rows.append({
                "date": day,
                "symbol": c.get("symbol"),
                "rs_rank": c.get("rs_rank"),
                "thesis_score": c.get("thesis_score"),
                "sector": c.get("sector"),
                "sector_leader": c.get("sector_leader"),
                "snapshot_price": c.get("price"),
            })
    return pd.DataFrame(rows)


def fetch_prices(symbols: List[str], start: str):
    """Daily opens and closes (split/dividend adjusted) for symbols + benchmark."""
    import yfinance as yf
    raw = yf.download(sorted(set(symbols) | {BENCHMARK}), start=start, auto_adjust=True,
                      progress=False, threads=True)
    return raw["Open"], raw["Close"]


def build(df: pd.DataFrame, opens: pd.DataFrame, closes: pd.DataFrame) -> pd.DataFrame:
    """Attach forward and benchmark-relative returns for every horizon."""
    df = df.copy()
    for h in HORIZONS:
        raw, excess = [], []
        for day, sym in zip(df["date"], df["symbol"]):
            snap = date.fromisoformat(day)
            r = b = None
            if sym in closes.columns:
                r = forward_return(opens[sym].dropna(), closes[sym].dropna(), snap, h)
            b = forward_return(opens[BENCHMARK].dropna(), closes[BENCHMARK].dropna(), snap, h)
            raw.append(r)
            excess.append(r - b if r is not None and b is not None else None)
        df[f"ret_{h}d"] = raw
        df[f"excess_{h}d"] = excess
    return df


WEIGHT_GRID = (1.0, 0.8, 0.6, 0.4, 0.2, 0.1, 0.0)   # w_rs; w_thesis = 1 - w_rs


def summary(df: pd.DataFrame, k: int = 3) -> Dict:
    """Everything the report prints, as plain data (also written to JSON for the record)."""
    scored = df.dropna(subset=["rs_rank", "thesis_score"])
    out = {
        "candidate_days": int(len(df)), "scored": int(len(scored)),
        "days": int(df["date"].nunique()), "symbols": int(df["symbol"].nunique()),
        "first_day": df["date"].min(), "last_day": df["date"].max(), "top_k": k,
        "ic": [], "weights": [], "paired_10_90_vs_60_40": [], "average_candidate": [],
    }
    for h in HORIZONS:
        col = f"excess_{h}d"
        for score in ("rs_rank", "thesis_score"):
            ic = daily_ic(scored, score, col)
            out["ic"].append({"horizon": h, "score": score, **summarize(ic, h),
                              "share_days_positive": float((ic > 0).mean()) if len(ic) else None})
        for w in WEIGHT_GRID:
            out["weights"].append({"horizon": h, "w_rs": w, "w_thesis": round(1 - w, 2),
                                   **summarize(top_k_edge(scored, w, k, col), h)})
        diff = (top_k_edge(scored, 0.1, k, col) - top_k_edge(scored, 0.6, k, col)).dropna()
        out["paired_10_90_vs_60_40"].append({
            "horizon": h, **summarize(diff, h),
            "share_days_ahead": float((diff > 0).mean()) if len(diff) else None,
            "share_days_tied": float((diff == 0).mean()) if len(diff) else None})
        per_day = df.dropna(subset=[col]).groupby("date")[col].mean()
        out["average_candidate"].append({"horizon": h, **summarize(per_day, h),
                                         "candidate_days": int(df[col].notna().sum())})
    return out


def report(s: Dict) -> None:
    print(f"\nCandidate-days: {s['candidate_days']}  |  with both scores: {s['scored']}  |  "
          f"days: {s['days']}  |  symbols: {s['symbols']}  |  {s['first_day']} to {s['last_day']}")

    print("\n1) Does each score predict what a candidate does next?  (daily rank correlation with return vs SPY)")
    print(f"   {'horizon':8} {'score':13} {'days':>5} {'mean IC':>8} {'t (overlap-adj)':>16} {'% days > 0':>11}")
    for r in s["ic"]:
        print(f"   {r['horizon']:>2}d      {r['score']:13} {r['n']:>5} {r['mean']:>+8.3f} {r['t']:>+16.2f} "
              f"{r['share_days_positive']:>10.0%}")

    print(f"\n2) Which weighting picks better names?  (top {s['top_k']} by composite, minus the day's candidate average, vs SPY)")
    print(f"   {'w_rs/w_thesis':14}" + "".join(f"{str(h) + 'd edge':>11}{'t':>7}" for h in HORIZONS))
    for w in WEIGHT_GRID:
        tag = "  <- old 60/40" if w == 0.6 else "  <- live 10/90" if w == 0.1 else ""
        line = f"   {w:.1f} / {1 - w:.1f}     "
        for h in HORIZONS:
            r = next(x for x in s["weights"] if x["horizon"] == h and x["w_rs"] == w)
            line += f"{r['mean']:>+10.2%}{r['t']:>+7.2f}"
        print(line + tag)

    print("\n3) Live 10/90 minus old 60/40, same days  (paired)")
    for r in s["paired_10_90_vs_60_40"]:
        print(f"   {r['horizon']:>2}d: mean difference {r['mean']:+.2%} per pick-day over {r['n']} days, "
              f"t = {r['t']:+.2f}, 10/90 ahead on {r['share_days_ahead']:.0%} of days (tied {r['share_days_tied']:.0%})")

    print("\n4) Context: average candidate vs SPY")
    for r in s["average_candidate"]:
        print(f"   {r['horizon']:>2}d: {r['mean']:+.2%} (t = {r['t']:+.2f}, {r['n']} days, "
              f"{r['candidate_days']} candidate-days)")


def main() -> int:
    df = load_snapshots()
    if df.empty:
        print("No screener snapshots found in git history")
        return 1
    start = (pd.Timestamp(df["date"].min()) - pd.Timedelta(days=5)).strftime("%Y-%m-%d")
    opens, closes = fetch_prices(df["symbol"].dropna().unique().tolist(), start)
    out = build(df, opens, closes)
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT_FILE, index=False)
    print(f"Wrote {len(out)} rows to {OUT_FILE.relative_to(ROOT)}")

    result = {"generated_at": datetime.now().isoformat(), **summary(out)}
    REPORT_FILE.write_text(json.dumps(result, indent=2))
    report(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
