#!/usr/bin/env python3
"""
Script to check order history and understand what happened with multiple buy orders
"""

import os
import sys
import time
import hmac
import hashlib
import requests
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Any
import json

class CoinExOrderChecker:
    def __init__(self, env_file=None):
        # Load environment variables from file if provided
        if env_file and os.path.exists(env_file):
            with open(env_file, 'r') as f:
                for line in f:
                    if '=' in line and not line.startswith('#'):
                        key, value = line.strip().split('=', 1)
                        os.environ[key] = value
        
        self.access_id = os.getenv('COINEX_API_KEY') or os.getenv('COINEX_ACCESS_ID')
        self.secret_key = os.getenv('COINEX_API_SECRET') or os.getenv('COINEX_SECRET_KEY')
        self.base_url = "https://api.coinex.com/v2"
        
        if not self.access_id or not self.secret_key:
            print("Error: COINEX_API_KEY and COINEX_API_SECRET environment variables not found")
            print(f"Tried to load from: {env_file}")
            exit(1)
            
        print(f"✓ Loaded API credentials (Key: {self.access_id[:8]}...)")
        
    def _generate_signature(self, method: str, request_path: str, body: str, timestamp: str) -> str:
        """Generate signature for API authentication"""
        prepared_str = f"{method}{request_path}{body}{timestamp}"
        signature = hmac.new(
            self.secret_key.encode('utf-8'),
            prepared_str.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        return signature
    
    def _make_request(self, method: str, endpoint: str, params: Dict = None) -> Dict:
        """Make authenticated API request"""
        timestamp = str(int(time.time() * 1000))
        request_path = f"/v2{endpoint}"
        
        # Prepare query string for GET requests
        query_string = ""
        if params and method == "GET":
            query_string = "&".join([f"{k}={v}" for k, v in params.items()])
            if query_string:
                request_path = f"{request_path}?{query_string}"
        
        body = ""
        signature = self._generate_signature(method, request_path, body, timestamp)
        
        headers = {
            "X-COINEX-KEY": self.access_id,
            "X-COINEX-SIGN": signature,
            "X-COINEX-TIMESTAMP": timestamp,
            "Content-Type": "application/json"
        }
        
        url = f"{self.base_url}{endpoint}"
        if query_string and method == "GET":
            url = f"{url}?{query_string}"
            
        response = requests.request(method, url, headers=headers)
        return response.json()
    
    def get_order_history(self, market: str, start_time: datetime = None, limit: int = 100) -> List[Dict]:
        """Get order history for a market"""
        params = {
            "market": market,
            "market_type": "FUTURES",
            "limit": limit,
            "page": 1
        }
        
        if start_time:
            # Convert to milliseconds
            params["start_time"] = int(start_time.timestamp() * 1000)
        
        response = self._make_request("GET", "/futures/finished-order", params)
        
        if response.get("code") == 0:
            return response.get("data", [])
        else:
            print(f"Error getting orders: {response}")
            return []
    
    def get_order_deals(self, order_id: int, market: str) -> List[Dict]:
        """Get transaction details for a specific order"""
        params = {
            "market": market,
            "market_type": "FUTURES",
            "order_id": order_id,
            "limit": 100
        }
        
        response = self._make_request("GET", "/futures/order-deals", params)
        
        if response.get("code") == 0:
            return response.get("data", [])
        else:
            print(f"Error getting deals for order {order_id}: {response}")
            return []
    
    def analyze_trading_day(self, market: str):
        """Analyze all orders from today"""
        # Get orders from midnight today
        today_midnight = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        
        print(f"\n{'='*80}")
        print(f"ANALYZING {market} ORDERS FROM {today_midnight.strftime('%Y-%m-%d %H:%M:%S')} UTC")
        print(f"{'='*80}")
        
        # Get order history
        orders = self.get_order_history(market, today_midnight, limit=200)
        
        if not orders:
            print(f"No orders found for {market}")
            return
        
        # Separate buy and sell orders
        buy_orders = [o for o in orders if o.get("side") == "buy"]
        sell_orders = [o for o in orders if o.get("side") == "sell"]
        
        print(f"\n📊 ORDER SUMMARY:")
        print(f"  Total Orders: {len(orders)}")
        print(f"  Buy Orders: {len(buy_orders)}")
        print(f"  Sell Orders: {len(sell_orders)}")
        
        # Calculate signal prices based on yesterday's data
        print(f"\n📈 TRADING SIGNAL CALCULATION:")
        print(f"  Formula: Buy = Low + (High - Low) / 4")
        print(f"  Formula: Sell = High - (High - Low) / 4")
        
        # Analyze buy orders in detail
        print(f"\n🔍 BUY ORDERS ANALYSIS:")
        print(f"{'='*80}")
        
        for i, order in enumerate(buy_orders, 1):
            created_time = datetime.fromtimestamp(order.get("created_at", 0) / 1000, tz=timezone.utc)
            finished_time = datetime.fromtimestamp(order.get("finished_at", 0) / 1000, tz=timezone.utc) if order.get("finished_at") else None
            
            print(f"\n{i}. Order ID: {order.get('order_id')} | Client ID: {order.get('client_id', 'N/A')}")
            print(f"   Created: {created_time.strftime('%H:%M:%S')}")
            if finished_time:
                print(f"   Finished: {finished_time.strftime('%H:%M:%S')}")
            print(f"   Price: ${order.get('price')}")
            print(f"   Amount: {order.get('amount')} {market[:3]}")
            print(f"   Filled: {order.get('filled_amount', 0)}/{order.get('amount')}")
            print(f"   Status: {order.get('status')}")
            print(f"   Value: ${float(order.get('filled_value', 0)):.2f}")
            
            # Get deal details for filled orders
            if float(order.get('filled_amount', 0)) > 0:
                deals = self.get_order_deals(order.get('order_id'), market)
                if deals:
                    print(f"   Fills:")
                    for deal in deals:
                        deal_time = datetime.fromtimestamp(deal.get("created_at", 0) / 1000, tz=timezone.utc)
                        print(f"     - {deal_time.strftime('%H:%M:%S')}: {deal.get('amount')} @ ${deal.get('price')} (Fee: {deal.get('fee')} {deal.get('fee_ccy')})")
        
        # Check for multiple buy orders in short time windows
        print(f"\n⚠️  DUPLICATE BUY ORDER DETECTION:")
        print(f"{'='*80}")
        
        # Group buy orders by hour
        hourly_buys = {}
        for order in buy_orders:
            created_time = datetime.fromtimestamp(order.get("created_at", 0) / 1000, tz=timezone.utc)
            hour_key = created_time.strftime('%H:00')
            if hour_key not in hourly_buys:
                hourly_buys[hour_key] = []
            hourly_buys[hour_key].append(order)
        
        multiple_buy_hours = []
        for hour, orders in sorted(hourly_buys.items()):
            if len(orders) > 1:
                multiple_buy_hours.append(hour)
                print(f"\n🚨 Multiple buy orders in hour {hour}: {len(orders)} orders")
                for order in orders:
                    created_time = datetime.fromtimestamp(order.get("created_at", 0) / 1000, tz=timezone.utc)
                    print(f"   - {created_time.strftime('%H:%M:%S')}: Order {order.get('order_id')} | {order.get('client_id', 'N/A')} | Amount: {order.get('amount')}")
        
        if not multiple_buy_hours:
            print(f"\n✅ No duplicate buy orders detected")
        
        return len(buy_orders), len(sell_orders), multiple_buy_hours

def main():
    # Check command line arguments
    if len(sys.argv) > 1:
        env_file = sys.argv[1]
    else:
        # Try to find .env file
        if os.path.exists('.env.ada'):
            env_file = '.env.ada'
        elif os.path.exists('.env.aave'):
            env_file = '.env.aave'
        elif os.path.exists('.env'):
            env_file = '.env'
        else:
            env_file = None
    
    print(f"Using environment file: {env_file}")
    checker = CoinExOrderChecker(env_file)
    
    # Check both markets
    results = {}
    for market in ["ADAUSDT", "AAVEUSDT"]:
        buy_count, sell_count, problem_hours = checker.analyze_trading_day(market)
        results[market] = {
            'buy_orders': buy_count,
            'sell_orders': sell_count,
            'problem_hours': problem_hours
        }
        print("\n")
    
    # Summary
    print(f"\n{'='*80}")
    print(f"SUMMARY OF FINDINGS")
    print(f"{'='*80}")
    
    for market, data in results.items():
        print(f"\n{market}:")
        print(f"  - Buy Orders: {data.get('buy_orders', 0)}")
        print(f"  - Sell Orders: {data.get('sell_orders', 0)}")
        if data.get('problem_hours'):
            print(f"  - ⚠️ Multiple buy orders detected in hours: {', '.join(data['problem_hours'])}")
        else:
            print(f"  - ✅ No duplicate buy orders")

if __name__ == "__main__":
    main()