#!/usr/bin/env python3
"""
Check current pending orders on CoinEx
"""
import sys
sys.path.insert(0, 'src')

from src.exchange.coinex_client import CoinExClient

def check_orders():
    client = CoinExClient()
    try:
        print("Fetching current pending orders...")
        orders = client.get_pending_orders()
        
        if isinstance(orders, dict) and 'data' in orders:
            data = orders['data']
            print(f"Found {len(data)} pending orders")
            
            for order in data:
                print(f"\n🔸 Pending Order:")
                print(f"  Order ID: {order.get('order_id')}")
                print(f"  Market: {order.get('market')}")
                print(f"  Side: {order.get('side')}")
                print(f"  Amount: {order.get('amount')}")
                print(f"  Price: {order.get('price')}")
                print(f"  Status: {order.get('status')}")
                print(f"  Created: {order.get('created_at')}")
            
            if not data:
                print("\n✅ No pending orders found")
                
        else:
            print("Unexpected response format:", orders)
            
    except Exception as e:
        print(f"❌ Error checking orders: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    check_orders()