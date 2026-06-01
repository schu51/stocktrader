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
            # 1. Get candidates
            if symbols:
                candidates = [{"symbol": s.upper(), "source": "manual"} for s in symbols]
            elif mode == "quick":
                candidates = self.screener.get_screening_candidates(max_candidates=15)
            else:
                candidates = self.screener.get_screening_candidates(max_candidates=max_candidates)

            logger.info(f"Screening {len(candidates)} candidates")

            # 2. Generate signals for each candidate
            opportunities = []
            holds = []
            errors = []

            for candidate in candidates:
                symbol = candidate["symbol"]

                try:
                    research = self.research_scores.get(symbol)

                    # Fetch live market data via yfinance (fallback when DataOrchestrator unavailable)
                    market_snapshot, price_bars = fetch_market_data(symbol)

                    decision = self.engine.evaluate_entry(
                        symbol=symbol,
                        portfolio=self.portfolio,
                        research_score=research,
                        market_snapshot=market_snapshot,
                        price_history=price_bars,
                        auto_fetch=bool(self.orchestrator)
                    )

                    decision_dict = self._decision_to_dict(decision)

                    if decision.action == "BUY":
                        opportunities.append(decision_dict)
                        self.screener.track_performer(
                            symbol=symbol,
                            score=decision.signal.strength if decision.signal else 0,
                            signal="BUY",
                            sector=candidate.get("sector", ""),
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

        return results

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
