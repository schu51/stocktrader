"""
Diamond fast test: did the AI's scores, made from filings public on each
list's date, line up with what the shares did afterwards?

    python research/fast_test/evaluate.py

Reads the judged lists in this folder (written by the Diamond Fast Test
workflow) and prices from Yahoo. Prints, for each list and pooled:
  - the hindsight probe: does the model already know what happened?
  - thirds by conviction, by the judgment alone, and by the numbers alone
  - the top 8 under each ranking against the small-cap index
The rule set before the run: build the sleeve if the top third by conviction
beat both the bottom third and the index.
"""
import glob
import json
import sys
import warnings
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")
HERE = Path(__file__).parent


def load():
    rows = []
    for path in sorted(glob.glob(str(HERE / "list_*.json"))):
        doc = json.loads(Path(path).read_text())
        if not doc.get("judged_as_of"):
            print(f"{Path(path).name}: not judged yet"); continue
        cutoff = date.fromisoformat(doc["cutoff"])
        tickers = [r["ticker"] for r in doc["watchlist"]]
        px = yf.download(sorted(set(tickers + ["IWM"])), start=cutoff.isoformat(),
                         end=(date.today() + timedelta(days=1)).isoformat(), auto_adjust=True, progress=False, threads=True)["Close"]
        def ret(t):
            s = px[t].dropna() if t in px.columns else pd.Series(dtype=float)
            return float(s.iloc[-1] / s.iloc[0] - 1) if len(s) > 20 else None
        index = ret("IWM")
        for r in doc["watchlist"]:
            j = r.get("judgment") or {}
            rows.append({"list": doc["cutoff"], "ticker": r["ticker"], "ret": ret(r["ticker"]), "index": index,
                         "numbers": (r.get("score") or {}).get("total"), "conviction": r.get("conviction"),
                         "edge": j.get("edge"), "demand": j.get("demand"), "n_flags": len(j.get("red_flags") or []),
                         "confidence": j.get("confidence"), "probe": r.get("hindsight_probe"),
                         "value": r.get("market_value"), "model": doc.get("model")})
    return pd.DataFrame(rows)


def thirds(d, key):
    d = d.dropna(subset=[key, "ret"]).sort_values(key, ascending=False)
    n = len(d) // 3
    out = []
    for name, g in (("top third", d.iloc[:n]), ("middle", d.iloc[n:len(d) - n]), ("bottom third", d.iloc[len(d) - n:])):
        out.append(f"{name:13} n={len(g):2}  avg {g.ret.mean():+6.1%}  median {g.ret.median():+6.1%}  beat index {(g.ret > g['index']).mean():4.0%}  lost>20% {(g.ret < -0.2).mean():4.0%}")
    return out, (d.iloc[:n].ret.mean(), d.iloc[len(d) - n:].ret.mean()) if n else (None, None)


def report(d, title):
    print(f"\n=== {title}: {len(d)} companies, {d.ret.notna().sum()} priced, judged {d.conviction.notna().sum()}; "
          f"all listed avg {d.ret.mean():+.1%}, median {d.ret.median():+.1%}; index {d['index'].mean():+.1%}")
    known = d[d.probe.isin(["higher", "lower"]) & d.ret.notna()]
    right = ((known.probe == "higher") == (known.ret > 0)).sum()
    print(f"hindsight probe: {dict(d.probe.value_counts())}; of the {len(known)} it claimed to know, {right} were right"
          + (f" ({right / len(known):.0%})" if len(known) else ""))
    d = d.copy(); d["judgment"] = d.edge + d.demand
    verdict = None
    for key, label in (("conviction", "by conviction (numbers + judgment)"), ("judgment", "by the judgment alone (edge + demand)"),
                       ("numbers", "by the numbers alone")):
        lines, (top, bottom) = thirds(d, key)
        print(f"-- {label}"); print("   " + "\n   ".join(lines))
        corr = d[[key, "ret"]].dropna().corr(method="spearman").iloc[0, 1]
        print(f"   rank correlation with what the shares did: {corr:+.2f}")
        if key == "conviction" and top is not None:
            verdict = top > bottom and top > d["index"].mean()
    for key in ("conviction", "numbers"):
        g = d.dropna(subset=[key, "ret"]).sort_values(key, ascending=False).head(8)
        print(f"top 8 by {key:10}: avg {g.ret.mean():+6.1%} median {g.ret.median():+6.1%}  {' '.join(g.ticker)}")
    print(f"RULE (top third by conviction beat the bottom third and the index): {'PASSED' if verdict else 'FAILED' if verdict is not None else 'n/a'}")
    return verdict


if __name__ == "__main__":
    d = load()
    if d.empty:
        sys.exit("nothing judged yet")
    d.to_csv(HERE / "results.csv", index=False)
    print("model:", set(d.model))
    for cutoff, g in d.groupby("list"):
        report(g, f"list as of {cutoff}")
    pooled = d.sort_values("list").drop_duplicates("ticker", keep="last")
    report(pooled, "pooled, each company once (its later list)")
    print("\nlowest and highest conviction, later list:")
    last = d[d.list == d.list.max()].dropna(subset=["conviction"]).sort_values("conviction")
    for _, r in pd.concat([last.head(5), last.tail(5)]).iterrows():
        print(f"  {r.ticker:6} conviction {r.conviction:5.1f} edge {r.edge:.0f} demand {r.demand:.0f} flags {r.n_flags}  shares since {r.ret:+.0%}" if r.ret == r.ret else f"  {r.ticker}")
