"""
Test cases for position management system
"""
import pytest
from unittest.mock import Mock
from datetime import datetime, timezone

from src.core.position_manager import PositionManager, Position, PositionSide


class TestPositionManager:
    """Test cases for position management"""
    
    @pytest.fixture
    def mock_client(self):
        """Create mock CoinEx client"""
        return Mock()
    
    @pytest.fixture
    def position_manager(self, mock_client):
        """Create position manager with mock client"""
        return PositionManager(mock_client)
    
    def test_add_to_position_new(self, position_manager):
        """Test adding to new position"""
        # Add to new position
        position = position_manager.add_to_position(
            market='BTCUSDT',
            fill_price=40000.0,
            fill_quantity=0.001,
            fill_fees=0.04  # 40 * 0.001 = 0.04
        )
        
        # Verify new position
        assert position.market == 'BTCUSDT'
        assert position.side == PositionSide.LONG
        assert position.size == 0.001
        assert position.avg_entry_price == 40000.0
        assert position.total_cost == 40.04  # (0.001 * 40000) + 0.04
        
        # Verify stored in manager
        assert 'BTCUSDT' in position_manager.positions
    
    def test_add_to_position_existing(self, position_manager):
        """Test adding to existing position (accumulation)"""
        # Create initial position
        position_manager.add_to_position(
            market='BTCUSDT',
            fill_price=40000.0,
            fill_quantity=0.001,
            fill_fees=0.04
        )
        
        # Add to existing position at different price
        position = position_manager.add_to_position(
            market='BTCUSDT',
            fill_price=38000.0,
            fill_quantity=0.002,
            fill_fees=0.076  # 38 * 0.002 = 0.076
        )
        
        # Verify accumulated position
        # Old: 0.001 * 40000 = 40
        # New: 0.002 * 38000 = 76
        # Total: 116, Size: 0.003
        # Avg: 116 / 0.003 = 38666.67
        assert position.size == 0.003
        assert abs(position.avg_entry_price - 38666.67) < 0.01
        assert position.total_cost == 116.116  # 40.04 + 76 + 0.076
    
    def test_reduce_position(self, position_manager):
        """Test reducing position size"""
        # Create initial position
        position_manager.add_to_position(
            market='BTCUSDT',
            fill_price=40000.0,
            fill_quantity=0.003,
            fill_fees=0.12
        )
        
        # Reduce position
        position = position_manager.reduce_position(
            market='BTCUSDT',
            fill_price=42000.0,
            fill_quantity=0.001,
            fill_fees=0.042
        )
        
        # Verify reduced position
        assert position.size == 0.002  # 0.003 - 0.001
        # Total cost should be reduced by cost of sold portion
        # Original cost: 120.12, Sold portion cost: 40000 * 0.001 = 40
        assert position.total_cost == 80.12  # 120.12 - 40
    
    def test_reduce_position_fully_close(self, position_manager):
        """Test fully closing a position"""
        # Create initial position
        position_manager.add_to_position(
            market='BTCUSDT',
            fill_price=40000.0,
            fill_quantity=0.001,
            fill_fees=0.04
        )
        
        # Close entire position
        position = position_manager.reduce_position(
            market='BTCUSDT',
            fill_price=42000.0,
            fill_quantity=0.001,
            fill_fees=0.042
        )
        
        # Position should be None and removed from manager
        assert position is None
        assert 'BTCUSDT' not in position_manager.positions
    
    def test_calculate_unrealized_pnl(self, position_manager):
        """Test unrealized P&L calculation"""
        # Create position
        position_manager.add_to_position(
            market='BTCUSDT',
            fill_price=40000.0,
            fill_quantity=0.001,
            fill_fees=0.04
        )
        
        position = position_manager.get_position('BTCUSDT')
        
        # Test P&L at different prices
        # Position: 0.001 BTC @ $40,000 (cost: $40.04)
        
        # At break-even price
        pnl = position.calculate_unrealized_pnl(40000.0)
        assert pnl == -0.04  # Only fees lost
        
        # At profit
        pnl = position.calculate_unrealized_pnl(42000.0)
        # Current value: 0.001 * 42000 = 42
        # Total cost: 40.04
        # P&L: 42 - 40.04 = 1.96
        assert abs(pnl - 1.96) < 0.01
        
        # At loss
        pnl = position.calculate_unrealized_pnl(38000.0)
        # Current value: 0.001 * 38000 = 38
        # P&L: 38 - 40.04 = -2.04
        assert abs(pnl - (-2.04)) < 0.01
    
    def test_calculate_pnl_percentage(self, position_manager):
        """Test P&L percentage calculation"""
        # Create position
        position_manager.add_to_position(
            market='BTCUSDT',
            fill_price=40000.0,
            fill_quantity=0.001,
            fill_fees=0.04
        )
        
        position = position_manager.get_position('BTCUSDT')
        
        # Test at 5% price increase
        pnl_percent = position.calculate_pnl_percentage(42000.0)
        # P&L: 1.96, Cost: 40.04
        # Percentage: (1.96 / 40.04) * 100 ≈ 4.89%
        assert abs(pnl_percent - 4.89) < 0.1
    
    def test_update_position_pnl(self, position_manager):
        """Test updating position P&L"""
        # Create position
        position_manager.add_to_position(
            market='BTCUSDT',
            fill_price=40000.0,
            fill_quantity=0.001,
            fill_fees=0.04
        )
        
        # Update P&L
        position = position_manager.update_position_pnl('BTCUSDT', 42000.0)
        
        # Verify P&L updated
        assert abs(position.unrealized_pnl - 1.96) < 0.01
    
    def test_sync_with_exchange(self, position_manager, mock_client):
        """Test syncing positions with exchange"""
        # Mock exchange response
        mock_client.get_positions.return_value = {
            'items': [
                {
                    'market': 'BTCUSDT',
                    'open_interest': '0.002',
                    'avg_entry_price': '41000',
                    'unrealized_pnl': '2.5',
                    'liq_price': '20000'
                },
                {
                    'market': 'ETHUSDT',
                    'open_interest': '0',  # Closed position
                    'avg_entry_price': '2500'
                }
            ]
        }
        
        # Sync positions
        count = position_manager.sync_with_exchange()
        
        # Should sync only the open position
        assert count == 1
        assert 'BTCUSDT' in position_manager.positions
        assert 'ETHUSDT' not in position_manager.positions
        
        # Verify position data
        position = position_manager.get_position('BTCUSDT')
        assert position.size == 0.002
        assert position.avg_entry_price == 41000.0
        assert position.unrealized_pnl == 2.5
    
    def test_calculate_total_unrealized_pnl(self, position_manager):
        """Test total P&L calculation across positions"""
        # Create multiple positions
        position_manager.add_to_position('BTCUSDT', 40000.0, 0.001, 0.04)
        position_manager.add_to_position('ETHUSDT', 2500.0, 0.04, 0.1)
        
        # Calculate total P&L
        market_prices = {
            'BTCUSDT': 42000.0,  # +$1.96 P&L
            'ETHUSDT': 2600.0    # +$4 - $0.1 = +$3.9 P&L
        }
        
        total_pnl = position_manager.calculate_total_unrealized_pnl(market_prices)
        # Total: 1.96 + 3.9 = 5.86
        assert abs(total_pnl - 5.86) < 0.1
    
    def test_get_position_summary(self, position_manager):
        """Test position summary generation"""
        # Create positions
        position_manager.add_to_position('BTCUSDT', 40000.0, 0.001, 0.04)
        position_manager.add_to_position('ETHUSDT', 2500.0, 0.04, 0.1)
        
        # Get summary
        market_prices = {
            'BTCUSDT': 42000.0,
            'ETHUSDT': 2600.0
        }
        
        summary = position_manager.get_position_summary(market_prices)
        
        # Verify summary
        assert summary['total_positions'] == 2
        assert summary['total_cost'] == 140.14  # 40.04 + 100.1
        assert abs(summary['total_unrealized_pnl'] - 5.86) < 0.1
        assert len(summary['positions']) == 2