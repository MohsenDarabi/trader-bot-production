"""
Simple cache implementation for reducing API calls
"""
import time
from typing import Any, Optional, Dict, Callable
from dataclasses import dataclass, field
from src.utils.logger import get_logger


logger = get_logger(__name__)


@dataclass
class CacheEntry:
    """Single cache entry with TTL"""
    value: Any
    expires_at: float
    
    def is_expired(self) -> bool:
        """Check if entry has expired"""
        return time.time() > self.expires_at


class SimpleCache:
    """
    Simple in-memory cache with TTL support
    """
    
    def __init__(self, default_ttl: int = 30):
        """
        Initialize cache
        
        Args:
            default_ttl: Default time-to-live in seconds
        """
        self.default_ttl = default_ttl
        self._cache: Dict[str, CacheEntry] = {}
        self._hit_count = 0
        self._miss_count = 0
        
    def get(self, key: str) -> Optional[Any]:
        """
        Get value from cache
        
        Args:
            key: Cache key
            
        Returns:
            Cached value or None if not found/expired
        """
        if key in self._cache:
            entry = self._cache[key]
            if not entry.is_expired():
                self._hit_count += 1
                logger.debug(f"Cache hit for key: {key}")
                return entry.value
            else:
                # Remove expired entry
                del self._cache[key]
                
        self._miss_count += 1
        logger.debug(f"Cache miss for key: {key}")
        return None
    
    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """
        Set value in cache
        
        Args:
            key: Cache key
            value: Value to cache
            ttl: Time-to-live in seconds (uses default if not specified)
        """
        ttl = ttl or self.default_ttl
        expires_at = time.time() + ttl
        
        self._cache[key] = CacheEntry(value=value, expires_at=expires_at)
        logger.debug(f"Cached key: {key} with TTL: {ttl}s")
    
    def invalidate(self, key: str) -> None:
        """
        Invalidate cache entry
        
        Args:
            key: Cache key to invalidate
        """
        if key in self._cache:
            del self._cache[key]
            logger.debug(f"Invalidated cache key: {key}")
    
    def clear(self) -> None:
        """Clear all cache entries"""
        self._cache.clear()
        logger.info("Cache cleared")
    
    def cleanup_expired(self) -> int:
        """
        Remove expired entries
        
        Returns:
            Number of entries removed
        """
        expired_keys = [
            key for key, entry in self._cache.items() 
            if entry.is_expired()
        ]
        
        for key in expired_keys:
            del self._cache[key]
            
        if expired_keys:
            logger.debug(f"Cleaned up {len(expired_keys)} expired cache entries")
            
        return len(expired_keys)
    
    def get_stats(self) -> Dict[str, int]:
        """
        Get cache statistics
        
        Returns:
            Dictionary with cache stats
        """
        total_requests = self._hit_count + self._miss_count
        hit_rate = (self._hit_count / total_requests * 100) if total_requests > 0 else 0
        
        return {
            'size': len(self._cache),
            'hits': self._hit_count,
            'misses': self._miss_count,
            'hit_rate': round(hit_rate, 2)
        }
    
    def cached(self, ttl: Optional[int] = None) -> Callable:
        """
        Decorator for caching function results
        
        Args:
            ttl: Time-to-live in seconds
            
        Returns:
            Decorator function
        """
        def decorator(func: Callable) -> Callable:
            def wrapper(*args, **kwargs):
                # Create cache key from function name and arguments
                cache_key = f"{func.__name__}:{str(args)}:{str(kwargs)}"
                
                # Try to get from cache
                cached_value = self.get(cache_key)
                if cached_value is not None:
                    return cached_value
                
                # Call function and cache result
                result = func(*args, **kwargs)
                self.set(cache_key, result, ttl)
                
                return result
            
            return wrapper
        return decorator


# Global cache instances
ticker_cache = SimpleCache(default_ttl=30)  # 30 seconds for ticker data
ohlc_cache = SimpleCache(default_ttl=300)   # 5 minutes for OHLC data
balance_cache = SimpleCache(default_ttl=60)  # 1 minute for balance data