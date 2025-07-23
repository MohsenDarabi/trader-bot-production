#!/usr/bin/env python3
"""
Complete order placement and cancellation demonstration
Shows the full order lifecycle with proper error handling and safety features
"""
import sys
import time
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from exchange.coinex_client import CoinExClient
from utils.test_mode import get_test_mode_status, validate_test_mode_order
from utils.error_handling import error_handler
from utils.logger import get_logger
from process_lock import ProcessLock

logger = get_logger(__name__)

def demonstrate_order_lifecycle():
    """Demonstrate complete order placement and cancellation"""
    
    # Use process lock to prevent conflicts
    with ProcessLock():
        print("🎬 CoinEx Order Lifecycle Demonstration")
        print("=" * 60)
        
        try:
            # Step 1: Initialize and check system status
            print("\n📋 STEP 1: System Initialization")
            print("-" * 30)
            
            client = CoinExClient()
            test_status = get_test_mode_status()
            
            print(f"✅ CoinEx client initialized")
            print(f"🛡️ Safety mode: {test_status.position_size_mode}")
            
            if test_status.is_active:
                print(f"🟡 TEST MODE ACTIVE - Day {test_status.current_day}/{test_status.total_days}")
                print(f"   {test_status.days_remaining:.1f} days remaining")
                print(f"   Orders will be limited to minimum sizes for safety")
            else:
                print(f"🟢 NORMAL MODE - Full position sizing available")
            
            # Step 2: Get market data
            print("\n📊 STEP 2: Market Data Collection")
            print("-" * 30)
            
            market = "ETHUSDT"
            ticker = client.get_ticker(market)
            current_price = float(ticker[0]['last'] if isinstance(ticker, list) else ticker['last'])
            
            print(f"Market: {market}")
            print(f"Current price: ${current_price:,.2f}")
            
            # Calculate safe order price (50% below market - will never execute)
            safe_price = current_price * 0.5
            print(f"Safe order price: ${safe_price:,.2f} (50% below market - won't execute)")
            
            # Step 3: Order size validation
            print("\n⚖️ STEP 3: Order Size Validation")
            print("-" * 30)
            
            # Get account balance
            account_info = client.get_account_info()
            available_balance = 1000.0  # Assume $1000 for demo
            
            # Test different order sizes
            requested_amount = 0.01  # Request 0.01 ETH
            is_valid, adjusted_amount, reason = validate_test_mode_order(
                requested_amount, safe_price, available_balance
            )
            
            print(f"Requested amount: {requested_amount} ETH")
            print(f"Adjusted amount: {adjusted_amount:.6f} ETH")
            print(f"Validation result: {'✅ Valid' if is_valid else '❌ Invalid'}")
            print(f"Reason: {reason}")
            
            # Step 4: Place order
            print("\n📝 STEP 4: Order Placement")
            print("-" * 30)
            
            client_id = f"DEMO_ORDER_{int(time.time())}"
            
            print(f"Placing order:")
            print(f"  📈 Market: {market}")
            print(f"  💰 Amount: {adjusted_amount:.6f} ETH")
            print(f"  💲 Price: ${safe_price:,.2f}")
            print(f"  🆔 Client ID: {client_id}")
            print(f"  🔒 Hidden: True (not visible in public order book)")
            
            order_result = client.place_order(
                market=market,
                side="buy",
                amount=str(adjusted_amount),
                order_type="limit",
                price=str(safe_price),
                client_id=client_id,
                is_hide=True
            )
            
            order_id = order_result.get('order_id')
            print(f"✅ Order placed successfully!")
            print(f"   Order ID: {order_id}")
            print(f"   Status: {order_result.get('status', 'Unknown')}")
            
            # Step 5: Check order status
            print("\n🔍 STEP 5: Order Status Check")
            print("-" * 30)
            
            time.sleep(1)  # Brief pause
            
            order_status = client.get_order_status(market, order_id=order_id)
            print(f"Order status: {order_status.get('status')}")
            print(f"Filled amount: {order_status.get('filled_amount', '0')}")
            print(f"Remaining: {order_status.get('unfilled_amount', '0')}")
            print(f"Price: ${order_status.get('price')}")
            
            # Step 6: Check pending orders
            print("\n📋 STEP 6: Pending Orders Check")
            print("-" * 30)
            
            pending_orders = client.get_pending_orders(market)
            orders_list = pending_orders.get('data', [])
            
            print(f"Total pending orders for {market}: {len(orders_list)}")
            
            # Find our order
            our_order = None
            for order in orders_list:
                if order.get('order_id') == order_id:
                    our_order = order
                    break
            
            if our_order:
                print(f"✅ Found our order in pending list:")
                print(f"   Order ID: {our_order.get('order_id')}")
                print(f"   Client ID: {our_order.get('client_id')}")
                print(f"   Price: ${our_order.get('price')}")
                print(f"   Amount: {our_order.get('amount')}")
            else:
                print(f"⚠️ Our order not found in pending list (may have filled or expired)")
            
            # Step 7: Cancel order
            print("\n🗑️ STEP 7: Order Cancellation")
            print("-" * 30)
            
            print(f"Cancelling order {order_id}...")
            
            cancel_result = client.cancel_order(market, order_id=order_id)
            print(f"✅ Order cancelled successfully!")
            print(f"   Final status: {cancel_result.get('status', 'cancelled')}")
            print(f"   Updated at: {cancel_result.get('updated_at')}")
            
            # Step 8: Verify cancellation
            print("\n✔️ STEP 8: Cancellation Verification")
            print("-" * 30)
            
            time.sleep(1)  # Brief pause
            
            # Check pending orders again
            pending_orders_after = client.get_pending_orders(market)
            orders_after = pending_orders_after.get('data', [])
            
            order_still_pending = False
            for order in orders_after:
                if order.get('order_id') == order_id:
                    order_still_pending = True
                    break
            
            if order_still_pending:
                print(f"⚠️ Order still appears in pending list")
            else:
                print(f"✅ Order successfully removed from pending list")
            
            print(f"Pending orders after cancellation: {len(orders_after)}")
            
            # Step 9: Error summary
            print("\n📊 STEP 9: Error Summary")
            print("-" * 30)
            
            error_summary = error_handler.get_error_summary()
            print(f"Total errors during demo: {error_summary['total_errors']}")
            print(f"Recent errors: {error_summary['recent_errors']}")
            
            if error_summary['category_breakdown']:
                print(f"Error categories: {error_summary['category_breakdown']}")
            else:
                print("✅ No errors occurred during demonstration")
            
            # Final summary
            print("\n🎉 DEMONSTRATION COMPLETE")
            print("=" * 60)
            print(f"✅ Successfully demonstrated complete order lifecycle:")
            print(f"   • Order placement with safety validation")
            print(f"   • Order status monitoring")
            print(f"   • Pending orders tracking")  
            print(f"   • Order cancellation")
            print(f"   • Error handling and logging")
            print(f"\n💡 The order was placed at 50% below market price")
            print(f"   to ensure it would not execute and consume capital.")
            
            if test_status.is_active:
                print(f"\n🛡️ Test mode protected you by limiting order size")
                print(f"   from {0.01} to {adjusted_amount:.6f} ETH")
            
        except Exception as e:
            print(f"\n❌ Error during demonstration: {e}")
            logger.error(f"Demo error: {e}", exc_info=True)
            
            # Show error summary even on failure
            error_summary = error_handler.get_error_summary()
            if error_summary['recent_errors'] > 0:
                print(f"\n📊 Error Summary:")
                print(f"Recent errors: {error_summary['recent_errors']}")
                print(f"Categories: {error_summary['category_breakdown']}")

if __name__ == "__main__":
    demonstrate_order_lifecycle()