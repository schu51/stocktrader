"""
Alpaca Broker Integration
=========================

Handles order execution, position management, and account info via Alpaca API.
Supports both paper trading and live trading modes.

Setup:
    1. Create free account at https://alpaca.markets
    2. Get API keys from dashboard (paper trading keys for testing)
    3. Set environment variables:
       - ALPACA_API_KEY
       - ALPACA_SECRET_KEY
       - ALPACA_PAPER=true (for paper trading)

Usage:
    from execution.alpaca_broker import AlpacaBroker
    
    broker = AlpacaBroker()  # Uses env vars
    
    # Check account
    account = broker.get_account()
    print(f"Buying Power: ${account['buying_power']}")
    
    # Place order
    order = broker.place_order(
        symbol="TOST",
        qty=100,
        side="buy",
        order_type="limit",
        limit_price=42.50,
        stop_loss=34.00
    )
    
    # Get positions
    positions = broker.get_positions()
"""

import os
import json
import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

try:
    import requests
except ImportError:
    requests = None

logger = logging.getLogger(__name__)


class TradingMode(Enum):
    PAPER = "paper"
    LIVE = "live"


class OrderSide(Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class OrderStatus(Enum):
    NEW = "new"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    DONE_FOR_DAY = "done_for_day"
    CANCELED = "canceled"
    EXPIRED = "expired"
    REPLACED = "replaced"
    PENDING_CANCEL = "pending_cancel"
    PENDING_REPLACE = "pending_replace"
    ACCEPTED = "accepted"
    PENDING_NEW = "pending_new"
    ACCEPTED_FOR_BIDDING = "accepted_for_bidding"
    STOPPED = "stopped"
    REJECTED = "rejected"
    SUSPENDED = "suspended"
    CALCULATED = "calculated"


@dataclass
class AlpacaConfig:
    """Configuration for Alpaca API connection."""
    api_key: str
    secret_key: str
    paper: bool = True
    
    @property
    def base_url(self) -> str:
        if self.paper:
            return "https://paper-api.alpaca.markets"
        return "https://api.alpaca.markets"
    
    @property
    def data_url(self) -> str:
        return "https://data.alpaca.markets"


class AlpacaBroker:
    """
    Alpaca broker integration for order execution and portfolio management.
    
    Features:
    - Paper and live trading modes
    - Market, limit, stop, and stop-limit orders
    - Bracket orders (entry + stop loss + take profit)
    - Position tracking
    - Order status monitoring
    """
    
    def __init__(self, 
                 api_key: str = None,
                 secret_key: str = None,
                 paper: bool = None):
        """
        Initialize Alpaca broker connection.
        
        Args:
            api_key: Alpaca API key (or set ALPACA_API_KEY env var)
            secret_key: Alpaca secret key (or set ALPACA_SECRET_KEY env var)
            paper: Use paper trading (or set ALPACA_PAPER env var)
        """
        if requests is None:
            raise ImportError("requests library required. Install with: pip install requests")
        
        # Load from env vars if not provided
        self.config = AlpacaConfig(
            api_key=api_key or os.getenv("ALPACA_API_KEY", ""),
            secret_key=secret_key or os.getenv("ALPACA_SECRET_KEY", ""),
            paper=paper if paper is not None else os.getenv("ALPACA_PAPER", "true").lower() == "true"
        )
        
        if not self.config.api_key or not self.config.secret_key:
            raise ValueError(
                "Alpaca API credentials required. Set ALPACA_API_KEY and ALPACA_SECRET_KEY "
                "environment variables or pass to constructor."
            )
        
        self.mode = TradingMode.PAPER if self.config.paper else TradingMode.LIVE
        logger.info(f"AlpacaBroker initialized in {self.mode.value} mode")
    
    def _headers(self) -> Dict[str, str]:
        """Get authentication headers."""
        return {
            "APCA-API-KEY-ID": self.config.api_key,
            "APCA-API-SECRET-KEY": self.config.secret_key,
            "Content-Type": "application/json"
        }
    
    def _request(self, 
                 method: str, 
                 endpoint: str, 
                 data: Dict = None,
                 params: Dict = None,
                 use_data_api: bool = False) -> Dict:
        """Make authenticated request to Alpaca API."""
        base = self.config.data_url if use_data_api else self.config.base_url
        url = f"{base}{endpoint}"
        
        try:
            response = requests.request(
                method=method,
                url=url,
                headers=self._headers(),
                json=data,
                params=params,
                timeout=30
            )
            
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 204:
                return {"success": True}
            else:
                error_msg = response.text
                try:
                    error_data = response.json()
                    error_msg = error_data.get("message", error_msg)
                except:
                    pass
                
                logger.error(f"Alpaca API error ({response.status_code}): {error_msg}")
                return {"error": error_msg, "status_code": response.status_code}
                
        except requests.exceptions.RequestException as e:
            logger.error(f"Request failed: {e}")
            return {"error": str(e)}
    
    # =========================================================================
    # ACCOUNT
    # =========================================================================
    
    def get_account(self) -> Dict:
        """
        Get account information.
        
        Returns:
            Dict with account details including:
            - buying_power: Available cash for trading
            - cash: Total cash
            - portfolio_value: Total portfolio value
            - equity: Account equity
            - last_equity: Previous day equity
            - long_market_value: Value of long positions
            - short_market_value: Value of short positions
            - daytrade_count: Number of day trades in last 5 days
            - pattern_day_trader: PDT status
        """
        result = self._request("GET", "/v2/account")
        
        if "error" not in result:
            # Convert string values to floats for easier use
            for key in ["buying_power", "cash", "portfolio_value", "equity", 
                       "last_equity", "long_market_value", "short_market_value"]:
                if key in result:
                    result[key] = float(result[key])
        
        return result
    
    def get_buying_power(self) -> float:
        """Get available buying power."""
        account = self.get_account()
        return account.get("buying_power", 0)
    
    def get_portfolio_value(self) -> float:
        """Get total portfolio value."""
        account = self.get_account()
        return account.get("portfolio_value", 0)
    
    # =========================================================================
    # POSITIONS
    # =========================================================================
    
    def get_positions(self) -> List[Dict]:
        """
        Get all open positions.
        
        Returns:
            List of position dicts with:
            - symbol: Stock ticker
            - qty: Number of shares
            - avg_entry_price: Average cost basis
            - market_value: Current market value
            - unrealized_pl: Unrealized P&L
            - unrealized_plpc: Unrealized P&L percentage
            - current_price: Current price
        """
        result = self._request("GET", "/v2/positions")
        
        if isinstance(result, list):
            for pos in result:
                for key in ["qty", "avg_entry_price", "market_value", 
                           "unrealized_pl", "unrealized_plpc", "current_price"]:
                    if key in pos:
                        pos[key] = float(pos[key])
            return result
        
        return []
    
    def get_position(self, symbol: str) -> Optional[Dict]:
        """Get position for a specific symbol."""
        result = self._request("GET", f"/v2/positions/{symbol}")
        
        if "error" not in result:
            for key in ["qty", "avg_entry_price", "market_value", 
                       "unrealized_pl", "unrealized_plpc", "current_price"]:
                if key in result:
                    result[key] = float(result[key])
            return result
        
        return None
    
    def close_position(self, symbol: str, qty: int = None) -> Dict:
        """
        Close a position (fully or partially).
        
        Args:
            symbol: Stock ticker
            qty: Number of shares to close (None = close all)
        
        Returns:
            Order result
        """
        params = {}
        if qty:
            params["qty"] = str(qty)
        
        return self._request("DELETE", f"/v2/positions/{symbol}", params=params)
    
    def close_all_positions(self) -> Dict:
        """Close all open positions."""
        return self._request("DELETE", "/v2/positions")
    
    # =========================================================================
    # ORDERS
    # =========================================================================
    
    def place_order(self,
                    symbol: str,
                    qty: int,
                    side: str,
                    order_type: str = "market",
                    limit_price: float = None,
                    stop_price: float = None,
                    stop_loss: float = None,
                    take_profit: float = None,
                    time_in_force: str = "day",
                    client_order_id: str = None) -> Dict:
        """
        Place an order.
        
        Args:
            symbol: Stock ticker
            qty: Number of shares
            side: "buy" or "sell"
            order_type: "market", "limit", "stop", "stop_limit"
            limit_price: Limit price (required for limit/stop_limit)
            stop_price: Stop price (required for stop/stop_limit)
            stop_loss: Stop loss price (creates bracket order)
            take_profit: Take profit price (creates bracket order)
            time_in_force: "day", "gtc", "ioc", "fok"
            client_order_id: Custom order ID for tracking
        
        Returns:
            Order result with order ID and status
        """
        order_data = {
            "symbol": symbol,
            "qty": str(qty),
            "side": side,
            "type": order_type,
            "time_in_force": time_in_force
        }
        
        if limit_price:
            order_data["limit_price"] = str(round(float(limit_price), 2))

        if stop_price:
            order_data["stop_price"] = str(round(float(stop_price), 2))

        if client_order_id:
            order_data["client_order_id"] = client_order_id

        # Bracket order (entry with stop loss and/or take profit)
        if stop_loss or take_profit:
            order_data["order_class"] = "bracket"

            if stop_loss:
                order_data["stop_loss"] = {"stop_price": str(round(float(stop_loss), 2))}

            if take_profit:
                order_data["take_profit"] = {"limit_price": str(round(float(take_profit), 2))}
        
        result = self._request("POST", "/v2/orders", data=order_data)
        
        if "error" not in result:
            logger.info(f"Order placed: {side.upper()} {qty} {symbol} @ {order_type}")
        
        return result
    
    def place_bracket_order(self,
                           symbol: str,
                           qty: int,
                           side: str,
                           limit_price: float,
                           stop_loss: float,
                           take_profit: float = None,
                           time_in_force: str = "day") -> Dict:
        """
        Place a bracket order (entry + stop loss + optional take profit).
        
        This is the recommended order type for the trading agent as it
        automatically sets up the stop loss.
        """
        return self.place_order(
            symbol=symbol,
            qty=qty,
            side=side,
            order_type="limit",
            limit_price=limit_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            time_in_force=time_in_force
        )
    
    def get_order(self, order_id: str) -> Dict:
        """Get order by ID."""
        return self._request("GET", f"/v2/orders/{order_id}")
    
    def get_orders(self, 
                   status: str = "open",
                   limit: int = 50,
                   symbols: List[str] = None) -> List[Dict]:
        """
        Get orders.
        
        Args:
            status: "open", "closed", "all"
            limit: Max orders to return
            symbols: Filter by symbols
        """
        params = {"status": status, "limit": limit}
        if symbols:
            params["symbols"] = ",".join(symbols)
        
        result = self._request("GET", "/v2/orders", params=params)
        return result if isinstance(result, list) else []
    
    def cancel_order(self, order_id: str) -> Dict:
        """Cancel an order by ID."""
        return self._request("DELETE", f"/v2/orders/{order_id}")
    
    def cancel_all_orders(self) -> Dict:
        """Cancel all open orders."""
        return self._request("DELETE", "/v2/orders")
    
    # =========================================================================
    # MARKET DATA
    # =========================================================================
    
    def get_latest_quote(self, symbol: str) -> Dict:
        """Get latest quote for a symbol."""
        result = self._request(
            "GET", 
            f"/v2/stocks/{symbol}/quotes/latest",
            use_data_api=True
        )
        return result.get("quote", result)
    
    def get_latest_trade(self, symbol: str) -> Dict:
        """Get latest trade for a symbol."""
        result = self._request(
            "GET",
            f"/v2/stocks/{symbol}/trades/latest", 
            use_data_api=True
        )
        return result.get("trade", result)
    
    def get_bars(self, 
                 symbol: str,
                 timeframe: str = "1Day",
                 start: str = None,
                 end: str = None,
                 limit: int = 100) -> List[Dict]:
        """
        Get historical bars.
        
        Args:
            symbol: Stock ticker
            timeframe: "1Min", "5Min", "15Min", "1Hour", "1Day"
            start: Start date (RFC3339 or YYYY-MM-DD)
            end: End date
            limit: Max bars to return
        """
        params = {"timeframe": timeframe, "limit": limit}
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        
        result = self._request(
            "GET",
            f"/v2/stocks/{symbol}/bars",
            params=params,
            use_data_api=True
        )
        return result.get("bars", [])
    
    # =========================================================================
    # UTILITIES
    # =========================================================================
    
    def is_market_open(self) -> bool:
        """Check if market is currently open."""
        result = self._request("GET", "/v2/clock")
        return result.get("is_open", False)
    
    def get_market_calendar(self, start: str = None, end: str = None) -> List[Dict]:
        """Get market calendar."""
        params = {}
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        
        result = self._request("GET", "/v2/calendar", params=params)
        return result if isinstance(result, list) else []
    
    def get_asset(self, symbol: str) -> Dict:
        """Get asset info (tradability, shortability, etc.)."""
        return self._request("GET", f"/v2/assets/{symbol}")
    
    def is_tradable(self, symbol: str) -> bool:
        """Check if asset is tradable."""
        asset = self.get_asset(symbol)
        return asset.get("tradable", False)
    
    # =========================================================================
    # PORTFOLIO STATE CONVERSION
    # =========================================================================
    
    def to_portfolio_state(self) -> Dict:
        """
        Convert Alpaca account/positions to PortfolioState format
        compatible with the decision framework.
        """
        account = self.get_account()
        positions = self.get_positions()
        
        # Build positions dict
        positions_dict = {}
        for pos in positions:
            positions_dict[pos["symbol"]] = {
                "symbol": pos["symbol"],
                "shares": int(pos["qty"]),
                "avg_cost": pos["avg_entry_price"],
                "current_price": pos["current_price"],
                "market_value": pos["market_value"],
                "unrealized_pnl": pos["unrealized_pl"],
                "unrealized_pnl_pct": pos["unrealized_plpc"] * 100
            }
        
        return {
            "timestamp": datetime.now().isoformat(),
            "total_value": account.get("portfolio_value", 0),
            "cash": account.get("cash", 0),
            "invested": account.get("long_market_value", 0),
            "buying_power": account.get("buying_power", 0),
            "positions": positions_dict,
            "num_positions": len(positions)
        }


# =============================================================================
# EXECUTION HELPER
# =============================================================================

class OrderExecutor:
    """
    Higher-level order execution that integrates with DecisionEngine output.
    
    Takes Decision objects and executes them via Alpaca.
    """
    
    def __init__(self, broker: AlpacaBroker = None):
        self.broker = broker or AlpacaBroker()
    
    def execute_decision(self, decision: Dict) -> Dict:
        """
        Execute a decision from the DecisionEngine.
        
        Args:
            decision: Decision dict with action, symbol, shares, limit_price, stop_loss_price
        
        Returns:
            Execution result
        """
        if decision.get("action") != "BUY":
            return {"status": "skipped", "reason": f"Action is {decision.get('action')}, not BUY"}
        
        symbol = decision.get("symbol")
        shares = decision.get("shares", 0)
        limit_price = decision.get("limit_price")
        stop_loss = decision.get("stop_loss_price")
        target_price = decision.get("target_price")
        
        if not symbol or shares <= 0:
            return {"status": "error", "reason": "Invalid symbol or shares"}
        
        # Check if tradable
        if not self.broker.is_tradable(symbol):
            return {"status": "error", "reason": f"{symbol} is not tradable on Alpaca"}
        
        # Check buying power
        buying_power = self.broker.get_buying_power()
        required = shares * (limit_price or 0)
        
        if required > buying_power:
            return {
                "status": "error", 
                "reason": f"Insufficient buying power: ${buying_power:,.2f} < ${required:,.2f}"
            }
        
        # Place bracket order
        result = self.broker.place_bracket_order(
            symbol=symbol,
            qty=shares,
            side="buy",
            limit_price=limit_price,
            stop_loss=stop_loss,
            take_profit=target_price,
            time_in_force="day"
        )
        
        if "error" in result:
            return {"status": "error", "reason": result["error"]}
        
        return {
            "status": "submitted",
            "order_id": result.get("id"),
            "symbol": symbol,
            "shares": shares,
            "limit_price": limit_price,
            "stop_loss": stop_loss,
            "target_price": target_price,
            "alpaca_response": result
        }
    
    def execute_decisions_batch(self, decisions: List[Dict]) -> List[Dict]:
        """Execute multiple decisions."""
        results = []
        for decision in decisions:
            if decision.get("action") == "BUY":
                result = self.execute_decision(decision)
                results.append(result)
        return results


# =============================================================================
# CLI TEST
# =============================================================================

if __name__ == "__main__":
    import sys
    
    print("Alpaca Broker Test")
    print("=" * 50)
    
    try:
        broker = AlpacaBroker()
        print(f"Mode: {broker.mode.value}")
        print()
        
        # Get account
        account = broker.get_account()
        if "error" not in account:
            print("Account Info:")
            print(f"  Portfolio Value: ${account['portfolio_value']:,.2f}")
            print(f"  Cash: ${account['cash']:,.2f}")
            print(f"  Buying Power: ${account['buying_power']:,.2f}")
        else:
            print(f"Error: {account['error']}")
            sys.exit(1)
        
        # Get positions
        print("\nPositions:")
        positions = broker.get_positions()
        if positions:
            for pos in positions:
                print(f"  {pos['symbol']}: {int(pos['qty'])} shares @ ${pos['avg_entry_price']:.2f}")
                print(f"    Current: ${pos['current_price']:.2f}, P&L: ${pos['unrealized_pl']:.2f}")
        else:
            print("  No open positions")
        
        # Market status
        print(f"\nMarket Open: {broker.is_market_open()}")
        
    except ValueError as e:
        print(f"Configuration Error: {e}")
        print("\nTo test, set environment variables:")
        print("  export ALPACA_API_KEY=your_api_key")
        print("  export ALPACA_SECRET_KEY=your_secret_key")
        print("  export ALPACA_PAPER=true")
