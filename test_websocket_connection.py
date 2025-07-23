#!/usr/bin/env python3
"""
Test WebSocket Connection and Order Tracking
Verifies that WebSocket is working properly for real-time order monitoring
"""
import asyncio
import time
from datetime import datetime

from src.exchange.websocket_client import CoinExWebSocketClient
from src.exchange.order_tracker import OrderTracker, OrderSide
from src.exchange.coinex_client import CoinExClient
from src.utils.logger import get_logger


logger = get_logger(__name__)


class WebSocketTester:
    """Test WebSocket functionality"""
    
    def __init__(self):
        self.ws_client = None
        self.rest_client = None
        self.order_tracker = None
        self.test_results = {
            "connection": False,
            "authentication": False,
            "order_subscription": False,
            "user_deals_subscription": False,
            "order_received": False,
            "deal_received": False,
            "messages_received": []
        }
    
    async def test_websocket_connection(self):
        """Test WebSocket connection and subscriptions"""
        try:
            logger.info("=" * 60)
            logger.info("Starting WebSocket Connection Test")
            logger.info("=" * 60)
            
            # Initialize clients
            self.rest_client = CoinExClient()
            self.ws_client = CoinExWebSocketClient()
            self.order_tracker = OrderTracker(self.ws_client, self.rest_client)
            
            # Register test handlers
            self._register_test_handlers()
            
            # Test 1: Connection
            logger.info("\n1. Testing WebSocket connection...")
            connected = await self.ws_client.connect()
            self.test_results["connection"] = connected
            logger.info(f"   Connection: {'✓ SUCCESS' if connected else '✗ FAILED'}")
            
            if not connected:
                logger.error("Cannot proceed without connection")
                return self.test_results
            
            # Test 2: Authentication
            logger.info("\n2. Testing authentication...")
            authenticated = await self.ws_client.authenticate()
            self.test_results["authentication"] = authenticated
            logger.info(f"   Authentication: {'✓ SUCCESS' if authenticated else '✗ FAILED'}")
            
            # Test 3: Order subscription
            logger.info("\n3. Testing order subscription...")
            order_sub = await self.ws_client.subscribe_orders()
            self.test_results["order_subscription"] = order_sub
            logger.info(f"   Order subscription: {'✓ SUCCESS' if order_sub else '✗ FAILED'}")
            
            # Test 4: User deals subscription
            logger.info("\n4. Testing user deals subscription...")
            deals_sub = await self.ws_client.subscribe_user_deals()
            self.test_results["user_deals_subscription"] = deals_sub
            logger.info(f"   User deals subscription: {'✓ SUCCESS' if deals_sub else '✗ FAILED'}")
            
            # Test 5: Sync existing orders
            logger.info("\n5. Syncing existing orders...")
            await self.order_tracker.sync_existing_orders()
            tracker_stats = self.order_tracker.get_statistics()
            logger.info(f"   Found {tracker_stats['total_orders']} existing orders")
            logger.info(f"   - Buy orders: {tracker_stats['buy_orders']}")
            logger.info(f"   - Sell orders: {tracker_stats['sell_orders']}")
            logger.info(f"   - Filled orders: {tracker_stats['filled_orders']}")
            
            # Test 6: Wait for real-time updates
            logger.info("\n6. Waiting for real-time updates (30 seconds)...")
            logger.info("   Place or cancel an order to test real-time updates")
            
            start_time = time.time()
            message_count_start = len(self.test_results["messages_received"])
            
            while time.time() - start_time < 30:
                await asyncio.sleep(1)
                new_messages = len(self.test_results["messages_received"]) - message_count_start
                if new_messages > 0:
                    logger.info(f"   Received {new_messages} real-time updates!")
                    break
                remaining = int(30 - (time.time() - start_time))
                if remaining % 5 == 0:
                    logger.info(f"   Waiting... {remaining}s remaining")
            
            # Print results summary
            self._print_test_summary()
            
            return self.test_results
            
        except Exception as e:
            logger.error(f"Test error: {e}", exc_info=True)
            return self.test_results
        finally:
            if self.ws_client:
                await self.ws_client.disconnect()
    
    def _register_test_handlers(self):
        """Register test message handlers"""
        
        def order_handler(params):
            """Handle order updates"""
            logger.info(f"[ORDER UPDATE] Event: {params.get('event')}")
            self.test_results["order_received"] = True
            self.test_results["messages_received"].append({
                "type": "order",
                "time": datetime.now().isoformat(),
                "event": params.get("event"),
                "orders": len(params.get("orders", []))
            })
            
            # Log order details
            for order in params.get("orders", []):
                logger.info(f"  Order ID: {order.get('order_id')}, "
                          f"Market: {order.get('market')}, "
                          f"Side: {order.get('side')}, "
                          f"Status: {order.get('status')}")
        
        def deals_handler(params):
            """Handle user deals updates"""
            logger.info(f"[USER DEAL] Received {len(params.get('deals', []))} deals")
            self.test_results["deal_received"] = True
            self.test_results["messages_received"].append({
                "type": "user_deals",
                "time": datetime.now().isoformat(),
                "deals": len(params.get("deals", []))
            })
            
            # Log deal details
            for deal in params.get("deals", []):
                logger.info(f"  Deal ID: {deal.get('deal_id')}, "
                          f"Order ID: {deal.get('order_id')}, "
                          f"Amount: {deal.get('amount')}, "
                          f"Price: {deal.get('price')}")
        
        # Register handlers
        self.ws_client.register_handler("order.update", order_handler)
        self.ws_client.register_handler("user_deals.update", deals_handler)
    
    def _print_test_summary(self):
        """Print test results summary"""
        logger.info("\n" + "=" * 60)
        logger.info("WebSocket Test Summary")
        logger.info("=" * 60)
        
        # Connection tests
        logger.info("\nConnection Tests:")
        logger.info(f"  Connection:        {'✓ PASS' if self.test_results['connection'] else '✗ FAIL'}")
        logger.info(f"  Authentication:    {'✓ PASS' if self.test_results['authentication'] else '✗ FAIL'}")
        logger.info(f"  Order Sub:         {'✓ PASS' if self.test_results['order_subscription'] else '✗ FAIL'}")
        logger.info(f"  User Deals Sub:    {'✓ PASS' if self.test_results['user_deals_subscription'] else '✗ FAIL'}")
        
        # Real-time updates
        logger.info("\nReal-time Updates:")
        logger.info(f"  Order Updates:     {'✓ RECEIVED' if self.test_results['order_received'] else '✗ NOT RECEIVED'}")
        logger.info(f"  Deal Updates:      {'✓ RECEIVED' if self.test_results['deal_received'] else '✗ NOT RECEIVED'}")
        logger.info(f"  Total Messages:    {len(self.test_results['messages_received'])}")
        
        # WebSocket status
        logger.info("\nWebSocket Status:")
        logger.info(f"  Connected:         {self.ws_client.is_connected if self.ws_client else False}")
        logger.info(f"  Authenticated:     {self.ws_client.is_authenticated if self.ws_client else False}")
        
        # Overall result
        all_passed = (
            self.test_results["connection"] and
            self.test_results["authentication"] and
            self.test_results["order_subscription"] and
            self.test_results["user_deals_subscription"]
        )
        
        logger.info("\nOverall Result:")
        if all_passed:
            logger.info("  ✓ WebSocket is working properly!")
            logger.info("  - Connection established")
            logger.info("  - Authentication successful")
            logger.info("  - Subscriptions active")
            if self.test_results["order_received"] or self.test_results["deal_received"]:
                logger.info("  - Real-time updates confirmed")
            else:
                logger.info("  - Ready for real-time updates (place an order to verify)")
        else:
            logger.info("  ✗ WebSocket has issues - check the failures above")
        
        logger.info("=" * 60)


async def main():
    """Run WebSocket tests"""
    tester = WebSocketTester()
    results = await tester.test_websocket_connection()
    
    # Return exit code based on results
    if results["connection"] and results["authentication"]:
        return 0
    else:
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    exit(exit_code)