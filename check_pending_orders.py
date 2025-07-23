#!/usr/bin/env python3
"""
Check pending orders to verify our test order
"""
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from src.exchange.coinex_client import CoinExClient

client = CoinExClient()

print("Checking pending orders for ETHUSDT...")

try:
    # Get pending orders
    response = client.get_pending_orders(market="ETHUSDT")
    
    if isinstance(response, dict):
        orders = response.get('data', [])
        print(f"\nFound {len(orders)} pending orders")
        
        for order in orders:
            print(f"\nOrder ID: {order.get('order_id')}")
            print(f"Client ID: {order.get('client_id')}")
            print(f"Side: {order.get('side')}")
            print(f"Price: {order.get('price')}")
            print(f"Amount: {order.get('amount')}")
            print(f"Status: {order.get('status')}")
            
            # If this is our test order, try different cancel methods
            if order.get('client_id') == "DEBUG_TEST_1753247481":
                print("\n✅ Found our test order!")
                
except Exception as e:
    print(f"Error: {e}")