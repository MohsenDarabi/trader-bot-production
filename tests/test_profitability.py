"""
Test cases for three-layer profitability validation system
"""
import pytest
from src.core.profitability import ProfitabilityValidator, ProfitabilityResult


class TestProfitabilityValidator:
    """Test cases for profitability validation"""
    
    @pytest.fixture
    def validator(self):
        """Create profitability validator"""
        return ProfitabilityValidator()
    
    def test_is_signal_profitable_positive(self, validator):
        """Test profitable signal validation"""
        # Test with profitable prices
        result = validator.is_signal_profitable(
            buy_price=40000.0,
            sell_price=42000.0,
            position_size=1000.0
        )
        
        assert result.is_profitable is True
        assert result.profit_percent > 1.2  # Should exceed minimum
        assert result.net_profit > 0
        assert result.reason is None
    
    def test_is_signal_profitable_negative(self, validator):
        """Test unprofitable signal validation"""
        # Test with barely profitable prices (below minimum)
        result = validator.is_signal_profitable(
            buy_price=40000.0,
            sell_price=40200.0,  # Very small spread
            position_size=1000.0
        )
        
        assert result.is_profitable is False
        assert result.profit_percent < 1.2  # Below minimum
        assert result.reason is not None
    
    def test_optimize_prices_for_profit_no_optimization(self, validator):
        """Test price optimization when already profitable"""
        # Already profitable prices
        buy_price, sell_price, optimized = validator.optimize_prices_for_profit(
            buy_price=40000.0,
            sell_price=42000.0,
            range_value=500.0,
            position_size=1000.0,
            high=42500.0,
            low=39500.0
        )
        
        # Should not need optimization
        assert optimized is False
        assert buy_price == 40000.0
        assert sell_price == 42000.0
    
    def test_optimize_prices_for_profit_with_optimization(self, validator):
        """Test price optimization when needed"""
        # Unprofitable prices that need optimization
        buy_price, sell_price, optimized = validator.optimize_prices_for_profit(
            buy_price=40000.0,
            sell_price=40200.0,  # Too small spread
            range_value=100.0,
            position_size=1000.0,
            high=41000.0,
            low=39000.0
        )
        
        # Should be optimized
        assert optimized is True
        assert buy_price < 40000.0  # Lower buy price
        assert sell_price > 40200.0  # Higher sell price
    
    def test_is_position_profitable_positive(self, validator):
        """Test profitable position validation"""
        # Position with good profit
        result = validator.is_position_profitable(
            entry_price=40000.0,
            quantity=0.025,  # 1000 / 40000
            sell_price=42000.0,
            entry_cost=1002.0  # 1000 + 2 fees
        )
        
        assert result.is_profitable is True
        assert result.profit_percent >= 1.2
        assert result.net_profit > 0
    
    def test_is_position_profitable_negative(self, validator):
        """Test unprofitable position validation"""
        # Position with insufficient profit
        result = validator.is_position_profitable(
            entry_price=40000.0,
            quantity=0.025,
            sell_price=40100.0,  # Small gain
            entry_cost=1002.0
        )
        
        assert result.is_profitable is False
        assert result.profit_percent < 1.2
        assert result.reason is not None
    
    def test_calculate_min_profitable_price(self, validator):
        """Test minimum profitable price calculation"""
        min_price = validator.calculate_min_profitable_price(
            quantity=0.025,
            entry_cost=1002.0
        )
        
        # Should be higher than entry price to account for fees and profit
        assert min_price > 40080.0  # Entry was ~40080 (1002 / 0.025)
        
        # Verify the calculation by testing profitability at this price
        result = validator.is_position_profitable(
            entry_price=40080.0,
            quantity=0.025,
            sell_price=min_price,
            entry_cost=1002.0
        )
        assert result.is_profitable is True
        assert result.profit_percent >= 1.2
    
    def test_validate_range_prices_valid(self, validator):
        """Test valid range price validation"""
        result = validator.validate_range_prices(
            high=42000.0,
            low=40000.0,
            buy_price=40500.0,
            sell_price=41500.0
        )
        
        assert result is True
    
    def test_validate_range_prices_invalid(self, validator):
        """Test invalid range price validation"""
        # Buy price too low
        result1 = validator.validate_range_prices(
            high=42000.0,
            low=40000.0,
            buy_price=39900.0,  # Below low
            sell_price=41500.0
        )
        assert result1 is False
        
        # Sell price too high
        result2 = validator.validate_range_prices(
            high=42000.0,
            low=40000.0,
            buy_price=40500.0,
            sell_price=42100.0  # Above high
        )
        assert result2 is False
        
        # Buy >= Sell
        result3 = validator.validate_range_prices(
            high=42000.0,
            low=40000.0,
            buy_price=41500.0,
            sell_price=41000.0  # Inverted
        )
        assert result3 is False
    
    def test_profitability_result_str(self):
        """Test ProfitabilityResult string representation"""
        result = ProfitabilityResult(
            is_profitable=True,
            net_profit=25.0,
            profit_percent=2.5,
            total_fees=5.0
        )
        
        str_result = str(result)
        assert "PROFITABLE" in str_result
        assert "2.50%" in str_result
        assert "$25.00" in str_result
        assert "$5.00" in str_result