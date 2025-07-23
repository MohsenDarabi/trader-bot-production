"""
Test cases for CoinEx authentication module
"""
import pytest
from src.exchange.auth import CoinExAuth


class TestCoinExAuth:
    """Test cases for CoinEx authentication"""
    
    def test_signature_generation(self):
        """Test signature generation with known values"""
        # Test with example values
        auth = CoinExAuth(
            access_id="test_access_id",
            secret_key="test_secret_key"
        )
        
        # Test GET request signature
        signature = auth.generate_signature(
            method="GET",
            path="/v2/futures/market",
            params={"market": "BTCUSDT"},
            timestamp="1700000000000"
        )
        assert isinstance(signature, str)
        assert len(signature) == 64  # SHA256 produces 64 character hex string
        
    def test_auth_headers(self):
        """Test authentication header generation"""
        auth = CoinExAuth(
            access_id="test_access_id",
            secret_key="test_secret_key"
        )
        
        headers = auth.get_auth_headers(
            method="POST",
            path="/v2/futures/order",
            body='{"market":"BTCUSDT","side":"buy","amount":"0.001"}'
        )
        
        assert 'X-COINEX-KEY' in headers
        assert 'X-COINEX-SIGN' in headers
        assert 'X-COINEX-TIMESTAMP' in headers
        assert headers['X-COINEX-KEY'] == "test_access_id"
        assert headers['Content-Type'] == "application/json"
        
    def test_websocket_auth(self):
        """Test WebSocket authentication data generation"""
        auth = CoinExAuth(
            access_id="test_access_id",
            secret_key="test_secret_key"
        )
        
        ws_auth = auth.sign_websocket_message(timestamp="1700000000000")
        
        assert 'access_id' in ws_auth
        assert 'signed_str' in ws_auth
        assert 'timestamp' in ws_auth
        assert ws_auth['access_id'] == "test_access_id"
        assert ws_auth['timestamp'] == "1700000000000"
        
    def test_missing_credentials(self):
        """Test that missing credentials raise an error"""
        with pytest.raises(ValueError, match="CoinEx API credentials not provided"):
            CoinExAuth(access_id=None, secret_key=None)