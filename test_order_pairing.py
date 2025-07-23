#!/usr/bin/env python3
"""
Test Order Pairing System
Demonstrates the buy-sell order pairing functionality
"""
import asyncio
import time
from datetime import datetime

from src.exchange.websocket_client import CoinExWebSocketClient
from src.exchange.order_tracker import OrderTracker, OrderSide, OrderFill
from src.exchange.coinex_client import CoinExClient
from src.exchange.order_manager import OrderManager
from src.core.order_pairing_manager import OrderPairingManager
from src.core.profitability import ProfitabilityValidator
from src.utils.logger import get_logger


logger = get_logger(__name__)


class OrderPairingTester:
    """Test order pairing functionality"""
    
    def __init__(self):
        self.rest_client = None
        self.ws_client = None
        self.order_tracker = None
        self.order_manager = None
        self.pairing_manager = None
        self.test_market = "BTCUSDT"  # Default test market
    
    async def test_order_pairing(self, market: str = None):
        """Test the order pairing system"""
        if market:
            self.test_market = market
            
        try:
            logger.info("=" * 60)
            logger.info("Order Pairing System Test")
            logger.info("=" * 60)
            
            # Initialize components
            await self._initialize_components()
            
            # Configure pairing rules
            logger.info(f"\nConfiguring pairing rules for {self.test_market}...")
            sell_levels = [1.002, 1.005, 1.010, 1.015, 1.020]
            self.pairing_manager.configure_pairing_rule(
                market=self.test_market,
                sell_price_levels=sell_levels,
                max_age_minutes=60,
                min_fill_amount=0.0001
            )
            logger.info(f"✓ Configured {len(sell_levels)} sell levels: {sell_levels}")
            
            # Check current status
            await self._check_current_status()
            
            # Monitor for fills
            await self._monitor_order_fills()
            
            # Show final statistics
            self._show_pairing_statistics()
            
        except Exception as e:
            logger.error(f"Test error: {e}", exc_info=True)
        finally:
            await self._cleanup()
    
    async def _initialize_components(self):
        """Initialize all required components"""
        logger.info("\nInitializing components...")
        
        # REST client
        self.rest_client = CoinExClient()
        logger.info("✓ REST client initialized")
        
        # WebSocket client
        self.ws_client = CoinExWebSocketClient()
        connected = await self.ws_client.connect()
        if not connected:
            raise Exception("Failed to connect WebSocket")
        logger.info("✓ WebSocket connected")
        
        # Authenticate
        authenticated = await self.ws_client.authenticate()
        if not authenticated:
            raise Exception("Failed to authenticate WebSocket")
        logger.info("✓ WebSocket authenticated")
        
        # Order tracker
        self.order_tracker = OrderTracker(self.ws_client, self.rest_client)
        logger.info("✓ Order tracker initialized")
        
        # Order manager
        validator = ProfitabilityValidator()
        self.order_manager = OrderManager(self.rest_client, validator)
        logger.info("✓ Order manager initialized")
        
        # Pairing manager
        self.pairing_manager = OrderPairingManager(
            self.order_tracker, self.order_manager, self.rest_client
        )
        logger.info("✓ Pairing manager initialized")
        
        # Subscribe to updates
        await self.ws_client.subscribe_orders()
        await self.ws_client.subscribe_user_deals()
        logger.info("✓ Subscribed to order and deal updates")
        
        # Register handlers for monitoring
        self._register_monitoring_handlers()
    
    async def _check_current_status(self):
        """Check current orders and positions"""
        logger.info("\nChecking current status...")
        
        # Sync existing orders
        await self.order_tracker.sync_existing_orders(self.test_market)
        
        # Get statistics
        stats = self.order_tracker.get_statistics()
        logger.info(f"\nCurrent orders:")
        logger.info(f"  Total tracked: {stats['total_orders']}")
        logger.info(f"  Buy orders: {stats['buy_orders']}")
        logger.info(f"  Sell orders: {stats['sell_orders']}")
        logger.info(f"  Filled orders: {stats['filled_orders']}")
        logger.info(f"  Order pairs: {stats['total_pairs']}")
        logger.info(f"  Unmatched pairs: {stats['unmatched_pairs']}")
        
        # Check positions (skip if signature issues)
        try:
            positions = self.rest_client.get_positions(market=self.test_market)
            if positions and positions.get("data"):
                logger.info(f"\nCurrent positions:")
                for pos in positions["data"]:
                    logger.info(f"  {pos['market']}: {pos['amount']} @ {pos['price']}")
        except Exception as e:
            logger.info(f"\nPosition check skipped (REST API signature issue): {e}")
        
        # Process any unmatched fills
        unmatched_count = await self.pairing_manager.process_unmatched_fills()
        if unmatched_count > 0:
            logger.info(f"\n✓ Processed {unmatched_count} unmatched buy fills")
    
    async def _monitor_order_fills(self):
        """Monitor for order fills"""
        logger.info("\nMonitoring for order fills (60 seconds)...")
        logger.info("Place a BUY order to see automatic sell order creation!")
        logger.info("-" * 40)
        
        start_time = time.time()
        self.fill_count = 0
        self.sell_orders_created = 0
        
        while time.time() - start_time < 60:
            await asyncio.sleep(1)
            
            # Show periodic status
            elapsed = int(time.time() - start_time)
            if elapsed % 10 == 0:
                remaining = 60 - elapsed
                logger.info(f"Monitoring... {remaining}s remaining "
                          f"(Fills: {self.fill_count}, Sells created: {self.sell_orders_created})")
            
            # Check for unmatched fills periodically
            if elapsed % 15 == 0:
                unmatched_count = await self.pairing_manager.process_unmatched_fills()
                if unmatched_count > 0:
                    logger.info(f"✓ Processed {unmatched_count} unmatched fills")
    
    def _register_monitoring_handlers(self):
        """Register handlers to monitor order activity"""
        
        def fill_handler(fill: OrderFill):
            """Handle order fills"""
            self.fill_count += 1
            logger.info(f"\n[FILL DETECTED] {fill.side.value.upper()} order filled:")
            logger.info(f"  Order ID: {fill.order_id}")
            logger.info(f"  Amount: {fill.amount} @ {fill.price}")
            logger.info(f"  Market: {fill.market}")
            
            if fill.side == OrderSide.BUY:
                logger.info("  → Automatic sell orders should be created soon...")
        
        def order_complete_handler(order):
            """Handle completed orders"""
            logger.info(f"\n[ORDER COMPLETE] {order.side.value.upper()} order {order.order_id}")
            logger.info(f"  Filled: {order.filled_amount}/{order.original_amount}")
        
        def pair_complete_handler(pair):
            """Handle completed pairs"""
            logger.info(f"\n[PAIR COMPLETE] Buy order {pair.buy_order.order_id}")
            logger.info(f"  Buy filled: {pair.buy_order.filled_amount}")
            logger.info(f"  Sell orders: {len(pair.sell_orders)}")
            logger.info(f"  Total sell amount: {pair.total_sell_amount}")
        
        # Track sell order creation
        original_place_sell = self.order_manager.place_sell_order
        
        def tracked_place_sell(*args, **kwargs):
            result = original_place_sell(*args, **kwargs)
            if result:
                self.sell_orders_created += 1
                logger.info(f"\n[SELL ORDER CREATED] Order ID: {result.order_id}")
                logger.info(f"  Amount: {kwargs.get('amount')} @ {kwargs.get('price')}")
            return result
        
        self.order_manager.place_sell_order = tracked_place_sell
        
        # Register handlers
        self.order_tracker.add_fill_handler(fill_handler)
        self.order_tracker.add_order_complete_handler(order_complete_handler)
        self.order_tracker.add_pair_complete_handler(pair_complete_handler)
    
    def _show_pairing_statistics(self):
        """Show final pairing statistics"""
        logger.info("\n" + "=" * 60)
        logger.info("Pairing System Statistics")
        logger.info("=" * 60)
        
        # Get statistics
        pairing_stats = self.pairing_manager.get_pairing_statistics()
        tracker_stats = self.order_tracker.get_statistics()
        
        logger.info("\nOrder Tracking:")
        logger.info(f"  Total orders tracked: {tracker_stats['total_orders']}")
        logger.info(f"  Buy orders: {tracker_stats['buy_orders']}")
        logger.info(f"  Sell orders: {tracker_stats['sell_orders']}")
        logger.info(f"  Filled orders: {tracker_stats['filled_orders']}")
        
        logger.info("\nPairing Results:")
        logger.info(f"  Order pairs: {tracker_stats['total_pairs']}")
        logger.info(f"  Complete pairs: {tracker_stats['complete_pairs']}")
        logger.info(f"  Unmatched pairs: {tracker_stats['unmatched_pairs']}")
        logger.info(f"  Pairs created: {pairing_stats['total_pairs_created']}")
        logger.info(f"  Sell orders placed: {pairing_stats['total_sell_orders_placed']}")
        logger.info(f"  Failed pairings: {pairing_stats['failed_pairings']}")
        logger.info(f"  Success rate: {pairing_stats['success_rate']:.1%}")
        
        logger.info("\nTest Results:")
        logger.info(f"  Fills detected: {self.fill_count}")
        logger.info(f"  Sell orders created: {self.sell_orders_created}")
        
        if self.fill_count > 0 and self.sell_orders_created > 0:
            logger.info("\n✓ Order pairing system is working correctly!")
        elif self.fill_count > 0:
            logger.info("\n⚠ Fills detected but no sell orders created - check pairing rules")
        else:
            logger.info("\n⚠ No fills detected - place a buy order to test the system")
        
        logger.info("=" * 60)
    
    async def _cleanup(self):
        """Clean up resources"""
        if self.ws_client:
            await self.ws_client.disconnect()
        if self.rest_client:
            self.rest_client.close()


async def main():
    """Run order pairing test"""
    import sys
    
    # Get market from command line if provided
    market = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT"
    
    tester = OrderPairingTester()
    await tester.test_order_pairing(market)


if __name__ == "__main__":
    asyncio.run(main())