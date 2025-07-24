"""
Daily Range Accumulation Strategy Implementation
"""
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple
from dataclasses import dataclass

from config.settings import RANGE_DIVISOR, MAKER_FEE, TAKER_FEE
from src.data.market_data import MarketDataManager
from src.utils.logger import get_logger


logger = get_logger(__name__)


@dataclass
class TradingSignal:
    """Trading signal with buy/sell prices"""
    market: str
    date: str
    buy_price: float
    sell_price: float
    range_value: float
    previous_high: float
    previous_low: float
    created_at: datetime
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for storage"""
        return {
            'market': self.market,
            'date': self.date,
            'buy_price': self.buy_price,
            'sell_price': self.sell_price,
            'range_value': self.range_value,
            'previous_high': self.previous_high,
            'previous_low': self.previous_low,
            'created_at': self.created_at.isoformat()
        }


class DailyRangeStrategy:
    """Daily Range Accumulation Strategy"""
    
    def __init__(self, market_data: MarketDataManager):
        """
        Initialize strategy
        
        Args:
            market_data: Market data manager instance
        """
        self.market_data = market_data
        self.range_divisor = RANGE_DIVISOR
        self._current_signals = {}
    
    def calculate_signal_prices(self, high: float, low: float) -> Tuple[float, float, float]:
        """
        Calculate buy and sell prices based on daily range
        
        Formula:
        - Range = (High - Low) / 4
        - Buy Price = Low + Range
        - Sell Price = High - Range
        
        Args:
            high: High price (could be yesterday's or hybrid)
            low: Low price (could be yesterday's or today's)
            
        Returns:
            Tuple of (buy_price, sell_price, range_value)
        """
        # Calculate range
        range_value = self.market_data.calculate_daily_range(high, low, self.range_divisor)
        
        # Calculate prices
        buy_price = low + range_value
        sell_price = high - range_value
        
        logger.info(f"Calculated prices - Buy: ${buy_price:.2f}, Sell: ${sell_price:.2f}, "
                   f"Range: ${range_value:.2f} (H={high:.2f}, L={low:.2f})")
        
        return buy_price, sell_price, range_value
    
    def generate_daily_signal(self, market: str, 
                            force: bool = False) -> Optional[TradingSignal]:
        """
        Generate daily trading signal for a market
        
        Args:
            market: Market symbol
            force: Force signal generation even if already exists
            
        Returns:
            Trading signal or None if not a new day
        """
        try:
            logger.info(f"🎯 Starting signal generation for {market}")
            # Check if we already have a signal for today
            today = datetime.now(timezone.utc).date().isoformat()
            
            if not force and market in self._current_signals:
                signal = self._current_signals[market]
                if signal.date == today:
                    logger.debug(f"Using cached signal for {market} on {today}")
                    return signal
            
            # Check if it's a new trading day
            logger.info(f"🕐 Checking if it's a new trading day for {market} (force={force})")
            is_new_day = self.market_data.is_new_trading_day(market)
            logger.info(f"🕐 New trading day check result: {is_new_day}")
            
            if not force and not is_new_day:
                logger.info(f"Not a new trading day for {market}, skipping signal generation")
                return None
            
            logger.info(f"Generating new signal for {market} - getting previous day OHLC...")
            
            # Get previous day OHLC
            ohlc = self.market_data.get_previous_day_ohlc(market)
            logger.info(f"Retrieved OHLC for {market}: {ohlc}")
            
            # Calculate signal prices
            buy_price, sell_price, range_value = self.calculate_signal_prices(
                ohlc['high'], 
                ohlc['low']
            )
            
            # Create signal
            signal = TradingSignal(
                market=market,
                date=today,
                buy_price=buy_price,
                sell_price=sell_price,
                range_value=range_value,
                previous_high=ohlc['high'],
                previous_low=ohlc['low'],
                created_at=datetime.now(timezone.utc)
            )
            
            # Cache the signal
            self._current_signals[market] = signal
            
            logger.info(f"Generated signal for {market} on {today}: "
                       f"Buy=${buy_price:.2f}, Sell=${sell_price:.2f}")
            
            return signal
            
        except Exception as e:
            logger.error(f"Failed to generate signal for {market}: {e}", exc_info=True)
            return None
    
    def generate_signal_with_custom_prices(self, market: str, high: float, low: float,
                                         description: str = "custom") -> TradingSignal:
        """
        Generate trading signal with custom high/low prices
        Used for hybrid calculations after cycle completion
        
        Args:
            market: Market symbol
            high: High price to use (e.g., yesterday's high)
            low: Low price to use (e.g., today's low)
            description: Description of calculation type
            
        Returns:
            Trading signal
        """
        logger.info(f"Generating {description} signal for {market} with H={high:.2f}, L={low:.2f}")
        
        # Calculate signal prices
        buy_price, sell_price, range_value = self.calculate_signal_prices(high, low)
        
        # Create signal
        today = datetime.now(timezone.utc).date().isoformat()
        signal = TradingSignal(
            market=market,
            date=today,
            buy_price=buy_price,
            sell_price=sell_price,
            range_value=range_value,
            previous_high=high,
            previous_low=low,
            created_at=datetime.now(timezone.utc)
        )
        
        # Update cached signal
        self._current_signals[market] = signal
        
        logger.info(f"Generated {description} signal for {market}: "
                   f"Buy=${buy_price:.2f}, Sell=${sell_price:.2f}")
        
        return signal
    
    def get_current_signal(self, market: str) -> Optional[TradingSignal]:
        """
        Get current day's signal if exists
        
        Args:
            market: Market symbol
            
        Returns:
            Current signal or None
        """
        today = datetime.now(timezone.utc).date().isoformat()
        
        if market in self._current_signals:
            signal = self._current_signals[market]
            if signal.date == today:
                return signal
        
        return None
    
    def should_place_buy_order(self, market: str, current_price: float) -> bool:
        """
        Check if we should place a buy order
        
        Args:
            market: Market symbol
            current_price: Current market price
            
        Returns:
            True if should place buy order
        """
        signal = self.get_current_signal(market)
        if not signal:
            return False
        
        # Price should be near or below buy price
        # Allow small buffer for limit order execution
        price_buffer = signal.buy_price * 0.001  # 0.1% buffer
        
        should_buy = current_price <= signal.buy_price + price_buffer
        
        if should_buy:
            logger.info(f"{market} current price ${current_price:.2f} is near/below "
                       f"buy price ${signal.buy_price:.2f}")
        
        return should_buy
    
    def should_place_sell_order(self, market: str, current_price: float,
                               position_entry_price: float) -> bool:
        """
        Check if we should place a sell order for a position
        
        Args:
            market: Market symbol
            current_price: Current market price
            position_entry_price: Average entry price of position
            
        Returns:
            True if should place sell order
        """
        signal = self.get_current_signal(market)
        if not signal:
            return False
        
        # Check if price is near sell signal
        price_buffer = signal.sell_price * 0.001  # 0.1% buffer
        near_sell_price = current_price >= signal.sell_price - price_buffer
        
        # For now, just check if we're near the sell price
        # Profitability will be checked in the profitability module
        should_sell = near_sell_price
        
        if should_sell:
            logger.info(f"{market} current price ${current_price:.2f} is near/above "
                       f"sell price ${signal.sell_price:.2f}")
        
        return should_sell
    
    def calculate_expected_profit(self, buy_price: float, sell_price: float,
                                position_size: float) -> Dict[str, float]:
        """
        Calculate expected profit for a trade
        
        Args:
            buy_price: Entry price
            sell_price: Exit price
            position_size: Position size in USDT
            
        Returns:
            Dictionary with profit calculations
        """
        # Calculate quantities
        buy_quantity = position_size / buy_price
        
        # Calculate fees
        buy_fee = position_size * TAKER_FEE
        sell_revenue = buy_quantity * sell_price
        sell_fee = sell_revenue * MAKER_FEE
        total_fees = buy_fee + sell_fee
        
        # Calculate profit
        gross_profit = sell_revenue - position_size
        net_profit = gross_profit - total_fees
        profit_percent = (net_profit / position_size) * 100
        
        return {
            'gross_profit': gross_profit,
            'total_fees': total_fees,
            'net_profit': net_profit,
            'profit_percent': profit_percent,
            'buy_quantity': buy_quantity
        }