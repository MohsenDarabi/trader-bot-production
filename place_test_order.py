#!/usr/bin/env python3
"""
Test Order Placement Script
Place small buy orders for testing the real-time buy-sell pairing system
"""
import sys
import time
from src.exchange.coinex_client import CoinExClient
from src.exchange.order_manager import OrderManager
from src.core.profitability import ProfitabilityValidator
from src.utils.logger import get_logger


logger = get_logger(__name__)


def place_test_buy_order(market: str = "ETHUSDT", amount: float = 0.001):
    """
    Place a small test buy order
    
    Args:
        market: Trading market (default: ETHUSDT)
        amount: Order amount (default: 0.001)
    """
    try:
        print("=" * 60)
        print("Test Order Placement")
        print("=" * 60)
        print(f"Market: {market}")
        print(f"Amount: {amount}")
        print()
        
        # Initialize components
        print("Initializing order management...")
        client = CoinExClient()
        validator = ProfitabilityValidator()
        order_manager = OrderManager(client, validator)
        
        # Get current market price
        print(f"Getting current {market} price...")
        ticker = client.get_ticker(market)
        current_price = float(ticker["last"])
        print(f"Current price: ${current_price:.2f}")
        
        # Calculate buy price (slightly below market for quick fill)
        buy_price = current_price * 0.9995  # 0.05% below market for quick fill
        position_size = amount * buy_price
        
        print(f"Buy price: ${buy_price:.2f} (0.05% below market)")
        print(f"Position size: ${position_size:.2f}")
        print()
        
        # Confirm order placement
        print("⚠️  This will place a REAL order with REAL money!")
        print(f"   Order: {amount} {market} @ ${buy_price:.2f}")
        print(f"   Cost: ~${position_size:.2f}")
        print()
        response = input("Continue? (y/N): ").strip().lower()
        
        if response != 'y':
            print("Order cancelled by user.")
            return None
        
        print("\nPlacing buy order...")
        order = order_manager.place_buy_order(
            market=market,
            amount=amount,
            price=buy_price,
            position_size=position_size,
            is_hide=True  # Hidden order
        )
        
        if order:
            print("✅ Order placed successfully!")
            print(f"   Order ID: {order.order_id}")
            print(f"   Client ID: {order.client_id}")
            print(f"   Amount: {amount}")
            print(f"   Price: ${buy_price:.2f}")
            print(f"   Status: {order.status}")
            print()
            print("🔍 Watch your pairing test script for:")
            print("   - Fill detection")
            print("   - Automatic creation of 5 sell orders")
            print("   - Order pairing completion")
            print()
            print(f"💡 You can cancel this order with:")
            print(f"   python3 cancel_test_order.py {order.order_id}")
            
            return order.order_id
        else:
            print("❌ Failed to place order")
            print("   Check your account balance and API permissions")
            return None
            
    except Exception as e:
        print(f"❌ Error placing test order: {e}")
        logger.error(f"Test order error: {e}", exc_info=True)
        return None


def get_account_balance():
    """Get current account balance for reference"""
    try:
        client = CoinExClient()
        balance = client.get_account_info()
        
        print("Account Balance:")
        if isinstance(balance, list):
            for asset in balance:
                if asset.get('ccy') == 'USDT':
                    available = float(asset.get('available', 0))
                    frozen = float(asset.get('frozen', 0))
                    print(f"  USDT: ${available:.2f} available, ${frozen:.2f} frozen")
                    break
        print()
        
    except Exception as e:
        print(f"Could not fetch balance: {e}")


def main():
    """Main function"""
    # Parse command line arguments
    market = sys.argv[1] if len(sys.argv) > 1 else "ETHUSDT"
    amount = float(sys.argv[2]) if len(sys.argv) > 2 else 0.001
    
    # Validate market
    valid_markets = ["ETHUSDT", "BTCUSDT", "ADAUSDT", "DOTUSDT"]
    if market not in valid_markets:
        print(f"⚠️  Market {market} not in recommended test markets: {valid_markets}")
        response = input("Continue anyway? (y/N): ").strip().lower()
        if response != 'y':
            return
    
    # Validate amount
    if amount > 0.01:  # More than ~$30-40
        print(f"⚠️  Amount {amount} seems large for testing")
        print("   Recommended test amounts: 0.001 - 0.01")
        response = input("Continue with large amount? (y/N): ").strip().lower()
        if response != 'y':
            return
    
    # Show account balance
    get_account_balance()
    
    # Place test order
    order_id = place_test_buy_order(market, amount)
    
    if order_id:
        print("=" * 60)
        print("Next Steps:")
        print("1. Monitor your test_order_pairing.py output")
        print("2. Watch for automatic sell order creation")
        print("3. Check your exchange account for new orders")
        print("4. Cancel test orders when done testing")
        print("=" * 60)


if __name__ == "__main__":
    main()