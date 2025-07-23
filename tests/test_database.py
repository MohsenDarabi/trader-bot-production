"""
Test cases for database management
"""
import pytest
import tempfile
import os
from datetime import datetime, timezone

from src.data.database import DatabaseManager


class TestDatabaseManager:
    """Test cases for database operations"""
    
    @pytest.fixture
    def temp_db(self):
        """Create temporary database for testing"""
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
        temp_file.close()
        
        db = DatabaseManager(temp_file.name)
        yield db
        
        # Cleanup
        os.unlink(temp_file.name)
    
    def test_database_creation(self, temp_db):
        """Test database and table creation"""
        # Database should be created and tables should exist
        conn = temp_db._get_connection()
        
        # Check if tables exist
        cursor = conn.execute("""
            SELECT name FROM sqlite_master 
            WHERE type='table' AND name IN 
            ('positions', 'orders', 'daily_signals', 'trade_history', 'bot_state')
        """)
        tables = [row[0] for row in cursor.fetchall()]
        
        expected_tables = ['positions', 'orders', 'daily_signals', 'trade_history', 'bot_state']
        for table in expected_tables:
            assert table in tables
        
        conn.close()
    
    def test_save_and_load_position(self, temp_db):
        """Test position persistence"""
        # Test data
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
        
        # Save position
        assert temp_db.save_position(position_data) is True
        
        # Load positions
        positions = temp_db.load_positions()
        assert len(positions) == 1
        assert positions[0]['market'] == 'BTCUSDT'
        assert positions[0]['size'] == 0.001
        assert positions[0]['avg_entry_price'] == 40000.0
    
    def test_save_and_load_order(self, temp_db):
        """Test order persistence"""
        # Test data
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
        
        # Save order
        assert temp_db.save_order(order_data) is True
        
        # Load active orders
        orders = temp_db.load_active_orders()
        assert len(orders) == 1
        assert orders[0]['client_id'] == 'DRA_123_buy_BTCUSDT'
        assert orders[0]['status'] == 'pending'
    
    def test_save_and_load_daily_signal(self, temp_db):
        """Test daily signal persistence"""
        # Test data
        signal_data = {
            'market': 'BTCUSDT',
            'date': '2024-01-20',
            'buy_price': 40500.0,
            'sell_price': 41500.0,
            'range_value': 500.0,
            'previous_high': 42000.0,
            'previous_low': 40000.0,
            'created_at': datetime.now(timezone.utc).isoformat()
        }
        
        # Save signal
        assert temp_db.save_daily_signal(signal_data) is True
        
        # Load signal
        loaded_signal = temp_db.load_daily_signal('BTCUSDT', '2024-01-20')
        assert loaded_signal is not None
        assert loaded_signal['buy_price'] == 40500.0
        assert loaded_signal['sell_price'] == 41500.0
        
        # Test loading non-existent signal
        missing_signal = temp_db.load_daily_signal('ETHUSDT', '2024-01-20')
        assert missing_signal is None
    
    def test_save_trade_history(self, temp_db):
        """Test trade history persistence"""
        # Test data
        trade_data = {
            'market': 'BTCUSDT',
            'side': 'buy',
            'quantity': 0.001,
            'price': 40000.0,
            'fees': 0.04,
            'pnl': None,  # Buy order has no P&L
            'order_client_id': 'DRA_123_buy_BTCUSDT',
            'exchange_trade_id': 'T12345',
            'executed_at': datetime.now(timezone.utc).isoformat()
        }
        
        # Save trade
        assert temp_db.save_trade(trade_data) is True
        
        # Verify saved (we don't have a direct load method, so check via SQL)
        conn = temp_db._get_connection()
        cursor = conn.execute("SELECT * FROM trade_history")
        trades = cursor.fetchall()
        conn.close()
        
        assert len(trades) == 1
        assert trades[0]['market'] == 'BTCUSDT'
        assert trades[0]['side'] == 'buy'
    
    def test_bot_state_persistence(self, temp_db):
        """Test bot state persistence"""
        # Save various types of state
        assert temp_db.save_bot_state('test_mode', True) is True
        assert temp_db.save_bot_state('start_date', '2024-01-20') is True
        assert temp_db.save_bot_state('config', {'leverage': 2.0, 'fee': 0.001}) is True
        
        # Load state
        assert temp_db.load_bot_state('test_mode') is True
        assert temp_db.load_bot_state('start_date') == '2024-01-20'
        
        config = temp_db.load_bot_state('config')
        assert config['leverage'] == 2.0
        assert config['fee'] == 0.001
        
        # Test default value
        assert temp_db.load_bot_state('missing_key', 'default') == 'default'
    
    def test_delete_operations(self, temp_db):
        """Test deletion operations"""
        # Create test data
        position_data = {
            'market': 'BTCUSDT',
            'side': 'long', 'size': 0.001, 'avg_entry_price': 40000.0,
            'total_cost': 40.04, 'unrealized_pnl': 0, 'liquidation_price': 0,
            'created_at': datetime.now(timezone.utc).isoformat(),
            'updated_at': datetime.now(timezone.utc).isoformat()
        }
        
        order_data = {
            'client_id': 'TEST_ORDER', 'market': 'BTCUSDT', 'side': 'buy',
            'order_type': 'limit', 'amount': 0.001, 'price': 40000.0,
            'status': 'pending', 'exchange_order_id': None, 'filled_amount': 0,
            'created_at': datetime.now(timezone.utc).isoformat(),
            'updated_at': datetime.now(timezone.utc).isoformat()
        }
        
        # Save data
        temp_db.save_position(position_data)
        temp_db.save_order(order_data)
        
        # Verify data exists
        assert len(temp_db.load_positions()) == 1
        assert len(temp_db.load_active_orders()) == 1
        
        # Delete data
        assert temp_db.delete_position('BTCUSDT') is True
        assert temp_db.delete_order('TEST_ORDER') is True
        
        # Verify data removed
        assert len(temp_db.load_positions()) == 0
        assert len(temp_db.load_active_orders()) == 0
    
    def test_thread_safety(self, temp_db):
        """Test basic thread safety with concurrent operations"""
        import threading
        import time
        
        def save_positions():
            for i in range(10):
                position_data = {
                    'market': f'TEST{i}USDT',
                    'side': 'long', 'size': 0.001, 'avg_entry_price': 40000.0,
                    'total_cost': 40.04, 'unrealized_pnl': 0, 'liquidation_price': 0,
                    'created_at': datetime.now(timezone.utc).isoformat(),
                    'updated_at': datetime.now(timezone.utc).isoformat()
                }
                temp_db.save_position(position_data)
                time.sleep(0.01)
        
        # Start multiple threads
        threads = [threading.Thread(target=save_positions) for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        
        # Should have 30 positions (10 * 3 threads)
        positions = temp_db.load_positions()
        assert len(positions) == 30