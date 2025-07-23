"""
Test cases for startup recovery system
"""
import pytest
from unittest.mock import Mock, patch
from datetime import datetime, timezone
import tempfile
import os

from src.core.recovery import StartupRecovery, RecoveryReport
from src.exchange.order_manager import OrderManager, Order, OrderStatus, OrderSide
from src.core.position_manager import PositionManager, Position, PositionSide
from src.data.database import DatabaseManager
from src.core.strategy import DailyRangeStrategy


class TestStartupRecovery:
    """Test cases for startup recovery system"""
    
    @pytest.fixture
    def temp_db(self):
        """Create temporary database for testing"""
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
        temp_file.close()
        
        db = DatabaseManager(temp_file.name)
        yield db
        
        # Cleanup
        os.unlink(temp_file.name)
    
    @pytest.fixture
    def mock_client(self):
        """Create mock CoinEx client"""
        return Mock()
    
    @pytest.fixture
    def recovery_system(self, mock_client, temp_db):
        """Create recovery system with mocks"""
        return StartupRecovery(mock_client, temp_db)
    
    @pytest.fixture
    def order_manager(self, mock_client):
        """Create order manager"""
        mock_validator = Mock()
        return OrderManager(mock_client, mock_validator)
    
    @pytest.fixture
    def position_manager(self, mock_client):
        """Create position manager"""
        return PositionManager(mock_client)
    
    @pytest.fixture
    def strategy(self, mock_client):
        """Create strategy"""
        market_data = Mock()
        return DailyRangeStrategy(market_data)
    
    def test_recover_positions_from_database(self, recovery_system, position_manager, temp_db):
        """Test position recovery from database"""
        # Save test position to database
        position_data = {
            'market': 'BTCUSDT',
            'side': 'long',
            'size': 0.001,
            'avg_entry_price': 40000.0,
            'total_cost': 40.04,
            'unrealized_pnl': 1.96,
            'liquidation_price': 20000.0,
            'created_at': datetime.now(timezone.utc).isoformat(),
            'updated_at': datetime.now(timezone.utc).isoformat()
        }
        temp_db.save_position(position_data)
        
        # Mock exchange sync
        recovery_system.client.get_positions.return_value = {'items': []}
        
        # Recover positions
        loaded_count, synced_count = recovery_system._recover_positions(position_manager)
        
        # Verify recovery
        assert loaded_count == 1
        assert 'BTCUSDT' in position_manager.positions
        
        position = position_manager.get_position('BTCUSDT')
        assert position.size == 0.001
        assert position.avg_entry_price == 40000.0
    
    def test_recover_orders_from_database(self, recovery_system, order_manager, temp_db):
        """Test order recovery from database"""
        # Save test order to database
        order_data = {
            'client_id': 'DRA_123_buy_BTCUSDT',
            'market': 'BTCUSDT',
            'side': 'buy',
            'order_type': 'limit',
            'amount': 0.001,
            'price': 40000.0,
            'status': 'pending',
            'exchange_order_id': 12345,
            'filled_amount': 0.0,
            'created_at': datetime.now(timezone.utc).isoformat(),
            'updated_at': datetime.now(timezone.utc).isoformat()
        }
        temp_db.save_order(order_data)
        
        # Mock exchange responses
        recovery_system.client.get_pending_orders.return_value = {'items': []}
        recovery_system.client.get_order_status.return_value = {
            'status': 'pending',
            'filled_amount': '0'
        }
        
        # Recover orders
        loaded_count, synced_count = recovery_system._recover_orders(order_manager)
        
        # Verify recovery
        assert loaded_count == 1
        assert 'DRA_123_buy_BTCUSDT' in order_manager.active_orders
        
        order = order_manager.active_orders['DRA_123_buy_BTCUSDT']
        assert order.market == 'BTCUSDT'
        assert order.side == OrderSide.BUY
        assert order.amount == 0.001
    
    def test_validate_consistency_finds_issues(self, recovery_system, order_manager, position_manager):
        """Test consistency validation finds issues"""
        # Create inconsistent state - sell order without position
        order = Order(
            client_id='DRA_123_sell_BTCUSDT',
            market='BTCUSDT',
            side=OrderSide.SELL,
            order_type='limit',
            amount=0.001,
            price=42000.0,
            status=OrderStatus.PENDING
        )
        order_manager.active_orders['DRA_123_sell_BTCUSDT'] = order
        
        # Mock market data
        recovery_system.market_data.get_current_price.return_value = 41000.0
        
        # Validate consistency
        recovery_system._validate_consistency(order_manager, position_manager)
        
        # Should find the inconsistency
        assert len(recovery_system.inconsistencies) > 0
        assert any('no position found' in issue for issue in recovery_system.inconsistencies)
    
    def test_cleanup_stale_data(self, recovery_system, order_manager, position_manager):
        """Test cleanup of stale data"""
        # Add completed order that shouldn't be in active_orders
        filled_order = Order(
            client_id='FILLED_ORDER',
            market='BTCUSDT',
            side=OrderSide.BUY,
            order_type='limit',
            amount=0.001,
            price=40000.0,
            status=OrderStatus.FILLED
        )
        order_manager.active_orders['FILLED_ORDER'] = filled_order
        
        # Add zero-size position
        zero_position = Position(
            market='ETHUSDT',
            side=PositionSide.LONG,
            size=0.0,
            avg_entry_price=2500.0,
            total_cost=0.0,
            unrealized_pnl=0.0,
            liquidation_price=0.0,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc)
        )
        position_manager.positions['ETHUSDT'] = zero_position
        
        # Mock database cleanup
        recovery_system.database.cleanup_old_data = Mock(return_value=True)
        recovery_system.database.delete_position = Mock(return_value=True)
        
        # Cleanup
        recovery_system._cleanup_stale_data(order_manager, position_manager)
        
        # Verify cleanup
        assert 'FILLED_ORDER' not in order_manager.active_orders
        assert 'ETHUSDT' not in position_manager.positions
    
    def test_full_recovery_success(self, recovery_system, order_manager, position_manager, strategy, temp_db):
        """Test successful full recovery"""
        # Prepare test data in database
        position_data = {
            'market': 'BTCUSDT', 'side': 'long', 'size': 0.001,
            'avg_entry_price': 40000.0, 'total_cost': 40.04,
            'unrealized_pnl': 0, 'liquidation_price': 0,
            'created_at': datetime.now(timezone.utc).isoformat(),
            'updated_at': datetime.now(timezone.utc).isoformat()
        }
        temp_db.save_position(position_data)
        
        # Mock all exchange calls
        recovery_system.client.get_positions.return_value = {'items': []}
        recovery_system.client.get_pending_orders.return_value = {'items': []}
        recovery_system.client.get_futures_markets.return_value = [{'market': 'BTCUSDT'}]
        recovery_system.market_data.get_current_price.return_value = 41000.0
        
        # Perform recovery
        report = recovery_system.perform_full_recovery(order_manager, position_manager, strategy)
        
        # Verify success
        assert report.recovery_successful is True
        assert report.positions_loaded == 1
        assert len(report.inconsistencies_found) == 0
    
    def test_full_recovery_with_inconsistencies(self, recovery_system, order_manager, position_manager, strategy):
        """Test recovery with inconsistencies"""
        # Create inconsistent state
        sell_order = Order(
            client_id='ORPHAN_SELL',
            market='BTCUSDT',
            side=OrderSide.SELL,
            order_type='limit',
            amount=0.001,
            price=42000.0,
            status=OrderStatus.PENDING
        )
        order_manager.active_orders['ORPHAN_SELL'] = sell_order
        
        # Mock exchange calls
        recovery_system.client.get_positions.return_value = {'items': []}
        recovery_system.client.get_pending_orders.return_value = {'items': []}
        recovery_system.market_data.get_current_price.return_value = 41000.0
        
        # Perform recovery
        report = recovery_system.perform_full_recovery(order_manager, position_manager, strategy)
        
        # Should detect inconsistencies but still complete
        assert report.recovery_successful is False  # Due to inconsistencies
        assert len(report.inconsistencies_found) > 0
    
    def test_is_safe_to_trade(self, recovery_system, order_manager, position_manager):
        """Test safety check for trading"""
        # Test with no issues
        recovery_system.client.get_futures_markets.return_value = [{'market': 'BTCUSDT'}]
        recovery_system.inconsistencies = []
        
        is_safe, issues = recovery_system.is_safe_to_trade(order_manager, position_manager)
        assert is_safe is True
        assert len(issues) == 0
        
        # Test with API connectivity issue
        recovery_system.client.get_futures_markets.side_effect = Exception("Connection failed")
        
        is_safe, issues = recovery_system.is_safe_to_trade(order_manager, position_manager)
        assert is_safe is False
        assert any('connectivity' in issue for issue in issues)
    
    def test_save_current_state(self, recovery_system, order_manager, position_manager, strategy, temp_db):
        """Test saving current state"""
        # Add test data
        position = Position(
            market='BTCUSDT', side=PositionSide.LONG, size=0.001,
            avg_entry_price=40000.0, total_cost=40.04,
            unrealized_pnl=0, liquidation_price=0,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc)
        )
        position_manager.positions['BTCUSDT'] = position
        
        order = Order(
            client_id='TEST_ORDER', market='BTCUSDT', side=OrderSide.BUY,
            order_type='limit', amount=0.001, price=40000.0,
            status=OrderStatus.PENDING
        )
        order_manager.active_orders['TEST_ORDER'] = order
        
        # Save state
        recovery_system.save_current_state(order_manager, position_manager, strategy)
        
        # Verify data was saved
        positions = temp_db.load_positions()
        orders = temp_db.load_active_orders()
        
        assert len(positions) == 1
        assert len(orders) == 1
        assert positions[0]['market'] == 'BTCUSDT'
        assert orders[0]['client_id'] == 'TEST_ORDER'