"""
Test cases for asset selector
"""
import pytest
from unittest.mock import Mock, patch
from io import StringIO

from src.utils.asset_selector import AssetSelector
from src.core.position_sizing import PositionSize, SizingMode


class TestAssetSelector:
    """Test cases for asset selection system"""
    
    @pytest.fixture
    def mock_market_data(self):
        """Create mock market data manager"""
        mock = Mock()
        mock.get_available_markets.return_value = [
            'BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'ADAUSDT'
        ]
        mock.get_market_info.return_value = {
            'market': 'BTCUSDT',
            'min_amount': '0.001'
        }
        mock.get_current_price.return_value = 40000.0
        mock.get_previous_day_ohlc.return_value = {
            'high': 41000.0,
            'low': 39000.0,
            'close': 40000.0,
            'volume': 1000000.0
        }
        mock.get_minimum_order_value.return_value = 50.0
        return mock
    
    @pytest.fixture
    def mock_position_sizer(self):
        """Create mock position sizer"""
        mock = Mock()
        mock.get_sizing_summary.return_value = {
            'mode': 'NORMAL',
            'available_capital': 5000.0,
            'test_mode': False,
            'leverage': 2.0,
            'max_positions': 10
        }
        mock.calculate_position_size.return_value = PositionSize(
            market='BTCUSDT',
            size_usdt=500.0,
            quantity=0.025,
            mode=SizingMode.NORMAL,
            reason='Normal mode sizing',
            is_valid=True
        )
        return mock
    
    @pytest.fixture
    def asset_selector(self, mock_market_data, mock_position_sizer):
        """Create asset selector with mocks"""
        return AssetSelector(mock_market_data, mock_position_sizer)
    
    def test_get_filtered_markets(self, asset_selector, mock_market_data):
        """Test filtering and analyzing markets"""
        markets = asset_selector._get_filtered_markets()
        
        # Should return analyzed markets
        assert len(markets) > 0
        
        # Check market structure
        market = markets[0]
        assert 'market' in market
        assert 'current_price' in market
        assert 'daily_range' in market
        assert 'range_percent' in market
        assert 'min_order_value' in market
    
    def test_analyze_market(self, asset_selector):
        """Test market analysis"""
        analysis = asset_selector._analyze_market('BTCUSDT')
        
        assert analysis is not None
        assert analysis['market'] == 'BTCUSDT'
        assert analysis['current_price'] == 40000.0
        assert analysis['daily_range'] == 2000.0  # 41000 - 39000
        assert analysis['range_percent'] == 5.0    # (2000 / 40000) * 100
        assert analysis['min_order_value'] == 50.0
    
    def test_analyze_market_error_handling(self, asset_selector, mock_market_data):
        """Test market analysis error handling"""
        # Make market data throw an error
        mock_market_data.get_current_price.side_effect = Exception("API Error")
        
        analysis = asset_selector._analyze_market('BTCUSDT')
        
        # Should return None on error
        assert analysis is None
    
    @patch('src.utils.asset_selector.Prompt.ask')
    def test_prompt_market_selection_valid_choice(self, mock_prompt, asset_selector):
        """Test valid market selection"""
        mock_prompt.return_value = "1"
        
        markets = [
            {'market': 'BTCUSDT', 'current_price': 40000.0},
            {'market': 'ETHUSDT', 'current_price': 2500.0}
        ]
        
        selected = asset_selector._prompt_market_selection(markets)
        assert selected == 'BTCUSDT'
    
    @patch('src.utils.asset_selector.Prompt.ask')
    def test_prompt_market_selection_quit(self, mock_prompt, asset_selector):
        """Test quitting market selection"""
        mock_prompt.return_value = "q"
        
        markets = [{'market': 'BTCUSDT', 'current_price': 40000.0}]
        
        selected = asset_selector._prompt_market_selection(markets)
        assert selected is None
    
    @patch('src.utils.asset_selector.Prompt.ask')
    def test_prompt_market_selection_invalid_choice(self, mock_prompt, asset_selector):
        """Test invalid choice handling"""
        # First invalid, then valid choice
        mock_prompt.side_effect = ["999", "1"]
        
        markets = [{'market': 'BTCUSDT', 'current_price': 40000.0}]
        
        selected = asset_selector._prompt_market_selection(markets)
        assert selected == 'BTCUSDT'
    
    @patch('src.utils.asset_selector.Confirm.ask')
    def test_confirm_selection_accept(self, mock_confirm, asset_selector):
        """Test confirming selection"""
        mock_confirm.return_value = True
        
        confirmed = asset_selector._confirm_selection('BTCUSDT', 5000.0)
        assert confirmed is True
    
    @patch('src.utils.asset_selector.Confirm.ask')
    def test_confirm_selection_reject(self, mock_confirm, asset_selector):
        """Test rejecting selection"""
        mock_confirm.return_value = False
        
        confirmed = asset_selector._confirm_selection('BTCUSDT', 5000.0)
        assert confirmed is False
    
    def test_show_market_analysis(self, asset_selector):
        """Test showing market analysis"""
        analysis = asset_selector.show_market_analysis('BTCUSDT')
        
        assert analysis['market'] == 'BTCUSDT'
        assert analysis['current_price'] == 40000.0
        assert analysis['daily_range'] == 2000.0
    
    def test_show_market_analysis_error(self, asset_selector, mock_market_data):
        """Test market analysis error handling"""
        mock_market_data.get_current_price.side_effect = Exception("API Error")
        
        analysis = asset_selector.show_market_analysis('BTCUSDT')
        assert analysis == {}
    
    @patch('src.utils.asset_selector.Prompt.ask')
    @patch('src.utils.asset_selector.Confirm.ask')
    def test_select_trading_asset_complete_flow(self, mock_confirm, mock_prompt, asset_selector):
        """Test complete asset selection flow"""
        # User selects first market and confirms
        mock_prompt.return_value = "1"
        mock_confirm.return_value = True
        
        selected = asset_selector.select_trading_asset(5000.0)
        
        # Should return selected market
        assert selected == 'BTCUSDT'
    
    @patch('src.utils.asset_selector.Prompt.ask')
    def test_select_trading_asset_quit(self, mock_prompt, asset_selector):
        """Test quitting asset selection"""
        mock_prompt.return_value = "q"
        
        selected = asset_selector.select_trading_asset(5000.0)
        assert selected is None
    
    @patch('src.utils.asset_selector.Prompt.ask')
    @patch('src.utils.asset_selector.Confirm.ask')
    def test_select_trading_asset_reject_confirmation(self, mock_confirm, mock_prompt, asset_selector):
        """Test rejecting final confirmation"""
        mock_prompt.return_value = "1"
        mock_confirm.return_value = False
        
        selected = asset_selector.select_trading_asset(5000.0)
        assert selected is None
    
    def test_select_trading_asset_no_markets(self, asset_selector, mock_market_data):
        """Test handling when no markets are available"""
        mock_market_data.get_available_markets.return_value = []
        
        selected = asset_selector.select_trading_asset(5000.0)
        assert selected is None
    
    def test_select_trading_asset_keyboard_interrupt(self, asset_selector):
        """Test handling keyboard interrupt"""
        with patch.object(asset_selector, '_get_filtered_markets', side_effect=KeyboardInterrupt):
            selected = asset_selector.select_trading_asset(5000.0)
            assert selected is None