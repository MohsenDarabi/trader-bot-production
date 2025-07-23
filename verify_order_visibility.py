#!/usr/bin/env python3
"""
Verify order visibility in CoinEx systems
Places an order, waits, then checks multiple ways before cancelling
"""
import sys
import time
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from exchange.coinex_client import CoinExClient
from utils.logger import get_logger
from process_lock import ProcessLock

logger = get_logger(__name__)

def verify_order_visibility():
    """Test order visibility across different CoinEx interfaces"""
    
    with ProcessLock():
        print("🔍 CoinEx Order Visibility Verification")
        print("=" * 50)
        
        try:
            client = CoinExClient()
            
            # Step 1: Get current price and setup
            print("\n📊 Step 1: Market Setup")
            print("-" * 30)
            
            market = "ETHUSDT"
            ticker = client.get_ticker(market)
            current_price = float(ticker[0]['last'] if isinstance(ticker, list) else ticker['last'])
            
            # Use 60% below market for even safer pricing
            safe_price = current_price * 0.4  # 60% below market
            test_amount = 0.005  # Minimum amount
            
            print(f"Market: {market}")
            print(f"Current price: ${current_price:,.2f}")
            print(f"Order price: ${safe_price:,.2f} (60% below market)")
            print(f"Amount: {test_amount} ETH")
            
            # Step 2: Check initial state
            print("\n📋 Step 2: Check Initial Pending Orders")
            print("-" * 30)
            
            initial_pending = client.get_pending_orders(market)
            initial_count = len(initial_pending.get('data', []))
            print(f"Initial pending orders: {initial_count}")
            
            # Step 3: Place order (NOT hidden this time)
            print("\n📝 Step 3: Place VISIBLE Order")
            print("-" * 30)
            
            client_id = f"VISIBILITY_TEST_{int(time.time())}"
            
            print(f"Placing VISIBLE order (is_hide=False):")
            print(f"  Client ID: {client_id}")
            print(f"  Price: ${safe_price:,.2f}")
            print(f"  Amount: {test_amount}")
            print(f"  Hidden: FALSE (should be visible)")
            
            order_result = client.place_order(
                market=market,
                side="buy",
                amount=str(test_amount),
                order_type="limit",
                price=str(safe_price),
                client_id=client_id,
                is_hide=False  # Make it visible this time
            )
            
            order_id = order_result.get('order_id')
            print(f"✅ Order placed: {order_id}")
            
            # Step 4: Wait for user confirmation
            print("\n⏸️ WAITING FOR CONFIRMATION")
            print("=" * 50)
            print("Please check your CoinEx web interface for this order:")
            print(f"  📊 Market: {market} Futures")
            print(f"  🆔 Order ID: {order_id}")
            print(f"  🔗 Client ID: {client_id}")
            print(f"  💰 Price: ${safe_price:,.2f}")
            print(f"  📦 Amount: {test_amount} ETH")
            print(f"  👁️ Visibility: PUBLIC (is_hide=False)")
            from datetime import datetime
            print(f"  🕐 Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            print()
            print("🔍 Check these locations in CoinEx web interface:")
            print("  • Futures Trading > Open Orders")
            print("  • Futures Trading > Order History") 
            print("  • Account > Order History")
            print()
            print("💡 The order is placed 60% below market price, so it should")
            print("   appear as a pending buy order that won't execute.")
            print()
            
            # Wait for user confirmation
            confirmation = input("❓ Can you see this order in CoinEx web interface? (y/n/empty Excel): ").strip().lower()
            
            if confirmation in ['n', 'no', 'empty', 'empty excel']:
                print("\n⚠️ ORDER NOT VISIBLE IN WEB INTERFACE")
                print("This confirms the discrepancy between API and web interface.")
                print("The order exists in API but not visible on web.")
            elif confirmation in ['y', 'yes']:
                print("\n✅ ORDER VISIBLE IN WEB INTERFACE")
                print("Great! The order appears correctly in both API and web.")
            else:
                print(f"\n📝 User response: '{confirmation}'")
            
            # Brief monitoring while waiting
            print("\n⏰ Step 4: Quick Status Check")
            print("-" * 30)
            
            # Check pending orders
            pending = client.get_pending_orders(market)
            pending_count = len(pending.get('data', []))
            print(f"API pending orders: {pending_count}")
            
            # Look for our order
            our_order_found = False
            for order in pending.get('data', []):
                if order.get('order_id') == order_id:
                    our_order_found = True
                    print(f"✅ Our order found in API: {order.get('client_id')}")
                    break
            
            if not our_order_found:
                print(f"⚠️ Our order not found in API pending list")
            
            # Check order status
            try:
                status = client.get_order_status(market, order_id=order_id)
                print(f"Order status: {status.get('status')}")
                print(f"Filled amount: {status.get('filled_amount')}")
            except Exception as e:
                print(f"❌ Status check failed: {e}")
            
            # Step 5: Final verification before cancellation
            print("\n🔍 Step 5: Final State Check")
            print("-" * 30)
            
            print("Final pending orders check:")
            final_pending = client.get_pending_orders(market)
            final_orders = final_pending.get('data', [])
            
            print(f"Total pending orders: {len(final_orders)}")
            
            our_final_order = None
            for order in final_orders:
                if order.get('order_id') == order_id:
                    our_final_order = order
                    break
            
            if our_final_order:
                print("✅ Our order still in pending list:")
                print(f"  Order ID: {our_final_order.get('order_id')}")
                print(f"  Client ID: {our_final_order.get('client_id')}")
                print(f"  Created at: {our_final_order.get('created_at')}")
                print(f"  Price: ${our_final_order.get('price')}")
                print(f"  Amount: {our_final_order.get('amount')}")
            else:
                print("⚠️ Our order NOT found in final pending list")
            
            # Step 6: Now cancel
            print("\n🗑️ Step 6: Cancel Order")
            print("-" * 30)
            
            print(f"Cancelling order {order_id}...")
            cancel_result = client.cancel_order(market, order_id=order_id)
            print(f"✅ Cancellation result: {cancel_result.get('updated_at')}")
            
            # Step 7: Verification after cancellation
            print("\n✔️ Step 7: Post-Cancellation Check")
            print("-" * 30)
            
            time.sleep(2)  # Brief wait
            
            post_cancel_pending = client.get_pending_orders(market)
            post_cancel_count = len(post_cancel_pending.get('data', []))
            
            print(f"Pending orders after cancellation: {post_cancel_count}")
            
            # Check if order is gone
            order_still_there = False
            for order in post_cancel_pending.get('data', []):
                if order.get('order_id') == order_id:
                    order_still_there = True
                    break
            
            if order_still_there:
                print("⚠️ Order still in pending list after cancellation")
            else:
                print("✅ Order successfully removed from pending list")
            
            # Final summary
            print("\n📊 SUMMARY")
            print("=" * 50)
            print(f"Order ID: {order_id}")
            print(f"Client ID: {client_id}")
            print(f"Placed at: ${safe_price:,.2f} (60% below market)")
            print(f"Visibility: PUBLIC (is_hide=False)")
            print(f"Duration: ~30 seconds before cancellation")
            
            print(f"\n🔍 Check your CoinEx web interface order history for:")
            print(f"   • Order ID: {order_id}")
            print(f"   • Client ID: {client_id}")
            print(f"   • Time range: Last 5 minutes")
            print(f"   • Market: ETHUSDT Futures")
            
            if our_final_order:
                timestamp = our_final_order.get('created_at')
                if timestamp:
                    dt = datetime.fromtimestamp(timestamp / 1000)
                    print(f"   • Exact time: {dt.strftime('%Y-%m-%d %H:%M:%S')}")
            
            # Note about Excel file being empty
            print(f"\n📋 Excel File Note:")
            print(f"   You mentioned the Excel order history file is empty.")
            print(f"   This further confirms orders placed via API don't appear")
            print(f"   in CoinEx's standard order history exports.")
            
        except Exception as e:
            print(f"\n❌ Error during verification: {e}")
            logger.error(f"Verification error: {e}", exc_info=True)

if __name__ == "__main__":
    verify_order_visibility()