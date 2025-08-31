"""
Daily Range Accumulation Strategy Implementation
"""
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple
from dataclasses import dataclass

from config.settings import RANGE_DIVISOR, MAKER_FEE, TAKER_FEE
from src.data.market_data import MarketDataManager
from src.data.database import DatabaseManager
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
        self.db = DatabaseManager()
        self._load_signals_from_database()
    
    def calculate_signal_prices(self, high: float, low: float) -> Tuple[float, float, float]:
        """
        Calculate buy and sell prices based on daily range
        
        Formula:
        - Range = (High - Low) / 4
        - Buy Price = Low + Range
        - Sell Price = High - Range
        
        Args:
            high: Previous day high
            low: Previous day low
            
        Returns:
            Tuple of (buy_price, sell_price, range_value)
        """
        # Calculate range
        range_value = self.market_data.calculate_daily_range(high, low, self.range_divisor)
        
        # Calculate prices
        buy_price = low + range_value
        sell_price = high - range_value
        
        logger.info(f"Calculated prices - Buy: ${buy_price:.2f}, Sell: ${sell_price:.2f}, "
                   f"Range: ${range_value:.2f}")
        
        return buy_price, sell_price, range_value
    
    def _load_signals_from_database(self):
        """Load today's signals from database on startup"""
        try:
            today = datetime.now(timezone.utc).date().isoformat()
            logger.info(f"Loading signals from database for date: {today}")
            
            # For now, we might not know which markets to load, so we'll load them as needed
            # This method will be called during signal retrieval if signal not in memory
            logger.info("Signal loading from database will be done on-demand per market")
            
        except Exception as e:
            logger.error(f"Error loading signals from database: {e}")
    
    def _save_signal_to_database(self, signal: TradingSignal):
        """Save signal to database for persistence"""
        try:
            signal_dict = signal.to_dict()
            success = self.db.save_daily_signal(signal_dict)
            if success:
                logger.info(f"✅ Signal saved to database: {signal.market} on {signal.date}")
            else:
                logger.error(f"❌ Failed to save signal to database: {signal.market} on {signal.date}")
                
        except Exception as e:
            logger.error(f"Error saving signal to database: {e}")
    
    def _load_signal_from_database(self, market: str, date: str) -> Optional[TradingSignal]:
        """Load a specific signal from database"""
        try:
            signal_dict = self.db.load_daily_signal(market, date)
            if signal_dict:
                # Convert back to TradingSignal object
                signal = TradingSignal(
                    market=signal_dict['market'],
                    date=signal_dict['date'],
                    buy_price=signal_dict['buy_price'],
                    sell_price=signal_dict['sell_price'],
                    range_value=signal_dict['range_value'],
                    previous_high=signal_dict['previous_high'],
                    previous_low=signal_dict['previous_low'],
                    created_at=datetime.fromisoformat(signal_dict['created_at'])
                )
                logger.info(f"✅ Loaded signal from database: {market} on {date}")
                return signal
            else:
                logger.debug(f"No signal found in database for {market} on {date}")
                return None
                
        except Exception as e:
            logger.error(f"Error loading signal from database for {market} on {date}: {e}")
            return None
    
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
            
            # For 24/7 trading: Allow signal generation when needed, not just in daily reset window
            # The trading bot will handle when to use these signals appropriately
            logger.info(f"🕐 Checking signal generation conditions for {market} (force={force})")
            is_daily_reset_window = self.market_data.is_new_trading_day(market)
            
            # Allow signal generation if:
            # 1. Force flag is set, OR
            # 2. We're in the daily reset window, OR  
            # 3. We don't have a signal for today (startup scenario)
            should_generate = force or is_daily_reset_window or (market not in self._current_signals or self._current_signals[market].date != today)
            
            if not should_generate:
                logger.info(f"Signal generation not needed for {market} - valid signal already exists")
                return self._current_signals.get(market)
            
            logger.info(f"Proceeding with signal generation for {market} (force={force}, daily_reset={is_daily_reset_window})")
            
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
            
            # Cache the signal in memory
            self._current_signals[market] = signal
            
            # Save signal to database for persistence
            self._save_signal_to_database(signal)
            
            logger.info(f"Generated signal for {market} on {today}: "
                       f"Buy=${buy_price:.2f}, Sell=${sell_price:.2f}")
            
            # Enhanced daily signal display
            logger.info(f"📊 Daily Trading Signals for {market}: Buy=${buy_price:.4f} | Sell=${sell_price:.4f} | Range=${range_value:.4f}")
            
            return signal
            
        except Exception as e:
            logger.error(f"Failed to generate signal for {market}: {e}", exc_info=True)
            return None
    
    def generate_hourly_signal(self, market: str, force: bool = False) -> Optional[TradingSignal]:
        """
        Generate hourly trading signal for a market
        
        Args:
            market: Market symbol
            force: Force signal generation even if already exists
            
        Returns:
            Trading signal or None if not a new hour
        """
        try:
            logger.info(f"🎯 Starting hourly signal generation for {market}")
            # Check if we already have a signal for this hour
            current_hour = datetime.now(timezone.utc).strftime('%Y-%m-%d-%H')
            
            if not force and market in self._current_signals:
                signal = self._current_signals[market]
                if hasattr(signal, 'hour_key') and signal.hour_key == current_hour:
                    logger.debug(f"Using cached signal for {market} on hour {current_hour}")
                    return signal
            
            logger.info(f"🕐 Checking signal generation conditions for {market} (force={force})")
            is_hourly_reset_window = self.market_data.is_new_trading_hour(market)
            
            # Allow signal generation if:
            # 1. Force flag is set, OR
            # 2. We're in the hourly reset window, OR  
            # 3. We don't have a signal for this hour
            should_generate = force or is_hourly_reset_window or (market not in self._current_signals or 
                             not hasattr(self._current_signals[market], 'hour_key') or 
                             self._current_signals[market].hour_key != current_hour)
            
            if not should_generate:
                logger.info(f"Signal generation not needed for {market} - valid signal already exists")
                return self._current_signals.get(market)
            
            logger.info(f"Proceeding with hourly signal generation for {market} (force={force}, hourly_reset={is_hourly_reset_window})")
            
            logger.info(f"Generating new signal for {market} - getting previous hour OHLC...")
            
            # Get previous hour OHLC
            ohlc = self.market_data.get_previous_hour_ohlc(market)
            logger.info(f"Retrieved OHLC for {market}: {ohlc}")
            
            # Calculate signal prices (using same formula, only needs high and low)
            buy_price, sell_price, range_value = self.calculate_signal_prices(
                ohlc['high'], 
                ohlc['low']
            )
            
            # Create signal with hour_key
            signal = TradingSignal(
                market=market,
                date=current_hour,  # Using hour format for compatibility
                buy_price=buy_price,
                sell_price=sell_price,
                range_value=range_value,
                previous_high=ohlc['high'],
                previous_low=ohlc['low'],
                created_at=datetime.now(timezone.utc)
            )
            signal.hour_key = current_hour  # Add hour tracking
            
            # Cache the signal in memory
            self._current_signals[market] = signal
            
            # Save signal to database for persistence
            self._save_signal_to_database(signal)
            
            logger.info(f"Generated hourly signal for {market} on {current_hour}: "
                       f"Buy=${buy_price:.2f}, Sell=${sell_price:.2f}")
            
            logger.info(f"📊 Hourly Trading Signals for {market}: Buy=${buy_price:.4f} | Sell=${sell_price:.4f} | Range=${range_value:.4f}")
            
            return signal
            
        except Exception as e:
            logger.error(f"Failed to generate hourly signal for {market}: {e}", exc_info=True)
            return None

    def get_current_signal(self, market: str) -> Optional[TradingSignal]:
        """
        Get current day's signal if exists, checking memory first then database
        
        Args:
            market: Market symbol
            
        Returns:
            Current signal or None
        """
        today = datetime.now(timezone.utc).date().isoformat()
        
        # First check in-memory cache
        if market in self._current_signals:
            signal = self._current_signals[market]
            if signal.date == today:
                logger.debug(f"Found signal in memory for {market} on {today}")
                return signal
        
        # If not in memory, try loading from database
        logger.debug(f"Signal not in memory for {market}, checking database...")
        signal = self._load_signal_from_database(market, today)
        
        if signal:
            # Cache it in memory for future access
            self._current_signals[market] = signal
            logger.info(f"🔄 Loaded and cached signal from database: {market} on {today}")
            
            # Enhanced daily signal display for loaded signals
            logger.info(f"📊 Daily Trading Signals for {market}: Buy=${signal.buy_price:.4f} | Sell=${signal.sell_price:.4f} | Range=${signal.range_value:.4f}")
            return signal
        
        logger.debug(f"No signal found for {market} on {today}")
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
                                margin: float) -> Dict[str, float]:
        """
        Calculate expected profit for a trade
        
        Args:
            buy_price: Entry price
            sell_price: Exit price
            margin: User's margin/collateral in USDT
            
        Returns:
            Dictionary with profit calculations
        """
        # Calculate quantities
        buy_quantity = margin / buy_price
        
        # Calculate fees
        buy_fee = margin * TAKER_FEE
        sell_revenue = buy_quantity * sell_price
        sell_fee = sell_revenue * MAKER_FEE
        total_fees = buy_fee + sell_fee
        
        # Calculate profit
        gross_profit = sell_revenue - margin
        net_profit = gross_profit - total_fees
        profit_percent = (net_profit / margin) * 100
        
        return {
            'gross_profit': gross_profit,
            'total_fees': total_fees,
            'net_profit': net_profit,
            'profit_percent': profit_percent,
            'buy_quantity': buy_quantity
        }