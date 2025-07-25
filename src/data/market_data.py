"""
Market data fetching and processing for CoinEx futures
"""
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
import pandas as pd

from src.exchange.coinex_client import CoinExClient
from src.data.websocket_market_data import WebSocketMarketDataProvider
from src.utils.logger import get_logger


logger = get_logger(__name__)


class MarketDataManager:
    """Manages market data fetching and processing"""
    
    def __init__(self, client: Optional[CoinExClient] = None, websocket_provider: Optional[WebSocketMarketDataProvider] = None):
        """
        Initialize market data manager
        
        Args:
            client: CoinEx API client instance
            websocket_provider: WebSocket market data provider for real-time data
        """
        self.client = client or CoinExClient()
        self.websocket_provider = websocket_provider
        self._market_info_cache = {}
        self._ohlc_cache = {}
        
        # Statistics for monitoring WebSocket vs HTTP usage
        self._websocket_hits = 0
        self._http_fallback_hits = 0
    
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
        min_amount = float(market_info.get('min_amount', 0))
        
        # For futures, min_amount is in base currency, so multiply by price
        min_value = min_amount * current_price
        
        logger.debug(f"{market} minimum order: {min_amount} @ ${current_price} = ${min_value}")
        return min_value
    
    def get_daily_candles(self, market: str, days: int = 2) -> pd.DataFrame:
        """
        Fetch daily OHLC candles
        
        Args:
            market: Market symbol
            days: Number of days to fetch (minimum 2 for previous day calculation)
            
        Returns:
            DataFrame with OHLC data
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
            
            # Convert to DataFrame
            df = pd.DataFrame(klines)
            
            # Ensure we have the required columns
            required_cols = ['created_at', 'open', 'high', 'low', 'close', 'volume']
            if not all(col in df.columns for col in required_cols):
                raise ValueError(f"Missing required columns in kline data")
            
            # Convert timestamp to datetime
            df['date'] = pd.to_datetime(df['created_at'], unit='ms')
            
            # Convert price columns to float
            for col in ['open', 'high', 'low', 'close', 'volume']:
                df[col] = df[col].astype(float)
            
            # Sort by date
            df = df.sort_values('date', ascending=False)
            
            logger.info(f"Fetched {len(df)} daily candles for {market}")
            return df
            
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
        df = self.get_daily_candles(market, days=2)
        
        if len(df) < 2:
            raise ValueError(f"Insufficient data for {market}, need at least 2 days")
        
        # Get previous day (second row, as we sorted descending)
        prev_day = df.iloc[1]
        
        ohlc = {
            'date': prev_day['date'].strftime('%Y-%m-%d'),
            'open': float(prev_day['open']),
            'high': float(prev_day['high']),
            'low': float(prev_day['low']),
            'close': float(prev_day['close']),
            'volume': float(prev_day['volume'])
        }
        
        logger.info(f"Previous day OHLC for {market} ({ohlc['date']}): "
                   f"O={ohlc['open']:.2f}, H={ohlc['high']:.2f}, "
                   f"L={ohlc['low']:.2f}, C={ohlc['close']:.2f}")
        
        return ohlc
    
    def get_current_price(self, market: str) -> float:
        """
        Get current market price, preferring WebSocket data with HTTP fallback
        
        Args:
            market: Market symbol
            
        Returns:
            Current price
        """
        # Try WebSocket data first if provider is available
        if self.websocket_provider:
            ws_price = self.websocket_provider.get_current_price(market)
            if ws_price is not None:
                self._websocket_hits += 1
                logger.debug(f"Retrieved WebSocket price for {market}: ${ws_price:.2f}")
                return ws_price
        
        # Fall back to HTTP API
        try:
            self._http_fallback_hits += 1
            ticker_response = self.client.get_ticker(market)
            # CoinEx ticker returns a list with one item
            if isinstance(ticker_response, list) and ticker_response:
                ticker = ticker_response[0]
            else:
                ticker = ticker_response
            
            price = float(ticker.get('last', 0))
            
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
        Check if it's a new trading day (after 00:00 UTC)
        
        Args:
            market: Market symbol
            
        Returns:
            True if new trading day
        """
        # Get latest candle
        df = self.get_daily_candles(market, days=1)
        if df.empty:
            return False
        
        latest_candle_date = df.iloc[0]['date'].date()
        current_date = datetime.now(timezone.utc).date()
        
        # For Daily Range Strategy, we want to generate signals when we have today's candle
        # This allows us to use yesterday's OHLC data for today's trading signals
        is_new_day = latest_candle_date >= current_date
        
        logger.info(f"Trading day check for {market}: latest_candle={latest_candle_date}, current={current_date}, is_new_day={is_new_day}")
        
        return is_new_day
    
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
    
    def set_websocket_provider(self, provider: WebSocketMarketDataProvider):
        """
        Set the WebSocket market data provider
        
        Args:
            provider: WebSocket market data provider instance
        """
        self.websocket_provider = provider
        logger.info("WebSocket market data provider connected to MarketDataManager")
    
    def get_data_source_stats(self) -> Dict[str, int]:
        """
        Get statistics on data source usage
        
        Returns:
            Dictionary with WebSocket hits and HTTP fallback counts
        """
        return {
            "websocket_hits": self._websocket_hits,
            "http_fallback_hits": self._http_fallback_hits,
            "total_requests": self._websocket_hits + self._http_fallback_hits,
            "websocket_percentage": (
                (self._websocket_hits / (self._websocket_hits + self._http_fallback_hits)) * 100
                if (self._websocket_hits + self._http_fallback_hits) > 0 else 0
            )
        }