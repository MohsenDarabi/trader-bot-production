#!/usr/bin/env python3
"""
Test CoinEx futures order placement using CCXT library
This will help us understand the correct endpoint and parameters
"""
import os
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / 'src'))

def test_ccxt_coinex():
    """Test CoinEx futures through CCXT"""
    print("🔍 Testing CoinEx Futures with CCXT Library...")
    
    try:
        # Try to import ccxt
        try:
            import ccxt
            print("✅ CCXT library is installed")
        except ImportError:
            print("❌ CCXT not installed. Installing...")
            os.system("pip install ccxt")
            import ccxt
            print("✅ CCXT installed successfully")
        
        # Get credentials from environment
        api_key = os.getenv('COINEX_API_KEY')
        api_secret = os.getenv('COINEX_API_SECRET')
        
        if not api_key or not api_secret:
            print("❌ Missing API credentials in environment")
            return
        
        print(f"✅ Found API credentials")
        
        # Initialize CoinEx exchange
        print("\n1. Initializing CoinEx with CCXT...")
        exchange = ccxt.coinex({
            'apiKey': api_key,
            'secret': api_secret,
            'options': {
                'defaultType': 'swap',  # For futures/perpetual contracts
            },
            'enableRateLimit': True,
        })
        
        # Enable sandbox mode for testing (if available)
        # exchange.set_sandbox_mode(True)
        
        print(f"CCXT Version: {ccxt.__version__}")
        print(f"Exchange ID: {exchange.id}")
        print(f"Exchange Version: {exchange.version}")
        
        # Load markets
        print("\n2. Loading markets...")
        markets = exchange.load_markets()
        
        # Find ETHUSDT futures
        futures_markets = {k: v for k, v in markets.items() if v['swap'] or v['future']}
        print(f"Found {len(futures_markets)} futures markets")
        
        # Look for ETHUSDT
        eth_market = None
        for symbol, market in futures_markets.items():
            if 'ETH' in symbol and 'USDT' in symbol:
                eth_market = market
                print(f"Found ETH futures market: {symbol}")
                print(f"Market info: {market}")
                break
        
        if not eth_market:
            print("❌ Could not find ETHUSDT futures market")
            return
        
        # Get current price
        print("\n3. Fetching current price...")
        ticker = exchange.fetch_ticker(eth_market['symbol'])
        current_price = ticker['last']
        print(f"Current price: ${current_price}")
        
        # Calculate safe test order
        safe_price = current_price * 0.5  # 50% below market
        min_amount = eth_market['limits']['amount']['min']
        test_amount = min_amount
        
        print(f"\n4. Test order parameters:")
        print(f"   Symbol: {eth_market['symbol']}")
        print(f"   Side: buy")
        print(f"   Type: limit")
        print(f"   Amount: {test_amount}")
        print(f"   Price: ${safe_price} (50% below market)")
        
        # Get balance first
        print("\n5. Checking balance...")
        balance = exchange.fetch_balance()
        usdt_balance = balance.get('USDT', {}).get('free', 0)
        print(f"USDT available: ${usdt_balance}")
        
        # Try to place order
        print("\n6. Attempting to place test order...")
        print("⚠️  This is a safe order 50% below market price")
        
        try:
            # Create the order
            order = exchange.create_order(
                symbol=eth_market['symbol'],
                type='limit',
                side='buy',
                amount=test_amount,
                price=safe_price,
                params={
                    'is_hide': True,  # Hidden order
                }
            )
            
            print("✅ ORDER PLACED SUCCESSFULLY!")
            print(f"Order details: {order}")
            
            # Cancel the order immediately
            print("\n7. Cancelling test order...")
            cancelled = exchange.cancel_order(order['id'], eth_market['symbol'])
            print("✅ Order cancelled successfully")
            
        except Exception as order_error:
            print(f"❌ Order placement failed: {order_error}")
            print(f"Error type: {type(order_error).__name__}")
            
            # Try to get more details about the error
            if hasattr(order_error, 'response'):
                print(f"Response: {order_error.response}")
        
        # Print API endpoints used by CCXT
        print("\n8. CCXT API Configuration:")
        print(f"Base URL: {exchange.urls.get('api', {})}")
        if hasattr(exchange, 'api'):
            print("API endpoints structure:")
            # This will show us what endpoints CCXT uses
            import json
            print(json.dumps(exchange.describe().get('api', {}), indent=2)[:500] + "...")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_ccxt_coinex()