"""
Momentum Screener
=================
Scans S&P 500 + Nasdaq 100 (~600 names) daily for momentum setups.

Methodology:
  1. Fetch universe (S&P 500 via Wikipedia + NDX supplement)
  2. Batch-download 1-year price + volume history (single yfinance call)
  3. Calculate IBD-style Relative Strength score per stock
     RS = 0.40 × Q4_return + 0.20 × Q3_return + 0.20 × Q2_return + 0.20 × Q1_return
  4. Percentile-rank all RS scores (RS Rank 99 = top 1% of performers)
  5. Check market regime via SPY vs 200MA (adjusts RS threshold)
  6. Apply quality gate from downloaded data (price, volume)
  7. Apply technical gate: price > 50MA > 200MA
  8. Score sector leadership — boost stocks in top 3 sectors
  9. Return top 50 ranked candidates

Output written to docs/data/screener.json and read by run_daily_analysis.py.

Usage:
    python screener.py                        # run and write output
    python screener.py --top 30               # limit candidates
    python screener.py --dry-run              # print results, no file write
"""

import argparse
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# Finviz sector name → internal key used throughout the screener
_FINVIZ_SECTOR_NORM: Dict[str, str] = {
    "Technology":             "technology",
    "Healthcare":             "healthcare",
    "Financial":              "financials",
    "Consumer Cyclical":      "consumer_cyclical",
    "Industrials":            "industrials",
    "Communication Services": "communication_services",
    "Consumer Defensive":     "consumer_defensive",
    "Energy":                 "energy",
    "Basic Materials":        "basic_materials",
    "Real Estate":            "real_estate",
    "Utilities":              "utilities",
}

# ── Fallback universe (used when Finviz is unreachable) ──────────────────────
# Covers the growth/momentum names the strategy focuses on; sector tags are
# assigned from config.SECTOR_MAP when Finviz data is unavailable.
FALLBACK_UNIVERSE: Dict[str, str] = {
    # Mega-cap tech
    **{s: "technology" for s in [
        "NVDA", "META", "GOOGL", "MSFT", "AMZN", "AAPL", "TSLA",
        "ORCL", "ADBE", "INTU", "CDNS", "SNPS", "NFLX", "UBER", "ABNB",
    ]},
    # Semiconductors
    **{s: "technology" for s in [
        "AMD", "AVGO", "QCOM", "MRVL", "ARM", "SMCI", "ON",
        "AMAT", "KLAC", "LRCX", "MCHP", "TXN", "ASML", "TSM",
    ]},
    # Cybersecurity / enterprise software
    **{s: "technology" for s in [
        "CRWD", "PANW", "FTNT", "ZS", "OKTA",
        "DDOG", "SNOW", "MDB", "NET", "NOW", "CRM", "WDAY", "TEAM",
        "VRT", "ANET", "APH",
    ]},
    # Fintech / payments
    **{s: "financials" for s in ["COIN", "NU", "AFRM", "SOFI", "V", "MA", "PYPL"]},
    # Consumer / media
    **{s: "consumer_cyclical" for s in ["SHOP", "DUOL", "APP", "TTD", "RBLX", "SPOT"]},
    # Healthcare
    **{s: "healthcare" for s in ["ISRG", "DXCM", "PODD", "LLY", "NVO", "REGN", "VRTX"]},
    # Industrials / defense / energy
    **{s: "industrials" for s in ["AXON", "HWM", "GE", "RTX", "ETN", "PWR", "URI", "GNRC"]},
    **{s: "energy" for s in ["CEG", "VST"]},
    **{s: "basic_materials" for s in ["FCX", "NEM", "GOLD"]},
    # Emerging / thematic
    **{s: "technology" for s in ["PLTR", "RDDT", "MSTR"]},
}


def _scrape_finviz_index(session, index_filter: str) -> Dict[str, str]:
    """
    Paginate through one Finviz index filter and return {ticker: sector}.
    Stops when a page returns fewer than 20 rows (last page).
    """
    from bs4 import BeautifulSoup

    base = f"https://finviz.com/screener.ashx?v=111&f={index_filter}&o=ticker"
    results: Dict[str, str] = {}

    for start in range(1, 1200, 20):
        try:
            resp = session.get(f"{base}&r={start}", timeout=15)
            if resp.status_code != 200:
                logger.warning(f"Finviz {index_filter} HTTP {resp.status_code} at r={start}")
                break

            soup = BeautifulSoup(resp.text, "lxml")
            rows = soup.select("tr.styled-row")
            if not rows:
                break

            for row in rows:
                cells = row.find_all("td")
                if len(cells) < 4:
                    continue
                ticker = cells[1].get_text(strip=True)
                sector = cells[3].get_text(strip=True)
                if ticker and 2 <= len(ticker) <= 5 and ticker.replace("-", "").isalpha():
                    results[ticker] = _FINVIZ_SECTOR_NORM.get(sector, "other")

            if len(rows) < 20:
                break  # Last page reached

            time.sleep(0.4)

        except Exception as e:
            logger.warning(f"Finviz fetch error ({index_filter} r={start}): {e}")
            break

    return results


def _fetch_finviz_universe() -> Dict[str, str]:
    """
    Fetch S&P 500 and Nasdaq 100 from Finviz as separate requests and merge.

    Finviz treats comma-separated index filters as AND (intersection), so we
    fetch each index independently and union the results.  S&P 500 sector
    takes precedence; Nasdaq-only names inherit the sector from the NDX fetch.

    Returns {ticker: normalized_sector} for ~550–600 stocks.
    """
    import requests

    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://finviz.com/screener.ashx",
    })

    sp500 = _scrape_finviz_index(session, "idx_sp500")
    logger.info(f"Finviz S&P 500: {len(sp500)} tickers")

    ndx = _scrape_finviz_index(session, "idx_ndx")
    logger.info(f"Finviz Nasdaq 100: {len(ndx)} tickers")

    # Merge: S&P 500 sector wins; add any NDX-only names
    merged = {**ndx, **sp500}
    return merged


def get_universe() -> Dict[str, str]:
    """
    Build the scan universe as {ticker: sector}.

    Primary source: Finviz screener (S&P 500 + Nasdaq 100) — gives real sectors
    for every stock so leadership scoring works across all 11 GICS sectors.

    Falls back to FALLBACK_UNIVERSE (tech-heavy hand-curated list) if Finviz
    is unreachable.

    NDX growth names not already in the Finviz result are merged in after.
    """
    universe: Dict[str, str] = {}

    try:
        universe = _fetch_finviz_universe()
        if len(universe) >= 100:
            logger.info(f"Finviz: fetched {len(universe)} tickers with sectors")
        else:
            raise ValueError(f"Finviz returned only {len(universe)} tickers — too few")
    except Exception as e:
        logger.warning(f"Finviz fetch failed ({e}) — using fallback universe")
        universe = dict(FALLBACK_UNIVERSE)

    # NDX high-growth supplement — names sometimes missing from S&P 500 filter
    ndx_supplement: Dict[str, str] = {
        **{s: "technology" for s in [
            "NVDA", "META", "AMD", "AVGO", "MRVL", "ARM", "SMCI",
            "CRWD", "PANW", "ZS", "FTNT",
            "DDOG", "SNOW", "MDB", "NET", "WDAY", "TEAM",
            "VRT", "ANET", "CEG", "VST",
            "NFLX", "UBER", "ABNB", "PLTR", "RDDT",
        ]},
        **{s: "financials"        for s in ["COIN", "NU", "AFRM", "SOFI", "HOOD", "UPST"]},
        **{s: "consumer_cyclical" for s in ["SHOP", "DUOL", "APP", "TTD", "RBLX", "SPOT"]},
    }
    # Only add supplement entries that aren't already from Finviz
    for sym, sector in ndx_supplement.items():
        if sym not in universe:
            universe[sym] = sector

    # Drop anything that looks like a bad ticker
    universe = {
        t: s for t, s in universe.items()
        if 2 <= len(t) <= 5 and t.replace("-", "").isalpha()
    }

    logger.info(f"Total universe: {len(universe)} tickers")
    return universe


def batch_download(tickers: List[str]) -> Optional[object]:
    """
    Download 1-year OHLCV for all tickers in a single yfinance call.
    Returns the raw DataFrame or None on failure.
    """
    import yfinance as yf

    if not tickers:
        return None

    logger.info(f"Batch downloading {len(tickers)} tickers (1 year)…")
    try:
        data = yf.download(
            tickers,
            period="1y",
            auto_adjust=True,
            progress=False,
            threads=True,
        )
        logger.info("Batch download complete")
        return data
    except Exception as e:
        logger.error(f"Batch download failed: {e}")
        return None


def calculate_rs_scores(
    tickers: List[str],
    data,
) -> Dict[str, float]:
    """
    IBD-style Relative Strength score from pre-downloaded price data.

    Formula (weights most recent performance):
        RS = 0.40 × Q4 + 0.20 × Q3 + 0.20 × Q2 + 0.20 × Q1

    Q4 = last 3 months (most recent), Q3 = 3-6M, Q2 = 6-9M, Q1 = 9-12M.

    Returns raw RS score (decimal return). Percentile ranking done separately.
    """
    if data is None:
        return {}

    # Handle single vs multi-ticker DataFrame shapes
    try:
        closes = data["Close"]
    except Exception:
        return {}

    scores = {}

    for sym in tickers:
        try:
            if hasattr(closes, "columns") and sym in closes.columns:
                prices = closes[sym].dropna().values
            elif not hasattr(closes, "columns"):
                # Single-ticker: closes is a Series
                prices = closes.dropna().values
            else:
                continue

            n = len(prices)
            if n < 200:
                continue

            # Quarter boundaries in trading days
            # Q4 = most recent 63 days, Q3 = 63-126, Q2 = 126-189, Q1 = 189-252
            def ret(start_idx, end_idx=-1):
                s = prices[max(0, n + start_idx)]
                e = prices[-1] if end_idx == -1 else prices[max(0, n + end_idx)]
                return (e / s - 1) if s > 0 else 0

            r4 = ret(-63)          # last 3 months
            r3 = ret(-126, -63)    # 3-6 months ago
            r2 = ret(-189, -126)   # 6-9 months ago
            r1 = ret(-252, -189)   # 9-12 months ago

            rs = 0.40 * r4 + 0.20 * r3 + 0.20 * r2 + 0.20 * r1
            scores[sym] = round(float(rs), 5)
        except Exception:
            continue

    logger.info(f"RS scores calculated for {len(scores)} tickers")
    return scores


def percentile_rank(scores: Dict[str, float]) -> Dict[str, int]:
    """
    Percentile-rank raw RS scores across the full universe.
    RS Rank 99 = top 1% (best performers), RS Rank 1 = worst.
    """
    if not scores:
        return {}

    syms = list(scores.keys())
    vals = list(scores.values())

    ranks = {}
    total = len(vals)
    for sym, val in zip(syms, vals):
        pct = sum(v <= val for v in vals) / total * 100
        ranks[sym] = min(99, max(1, round(pct)))

    return ranks


def get_market_regime() -> Dict:
    """
    Check SPY vs 50MA and 200MA to determine market regime.

    Returns:
        regime:        'bull' | 'neutral' | 'bear'
        rs_threshold:  minimum RS rank to qualify (raised in bear markets)
    """
    import yfinance as yf

    try:
        spy    = yf.Ticker("SPY").history(period="1y")
        closes = spy["Close"].values
        price  = float(closes[-1])
        sma50  = float(closes[-50:].mean())
        sma200 = float(closes[-200:].mean())
        ret_3m = float((closes[-1] / closes[-63] - 1) * 100) if len(closes) >= 63 else 0

        if price > sma50 > sma200:
            regime, threshold = "bull", 70
            note = "SPY: golden alignment — normal thresholds"
        elif price > sma200:
            regime, threshold = "neutral", 80
            note = "SPY: above 200MA but below 50MA — elevated thresholds"
        else:
            regime, threshold = "bear", 90
            note = "SPY: below 200MA — defensive mode, only elite RS names"

        return {
            "regime":        regime,
            "spy_price":     round(price, 2),
            "spy_50ma":      round(sma50, 2),
            "spy_200ma":     round(sma200, 2),
            "spy_3m_return": round(ret_3m, 1),
            "rs_threshold":  threshold,
            "note":          note,
        }
    except Exception as e:
        logger.warning(f"Market regime check failed: {e}")
        return {
            "regime": "unknown",
            "rs_threshold": 75,
            "note": "SPY data unavailable — using default threshold",
        }


def get_sector_leaders(
    rs_ranks: Dict[str, int],
    ticker_sectors: Dict[str, str] = None,
) -> List[str]:
    """
    Rank sectors by average RS rank of their members.

    When ticker_sectors (Finviz-sourced) is provided, every stock in the
    universe contributes to its sector score — not just the hand-coded
    SECTOR_MAP names.  Requires at least 3 members per sector for a
    meaningful average (avoids noise from thin sectors).
    """
    if ticker_sectors:
        sector_to_syms: Dict[str, List[str]] = {}
        for sym, sector in ticker_sectors.items():
            if sym in rs_ranks:
                sector_to_syms.setdefault(sector, []).append(sym)
    else:
        from config import SECTOR_MAP
        sector_to_syms = {s: list(syms) for s, syms in SECTOR_MAP.items()}

    sector_scores = {}
    for sector, syms in sector_to_syms.items():
        member_ranks = [rs_ranks[s] for s in syms if s in rs_ranks]
        if len(member_ranks) >= 3:
            sector_scores[sector] = sum(member_ranks) / len(member_ranks)

    return sorted(sector_scores, key=lambda s: sector_scores[s], reverse=True)


def run_screener(
    output_path: Optional[Path] = None,
    top_n: int = 50,
    min_rs_rank: int = 70,
    min_price: float = 5.0,
    min_avg_volume: int = 500_000,
) -> Dict:
    """
    Full momentum screening pipeline. Single entry point.

    Steps:
      1. Universe (S&P 500 + NDX ~600 names)
      2. Market regime (adjusts RS threshold)
      3. Batch download all tickers (one network call)
      4. RS score + percentile rank
      5. Quality gate (price, volume — from downloaded data, no extra API calls)
      6. Technical gate (price > 50MA > 200MA)
      7. Sector leadership scoring
      8. Return top N sorted by effective RS rank

    Args:
        output_path:    write JSON to this path if set
        top_n:          max candidates to return
        min_rs_rank:    floor RS rank (overridden upward by market regime)
        min_price:      minimum stock price
        min_avg_volume: minimum 20-day average volume
    """
    start = datetime.now()

    logger.info("=" * 50)
    logger.info("MOMENTUM SCREENER")
    logger.info("=" * 50)

    # 1. Universe — dict {ticker: sector} from Finviz (or fallback)
    universe = get_universe()
    tickers  = list(universe.keys())

    # 2. Market regime
    regime = get_market_regime()
    threshold = max(min_rs_rank, regime["rs_threshold"])
    logger.info(f"Regime: {regime['regime'].upper()} | RS threshold: {threshold}")

    # 3. Batch download
    data = batch_download(tickers)
    if data is None:
        logger.error("Download failed — screener aborted")
        return {"error": "download_failed", "candidates": []}

    # 4. RS scores + percentile rank
    raw_scores = calculate_rs_scores(tickers, data)
    rs_ranks   = percentile_rank(raw_scores)

    # 5 + 6 + 7. Quality gate + technical gate + sector boost
    # Sector comes from Finviz universe dict — covers all 11 GICS sectors.
    sector_leaders = get_sector_leaders(rs_ranks, universe)
    top_sectors    = set(sector_leaders[:3])

    # Get volume data for quality filter
    try:
        volumes = data["Volume"]
    except Exception:
        volumes = None

    try:
        closes = data["Close"]
    except Exception:
        logger.error("No Close data in download")
        return {"error": "no_close_data", "candidates": []}

    candidates = []
    rejected   = {"rs_rank": 0, "quality": 0, "technical": 0}

    for sym in universe:
        # Get price series
        try:
            if hasattr(closes, "columns") and sym in closes.columns:
                price_arr = closes[sym].dropna().values
            else:
                continue

            if len(price_arr) < 200:
                continue
        except Exception:
            continue

        # RS rank filter (fast, no I/O)
        rs_rank = rs_ranks.get(sym, 0)
        if rs_rank < threshold:
            rejected["rs_rank"] += 1
            continue

        # Quality gate (from downloaded data — no extra API calls)
        price = float(price_arr[-1])
        if price < min_price:
            rejected["quality"] += 1
            continue

        if volumes is not None:
            try:
                vol_series = (
                    volumes[sym].dropna() if sym in volumes.columns else None
                )
                if vol_series is not None:
                    avg_vol = float(vol_series.tail(20).mean())
                    if avg_vol < min_avg_volume:
                        rejected["quality"] += 1
                        continue
            except Exception:
                pass

        # Technical gate: price > 50MA > 200MA
        sma50  = float(price_arr[-50:].mean())
        sma200 = float(price_arr[-200:].mean())

        if not (price > sma50 > sma200):
            rejected["technical"] += 1
            continue

        # Build candidate record
        rs_score  = raw_scores.get(sym, 0)
        sector    = universe.get(sym, "other")
        is_leader = sector in top_sectors

        # Forward thesis score — WHERE is this stock going, not just where it's been
        try:
            vol_arr = None
            if volumes is not None and sym in volumes.columns:
                vol_arr = volumes[sym].dropna().values
            if vol_arr is None or len(vol_arr) < 40:
                vol_arr = np.ones(len(price_arr))

            from forward_thesis import score_forward_thesis
            thesis = score_forward_thesis(
                sym, price_arr, vol_arr,
                include_earnings=False,  # Skip slow earnings call; pre-market agent handles it
            )
            thesis_score = thesis["thesis_score"]
            thesis_grade = thesis["thesis_grade"]
            setup_reason = thesis["setup_reason"]
        except Exception as e:
            logger.warning(f"Thesis scoring failed for {sym}: {e}")
            thesis_score = 0.0
            thesis_grade = "D"
            setup_reason = "scoring error"

        # Combined ranking: 60% RS rank (proven momentum) + 40% forward thesis
        # RS rank tells us the trend is real. Thesis tells us why it continues.
        sector_boost    = 1.05 if is_leader else 1.0
        effective_score = (0.60 * rs_rank + 0.40 * thesis_score) * sector_boost

        ret_1m = float((price_arr[-1] / price_arr[-21] - 1) * 100) if len(price_arr) >= 21 else None
        ret_3m = float((price_arr[-1] / price_arr[-63] - 1) * 100) if len(price_arr) >= 63 else None
        ret_6m = float((price_arr[-1] / price_arr[-126] - 1) * 100) if len(price_arr) >= 126 else None

        candidates.append({
            "symbol":          sym,
            "rs_rank":         rs_rank,
            "rs_score":        round(rs_score * 100, 1),
            "thesis_score":    round(thesis_score, 1),
            "thesis_grade":    thesis_grade,
            "setup_reason":    setup_reason,
            "effective_score": round(effective_score, 1),
            "sector":          sector,
            "sector_leader":   is_leader,
            "trend":           "strong_up",
            "price":           round(price, 2),
            "sma50":           round(sma50, 2),
            "sma200":          round(sma200, 2),
            "ret_1m":          round(ret_1m, 1) if ret_1m is not None else None,
            "ret_3m":          round(ret_3m, 1) if ret_3m is not None else None,
            "ret_6m":          round(ret_6m, 1) if ret_6m is not None else None,
            "source":          "screener",
            "priority":        0 if is_leader else 1,
        })

    # Sort by effective score (RS rank + forward thesis + sector boost)
    candidates.sort(key=lambda x: x["effective_score"], reverse=True)
    candidates = candidates[:top_n]

    elapsed = round((datetime.now() - start).total_seconds(), 1)

    result = {
        "generated_at":    datetime.now().isoformat(),
        "elapsed_seconds": elapsed,
        "market_regime":   regime,
        "universe_size":   len(universe),
        "rs_qualifying":   len(universe) - rejected["rs_rank"],
        "quality_pass":    len(universe) - rejected["rs_rank"] - rejected["quality"],
        "tech_gate_pass":  len(candidates),
        "final_count":     len(candidates),
        "sector_leaders":  sector_leaders[:6],
        "rejection_stats": rejected,
        "candidates":      candidates,
    }

    logger.info(
        f"Complete in {elapsed}s | "
        f"Universe {len(universe)} → RS {result['rs_qualifying']} → "
        f"Quality {result['quality_pass']} → "
        f"Tech gate {result['final_count']} candidates"
    )

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(json.dumps(result, indent=2))
        logger.info(f"Written to {output_path}")

    return result


def print_results(result: Dict):
    """Pretty-print screener results to stdout."""
    regime = result.get("market_regime", {})
    print(f"\n{'='*65}")
    print(f"  MOMENTUM SCREENER  ·  {result.get('generated_at','')[:16]}")
    print(f"{'='*65}")
    print(f"  Universe:     {result.get('universe_size', 0)}")
    print(f"  RS threshold: ≥{regime.get('rs_threshold', '?')}  ({regime.get('regime','?').upper()})")
    print(f"  RS qualifying:{result.get('rs_qualifying', 0)}")
    print(f"  Quality pass: {result.get('quality_pass', 0)}")
    print(f"  Final:        {result.get('final_count', 0)} candidates")
    print(f"  Runtime:      {result.get('elapsed_seconds', 0)}s")
    print(f"\n  Market:  {regime.get('note', '')}")
    leaders = result.get("sector_leaders", [])[:3]
    print(f"  Leaders: {' · '.join(leaders)}")
    print(f"\n{'─'*65}")
    print(f"  {'SYM':<7} {'RS':>4}  {'SECTOR':<22} {'1M%':>7} {'3M%':>7} {'6M%':>7}  LDR")
    print(f"{'─'*65}")
    for c in result.get("candidates", [])[:30]:
        ldr = "★" if c.get("sector_leader") else " "
        r1  = f"{c.get('ret_1m') or 0:+.1f}%" if c.get("ret_1m") is not None else "   —  "
        r3  = f"{c.get('ret_3m') or 0:+.1f}%" if c.get("ret_3m") is not None else "   —  "
        r6  = f"{c.get('ret_6m') or 0:+.1f}%" if c.get("ret_6m") is not None else "   —  "
        print(f"  {c['symbol']:<7} {c['rs_rank']:>4}  {c['sector']:<22} {r1:>7} {r3:>7} {r6:>7}  {ldr}")
    print(f"{'='*65}\n")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)s  %(message)s",
    )

    parser = argparse.ArgumentParser(description="Momentum screener")
    parser.add_argument("--top", type=int, default=50, help="Max candidates")
    parser.add_argument("--dry-run", action="store_true", help="Print only, no file write")
    parser.add_argument("--output", type=str, default=None, help="Output path override")
    args = parser.parse_args()

    output = None
    if not args.dry_run:
        output = Path(args.output) if args.output else (
            Path(__file__).parent / "docs" / "data" / "screener.json"
        )

    result = run_screener(output_path=output, top_n=args.top)
    print_results(result)
