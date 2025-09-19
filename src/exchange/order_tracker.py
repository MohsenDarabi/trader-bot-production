"""
Order Tracking System for CoinEx
Monitors order fills in real-time and manages buy-sell order pairing to prevent short positions
"""
import time
import asyncio
from typing import Dict, List, Optional, Callable, Any
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime

from src.exchange.websocket_client import CoinExWebSocketClient
from src.exchange.coinex_client import CoinExClient
from src.utils.logger import get_logger
from src.utils.safe_conversions import safe_float


logger = get_logger(__name__)


class OrderSide(Enum):
    """Order side enumeration"""
    BUY = "buy"
    SELL = "sell"


class OrderStatus(Enum):
    """Order status enumeration"""
    PENDING = "pending"
    PARTIAL = "partial"
    FILLED = "filled"
    CANCELLED = "cancelled"


@dataclass
class OrderFill:
    """Represents a single order fill/execution"""
    fill_id: str
    order_id: str
    market: str
    side: OrderSide
    amount: float
    price: float
    timestamp: datetime
    fee: float = 0.0
    role: str = "taker"  # taker or maker


@dataclass
class TrackedOrder:
    """Represents an order being tracked"""
    order_id: str
    client_id: Optional[str]
    market: str
    side: OrderSide
    original_amount: float
    price: float
    status: OrderStatus
    filled_amount: float = 0.0
    remaining_amount: float = field(init=False)
    fills: List[OrderFill] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    
    def __post_init__(self):
        self.remaining_amount = self.original_amount - self.filled_amount
    
    def add_fill(self, fill: OrderFill) -> None:
        """Add a fill to this order"""
        self.fills.append(fill)
        self.filled_amount += fill.amount
        self.remaining_amount = self.original_amount - self.filled_amount
        self.updated_at = datetime.now()
        
        # Update status based on fill
        if self.remaining_amount <= 0:
            self.status = OrderStatus.FILLED
        elif self.filled_amount > 0:
            self.status = OrderStatus.PARTIAL


@dataclass
class OrderPair:
    """Represents a buy order and its corresponding sell orders"""
    buy_order: TrackedOrder
    sell_orders: List[TrackedOrder] = field(default_factory=list)
    total_sell_amount: float = 0.0
    is_complete: bool = False
    
    def add_sell_order(self, sell_order: TrackedOrder) -> None:
        """Add a sell order to this pair"""
        self.sell_orders.append(sell_order)
        self.total_sell_amount += sell_order.original_amount
    
    def get_remaining_buy_amount(self) -> float:
        """Get the remaining buy amount that needs sell orders"""
        return self.buy_order.filled_amount - self.total_sell_amount
    
    def is_balanced(self) -> bool:
        """Check if sell orders match buy fills exactly"""
        return abs(self.get_remaining_buy_amount()) < 0.0001  # Account for floating point precision


class OrderTracker:
    """
    Tracks orders in real-time and manages buy-sell pairing to prevent short positions
    """
    
    def __init__(self, websocket_client: CoinExWebSocketClient, 
                 rest_client: CoinExClient):
        """
        Initialize order tracker
        
        Args:
            websocket_client: WebSocket client for real-time updates
            rest_client: REST client for API calls
        """
        self.ws_client = websocket_client
        self.rest_client = rest_client
        
        # Order tracking
        self.tracked_orders: Dict[str, TrackedOrder] = {}
        self.order_pairs: Dict[str, OrderPair] = {}  # buy_order_id -> OrderPair
        
        # Event handlers
        self.fill_handlers: List[Callable[[OrderFill], None]] = []
        self.order_complete_handlers: List[Callable[[TrackedOrder], None]] = []
        self.pair_complete_handlers: List[Callable[[OrderPair], None]] = []
        
        # Register WebSocket handlers
        self._register_websocket_handlers()
    
    def _register_websocket_handlers(self) -> None:
        """Register handlers for WebSocket messages"""
        self.ws_client.register_handler("order.update", self._handle_order_update)
        self.ws_client.register_handler("user_deals.update", self._handle_user_deals_update)
    
    def add_fill_handler(self, handler: Callable[[OrderFill], None]) -> None:
        """Add a handler for order fills"""
        self.fill_handlers.append(handler)
    
    def add_order_complete_handler(self, handler: Callable[[TrackedOrder], None]) -> None:
        """Add a handler for completed orders"""
        self.order_complete_handlers.append(handler)
    
    def add_pair_complete_handler(self, handler: Callable[[OrderPair], None]) -> None:
        """Add a handler for completed order pairs"""
        self.pair_complete_handlers.append(handler)
    
    def track_order(self, order_id: str, client_id: Optional[str], 
                   market: str, side: OrderSide, amount: float, price: float) -> None:
        """
        Start tracking an order
        
        Args:
            order_id: Exchange order ID
            client_id: Custom client order ID
            market: Market symbol
            side: Order side (buy/sell)
            amount: Order amount
            price: Order price
        """
        order = TrackedOrder(
            order_id=order_id,
            client_id=client_id,
            market=market,
            side=side,
            original_amount=amount,
            price=price,
            status=OrderStatus.PENDING
        )
        
        self.tracked_orders[order_id] = order
        
        # If this is a buy order, create a new order pair
        if side == OrderSide.BUY:
            self.order_pairs[order_id] = OrderPair(buy_order=order)
        
        logger.info(f"Started tracking {side.value} order {order_id} for {amount} {market} @ {price}")
    
    def get_order(self, order_id: str) -> Optional[TrackedOrder]:
        """Get a tracked order by ID"""
        return self.tracked_orders.get(order_id)
    
    def get_order_pair(self, buy_order_id: str) -> Optional[OrderPair]:
        """Get an order pair by buy order ID"""
        return self.order_pairs.get(buy_order_id)
    
    def get_unmatched_buy_fills(self) -> List[OrderPair]:
        """Get buy orders that have fills but no corresponding sell orders"""
        unmatched = []
        for pair in self.order_pairs.values():
            if pair.buy_order.filled_amount > 0 and pair.get_remaining_buy_amount() > 0.0001:
                unmatched.append(pair)
        return unmatched
    
    def link_sell_order_to_buy(self, sell_order_id: str, buy_order_id: str) -> bool:
        """
        Link a sell order to a specific buy order
        
        Args:
            sell_order_id: Sell order ID
            buy_order_id: Buy order ID to link to
            
        Returns:
            True if linking successful, False otherwise
        """
        sell_order = self.tracked_orders.get(sell_order_id)
        order_pair = self.order_pairs.get(buy_order_id)
        
        if not sell_order or not order_pair:
            logger.error(f"Cannot link orders: sell_order={sell_order_id}, buy_order={buy_order_id}")
            return False
        
        if sell_order.side != OrderSide.SELL:
            logger.error(f"Order {sell_order_id} is not a sell order")
            return False
        
        order_pair.add_sell_order(sell_order)
        logger.info(f"Linked sell order {sell_order_id} to buy order {buy_order_id}")
        return True
    
    def _handle_order_update(self, data: Dict[str, Any]) -> None:
        """Handle order status updates from WebSocket"""
        try:
            logger.debug(f"Processing order update: {data}")
            logger.info("Processing order update")
            
            event_type = data.get("event")
            # CoinEx sends single "order" object, not "orders" array
            order_data = data.get("order", {})
            
            if order_data:
                order_id = str(order_data.get("order_id"))
                logger.info(f"Order update for ID {order_id}: event={event_type}")
                
                if order_id in self.tracked_orders:
                    logger.info(f"Updating tracked order {order_id}")
                    self._update_order_from_data(order_id, order_data, event_type)
                else:
                    logger.info(f"Order {order_id} not in tracked orders - might be external order")
            else:
                logger.warning("Order update received but no order data found")
                    
        except Exception as e:
            logger.error(f"Error handling order update: {e}", exc_info=True)
    
    def _handle_user_deals_update(self, data: Dict[str, Any]) -> None:
        """Handle user deal/fill updates from WebSocket"""
        try:
            logger.debug(f"Processing user deals update: {data}")
            logger.info("Processing user deals update")
            
            # Check both possible structures for deals data
            deals = data.get("deals", [])
            if not deals and "deal" in data:
                # Single deal object
                deals = [data.get("deal")]
            
            for deal_data in deals:
                if deal_data:
                    logger.debug(f"Processing deal: {deal_data}")
                    logger.info(f"Processing deal for order")
                    self._process_deal(deal_data)
                
        except Exception as e:
            logger.error(f"Error handling user deals update: {e}", exc_info=True)
    
    def _process_deal(self, deal_data: Dict[str, Any]) -> None:
        """Process a single deal/fill"""
        try:
            order_id = str(deal_data.get("order_id"))
            
            if order_id not in self.tracked_orders:
                logger.debug(f"Received deal for untracked order: {order_id}")
                return
            
            # Create OrderFill from deal data
            fill = OrderFill(
                fill_id=str(deal_data.get("deal_id")),
                order_id=order_id,
                market=deal_data.get("market"),
                side=OrderSide(deal_data.get("side")),
                amount=safe_float(deal_data.get("amount")),
                price=safe_float(deal_data.get("price")),
                timestamp=datetime.fromtimestamp(safe_float(deal_data.get("timestamp", time.time()))),
                fee=safe_float(deal_data.get("fee", 0)),
                role=deal_data.get("role", "taker")
            )
            
            # Add fill to tracked order
            order = self.tracked_orders[order_id]
            order.add_fill(fill)
            
            logger.info(f"Order {order_id} filled: {fill.amount} @ {fill.price} (total: {order.filled_amount}/{order.original_amount})")
            
            # Notify fill handlers
            for handler in self.fill_handlers:
                try:
                    handler(fill)
                except Exception as e:
                    logger.error(f"Error in fill handler: {e}")
            
            # Check if order is complete
            if order.status == OrderStatus.FILLED:
                logger.info(f"Order {order_id} fully filled")
                for handler in self.order_complete_handlers:
                    try:
                        handler(order)
                    except Exception as e:
                        logger.error(f"Error in order complete handler: {e}")
            
            # Check if this is a buy order and update the pair
            if order.side == OrderSide.BUY and order_id in self.order_pairs:
                pair = self.order_pairs[order_id]
                if pair.is_balanced() and pair.buy_order.status == OrderStatus.FILLED:
                    pair.is_complete = True
                    logger.info(f"Order pair for buy order {order_id} is complete")
                    for handler in self.pair_complete_handlers:
                        try:
                            handler(pair)
                        except Exception as e:
                            logger.error(f"Error in pair complete handler: {e}")
            
        except Exception as e:
            logger.error(f"Error processing deal: {e}")
    
    def _update_order_from_data(self, order_id: str, order_data: Dict[str, Any], 
                               event_type: str) -> None:
        """Update order from WebSocket order data"""
        try:
            order = self.tracked_orders[order_id]
            
            # Update order status
            status_map = {
                "pending": OrderStatus.PENDING,
                "partial": OrderStatus.PARTIAL, 
                "done": OrderStatus.FILLED,
                "cancelled": OrderStatus.CANCELLED
            }
            
            status = order_data.get("status", "pending")
            order.status = status_map.get(status, OrderStatus.PENDING)
            
            # Update filled amount if provided
            if "filled_amount" in order_data:
                order.filled_amount = float(order_data["filled_amount"])
                order.remaining_amount = order.original_amount - order.filled_amount
            
            order.updated_at = datetime.now()
            
            logger.debug(f"Updated order {order_id}: status={order.status.value}, filled={order.filled_amount}")
            
        except Exception as e:
            logger.error(f"Error updating order {order_id}: {e}")
    
    async def sync_existing_orders(self, market: Optional[str] = None) -> None:
        """
        Sync with existing orders from the exchange, including both pending and recently filled orders.
        """
        try:
            logger.info(f"Syncing existing orders from exchange for market: {market or 'all'}...")

            # --- Sync PENDING orders ---
            pending_response = self.rest_client.get_pending_orders(market=market)
            pending_orders = pending_response.get("data", pending_response if isinstance(pending_response, list) else [])

            for order_data in pending_orders:
                order_id = str(order_data.get("order_id"))
                if order_id not in self.tracked_orders:
                    side = OrderSide(order_data.get("side"))
                    self.track_order(
                        order_id=order_id,
                        client_id=order_data.get("client_id"),
                        market=order_data.get("market"),
                        side=side,
                        amount=safe_float(order_data.get("amount")),
                        price=safe_float(order_data.get("price"))
                    )
                    self._update_order_from_data(order_id, order_data, "sync")

            # --- Sync FILLED orders (deals) from the last few hours ---
            from config.settings import TRADING_INTERVAL
            lookback_hours = 2 if TRADING_INTERVAL == 'hourly' else 24  # 2 hours for hourly, 24 for daily

            now_ms = int(time.time() * 1000)
            start_time_ms = now_ms - (lookback_hours * 60 * 60 * 1000)

            # This needs to be awaited as it's a coroutine
            # Per TROUBLESHOOTING.md, do not pass optional 'market' param to avoid signature errors.
            deals_response = await asyncio.to_thread(
                self.rest_client.get_user_deals,
                start_time=start_time_ms,
                limit=1000
            )
            all_deals = deals_response.get("data", [])

            # Filter deals manually if a market was specified
            deals = [d for d in all_deals if d.get('market') == market] if market else all_deals

            for deal_data in deals:
                # The deal processing logic will automatically add the fill to the correct tracked order
                self._process_deal(deal_data)

            logger.info(f"Synced {len(pending_orders)} pending orders and {len(deals)} recent fills for {market or 'all'}.")

        except Exception as e:
            logger.error(f"Error syncing existing orders: {e}")
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get tracking statistics"""
        total_orders = len(self.tracked_orders)
        buy_orders = sum(1 for o in self.tracked_orders.values() if o.side == OrderSide.BUY)
        sell_orders = sum(1 for o in self.tracked_orders.values() if o.side == OrderSide.SELL)
        filled_orders = sum(1 for o in self.tracked_orders.values() if o.status == OrderStatus.FILLED)
        total_pairs = len(self.order_pairs)
        complete_pairs = sum(1 for p in self.order_pairs.values() if p.is_complete)
        unmatched_pairs = len(self.get_unmatched_buy_fills())
        
        return {
            "total_orders": total_orders,
            "buy_orders": buy_orders,
            "sell_orders": sell_orders,
            "filled_orders": filled_orders,
            "total_pairs": total_pairs,
            "complete_pairs": complete_pairs,
            "unmatched_pairs": unmatched_pairs
        }
