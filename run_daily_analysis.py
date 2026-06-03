#!/usr/bin/env python3
"""
Daily Analysis Runner
=====================

Main entry point for the trading agent.

Usage:
    # Dry run (default) - shows signals, no orders placed
    python run_daily_analysis.py

    # Execute trades - submits orders to Alpaca
    python run_daily_analysis.py --execute

    # Execute with higher confidence bar
    python run_daily_analysis.py --execute --min-confidence 0.75

    # Quick scan (fewer candidates, faster)
    python run_daily_analysis.py --mode quick

    # Specific symbols only
    python run_daily_analysis.py --symbols NVDA,META,CRWD

    # Write results to file
    python run_daily_analysis.py --execute --output /path/to/output.json

Execution safety gates (all must pass before any order is submitted):
    1. --execute flag must be set (default is dry-run)
    2. Market must currently be open (Alpaca clock check)
    3. Signal confidence >= --min-confidence (default 0.65)
    4. Limit price and share count must be valid
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, date
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent))

from decision_engine import DecisionEngine
from config import DecisionConfig, ConvictionTier
from models import PortfolioState, ResearchScore
from universe_screener import UniverseScreener

try:
    from data_infrastructure import DataOrchestrator
    DATA_AVAILABLE = True
except ImportError:
    DATA_AVAILABLE = False
    DataOrchestrator = None

# Support both root-level and execution/ subdirectory layouts
try:
    from alpaca_broker import AlpacaBroker, OrderExecutor
    ALPACA_AVAILABLE = True
except ImportError:
    try:
        from execution.alpaca_broker import AlpacaBroker, OrderExecutor
        ALPACA_AVAILABLE = True
    except ImportError:
        ALPACA_AVAILABLE = False
        AlpacaBroker = None
        OrderExecutor = None


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

MIN_CONFIDENCE_DEFAULT = 0.65


def fetch_market_data(symbol: str):
    """
    Fetch market snapshot and price history via yfinance.
    Returns (MarketSnapshot, List[PriceBar]) or (None, None) on failure.
    """
    try:
        import yfinance as yf
        from models import MarketSnapshot
        from momentum import PriceBar

        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="1y")
        if hist.empty:
            return None, None

        info = ticker.fast_info

        closes = hist["Close"].values
        sma_50  = float(closes[-50:].mean())  if len(closes) >= 50  else None
        sma_200 = float(closes[-200:].mean()) if len(closes) >= 200 else None

        current_price = float(closes[-1])

        snapshot = MarketSnapshot(
            symbol=symbol,
            timestamp=datetime.now(),
            current_price=current_price,
            previous_close=float(closes[-2]) if len(closes) >= 2 else current_price,
            day_change_pct=float((closes[-1] / closes[-2] - 1) * 100) if len(closes) >= 2 else 0,
            day_high=float(hist["High"].iloc[-1]),
            day_low=float(hist["Low"].iloc[-1]),
            week_52_high=float(hist["High"].max()),
            week_52_low=float(hist["Low"].min()),
            volume=int(hist["Volume"].iloc[-1]),
            avg_volume=int(hist["Volume"].tail(20).mean()),
            market_cap=float(getattr(info, "market_cap", 0) or 0),
            sma_50=sma_50,
            sma_200=sma_200,
        )

        price_bars = []
        for idx, row in hist.tail(252).iterrows():
            try:
                price_bars.append(PriceBar(
                    date=idx.date(),
                    open=float(row["Open"]),
                    high=float(row["High"]),
                    low=float(row["Low"]),
                    close=float(row["Close"]),
                    volume=int(row["Volume"]),
                ))
            except Exception:
                continue

        return snapshot, price_bars

    except Exception as e:
        logger.warning(f"yfinance fetch failed for {symbol}: {e}")
        return None, None


class DailyRunner:
    """Orchestrates the daily analysis and optional trade execution workflow."""

    def __init__(self,
                 portfolio_value: float = 100000,
                 data_dir: Path = None):
        self.data_dir = Path(data_dir or os.getenv("TRADING_AGENT_DATA_DIR", "./trading_data"))
        self.data_dir.mkdir(parents=True, exist_ok=True)

        self.screener = UniverseScreener(data_dir=self.data_dir / "screening")

        self.orchestrator = None
        if DATA_AVAILABLE:
            try:
                self.orchestrator = DataOrchestrator()
                logger.info("Data infrastructure initialized")
            except Exception as e:
                logger.warning(f"Could not initialize data infrastructure: {e}")

        self.engine = DecisionEngine(
            data_orchestrator=self.orchestrator,
            data_dir=self.data_dir / "decisions"
        )

        self.broker = None
        self.portfolio = self._initialize_portfolio(portfolio_value)
        self.research_scores = self._load_research_scores()

    def _initialize_portfolio(self, default_value: float) -> PortfolioState:
        """Load portfolio from Alpaca if available, otherwise use default value."""
        if ALPACA_AVAILABLE:
            try:
                self.broker = AlpacaBroker()
                account = self.broker.get_account()

                if "error" not in account:
                    logger.info(
                        f"Loaded Alpaca portfolio: "
                        f"${account['portfolio_value']:,.2f} total, "
                        f"${account['buying_power']:,.2f} buying power"
                    )
                    return PortfolioState(
                        timestamp=datetime.now(),
                        total_value=account["portfolio_value"],
                        cash=account["cash"],
                        invested=account["long_market_value"],
                        available_cash=account["buying_power"] * 0.95  # 5% buffer
                    )
            except Exception as e:
                logger.warning(f"Could not connect to Alpaca: {e}")

        logger.info(f"Using default portfolio value: ${default_value:,.2f}")
        return PortfolioState(
            timestamp=datetime.now(),
            total_value=default_value,
            cash=default_value,
            invested=0,
            available_cash=default_value * 0.95
        )

    def _load_research_scores(self) -> Dict[str, ResearchScore]:
        """
        Load research scores from research_scores.json if present.

        File format:
        {
            "NVDA": {
                "overall_score": 4.40,
                "conviction_tier": "HIGH",
                "thesis": "...",
                "bear_case_price": 100,
                "base_case_price": 160,
                "bull_case_price": 200
            }
        }
        """
        scores_file = self.data_dir / "research_scores.json"

        if scores_file.exists():
            try:
                with open(scores_file) as f:
                    data = json.load(f)

                scores = {}
                for symbol, info in data.items():
                    scores[symbol] = ResearchScore(
                        symbol=symbol,
                        score_date=date.today(),
                        overall_score=info.get("overall_score", 3.5),
                        conviction_tier=ConvictionTier[info.get("conviction_tier", "MEDIUM")],
                        thesis=info.get("thesis", ""),
                        bear_case_price=info.get("bear_case_price"),
                        base_case_price=info.get("base_case_price"),
                        bull_case_price=info.get("bull_case_price"),
                        key_risks=info.get("key_risks", []),
                        catalysts=info.get("catalysts", [])
                    )

                logger.info(f"Loaded {len(scores)} research scores")
                return scores

            except Exception as e:
                logger.warning(f"Error loading research scores: {e}")

        return {}

    def run(self,
            mode: str = "full",
            symbols: List[str] = None,
            max_candidates: int = 30,
            execute: bool = False,
            min_confidence: float = MIN_CONFIDENCE_DEFAULT) -> Dict:
        """
        Run daily analysis and optionally execute trades.

        Args:
            mode: "full", "quick", or "symbols"
            symbols: Specific symbols (for "symbols" mode)
            max_candidates: Max candidates to screen
            execute: If True, submit qualifying BUY orders to Alpaca
            min_confidence: Minimum signal confidence required for execution
        """
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        start_time = datetime.now()

        logger.info(
            f"Starting run {run_id} | mode={mode} | "
            f"execute={'YES' if execute else 'DRY-RUN'} | "
            f"min_confidence={min_confidence}"
        )

        results = {
            "run_id": run_id,
            "timestamp": start_time.isoformat(),
            "mode": mode,
            "execute": execute,
            "status": "running",
            "summary": {},
            "opportunities": [],
            "holds": [],
            "execution": {},
            "performers_update": [],
            "portfolio": {},
            "errors": []
        }

        try:
            # 0. Evaluate exits on ALL open positions before looking for new entries
            exit_results = self._evaluate_exits(execute=execute)
            results["exits"] = exit_results

            # 1. Get candidates — prefer RS-ranked screener output if available and fresh
            if symbols:
                candidates = [{"symbol": s.upper(), "source": "manual"} for s in symbols]
            elif mode == "quick":
                candidates = self.screener.get_screening_candidates(max_candidates=15)
            else:
                candidates = self._load_screener_candidates(max_candidates) or \
                             self.screener.get_screening_candidates(max_candidates=max_candidates)

            logger.info(f"Screening {len(candidates)} candidates")

            # 2. Generate signals for each candidate
            opportunities = []
            holds = []
            errors = []

            # Get current sector allocations for concentration check
            sector_allocations = self._get_sector_allocations()

            for candidate in candidates:
                symbol = candidate["symbol"]

                try:
                    research = self.research_scores.get(symbol)

                    # Fetch live market data via yfinance
                    market_snapshot, price_bars = fetch_market_data(symbol)

                    # ── Pre-entry filters ──────────────────────────────────────

                    # Earnings blackout: skip if earnings within N days
                    earnings_skip, earnings_reason = self._check_earnings_proximity(symbol)
                    if earnings_skip:
                        holds.append({"symbol": symbol, "reason": earnings_reason})
                        logger.info(f"Skipping {symbol}: {earnings_reason}")
                        continue

                    # RSI overbought: skip if RSI above threshold
                    rsi_skip, rsi_reason = self._check_rsi_at_entry(price_bars)
                    if rsi_skip:
                        holds.append({"symbol": symbol, "reason": rsi_reason})
                        logger.info(f"Skipping {symbol}: {rsi_reason}")
                        continue

                    # Sector concentration: skip if sector already at limit
                    sector = candidate.get("sector", "")
                    sector_skip, sector_reason = self._check_sector_concentration(
                        sector, sector_allocations
                    )
                    if sector_skip:
                        holds.append({"symbol": symbol, "reason": sector_reason})
                        logger.info(f"Skipping {symbol}: {sector_reason}")
                        continue

                    # ── Signal generation ──────────────────────────────────────
                    decision = self.engine.evaluate_entry(
                        symbol=symbol,
                        portfolio=self.portfolio,
                        research_score=research,
                        market_snapshot=market_snapshot,
                        price_history=price_bars,
                        auto_fetch=bool(self.orchestrator)
                    )

                    decision_dict = self._decision_to_dict(decision)

                    # Apply momentum score scaling to position size
                    decision_dict = self._scale_size_by_momentum(decision_dict)

                    if decision.action == "BUY":
                        opportunities.append(decision_dict)
                        self.screener.track_performer(
                            symbol=symbol,
                            score=decision.signal.strength if decision.signal else 0,
                            signal="BUY",
                            sector=sector,
                            thesis=candidate.get("theme", ""),
                            note=f"confidence={decision_dict.get('confidence', 0):.2f}"
                        )
                    else:
                        holds.append({"symbol": symbol, "reason": decision.primary_reason[:100]})

                except Exception as e:
                    logger.error(f"Error analyzing {symbol}: {e}")
                    errors.append({"symbol": symbol, "error": str(e)})

            opportunities.sort(
                key=lambda x: (x.get("signal_strength", 0), x.get("trend_score") or 0),
                reverse=True
            )
            opportunities = opportunities[:8]

            # 3. Execute trades (or dry-run)
            results["execution"] = self._execute_opportunities(
                opportunities, execute, min_confidence
            )

            # 4. Build summary
            total_investment = sum(o.get("position_value", 0) for o in opportunities)
            executed_count = results["execution"].get("submitted", 0)

            results["summary"] = {
                "candidates_screened": len(candidates),
                "buy_signals": len(opportunities),
                "hold_signals": len(holds),
                "errors": len(errors),
                "total_investment_recommended": round(total_investment, 2),
                "orders_submitted": executed_count,
                "portfolio_value": self.portfolio.total_value,
                "available_cash": self.portfolio.available_cash
            }

            results["opportunities"] = opportunities
            results["holds"] = holds[:10]
            results["errors"] = errors
            results["performers_update"] = self.screener.get_persistent_performers(min_times_seen=2)
            results["portfolio"] = {
                "total_value": self.portfolio.total_value,
                "cash": self.portfolio.cash,
                "invested": self.portfolio.invested,
                "available_cash": self.portfolio.available_cash,
                "num_positions": self.portfolio.num_positions
            }
            results["status"] = "success"

        except Exception as e:
            logger.error(f"Analysis run failed: {e}")
            results["status"] = "error"
            results["errors"].append({"error": str(e)})

        results["duration_seconds"] = (datetime.now() - start_time).total_seconds()
        logger.info(
            f"Run complete: {len(results['opportunities'])} signals, "
            f"{results['execution'].get('submitted', 0)} orders submitted"
        )

        self._write_dashboard_data(results, execute)
        return results

    def _get_company_cache(self, symbols: list, docs_dir: Path) -> dict:
        """
        Return company metadata for each symbol, using a local cache file.
        Static fields (name, sector, description) are cached; only fetched once per symbol.
        """
        import yfinance as yf

        cache_file = docs_dir / "companies.json"
        cache = {}
        if cache_file.exists():
            try:
                cache = json.loads(cache_file.read_text())
            except Exception:
                pass

        uncached = [s for s in symbols if s not in cache]
        if uncached:
            logger.info(f"Fetching company info for {len(uncached)} new symbols: {uncached}")
            for sym in uncached:
                try:
                    info = yf.Ticker(sym).info
                    cap = info.get("marketCap") or 0
                    if cap > 1e12:
                        cap_str = f"${cap/1e12:.1f}T"
                    elif cap > 1e9:
                        cap_str = f"${cap/1e9:.1f}B"
                    elif cap > 1e6:
                        cap_str = f"${cap/1e6:.0f}M"
                    else:
                        cap_str = "—"

                    summary = (info.get("longBusinessSummary") or "").strip()
                    if len(summary) > 240:
                        summary = summary[:240].rsplit(" ", 1)[0] + "…"

                    cache[sym] = {
                        "name":       info.get("longName") or info.get("shortName") or sym,
                        "sector":     info.get("sector") or "",
                        "industry":   info.get("industry") or "",
                        "summary":    summary,
                        "market_cap": cap_str,
                    }
                except Exception:
                    cache[sym] = {
                        "name": sym, "sector": "", "industry": "",
                        "summary": "", "market_cap": "—",
                    }
            try:
                cache_file.write_text(json.dumps(cache, indent=2))
            except Exception:
                pass

        return cache

    def _enrich_positions(self, positions: list, docs_dir: Path) -> list:
        """Add company metadata and 52-week context to each position."""
        import yfinance as yf

        if not positions:
            return positions

        symbols = [p["symbol"] for p in positions]
        company_cache = self._get_company_cache(symbols, docs_dir)

        for p in positions:
            sym = p["symbol"]
            meta = company_cache.get(sym, {})

            # Fetch 52w range via fast_info (much faster than .info)
            week_52_high = week_52_low = pct_from_high = None
            try:
                fi = yf.Ticker(sym).fast_info
                week_52_high = round(float(fi.year_high), 2) if fi.year_high else None
                week_52_low  = round(float(fi.year_low), 2)  if fi.year_low  else None
                if week_52_high and week_52_high > 0:
                    pct_from_high = round(
                        ((week_52_high - p["current_price"]) / week_52_high) * 100, 1
                    )
            except Exception:
                pass

            p["company"] = {
                "name":         meta.get("name", sym),
                "sector":       meta.get("sector", ""),
                "industry":     meta.get("industry", ""),
                "summary":      meta.get("summary", ""),
                "market_cap":   meta.get("market_cap", "—"),
                "week_52_high": week_52_high,
                "week_52_low":  week_52_low,
                "pct_from_high": pct_from_high,
            }

        return positions

    def _write_dashboard_data(self, run_results: dict, execute: bool = False):
        """Write docs/data/latest.json and docs/data/history.json for the dashboard."""
        try:
            docs_dir = Path(__file__).parent / "docs" / "data"
            docs_dir.mkdir(parents=True, exist_ok=True)

            # Fetch live Alpaca positions
            positions = []
            if self.broker:
                for p in (self.broker.get_positions() or []):
                    positions.append({
                        "symbol": p["symbol"],
                        "qty": int(p["qty"]),
                        "avg_cost": round(float(p["avg_entry_price"]), 2),
                        "current_price": round(float(p["current_price"]), 2),
                        "market_value": round(float(p["market_value"]), 2),
                        "unrealized_pnl": round(float(p["unrealized_pl"]), 2),
                        "unrealized_pnl_pct": round(float(p["unrealized_plpc"]) * 100, 2),
                        "stop_loss": None,
                    })

            # Enrich positions with company metadata and 52w context
            positions = self._enrich_positions(positions, docs_dir)

            # Build signal list with execution status
            exec_details = run_results.get("execution", {}).get("details", [])
            executed = {d["symbol"] for d in exec_details if d.get("status") == "submitted"}
            signals = [
                {
                    "symbol": o["symbol"],
                    "action": o["action"],
                    "shares": o.get("shares"),
                    "limit_price": o.get("limit_price"),
                    "stop_loss": o.get("stop_loss"),
                    "confidence": o.get("confidence"),
                    "signal_strength": o.get("signal_strength"),
                    "trend_score": o.get("trend_score"),
                    "reasons": o.get("reasons", []),
                    "executed": o["symbol"] in executed,
                }
                for o in run_results.get("opportunities", [])
            ]

            acct = run_results.get("portfolio", {})
            total_unrealized = sum(p["unrealized_pnl"] for p in positions)

            latest = {
                "generated_at": datetime.now().isoformat(),
                "run_id": run_results.get("run_id", ""),
                "model_status": "healthy" if run_results.get("status") == "success" else "error",
                "account": {
                    "portfolio_value": acct.get("total_value", 0),
                    "cash": acct.get("cash", 0),
                    "buying_power": acct.get("available_cash", 0),
                    "long_market_value": acct.get("invested", 0),
                    "unrealized_pnl": total_unrealized,
                },
                "positions": positions,
                "todays_run": {
                    "mode": "EXECUTE" if execute else "DRY-RUN",
                    "candidates_screened": run_results.get("summary", {}).get("candidates_screened", 0),
                    "buy_signals": run_results.get("summary", {}).get("buy_signals", 0),
                    "orders_submitted": run_results.get("summary", {}).get("orders_submitted", 0),
                    "total_invested": run_results.get("summary", {}).get("total_investment_recommended", 0),
                    "signals": signals,
                    "execution_details": exec_details,
                },
                "persistent_performers": run_results.get("performers_update", []),
            }

            with open(docs_dir / "latest.json", "w") as f:
                json.dump(latest, f, indent=2)

            # Append/update history.json
            history_file = docs_dir / "history.json"
            history = json.loads(history_file.read_text()) if history_file.exists() else []

            today = datetime.now().strftime("%Y-%m-%d")
            entry = {
                "date": today,
                "run_id": run_results.get("run_id", ""),
                "mode": "EXECUTE" if execute else "DRY-RUN",
                "candidates_screened": run_results.get("summary", {}).get("candidates_screened", 0),
                "buy_signals": run_results.get("summary", {}).get("buy_signals", 0),
                "orders_submitted": run_results.get("summary", {}).get("orders_submitted", 0),
                "portfolio_value": acct.get("total_value", 0),
                "status": run_results.get("status", "unknown"),
            }

            idx = next((i for i, h in enumerate(history) if h["date"] == today), None)
            if idx is not None:
                history[idx] = entry
            else:
                history.append(entry)

            with open(history_file, "w") as f:
                json.dump(history[-90:], f, indent=2)

            logger.info("Dashboard data written to docs/data/")

        except Exception as e:
            logger.warning(f"Could not write dashboard data: {e}")

    def _load_screener_candidates(self, max_candidates: int = 30) -> Optional[List[Dict]]:
        """
        Load RS-ranked candidates from screener.json if generated today.
        Falls back to None (caller uses static list) if stale or missing.
        """
        screener_file = Path(__file__).parent / "docs" / "data" / "screener.json"
        if not screener_file.exists():
            logger.info("screener.json not found — using static universe")
            return None
        try:
            data = json.loads(screener_file.read_text())
            generated = (data.get("generated_at") or "")[:10]
            today = datetime.now().strftime("%Y-%m-%d")
            if generated != today:
                logger.info(f"screener.json is from {generated} — using static universe")
                return None
            candidates = data.get("candidates", [])[:max_candidates]
            regime = data.get("market_regime", {}).get("regime", "unknown")
            leaders = ", ".join(data.get("sector_leaders", [])[:3])
            logger.info(
                f"Screener loaded: {len(candidates)} RS-ranked candidates "
                f"| regime={regime} | leaders={leaders}"
            )
            return candidates
        except Exception as e:
            logger.warning(f"Could not load screener.json: {e}")
            return None

    # =========================================================================
    # PRE-ENTRY FILTERS
    # =========================================================================

    def _check_earnings_proximity(self, symbol: str) -> tuple:
        """
        Returns (skip: bool, reason: str).
        Blocks entry if earnings are within EntryRules.earnings_blackout_days.
        """
        blackout = self.engine.config.entry_rules.earnings_blackout_days
        try:
            import yfinance as yf
            cal = yf.Ticker(symbol).calendar
            if cal is not None:
                dates = cal.get("Earnings Date") if hasattr(cal, "get") else None
                if dates is None and hasattr(cal, "T"):
                    row = cal.T.get("Earnings Date") if "Earnings Date" in cal.T.columns else None
                    dates = row.values.tolist() if row is not None else None
                if dates:
                    from datetime import timezone
                    for ed in (dates if isinstance(dates, list) else [dates]):
                        try:
                            if hasattr(ed, "date"):
                                ed = ed.date()
                            days = (ed - date.today()).days
                            if 0 <= days <= blackout:
                                return True, f"Earnings in {days}d — blackout {blackout}d window"
                        except Exception:
                            pass
        except Exception:
            pass
        return False, ""

    def _check_rsi_at_entry(self, price_bars) -> tuple:
        """
        Returns (skip: bool, reason: str).
        Blocks entry if RSI is above the overbought threshold.
        """
        threshold = self.engine.config.entry_rules.rsi_overbought_block
        if not price_bars or len(price_bars) < 15:
            return False, ""
        try:
            from momentum import MomentumAnalyzer
            analyzer = MomentumAnalyzer()
            closes = [b.close for b in price_bars]
            rsi_result = analyzer._calculate_rsi(closes)
            if rsi_result and rsi_result.rsi > threshold:
                return True, f"RSI {rsi_result.rsi:.0f} > {threshold} — overbought, wait for pullback"
        except Exception:
            pass
        return False, ""

    def _get_sector_allocations(self) -> dict:
        """
        Returns dict of sector → current allocation fraction of portfolio.
        Uses SECTOR_MAP from config and live Alpaca positions.
        """
        from config import SECTOR_MAP
        if not self.broker:
            return {}
        try:
            positions = self.broker.get_positions() or []
            total_value = self.portfolio.total_value or 1
            # Build symbol → sector lookup
            sym_to_sector = {}
            for sector, syms in SECTOR_MAP.items():
                for s in syms:
                    sym_to_sector[s] = sector

            allocations = {}
            for p in positions:
                sector = sym_to_sector.get(p["symbol"], "other")
                allocations[sector] = allocations.get(sector, 0) + float(p["market_value"])

            return {s: v / total_value for s, v in allocations.items()}
        except Exception:
            return {}

    def _check_sector_concentration(self, sector: str, allocations: dict) -> tuple:
        """
        Returns (skip: bool, reason: str).
        Blocks entry if adding to this sector would exceed max_sector_allocation.
        """
        if not sector:
            return False, ""
        max_alloc = self.engine.config.entry_rules.max_sector_allocation
        # Map display sector name to config sector key
        sector_key = sector.lower().replace(" ", "_").replace("-", "_")
        current = max(
            allocations.get(sector_key, 0),
            allocations.get(sector.lower(), 0)
        )
        if current >= max_alloc:
            return True, (
                f"Sector '{sector}' already at {current*100:.0f}% "
                f"(max {max_alloc*100:.0f}%)"
            )
        return False, ""

    def _scale_size_by_momentum(self, decision_dict: dict) -> dict:
        """
        Scale position shares up or down based on trend_score.
        Higher momentum score → more capital allocated within portfolio limits.

        Tiers:
          score >= 85 : 1.3× (strong trend, overweight)
          score >= 75 : 1.0× (baseline)
          score >= 65 : 0.75× (marginal trend, underweight)
          score <  65 : 0.60× (weak, minimum size)
        """
        score = decision_dict.get("trend_score") or 70
        shares = decision_dict.get("shares") or 0
        limit_price = decision_dict.get("limit_price") or 0

        if score >= 85:
            multiplier = 1.30
        elif score >= 75:
            multiplier = 1.00
        elif score >= 65:
            multiplier = 0.75
        else:
            multiplier = 0.60

        if multiplier != 1.0 and shares > 0:
            new_shares = max(1, round(shares * multiplier))
            decision_dict["shares"] = new_shares
            decision_dict["position_value"] = round(new_shares * limit_price, 2)
            decision_dict["momentum_scale"] = multiplier

        return decision_dict

    # =========================================================================
    # TRADE LOG
    # =========================================================================

    def _log_trade(self, action: str, symbol: str, shares: int, price: float,
                   stop_loss: float = None, trend_score: int = None,
                   confidence: float = None, exit_reason: str = None,
                   trade_id: str = None):
        """
        Append-only trade log. Records every entry and exit with outcome data.
        Stored in docs/data/trades.json — the feedback loop foundation.
        """
        try:
            trades_file = Path(__file__).parent / "docs" / "data" / "trades.json"
            trades = json.loads(trades_file.read_text()) if trades_file.exists() else []

            today = datetime.now().strftime("%Y-%m-%d")
            ts    = datetime.now().isoformat()

            if action == "BUY":
                trades.append({
                    "trade_id":    f"TRD-{datetime.now().strftime('%Y%m%d%H%M%S')}-{symbol}",
                    "symbol":      symbol,
                    "status":      "OPEN",
                    "entry_date":  today,
                    "entry_ts":    ts,
                    "entry_price": round(price, 2),
                    "shares":      shares,
                    "stop_loss":   round(stop_loss, 2) if stop_loss else None,
                    "trend_score": trend_score,
                    "confidence":  confidence,
                    "exit_date":   None,
                    "exit_price":  None,
                    "exit_reason": None,
                    "pnl_usd":     None,
                    "pnl_pct":     None,
                    "hold_days":   None,
                })
                logger.info(f"Trade logged: BUY {shares} {symbol} @ ${price:.2f}")

            elif action in ("SELL", "EXIT"):
                # Find the matching open trade and close it
                for t in reversed(trades):
                    if t["symbol"] == symbol and t["status"] == "OPEN":
                        entry = t["entry_price"]
                        pnl_pct = ((price - entry) / entry) * 100 if entry else 0
                        pnl_usd = (price - entry) * t["shares"]
                        entry_dt = date.fromisoformat(t["entry_date"]) if t.get("entry_date") else date.today()
                        hold_days = (date.today() - entry_dt).days

                        t.update({
                            "status":      "CLOSED",
                            "exit_date":   today,
                            "exit_ts":     ts,
                            "exit_price":  round(price, 2),
                            "exit_reason": exit_reason or "UNKNOWN",
                            "pnl_usd":     round(pnl_usd, 2),
                            "pnl_pct":     round(pnl_pct, 2),
                            "hold_days":   hold_days,
                        })
                        logger.info(
                            f"Trade closed: {symbol} @ ${price:.2f} | "
                            f"P&L: {pnl_pct:+.1f}% | Reason: {exit_reason}"
                        )
                        break

            trades_file.write_text(json.dumps(trades, indent=2))

        except Exception as e:
            logger.warning(f"Could not log trade for {symbol}: {e}")

    def _evaluate_exits(self, execute: bool = False) -> dict:
        """
        Evaluate ALL open Alpaca positions for exit signals and trailing stop updates.

        Runs before entry screening every session. Checks positions the model
        doesn't even track (e.g. old strategy holds) — no position is invisible.

        Exit triggers:
          HARD EXIT  — price closed below 50-day MA (trend gate violated)
          TRAIL UPD  — position up 25%+: raise stop to break-even
                     — position up 50%+: trail at 15% below current price
                     — position up 100%+: trail at 10% below current price

        Returns a summary dict logged to run results and dashboard.
        """
        import yfinance as yf

        result = {
            "exits_triggered": [],
            "stops_updated": [],
            "positions_checked": 0,
            "mode": "EXECUTE" if execute else "DRY-RUN",
        }

        if not self.broker:
            result["error"] = "Alpaca not available"
            return result

        positions = self.broker.get_positions() or []
        result["positions_checked"] = len(positions)

        if not positions:
            return result

        for pos in positions:
            sym         = pos["symbol"]
            qty         = int(pos["qty"])
            avg_cost    = float(pos["avg_entry_price"])
            current     = float(pos["current_price"])
            pnl_pct     = float(pos["unrealized_plpc"]) * 100

            # Fetch price history for MA calculation
            try:
                hist   = yf.Ticker(sym).history(period="1y")
                closes = hist["Close"].values
                if len(closes) < 50:
                    continue
                sma50  = float(closes[-50:].mean())
                sma200 = float(closes[-200:].mean()) if len(closes) >= 200 else None
            except Exception as e:
                logger.warning(f"Could not fetch data for {sym}: {e}")
                continue

            # ── Hard exit: price below 50MA ─────────────────────────────────
            if current < sma50:
                pct_below = (sma50 - current) / sma50 * 100
                action = {
                    "symbol":    sym,
                    "trigger":   "PRICE_BELOW_50MA",
                    "current":   round(current, 2),
                    "sma50":     round(sma50, 2),
                    "pct_below": round(pct_below, 2),
                    "pnl_pct":   round(pnl_pct, 2),
                    "qty":       qty,
                    "executed":  False,
                    "reason":    f"Price ${current:.2f} is {pct_below:.1f}% below 50MA ${sma50:.2f}",
                }
                if execute:
                    try:
                        close_result = self.broker.close_position(sym)
                        action["executed"] = "error" not in close_result
                        action["order_id"] = close_result.get("id")
                        logger.info(
                            f"EXIT EXECUTED: {sym} — price below 50MA "
                            f"(${current:.2f} < ${sma50:.2f}) | P&L: {pnl_pct:+.1f}%"
                        )
                        if action["executed"]:
                            self._log_trade(
                                action="EXIT",
                                symbol=sym,
                                shares=qty,
                                price=current,
                                exit_reason="PRICE_BELOW_50MA",
                            )
                    except Exception as e:
                        action["error"] = str(e)
                else:
                    logger.info(
                        f"EXIT SIGNAL (dry-run): {sym} — price ${current:.2f} "
                        f"below 50MA ${sma50:.2f} | P&L: {pnl_pct:+.1f}%"
                    )
                result["exits_triggered"].append(action)
                continue  # Skip stop update — position is being closed

            # ── Trailing stop update ─────────────────────────────────────────
            # Only update if position is in profit AND above 50MA
            if pnl_pct >= 25:
                if pnl_pct >= 100:
                    new_stop = round(current * 0.90, 2)   # 10% trail
                    tier     = "100%+ gain → 10% trail"
                elif pnl_pct >= 50:
                    new_stop = round(current * 0.85, 2)   # 15% trail
                    tier     = "50%+ gain → 15% trail"
                else:
                    new_stop = round(avg_cost * 1.01, 2)  # break-even + 1%
                    tier     = "25%+ gain → break-even stop"

                stop_action = {
                    "symbol":    sym,
                    "trigger":   "TRAILING_STOP_UPDATE",
                    "new_stop":  new_stop,
                    "pnl_pct":   round(pnl_pct, 2),
                    "current":   round(current, 2),
                    "tier":      tier,
                    "executed":  False,
                }

                if execute:
                    try:
                        # Cancel any existing stop sell orders for this symbol
                        open_orders = self.broker.get_orders(
                            status="open", symbols=[sym]
                        )
                        for o in open_orders:
                            if o.get("side") == "sell" and o.get("type") in (
                                "stop", "stop_limit", "trailing_stop"
                            ):
                                self.broker.cancel_order(o["id"])
                                logger.info(f"Cancelled stop order {o['id']} for {sym}")

                        # Place new stop order
                        stop_result = self.broker.place_order(
                            symbol=sym,
                            qty=qty,
                            side="sell",
                            order_type="stop",
                            stop_price=new_stop,
                            time_in_force="gtc",
                        )
                        stop_action["executed"] = "error" not in stop_result
                        stop_action["order_id"] = stop_result.get("id")
                        logger.info(
                            f"STOP UPDATED: {sym} → ${new_stop:.2f} ({tier}) | "
                            f"P&L: {pnl_pct:+.1f}%"
                        )
                    except Exception as e:
                        stop_action["error"] = str(e)
                else:
                    logger.info(
                        f"STOP UPDATE (dry-run): {sym} → ${new_stop:.2f} "
                        f"({tier}) | P&L: {pnl_pct:+.1f}%"
                    )
                result["stops_updated"].append(stop_action)

        exits   = len(result["exits_triggered"])
        updates = len(result["stops_updated"])
        logger.info(
            f"Exit evaluation complete: {exits} exits triggered, "
            f"{updates} stops updated ({result['mode']})"
        )
        return result

    def _execute_opportunities(self,
                               opportunities: List[Dict],
                               execute: bool,
                               min_confidence: float) -> Dict:
        """
        Submit qualifying BUY orders to Alpaca, or log dry-run output.

        Safety gates (all must pass per opportunity):
          1. --execute flag is set
          2. Alpaca is available and connected
          3. Market is currently open
          4. Signal confidence >= min_confidence
          5. Valid limit_price and shares > 0
        """
        if not opportunities:
            return {"status": "skipped", "reason": "No BUY signals to execute"}

        if not execute:
            # Dry-run: log what would be submitted
            qualified = [
                o for o in opportunities
                if o.get("confidence", 0) >= min_confidence
                and o.get("limit_price")
                and o.get("shares", 0) > 0
            ]
            logger.info(
                f"DRY-RUN: {len(qualified)}/{len(opportunities)} signals meet "
                f"confidence >= {min_confidence}. Run with --execute to submit orders."
            )
            for o in qualified:
                logger.info(
                    f"  Would submit: BUY {o['shares']} {o['symbol']} "
                    f"@ ${o['limit_price']:.2f} | stop=${o.get('stop_loss', 0):.2f} | "
                    f"confidence={o['confidence']:.2f}"
                )
            return {
                "status": "dry_run",
                "would_submit": len(qualified),
                "skipped_low_confidence": len(opportunities) - len(qualified)
            }

        # Live execution
        if not ALPACA_AVAILABLE or not self.broker:
            return {"status": "error", "reason": "Alpaca not available - check API keys"}

        # Gate 1: market must be open
        try:
            if not self.broker.is_market_open():
                logger.warning("Execution skipped: market is closed")
                return {"status": "skipped", "reason": "Market is closed"}
        except Exception as e:
            return {"status": "error", "reason": f"Could not check market status: {e}"}

        executor = OrderExecutor(self.broker)
        execution_results = []
        submitted = 0
        skipped = 0

        for opp in opportunities:
            symbol = opp["symbol"]
            confidence = opp.get("confidence", 0)
            limit_price = opp.get("limit_price")
            shares = opp.get("shares", 0)

            # Gate 2: confidence threshold
            if confidence < min_confidence:
                logger.info(f"Skipping {symbol}: confidence {confidence:.2f} < {min_confidence}")
                skipped += 1
                execution_results.append({
                    "symbol": symbol,
                    "status": "skipped",
                    "reason": f"confidence {confidence:.2f} below threshold {min_confidence}"
                })
                continue

            # Gate 3: valid order parameters
            if not limit_price or shares <= 0:
                logger.warning(f"Skipping {symbol}: invalid limit_price or shares")
                skipped += 1
                execution_results.append({
                    "symbol": symbol,
                    "status": "skipped",
                    "reason": "invalid limit_price or shares"
                })
                continue

            # Submit order
            try:
                result = executor.execute_decision(opp)
                status = result.get("status", "error")

                if status == "submitted":
                    submitted += 1
                    logger.info(
                        f"ORDER SUBMITTED: BUY {shares} {symbol} "
                        f"@ ${limit_price:.2f} | stop=${opp.get('stop_loss', 0):.2f} | "
                        f"order_id={result.get('order_id')}"
                    )
                    self._log_trade(
                        action="BUY",
                        symbol=symbol,
                        shares=shares,
                        price=limit_price,
                        stop_loss=opp.get("stop_loss"),
                        trend_score=opp.get("trend_score"),
                        confidence=confidence,
                    )
                else:
                    logger.warning(f"Order not submitted for {symbol}: {result.get('reason')}")

                execution_results.append({
                    "symbol": symbol,
                    "shares": shares,
                    "limit_price": limit_price,
                    "stop_loss": opp.get("stop_loss"),
                    "confidence": confidence,
                    "status": status,
                    "order_id": result.get("order_id"),
                    "reason": result.get("reason")
                })

            except Exception as e:
                logger.error(f"Error executing order for {symbol}: {e}")
                execution_results.append({
                    "symbol": symbol,
                    "status": "error",
                    "reason": str(e)
                })

        return {
            "status": "executed",
            "submitted": submitted,
            "skipped": skipped,
            "total_attempted": len(opportunities),
            "details": execution_results
        }

    def _decision_to_dict(self, decision) -> Dict:
        """Convert Decision object to JSON-serializable dict."""
        return {
            "decision_id": decision.decision_id,
            "symbol": decision.symbol,
            "action": decision.action,
            "shares": decision.shares,
            "limit_price": decision.limit_price,
            "position_value": decision.position_value,
            "position_size_pct": decision.position_size_pct,
            "stop_loss": decision.stop_loss_price,
            "target_price": decision.target_price,
            "risk_reward_ratio": decision.risk_reward_ratio,
            "signal_strength": decision.signal.strength if decision.signal else 0,
            "confidence": decision.signal.confidence if decision.signal else 0,
            "trend_score": self._extract_trend_score(decision.supporting_reasons),
            "reasons": decision.supporting_reasons[:5],
            "risks": decision.risks[:3],
            "requires_confirmation": decision.requires_confirmation
        }

    def _extract_trend_score(self, reasons: List[str]) -> Optional[int]:
        """Extract trend score from supporting reasons."""
        for reason in reasons:
            if "Trend Score:" in reason:
                try:
                    score_part = reason.split(":")[1].split("/")[0]
                    return int(score_part.strip())
                except Exception:
                    pass
        return None


def main():
    parser = argparse.ArgumentParser(description="Daily Trading Analysis")

    parser.add_argument("--execute", action="store_true",
                        help="Submit orders to Alpaca (default: dry-run only)")
    parser.add_argument("--min-confidence", type=float, default=MIN_CONFIDENCE_DEFAULT,
                        help=f"Minimum signal confidence to execute (default: {MIN_CONFIDENCE_DEFAULT})")
    parser.add_argument("--mode", choices=["full", "quick", "symbols"], default="full",
                        help="Analysis mode")
    parser.add_argument("--symbols", type=str, default=None,
                        help="Comma-separated symbols to analyze")
    parser.add_argument("--max-candidates", type=int, default=30,
                        help="Max candidates to screen")
    parser.add_argument("--portfolio-value", type=float, default=100000,
                        help="Portfolio value for position sizing (used if Alpaca unavailable)")
    parser.add_argument("--output", type=str, default=None,
                        help="Write results to JSON file")
    parser.add_argument("--data-dir", type=str, default=None,
                        help="Data directory path")
    parser.add_argument("--quiet", action="store_true",
                        help="Suppress console output (log file still written)")

    args = parser.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",")] if args.symbols else None

    runner = DailyRunner(
        portfolio_value=args.portfolio_value,
        data_dir=args.data_dir
    )

    results = runner.run(
        mode=args.mode if not symbols else "symbols",
        symbols=symbols,
        max_candidates=args.max_candidates,
        execute=args.execute,
        min_confidence=args.min_confidence
    )

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(results, f, indent=2)
        if not args.quiet:
            print(f"Results written to {args.output}")

    if not args.quiet:
        print("\n" + "=" * 60)
        print("DAILY ANALYSIS SUMMARY")
        print("=" * 60)
        print(f"Run ID:    {results['run_id']}")
        print(f"Status:    {results['status']}")
        print(f"Mode:      {'EXECUTE' if args.execute else 'DRY-RUN'}")
        print(f"Duration:  {results.get('duration_seconds', 0):.1f}s")
        print()

        summary = results.get("summary", {})
        print(f"Candidates Screened:  {summary.get('candidates_screened', 0)}")
        print(f"Buy Signals:          {summary.get('buy_signals', 0)}")
        print(f"Hold Signals:         {summary.get('hold_signals', 0)}")
        print(f"Orders Submitted:     {summary.get('orders_submitted', 0)}")
        print(f"Total Investment:     ${summary.get('total_investment_recommended', 0):,.2f}")
        print()

        execution = results.get("execution", {})
        exec_status = execution.get("status", "")
        if exec_status == "dry_run":
            print(f"DRY-RUN: {execution.get('would_submit', 0)} orders would be submitted.")
            print("Run with --execute to place real orders.")
        elif exec_status == "executed":
            print(f"EXECUTION: {execution.get('submitted', 0)} orders submitted, "
                  f"{execution.get('skipped', 0)} skipped.")
        elif exec_status == "skipped":
            print(f"EXECUTION SKIPPED: {execution.get('reason', '')}")
        print()

        if results["opportunities"]:
            print("SIGNALS:")
            for opp in results["opportunities"]:
                marker = "SUBMITTED" if args.execute else "WOULD BUY"
                print(f"  [{marker}] {opp['symbol']}: {opp['shares']} shares "
                      f"@ ${opp.get('limit_price', 0):.2f}")
                print(f"    Stop: ${opp.get('stop_loss', 0):.2f} | "
                      f"Confidence: {opp.get('confidence', 0):.2f} | "
                      f"Strength: {opp.get('signal_strength', 0):.2f}")
        else:
            print("No BUY signals today.")

        print("=" * 60)

    return results


if __name__ == "__main__":
    main()
