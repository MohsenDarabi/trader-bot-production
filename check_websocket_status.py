#!/usr/bin/env python3
"""
Quick WebSocket Status Check
Shows current WebSocket connection status and statistics
"""
import asyncio
import sys
from datetime import datetime

from src.exchange.websocket_client import CoinExWebSocketClient
from src.exchange.order_tracker import OrderTracker
from src.exchange.coinex_client import CoinExClient
from src.utils.logger import get_logger


logger = get_logger(__name__)


async def check_websocket_status():
    """Quick status check"""
    ws_client = None
    rest_client = None
    
    try:
        # Quick connection test
        logger.info("Checking WebSocket connection status...")
        
        ws_client = CoinExWebSocketClient()
        rest_client = CoinExClient()
        
        # Try to connect
        connected = await ws_client.connect()
        
        print("\n" + "=" * 50)
        print("WebSocket Status Check")
        print("=" * 50)
        print(f"Timestamp: {datetime.now().isoformat()}")
        print(f"\nConnection Status:")
        print(f"  Connected:     {'✓ YES' if connected else '✗ NO'}")
        
        if connected:
            # Try authentication
            authenticated = await ws_client.authenticate()
            print(f"  Authenticated: {'✓ YES' if authenticated else '✗ NO'}")
            
            if authenticated:
                # Try subscriptions
                order_sub = await ws_client.subscribe_orders()
                deals_sub = await ws_client.subscribe_user_deals()
                
                print(f"\nSubscriptions:")
                print(f"  Orders:        {'✓ ACTIVE' if order_sub else '✗ FAILED'}")
                print(f"  User Deals:    {'✓ ACTIVE' if deals_sub else '✗ FAILED'}")
                
                # Quick order check
                order_tracker = OrderTracker(ws_client, rest_client)
                await order_tracker.sync_existing_orders()
                stats = order_tracker.get_statistics()
                
                print(f"\nOrder Statistics:")
                print(f"  Total Orders:  {stats['total_orders']}")
                print(f"  Buy Orders:    {stats['buy_orders']}")
                print(f"  Sell Orders:   {stats['sell_orders']}")
                print(f"  Filled Orders: {stats['filled_orders']}")
                
                print(f"\nResult: ✓ WebSocket is working properly!")
            else:
                print(f"\nResult: ✗ WebSocket authentication failed")
        else:
            print(f"\nResult: ✗ WebSocket connection failed")
        
        print("=" * 50)
        
    except Exception as e:
        print(f"\nError: {e}")
        print(f"Result: ✗ WebSocket check failed")
        return 1
    finally:
        if ws_client:
            await ws_client.disconnect()
        if rest_client:
            rest_client.close()
    
    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(check_websocket_status())
    sys.exit(exit_code)