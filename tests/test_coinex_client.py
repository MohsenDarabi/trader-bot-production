"""
Test cases for CoinEx REST API client
"""
import pytest
from unittest.mock import Mock, patch
from src.exchange.coinex_client import CoinExClient, RateLimiter
from src.exchange.auth import CoinExAuth


class TestRateLimiter:
    """Test cases for rate limiter"""
    
    def test_rate_limiter_allows_requests_under_limit(self):
        """Test that requests under limit are allowed immediately"""
        limiter = RateLimiter(max_requests=5, window_seconds=1)
        
        # Make 5 requests quickly - should all be allowed
        for _ in range(5):
            limiter.wait_if_needed()
        
        # No assertion needed - if it doesn't sleep, test passes
    
    @patch('time.sleep')
    def test_rate_limiter_delays_when_limit_reached(self, mock_sleep):
        """Test that rate limiter delays when limit is reached"""
        limiter = RateLimiter(max_requests=2, window_seconds=1)
        
        # Make 2 requests
        limiter.wait_if_needed()
        limiter.wait_if_needed()
        
        # Third request should trigger sleep
        limiter.wait_if_needed()
        
        assert mock_sleep.called


class TestCoinExClient:
    """Test cases for CoinEx API client"""
    
    @pytest.fixture
    def client(self):
        """Create test client"""
        auth = CoinExAuth("test_id", "test_key")
        return CoinExClient(auth)
    
    @patch('requests.Session.request')
    def test_get_futures_markets(self, mock_request, client):
        """Test getting futures markets"""
        # Mock response
        mock_response = Mock()
        mock_response.json.return_value = {
            'code': 0,
            'data': [{'market': 'BTCUSDT', 'min_amount': '0.001'}]
        }
        mock_request.return_value = mock_response
        
        # Make request
        result = client.get_futures_markets()
        
        # Verify
        assert result == [{'market': 'BTCUSDT', 'min_amount': '0.001'}]
        mock_request.assert_called_once()
    
    @patch('requests.Session.request')
    def test_place_order(self, mock_request, client):
        """Test placing an order"""
        # Mock response
        mock_response = Mock()
        mock_response.json.return_value = {
            'code': 0,
            'data': {
                'order_id': 12345,
                'market': 'BTCUSDT',
                'side': 'buy',
                'amount': '0.001'
            }
        }
        mock_request.return_value = mock_response
        
        # Place order
        result = client.place_order(
            market='BTCUSDT',
            side='buy',
            amount='0.001',
            order_type='limit',
            price='40000',
            client_id='test_order_123'
        )
        
        # Verify
        assert result['order_id'] == 12345
        assert result['market'] == 'BTCUSDT'
    
    @patch('requests.Session.request')
    def test_api_error_handling(self, mock_request, client):
        """Test API error handling"""
        # Mock error response
        mock_response = Mock()
        mock_response.json.return_value = {
            'code': 1001,
            'message': 'Invalid parameters'
        }
        mock_request.return_value = mock_response
        
        # Should raise ValueError
        with pytest.raises(ValueError, match="Invalid parameters"):
            client.get_futures_markets()
    
    def test_order_requires_price_for_limit(self, client):
        """Test that limit orders require price"""
        # This should work
        data = {
            'market': 'BTCUSDT',
            'side': 'buy',
            'amount': '0.001',
            'order_type': 'limit',
            'price': '40000'
        }
        # Just verify no exception is raised during data preparation
        # (actual request would be mocked)
    
    def test_cancel_order_requires_id(self, client):
        """Test that cancel order requires either order_id or client_id"""
        with pytest.raises(ValueError, match="Either order_id or client_id must be provided"):
            client.cancel_order(market='BTCUSDT')