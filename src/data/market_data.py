"""
Market data fetching and processing for CoinEx futures
"""
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

from src.exchange.coinex_client import CoinExClient
from src.utils.logger import get_logger
from src.utils.safe_conversions import safe_float, safe_int


logger = get_logger(__name__)


class MarketDataManager:
    """Manages market data fetching and processing"""
    
    def __init__(self, client: Optional[CoinExClient] = None):
        """
        Initialize market data manager
        
        Args:
            client: CoinEx API client instance
        """
        self.client = client or CoinExClient()
        self._market_info_cache = {}
        self._ohlc_cache = {}
        
        # Basic usage statistics for observability
        self._http_requests = 0
    
    def get_market_info(self, market: str, force_refresh: bool = False) -> Dict:
        """
        Get market information including minimum order size
        
        Args:
            market: Market symbol (e.g., BTCUSDT)
            force_refresh: Force refresh from API
            
        Returns:
            Market information dictionary
        """
        if market in self._market_info_cache and not force_refresh:
            return self._market_info_cache[market]
        
        try:
            markets = self.client.get_futures_markets(market)
            if not markets:
                raise ValueError(f"Market {market} not found")
            
            market_info = markets[0] if isinstance(markets, list) else markets
            self._market_info_cache[market] = market_info
            
            logger.info(f"Fetched market info for {market}: "
                       f"min_amount={market_info.get('min_amount')}, "
                       f"tick_size={market_info.get('tick_size')}")
            
            return market_info
            
        except Exception as e:
            logger.error(f"Failed to fetch market info for {market}: {e}")
            raise
    
    def get_minimum_order_value(self, market: str, current_price: float) -> float:
        """
        Calculate minimum order value in quote currency (USDT)
        
        Args:
            market: Market symbol
            current_price: Current market price
            
        Returns:
            Minimum order value in USDT
        """
        market_info = self.get_market_info(market)
        min_amount = safe_float(market_info.get('min_amount', 0))
        
        # For futures, min_amount is in base currency, so multiply by price
        min_value = min_amount * current_price
        
        logger.debug(f"{market} minimum order: {min_amount} @ ${current_price} = ${min_value}")
        return min_value
    
    def get_daily_candles(self, market: str, days: int = 2) -> List[Dict[str, Any]]:
        """
        Fetch daily OHLC candles
        
        Args:
            market: Market symbol
            days: Number of days to fetch (minimum 2 for previous day calculation)
            
        Returns:
            List of dictionaries with OHLC data, sorted by date descending
        """
        try:
            # Fetch daily candles
            klines = self.client.get_kline(
                market=market,
                period='1day',
                limit=days + 1  # Extra day for safety
            )
            
            if not klines:
                raise ValueError(f"No candle data available for {market}")
            
            # Ensure we have the required columns
            required_cols = ['created_at', 'open', 'high', 'low', 'close', 'volume']
            if not klines or not all(col in klines[0] for col in required_cols):
                raise ValueError("Missing required columns in kline data")
            
            # Process each kline with safe conversions
            processed_klines = []
            for kline in klines:
                # Convert timestamp to datetime
                timestamp_ms = safe_int(kline['created_at'])
                date_obj = datetime.fromtimestamp(timestamp_ms / 1000, timezone.utc)
                
                processed_kline = {
                    'created_at': timestamp_ms,
                    'date': date_obj,
                    'open': safe_float(kline['open']),
                    'high': safe_float(kline['high']),
                    'low': safe_float(kline['low']),
                    'close': safe_float(kline['close']),
                    'volume': safe_float(kline['volume'])
                }
                processed_klines.append(processed_kline)
            
            # Sort by date descending (newest first)
            processed_klines.sort(key=lambda x: x['created_at'], reverse=True)
            
            logger.info(f"Fetched {len(processed_klines)} daily candles for {market}")
            return processed_klines
            
        except Exception as e:
            logger.error(f"Failed to fetch daily candles for {market}: {e}")
            raise
    
    def get_previous_day_ohlc(self, market: str) -> Dict[str, float]:
        """
        Get previous day's OHLC data
        
        Args:
            market: Market symbol
            
        Returns:
            Dictionary with open, high, low, close prices
        """
        klines = self.get_daily_candles(market, days=2)
        
        if len(klines) < 2:
            raise ValueError(f"Insufficient data for {market}, need at least 2 days")
        
        # Get previous day (second item, as we sorted descending)
        prev_day = klines[1]
        
        ohlc = {
            'date': prev_day['date'].strftime('%Y-%m-%d'),
            'open': prev_day['open'],  # Already safe_float converted
            'high': prev_day['high'],  # Already safe_float converted
            'low': prev_day['low'],    # Already safe_float converted
            'close': prev_day['close'], # Already safe_float converted
            'volume': prev_day['volume'] # Already safe_float converted
        }
        
        logger.info(f"Previous day OHLC for {market} ({ohlc['date']}): "
                   f"O={ohlc['open']:.2f}, H={ohlc['high']:.2f}, "
                   f"L={ohlc['low']:.2f}, C={ohlc['close']:.2f}")
        
        return ohlc
    
    def get_hourly_candles(self, market: str, hours: int = 2) -> List[Dict[str, Any]]:
        """
        Fetch hourly OHLC candles
        
        Args:
            market: Market symbol
            hours: Number of hours to fetch (minimum 2 for previous hour calculation)
            
        Returns:
            List of dictionaries with OHLC data, sorted by date descending
        """
        try:
            # Fetch hourly candles
            klines = self.client.get_kline(
                market=market,
                period='1hour',
                limit=hours + 1  # Extra hour for safety
            )
            
            if not klines:
                raise ValueError(f"No candle data available for {market}")
            
            # Ensure we have the required columns
            required_cols = ['created_at', 'open', 'high', 'low', 'close', 'volume']
            if not klines or not all(col in klines[0] for col in required_cols):
                raise ValueError("Missing required columns in kline data")
            
            # Process each kline with safe conversions
            processed_klines = []
            for kline in klines:
                # Convert timestamp to datetime
                timestamp_ms = safe_int(kline['created_at'])
                date_obj = datetime.fromtimestamp(timestamp_ms / 1000, timezone.utc)
                
                processed_kline = {
                    'created_at': timestamp_ms,
                    'date': date_obj,
                    'open': safe_float(kline['open']),
                    'high': safe_float(kline['high']),
                    'low': safe_float(kline['low']),
                    'close': safe_float(kline['close']),
                    'volume': safe_float(kline['volume'])
                }
                processed_klines.append(processed_kline)
            
            # Sort by date descending (newest first)
            processed_klines.sort(key=lambda x: x['created_at'], reverse=True)
            
            logger.info(f"Fetched {len(processed_klines)} hourly candles for {market}")
            return processed_klines
            
        except Exception as e:
            logger.error(f"Failed to fetch hourly candles for {market}: {e}")
            raise
    
    def get_previous_hour_ohlc(self, market: str) -> Dict[str, float]:
        """
        Get previous hour's OHLC data
        
        Args:
            market: Market symbol
            
        Returns:
            Dictionary with open, high, low, close prices
        """
        klines = self.get_hourly_candles(market, hours=2)
        
        if len(klines) < 2:
            raise ValueError(f"Insufficient data for {market}, need at least 2 hours")
        
        # Get previous hour (second item, as we sorted descending)
        prev_hour = klines[1]
        
        ohlc = {
            'date': prev_hour['date'].strftime('%Y-%m-%d %H:00'),
            'open': prev_hour['open'],  # Already safe_float converted
            'high': prev_hour['high'],  # Already safe_float converted
            'low': prev_hour['low'],    # Already safe_float converted
            'close': prev_hour['close'], # Already safe_float converted
            'volume': prev_hour['volume'] # Already safe_float converted
        }
        
        logger.info(f"Previous hour OHLC for {market} ({ohlc['date']}): "
                   f"O={ohlc['open']:.2f}, H={ohlc['high']:.2f}, "
                   f"L={ohlc['low']:.2f}, C={ohlc['close']:.2f}")
        
        return ohlc

    def get_current_price(self, market: str) -> float:
        """
        Get current market price using the REST ticker endpoint.
        
        Args:
            market: Market symbol
            
        Returns:
            Current price
        """
        try:
            self._http_requests += 1
            ticker_response = self.client.get_ticker(market)
            # CoinEx ticker returns a list with one item
            if isinstance(ticker_response, list) and ticker_response:
                ticker = ticker_response[0]
            else:
                ticker = ticker_response
            
            price = safe_float(ticker.get('last', 0))
            
            if price <= 0:
                raise ValueError(f"Invalid price for {market}: {price}")
            
            logger.debug(f"Retrieved HTTP price for {market}: ${price:.2f}")
            return price
            
        except Exception as e:
            logger.error(f"Failed to fetch current price for {market}: {e}")
            raise
    
    def calculate_daily_range(self, high: float, low: float, divisor: int = 4) -> float:
        """
        Calculate daily range for strategy
        
        Args:
            high: Daily high price
            low: Daily low price
            divisor: Range divisor (default 4)
            
        Returns:
            Calculated range
        """
        if high <= low:
            raise ValueError(f"Invalid high/low: {high}/{low}")
        
        range_value = (high - low) / divisor
        logger.debug(f"Daily range: ({high} - {low}) / {divisor} = {range_value}")
        
        return range_value
    
    def is_new_trading_day(self, market: str) -> bool:
        """
        Check if it's a new trading day or bot startup requiring buy order reset
        
        This method enables 24/7 trading while providing clean daily resets.
        Returns True in two scenarios:
        1. Regular daily reset: 00:04-00:59 UTC (after funding fee settlement)
        2. Bot startup: Anytime when no valid buy order exists for current day
        
        Args:
            market: Market symbol
            
        Returns:
            True if buy orders should be reset for new day trading
        """
        current_time = datetime.now(timezone.utc)
        current_hour = current_time.hour
        current_minute = current_time.minute
        
        # Scenario 1: Check if it's a new trading day (after settlement period)
        # The actual reset logic is handled as a one-time event in the trading bot
        is_daily_reset_window = current_hour == 0 and current_minute >= 4
        
        # Scenario 2: Bot startup condition checking is handled directly in trading bot
        # via startup cleanup and position validation logic
        
        logger.info(f"Trading day check for {market}: current_time={current_time.strftime('%H:%M:%S UTC')}, is_daily_reset_window={is_daily_reset_window}")
        
        return is_daily_reset_window
    
    def is_new_trading_hour(self, market: str) -> bool:
        """
        Check if we're outside funding settlement periods for hourly trading
        
        This method enables hourly trading while avoiding funding fee conflicts.
        Returns False during funding settlement periods (60 seconds max):
        - 00:00:00 - 00:01:00 UTC (daily funding)  
        - 08:00:00 - 08:01:00 UTC (8-hour funding)
        - 16:00:00 - 16:01:00 UTC (16-hour funding)
        
        Returns True during all other times (23h 57m trading per day)
        
        Args:
            market: Market symbol
            
        Returns:
            True if hourly trading is allowed (outside settlement periods)
        """
        current_time = datetime.now(timezone.utc)
        current_hour = current_time.hour
        current_minute = current_time.minute
        current_second = current_time.second
        
        # Check if we're in a funding settlement period (only 3 times per day)
        is_funding_hour = current_hour in [0, 8, 16]
        is_settlement_window = is_funding_hour and current_minute == 0 and current_second <= 60
        
        # Log for debugging
        if is_settlement_window:
            logger.info(f"Funding settlement period for {market}: {current_time.strftime('%H:%M:%S UTC')} - hourly trading paused")
        else:
            logger.debug(f"Hourly trading allowed for {market}: {current_time.strftime('%H:%M:%S UTC')}")
        
        # Return True when NOT in settlement (allow trading)
        return not is_settlement_window
    
    def get_available_markets(self) -> List[str]:
        """
        Get list of all available futures markets
        
        Returns:
            List of market symbols
        """
        try:
            markets = self.client.get_futures_markets()
            
            # Filter for active markets
            active_markets = [
                m['market'] for m in markets 
                if m.get('available', True) and m['market'].endswith('USDT')
            ]
            
            logger.info(f"Found {len(active_markets)} active USDT futures markets")
            return sorted(active_markets)
            
        except Exception as e:
            logger.error(f"Failed to fetch available markets: {e}")
            raise
    
    def get_data_source_stats(self) -> Dict[str, int]:
        """
        Get statistics on market data usage
        
        Returns:
            Dictionary with HTTP request counts
        """
        return {
            "http_requests": self._http_requests,
        }
