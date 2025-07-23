#!/usr/bin/env python3
"""
Cancel Test Orders Script
Cancel specific test orders or clean up multiple test orders
"""
import sys
from src.exchange.coinex_client import CoinExClient
from src.utils.logger import get_logger


logger = get_logger(__name__)


def cancel_order_by_id(order_id: str, market: str = "ETHUSDT"):
    """
    Cancel a specific order by ID
    
    Args:
        order_id: Order ID to cancel
        market: Market for the order
    """
    try:
        print(f"Cancelling order {order_id} in {market}...")
        
        client = CoinExClient()
        result = client.cancel_order(market=market, order_id=int(order_id))
        
        if result:
            print(f"✅ Order {order_id} cancelled successfully")
            return True
        else:
            print(f"❌ Failed to cancel order {order_id}")
            return False
            
    except Exception as e:
        print(f"❌ Error cancelling order {order_id}: {e}")
        logger.error(f"Cancel order error: {e}", exc_info=True)
        return False


def list_pending_orders(market: str = None):
    """
    List all pending orders
    
    Args:
        market: Optional market filter
    """
    try:
        print("Fetching pending orders...")
        
        client = CoinExClient()
        response = client.get_pending_orders(market=market)
        orders = response.get("data", [])
        
        if not orders:
            print("No pending orders found.")
            return []
        
        print(f"\nFound {len(orders)} pending orders:")
        print("-" * 80)
        print(f"{'Order ID':<15} {'Market':<12} {'Side':<6} {'Amount':<12} {'Price':<12} {'Status'}")
        print("-" * 80)
        
        for order in orders:
            order_id = order.get("order_id")
            market_name = order.get("market")
            side = order.get("side")
            amount = order.get("amount")
            price = order.get("price")
            status = order.get("status")
            
            print(f"{order_id:<15} {market_name:<12} {side:<6} {amount:<12} {price:<12} {status}")
        
        print("-" * 80)
        return orders
        
    except Exception as e:
        print(f"❌ Error fetching orders: {e}")
        logger.error(f"List orders error: {e}", exc_info=True)
        return []


def cancel_all_test_orders(market: str = None, confirm: bool = True):
    """
    Cancel all pending orders (use with caution!)
    
    Args:
        market: Optional market filter
        confirm: Whether to ask for confirmation
    """
    try:
        orders = list_pending_orders(market)
        if not orders:
            return
        
        if confirm:
            print(f"\n⚠️  This will cancel ALL {len(orders)} pending orders!")
            if market:
                print(f"   Market filter: {market}")
            response = input("Are you sure? (y/N): ").strip().lower()
            if response != 'y':
                print("Cancelled by user.")
                return
        
        print(f"\nCancelling {len(orders)} orders...")
        cancelled = 0
        failed = 0
        
        for order in orders:
            order_id = str(order.get("order_id"))
            order_market = order.get("market")
            
            if cancel_order_by_id(order_id, order_market):
                cancelled += 1
            else:
                failed += 1
        
        print(f"\nResults:")
        print(f"  ✅ Cancelled: {cancelled}")
        print(f"  ❌ Failed: {failed}")
        
    except Exception as e:
        print(f"❌ Error in bulk cancel: {e}")
        logger.error(f"Bulk cancel error: {e}", exc_info=True)


def show_usage():
    """Show usage instructions"""
    print("Cancel Test Orders - Usage:")
    print()
    print("Cancel specific order:")
    print("  python3 cancel_test_order.py <order_id> [market]")
    print("  Example: python3 cancel_test_order.py 123456789 ETHUSDT")
    print()
    print("List pending orders:")
    print("  python3 cancel_test_order.py --list [market]")
    print("  Example: python3 cancel_test_order.py --list ETHUSDT")
    print()
    print("Cancel all pending orders:")
    print("  python3 cancel_test_order.py --all [market]")
    print("  Example: python3 cancel_test_order.py --all ETHUSDT")
    print()
    print("Show this help:")
    print("  python3 cancel_test_order.py --help")


def main():
    """Main function"""
    if len(sys.argv) < 2:
        show_usage()
        return
    
    command = sys.argv[1]
    
    if command == "--help":
        show_usage()
        
    elif command == "--list":
        market = sys.argv[2] if len(sys.argv) > 2 else None
        list_pending_orders(market)
        
    elif command == "--all":
        market = sys.argv[2] if len(sys.argv) > 2 else None
        cancel_all_test_orders(market)
        
    else:
        # Assume it's an order ID
        order_id = command
        market = sys.argv[2] if len(sys.argv) > 2 else "ETHUSDT"
        
        try:
            int(order_id)  # Validate it's a number
        except ValueError:
            print(f"❌ Invalid order ID: {order_id}")
            print("Order ID must be a number")
            return
        
        cancel_order_by_id(order_id, market)


if __name__ == "__main__":
    main()