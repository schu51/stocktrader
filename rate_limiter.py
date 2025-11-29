"""
Rate Limiter Utility
====================
Token bucket rate limiter for API calls.
"""

import time
import threading
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, Optional

@dataclass
class RateLimitConfig:
    requests: int
    window_seconds: float

class RateLimiter:
    """Thread-safe token bucket rate limiter."""
    
    def __init__(self):
        self._buckets: Dict[str, dict] = defaultdict(lambda: {
            "tokens": 0,
            "last_update": time.time(),
            "config": None
        })
        self._lock = threading.Lock()
    
    def configure(self, name: str, requests: int, window_seconds: float):
        """Configure rate limit for a named source."""
        with self._lock:
            self._buckets[name]["config"] = RateLimitConfig(requests, window_seconds)
            self._buckets[name]["tokens"] = requests
            self._buckets[name]["last_update"] = time.time()
    
    def _refill(self, name: str) -> None:
        """Refill tokens based on elapsed time."""
        bucket = self._buckets[name]
        if bucket["config"] is None:
            return
        
        now = time.time()
        elapsed = now - bucket["last_update"]
        config = bucket["config"]
        
        # Calculate tokens to add
        tokens_to_add = (elapsed / config.window_seconds) * config.requests
        bucket["tokens"] = min(config.requests, bucket["tokens"] + tokens_to_add)
        bucket["last_update"] = now
    
    def acquire(self, name: str, timeout: Optional[float] = 30.0) -> bool:
        """
        Acquire a token for the named rate limiter.
        Blocks until a token is available or timeout is reached.
        
        Returns True if token acquired, False if timeout.
        """
        start_time = time.time()
        
        while True:
            with self._lock:
                self._refill(name)
                bucket = self._buckets[name]
                
                if bucket["config"] is None:
                    return True  # No limit configured
                
                if bucket["tokens"] >= 1:
                    bucket["tokens"] -= 1
                    return True
                
                # Calculate wait time for next token
                config = bucket["config"]
                wait_time = config.window_seconds / config.requests
            
            # Check timeout
            if timeout is not None:
                elapsed = time.time() - start_time
                if elapsed >= timeout:
                    return False
                wait_time = min(wait_time, timeout - elapsed)
            
            time.sleep(wait_time)
    
    def get_status(self, name: str) -> dict:
        """Get current status of a rate limiter."""
        with self._lock:
            self._refill(name)
            bucket = self._buckets[name]
            if bucket["config"] is None:
                return {"configured": False}
            return {
                "configured": True,
                "tokens_available": bucket["tokens"],
                "max_tokens": bucket["config"].requests,
                "window_seconds": bucket["config"].window_seconds
            }

# Global rate limiter instance
rate_limiter = RateLimiter()

def configure_rate_limits(limits: Dict[str, dict]) -> None:
    """Configure all rate limits from config."""
    for name, config in limits.items():
        rate_limiter.configure(name, config["requests"], config["window_seconds"])
