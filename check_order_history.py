#!/usr/bin/env python3
"""
Script to check order history and understand what happened with multiple buy orders
"""

import os
import time
import hmac
import hashlib
import requests
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Any
import json
from dotenv import load_dotenv

# Load environment variables
load_dotenv('.env')

class CoinExOrderChecker:
    def __init__(self):
        self.access_id = os.getenv('COINEX_ACCESS_ID')
        self.secret_key = os.getenv('COINEX_SECRET_KEY')
        self.base_url = "https://api.coinex.com/v2"
        
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
        
        for hour, orders in sorted(hourly_buys.items()):
            if len(orders) > 1:
                print(f"\n🚨 Multiple buy orders in hour {hour}: {len(orders)} orders")
                for order in orders:
                    created_time = datetime.fromtimestamp(order.get("created_at", 0) / 1000, tz=timezone.utc)
                    print(f"   - {created_time.strftime('%H:%M:%S')}: Order {order.get('order_id')} | {order.get('client_id', 'N/A')} | Amount: {order.get('amount')}")

def main():
    checker = CoinExOrderChecker()
    
    # Check both markets
    for market in ["ADAUSDT", "AAVEUSDT"]:
        checker.analyze_trading_day(market)
        print("\n")

if __name__ == "__main__":
    main()