#!/usr/bin/env python3
"""
Daily Analysis Runner
=====================

Main entry point for the trading agent. Designed to be called by n8n.

Features:
- Screens universe for opportunities
- Generates trading signals with enhanced momentum
- Outputs JSON for n8n consumption
- Tracks persistent performers
- Supports different run modes

Usage:
    # Full analysis (default)
    python run_daily_analysis.py
    
    # Quick scan (faster, fewer candidates)
    python run_daily_analysis.py --mode quick
    
    # Specific symbols only
    python run_daily_analysis.py --symbols TOST,VRT,S
    
    # Output to file (for n8n)
    python run_daily_analysis.py --output /path/to/output.json

Output Format (JSON):
    {
        "run_id": "20241129_093000",
        "timestamp": "2024-11-29T09:30:00",
        "status": "success",
        "summary": {
            "candidates_screened": 25,
            "buy_signals": 3,
            "hold_signals": 22,
            "total_investment_recommended": 8500.00
        },
        "opportunities": [
            {
                "symbol": "TOST",
                "action": "BUY",
                "shares": 94,
                "limit_price": 42.50,
                "stop_loss": 34.00,
                "position_size_pct": 4.0,
                "signal_strength": 0.73,
                "confidence": 0.97,
                "trend_score": 76,
                "reasons": [...],
                "risks": [...]
            }
        ],
        "performers_update": [...],
        "errors": []
    }
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, date
from pathlib import Path
from typing import Dict, List, Optional

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from decision_framework import (
    DecisionEngine, DecisionConfig, PortfolioState, ResearchScore, ConvictionTier
)
from screening.universe_screener import UniverseScreener

# Try to import data infrastructure
try:
    from data_infrastructure import DataOrchestrator
    DATA_AVAILABLE = True
except ImportError:
    DATA_AVAILABLE = False
    DataOrchestrator = None

# Try to import Alpaca
try:
    from execution.alpaca_broker import AlpacaBroker
    ALPACA_AVAILABLE = True
except ImportError:
    ALPACA_AVAILABLE = False
    AlpacaBroker = None


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class DailyRunner:
    """
    Orchestrates the daily analysis workflow.
    """
    
    def __init__(self, 
                 portfolio_value: float = 100000,
                 data_dir: Path = None):
        """
        Initialize daily runner.
        
        Args:
            portfolio_value: Total portfolio value (for sizing)
            data_dir: Directory for persistent data
        """
        self.data_dir = Path(data_dir or os.getenv("TRADING_AGENT_DATA_DIR", "./trading_data"))
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        # Initialize components
        self.screener = UniverseScreener(data_dir=self.data_dir / "screening")
        
        # Initialize data orchestrator if available
        self.orchestrator = None
        if DATA_AVAILABLE:
            try:
                self.orchestrator = DataOrchestrator()
                logger.info("Data infrastructure initialized")
            except Exception as e:
                logger.warning(f"Could not initialize data infrastructure: {e}")
        
        # Initialize decision engine
        self.engine = DecisionEngine(
            data_orchestrator=self.orchestrator,
            data_dir=self.data_dir / "decisions"
        )
        
        # Portfolio state (load from Alpaca if available, else use default)
        self.portfolio = self._initialize_portfolio(portfolio_value)
        
        # Research scores cache (would be loaded from your Stage 1D analysis)
        self.research_scores = self._load_research_scores()
    
    def _initialize_portfolio(self, default_value: float) -> PortfolioState:
        """Initialize portfolio state, preferring Alpaca data if available."""
        if ALPACA_AVAILABLE:
            try:
                broker = AlpacaBroker()
                account = broker.get_account()
                
                if "error" not in account:
                    return PortfolioState(
                        timestamp=datetime.now(),
                        total_value=account["portfolio_value"],
                        cash=account["cash"],
                        invested=account["long_market_value"],
                        available_cash=account["buying_power"] * 0.95  # Keep 5% buffer
                    )
            except Exception as e:
                logger.warning(f"Could not get Alpaca portfolio: {e}")
        
        # Default portfolio
        return PortfolioState(
            timestamp=datetime.now(),
            total_value=default_value,
            cash=default_value,
            invested=0,
            available_cash=default_value * 0.95
        )
    
    def _load_research_scores(self) -> Dict[str, ResearchScore]:
        """
        Load research scores from file or return empty dict.
        
        In practice, you would:
        1. Export your Stage 1D scores to a JSON file
        2. This method loads them
        
        File format (research_scores.json):
        {
            "TOST": {
                "overall_score": 4.40,
                "conviction_tier": "HIGH",
                "thesis": "...",
                "bear_case_price": 34,
                "base_case_price": 58,
                "bull_case_price": 73
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
            max_candidates: int = 30) -> Dict:
        """
        Run daily analysis.
        
        Args:
            mode: "full", "quick", or "symbols"
            symbols: Specific symbols to analyze (for "symbols" mode)
            max_candidates: Max candidates to screen
        
        Returns:
            Analysis results as JSON-serializable dict
        """
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        start_time = datetime.now()
        
        logger.info(f"Starting daily analysis run {run_id} (mode: {mode})")
        
        results = {
            "run_id": run_id,
            "timestamp": start_time.isoformat(),
            "mode": mode,
            "status": "running",
            "summary": {},
            "opportunities": [],
            "holds": [],
            "performers_update": [],
            "portfolio": {},
            "errors": []
        }
        
        try:
            # 1. Get candidates to screen
            if symbols:
                candidates = [{"symbol": s.upper(), "source": "manual"} for s in symbols]
            elif mode == "quick":
                candidates = self.screener.get_screening_candidates(max_candidates=15)
            else:
                candidates = self.screener.get_screening_candidates(max_candidates=max_candidates)
            
            logger.info(f"Screening {len(candidates)} candidates")
            
            # 2. Analyze each candidate
            opportunities = []
            holds = []
            errors = []
            
            for candidate in candidates:
                symbol = candidate["symbol"]
                
                try:
                    # Get research score if available
                    research = self.research_scores.get(symbol)
                    
                    # Evaluate entry
                    decision = self.engine.evaluate_entry(
                        symbol=symbol,
                        portfolio=self.portfolio,
                        research_score=research,
                        auto_fetch=True  # Let engine fetch price history
                    )
                    
                    # Convert to output format
                    decision_dict = self._decision_to_dict(decision)
                    
                    if decision.action == "BUY":
                        opportunities.append(decision_dict)
                        
                        # Track as performer
                        self.screener.track_performer(
                            symbol=symbol,
                            score=decision.signal.strength if decision.signal else 0,
                            signal="BUY",
                            sector=candidate.get("sector", ""),
                            thesis=candidate.get("theme", ""),
                            note=f"Signal strength: {decision.signal.strength:.2f}" if decision.signal else ""
                        )
                    else:
                        holds.append({
                            "symbol": symbol,
                            "reason": decision.primary_reason[:100]
                        })
                        
                except Exception as e:
                    logger.error(f"Error analyzing {symbol}: {e}")
                    errors.append({"symbol": symbol, "error": str(e)})
            
            # 3. Sort opportunities by strength
            opportunities.sort(key=lambda x: x.get("signal_strength", 0), reverse=True)
            
            # 4. Build summary
            total_investment = sum(o.get("position_value", 0) for o in opportunities)
            
            results["summary"] = {
                "candidates_screened": len(candidates),
                "buy_signals": len(opportunities),
                "hold_signals": len(holds),
                "errors": len(errors),
                "total_investment_recommended": round(total_investment, 2),
                "portfolio_value": self.portfolio.total_value,
                "available_cash": self.portfolio.available_cash
            }
            
            results["opportunities"] = opportunities
            results["holds"] = holds[:10]  # Top 10 holds
            results["errors"] = errors
            
            # 5. Get persistent performers
            results["performers_update"] = self.screener.get_persistent_performers(min_times_seen=2)
            
            # 6. Portfolio info
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
        
        # Timing
        end_time = datetime.now()
        results["duration_seconds"] = (end_time - start_time).total_seconds()
        
        logger.info(f"Analysis complete: {len(results['opportunities'])} opportunities found")
        
        return results
    
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
                    # Extract number from "📊 Trend Score: 76/100 (STRONG_UP)"
                    score_part = reason.split(":")[1].split("/")[0]
                    return int(score_part.strip())
                except:
                    pass
        return None


def main():
    parser = argparse.ArgumentParser(description="Daily Trading Analysis")
    
    parser.add_argument("--mode", choices=["full", "quick", "symbols"], default="full",
                       help="Analysis mode")
    parser.add_argument("--symbols", type=str, default=None,
                       help="Comma-separated symbols to analyze")
    parser.add_argument("--max-candidates", type=int, default=30,
                       help="Max candidates to screen")
    parser.add_argument("--portfolio-value", type=float, default=100000,
                       help="Portfolio value for position sizing")
    parser.add_argument("--output", type=str, default=None,
                       help="Output file path (JSON)")
    parser.add_argument("--data-dir", type=str, default=None,
                       help="Data directory path")
    parser.add_argument("--quiet", action="store_true",
                       help="Suppress console output")
    
    args = parser.parse_args()
    
    # Parse symbols
    symbols = None
    if args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",")]
    
    # Run analysis
    runner = DailyRunner(
        portfolio_value=args.portfolio_value,
        data_dir=args.data_dir
    )
    
    results = runner.run(
        mode=args.mode if not symbols else "symbols",
        symbols=symbols,
        max_candidates=args.max_candidates
    )
    
    # Output
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(results, f, indent=2)
        if not args.quiet:
            print(f"Results written to {args.output}")
    
    if not args.quiet:
        # Print summary
        print("\n" + "=" * 60)
        print("DAILY ANALYSIS SUMMARY")
        print("=" * 60)
        print(f"Run ID: {results['run_id']}")
        print(f"Status: {results['status']}")
        print(f"Duration: {results.get('duration_seconds', 0):.1f}s")
        print()
        
        summary = results.get("summary", {})
        print(f"Candidates Screened: {summary.get('candidates_screened', 0)}")
        print(f"Buy Signals: {summary.get('buy_signals', 0)}")
        print(f"Hold Signals: {summary.get('hold_signals', 0)}")
        print(f"Total Investment: ${summary.get('total_investment_recommended', 0):,.2f}")
        print()
        
        if results["opportunities"]:
            print("OPPORTUNITIES:")
            for opp in results["opportunities"]:
                print(f"  📈 {opp['symbol']}: BUY {opp['shares']} @ ${opp['limit_price']:.2f}")
                print(f"     Position: {opp['position_size_pct']:.1f}%, Stop: ${opp['stop_loss']:.2f}")
                print(f"     Strength: {opp['signal_strength']:.2f}, Trend: {opp.get('trend_score', 'N/A')}")
                print()
        else:
            print("No opportunities found today.")
        
        print("=" * 60)
    
    # Return results for programmatic use
    return results


if __name__ == "__main__":
    main()
