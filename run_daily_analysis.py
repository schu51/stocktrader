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
from exit_logic import reconcile_phantom_trades
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


def classify_order_for_reconcile(status: str) -> str:
    """
    Decide whether an OPEN trade's BUY order indicates a phantom (never filled).

    Returns "cancel" or "keep".
      filled / partially_filled  → keep   (real position exists)
      canceled/expired/rejected/
        done_for_day/replaced     → cancel (DAY limit order never filled)
      anything else (new, accepted,
        pending, unknown, error)  → keep   (still working or can't tell — don't guess)
    """
    s = (status or "").lower()
    if s in ("filled", "partially_filled"):
        return "keep"
    if s in ("canceled", "cancelled", "expired", "rejected", "done_for_day", "replaced"):
        return "cancel"
    return "keep"


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

    def _build_portfolio_state(self) -> PortfolioState:
        """
        Build a fully-hydrated PortfolioState with Position objects from live Alpaca data.
        Called at run-time so positions reflect current reality.
        Unlocks: pre_trade_risk_check, check_positions_for_exit, assess_portfolio_risk.
        """
        from models import Position
        from config import PositionStatus

        if not self.broker:
            return self.portfolio

        account = self.broker.get_account()
        raw     = self.broker.get_positions() or []
        total_value = float(account.get("portfolio_value", self.portfolio.total_value))

        # Build stop-price map from active Alpaca stop/stop_limit/trailing_stop orders
        # so risk assessment knows which positions already have stop protection.
        stop_map: dict = {}
        try:
            for o in (self.broker.get_orders(status="open") or []):
                if o.get("side") == "sell" and o.get("type") in (
                    "stop", "stop_limit", "trailing_stop"
                ):
                    sym = o.get("symbol", "")
                    sp  = o.get("stop_price")
                    if sym and sp:
                        try:
                            stop_map[sym] = round(float(sp), 2)
                        except (TypeError, ValueError):
                            pass
        except Exception:
            pass

        positions = {}
        total_unrealized = 0.0
        for p in raw:
            mv  = float(p["market_value"])
            pnl = float(p["unrealized_pl"])
            total_unrealized += pnl
            pos = Position(
                symbol=p["symbol"],
                status=PositionStatus.OPEN,
                shares=int(p["qty"]),
                avg_cost=float(p["avg_entry_price"]),
                current_price=float(p["current_price"]),
                market_value=mv,
                unrealized_pnl=pnl,
                unrealized_pnl_pct=float(p["unrealized_plpc"]) * 100,
                current_allocation=mv / total_value if total_value > 0 else 0.0,
                stop_loss_price=stop_map.get(p["symbol"]),
            )
            positions[p["symbol"]] = pos

        cash = float(account.get("cash", self.portfolio.cash))
        bp   = float(account.get("buying_power", self.portfolio.buying_power))

        # Use cash (not buying_power) so the model never trades on margin.
        # buying_power in Alpaca margin accounts is 2-3× cash; using it as the
        # sizing constraint caused the account to go negative.
        available = max(0.0, cash) * 0.95

        return PortfolioState(
            timestamp=datetime.now(),
            total_value=total_value,
            cash=cash,
            invested=float(account.get("long_market_value", 0)),
            positions=positions,
            num_positions=len(positions),
            cash_allocation=cash / total_value if total_value > 0 else 0.0,
            available_cash=available,
            buying_power=bp,
            total_unrealized_pnl=total_unrealized,
        )

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
                        available_cash=max(0.0, float(account["cash"])) * 0.95
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
            "exits": {},
            "risk_assessment": {},
            "performers_update": [],
            "portfolio": {},
            "errors": []
        }

        try:
            # 0a. Reconcile phantom trades (unfilled DAY limit orders logged as OPEN)
            # before anything reads the trade log, so a later exit can't stamp a
            # phantom lot with a fabricated P&L.
            self._reconcile_phantom_trades()

            # 0b. Evaluate exits on ALL open positions before looking for new entries
            exit_results = self._evaluate_exits(execute=execute)
            results["exits"] = exit_results

            # Build rich portfolio state with real Position objects (unlocks engine methods)
            live_portfolio = self._build_portfolio_state()
            self.portfolio = live_portfolio  # keep instance state current for sizing calcs

            # Reconcile phantom OPEN trades — DAY limit orders that never filled
            # are cancelled by Alpaca at EOD without notifying trades.json, so
            # they otherwise sit OPEN forever and corrupt hold-day/exposure stats.
            try:
                trades_file = Path(__file__).parent / "docs" / "data" / "trades.json"
                if trades_file.exists():
                    trades = json.loads(trades_file.read_text())
                    n = reconcile_phantom_trades(trades, set(live_portfolio.positions.keys()))
                    if n:
                        trades_file.write_text(json.dumps(trades, indent=2))
                        logger.info(f"Reconciled {n} phantom OPEN trade(s) — order(s) never filled")
            except Exception as e:
                logger.warning(f"Trade log reconciliation failed: {e}")

            # Portfolio risk assessment
            try:
                risk = self.engine.risk_manager.assess_portfolio_risk(live_portfolio)
                results["risk_assessment"] = risk
                logger.info(
                    f"Portfolio risk: {risk.get('risk_level','?')} "
                    f"(score {risk.get('overall_risk_score', 0):.0f})"
                )
            except Exception as e:
                logger.warning(f"Risk assessment failed: {e}")
                results["risk_assessment"] = {}

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

                    # Bollinger Band overextended: skip if price in top 10% of band
                    bb_skip, bb_reason = self._check_bb_at_entry(price_bars)
                    if bb_skip:
                        holds.append({"symbol": symbol, "reason": bb_reason})
                        logger.info(f"Skipping {symbol}: {bb_reason}")
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

                    # Carry screener entry features through for trade logging
                    # (the learning agent regresses outcomes on these).
                    decision_dict["rs_rank"]      = candidate.get("rs_rank")
                    decision_dict["thesis_score"] = candidate.get("thesis_score")
                    decision_dict["thesis_grade"] = candidate.get("thesis_grade")
                    decision_dict["sector"]       = candidate.get("sector", "")

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
                opportunities, execute, min_confidence, portfolio=live_portfolio
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
            # If Alpaca is unavailable, preserve existing positions from last good run
            # rather than overwriting with an empty list.
            positions = []
            if self.broker:
                # Build stop-price map from active Alpaca stop/stop_limit orders
                stop_map: dict = {}
                try:
                    for o in (self.broker.get_orders(status="open") or []):
                        if o.get("side") == "sell" and o.get("type") in (
                            "stop", "stop_limit"
                        ):
                            sym = o.get("symbol", "")
                            sp  = o.get("stop_price")
                            if sym and sp:
                                try:
                                    stop_map[sym] = round(float(sp), 2)
                                except (TypeError, ValueError):
                                    pass
                except Exception:
                    pass

                for p in (self.broker.get_positions() or []):
                    positions.append({
                        "symbol": p["symbol"],
                        "qty": int(p["qty"]),
                        "avg_cost": round(float(p["avg_entry_price"]), 2),
                        "current_price": round(float(p["current_price"]), 2),
                        "market_value": round(float(p["market_value"]), 2),
                        "unrealized_pnl": round(float(p["unrealized_pl"]), 2),
                        "unrealized_pnl_pct": round(float(p["unrealized_plpc"]) * 100, 2),
                        "stop_loss": stop_map.get(p["symbol"]),
                    })
            else:
                # Alpaca unavailable — preserve last known positions so dashboard
                # doesn't show empty portfolio with $100k default value
                existing_latest = docs_dir / "latest.json"
                if existing_latest.exists():
                    try:
                        prev = json.loads(existing_latest.read_text())
                        positions = prev.get("positions", [])
                        logger.warning(
                            f"Alpaca unavailable — preserving {len(positions)} positions "
                            f"from last known state"
                        )
                    except Exception:
                        pass

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
                "exits": run_results.get("exits", {
                    "exits_triggered": [],
                    "stops_updated": [],
                    "bb_warnings": [],
                    "positions_checked": 0,
                    "mode": "DRY-RUN",
                }),
                "risk_assessment": run_results.get("risk_assessment", {}),
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
                # Multiple runs can hit the same day (manual workflow_dispatch
                # reruns). Accumulate signal/order counts instead of
                # overwriting, or a same-day rerun silently erases the
                # earlier run's activity from the dashboard history.
                prev = history[idx]
                entry["candidates_screened"] = max(prev.get("candidates_screened", 0), entry["candidates_screened"])
                entry["buy_signals"] += prev.get("buy_signals", 0)
                entry["orders_submitted"] += prev.get("orders_submitted", 0)
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
            if cal is None:
                return False, ""

            # yfinance returns inconsistent types across versions — normalise here.
            dates = []
            if isinstance(cal, dict):
                raw = cal.get("Earnings Date", [])
                dates = raw if isinstance(raw, list) else [raw]
            elif hasattr(cal, "columns") and "Earnings Date" in cal.columns:
                dates = cal["Earnings Date"].dropna().tolist()
            elif hasattr(cal, "index") and "Earnings Date" in cal.index:
                raw = cal.loc["Earnings Date"]
                dates = raw.tolist() if hasattr(raw, "tolist") else [raw]

            for ed in dates:
                try:
                    ed = ed.date() if hasattr(ed, "date") else date.fromisoformat(str(ed)[:10])
                    days = (ed - date.today()).days
                    if 0 <= days <= blackout:
                        return True, f"Earnings in {days}d — blackout {blackout}d window"
                except Exception:
                    continue
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

    def _check_bb_at_entry(self, price_bars) -> tuple:
        """
        Returns (skip: bool, reason: str).
        Blocks entry when price is in the top 10% of its Bollinger Band
        (%B > 0.90) — overextended, better to wait for a pullback toward
        the middle band before entering.

        Also returns the %B value for informational use even when not blocking.
        """
        if not price_bars or len(price_bars) < 20:
            return False, ""
        try:
            closes = [b.close for b in price_bars]
            bb_slice = closes[-20:]
            bb_middle = sum(bb_slice) / len(bb_slice)
            variance = sum((x - bb_middle) ** 2 for x in bb_slice) / len(bb_slice)
            bb_std    = variance ** 0.5
            bb_upper  = bb_middle + 2 * bb_std
            bb_lower  = bb_middle - 2 * bb_std
            price     = closes[-1]
            band_width = bb_upper - bb_lower

            if band_width <= 0:
                return False, ""

            pct_b = (price - bb_lower) / band_width

            if pct_b > 0.90:
                return True, (
                    f"BB overextended: %%B={pct_b:.2f} — price near upper band "
                    f"(${bb_upper:.2f}), wait for pullback to middle (${bb_middle:.2f})"
                )
        except Exception:
            pass
        return False, ""

    @staticmethod
    def _bb_middle(price_bars, period: int = 20) -> float:
        """Return the 20-day Bollinger Band middle (SMA) from price bars."""
        if not price_bars or len(price_bars) < period:
            return 0.0
        closes = [b.close for b in price_bars[-period:]]
        return sum(closes) / len(closes)

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
        Scale position shares by momentum score AND RS rank.

        Insight: the model was too diversified — 3% positions can't move the
        needle. Top RS names (90+) with strong scores deserve 1.5× sizing.
        Weak setups get cut. Capital follows conviction.

        Tiers:
          rs_rank >= 90 AND score >= 80 : 1.5× (elite momentum, overweight)
          score >= 85                   : 1.3× (strong, overweight)
          score >= 75                   : 1.0× (baseline)
          score >= 68                   : 0.75× (marginal)
          score <  68                   : 0.50× (weak — barely passes gate)
        """
        score    = decision_dict.get("trend_score") or 70
        rs_rank  = decision_dict.get("rs_rank") or 0
        shares   = decision_dict.get("shares") or 0
        limit_price = decision_dict.get("limit_price") or 0

        if rs_rank >= 90 and score >= 80:
            multiplier = 1.50   # Elite: size up meaningfully
        elif score >= 85:
            multiplier = 1.30
        elif score >= 75:
            multiplier = 1.00
        elif score >= 68:
            multiplier = 0.75
        else:
            multiplier = 0.50   # Barely passed — minimum sizing

        # Cash deployment boost: if >15% cash idle, deploy more aggressively
        # into high-conviction signals rather than letting it sit
        if self.portfolio.total_value > 0:
            cash_pct = self.portfolio.available_cash / self.portfolio.total_value
            if cash_pct > 0.15 and score >= 75 and multiplier >= 1.0:
                multiplier = min(multiplier * 1.20, 2.0)  # Up to 20% boost to deploy cash

        if multiplier != 1.0 and shares > 0:
            new_shares = max(1, round(shares * multiplier))
            decision_dict["shares"] = new_shares
            decision_dict["position_value"] = round(new_shares * limit_price, 2)
            decision_dict["momentum_scale"] = round(multiplier, 2)

        return decision_dict

    # =========================================================================
    # TRADE LOG
    # =========================================================================

    def _active_weight_version(self) -> int:
        """Read the active ranking-weight version for trade tagging. Defaults to 1."""
        try:
            wf = Path(__file__).parent / "docs" / "data" / "weights.json"
            if wf.exists():
                return int(json.loads(wf.read_text()).get("active", {}).get("version") or 1)
        except Exception:
            pass
        return 1

    def _reconcile_phantom_trades(self):
        """
        Cancel phantom OPEN trades whose BUY (DAY limit) order never filled.

        Without this, an unfilled limit order leaves a trade logged as OPEN
        forever. When the symbol later genuinely exits, _log_trade closes ALL
        its OPEN lots — stamping the phantom with a fabricated P&L that then
        corrupts the learning agent's regression.

        Deterministic path: any OPEN trade carrying an order_id is reconciled by
        querying Alpaca for that order's real status (see classify_order_for_reconcile).
        Trades without an order_id (logged before order_id tracking) are left
        untouched here — they are handled by a one-time data correction.
        """
        if not self.broker:
            return
        try:
            trades_file = Path(__file__).parent / "docs" / "data" / "trades.json"
            if not trades_file.exists():
                return
            trades = json.loads(trades_file.read_text())
        except Exception as e:
            logger.warning(f"Phantom reconcile: could not read trades.json: {e}")
            return

        cancelled = 0
        for t in trades:
            if t.get("status") != "OPEN" or not t.get("order_id"):
                continue
            try:
                order = self.broker.get_order(t["order_id"])
                status = order.get("status") if isinstance(order, dict) else None
                if classify_order_for_reconcile(status) == "cancel":
                    t["status"] = "CANCELLED"
                    t["exit_reason"] = "ORDER_NOT_FILLED"
                    cancelled += 1
                    logger.info(
                        f"Phantom reconcile: {t['symbol']} {t.get('entry_date')} "
                        f"order {t['order_id']} status={status} → CANCELLED"
                    )
            except Exception as e:
                logger.debug(f"Phantom reconcile: could not check {t.get('symbol')}: {e}")
                continue

        if cancelled:
            try:
                import os
                tmp = trades_file.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(trades, indent=2))
                os.replace(tmp, trades_file)
                logger.info(f"Phantom reconcile: cancelled {cancelled} unfilled trade(s)")
            except Exception as e:
                logger.warning(f"Phantom reconcile: could not write trades.json: {e}")

    def _log_trade(self, action: str, symbol: str, shares: int, price: float,
                   stop_loss: float = None, trend_score: int = None,
                   confidence: float = None, exit_reason: str = None,
                   trade_id: str = None, rs_rank: int = None,
                   thesis_score: float = None, thesis_grade: str = None,
                   sector: str = None, weight_version: int = None,
                   realized_pnl_pct: float = None, order_id: str = None):
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
                # Guard against duplicate entries when analysis runs multiple times per day
                # at the same fill price (same order re-logged on re-run).
                duplicate = any(
                    t["symbol"] == symbol
                    and t["status"] == "OPEN"
                    and t["entry_date"] == today
                    and t.get("entry_price") == round(price, 2)
                    for t in trades
                )
                if duplicate:
                    logger.info(f"Skipping duplicate trade log: {symbol} @ ${price:.2f} already open today")
                    return

                trades.append({
                    "trade_id":    f"TRD-{datetime.now().strftime('%Y%m%d%H%M%S')}-{symbol}",
                    "symbol":      symbol,
                    "status":      "OPEN",
                    "entry_date":  today,
                    "entry_ts":    ts,
                    "entry_price": round(price, 2),
                    "shares":      shares,
                    "stop_loss":   round(stop_loss, 2) if stop_loss else None,
                    "trend_score":    trend_score,
                    "confidence":     confidence,
                    "rs_rank":        rs_rank,
                    "thesis_score":   thesis_score,
                    "thesis_grade":   thesis_grade,
                    "sector":         sector,
                    "weight_version": weight_version,
                    "order_id":       order_id,
                    "exit_date":   None,
                    "exit_price":  None,
                    "exit_reason": None,
                    "pnl_usd":     None,
                    "pnl_pct":     None,
                    "hold_days":   None,
                })
                logger.info(f"Trade logged: BUY {shares} {symbol} @ ${price:.2f}")

            elif action in ("SELL", "EXIT"):
                # Close all open lots for the symbol (full position exit)
                closed_count = 0
                for t in trades:
                    if t["symbol"] == symbol and t["status"] == "OPEN":
                        entry = t["entry_price"]
                        # Prefer the realized P&L from Alpaca's actual cost basis
                        # (the figure the exit trigger fired on). Recomputing from
                        # the logged entry_price diverges from reality when Alpaca's
                        # blended avg cost differs from the per-lot limit price —
                        # which corrupts the pnl_pct the learning agent regresses on.
                        if realized_pnl_pct is not None:
                            pnl_pct = realized_pnl_pct
                            pnl_usd = (realized_pnl_pct / 100.0) * entry * t["shares"] if entry else 0
                        else:
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
                        closed_count += 1
                if closed_count == 0:
                    logger.warning(f"No open trade found to close for {symbol}")

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
                                realized_pnl_pct=pnl_pct,
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

            # ── Thesis-invalidation check ────────────────────────────────────
            # Exit when the MOMENTUM THESIS breaks — not on a clock.
            # A stock down 8% in a week but still above 50MA > 200MA with
            # strong RS is in a healthy pullback within a trend. That's
            # normal. We don't exit on that. We exit when:
            #   1. 50MA alignment breaks (already caught above)
            #   2. Position has deteriorated for 20+ days with no trend intact
            #   3. Loss exceeds 15% (hard stop — thesis was clearly wrong)
            #
            # Short-term time-stops on momentum positions destroy alpha.
            # Give thesis time to play out (20 trading days = ~4 weeks).
            entry_date = None
            try:
                trades_file = Path(__file__).parent / "docs" / "data" / "trades.json"
                if trades_file.exists():
                    trades = json.loads(trades_file.read_text())
                    open_trade = next(
                        (t for t in reversed(trades)
                         if t.get("symbol") == sym and t.get("status") == "OPEN"),
                        None
                    )
                    if open_trade and open_trade.get("entry_date"):
                        entry_date = date.fromisoformat(open_trade["entry_date"])
            except Exception:
                pass

            if entry_date:
                hold_days = (date.today() - entry_date).days

                # Hard stop: down >15% at any point = thesis was wrong at entry
                if pnl_pct < -15:
                    action = {
                        "symbol":    sym,
                        "trigger":   "HARD_LOSS_STOP",
                        "current":   round(current, 2),
                        "pnl_pct":   round(pnl_pct, 2),
                        "hold_days": hold_days,
                        "qty":       qty,
                        "executed":  False,
                        "reason":    f"Down {pnl_pct:.1f}% — entry thesis was wrong, taking loss",
                    }
                    if execute:
                        try:
                            close_result = self.broker.close_position(sym)
                            action["executed"] = "error" not in close_result
                            action["order_id"] = close_result.get("id")
                            logger.info(
                                f"HARD-STOP EXIT: {sym} — {pnl_pct:+.1f}% after "
                                f"{hold_days}d — entry thesis invalidated"
                            )
                            if action["executed"]:
                                self._log_trade("EXIT", sym, qty, current,
                                                exit_reason="HARD_LOSS_STOP",
                                                realized_pnl_pct=pnl_pct)
                        except Exception as e:
                            action["error"] = str(e)
                    else:
                        logger.info(
                            f"HARD-STOP (dry-run): {sym} — {pnl_pct:+.1f}% "
                            f"after {hold_days}d — would close"
                        )
                    result["exits_triggered"].append(action)
                    continue

                # Long thesis review: held 20+ days AND still >8% underwater
                # AND trend shows no recovery (50MA still below entry price).
                # This is not a pullback — this is a failed thesis.
                if hold_days >= 20 and pnl_pct < -8:
                    if sma50 and sma50 < avg_cost:
                        action = {
                            "symbol":    sym,
                            "trigger":   "THESIS_FAILED",
                            "current":   round(current, 2),
                            "pnl_pct":   round(pnl_pct, 2),
                            "hold_days": hold_days,
                            "qty":       qty,
                            "executed":  False,
                            "reason":    (
                                f"Down {pnl_pct:.1f}% after {hold_days} days — "
                                f"50MA (${sma50:.2f}) still below entry (${avg_cost:.2f}), "
                                f"trend never recovered"
                            ),
                        }
                        if execute:
                            try:
                                close_result = self.broker.close_position(sym)
                                action["executed"] = "error" not in close_result
                                action["order_id"] = close_result.get("id")
                                logger.info(
                                    f"THESIS-FAILED EXIT: {sym} — {hold_days}d, "
                                    f"{pnl_pct:+.1f}%, 50MA never recovered"
                                )
                                if action["executed"]:
                                    self._log_trade("EXIT", sym, qty, current,
                                                    exit_reason="THESIS_FAILED",
                                                    realized_pnl_pct=pnl_pct)
                            except Exception as e:
                                action["error"] = str(e)
                        else:
                            logger.info(
                                f"THESIS-FAILED (dry-run): {sym} — {hold_days}d, "
                                f"{pnl_pct:+.1f}% — would close"
                            )
                        result["exits_triggered"].append(action)
                        continue

            # ── Trailing stop update ─────────────────────────────────────────
            # Tightened tiers: protect gains more aggressively as they compound.
            if pnl_pct >= 25:
                if pnl_pct >= 100:
                    new_stop = round(current * 0.92, 2)   # 8% trail (tightened from 10%)
                    tier     = "100%+ gain → 8% trail"
                elif pnl_pct >= 50:
                    new_stop = round(current * 0.90, 2)   # 10% trail (tightened from 15%)
                    tier     = "50%+ gain → 10% trail"
                else:
                    new_stop = round(avg_cost * 1.015, 2) # break-even + 1.5%
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

        # Bollinger Band middle band monitor: price below 20-day SMA on a
        # profitable position → flag for potential reduction (not auto-close)
        already_flagged = {e["symbol"] for e in result["exits_triggered"]}
        for pos in positions:
            sym = pos["symbol"]
            if sym in already_flagged:
                continue
            # unrealized_plpc from Alpaca is a decimal (0.15 = +15%); convert to pct.
            # unrealized_pnl_pct from our own position dict is already a percentage.
            if "unrealized_plpc" in pos:
                pnl_pct = float(pos["unrealized_plpc"]) * 100
            else:
                pnl_pct = float(pos.get("unrealized_pnl_pct", 0))
            if pnl_pct < 10:  # Only monitor profitable positions worth protecting
                continue
            try:
                import yfinance as yf
                hist = yf.Ticker(sym).history(period="60d")
                closes = hist["Close"].values
                if len(closes) < 20:
                    continue
                bb_mid = float(closes[-20:].mean())
                current = float(pos.get("current_price", closes[-1]))
                if current < bb_mid:
                    result["bb_warnings"] = result.get("bb_warnings", [])
                    result["bb_warnings"].append({
                        "symbol":   sym,
                        "trigger":  "MIDDLE_BAND_CROSS",
                        "reason":   f"Price ${current:.2f} crossed below BB middle ${bb_mid:.2f} — consider reducing",
                        "pnl_pct":  round(pnl_pct, 2),
                        "current":  round(current, 2),
                        "bb_middle": round(bb_mid, 2),
                    })
                    logger.info(
                        f"BB WARNING: {sym} below middle band (${current:.2f} < ${bb_mid:.2f}) "
                        f"| P&L: {pnl_pct:+.1f}% — consider reducing position"
                    )
            except Exception:
                continue

        # Engine-level exit checks: partial profit taking (40%+ gain),
        # time stop (90 days), earnings proximity reduction
        already_exited = {e["symbol"] for e in result["exits_triggered"]}
        try:
            live_portfolio = self._build_portfolio_state()
            current_prices = {p["symbol"]: float(p["current_price"]) for p in positions}
            engine_decisions = self.engine.check_positions_for_exit(
                portfolio=live_portfolio,
                current_prices=current_prices,
                earnings_calendar={},
            )
            for dec in [d for d in engine_decisions if d.symbol not in already_exited]:
                pos_obj = live_portfolio.positions.get(dec.symbol)
                pnl_pct = pos_obj.unrealized_pnl_pct if pos_obj else 0
                entry = {
                    "symbol":  dec.symbol,
                    "trigger": dec.decision_type.value,
                    "reason":  dec.primary_reason,
                    "pnl_pct": round(pnl_pct, 2),
                    "qty":     dec.shares,
                    "executed": False,
                }
                if execute and dec.action == "SELL":
                    try:
                        # Cancel any open stop orders first — they reserve shares and
                        # cause Alpaca 403 "insufficient qty" when we try to sell a partial.
                        open_orders = self.broker.get_orders(status="open", symbols=[dec.symbol])
                        for o in open_orders:
                            if o.get("side") == "sell" and o.get("type") in (
                                "stop", "stop_limit", "trailing_stop"
                            ):
                                self.broker.cancel_order(o["id"])

                        r = self.broker.close_position(dec.symbol, qty=dec.shares)
                        entry["executed"] = "error" not in r
                        entry["order_id"] = r.get("id")
                        if entry["executed"]:
                            self._log_trade(
                                action="EXIT",
                                symbol=dec.symbol,
                                shares=dec.shares,
                                price=current_prices.get(dec.symbol, 0),
                                exit_reason=dec.decision_type.value,
                                realized_pnl_pct=pnl_pct,
                            )
                            logger.info(
                                f"ENGINE EXIT: {dec.symbol} — {dec.primary_reason} "
                                f"| P&L: {pnl_pct:+.1f}%"
                            )

                            # Re-place stop for the residual position.
                            # Cancelling the stop was required to free qty for the
                            # partial close; without this the remaining shares are
                            # completely unprotected until the next daily run.
                            remaining = (pos_obj.shares if pos_obj else 0) - dec.shares
                            cur_price  = current_prices.get(dec.symbol, 0)
                            if remaining > 0 and cur_price > 0:
                                residual_stop = round(cur_price * 0.90, 2)  # 10% trail
                                try:
                                    self.broker.place_order(
                                        symbol=dec.symbol,
                                        qty=remaining,
                                        side="sell",
                                        order_type="stop",
                                        stop_price=residual_stop,
                                        time_in_force="gtc",
                                    )
                                    logger.info(
                                        f"RESIDUAL STOP: {dec.symbol} {remaining} shares "
                                        f"@ ${residual_stop:.2f} (10% trail, post-partial-exit)"
                                    )
                                except Exception as se:
                                    logger.warning(
                                        f"Could not place residual stop for {dec.symbol}: {se}"
                                    )
                    except Exception as e:
                        entry["error"] = str(e)
                else:
                    logger.info(
                        f"ENGINE EXIT (dry-run): {dec.symbol} — {dec.primary_reason} "
                        f"| P&L: {pnl_pct:+.1f}%"
                    )
                result["exits_triggered"].append(entry)
        except Exception as e:
            logger.warning(f"engine.check_positions_for_exit failed: {e}")

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
                               min_confidence: float,
                               portfolio: PortfolioState = None) -> Dict:
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
                if limit_price and shares == 0:
                    at_cap = self.portfolio.num_positions >= self.engine.config.portfolio_constraints.max_positions
                    reason = (
                        f"position size calculated as 0 — portfolio at max capacity "
                        f"({self.portfolio.num_positions}/{self.engine.config.portfolio_constraints.max_positions} positions)"
                        if at_cap else
                        "position size calculated as 0 — insufficient cash or constraints"
                    )
                else:
                    reason = "invalid limit_price or shares"
                logger.warning(f"Skipping {symbol}: {reason}")
                skipped += 1
                execution_results.append({
                    "symbol": symbol,
                    "status": "skipped",
                    "reason": reason
                })
                continue

            # Gate 4: live ask price override (use Alpaca ask instead of stale yfinance close)
            if self.broker:
                try:
                    quote = self.broker.get_latest_quote(symbol)
                    # BUY limit orders must be priced at (or above) the ask to be
                    # marketable. Pricing at the bid — the previous behavior — meant
                    # the order only filled if the market dropped to meet it, which
                    # rarely happens on a momentum entry and left most signals
                    # unfilled (see trades.json: ORDER_NOT_FILLED cancellations).
                    ask = float(quote.get("ap", 0) or quote.get("ask_price", 0) or 0)
                    if ask > 0:
                        # Recalculate stop_loss to maintain the same % distance from the
                        # actual fill price. Without this, a stale yfinance close > ask
                        # causes the stop to land above the entry price.
                        orig_limit = limit_price
                        stop_loss_orig = opp.get("stop_loss")
                        if stop_loss_orig and orig_limit > 0 and orig_limit > stop_loss_orig:
                            stop_pct = (orig_limit - stop_loss_orig) / orig_limit
                            opp["stop_loss"] = round(ask * (1 - stop_pct), 2)
                        limit_price = round(ask, 2)
                        opp["limit_price"] = limit_price
                except Exception:
                    pass  # keep yfinance close as fallback

            # Gate 5: pre-trade risk check
            portfolio_for_check = portfolio or self.portfolio
            try:
                risk_check = self.engine.risk_manager.pre_trade_risk_check(
                    symbol=symbol,
                    action="BUY",
                    shares=shares,
                    price=limit_price,
                    portfolio=portfolio_for_check,
                )
                if not risk_check.get("approved", True):
                    failed = [
                        c["message"] for c in risk_check.get("checks", [])
                        if c.get("status") == "FAILED"
                    ]
                    reason = "; ".join(failed) or "risk check failed"
                    logger.info(f"Skipping {symbol}: risk check blocked — {reason}")
                    skipped += 1
                    execution_results.append({
                        "symbol": symbol,
                        "status": "skipped",
                        "reason": f"risk: {reason}",
                    })
                    continue
            except Exception as e:
                logger.warning(f"Risk check raised exception for {symbol}: {e} — skipping for safety")
                skipped += 1
                execution_results.append({
                    "symbol": symbol, "status": "skipped",
                    "reason": f"risk check error: {e}"
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
                        rs_rank=opp.get("rs_rank"),
                        thesis_score=opp.get("thesis_score"),
                        thesis_grade=opp.get("thesis_grade"),
                        sector=opp.get("sector"),
                        weight_version=self._active_weight_version(),
                        order_id=result.get("order_id"),
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
