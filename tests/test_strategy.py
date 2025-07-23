"""
Test cases for Daily Range Accumulation Strategy
"""
import pytest
from unittest.mock import Mock, patch
from datetime import datetime, timezone

from src.core.strategy import DailyRangeStrategy, TradingSignal


class TestDailyRangeStrategy:
    """Test cases for strategy implementation"""
    
    @pytest.fixture
    def mock_market_data(self):
        """Create mock market data manager"""
        return Mock()
    
    @pytest.fixture
    def strategy(self, mock_market_data):
        """Create strategy instance with mock data"""
        return DailyRangeStrategy(mock_market_data)
    
    def test_calculate_signal_prices(self, strategy, mock_market_data):
        """Test signal price calculation"""
        # Mock range calculation
        mock_market_data.calculate_daily_range.return_value = 500.0
        
        # Calculate prices
        buy_price, sell_price, range_value = strategy.calculate_signal_prices(
            high=42000.0, 
            low=40000.0
        )
        
        # Verify
        assert buy_price == 40500.0  # Low + Range (40000 + 500)
        assert sell_price == 41500.0  # High - Range (42000 - 500)
        assert range_value == 500.0
    
    def test_generate_daily_signal(self, strategy, mock_market_data):
        """Test daily signal generation"""
        # Mock market data responses
        mock_market_data.is_new_trading_day.return_value = True
        mock_market_data.get_previous_day_ohlc.return_value = {
            'date': '2024-01-19',
            'open': 41000.0,
            'high': 42000.0,
            'low': 40000.0,
            'close': 41500.0,
            'volume': 1000.0
        }
        mock_market_data.calculate_daily_range.return_value = 500.0
        
        # Generate signal
        signal = strategy.generate_daily_signal('BTCUSDT')
        
        # Verify signal
        assert signal is not None
        assert signal.market == 'BTCUSDT'
        assert signal.buy_price == 40500.0
        assert signal.sell_price == 41500.0
        assert signal.range_value == 500.0
        assert signal.previous_high == 42000.0
        assert signal.previous_low == 40000.0
    
    def test_signal_caching(self, strategy, mock_market_data):
        """Test that signals are cached for the day"""
        # Setup mocks
        mock_market_data.is_new_trading_day.return_value = True
        mock_market_data.get_previous_day_ohlc.return_value = {
            'high': 42000.0,
            'low': 40000.0
        }
        mock_market_data.calculate_daily_range.return_value = 500.0
        
        # Generate signal twice
        signal1 = strategy.generate_daily_signal('BTCUSDT')
        signal2 = strategy.generate_daily_signal('BTCUSDT')
        
        # Should return same signal (cached)
        assert signal1 == signal2
        # Market data should only be fetched once
        assert mock_market_data.get_previous_day_ohlc.call_count == 1
    
    def test_should_place_buy_order(self, strategy):
        """Test buy order logic"""
        # Create a signal
        signal = TradingSignal(
            market='BTCUSDT',
            date=datetime.now(timezone.utc).date().isoformat(),
            buy_price=40000.0,
            sell_price=42000.0,
            range_value=500.0,
            previous_high=42500.0,
            previous_low=39500.0,
            created_at=datetime.now(timezone.utc)
        )
        strategy._current_signals['BTCUSDT'] = signal
        
        # Test various prices
        assert strategy.should_place_buy_order('BTCUSDT', 39900.0) is True  # Below buy
        assert strategy.should_place_buy_order('BTCUSDT', 40000.0) is True  # At buy
        assert strategy.should_place_buy_order('BTCUSDT', 40040.0) is True  # Slightly above (buffer)
        assert strategy.should_place_buy_order('BTCUSDT', 40500.0) is False  # Too high
    
    def test_should_place_sell_order(self, strategy):
        """Test sell order logic"""
        # Create a signal
        signal = TradingSignal(
            market='BTCUSDT',
            date=datetime.now(timezone.utc).date().isoformat(),
            buy_price=40000.0,
            sell_price=42000.0,
            range_value=500.0,
            previous_high=42500.0,
            previous_low=39500.0,
            created_at=datetime.now(timezone.utc)
        )
        strategy._current_signals['BTCUSDT'] = signal
        
        # Test various prices
        position_entry = 40000.0
        assert strategy.should_place_sell_order('BTCUSDT', 42100.0, position_entry) is True  # Above sell
        assert strategy.should_place_sell_order('BTCUSDT', 42000.0, position_entry) is True  # At sell
        assert strategy.should_place_sell_order('BTCUSDT', 41960.0, position_entry) is True  # Slightly below (buffer)
        assert strategy.should_place_sell_order('BTCUSDT', 41000.0, position_entry) is False  # Too low
    
    def test_calculate_expected_profit(self, strategy):
        """Test profit calculation"""
        # Calculate profit for a trade
        result = strategy.calculate_expected_profit(
            buy_price=40000.0,
            sell_price=41000.0,
            position_size=1000.0  # $1000 position
        )
        
        # Verify calculations
        assert result['buy_quantity'] == 0.025  # 1000 / 40000
        assert result['gross_profit'] == 25.0  # 0.025 * 41000 - 1000
        
        # Fees: Buy fee = 1000 * 0.001 = 1.0
        #       Sell fee = 1025 * 0.001 = 1.025
        assert result['total_fees'] == 2.025
        assert result['net_profit'] == 22.975  # 25 - 2.025
        assert result['profit_percent'] == 2.2975  # (22.975 / 1000) * 100