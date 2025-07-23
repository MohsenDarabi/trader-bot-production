"""
Position sizing calculator with minimum order validation and test mode support
"""
from typing import Dict, Tuple, Optional
from dataclasses import dataclass
from enum import Enum

from config.settings import (
    POSITION_SIZE_PERCENT, LEVERAGE, 
    is_test_mode, get_position_size_mode
)
from src.data.market_data import MarketDataManager
from src.core.position_manager import PositionManager
from src.utils.logger import get_logger


logger = get_logger(__name__)


class SizingMode(Enum):
    """Position sizing mode"""
    MINIMUM = "minimum"  # Use minimum order size only
    NORMAL = "normal"    # Use calculated percentage
    CUSTOM = "custom"    # Use custom size


@dataclass
class PositionSize:
    """Position size calculation result"""
    market: str
    size_usdt: float  # Position size in USDT
    quantity: float   # Quantity in base currency
    mode: SizingMode  # Sizing mode used
    reason: str       # Explanation of sizing decision
    is_valid: bool    # Whether size meets requirements
    
    def __str__(self) -> str:
        status = "VALID" if self.is_valid else "INVALID"
        return (f"{status}: {self.market} ${self.size_usdt:.2f} "
                f"({self.quantity:.6f}) - {self.reason}")


class PositionSizer:
    """Calculates appropriate position sizes with validation"""
    
    def __init__(self, market_data: MarketDataManager, 
                 position_manager: PositionManager):
        """
        Initialize position sizer
        
        Args:
            market_data: Market data manager
            position_manager: Position manager
        """
        self.market_data = market_data
        self.position_manager = position_manager
        self.leverage = LEVERAGE
    
    def get_available_capital(self, total_balance: float) -> float:
        """
        Calculate available capital for new positions
        
        Args:
            total_balance: Total account balance in USDT
            
        Returns:
            Available capital for trading
        """
        # Calculate capital used in open positions
        used_capital = sum(
            pos.total_cost for pos in self.position_manager.get_all_positions()
        )
        
        # Available capital is total minus used
        available = total_balance - used_capital
        
        logger.debug(f"Capital: Total=${total_balance:.2f}, "
                    f"Used=${used_capital:.2f}, Available=${available:.2f}")
        
        return max(0, available)
    
    def calculate_position_size(self, market: str, price: float,
                              available_capital: float,
                              mode: Optional[SizingMode] = None) -> PositionSize:
        """
        Calculate position size for a market
        
        Args:
            market: Market symbol
            price: Current market price
            available_capital: Available capital in USDT
            mode: Optional sizing mode override
            
        Returns:
            PositionSize with calculation results
        """
        if mode is None:
            # Determine mode based on test settings
            if is_test_mode():
                mode = SizingMode.MINIMUM
            else:
                mode = SizingMode.NORMAL
        
        try:
            # Get market info for minimum order validation
            market_info = self.market_data.get_market_info(market)
            min_order_value = self.market_data.get_minimum_order_value(market, price)
            
            # Calculate size based on mode
            if mode == SizingMode.MINIMUM:
                size_usdt = min_order_value
                reason = f"Test mode: using minimum order (${min_order_value:.2f})"
                
            elif mode == SizingMode.NORMAL:
                # Calculate percentage-based size
                calculated_size = available_capital * (POSITION_SIZE_PERCENT / 100)
                
                # Ensure we meet minimum order requirement
                size_usdt = max(calculated_size, min_order_value)
                
                if calculated_size < min_order_value:
                    reason = (f"Adjusted to minimum: calculated ${calculated_size:.2f}, "
                            f"minimum ${min_order_value:.2f}")
                else:
                    reason = f"Normal mode: {POSITION_SIZE_PERCENT}% of available capital"
            
            else:  # CUSTOM mode
                size_usdt = min_order_value  # Default to minimum for custom
                reason = "Custom mode: set to minimum (customize as needed)"
            
            # Calculate quantity in base currency
            # For futures with leverage, we use the leverage amount for quantity calculation
            leveraged_size = size_usdt * self.leverage
            quantity = leveraged_size / price
            
            # Validate the position size
            is_valid, validation_reason = self._validate_position_size(
                market, size_usdt, quantity, available_capital, market_info
            )
            
            if not is_valid:
                reason += f" - INVALID: {validation_reason}"
            
            return PositionSize(
                market=market,
                size_usdt=size_usdt,
                quantity=quantity,
                mode=mode,
                reason=reason,
                is_valid=is_valid
            )
            
        except Exception as e:
            logger.error(f"Failed to calculate position size for {market}: {e}")
            return PositionSize(
                market=market,
                size_usdt=0.0,
                quantity=0.0,
                mode=mode or SizingMode.MINIMUM,
                reason=f"Calculation error: {e}",
                is_valid=False
            )
    
    def _validate_position_size(self, market: str, size_usdt: float, 
                              quantity: float, available_capital: float,
                              market_info: Dict) -> Tuple[bool, str]:
        """
        Validate calculated position size
        
        Args:
            market: Market symbol
            size_usdt: Position size in USDT
            quantity: Quantity in base currency
            available_capital: Available capital
            market_info: Market information
            
        Returns:
            Tuple of (is_valid, reason)
        """
        # Check available capital
        if size_usdt > available_capital:
            return False, f"Size ${size_usdt:.2f} exceeds available capital ${available_capital:.2f}"
        
        # Check minimum amount
        min_amount = float(market_info.get('min_amount', 0))
        if quantity < min_amount:
            return False, f"Quantity {quantity:.6f} below minimum {min_amount}"
        
        # Check if size is reasonable (not too small)
        if size_usdt < 1.0:  # Less than $1
            return False, f"Position size ${size_usdt:.2f} too small"
        
        # Check leverage limits (ensure we don't over-leverage)
        if self.leverage > 10:  # Conservative limit
            return False, f"Leverage {self.leverage}x too high"
        
        return True, "Valid"
    
    def calculate_buy_order_size(self, market: str, buy_price: float,
                               available_capital: float) -> PositionSize:
        """
        Calculate size for a buy order
        
        Args:
            market: Market symbol
            buy_price: Planned buy price
            available_capital: Available capital
            
        Returns:
            PositionSize for buy order
        """
        return self.calculate_position_size(market, buy_price, available_capital)
    
    def calculate_sell_order_size(self, market: str, sell_price: float) -> PositionSize:
        """
        Calculate size for a sell order (position reduction)
        
        Args:
            market: Market symbol
            sell_price: Planned sell price
            
        Returns:
            PositionSize for sell order
        """
        position = self.position_manager.get_position(market)
        if not position:
            return PositionSize(
                market=market,
                size_usdt=0.0,
                quantity=0.0,
                mode=SizingMode.NORMAL,
                reason="No position found to sell",
                is_valid=False
            )
        
        # For sell orders, we typically sell a portion or all of the position
        # For now, we'll sell the entire position
        sell_quantity = position.size
        sell_value = sell_quantity * sell_price
        
        return PositionSize(
            market=market,
            size_usdt=sell_value,
            quantity=sell_quantity,
            mode=SizingMode.NORMAL,
            reason=f"Selling entire position: {sell_quantity:.6f} @ ${sell_price:.2f}",
            is_valid=True
        )
    
    def get_max_position_count(self, available_capital: float,
                             min_position_size: float = 10.0) -> int:
        """
        Calculate maximum number of positions possible
        
        Args:
            available_capital: Available capital
            min_position_size: Minimum size per position
            
        Returns:
            Maximum number of positions
        """
        if available_capital <= 0:
            return 0
        
        # In normal mode, each position uses a percentage of capital
        if not is_test_mode():
            position_size = available_capital * (POSITION_SIZE_PERCENT / 100)
            max_positions = int(available_capital / max(position_size, min_position_size))
        else:
            # In test mode, positions are smaller (minimum size)
            max_positions = int(available_capital / min_position_size)
        
        logger.debug(f"Max positions: {max_positions} "
                    f"(${available_capital:.2f} / ${min_position_size:.2f})")
        
        return max_positions
    
    def should_skip_trade_due_to_size(self, market: str, price: float,
                                    available_capital: float) -> Tuple[bool, str]:
        """
        Check if we should skip a trade due to size constraints
        
        Args:
            market: Market symbol
            price: Trade price
            available_capital: Available capital
            
        Returns:
            Tuple of (should_skip, reason)
        """
        try:
            min_order_value = self.market_data.get_minimum_order_value(market, price)
            
            # Check if we have enough capital for minimum order
            if available_capital < min_order_value:
                return True, f"Insufficient capital: need ${min_order_value:.2f}, have ${available_capital:.2f}"
            
            # In test mode, check if we're using minimum sizing
            if is_test_mode():
                if min_order_value > available_capital * 0.5:  # More than 50% of capital
                    return True, f"Test mode: minimum order ${min_order_value:.2f} too large for capital ${available_capital:.2f}"
            
            return False, "Size constraints met"
            
        except Exception as e:
            return True, f"Size validation error: {e}"
    
    def get_sizing_summary(self, available_capital: float) -> Dict:
        """
        Get summary of current sizing configuration
        
        Args:
            available_capital: Available capital
            
        Returns:
            Summary dictionary
        """
        mode = get_position_size_mode()
        max_positions = self.get_max_position_count(available_capital)
        
        if is_test_mode():
            typical_size = "Minimum order size"
            size_percent = "N/A (minimum mode)"
        else:
            typical_size = f"${available_capital * (POSITION_SIZE_PERCENT / 100):.2f}"
            size_percent = f"{POSITION_SIZE_PERCENT}%"
        
        return {
            'mode': mode,
            'available_capital': available_capital,
            'position_size_percent': size_percent,
            'typical_position_size': typical_size,
            'leverage': self.leverage,
            'max_positions': max_positions,
            'test_mode': is_test_mode()
        }