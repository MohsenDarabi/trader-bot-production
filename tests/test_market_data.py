"""
Test cases for market data manager
"""
import pytest
from unittest.mock import Mock, patch
from datetime import datetime, timezone
import pandas as pd

from src.data.market_data import MarketDataManager


class TestMarketDataManager:
    """Test cases for market data management"""
    
    @pytest.fixture
    def mock_client(self):
        """Create mock CoinEx client"""
        return Mock()
    
    @pytest.fixture
    def manager(self, mock_client):
        """Create market data manager with mock client"""
        return MarketDataManager(client=mock_client)
    
    def test_get_market_info(self, manager, mock_client):
        """Test fetching market information"""
        # Mock response
        mock_client.get_futures_markets.return_value = [{
            'market': 'BTCUSDT',
            'min_amount': '0.0005',
            'tick_size': '0.5',
            'base_precision': 8,
            'quote_precision': 2
        }]
        
        # Get market info
        info = manager.get_market_info('BTCUSDT')
        
        # Verify
        assert info['market'] == 'BTCUSDT'
        assert info['min_amount'] == '0.0005'
        assert info['tick_size'] == '0.5'
        
        # Test caching
        info2 = manager.get_market_info('BTCUSDT')
        assert mock_client.get_futures_markets.call_count == 1  # Should use cache
    
    def test_get_minimum_order_value(self, manager, mock_client):
        """Test minimum order value calculation"""
        # Mock market info
        mock_client.get_futures_markets.return_value = [{
            'market': 'BTCUSDT',
            'min_amount': '0.001'
        }]
        
        # Calculate minimum order value
        min_value = manager.get_minimum_order_value('BTCUSDT', 50000.0)
        
        # Verify: 0.001 BTC * $50,000 = $50
        assert min_value == 50.0
    
    def test_get_daily_candles(self, manager, mock_client):
        """Test fetching daily OHLC candles"""
        # Mock kline response
        mock_client.get_kline.return_value = [
            {
                'created_at': 1705708800000,  # 2024-01-20
                'open': '42000',
                'high': '43000',
                'low': '41000',
                'close': '42500',
                'volume': '1000'
            },
            {
                'created_at': 1705622400000,  # 2024-01-19
                'open': '41500',
                'high': '42500',
                'low': '41000',
                'close': '42000',
                'volume': '1200'
            }
        ]
        
        # Get candles
        df = manager.get_daily_candles('BTCUSDT', days=2)
        
        # Verify
        assert len(df) == 2
        assert df.iloc[0]['close'] == 42500.0  # Most recent first
        assert df.iloc[1]['close'] == 42000.0
    
    def test_get_previous_day_ohlc(self, manager, mock_client):
        """Test getting previous day OHLC"""
        # Mock kline response
        mock_client.get_kline.return_value = [
            {
                'created_at': 1705708800000,  # Today
                'open': '42000',
                'high': '43000',
                'low': '41000',
                'close': '42500',
                'volume': '1000'
            },
            {
                'created_at': 1705622400000,  # Yesterday
                'open': '41500',
                'high': '42500',
                'low': '41000',
                'close': '42000',
                'volume': '1200'
            }
        ]
        
        # Get previous day OHLC
        ohlc = manager.get_previous_day_ohlc('BTCUSDT')
        
        # Verify it returns yesterday's data
        assert ohlc['open'] == 41500.0
        assert ohlc['high'] == 42500.0
        assert ohlc['low'] == 41000.0
        assert ohlc['close'] == 42000.0
    
    def test_calculate_daily_range(self, manager):
        """Test daily range calculation"""
        # Test normal calculation
        range_value = manager.calculate_daily_range(high=44000, low=41000, divisor=4)
        assert range_value == 750.0  # (44000 - 41000) / 4
        
        # Test invalid inputs
        with pytest.raises(ValueError):
            manager.calculate_daily_range(high=40000, low=41000)  # High < Low
    
    def test_get_current_price(self, manager, mock_client):
        """Test fetching current price"""
        # Mock ticker response
        mock_client.get_ticker.return_value = {
            'last': '42350.5',
            'volume': '1500'
        }
        
        # Get price
        price = manager.get_current_price('BTCUSDT')
        
        # Verify
        assert price == 42350.5
    
    def test_get_available_markets(self, manager, mock_client):
        """Test fetching available markets"""
        # Mock markets response
        mock_client.get_futures_markets.return_value = [
            {'market': 'BTCUSDT', 'available': True},
            {'market': 'ETHUSDT', 'available': True},
            {'market': 'BNBUSDT', 'available': False},  # Not available
            {'market': 'BTCUSD', 'available': True}     # Not USDT
        ]
        
        # Get available markets
        markets = manager.get_available_markets()
        
        # Verify only active USDT markets are returned
        assert markets == ['BTCUSDT', 'ETHUSDT']