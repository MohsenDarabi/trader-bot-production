"""
WebSocket Market Data Provider
Handles real-time market data from WebSocket subscriptions with HTTP fallback
"""
import time
from datetime import datetime, timezone
from typing import Dict, Optional, Any, Callable
from dataclasses import dataclass
from threading import Lock

from src.utils.logger import get_logger


logger = get_logger(__name__)


@dataclass
class MarketState:
    """Market state data from WebSocket"""
    market: str
    last_price: float
    open_price: float
    high_price: float
    low_price: float
    volume: float
    timestamp: float
    mark_price: Optional[float] = None
    index_price: Optional[float] = None


class WebSocketMarketDataProvider:
    """
    Provides real-time market data from WebSocket subscriptions
    Maintains price cache with fallback to HTTP when needed
    """
    
    def __init__(self):
        """Initialize the WebSocket market data provider"""
        self._market_states: Dict[str, MarketState] = {}
        self._price_cache: Dict[str, float] = {}
        self._cache_timestamps: Dict[str, float] = {}
        self._cache_lock = Lock()
        self._price_update_callbacks: Dict[str, Callable[[str, float], None]] = {}
        
        # Configuration
        self._max_cache_age = 30.0  # seconds
        self._price_validation_threshold = 0.05  # 5% price change threshold for validation
        
        logger.info("WebSocket market data provider initialized")
    
    def register_price_callback(self, market: str, callback: Callable[[str, float], None]):
        """
        Register a callback for price updates on a specific market
        
        Args:
            market: Market symbol (e.g., BTCUSDT)
            callback: Function to call when price updates (market, price)
        """
        self._price_update_callbacks[market] = callback
        logger.debug(f"Registered price callback for {market}")
    
    def handle_state_update(self, data: Dict[str, Any]):
        """
        Handle state.update message from WebSocket
        
        Args:
            data: WebSocket message data containing state_list
        """
        try:
            state_list = data.get("state_list", [])
            if not state_list:
                logger.warning("Received empty state_list in state.update")
                return
            
            current_time = time.time()
            
            for state_data in state_list:
                market = state_data.get("market")
                if not market:
                    logger.warning("Received state update without market name")
                    continue
                
                try:
                    # Parse market state data
                    market_state = MarketState(
                        market=market,
                        last_price=float(state_data.get("last", 0)),
                        open_price=float(state_data.get("open", 0)),
                        high_price=float(state_data.get("high", 0)),
                        low_price=float(state_data.get("low", 0)),
                        volume=float(state_data.get("volume", 0)),
                        timestamp=current_time,
                        mark_price=float(state_data.get("mark_price", 0)) if state_data.get("mark_price") else None,
                        index_price=float(state_data.get("index_price", 0)) if state_data.get("index_price") else None
                    )
                    
                    # Validate price data
                    if market_state.last_price <= 0:
                        logger.warning(f"Invalid price data for {market}: {market_state.last_price}")
                        continue
                    
                    # Update cache with thread safety
                    with self._cache_lock:
                        # Validate price change if we have previous data
                        if market in self._price_cache:
                            old_price = self._price_cache[market]
                            price_change_pct = abs(market_state.last_price - old_price) / old_price
                            
                            if price_change_pct > self._price_validation_threshold:
                                logger.warning(
                                    f"Large price change detected for {market}: "
                                    f"{old_price:.2f} -> {market_state.last_price:.2f} "
                                    f"({price_change_pct:.2%})"
                                )
                        
                        # Update cache
                        self._market_states[market] = market_state
                        self._price_cache[market] = market_state.last_price
                        self._cache_timestamps[market] = current_time
                    
                    # Call registered callback if available
                    if market in self._price_update_callbacks:
                        try:
                            self._price_update_callbacks[market](market, market_state.last_price)
                        except Exception as e:
                            logger.error(f"Error in price callback for {market}: {e}")
                    
                    logger.debug(f"Updated market state for {market}: ${market_state.last_price:.2f}")
                    
                except (ValueError, TypeError) as e:
                    logger.error(f"Error parsing market state for {market}: {e}")
                    continue
            
            logger.debug(f"Processed {len(state_list)} market state updates")
            
        except Exception as e:
            logger.error(f"Error handling state update: {e}")
    
    def get_current_price(self, market: str) -> Optional[float]:
        """
        Get current price for a market from WebSocket cache
        
        Args:
            market: Market symbol (e.g., BTCUSDT)
            
        Returns:
            Current price if available and fresh, None otherwise
        """
        with self._cache_lock:
            if market not in self._price_cache:
                logger.debug(f"No cached price for {market}")
                return None
            
            # Check cache age
            cache_age = time.time() - self._cache_timestamps.get(market, 0)
            if cache_age > self._max_cache_age:
                logger.debug(f"Cached price for {market} is stale ({cache_age:.1f}s old)")
                return None
            
            price = self._price_cache[market]
            logger.debug(f"Retrieved cached price for {market}: ${price:.2f} (age: {cache_age:.1f}s)")
            return price
    
    def get_market_state(self, market: str) -> Optional[MarketState]:
        """
        Get full market state for a market
        
        Args:
            market: Market symbol
            
        Returns:
            MarketState object if available and fresh, None otherwise
        """
        with self._cache_lock:
            if market not in self._market_states:
                return None
            
            state = self._market_states[market]
            cache_age = time.time() - state.timestamp
            
            if cache_age > self._max_cache_age:
                logger.debug(f"Market state for {market} is stale ({cache_age:.1f}s old)")
                return None
            
            return state
    
    def is_price_fresh(self, market: str) -> bool:
        """
        Check if cached price for market is fresh
        
        Args:
            market: Market symbol
            
        Returns:
            True if price is fresh, False otherwise
        """
        with self._cache_lock:
            if market not in self._cache_timestamps:
                return False
            
            cache_age = time.time() - self._cache_timestamps[market]
            return cache_age <= self._max_cache_age
    
    def get_cached_markets(self) -> list[str]:
        """
        Get list of markets with cached data
        
        Returns:
            List of market symbols with cached data
        """
        with self._cache_lock:
            return list(self._price_cache.keys())
    
    def clear_cache(self, market: Optional[str] = None):
        """
        Clear price cache for specific market or all markets
        
        Args:
            market: Market to clear, or None to clear all
        """
        with self._cache_lock:
            if market:
                self._price_cache.pop(market, None)
                self._cache_timestamps.pop(market, None)
                self._market_states.pop(market, None)
                logger.info(f"Cleared cache for {market}")
            else:
                self._price_cache.clear()
                self._cache_timestamps.clear()
                self._market_states.clear()
                logger.info("Cleared all market data cache")
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """
        Get cache statistics
        
        Returns:
            Dictionary with cache statistics
        """
        with self._cache_lock:
            current_time = time.time()
            fresh_count = sum(
                1 for timestamp in self._cache_timestamps.values()
                if current_time - timestamp <= self._max_cache_age
            )
            
            return {
                "total_markets": len(self._price_cache),
                "fresh_markets": fresh_count,
                "stale_markets": len(self._price_cache) - fresh_count,
                "max_cache_age": self._max_cache_age,
                "price_validation_threshold": self._price_validation_threshold
            }