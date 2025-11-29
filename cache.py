"""
Cache Utility
=============
File-based and memory caching for API responses.
"""

import json
import hashlib
import time
import pickle
from pathlib import Path
from typing import Any, Optional, Callable
from functools import wraps
import threading

class Cache:
    """Hybrid memory + file cache with TTL support."""
    
    def __init__(self, cache_dir: Path, default_ttl: int = 3600):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.default_ttl = default_ttl
        self._memory_cache: dict = {}
        self._lock = threading.Lock()
    
    def _make_key(self, *args, **kwargs) -> str:
        """Generate cache key from arguments."""
        key_data = json.dumps({"args": args, "kwargs": kwargs}, sort_keys=True, default=str)
        return hashlib.md5(key_data.encode()).hexdigest()
    
    def _get_cache_path(self, key: str) -> Path:
        """Get file path for cache key."""
        return self.cache_dir / f"{key}.cache"
    
    def get(self, key: str) -> Optional[Any]:
        """Get value from cache if not expired."""
        # Check memory cache first
        with self._lock:
            if key in self._memory_cache:
                entry = self._memory_cache[key]
                if entry["expires_at"] > time.time():
                    return entry["value"]
                else:
                    del self._memory_cache[key]
        
        # Check file cache
        cache_path = self._get_cache_path(key)
        if cache_path.exists():
            try:
                with open(cache_path, "rb") as f:
                    entry = pickle.load(f)
                if entry["expires_at"] > time.time():
                    # Promote to memory cache
                    with self._lock:
                        self._memory_cache[key] = entry
                    return entry["value"]
                else:
                    cache_path.unlink()  # Delete expired
            except Exception:
                pass
        
        return None
    
    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """Set value in cache with TTL."""
        ttl = ttl or self.default_ttl
        entry = {
            "value": value,
            "expires_at": time.time() + ttl,
            "created_at": time.time()
        }
        
        # Set in memory
        with self._lock:
            self._memory_cache[key] = entry
        
        # Persist to file
        cache_path = self._get_cache_path(key)
        try:
            with open(cache_path, "wb") as f:
                pickle.dump(entry, f)
        except Exception:
            pass  # File cache is best-effort
    
    def delete(self, key: str) -> None:
        """Delete value from cache."""
        with self._lock:
            self._memory_cache.pop(key, None)
        
        cache_path = self._get_cache_path(key)
        if cache_path.exists():
            cache_path.unlink()
    
    def clear(self) -> None:
        """Clear all cached data."""
        with self._lock:
            self._memory_cache.clear()
        
        for cache_file in self.cache_dir.glob("*.cache"):
            cache_file.unlink()
    
    def get_stats(self) -> dict:
        """Get cache statistics."""
        memory_count = len(self._memory_cache)
        file_count = len(list(self.cache_dir.glob("*.cache")))
        total_size = sum(f.stat().st_size for f in self.cache_dir.glob("*.cache"))
        return {
            "memory_entries": memory_count,
            "file_entries": file_count,
            "total_size_bytes": total_size
        }


def cached(cache_instance: Cache, ttl: Optional[int] = None, key_prefix: str = ""):
    """
    Decorator for caching function results.
    
    Usage:
        @cached(cache, ttl=3600, key_prefix="prices")
        def get_prices(symbol):
            ...
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Build cache key
            key_parts = [key_prefix, func.__name__]
            key_data = json.dumps({"args": args[1:] if args else args, "kwargs": kwargs}, 
                                  sort_keys=True, default=str)
            key = hashlib.md5("".join(key_parts).encode() + key_data.encode()).hexdigest()
            
            # Check cache
            result = cache_instance.get(key)
            if result is not None:
                return result
            
            # Execute and cache
            result = func(*args, **kwargs)
            if result is not None:
                cache_instance.set(key, result, ttl)
            
            return result
        return wrapper
    return decorator
