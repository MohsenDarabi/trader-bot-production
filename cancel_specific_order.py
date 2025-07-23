#!/usr/bin/env python3
"""
Cancel a specific order by ID
"""
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from exchange.coinex_client import CoinExClient
from process_lock import ProcessLock

def cancel_order_by_id():
    """Cancel the specific order that was left hanging"""
    
    with ProcessLock():
        print("🗑️ Cancelling Specific Order")
        print("=" * 40)
        
        client = CoinExClient()
        
        # The order details from the latest test
        order_id = 178478855239
        market = "ETHUSDT"
        client_id = "VISIBILITY_TEST_1753250551"
        
        print(f"Cancelling order: {order_id}")
        print(f"Market: {market}")
        print(f"Client ID: {client_id}")
        
        try:
            # Cancel the order
            result = client.cancel_order(market, order_id=order_id)
            print(f"✅ Order cancelled successfully!")
            print(f"Result: {result}")
            
        except Exception as e:
            print(f"❌ Error cancelling order: {e}")
            
            # Try to check if it still exists
            try:
                pending = client.get_pending_orders(market)
                order_found = False
                for order in pending.get('data', []):
                    if order.get('order_id') == order_id:
                        order_found = True
                        print(f"⚠️ Order still exists in pending list")
                        break
                
                if not order_found:
                    print(f"✅ Order not found in pending list (may have been cancelled)")
                    
            except Exception as e2:
                print(f"❌ Error checking pending orders: {e2}")

if __name__ == "__main__":
    cancel_order_by_id()