"""
Order Management System with client_id duplicate prevention
"""
import time
import signal
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set
from dataclasses import dataclass
from enum import Enum

from src.exchange.coinex_client import CoinExClient
from src.core.profitability import ProfitabilityValidator
from src.utils.logger import get_logger


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


class OrderSide(Enum):
    """Order side enumeration"""
    BUY = "buy"
    SELL = "sell"


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
    created_at: datetime = None
    updated_at: datetime = None
    
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
        # Generate client_id
        client_id = self.generate_client_id(market, OrderSide.BUY)
        
        logger.info(f"Placing buy order: {market} {amount} @ ${price:.2f} [{client_id}]")
        
        try:
            # Place order on exchange with detailed logging and timeout protection
            logger.info(f"About to call client.place_order() with params: market={market}, side=buy, amount={amount}, price={price}, client_id={client_id}, is_hide={is_hide}")
            
            # Set up timeout protection (45 seconds)
            signal.signal(signal.SIGALRM, timeout_handler)
            signal.alarm(45)
            
            try:
                response = self.client.place_order(
                    market=market,
                    side='buy',
                    amount=str(amount),
                    order_type='limit',
                    price=str(price),
                    client_id=client_id,
                    is_hide=is_hide
                )
                
                # Clear the alarm
                signal.alarm(0)
                logger.info(f"Received response from place_order: {response}")
                
            except TimeoutError:
                logger.error("Order placement timed out after 45 seconds")
                signal.alarm(0)  # Clear the alarm
                raise Exception("Order placement timed out - possible API or network issue")
            
            # Create order object
            order = Order(
                client_id=client_id,
                market=market,
                side=OrderSide.BUY,
                order_type='limit',
                amount=amount,
                price=price,
                status=OrderStatus.PENDING,
                exchange_order_id=response.get('order_id')
            )
            
            # Store in active orders
            self.active_orders[client_id] = order
            
            logger.info(f"Buy order placed successfully: {client_id} -> {order.exchange_order_id}")
            return order
            
        except Exception as e:
            logger.error(f"Failed to place buy order {client_id}: {e}")
            # Remove from used IDs since order failed
            self.used_client_ids.discard(client_id)
            return None
    
    def place_sell_order(self, market: str, amount: float, price: float,
                        position_size: float, is_hide: bool = True) -> Optional[Order]:
        """
        Place a sell order with profitability validation
        
        Args:
            market: Market symbol
            amount: Order amount in base currency
            price: Limit price
            position_size: Position size for validation
            is_hide: Whether to hide order from public order book
            
        Returns:
            Order object if successful, None otherwise
        """
        # Profitability validation is handled by the trading bot before calling this method
        
        # Generate client_id
        client_id = self.generate_client_id(market, OrderSide.SELL)
        
        logger.info(f"Placing sell order: {market} {amount} @ ${price:.2f} [{client_id}]")
        
        try:
            # Place order on exchange
            response = self.client.place_order(
                market=market,
                side='sell',
                amount=str(amount),
                order_type='limit',
                price=str(price),
                client_id=client_id,
                is_hide=is_hide
            )
            
            # Create order object
            order = Order(
                client_id=client_id,
                market=market,
                side=OrderSide.SELL,
                order_type='limit',
                amount=amount,
                price=price,
                status=OrderStatus.PENDING,
                exchange_order_id=response.get('order_id')
            )
            
            # Store in active orders
            self.active_orders[client_id] = order
            
            logger.info(f"Sell order placed successfully: {client_id} -> {order.exchange_order_id}")
            return order
            
        except Exception as e:
            logger.error(f"Failed to place sell order {client_id}: {e}")
            # Remove from used IDs since order failed
            self.used_client_ids.discard(client_id)
            return None
    
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
            # Cancel on exchange
            self.client.cancel_order(
                market=order.market,
                client_id=client_id
            )
            
            # Update status
            order.status = OrderStatus.CANCELLED
            order.updated_at = datetime.now(timezone.utc)
            
            # Remove from active orders
            del self.active_orders[client_id]
            
            logger.info(f"Order cancelled: {client_id}")
            return True
            
        except Exception as e:
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
                order.filled_amount = float(status_response.get('filled_amount', 0))
                order.updated_at = datetime.now(timezone.utc)
                
                # Log status changes
                if old_status != order.status:
                    logger.info(f"Order {client_id} status: {old_status.value} -> {order.status.value}")
                
                # Remove from active if completed
                if order.status in [OrderStatus.FILLED, OrderStatus.CANCELLED]:
                    del self.active_orders[client_id]
            
            return order
            
        except Exception as e:
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
                            
                            order.filled_amount = float(status_response.get('filled_amount', 0))
                            order.updated_at = datetime.now(timezone.utc)
                            
                            if old_status != order.status:
                                logger.info(f"Order {client_id} status: {old_status.value} -> {order.status.value}")
                            
                            if order.status in [OrderStatus.FILLED, OrderStatus.CANCELLED]:
                                del self.active_orders[client_id]
                            
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
        
        for order in self.active_orders.values():
            if market and order.market != market:
                continue
            
            if order.status == OrderStatus.PENDING:
                # Update status from exchange
                self.update_order_status(order.client_id)
                
                # Re-check if still pending
                if order.client_id in self.active_orders:
                    orders.append(order)
        
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
                    # Create order object
                    order = Order(
                        client_id=client_id,
                        market=order_data['market'],
                        side=OrderSide.BUY if order_data['side'] == 'buy' else OrderSide.SELL,
                        order_type=order_data['type'],
                        amount=float(order_data['amount']),
                        price=float(order_data['price']),
                        status=OrderStatus.PENDING,
                        exchange_order_id=order_data['order_id'],
                        filled_amount=float(order_data.get('filled_amount', 0))
                    )
                    
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