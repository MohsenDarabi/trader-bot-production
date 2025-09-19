"""
Order Pairing Manager
Automatically manages buy-sell order pairing to prevent unintended short positions
"""
import time
import asyncio
from typing import Dict, List
from dataclasses import dataclass
from datetime import datetime

from src.exchange.order_tracker import OrderTracker, OrderFill, TrackedOrder, OrderSide
from src.exchange.order_manager import OrderManager, OrderStatus
from src.exchange.coinex_client import CoinExClient
from src.utils.logger import get_logger


logger = get_logger(__name__)


@dataclass
class PairingRule:
    """Configuration for buy-sell pairing"""
    market: str
    sell_price_levels: List[float]  # Multiple sell prices for the same buy
    min_fill_amount: float = 0.001  # Minimum fill amount to create sell order


class OrderPairingManager:
    """
    Manages automatic buy-sell order pairing to prevent short positions
    """
    
    def __init__(self, order_tracker: OrderTracker, order_manager: OrderManager,
                 rest_client: CoinExClient):
        """
        Initialize order pairing manager
        
        Args:
            order_tracker: Order tracker for monitoring fills
            order_manager: Order manager for placing orders
            rest_client: REST client for API calls
        """
        self.order_tracker = order_tracker
        self.order_manager = order_manager
        self.rest_client = rest_client
        
        # Pairing configuration
        self.pairing_rules: Dict[str, PairingRule] = {}
        self.auto_pairing_enabled = True
        
        # Statistics
        self.total_pairs_created = 0
        self.total_sell_orders_placed = 0
        self.failed_pairings = 0
        
        # Register event handlers
        self._register_handlers()
    
    def _register_handlers(self) -> None:
        """Register handlers for order tracker events"""
        self.order_tracker.add_fill_handler(self._handle_order_fill)
        self.order_tracker.add_order_complete_handler(self._handle_order_complete)
    
    def configure_pairing_rule(self, market: str, sell_price_levels: List[float],
                              min_fill_amount: float = 0.001) -> None:
        """
        Configure pairing rule for a market
        
        Args:
            market: Market symbol
            sell_price_levels: List of sell price levels relative to buy price
            min_fill_amount: Minimum fill amount to trigger sell order
        """
        self.pairing_rules[market] = PairingRule(
            market=market,
            sell_price_levels=sell_price_levels,
            min_fill_amount=min_fill_amount
        )
        logger.info(f"Configured pairing rule for {market}: {len(sell_price_levels)} sell levels")
    
    def enable_auto_pairing(self) -> None:
        """Enable automatic buy-sell pairing"""
        self.auto_pairing_enabled = True
        logger.info("Automatic order pairing enabled")
    
    def disable_auto_pairing(self) -> None:
        """Disable automatic buy-sell pairing"""
        self.auto_pairing_enabled = False
        logger.info("Automatic order pairing disabled")
    
    def _handle_order_fill(self, fill: OrderFill) -> None:
        """
        Handle order fill event
        
        Args:
            fill: Order fill information
        """
        try:
            logger.info(f"🔄 WebSocket fill received: {fill.side} {fill.amount} {fill.market} @ ${fill.price:.4f} (order_id: {fill.order_id})")
            
            # Log all fills for debugging purposes
            if fill.side == OrderSide.SELL:
                logger.info(f"📤 Sell fill detected (will be skipped for pairing): {fill.amount} {fill.market}")
                return
            elif fill.side != OrderSide.BUY:
                logger.warning(f"⚠️ Unknown fill side: {fill.side} - skipping")
                return
                
            logger.info(f"✅ Processing buy fill for automatic pairing: {fill.amount} {fill.market}")
                
            if not self.auto_pairing_enabled:
                logger.warning("Auto pairing disabled - skipping fill processing")
                return
            
            # Check if we have pairing rules for this market
            if fill.market not in self.pairing_rules:
                logger.error(f"❌ PAIRING FAILED: No pairing rule configured for market {fill.market}")
                logger.error(f"   Available pairing rules: {list(self.pairing_rules.keys())}")
                logger.error("   This buy fill will NOT create corresponding sell orders!")
                return
            
            # Check if fill meets minimum amount threshold
            rule = self.pairing_rules[fill.market]
            logger.info(f"🔍 Checking pairing rule: min_amount={rule.min_fill_amount}, sell_levels={len(rule.sell_price_levels)}")
            
            if fill.amount < rule.min_fill_amount:
                logger.warning(f"❌ Fill amount {fill.amount} below minimum {rule.min_fill_amount} - skipping pairing")
                return
                
            if not rule.sell_price_levels:
                logger.error(f"❌ PAIRING FAILED: Empty sell_price_levels for {fill.market}")
                logger.error("   This buy fill will NOT create corresponding sell orders!")
                return
            
            logger.info(f"✅ Buy fill qualifies for pairing - creating sell orders with rule: {len(rule.sell_price_levels)} price levels")
            logger.info(f"   Sell price levels: {rule.sell_price_levels}")
            # Create sell orders for this buy fill
            asyncio.create_task(self._create_sell_orders_for_fill(fill, rule))
            
        except Exception as e:
            logger.error(f"Error handling order fill: {e}")
            self.failed_pairings += 1
    
    def _handle_order_complete(self, order: TrackedOrder) -> None:
        """
        Handle order completion event
        
        Args:
            order: Completed order
        """
        if order.side == OrderSide.BUY:
            logger.info(f"Buy order {order.order_id} completed with {order.filled_amount} filled")
            # The fills will be handled individually by _handle_order_fill
        elif order.side == OrderSide.SELL:
            logger.info(f"Sell order {order.order_id} completed")
    
    async def _create_sell_orders_for_fill(self, fill: OrderFill, rule: PairingRule) -> None:
        """
        Create sell orders for a buy order fill
        
        Args:
            fill: Buy order fill
            rule: Pairing rule for the market
        """
        try:
            logger.info(f"Creating sell orders for buy fill: {fill.amount} {fill.market} @ {fill.price}")
            
            # Calculate sell order amount per price level
            # Distribute the fill amount across all sell price levels
            amount_per_level = fill.amount / len(rule.sell_price_levels)
            
            sell_orders_created = []
            
            for sell_price_level in rule.sell_price_levels:
                # FIXED: Always treat as absolute price since we only pass strategy sell prices
                # The arbitrary 2.0 threshold was causing strategy prices to be misinterpreted 
                # as multipliers for lower-priced assets like ADA
                sell_price = sell_price_level
                logger.info(f"Using absolute strategy price: ${sell_price:.8f}")
                
                logger.info(f"Sell price calculation: fill=${fill.price:.2f}, level={sell_price_level}, result=${sell_price:.2f}")
                
                try:
                    # Place sell order
                    sell_order = self.order_manager.place_sell_order(
                        market=fill.market,
                        amount=amount_per_level,
                        price=sell_price,
                        position_size=amount_per_level * sell_price,
                        is_hide=True
                    )
                    
                    if sell_order:
                        # Track the sell order
                        self.order_tracker.track_order(
                            order_id=str(sell_order.exchange_order_id),
                            client_id=sell_order.client_id,
                            market=fill.market,
                            side=OrderSide.SELL,
                            amount=amount_per_level,
                            price=sell_price
                        )
                        
                        # Link sell order to buy order
                        self.order_tracker.link_sell_order_to_buy(
                            sell_order_id=str(sell_order.exchange_order_id),
                            buy_order_id=fill.order_id
                        )
                        
                        sell_orders_created.append(sell_order)
                        self.total_sell_orders_placed += 1
                        
                        logger.info(f"Created sell order: {amount_per_level} {fill.market} @ {sell_price}")
                    
                except Exception as e:
                    logger.error(f"Failed to create sell order at price {sell_price}: {e}")
                    self.failed_pairings += 1
            
            if sell_orders_created:
                self.total_pairs_created += 1
                logger.info(f"Successfully created {len(sell_orders_created)} sell orders for buy fill")
            
        except Exception as e:
            logger.error(f"Error creating sell orders for fill: {e}")
            self.failed_pairings += 1
    
    async def process_unmatched_fills(self) -> int:
        """
        Process any unmatched buy fills that need sell orders
        
        Returns:
            Number of fills processed
        """
        try:
            unmatched_pairs = self.order_tracker.get_unmatched_buy_fills()
            processed = 0
            
            for pair in unmatched_pairs:
                buy_order = pair.buy_order
                
                # Check if we have pairing rules for this market
                if buy_order.market not in self.pairing_rules:
                    continue
                
                rule = self.pairing_rules[buy_order.market]
                
                # Calculate remaining amount that needs sell orders
                remaining_amount = pair.get_remaining_buy_amount()
                if remaining_amount < rule.min_fill_amount:
                    continue
                
                # Create a synthetic fill for the remaining amount
                # Use the average price of the buy order's fills
                if buy_order.fills:
                    avg_price = sum(f.price * f.amount for f in buy_order.fills) / buy_order.filled_amount
                    
                    synthetic_fill = OrderFill(
                        fill_id=f"synthetic_{buy_order.order_id}_{int(time.time())}",
                        order_id=buy_order.order_id,
                        market=buy_order.market,
                        side=OrderSide.BUY,
                        amount=remaining_amount,
                        price=avg_price,
                        timestamp=datetime.now()
                    )
                    
                    await self._create_sell_orders_for_fill(synthetic_fill, rule)
                    processed += 1
            
            if processed > 0:
                logger.info(f"Processed {processed} unmatched buy fills")
            
            return processed
            
        except Exception as e:
            logger.error(f"Error processing unmatched fills: {e}")
            return 0
    
    async def verify_position_balance(self, market: str) -> Dict[str, any]:
        """
        Verify that buy and sell orders are balanced for a market.
        Forces a sync to ensure data is fresh, especially after a restart.
        """
        try:
            # Force a sync of the order tracker before performing the check
            # to ensure we have the latest data, preventing a race condition after restarts.
            logger.info(f"Syncing order tracker for {market} before balance verification...")
            await self.order_tracker.sync_existing_orders(market)

            # Get current position from exchange
            positions_response = self.rest_client.get_positions(market=market)
            positions = positions_response.get("data", [])
            
            current_position = 0.0
            if positions:
                # Import safe_float from safe_conversions module
                from src.utils.safe_conversions import safe_float
                current_position = safe_float(positions[0].get("open_interest", 0))
            
            # Calculate total buy and sell amounts from the now-synced tracked orders
            total_buy_filled = 0.0
            total_sell_filled = 0.0
            
            for order in self.order_tracker.tracked_orders.values():
                if order.market == market:
                    if order.side == OrderSide.BUY:
                        total_buy_filled += order.filled_amount
                    elif order.side == OrderSide.SELL:
                        total_sell_filled += order.filled_amount
            
            # Calculate expected position
            expected_position = total_buy_filled - total_sell_filled
            position_difference = current_position - expected_position
            
            balance_info = {
                "market": market,
                "current_position": current_position,
                "total_buy_filled": total_buy_filled,
                "total_sell_filled": total_sell_filled,
                "expected_position": expected_position,
                "position_difference": position_difference,
                "is_balanced": abs(position_difference) < 0.001
            }
            
            if not balance_info["is_balanced"]:
                logger.warning(f"Position imbalance detected for {market}: {position_difference}")
            
            return balance_info
            
        except Exception as e:
            logger.error(f"Error verifying position balance for {market}: {e}")
            return {"error": str(e)}
    
    def get_pairing_statistics(self) -> Dict[str, any]:
        """Get pairing statistics"""
        unmatched_count = len(self.order_tracker.get_unmatched_buy_fills())
        
        return {
            "auto_pairing_enabled": self.auto_pairing_enabled,
            "total_pairs_created": self.total_pairs_created,
            "total_sell_orders_placed": self.total_sell_orders_placed,
            "failed_pairings": self.failed_pairings,
            "unmatched_buy_fills": unmatched_count,
            "configured_markets": list(self.pairing_rules.keys()),
            "success_rate": (
                self.total_pairs_created / (self.total_pairs_created + self.failed_pairings)
                if (self.total_pairs_created + self.failed_pairings) > 0 else 0.0
            )
        }
    
    async def emergency_balance_check(self) -> List[str]:
        """
        Emergency check for position imbalances across all markets
        
        Returns:
            List of markets with imbalances
        """
        imbalanced_markets = []
        
        try:
            # Get all markets we're tracking
            tracked_markets = set()
            for order in self.order_tracker.tracked_orders.values():
                tracked_markets.add(order.market)
            
            # Check balance for each market
            for market in tracked_markets:
                balance_info = await self.verify_position_balance(market)
                if not balance_info.get("is_balanced", True):
                    imbalanced_markets.append(market)
                    logger.error(f"EMERGENCY: Position imbalance in {market}: {balance_info}")
            
            return imbalanced_markets
            
        except Exception as e:
            logger.error(f"Error in emergency balance check: {e}")
            return []
    
    async def check_and_handle_stale_sell_orders(self, max_sell_age_hours: float = 24.0) -> int:
        """
        Check for stale sell orders and handle them appropriately
        
        Args:
            max_sell_age_hours: Maximum age in hours for a sell order before considering it stale
            
        Returns:
            Number of stale sell orders found
        """
        stale_count = 0
        
        try:
            current_time = datetime.now()
            
            # Check all order pairs
            for pair in self.order_tracker.order_pairs.values():
                for sell_order in pair.sell_orders:
                    # Skip if order is already filled or cancelled
                    if sell_order.status in [OrderStatus.FILLED, OrderStatus.CANCELLED]:
                        continue
                    
                    # Calculate order age
                    order_age_hours = (current_time - sell_order.created_at).total_seconds() / 3600
                    
                    # Check if order is stale
                    if order_age_hours > max_sell_age_hours:
                        stale_count += 1
                        
                        # Log stale order information
                        logger.warning(
                            f"Stale sell order detected: {sell_order.order_id} "
                            f"({order_age_hours:.1f} hours old, "
                            f"filled: {sell_order.filled_amount}/{sell_order.original_amount})"
                        )
                        
                        # Log additional context
                        if sell_order.status == OrderStatus.PARTIAL:
                            fill_percentage = (sell_order.filled_amount / sell_order.original_amount) * 100
                            logger.info(
                                f"Stale sell order is {fill_percentage:.1f}% filled. "
                                f"Market: {sell_order.market}, Price: {sell_order.price}"
                            )
            
            if stale_count > 0:
                logger.warning(f"Found {stale_count} stale sell orders")
            
            return stale_count
            
        except Exception as e:
            logger.error(f"Error checking stale sell orders: {e}")
            return 0
