"""
Test cases for order management system
"""
import pytest
from unittest.mock import Mock, patch
from datetime import datetime, timezone

from src.exchange.order_manager import OrderManager, Order, OrderStatus, OrderSide
from src.core.profitability import ProfitabilityResult


class TestOrderManager:
    """Test cases for order management"""
    
    @pytest.fixture
    def mock_client(self):
        """Create mock CoinEx client"""
        return Mock()
    
    @pytest.fixture
    def mock_validator(self):
        """Create mock profitability validator"""
        return Mock()
    
    @pytest.fixture
    def order_manager(self, mock_client, mock_validator):
        """Create order manager with mocks"""
        return OrderManager(mock_client, mock_validator)
    
    def test_generate_client_id(self, order_manager):
        """Test client ID generation"""
        # Test basic generation
        client_id = order_manager.generate_client_id('BTCUSDT', OrderSide.BUY, 1234567890)
        expected = 'DRA_1234567890_buy_BTCUSDT'
        assert client_id == expected
        
        # Test uniqueness - should add suffix if duplicate
        order_manager.used_client_ids.add(expected)
        client_id2 = order_manager.generate_client_id('BTCUSDT', OrderSide.BUY, 1234567890)
        assert client_id2 == expected + '_1'
    
    def test_place_buy_order_success(self, order_manager, mock_client):
        """Test successful buy order placement"""
        # Mock exchange response
        mock_client.place_order.return_value = {'order_id': 12345}
        
        # Place order
        order = order_manager.place_buy_order(
            market='BTCUSDT',
            amount=0.001,
            price=40000.0,
            position_size=1000.0
        )
        
        # Verify order
        assert order is not None
        assert order.market == 'BTCUSDT'
        assert order.side == OrderSide.BUY
        assert order.amount == 0.001
        assert order.price == 40000.0
        assert order.status == OrderStatus.PENDING
        assert order.exchange_order_id == 12345
        
        # Verify stored in active orders
        assert order.client_id in order_manager.active_orders
    
    def test_place_buy_order_failure(self, order_manager, mock_client):
        """Test buy order placement failure"""
        # Mock exchange error
        mock_client.place_order.side_effect = Exception("API Error")
        
        # Place order
        order = order_manager.place_buy_order(
            market='BTCUSDT',
            amount=0.001,
            price=40000.0,
            position_size=1000.0
        )
        
        # Should return None on failure
        assert order is None
        assert len(order_manager.active_orders) == 0
    
    def test_place_sell_order_profitable(self, order_manager, mock_client, mock_validator):
        """Test sell order placement when profitable"""
        # Mock profitable validation
        mock_validator.is_position_profitable.return_value = ProfitabilityResult(
            is_profitable=True,
            net_profit=25.0,
            profit_percent=2.5,
            total_fees=5.0
        )
        
        # Mock exchange response
        mock_client.place_order.return_value = {'order_id': 12346}
        
        # Place order
        order = order_manager.place_sell_order(
            market='BTCUSDT',
            amount=0.001,
            price=42000.0,
            position_entry_price=40000.0,
            position_entry_cost=1002.0
        )
        
        # Verify order placed
        assert order is not None
        assert order.side == OrderSide.SELL
        assert order.exchange_order_id == 12346
    
    def test_place_sell_order_unprofitable(self, order_manager, mock_validator):
        """Test sell order rejection when unprofitable"""
        # Mock unprofitable validation
        mock_validator.is_position_profitable.return_value = ProfitabilityResult(
            is_profitable=False,
            net_profit=-10.0,
            profit_percent=0.5,
            total_fees=5.0,
            reason="Below minimum profit"
        )
        
        # Place order
        order = order_manager.place_sell_order(
            market='BTCUSDT',
            amount=0.001,
            price=40100.0,
            position_entry_price=40000.0,
            position_entry_cost=1002.0
        )
        
        # Should reject unprofitable order
        assert order is None
    
    def test_cancel_order(self, order_manager, mock_client):
        """Test order cancellation"""
        # Create a test order
        order = Order(
            client_id='test_123',
            market='BTCUSDT',
            side=OrderSide.BUY,
            order_type='limit',
            amount=0.001,
            price=40000.0,
            status=OrderStatus.PENDING
        )
        order_manager.active_orders['test_123'] = order
        
        # Mock successful cancellation
        mock_client.cancel_order.return_value = {'result': 'success'}
        
        # Cancel order
        result = order_manager.cancel_order('test_123')
        
        # Verify cancellation
        assert result is True
        assert 'test_123' not in order_manager.active_orders
        assert order.status == OrderStatus.CANCELLED
    
    def test_update_order_status(self, order_manager, mock_client):
        """Test order status update"""
        # Create a test order
        order = Order(
            client_id='test_123',
            market='BTCUSDT',
            side=OrderSide.BUY,
            order_type='limit',
            amount=0.001,
            price=40000.0,
            status=OrderStatus.PENDING
        )
        order_manager.active_orders['test_123'] = order
        
        # Mock status response - order filled
        mock_client.get_order_status.return_value = {
            'status': 'done',
            'filled_amount': '0.001'
        }
        
        # Update status
        updated_order = order_manager.update_order_status('test_123')
        
        # Verify update
        assert updated_order.status == OrderStatus.FILLED
        assert updated_order.filled_amount == 0.001
        # Should be removed from active orders when filled
        assert 'test_123' not in order_manager.active_orders
    
    def test_has_pending_orders(self, order_manager):
        """Test pending order checks"""
        # Add test orders
        buy_order = Order(
            client_id='buy_123',
            market='BTCUSDT',
            side=OrderSide.BUY,
            order_type='limit',
            amount=0.001,
            price=40000.0,
            status=OrderStatus.PENDING
        )
        
        sell_order = Order(
            client_id='sell_123',
            market='BTCUSDT',
            side=OrderSide.SELL,
            order_type='limit',
            amount=0.001,
            price=42000.0,
            status=OrderStatus.PENDING
        )
        
        order_manager.active_orders['buy_123'] = buy_order
        order_manager.active_orders['sell_123'] = sell_order
        
        # Test checks
        assert order_manager.has_pending_buy_order('BTCUSDT') is True
        assert order_manager.has_pending_sell_order('BTCUSDT') is True
        assert order_manager.has_pending_buy_order('ETHUSDT') is False
    
    def test_load_existing_orders(self, order_manager, mock_client):
        """Test loading existing orders from exchange"""
        # Mock exchange response
        mock_client.get_pending_orders.return_value = {
            'items': [
                {
                    'client_id': 'DRA_1234567890_buy_BTCUSDT',
                    'market': 'BTCUSDT',
                    'side': 'buy',
                    'type': 'limit',
                    'amount': '0.001',
                    'price': '40000',
                    'order_id': 12345,
                    'filled_amount': '0'
                },
                {
                    'client_id': 'OTHER_ORDER_123',  # Not our format
                    'market': 'BTCUSDT',
                    'side': 'sell',
                    'type': 'limit',
                    'amount': '0.001',
                    'price': '42000',
                    'order_id': 12346
                }
            ]
        }
        
        # Load orders
        count = order_manager.load_existing_orders()
        
        # Should only load our orders (with DRA_ prefix)
        assert count == 1
        assert len(order_manager.active_orders) == 1
        assert 'DRA_1234567890_buy_BTCUSDT' in order_manager.active_orders
        assert 'OTHER_ORDER_123' not in order_manager.active_orders