"""
Test cases for position sizing calculator
"""
import pytest
from unittest.mock import Mock, patch

from src.core.position_sizing import PositionSizer, PositionSize, SizingMode
from src.core.position_manager import Position, PositionSide
from datetime import datetime, timezone


class TestPositionSizer:
    """Test cases for position sizing"""
    
    @pytest.fixture
    def mock_market_data(self):
        """Create mock market data manager"""
        mock = Mock()
        mock.get_market_info.return_value = {
            'market': 'BTCUSDT',
            'min_amount': '0.001',
            'tick_size': '0.5'
        }
        mock.get_minimum_order_value.return_value = 50.0  # $50 minimum
        return mock
    
    @pytest.fixture
    def mock_position_manager(self):
        """Create mock position manager"""
        mock = Mock()
        mock.get_all_positions.return_value = []
        return mock
    
    @pytest.fixture
    def position_sizer(self, mock_market_data, mock_position_manager):
        """Create position sizer with mocks"""
        return PositionSizer(mock_market_data, mock_position_manager)
    
    def test_get_available_capital_no_positions(self, position_sizer):
        """Test available capital calculation with no positions"""
        available = position_sizer.get_available_capital(total_balance=10000.0)
        assert available == 10000.0
    
    def test_get_available_capital_with_positions(self, position_sizer, mock_position_manager):
        """Test available capital calculation with open positions"""
        # Mock position with cost
        position = Position(
            market='BTCUSDT', side=PositionSide.LONG, size=0.001,
            avg_entry_price=40000.0, total_cost=1000.0,
            unrealized_pnl=0, liquidation_price=0,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc)
        )
        mock_position_manager.get_all_positions.return_value = [position]
        
        available = position_sizer.get_available_capital(total_balance=10000.0)
        assert available == 9000.0  # 10000 - 1000
    
    @patch('src.core.position_sizing.is_test_mode')
    def test_calculate_position_size_test_mode(self, mock_test_mode, position_sizer):
        """Test position sizing in test mode"""
        mock_test_mode.return_value = True
        
        size = position_sizer.calculate_position_size(
            market='BTCUSDT',
            price=40000.0,
            available_capital=5000.0
        )
        
        # Should use minimum order size in test mode
        assert size.mode == SizingMode.MINIMUM
        assert size.size_usdt == 50.0  # Minimum order value
        assert size.is_valid is True
        assert "test mode" in size.reason.lower()
    
    @patch('src.core.position_sizing.is_test_mode')
    def test_calculate_position_size_normal_mode(self, mock_test_mode, position_sizer):
        """Test position sizing in normal mode"""
        mock_test_mode.return_value = False
        
        with patch('src.core.position_sizing.POSITION_SIZE_PERCENT', 10):
            size = position_sizer.calculate_position_size(
                market='BTCUSDT',
                price=40000.0,
                available_capital=5000.0
            )
        
        # Should use 10% of available capital
        assert size.mode == SizingMode.NORMAL
        assert size.size_usdt == 500.0  # 10% of 5000
        assert size.is_valid is True
    
    @patch('src.core.position_sizing.is_test_mode')
    def test_calculate_position_size_minimum_adjustment(self, mock_test_mode, position_sizer):
        """Test position sizing when calculated size is below minimum"""
        mock_test_mode.return_value = False
        
        with patch('src.core.position_sizing.POSITION_SIZE_PERCENT', 10):
            size = position_sizer.calculate_position_size(
                market='BTCUSDT',
                price=40000.0,
                available_capital=100.0  # Small capital
            )
        
        # Should adjust to minimum order size
        # 10% of 100 = 10, but minimum is 50
        assert size.size_usdt == 50.0  # Adjusted to minimum
        assert "adjusted to minimum" in size.reason.lower()
    
    def test_calculate_position_size_insufficient_capital(self, position_sizer):
        """Test position sizing with insufficient capital"""
        size = position_sizer.calculate_position_size(
            market='BTCUSDT',
            price=40000.0,
            available_capital=10.0  # Less than minimum
        )
        
        # Should be invalid due to insufficient capital
        assert size.is_valid is False
        assert "exceeds available capital" in size.reason
    
    def test_calculate_buy_order_size(self, position_sizer):
        """Test buy order size calculation"""
        with patch('src.core.position_sizing.is_test_mode', return_value=True):
            size = position_sizer.calculate_buy_order_size(
                market='BTCUSDT',
                buy_price=40000.0,
                available_capital=1000.0
            )
        
        assert size.market == 'BTCUSDT'
        assert size.size_usdt == 50.0  # Minimum in test mode
        assert size.is_valid is True
    
    def test_calculate_sell_order_size_with_position(self, position_sizer, mock_position_manager):
        """Test sell order size calculation with existing position"""
        # Mock existing position
        position = Position(
            market='BTCUSDT', side=PositionSide.LONG, size=0.002,
            avg_entry_price=40000.0, total_cost=80.0,
            unrealized_pnl=0, liquidation_price=0,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc)
        )
        mock_position_manager.get_position.return_value = position
        
        size = position_sizer.calculate_sell_order_size(
            market='BTCUSDT',
            sell_price=42000.0
        )
        
        # Should sell entire position
        assert size.quantity == 0.002
        assert size.size_usdt == 84.0  # 0.002 * 42000
        assert size.is_valid is True
        assert "entire position" in size.reason.lower()
    
    def test_calculate_sell_order_size_no_position(self, position_sizer, mock_position_manager):
        """Test sell order size calculation without position"""
        mock_position_manager.get_position.return_value = None
        
        size = position_sizer.calculate_sell_order_size(
            market='BTCUSDT',
            sell_price=42000.0
        )
        
        # Should be invalid - no position to sell
        assert size.is_valid is False
        assert "no position found" in size.reason.lower()
    
    @patch('src.core.position_sizing.is_test_mode')
    def test_get_max_position_count_normal_mode(self, mock_test_mode, position_sizer):
        """Test maximum position count in normal mode"""
        mock_test_mode.return_value = False
        
        with patch('src.core.position_sizing.POSITION_SIZE_PERCENT', 10):
            max_positions = position_sizer.get_max_position_count(
                available_capital=10000.0,
                min_position_size=50.0
            )
        
        # 10% of 10000 = 1000 per position
        # 10000 / 1000 = 10 positions
        assert max_positions == 10
    
    @patch('src.core.position_sizing.is_test_mode')
    def test_get_max_position_count_test_mode(self, mock_test_mode, position_sizer):
        """Test maximum position count in test mode"""
        mock_test_mode.return_value = True
        
        max_positions = position_sizer.get_max_position_count(
            available_capital=1000.0,
            min_position_size=50.0
        )
        
        # Test mode uses minimum position size
        # 1000 / 50 = 20 positions
        assert max_positions == 20
    
    def test_should_skip_trade_sufficient_capital(self, position_sizer):
        """Test trade skip check with sufficient capital"""
        should_skip, reason = position_sizer.should_skip_trade_due_to_size(
            market='BTCUSDT',
            price=40000.0,
            available_capital=1000.0
        )
        
        assert should_skip is False
        assert "constraints met" in reason
    
    def test_should_skip_trade_insufficient_capital(self, position_sizer):
        """Test trade skip check with insufficient capital"""
        should_skip, reason = position_sizer.should_skip_trade_due_to_size(
            market='BTCUSDT',
            price=40000.0,
            available_capital=10.0  # Less than minimum $50
        )
        
        assert should_skip is True
        assert "insufficient capital" in reason.lower()
    
    @patch('src.core.position_sizing.is_test_mode')
    def test_should_skip_trade_test_mode_large_minimum(self, mock_test_mode, position_sizer):
        """Test trade skip in test mode when minimum is too large"""
        mock_test_mode.return_value = True
        
        should_skip, reason = position_sizer.should_skip_trade_due_to_size(
            market='BTCUSDT',
            price=40000.0,
            available_capital=60.0  # Just above minimum but large portion
        )
        
        # Minimum ($50) is more than 50% of capital ($60)
        assert should_skip is True
        assert "test mode" in reason.lower()
    
    @patch('src.core.position_sizing.is_test_mode')
    @patch('src.core.position_sizing.get_position_size_mode')
    def test_get_sizing_summary(self, mock_mode, mock_test_mode, position_sizer):
        """Test sizing summary generation"""
        mock_test_mode.return_value = True
        mock_mode.return_value = "MINIMUM"
        
        summary = position_sizer.get_sizing_summary(available_capital=5000.0)
        
        assert summary['mode'] == "MINIMUM"
        assert summary['available_capital'] == 5000.0
        assert summary['test_mode'] is True
        assert summary['typical_position_size'] == "Minimum order size"
        assert isinstance(summary['max_positions'], int)