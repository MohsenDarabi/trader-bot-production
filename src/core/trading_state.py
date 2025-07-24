"""
Trading State Management
Centralized state tracking for event-driven trading bot
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from enum import Enum

from src.models.order import Order, OrderStatus
from src.models.position import Position
from src.models.signal import DailyRangeSignal
from src.utils.logger import get_logger


logger = get_logger(__name__)


class CalculationMode(Enum):
    """Price calculation mode"""
    YESTERDAY_ONLY = "yesterday_only"  # Day start - use yesterday's OHLC
    HYBRID_TODAY_LOW = "hybrid_today_low"  # After cycle - use today's low with yesterday's high


@dataclass
class MarketOHLC:
    """Market OHLC data"""
    high: float
    low: float
    open: float
    close: float
    timestamp: datetime


@dataclass
class TradingState:
    """
    Comprehensive trading state management
    Tracks all state for event-driven architecture
    """
    # Market being traded
    market: str
    
    # Price data
    yesterday_ohlc: Optional[MarketOHLC] = None
    today_ohlc: Optional[MarketOHLC] = None
    
    # Calculation state
    calculation_mode: CalculationMode = CalculationMode.YESTERDAY_ONLY
    calculation_generation: int = 0  # Increments with each recalculation
    
    # Current signals
    current_signals: Optional[DailyRangeSignal] = None
    
    # Position tracking
    current_position: Optional[Position] = None
    total_position_size: float = 0.0
    covered_by_sells: float = 0.0
    uncovered_amount: float = 0.0
    
    # Order tracking
    active_buy_order: Optional[Order] = None
    active_sell_orders: List[Order] = field(default_factory=list)
    
    # Daily statistics
    completed_cycles_today: int = 0
    last_daily_reset: Optional[datetime] = None
    
    # Tracking buy-sell pairs
    pending_buy_sell_pairs: Dict[str, Tuple[str, float]] = field(default_factory=dict)  # buy_order_id -> (sell_order_id, amount)
    
    def update_position(self, position: Optional[Position]) -> None:
        """Update position state"""
        self.current_position = position
        if position:
            self.total_position_size = position.size
            self._recalculate_uncovered_amount()
        else:
            self.total_position_size = 0.0
            self.uncovered_amount = 0.0
            
        logger.info(f"Position updated: size={self.total_position_size}, uncovered={self.uncovered_amount}")
    
    def update_sell_orders(self, orders: List[Order]) -> None:
        """Update active sell orders"""
        self.active_sell_orders = [o for o in orders if o.status in [OrderStatus.PENDING, OrderStatus.PARTIAL]]
        self._recalculate_uncovered_amount()
        
        logger.info(f"Sell orders updated: {len(self.active_sell_orders)} active orders")
    
    def _recalculate_uncovered_amount(self) -> None:
        """Recalculate amount not covered by sell orders"""
        self.covered_by_sells = sum(order.amount for order in self.active_sell_orders)
        self.uncovered_amount = max(0, self.total_position_size - self.covered_by_sells)
        
        logger.debug(f"Recalculated coverage: position={self.total_position_size}, "
                    f"covered={self.covered_by_sells}, uncovered={self.uncovered_amount}")
    
    def add_buy_order(self, order: Order) -> None:
        """Add active buy order"""
        self.active_buy_order = order
        logger.info(f"Buy order added: {order.client_id} at {order.price}")
    
    def clear_buy_order(self) -> None:
        """Clear active buy order"""
        if self.active_buy_order:
            logger.info(f"Clearing buy order: {self.active_buy_order.client_id}")
        self.active_buy_order = None
    
    def add_sell_order(self, order: Order, corresponding_buy_id: Optional[str] = None) -> None:
        """Add sell order and track pairing if applicable"""
        self.active_sell_orders.append(order)
        if corresponding_buy_id:
            self.pending_buy_sell_pairs[corresponding_buy_id] = (order.client_id, order.amount)
        self._recalculate_uncovered_amount()
        
        logger.info(f"Sell order added: {order.client_id} at {order.price}, amount={order.amount}")
    
    def complete_cycle(self, buy_order_id: str) -> None:
        """Mark a buy-sell cycle as complete"""
        if buy_order_id in self.pending_buy_sell_pairs:
            sell_order_id, amount = self.pending_buy_sell_pairs.pop(buy_order_id)
            self.completed_cycles_today += 1
            
            # After first cycle, switch to hybrid calculation mode
            if self.completed_cycles_today == 1:
                self.calculation_mode = CalculationMode.HYBRID_TODAY_LOW
                logger.info("Switched to hybrid calculation mode after first cycle")
            
            logger.info(f"Completed cycle #{self.completed_cycles_today}: "
                       f"buy={buy_order_id}, sell={sell_order_id}, amount={amount}")
    
    def update_today_ohlc(self, high: float, low: float, open_price: float, close: float) -> None:
        """Update today's OHLC data"""
        self.today_ohlc = MarketOHLC(
            high=high,
            low=low,
            open=open_price,
            close=close,
            timestamp=datetime.now(timezone.utc)
        )
        logger.debug(f"Updated today's OHLC: H={high}, L={low}, O={open_price}, C={close}")
    
    def reset_daily_state(self, yesterday_ohlc: MarketOHLC) -> None:
        """Reset state for new trading day"""
        self.yesterday_ohlc = yesterday_ohlc
        self.today_ohlc = None
        self.calculation_mode = CalculationMode.YESTERDAY_ONLY
        self.calculation_generation = 0
        self.completed_cycles_today = 0
        self.last_daily_reset = datetime.now(timezone.utc)
        self.pending_buy_sell_pairs.clear()
        
        logger.info(f"Daily state reset at {self.last_daily_reset}")
    
    def should_recalculate_signals(self) -> bool:
        """Check if signals should be recalculated"""
        # Recalculate after completing cycles when in hybrid mode
        return (self.calculation_mode == CalculationMode.HYBRID_TODAY_LOW and 
                self.completed_cycles_today > self.calculation_generation)
    
    def increment_calculation_generation(self) -> None:
        """Increment calculation generation after recalculation"""
        self.calculation_generation += 1
        logger.info(f"Signal calculation generation: {self.calculation_generation}")
    
    def get_price_calculation_params(self) -> Tuple[float, float]:
        """
        Get high and low prices for signal calculation based on current mode
        
        Returns:
            Tuple of (high, low) prices to use
        """
        if self.calculation_mode == CalculationMode.YESTERDAY_ONLY:
            # Use pure yesterday data
            if not self.yesterday_ohlc:
                raise ValueError("Yesterday OHLC data not available")
            return self.yesterday_ohlc.high, self.yesterday_ohlc.low
        
        else:  # HYBRID_TODAY_LOW
            # Use yesterday's high with today's low
            if not self.yesterday_ohlc or not self.today_ohlc:
                raise ValueError("OHLC data not available for hybrid calculation")
            return self.yesterday_ohlc.high, self.today_ohlc.low
    
    def is_new_trading_day(self) -> bool:
        """Check if it's a new trading day (UTC midnight)"""
        now = datetime.now(timezone.utc)
        if not self.last_daily_reset:
            return True
        
        # Check if we've crossed midnight UTC
        return now.date() > self.last_daily_reset.date()
    
    def log_state_summary(self) -> None:
        """Log current state summary"""
        logger.info(f"""
Trading State Summary for {self.market}:
- Calculation Mode: {self.calculation_mode.value}
- Generation: {self.calculation_generation}
- Position: {self.total_position_size} ({self.uncovered_amount} uncovered)
- Active Buy: {bool(self.active_buy_order)}
- Active Sells: {len(self.active_sell_orders)}
- Completed Cycles: {self.completed_cycles_today}
- Current Signal: Buy={self.current_signals.buy_price if self.current_signals else 'N/A'}, 
                  Sell={self.current_signals.sell_price if self.current_signals else 'N/A'}
        """.strip())