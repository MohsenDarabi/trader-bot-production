#!/usr/bin/env python3
"""
Minimal test to debug order placement hanging issue
Tests the order placement API call in isolation
"""
import sys
import os
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / 'src'))

import time
from src.exchange.coinex_client import CoinExClient
from src.utils.logger import get_logger

logger = get_logger(__name__)

def test_minimal_order_placement():
    """Test minimal order placement to debug hanging issue"""
    print("🔍 Starting minimal order placement test...")
    
    try:
        # Initialize client
        print("1. Initializing CoinEx client...")
        client = CoinExClient()
        print("✅ Client initialized")
        
        # Test basic API connectivity first
        print("2. Testing API connectivity...")
        account_info = client.get_account_info()
        print(f"✅ API connectivity OK - got {len(account_info) if isinstance(account_info, list) else 'dict'} assets")
        
        # Get current price for ETHUSDT
        print("3. Getting current price...")
        ticker = client.get_ticker("ETHUSDT")
        current_price = float(ticker[0]['last'] if isinstance(ticker, list) else ticker['last'])
        print(f"✅ Current ETHUSDT price: ${current_price:.2f}")
        
        # Prepare order parameters
        safe_price = current_price * 0.5  # 50% below market
        test_quantity = 0.005  # Minimum order
        client_id = f"DEBUG_TEST_{int(time.time())}"
        
        print(f"4. Preparing test order:")
        print(f"   Market: ETHUSDT")
        print(f"   Price: ${safe_price:.2f} (50% below market)")
        print(f"   Quantity: {test_quantity}")
        print(f"   Client ID: {client_id}")
        print(f"   Hidden: True")
        
        # Try to find the correct endpoint by testing with POST and authentication
        print("5. Testing possible order endpoints with POST method and auth...")
        
        # Get authentication headers
        from src.exchange.auth import CoinExAuth
        auth = CoinExAuth()
        
        # Prepare test order data
        order_data = {
            "market": "ETHUSDT",
            "market_type": "FUTURES",
            "side": "buy",
            "type": "limit",
            "amount": str(test_quantity),
            "price": str(safe_price),
            "client_id": client_id,
            "is_hide": True
        }
        
        import json
        body_str = json.dumps(order_data)
        
        possible_endpoints = [
            "/v2/futures/order",           # Current attempt
            "/v2/futures/put-order",       # Hyphenated version
            "/v2/order/futures",           # Reversed structure
            "/v2/perpetual/order",         # Different terminology
            "/v2/contract/order",          # Alternative name
            "/futures/order",              # Without v2
            "/api/v2/futures/order",       # With /api prefix
        ]
        
        # Test each endpoint with POST
        for endpoint in possible_endpoints:
            try:
                print(f"\n   Testing endpoint: POST {endpoint}")
                
                # Get auth headers for this endpoint
                headers = auth.get_auth_headers(
                    method='POST',
                    path=endpoint,
                    params=None,
                    body=body_str
                )
                
                # Make POST request
                import requests
                url = f"https://api.coinex.com{endpoint}"
                
                response = requests.post(
                    url, 
                    data=body_str,
                    headers=headers,
                    timeout=10
                )
                
                print(f"   Status: {response.status_code}")
                
                if response.status_code != 404:
                    print(f"   ✅ Endpoint exists! Response:")
                    print(f"   Headers: {dict(response.headers)}")
                    print(f"   Body: {response.text[:200]}...")
                    
                    # If we get a JSON response, it might be working
                    try:
                        json_resp = response.json()
                        print(f"   JSON Response: {json_resp}")
                    except:
                        pass
                else:
                    print(f"   ❌ Returns 404")
                    
            except Exception as e:
                print(f"   ❌ Error: {e}")
        
        print("\n6. Testing if we need a different base URL for futures...")
        alternative_bases = [
            "https://futures.coinex.com",
            "https://api.futures.coinex.com",
            "https://perpetual.coinex.com",
        ]
        
        for base in alternative_bases:
            try:
                print(f"   Testing {base}/v2/futures/order")
                response = requests.get(f"{base}/v2/futures/order", timeout=5)
                print(f"   Status: {response.status_code}")
            except Exception as e:
                print(f"   Error: {e}")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_minimal_order_placement()