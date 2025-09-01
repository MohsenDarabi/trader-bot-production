"""
Order Management System with client_id duplicate prevention
"""
import time
import signal
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Any
from dataclasses import dataclass
from enum import Enum

from src.exchange.coinex_client import CoinExClient
from src.exchange.order_tracker import OrderSide
from src.core.profitability import ProfitabilityValidator
from src.utils.logger import get_logger
from src.utils.safe_conversions import safe_float
from config.settings import ENABLE_REST_API_STATUS_CHECKS, ORDER_STATUS_CHECK_INTERVAL
from src.utils.settlement_handler import settlement_retry


logger = get_logger(__name__)


class TimeoutError(Exception):
    """Custom timeout error"""
    pass


def timeout_handler(signum, frame):
    """Signal handler for timeout"""
    raise TimeoutError("Operation timed out")


class OrderStatus(Enum):
    """Order status enumeration"""
    PENDING = "pending"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass
class Order:
    """Order data structure"""
    client_id: str
    market: str
    side: OrderSide
    order_type: str
    amount: float
    price: float
    status: OrderStatus
    exchange_order_id: Optional[int] = None
    filled_amount: float = 0.0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    
    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now(timezone.utc)
        if self.updated_at is None:
            self.updated_at = self.created_at
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for storage"""
        return {
            'client_id': self.client_id,
            'market': self.market,
            'side': self.side.value,
            'order_type': self.order_type,
            'amount': self.amount,
            'price': self.price,
            'status': self.status.value,
            'exchange_order_id': self.exchange_order_id,
            'filled_amount': self.filled_amount,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat()
        }


class OrderManager:
    """Manages order placement and tracking with duplicate prevention"""
    
    def __init__(self, client: CoinExClient, 
                 profitability_validator: ProfitabilityValidator):
        """
        Initialize order manager
        
        Args:
            client: CoinEx API client
            profitability_validator: Profitability validation system
        """
        self.client = client
        self.validator = profitability_validator
        self.active_orders: Dict[str, Order] = {}
        self.used_client_ids: Set[str] = set()
        self._last_status_check: Dict[str, datetime] = {}  # Track last status check per order
        
        # Coordination with order tracking system for unified pairing
        self.order_tracker = None
        self.pairing_manager = None
    
    def set_tracking_coordination(self, order_tracker, pairing_manager, trading_bot=None):
        """Set references to order tracker, pairing manager, and trading bot for unified coordination"""
        self.order_tracker = order_tracker
        self.pairing_manager = pairing_manager
        self._trading_bot_ref = trading_bot  # Reference for over-selling protection
        logger.info("✅ OrderManager coordination with tracking system established")
        if trading_bot:
            logger.info("✅ Trading bot reference established for over-selling protection")
    
    def generate_client_id(self, market: str, side: OrderSide, 
                          timestamp: Optional[int] = None) -> str:
        """
        Generate unique client_id for order tracking
        
        Format: DRA_{timestamp}_{side}_{market}
        
        Args:
            market: Market symbol
            side: Order side
            timestamp: Optional timestamp, uses current if not provided
            
        Returns:
            Unique client_id
        """
        if timestamp is None:
            timestamp = int(time.time())
        
        base_id = f"DRA_{timestamp}_{side.value}_{market}"
        
        # Ensure uniqueness by adding suffix if needed
        counter = 0
        client_id = base_id
        
        while client_id in self.used_client_ids:
            counter += 1
            client_id = f"{base_id}_{counter}"
        
        self.used_client_ids.add(client_id)
        return client_id
    
    @settlement_retry
    def place_buy_order(self, market: str, amount: float, price: float,
                       position_size: float, is_hide: bool = True) -> Optional[Order]:
        """
        Place a buy order with profitability validation
        
        Args:
            market: Market symbol
            amount: Order amount in base currency
            price: Limit price
            position_size: Position size for validation
            is_hide: Whether to hide order from public order book
            
        Returns:
            Order object if successful, None otherwise
        """
        # Safety checks are handled by the trading bot's can_place_buy_order() method
        # Order manager focuses on order execution, not trading logic decisions
        
        # Generate client_id
        client_id = self.generate_client_id(market, OrderSide.BUY)
        
        logger.info(f"Placing buy order: {market} {amount} @ ${price:.2f} [{client_id}]")
        
        try:
            # Place order on exchange with detailed logging and timeout protection
            logger.debug(f"About to call client.place_order() with params: market={market}, side=buy, amount={amount}, price={price}, client_id={client_id}, is_hide={is_hide}")
            
            # Set up timeout protection (45 seconds)
            signal.signal(signal.SIGALRM, timeout_handler)
            signal.alarm(45)
            
            try:
                # Format order parameters with proper precision
                formatted_amount, formatted_price = self.client._format_order_params(market, amount, price)
                
                response = self.client.place_order(
                    market=market,
                    side='buy',
                    amount=formatted_amount,
                    order_type='limit',
                    price=formatted_price,
                    client_id=client_id,
                    is_hide=is_hide
                )
                
                # Clear the alarm
                signal.alarm(0)
                logger.debug(f"Received response from place_order: {response}")
                
            except TimeoutError:
                logger.error("Order placement timed out after 45 seconds")
                signal.alarm(0)  # Clear the alarm
                raise Exception("Order placement timed out - possible API or network issue")
            
            # Extract comprehensive order data from exchange response
            exchange_amount = safe_float(response.get('amount', amount))
            exchange_filled = safe_float(response.get('filled_amount', 0))
            exchange_unfilled = safe_float(response.get('unfilled_amount', amount))
            
            # Determine order status based on fill state
            if exchange_filled > 0 and exchange_filled >= exchange_amount:
                order_status = OrderStatus.FILLED
            elif exchange_filled > 0:
                order_status = OrderStatus.PARTIALLY_FILLED
            else:
                order_status = OrderStatus.PENDING
            
            # Log exchange vs intended amounts for validation
            if abs(exchange_amount - amount) > 0.000001:
                logger.warning(f"⚠️ Exchange modified order amount: intended={amount}, actual={exchange_amount}")
            
            # Create order object with exchange-confirmed data
            order = Order(
                client_id=client_id,
                market=market,
                side=OrderSide.BUY,
                order_type='limit',
                amount=exchange_amount,  # Use exchange-confirmed amount
                price=price,
                status=order_status,     # Use calculated status
                exchange_order_id=response.get('order_id'),
                filled_amount=exchange_filled  # Use exchange-confirmed fills
            )
            
            # Log comprehensive order state
            if exchange_filled > 0:
                logger.info(f"✅ Buy order placed with immediate fill: {client_id} - {exchange_filled}/{exchange_amount} filled")
            else:
                logger.info(f"✅ Buy order placed and pending: {client_id} - {exchange_unfilled} unfilled")
            
            # CRITICAL: Validate order was actually created on exchange
            if not order.exchange_order_id:
                logger.error(f"🚨 Order creation failed - no exchange order ID returned for {client_id}")
                self.used_client_ids.discard(client_id)
                return None
            
            # Store in active orders
            self.active_orders[client_id] = order
            
            # UNIFIED IMMEDIATE PAIRING: Coordinate with tracking system
            filled_amount = exchange_filled
            unfilled_amount = exchange_unfilled
            last_filled_price = safe_float(response.get('last_filled_price', price))
            
            # Always track the buy order first
            if self.order_tracker:
                self.order_tracker.track_order(
                    order_id=str(order.exchange_order_id),
                    client_id=client_id,
                    market=market,
                    side=OrderSide.BUY,
                    amount=exchange_amount,
                    price=price
                )
                logger.info(f"🔗 Buy order tracked: {client_id} -> {order.exchange_order_id}")
            
            # Handle immediate fills with unified coordination
            if filled_amount > 0:
                logger.info(f"🔄 Immediate fill detected: {filled_amount} {market} @ {last_filled_price} - using unified pairing")
                
                # Use the pairing manager's configured sell price if available
                sell_price = price * 1.015  # Default 1.5% profit fallback
                strategy_price_used = False
                
                if self.pairing_manager and market in self.pairing_manager.pairing_rules:
                    rule = self.pairing_manager.pairing_rules[market]
                    if rule.sell_price_levels:
                        # Use the strategy-configured absolute sell price
                        sell_price = rule.sell_price_levels[0]
                        strategy_price_used = True
                        logger.info(f"📊 Using strategy sell price: ${sell_price:.2f} for {market}")
                    else:
                        logger.warning(f"⚠️ Empty pairing rule for {market} - using fallback price: ${sell_price:.2f}")
                else:
                    logger.warning(f"⚠️ No pairing rule for {market} - using fallback price: ${sell_price:.2f}")
                
                # Log the price calculation method for debugging
                price_method = "strategy absolute price" if strategy_price_used else f"fallback (buy price * 1.015)"
                logger.info(f"🔢 Sell price calculation: {price_method} = ${sell_price:.2f}")
                
                try:
                    # Place coordinated immediate paired sell order
                    paired_sell = self.place_sell_order(
                        market=market,
                        amount=filled_amount,
                        price=sell_price,
                        position_size=filled_amount * sell_price,
                        is_hide=True,
                        is_paired=True  # Mark as immediate pair
                    )
                    
                    if paired_sell and self.order_tracker:
                        # Track the paired sell order and link it to the buy order
                        self.order_tracker.track_order(
                            order_id=str(paired_sell.exchange_order_id),
                            client_id=paired_sell.client_id,
                            market=market,
                            side=OrderSide.SELL,
                            amount=filled_amount,
                            price=sell_price
                        )
                        
                        # Link sell order to buy order for coordinated tracking
                        self.order_tracker.link_sell_order_to_buy(
                            sell_order_id=str(paired_sell.exchange_order_id),
                            buy_order_id=str(order.exchange_order_id)
                        )
                        
                        logger.info(f"✅ Unified pairing completed: {filled_amount} @ {last_filled_price:.2f} → sell @ {sell_price:.2f}")
                        logger.info(f"🔗 Paired orders linked: buy {order.exchange_order_id} ↔ sell {paired_sell.exchange_order_id}")
                    else:
                        logger.error(f"❌ Failed to place or track immediate paired sell for {filled_amount} {market}")
                        
                except Exception as e:
                    logger.error(f"❌ Exception during unified immediate pairing for {filled_amount} {market}: {e}")
            
            # Handle unfilled portion - will be managed by WebSocket events
            if unfilled_amount > 0:
                logger.info(f"📊 Unfilled portion {unfilled_amount} {market} will be handled by WebSocket pairing")
            
            # Enhanced validation: verify order exists on exchange with retry logic
            validation_success = False
            max_retries = 3
            base_delay = 0.5  # Increased from 0.1s to 0.5s for better exchange processing
            
            for attempt in range(max_retries):
                try:
                    # Progressive delay for exchange processing
                    import time
                    delay = base_delay * (2 ** attempt)  # Exponential backoff: 0.5s, 1s, 2s
                    time.sleep(delay)
                    
                    logger.debug(f"📋 Order validation attempt {attempt + 1}/{max_retries} for {client_id} (delay: {delay}s)")
                    
                    # Check if order actually exists on exchange
                    validation_response = self.client.get_order_status(
                        market=market,
                        order_id=order.exchange_order_id
                    )
                    
                    if validation_response:
                        logger.info(f"✅ Order validation successful: {client_id} -> {order.exchange_order_id} (attempt {attempt + 1})")
                        validation_success = True
                        break
                    else:
                        logger.warning(f"⚠️ Order validation attempt {attempt + 1} failed - order {client_id} not found on exchange")
                        
                except Exception as validation_error:
                    logger.warning(f"⚠️ Order validation attempt {attempt + 1} error for {client_id}: {validation_error}")
                    
                    # Handle specific error types
                    if "timeout" in str(validation_error).lower() or "network" in str(validation_error).lower():
                        logger.debug(f"🔄 Network/timeout error - will retry validation for {client_id}")
                        continue
                    elif attempt == max_retries - 1:  # Last attempt
                        logger.error(f"🚨 All validation attempts failed for {client_id}")
                        break
            
            # Handle validation failure
            if not validation_success:
                logger.error(f"🚨 Order validation completely failed - order {client_id} could not be verified on exchange")
                logger.error(f"🔄 Removing unverified order from tracking - WebSocket events will detect if order actually exists")
                
                # Remove from tracking since we can't verify it exists
                del self.active_orders[client_id]
                self.used_client_ids.discard(client_id)
                return None
            
            logger.info(f"Buy order placed successfully: {client_id} -> {order.exchange_order_id}")
            return order
            
        except Exception as e:
            # Handle funding fee settlement period gracefully
            if "3007" in str(e) or "funding fee settlement" in str(e).lower():
                logger.warning(f"Order placement delayed due to funding fee settlement: {client_id}")
                logger.info("This is a temporary exchange restriction - will retry in next cycle")
                # Keep client_id reserved for retry
                return None
            else:
                logger.error(f"Failed to place buy order {client_id}: {e}")
                # Remove from used IDs since order failed
                self.used_client_ids.discard(client_id)
                return None
    
    @settlement_retry
    def place_sell_order(self, market: str, amount: float, price: float,
                        position_size: float, is_hide: bool = True, is_orphaned: bool = False, is_paired: bool = False) -> Optional[Order]:
        """
        Place a sell order with profitability validation
        
        Args:
            market: Market symbol
            amount: Order amount in base currency
            price: Limit price
            position_size: Position size for validation
            is_hide: Whether to hide order from public order book
            is_orphaned: Whether this is an orphaned position sell order
            is_paired: Whether this is an immediate paired sell order from buy fill
            
        Returns:
            Order object if successful, None otherwise
        """
        # Profitability validation is handled by the trading bot before calling this method
        
        # CRITICAL: Over-selling protection - validate position balance before placing sell order
        if hasattr(self, '_trading_bot_ref') and self._trading_bot_ref:
            try:
                # SYNC FIX: Ensure position data is fresh before over-selling check
                # This prevents the timing issue where immediate fills create positions
                # but the over-selling check still sees stale data (position_size = 0)
                logger.debug(f"🔄 Syncing position data for {market} before over-selling check...")
                synced_count = self._trading_bot_ref.position_manager.sync_with_exchange(market)
                logger.debug(f"✅ Position sync completed: {synced_count} positions updated")
                
                balance = self._trading_bot_ref._calculate_position_sell_balance(market)
                position_size_actual = balance['position_size']
                total_sells_current = balance['total_sells']
                
                # Check if adding this sell order would exceed position size
                total_sells_after = total_sells_current + amount
                if total_sells_after > position_size_actual + 0.000001:  # Small tolerance for rounding
                    logger.error(f"🚨 OVER-SELLING PREVENTED: Sell order would exceed position size!")
                    logger.error(f"   Position: {position_size_actual:.6f} {market}")
                    logger.error(f"   Current sells: {total_sells_current:.6f}")  
                    logger.error(f"   Requested sell: {amount:.6f}")
                    logger.error(f"   Would total: {total_sells_after:.6f} (EXCEEDS POSITION)")
                    
                    # Adjust amount to fit within position limits
                    max_allowed = max(0, position_size_actual - total_sells_current)
                    if max_allowed >= 0.000001:  # Minimum viable amount
                        logger.warning(f"🔧 Adjusting sell amount from {amount:.6f} to {max_allowed:.6f} to prevent over-selling")
                        amount = max_allowed
                    else:
                        logger.error(f"❌ Cannot place sell order - position fully covered or no position exists")
                        return None
                        
                logger.info(f"✅ Over-selling check passed: {amount:.6f} sell + {total_sells_current:.6f} existing = {amount + total_sells_current:.6f} <= {position_size_actual:.6f} position")
            except Exception as e:
                logger.warning(f"⚠️ Could not perform over-selling check: {e} - proceeding with order placement")
        
        # Generate client_id with orphaned marker if needed
        if is_orphaned:
            # Use special marker for orphaned position sells
            import time
            timestamp = int(time.time() * 1000)
            client_id = f"DRA_{timestamp}_OS_{market}"
        else:
            client_id = self.generate_client_id(market, OrderSide.SELL)
        
        # Enhanced logging based on sell order type
        if is_paired:
            logger.info(f"🔗 Placing immediate paired sell order: {market} {amount} @ ${price:.2f} [{client_id}]")
        elif is_orphaned:
            logger.info(f"📍 Placing orphaned position sell order: {market} {amount} @ ${price:.2f} [{client_id}]")
        else:
            logger.info(f"📤 Placing regular sell order: {market} {amount} @ ${price:.2f} [{client_id}]")
        
        try:
            # Format order parameters with proper precision
            formatted_amount, formatted_price = self.client._format_order_params(market, amount, price)
            
            # Place order on exchange
            response = self.client.place_order(
                market=market,
                side='sell',
                amount=formatted_amount,
                order_type='limit',
                price=formatted_price,
                client_id=client_id,
                is_hide=is_hide
            )
            
            # Extract comprehensive order data from exchange response
            exchange_amount = safe_float(response.get('amount', amount))
            exchange_filled = safe_float(response.get('filled_amount', 0))
            exchange_unfilled = safe_float(response.get('unfilled_amount', amount))
            
            # Determine order status based on fill state
            if exchange_filled > 0 and exchange_filled >= exchange_amount:
                order_status = OrderStatus.FILLED
            elif exchange_filled > 0:
                order_status = OrderStatus.PARTIALLY_FILLED
            else:
                order_status = OrderStatus.PENDING
            
            # Log exchange vs intended amounts for validation
            if abs(exchange_amount - amount) > 0.000001:
                logger.warning(f"⚠️ Exchange modified sell order amount: intended={amount}, actual={exchange_amount}")
            
            # Create order object with exchange-confirmed data
            order = Order(
                client_id=client_id,
                market=market,
                side=OrderSide.SELL,
                order_type='limit',
                amount=exchange_amount,  # Use exchange-confirmed amount
                price=price,
                status=order_status,     # Use calculated status
                exchange_order_id=response.get('order_id'),
                filled_amount=exchange_filled  # Use exchange-confirmed fills
            )
            
            # Log comprehensive order state
            if exchange_filled > 0:
                logger.info(f"✅ Sell order placed with immediate fill: {client_id} - {exchange_filled}/{exchange_amount} filled")
            else:
                logger.info(f"✅ Sell order placed and pending: {client_id} - {exchange_unfilled} unfilled")
            
            # Special logging for orphaned position sells
            if is_orphaned:
                logger.info(f"📍 Orphaned position sell order successfully placed: {client_id} for {exchange_amount} {market}")
            
            # CRITICAL: Validate order was actually created on exchange
            if not order.exchange_order_id:
                logger.error(f"🚨 Sell order creation failed - no exchange order ID returned for {client_id}")
                self.used_client_ids.discard(client_id)
                return None
            
            # Store in active orders
            self.active_orders[client_id] = order
            
            # Enhanced validation: verify order exists on exchange with retry logic
            validation_success = False
            max_retries = 3
            base_delay = 0.5  # Increased from 0.1s to 0.5s for better exchange processing
            
            for attempt in range(max_retries):
                try:
                    # Progressive delay for exchange processing
                    import time
                    delay = base_delay * (2 ** attempt)  # Exponential backoff: 0.5s, 1s, 2s
                    time.sleep(delay)
                    
                    logger.debug(f"📋 Sell order validation attempt {attempt + 1}/{max_retries} for {client_id} (delay: {delay}s)")
                    
                    # Check if order actually exists on exchange
                    validation_response = self.client.get_order_status(
                        market=market,
                        order_id=order.exchange_order_id
                    )
                    
                    if validation_response:
                        logger.info(f"✅ Sell order validation successful: {client_id} -> {order.exchange_order_id} (attempt {attempt + 1})")
                        validation_success = True
                        break
                    else:
                        logger.warning(f"⚠️ Sell order validation attempt {attempt + 1} failed - order {client_id} not found on exchange")
                        
                except Exception as validation_error:
                    logger.warning(f"⚠️ Sell order validation attempt {attempt + 1} error for {client_id}: {validation_error}")
                    
                    # Handle specific error types
                    if "timeout" in str(validation_error).lower() or "network" in str(validation_error).lower():
                        logger.debug(f"🔄 Network/timeout error - will retry validation for {client_id}")
                        continue
                    elif attempt == max_retries - 1:  # Last attempt
                        logger.error(f"🚨 All sell order validation attempts failed for {client_id}")
                        break
            
            # Handle validation failure
            if not validation_success:
                logger.error(f"🚨 Sell order validation completely failed - order {client_id} could not be verified on exchange")
                logger.error(f"🔄 Removing unverified sell order from tracking - WebSocket events will detect if order actually exists")
                
                # Remove from tracking since we can't verify it exists
                del self.active_orders[client_id]
                self.used_client_ids.discard(client_id)
                return None
            
            logger.info(f"Sell order placed successfully: {client_id} -> {order.exchange_order_id}")
            return order
            
        except Exception as e:
            # Handle funding fee settlement period gracefully
            if "3007" in str(e) or "funding fee settlement" in str(e).lower():
                logger.warning(f"Sell order placement delayed due to funding fee settlement: {client_id}")
                logger.info("This is a temporary exchange restriction - will retry in next cycle")
                # Keep client_id reserved for retry
                return None
            else:
                logger.error(f"Failed to place sell order {client_id}: {e}")
                # Remove from used IDs since order failed
                self.used_client_ids.discard(client_id)
                return None
    
    @settlement_retry
    def cancel_order(self, client_id: str) -> bool:
        """
        Cancel an order by client_id
        
        Args:
            client_id: Client order ID
            
        Returns:
            True if cancelled successfully
        """
        if client_id not in self.active_orders:
            logger.warning(f"Order {client_id} not found in active orders")
            return False
        
        order = self.active_orders[client_id]
        
        try:
            # Cancel on exchange using exchange order_id (required by CoinEx API)
            if not order.exchange_order_id:
                logger.error(f"Cannot cancel order {client_id} - missing exchange_order_id")
                return False
                
            self.client.cancel_order(
                market=order.market,
                order_id=order.exchange_order_id
            )
            
            # Update status
            order.status = OrderStatus.CANCELLED
            order.updated_at = datetime.now(timezone.utc)
            
            # Remove from active orders
            del self.active_orders[client_id]
            
            logger.info(f"Order cancelled: {client_id}")
            return True
            
        except Exception as e:
            # INTELLIGENT ERROR HANDLING: Recognize and investigate "order not exists"
            if "order not exists" in str(e).lower() or "order_not_exists" in str(e).lower():
                logger.warning(f"🚨 Order {client_id} doesn't exist on exchange - investigating what happened...")
                
                # Investigate what happened to this missing order
                reconciliation_success = self._reconcile_order_state(client_id, order.market)
                
                if reconciliation_success:
                    logger.info(f"✅ Successfully reconciled missing order {client_id}")
                    return True  # Treated as successful since we handled the discrepancy
                else:
                    logger.error(f"❌ Failed to reconcile missing order {client_id}")
                    return False
                    
            # Handle other cancellation errors normally
            else:
                logger.error(f"Failed to cancel order {client_id}: {e}")
                return False
    
    def update_order_status(self, client_id: str) -> Optional[Order]:
        """
        Update order status from exchange
        
        Args:
            client_id: Client order ID
            
        Returns:
            Updated order or None if not found
        """
        if client_id not in self.active_orders:
            return None
        
        order = self.active_orders[client_id]
        
        try:
            # Query order status - prefer order_id over client_id to avoid signature issues
            if order.exchange_order_id:
                status_response = self.client.get_order_status(
                    market=order.market,
                    order_id=order.exchange_order_id
                )
            else:
                # Fallback to client_id if exchange_order_id not available
                status_response = self.client.get_order_status(
                    market=order.market,
                    client_id=client_id
                )
            
            # Update order details
            if status_response:
                old_status = order.status
                
                # Map exchange status to our enum
                exchange_status = status_response.get('status', '')
                if exchange_status == 'done':
                    order.status = OrderStatus.FILLED
                elif exchange_status == 'part_deal':
                    order.status = OrderStatus.PARTIALLY_FILLED
                elif exchange_status == 'cancel':
                    order.status = OrderStatus.CANCELLED
                
                # Update filled amount
                order.filled_amount = safe_float(status_response.get('filled_amount', 0))
                order.updated_at = datetime.now(timezone.utc)
                
                # Log status changes
                if old_status != order.status:
                    logger.info(f"Order {client_id} status: {old_status.value} -> {order.status.value}")
                
                # Remove from active if completed
                if order.status in [OrderStatus.FILLED, OrderStatus.CANCELLED]:
                    del self.active_orders[client_id]
                    # Clean up status check timestamp
                    self._last_status_check.pop(client_id, None)
            
            return order
            
        except Exception as e:
            # Handle order not exists - investigate and reconcile
            if "order not exists" in str(e).lower() or "order_not_exists" in str(e).lower():
                logger.warning(f"🚨 Order {client_id} no longer exists on exchange during status check - investigating...")
                
                # Investigate what happened to this missing order
                reconciliation_success = self._reconcile_order_state(client_id, order.market)
                
                if reconciliation_success:
                    logger.info(f"✅ Successfully reconciled missing order {client_id} during status check")
                    # Order has been handled by reconciliation - it may have been removed from active_orders
                    return self.active_orders.get(client_id)  # Return current state or None if removed
                else:
                    logger.error(f"❌ Failed to reconcile missing order {client_id} during status check")
                    return None
                
            # Handle common API signature errors gracefully
            if "Signature Incorrect" in str(e):
                logger.warning(f"Order status check failed due to signature error for {client_id}: {e}")
                
                # Try alternative approach if we used order_id initially
                if order.exchange_order_id and "order_id" not in str(e):
                    try:
                        logger.info(f"Retrying with client_id instead of order_id for {client_id}")
                        status_response = self.client.get_order_status(
                            market=order.market,
                            client_id=client_id
                        )
                        # If successful, process the response
                        if status_response:
                            logger.info(f"Alternative approach succeeded for {client_id}")
                            # Process status_response same as above...
                            old_status = order.status
                            exchange_status = status_response.get('status', '')
                            if exchange_status == 'done':
                                order.status = OrderStatus.FILLED
                            elif exchange_status == 'part_deal':
                                order.status = OrderStatus.PARTIALLY_FILLED
                            elif exchange_status == 'cancel':
                                order.status = OrderStatus.CANCELLED
                            
                            order.filled_amount = safe_float(status_response.get('filled_amount', 0))
                            order.updated_at = datetime.now(timezone.utc)
                            
                            if old_status != order.status:
                                logger.info(f"Order {client_id} status: {old_status.value} -> {order.status.value}")
                            
                            if order.status in [OrderStatus.FILLED, OrderStatus.CANCELLED]:
                                del self.active_orders[client_id]
                                # Clean up status check timestamp
                                self._last_status_check.pop(client_id, None)
                            
                            return order
                    except Exception as e2:
                        logger.debug(f"Alternative approach also failed for {client_id}: {e2}")
                
                logger.debug("This is a known CoinEx API issue - continuing without status update")
                # Return order without modification - WebSocket updates will handle status changes
                return order
            elif "Rate limit" in str(e) or "429" in str(e):
                logger.warning(f"Rate limit hit checking order status for {client_id} - will retry later")
                return order
            else:
                logger.error(f"Failed to update order status {client_id}: {e}")
                return order
    
    def get_pending_orders(self, market: Optional[str] = None) -> List[Order]:
        """
        Get list of pending orders
        
        Args:
            market: Optional market filter
            
        Returns:
            List of pending orders
        """
        orders = []
        now = datetime.now(timezone.utc)
        stale_orders_to_remove = []
        
        for order in self.active_orders.values():
            if market and order.market != market:
                continue
            
            if order.status == OrderStatus.PENDING:
                # Check for orders older than 24 hours - likely stale
                order_age = (now - order.created_at).total_seconds()
                is_old_order = order_age > 86400  # 24 hours
                
                # Only perform REST API status check if enabled and enough time has passed, or if order is old
                should_check_status = is_old_order
                if not should_check_status and ENABLE_REST_API_STATUS_CHECKS:
                    last_check = self._last_status_check.get(order.client_id)
                    if (not last_check or 
                        (now - last_check).total_seconds() > ORDER_STATUS_CHECK_INTERVAL):
                        should_check_status = True
                        self._last_status_check[order.client_id] = now
                
                if should_check_status:
                    if is_old_order:
                        logger.warning(f"Checking potentially stale order {order.client_id} (age: {order_age:.0f}s)")
                    else:
                        logger.debug(f"Performing periodic status check for order {order.client_id}")
                    
                    updated_order = self.update_order_status(order.client_id)
                    
                    # If order was removed during update (filled/cancelled), skip it
                    if not updated_order or order.client_id not in self.active_orders:
                        logger.info(f"Order {order.client_id} removed during status check")
                        continue
                else:
                    logger.debug(f"Skipping status check for {order.client_id} - relying on WebSocket updates")
                
                # Re-check if still pending after potential status update
                if order.client_id in self.active_orders and self.active_orders[order.client_id].status == OrderStatus.PENDING:
                    orders.append(self.active_orders[order.client_id])
        
        return orders
    
    def has_pending_buy_order(self, market: str) -> bool:
        """
        Check if there's already a pending buy order for market
        
        Args:
            market: Market symbol
            
        Returns:
            True if pending buy order exists
        """
        for order in self.get_pending_orders(market):
            if order.side == OrderSide.BUY:
                return True
        return False
    
    def has_pending_sell_order(self, market: str) -> bool:
        """
        Check if there's already a pending sell order for market
        
        Args:
            market: Market symbol
            
        Returns:
            True if pending sell order exists
        """
        for order in self.get_pending_orders(market):
            if order.side == OrderSide.SELL:
                return True
        return False
    
    def load_existing_orders(self, market: Optional[str] = None) -> int:
        """
        Load existing orders from exchange to prevent duplicates
        
        Args:
            market: Optional market filter
            
        Returns:
            Number of orders loaded
        """
        try:
            # Get pending orders from exchange
            response = self.client.get_pending_orders(market=market)
            # Handle CoinEx response format: {"code": 0, "data": [...], "pagination": {...}}
            if isinstance(response, list):
                orders_data = response
            elif isinstance(response, dict):
                # CoinEx format has data array and pagination object
                orders_data = response.get('data', [])
            else:
                orders_data = []
            
            loaded_count = 0
            
            for order_data in orders_data:
                client_id = order_data.get('client_id')
                
                # Only load orders with our client_id format
                if client_id and client_id.startswith('DRA_'):
                    # Extract creation timestamp from exchange data
                    created_time = order_data.get('created_at', 0)
                    if isinstance(created_time, str):
                        # Try to parse ISO format string
                        try:
                            # Handle ISO format: "2025-01-26T00:00:03Z" or "2025-01-26T00:00:03+00:00"
                            if created_time.endswith('Z'):
                                created_time = created_time[:-1] + '+00:00'
                            created_at = datetime.fromisoformat(created_time).replace(tzinfo=timezone.utc)
                        except ValueError:
                            # Fallback: use current time if parsing fails
                            logger.warning(f"Could not parse order creation time: {created_time}, using current time")
                            created_at = datetime.now(timezone.utc)
                    else:
                        # Timestamp in milliseconds
                        created_at = datetime.fromtimestamp(created_time / 1000, timezone.utc)
                    
                    # Create order object with proper creation timestamp
                    order = Order(
                        client_id=client_id,
                        market=order_data['market'],
                        side=OrderSide.BUY if order_data['side'] == 'buy' else OrderSide.SELL,
                        order_type=order_data['type'],
                        amount=float(order_data['amount']),
                        price=float(order_data['price']),
                        status=OrderStatus.PENDING,
                        exchange_order_id=order_data['order_id'],
                        filled_amount=safe_float(order_data.get('filled_amount', 0)),
                        created_at=created_at
                    )
                    
                    # Debug log to verify creation date is properly loaded
                    logger.debug(f"Loaded order {client_id} with creation date: {created_at.date()} {created_at.time()}")
                    
                    # Add to tracking
                    self.active_orders[client_id] = order
                    self.used_client_ids.add(client_id)
                    loaded_count += 1
            
            logger.info(f"Loaded {loaded_count} existing orders from exchange")
            return loaded_count
            
        except Exception as e:
            # Some CoinEx API endpoints may require specific permissions
            # or may not be available for all account types
            if "Signature Incorrect" in str(e):
                logger.warning(f"Pending orders endpoint authentication failed - this may be a permission issue: {e}")
                logger.info("Continuing without loading existing orders - new orders will still work")
            else:
                logger.error(f"Failed to load existing orders: {e}")
            return 0
    
    def handle_order_completion(self, tracked_order) -> None:
        """
        Handle order completion events from OrderTracker
        
        This method is called when OrderTracker detects an order is fully filled
        or cancelled via WebSocket events, ensuring OrderManager's cache stays synchronized.
        
        Args:
            tracked_order: TrackedOrder object from OrderTracker
        """
        try:
            # Find the corresponding order in our active_orders by client_id or order_id
            client_id = tracked_order.client_id
            order_id = tracked_order.order_id
            
            order_to_remove = None
            
            # Try to find by client_id first (most reliable)
            if client_id and client_id in self.active_orders:
                order_to_remove = client_id
            else:
                # Fallback: search by exchange order_id
                for active_client_id, active_order in self.active_orders.items():
                    if active_order.exchange_order_id == order_id:
                        order_to_remove = active_client_id
                        break
            
            if order_to_remove:
                # Remove from active orders (order is completed)
                removed_order = self.active_orders.pop(order_to_remove)
                
                # Clean up status check timestamp
                self._last_status_check.pop(order_to_remove, None)
                
                logger.info(f"OrderManager: Removed completed order {order_to_remove} "
                          f"({tracked_order.status.value}) via OrderTracker event")
                
                # Log order completion details for debugging
                logger.debug(f"Completed order details: market={tracked_order.market}, "
                           f"side={tracked_order.side.value}, filled={tracked_order.filled_amount}/"
                           f"{tracked_order.original_amount}")
            else:
                # Order not found in active_orders - may have been removed already or not tracked
                logger.debug(f"OrderManager: Order completion event for {client_id or order_id} "
                           f"but order not found in active_orders (may have been cleaned up already)")
                
        except Exception as e:
            logger.error(f"Error handling order completion from OrderTracker: {e}", exc_info=True)
    
    def _investigate_missing_order(self, client_id: str, market: str) -> Optional[Dict[str, Any]]:
        """
        CSI: Order Investigation - What happened to this missing order?
        
        Args:
            client_id: Client order ID that's missing
            market: Market symbol
            
        Returns:
            Order data if found, None if order never existed or failed
        """
        try:
            logger.info(f"🕵️ Investigating missing order: {client_id}")
            
            # Check 1: Query current pending orders for this market
            logger.debug(f"🔍 Checking pending orders for {market}")
            try:
                # Get pending orders (this may include the missing order if it's still pending)
                pending_response = self.client.get_pending_orders(market=market)
                
                # Handle different response formats
                if isinstance(pending_response, list):
                    pending_orders = pending_response
                elif isinstance(pending_response, dict):
                    pending_orders = pending_response.get('data', [])
                else:
                    pending_orders = []
                
                # Look for our missing order in pending orders
                for order_data in pending_orders:
                    if order_data.get('client_id') == client_id:
                        status = order_data.get('status', 'pending')
                        logger.info(f"🕵️ Found missing order in pending orders: {client_id} status={status}")
                        return order_data
                            
            except Exception as e:
                logger.warning(f"⚠️ Could not check pending orders: {e}")
            
            # Check 2: Try to query the specific order directly (last resort)
            logger.debug(f"🔍 Direct order query for {client_id}")
            try:
                if client_id in self.active_orders:
                    order = self.active_orders[client_id]
                    if order.exchange_order_id:
                        status_response = self.client.get_order_status(
                            market=market,
                            order_id=order.exchange_order_id
                        )
                        if status_response:
                            logger.info(f"🕵️ Direct query found order: {client_id}")
                            return status_response
            except Exception as e:
                logger.debug(f"Direct order query failed: {e}")
            
            logger.info(f"🕵️ Investigation complete: Order {client_id} likely failed silently during placement")
            return None
            
        except Exception as e:
            logger.error(f"Error during order investigation for {client_id}: {e}")
            return None
    
    def _handle_discovered_fill(self, order_data: Dict[str, Any]) -> None:
        """
        Handle an order that was discovered to be filled during investigation
        
        Args:
            order_data: Order data from investigation
        """
        try:
            client_id = order_data.get('client_id')
            if not client_id:
                return
            
            logger.info(f"🔄 Processing discovered fill for {client_id}")
            
            # Update local order if it exists
            if client_id in self.active_orders:
                order = self.active_orders[client_id]
                order.status = OrderStatus.FILLED
                order.filled_amount = safe_float(order_data.get('filled_amount', order.amount))
                order.updated_at = datetime.now(timezone.utc)
                
                # Remove from active orders (it's completed)
                del self.active_orders[client_id]
                self._last_status_check.pop(client_id, None)
                
                logger.info(f"✅ Updated local state: {client_id} marked as filled")
            else:
                logger.debug(f"Order {client_id} not in local tracking - no local update needed")
            
        except Exception as e:
            logger.error(f"Error handling discovered fill for {order_data}: {e}")
    
    def _mark_order_failed(self, client_id: str) -> None:
        """
        Mark an order as failed and clean up tracking
        
        Args:
            client_id: Client order ID to mark as failed
        """
        try:
            logger.info(f"🔄 Marking order as failed: {client_id}")
            
            # Remove from active orders if present  
            if client_id in self.active_orders:
                order = self.active_orders[client_id]
                order.status = OrderStatus.FAILED  
                order.updated_at = datetime.now(timezone.utc)
                
                # Remove from active tracking
                del self.active_orders[client_id]
                self._last_status_check.pop(client_id, None)
                
                logger.info(f"✅ Cleaned up failed order: {client_id}")
            
            # Clean up client_id reservation
            self.used_client_ids.discard(client_id)
            
        except Exception as e:
            logger.error(f"Error marking order as failed {client_id}: {e}")
    
    def _reconcile_order_state(self, client_id: str, market: str) -> bool:
        """
        Reconcile local order state with exchange reality
        
        Args:
            client_id: Client order ID
            market: Market symbol
            
        Returns:
            True if reconciliation successful
        """
        try:
            logger.info(f"🔄 Reconciling state for order: {client_id}")
            
            # Investigate what happened to this order
            investigation_result = self._investigate_missing_order(client_id, market)
            
            if investigation_result:
                status = investigation_result.get('status', '')
                
                if status in ['done', 'filled']:
                    self._handle_discovered_fill(investigation_result)
                    logger.info(f"✅ Reconciliation: {client_id} was filled")
                    return True
                elif status in ['cancel', 'cancelled']:
                    self._mark_order_failed(client_id)  # Treat external cancellation as failure
                    logger.info(f"✅ Reconciliation: {client_id} was cancelled externally") 
                    return True
                else:
                    logger.warning(f"⚠️ Reconciliation: {client_id} status unclear: {status}")
                    return False
            else:
                # Order never existed or failed silently
                self._mark_order_failed(client_id)
                logger.info(f"✅ Reconciliation: {client_id} marked as failed (never existed)")
                return True
                
        except Exception as e:
            logger.error(f"Error reconciling order state for {client_id}: {e}")
            return False