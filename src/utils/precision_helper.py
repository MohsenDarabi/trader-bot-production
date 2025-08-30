"""
Market-specific precision handling using exchange tick_size data.
Reuses existing patterns from coinex_client.py for consistency.
"""
from typing import Dict, Optional
from src.utils.logger import get_logger
from src.utils.safe_conversions import safe_float

logger = get_logger(__name__)


class PrecisionHelper:
    """Helper for market-specific price and amount formatting"""
    
    def __init__(self):
        """Initialize precision helper with caching"""
        self._precision_cache = {}
    
    def get_price_precision(self, market_info: Dict, market: str) -> int:
        """
        Get price precision from market tick_size
        
        Args:
            market_info: Market information from exchange
            market: Market symbol for logging
            
        Returns:
            Number of decimal places for price formatting
        """
        if market in self._precision_cache:
            return self._precision_cache[market]['price']
        
        try:
            tick_size = safe_float(market_info.get('tick_size', 0.0001))
            
            # Calculate decimal places from tick_size (reusing coinex_client logic)
            if tick_size > 0:
                # Count decimal places in tick_size
                tick_str = str(tick_size)
                if '.' in tick_str:
                    precision = len(tick_str.split('.')[-1])
                else:
                    precision = 0
            else:
                precision = 4  # Default fallback
            
            # Cache the result
            if market not in self._precision_cache:
                self._precision_cache[market] = {}
            self._precision_cache[market]['price'] = precision
            
            logger.debug(f"Price precision for {market}: {precision} decimals (tick_size={tick_size})")
            return precision
            
        except Exception as e:
            logger.warning(f"Could not determine price precision for {market}: {e}")
            return 4  # Safe fallback
    
    def get_amount_precision(self, market_info: Dict, market: str) -> int:
        """
        Get amount precision from market data
        
        Args:
            market_info: Market information from exchange
            market: Market symbol for logging
            
        Returns:
            Number of decimal places for amount formatting
        """
        if market in self._precision_cache and 'amount' in self._precision_cache[market]:
            return self._precision_cache[market]['amount']
        
        try:
            # Try to get amount_precision directly from market data
            amount_precision = market_info.get('amount_precision')
            if amount_precision is not None:
                precision = int(amount_precision)
            else:
                # Derive from min_amount (reusing coinex_client logic)
                min_amount = safe_float(market_info.get('min_amount', 0))
                if min_amount > 0:
                    min_amount_str = str(min_amount)
                    if '.' in min_amount_str:
                        precision = len(min_amount_str.split('.')[-1])
                    else:
                        precision = 0
                else:
                    precision = 6  # Default for crypto pairs
            
            # Cache the result
            if market not in self._precision_cache:
                self._precision_cache[market] = {}
            self._precision_cache[market]['amount'] = precision
            
            logger.debug(f"Amount precision for {market}: {precision} decimals")
            return precision
            
        except Exception as e:
            logger.warning(f"Could not determine amount precision for {market}: {e}")
            return 6  # Safe fallback
    
    def format_price(self, price: float, market_info: Dict, market: str) -> str:
        """
        Format price with market-specific precision
        
        Args:
            price: Price to format
            market_info: Market information from exchange
            market: Market symbol
            
        Returns:
            Formatted price string
        """
        precision = self.get_price_precision(market_info, market)
        return f"{price:.{precision}f}".rstrip('0').rstrip('.')
    
    def format_amount(self, amount: float, market_info: Dict, market: str) -> str:
        """
        Format amount with market-specific precision
        
        Args:
            amount: Amount to format
            market_info: Market information from exchange
            market: Market symbol
            
        Returns:
            Formatted amount string
        """
        precision = self.get_amount_precision(market_info, market)
        if precision > 0:
            return f"{amount:.{precision}f}".rstrip('0').rstrip('.')
        else:
            return f"{int(amount)}"
    
    def clear_cache(self, market: Optional[str] = None):
        """
        Clear precision cache
        
        Args:
            market: Specific market to clear, or None for all markets
        """
        if market:
            self._precision_cache.pop(market, None)
            logger.debug(f"Cleared precision cache for {market}")
        else:
            self._precision_cache.clear()
            logger.debug("Cleared all precision cache")


# Global instance for reuse
precision_helper = PrecisionHelper()