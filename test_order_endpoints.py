#!/usr/bin/env python3
"""
Comprehensive test of all CoinEx futures order endpoints
Tests placement, status checking, and cancellation
"""
import sys
import time
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from src.exchange.coinex_client import CoinExClient
from src.utils.logger import get_logger

logger = get_logger(__name__)

def test_order_endpoints():
    """Test all order-related endpoints comprehensively"""
    print("🔍 Testing CoinEx Futures Order Endpoints...")
    
    try:
        # Initialize client
        client = CoinExClient()
        
        # Get current price
        ticker = client.get_ticker("ETHUSDT")
        current_price = float(ticker[0]['last'] if isinstance(ticker, list) else ticker['last'])
        safe_price = current_price * 0.5  # 50% below market
        test_quantity = 0.005
        
        print(f"Current ETHUSDT price: ${current_price:.2f}")
        print(f"Test order: {test_quantity} ETH @ ${safe_price:.2f} (50% below market)")
        
        # Step 1: Place order using current working method
        print("\n1. Placing test order...")
        client_id = f"TEST_ORDER_{int(time.time())}"
        
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
        
        # Place order using the working POST method
        import json
        import requests
        from src.exchange.auth import CoinExAuth
        
        auth = CoinExAuth()
        body_str = json.dumps(order_data)
        headers = auth.get_auth_headers('POST', '/v2/futures/order', None, body_str)
        
        response = requests.post(
            "https://api.coinex.com/v2/futures/order",
            data=body_str,
            headers=headers,
            timeout=30
        )
        
        if response.status_code == 200:
            order_response = response.json()
            if order_response.get('code') == 0:
                order_id = order_response['data']['order_id']
                print(f"✅ Order placed successfully!")
                print(f"   Order ID: {order_id}")
                print(f"   Client ID: {client_id}")
            else:
                print(f"❌ Order placement failed: {order_response}")
                return
        else:
            print(f"❌ HTTP error: {response.status_code} - {response.text}")
            return
        
        # Step 2: Test order status endpoint
        print(f"\n2. Testing order status endpoint...")
        test_order_status_endpoints(client, "ETHUSDT", order_id, client_id)
        
        # Step 3: Test order cancellation endpoints
        print(f"\n3. Testing order cancellation endpoints...")
        test_order_cancellation_endpoints(client, "ETHUSDT", order_id, client_id)
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

def test_order_status_endpoints(client, market, order_id, client_id):
    """Test different order status endpoints"""
    
    # Test the documented endpoint structure
    status_endpoints = [
        "/v2/futures/order-status",
        "/v2/futures/order/status", 
        "/futures/order-status",
        "/v2/order-status"
    ]
    
    for endpoint in status_endpoints:
        try:
            print(f"   Testing GET {endpoint}")
            
            # Test with order_id
            params = {
                "market": market,
                "market_type": "FUTURES",
                "order_id": order_id
            }
            
            response = client._request('GET', endpoint, params=params)
            print(f"   ✅ Success with order_id: {response}")
            break
            
        except Exception as e:
            print(f"   ❌ Failed: {e}")
            continue
    
    # Also test with client_id if order_id fails
    for endpoint in status_endpoints:
        try:
            print(f"   Testing GET {endpoint} with client_id")
            
            params = {
                "market": market,
                "market_type": "FUTURES", 
                "client_id": client_id
            }
            
            response = client._request('GET', endpoint, params=params)
            print(f"   ✅ Success with client_id: {response}")
            break
            
        except Exception as e:
            print(f"   ❌ Failed with client_id: {e}")
            continue

def test_order_cancellation_endpoints(client, market, order_id, client_id):
    """Test different order cancellation endpoints"""
    
    # Test the documented endpoint structure
    cancel_endpoints = [
        "/v2/futures/cancel-order",   # From documentation
        "/v2/futures/order/cancel",   # Alternative structure
        "/futures/cancel-order",      # Without v2
        "/v2/cancel-order"           # Simplified
    ]
    
    for endpoint in cancel_endpoints:
        try:
            print(f"   Testing POST {endpoint}")
            
            # Test with order_id first
            cancel_data = {
                "market": market,
                "market_type": "FUTURES",
                "order_id": order_id
            }
            
            response = client._request('POST', endpoint, data=cancel_data)
            print(f"   ✅ SUCCESS - Order cancelled with order_id!")
            print(f"   Response: {response}")
            return  # Success, exit
            
        except Exception as e:
            print(f"   ❌ Failed with order_id: {e}")
            
            # Try with client_id if order_id fails
            try:
                print(f"   Testing POST {endpoint} with client_id")
                
                cancel_data = {
                    "market": market,
                    "market_type": "FUTURES",
                    "client_id": client_id
                }
                
                response = client._request('POST', endpoint, data=cancel_data)
                print(f"   ✅ SUCCESS - Order cancelled with client_id!")
                print(f"   Response: {response}")
                return  # Success, exit
                
            except Exception as e2:
                print(f"   ❌ Failed with client_id: {e2}")
                continue
    
    print("   ❌ All cancellation endpoints failed!")

if __name__ == "__main__":
    test_order_endpoints()