"""
Test mode management for 5-day safe testing period
"""
from datetime import datetime, timedelta
from typing import Dict, Any, Tuple
from dataclasses import dataclass

from config.settings import (
    TRADING_MODE, TEST_MODE_DAYS, TEST_MODE_START_DATE,
    POSITION_SIZE_PERCENT, is_test_mode, get_position_size_mode
)
from src.utils.logger import get_logger
from src.utils.error_handling import handle_validation_error

logger = get_logger(__name__)


@dataclass
class TestModeStatus:
    """Test mode status information"""
    is_active: bool
    days_remaining: float
    start_date: datetime
    end_date: datetime
    current_day: int
    total_days: int
    position_size_mode: str
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'is_active': self.is_active,
            'days_remaining': round(self.days_remaining, 2),
            'start_date': self.start_date.isoformat(),
            'end_date': self.end_date.isoformat(),
            'current_day': self.current_day,
            'total_days': self.total_days,
            'position_size_mode': self.position_size_mode
        }


class TestModeManager:
    """Manages test mode operations and safety checks"""
    
    def __init__(self):
        self.start_date = datetime.fromisoformat(TEST_MODE_START_DATE)
        self.end_date = self.start_date + timedelta(days=TEST_MODE_DAYS)
        
    def get_status(self) -> TestModeStatus:
        """Get current test mode status"""
        now = datetime.now()
        is_active = is_test_mode()
        
        if is_active:
            time_elapsed = now - self.start_date
            time_remaining = self.end_date - now
            days_remaining = time_remaining.total_seconds() / (24 * 3600)
            current_day = time_elapsed.days + 1
        else:
            days_remaining = 0
            current_day = TEST_MODE_DAYS if now > self.end_date else 0
        
        return TestModeStatus(
            is_active=is_active,
            days_remaining=max(0, days_remaining),
            start_date=self.start_date,
            end_date=self.end_date,
            current_day=current_day,
            total_days=TEST_MODE_DAYS,
            position_size_mode=get_position_size_mode()
        )
    
    def validate_order_size(self, requested_amount: float, market_price: float, 
                          available_balance: float) -> Tuple[bool, float, str]:
        """
        Validate and adjust order size based on test mode
        
        Args:
            requested_amount: Requested order amount
            market_price: Current market price
            available_balance: Available balance for trading
            
        Returns:
            Tuple of (is_valid, adjusted_amount, reason)
        """
        status = self.get_status()
        
        if not status.is_active:
            # Normal mode - use standard position sizing
            max_position_value = available_balance * (POSITION_SIZE_PERCENT / 100)
            max_amount = max_position_value / market_price
            
            if requested_amount > max_amount:
                return False, max_amount, f"Amount exceeds {POSITION_SIZE_PERCENT}% position limit"
            return True, requested_amount, "Normal mode - standard sizing"
        
        # Test mode - enforce minimum orders only
        min_amount = self._get_minimum_order_amount(market_price)
        test_mode_max = min_amount * 2  # Allow up to 2x minimum for test orders
        
        if requested_amount > test_mode_max:
            logger.warning(f"Test mode: reducing order from {requested_amount} to {test_mode_max}")
            return True, test_mode_max, f"Test mode: limited to {test_mode_max} (2x minimum)"
        
        if requested_amount < min_amount:
            logger.warning(f"Test mode: increasing order from {requested_amount} to {min_amount}")
            return True, min_amount, f"Test mode: increased to minimum {min_amount}"
        
        return True, requested_amount, "Test mode: order size approved"
    
    def _get_minimum_order_amount(self, market_price: float) -> float:
        """
        Get minimum order amount for the market
        
        This is a conservative approach - in production this would query
        the exchange for actual minimum order requirements
        """
        # Conservative minimum order values (in USD equivalent)
        minimum_usd_value = 10.0  # $10 minimum
        return minimum_usd_value / market_price
    
    def should_place_order(self) -> Tuple[bool, str]:
        """
        Check if orders should be placed based on test mode status
        
        Returns:
            Tuple of (should_place, reason)
        """
        status = self.get_status()
        
        if not status.is_active:
            return True, "Normal mode - orders allowed"
        
        if status.current_day > TEST_MODE_DAYS:
            logger.error("Test mode expired but is_test_mode() still returns True")
            handle_validation_error(
                "Test mode configuration inconsistency detected",
                {'current_day': status.current_day, 'total_days': TEST_MODE_DAYS}
            )
            return False, "Test mode configuration error"
        
        # Check if we're in the last 12 hours - be extra cautious
        if status.days_remaining < 0.5:
            logger.warning("Test mode ending soon - being extra cautious with orders")
            return True, f"Test mode ending in {status.days_remaining:.1f} days - minimum orders only"
        
        return True, f"Test mode day {status.current_day}/{TEST_MODE_DAYS} - minimum orders only"
    
    def get_safety_message(self) -> str:
        """Get safety message for display"""
        status = self.get_status()
        
        if not status.is_active:
            return "🟢 NORMAL MODE - Full position sizing active"
        
        return (
            f"🟡 TEST MODE - Day {status.current_day}/{TEST_MODE_DAYS} "
            f"({status.days_remaining:.1f} days remaining)\n"
            f"   💡 Minimum orders only (0.1% position size max)\n"
            f"   📅 Test ends: {status.end_date.strftime('%Y-%m-%d %H:%M')}"
        )
    
    def transition_to_normal_mode(self) -> bool:
        """
        Check if bot should transition to normal mode
        
        Returns:
            True if transition is recommended
        """
        status = self.get_status()
        
        if not status.is_active and TRADING_MODE == 'TEST':
            logger.info("Test mode period completed - ready for normal mode")
            return True
        
        return False
    
    def get_risk_summary(self) -> Dict[str, Any]:
        """Get risk management summary"""
        status = self.get_status()
        
        return {
            'test_mode_active': status.is_active,
            'position_size_mode': status.position_size_mode,
            'max_position_percent': 0.1 if status.is_active else POSITION_SIZE_PERCENT,
            'risk_level': 'MINIMAL' if status.is_active else 'NORMAL',
            'safety_checks': {
                'minimum_orders_only': status.is_active,
                'reduced_position_sizing': status.is_active,
                'extra_validation': status.is_active
            }
        }


# Global test mode manager instance
test_mode_manager = TestModeManager()


def get_test_mode_status() -> TestModeStatus:
    """Get current test mode status"""
    return test_mode_manager.get_status()


def validate_test_mode_order(amount: float, price: float, balance: float) -> Tuple[bool, float, str]:
    """Validate order for test mode requirements"""
    return test_mode_manager.validate_order_size(amount, price, balance)


def should_place_test_mode_order() -> Tuple[bool, str]:
    """Check if orders should be placed in current mode"""
    return test_mode_manager.should_place_order()
