"""
Event-Driven Trading Bot
Handles WebSocket events and executes trading logic reactively
"""
import asyncio
from datetime import datetime, timezone, time, timedelta
from typing import Dict, Optional, List, Callable
from dataclasses import dataclass

from src.core.trading_state import TradingState, CalculationMode, MarketOHLC
from src.core.trading_bot import DailyRangeBot
from src.models.order import Order, OrderStatus, OrderSide, OrderType
from src.models.position import Position, PositionSide
from src.models.signal import DailyRangeSignal
from src.utils.logger import get_logger
from config.settings import TIMEZONE


logger = get_logger(__name__)


class EventDrivenBot(DailyRangeBot):
    """
    Event-driven version of the Daily Range Bot
    Reacts to WebSocket events instead of polling
    """
    
    def __init__(self):
        super().__init__()
        self.state: Optional[TradingState] = None
        self.daily_reset_task: Optional[asyncio.Task] = None
        self.heartbeat_task: Optional[asyncio.Task] = None
        
    async def initialize(self):
        """Initialize bot with event-driven components"""
        await super().initialize()
        
        # Subscribe to WebSocket events
        await self._setup_websocket_subscriptions()
        
        # Start daily reset timer
        self.daily_reset_task = asyncio.create_task(self._daily_reset_timer())
        
        # Start heartbeat for safety checks
        self.heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        
        logger.info("Event-driven bot initialized")
    
    async def _setup_websocket_subscriptions(self):
        """Setup WebSocket subscriptions and handlers"""
        # Connect and authenticate WebSocket
        if not self.websocket_client.is_connected:
            await self.websocket_client.connect()
            await self.websocket_client.authenticate()
        
        # Register event handlers
        self.websocket_client.register_handler("order.update", self._handle_order_update)
        self.websocket_client.register_handler("position.update", self._handle_position_update)
        
        # Subscribe to updates
        await self.websocket_client.subscribe_orders()
        await self.websocket_client.subscribe_positions()
        
        logger.info("WebSocket subscriptions setup complete")
    
    async def set_trading_market(self, market: str):
        """Set the market to trade and initialize state"""
        self.trading_markets = [market]
        
        # Initialize state for the market
        self.state = TradingState(market=market)
        
        # Load initial state
        await self._load_initial_state()
        
        logger.info(f"Trading market set to {market}")
    
    async def _load_initial_state(self):
        """Load initial positions, orders, and market data"""
        if not self.state:
            return
            
        market = self.state.market
        
        # Load current position
        positions = await self.position_manager.get_positions()
        market_position = next((p for p in positions if p.market == market), None)
        self.state.update_position(market_position)
        
        # Load active orders
        orders = await self.order_manager.get_active_orders(market)
        buy_orders = [o for o in orders if o.side == OrderSide.BUY]
        sell_orders = [o for o in orders if o.side == OrderSide.SELL]
        
        if buy_orders:
            self.state.active_buy_order = buy_orders[0]  # Should only be one
        self.state.update_sell_orders(sell_orders)
        
        # Load OHLC data
        await self._update_ohlc_data()
        
        # Check if we need to handle restart scenario
        await self._handle_restart_scenario()
        
        self.state.log_state_summary()
    
    async def _update_ohlc_data(self):
        """Update OHLC data in state"""
        if not self.state:
            return
            
        # Get yesterday's OHLC
        yesterday_ohlc = self.market_data.get_previous_day_ohlc(self.state.market)
        self.state.yesterday_ohlc = MarketOHLC(
            high=yesterday_ohlc['high'],
            low=yesterday_ohlc['low'],
            open=yesterday_ohlc['open'],
            close=yesterday_ohlc['close'],
            timestamp=datetime.now(timezone.utc)
        )
        
        # Get today's OHLC (if available)
        try:
            ticker = await self.client.get_ticker(self.state.market)
            if ticker:
                self.state.update_today_ohlc(
                    high=float(ticker['high']),
                    low=float(ticker['low']),
                    open_price=float(ticker['open']),
                    close=float(ticker['last'])
                )
        except Exception as e:
            logger.warning(f"Could not get today's OHLC: {e}")
    
    async def _handle_restart_scenario(self):
        """Handle bot restart with existing positions"""
        if not self.state or not self.state.current_position:
            return
            
        # Check if we have uncovered positions
        if self.state.uncovered_amount > 0:
            logger.info(f"Restart detected: {self.state.uncovered_amount} uncovered position")
            
            # Calculate smart sell price
            position = self.state.current_position
            min_profitable = position.avg_entry_price * 1.015  # 1.5% minimum
            
            current_price = await self._get_current_price(self.state.market)
            if current_price > min_profitable:
                sell_price = current_price
                logger.info(f"Using current market price for sell: ${sell_price:.2f}")
            else:
                sell_price = min_profitable
                logger.info(f"Using minimum profitable price for sell: ${sell_price:.2f}")
            
            # Place sell order for uncovered amount
            await self._place_sell_order(self.state.uncovered_amount, sell_price)
    
    async def _handle_order_update(self, data: Dict):
        """Handle order update from WebSocket"""
        try:
            event = data.get('event', '')
            order_data = data.get('order', {})
            
            if not order_data or not self.state:
                return
                
            # Convert to Order object
            order = self._parse_order_data(order_data)
            if order.market != self.state.market:
                return  # Not our market
                
            logger.info(f"Order update: {order.client_id} - {event} - {order.status}")
            
            # Handle based on order side and event
            if order.side == OrderSide.BUY:
                await self._handle_buy_order_update(order, event)
            else:  # SELL
                await self._handle_sell_order_update(order, event)
                
        except Exception as e:
            logger.error(f"Error handling order update: {e}", exc_info=True)
    
    async def _handle_buy_order_update(self, order: Order, event: str):
        """Handle buy order updates"""
        if order.status == OrderStatus.FILLED:
            logger.info(f"Buy order filled: {order.client_id} - {order.amount} @ ${order.price}")
            
            # Clear buy order from state
            self.state.clear_buy_order()
            
            # Place corresponding sell order
            sell_price = self.state.current_signals.sell_price if self.state.current_signals else order.price * 1.015
            await self._place_sell_order(order.amount, sell_price, corresponding_buy_id=order.client_id)
            
        elif order.status in [OrderStatus.CANCELLED, OrderStatus.REJECTED]:
            logger.info(f"Buy order {order.status}: {order.client_id}")
            self.state.clear_buy_order()
    
    async def _handle_sell_order_update(self, order: Order, event: str):
        """Handle sell order updates"""
        if order.status == OrderStatus.FILLED:
            logger.info(f"Sell order filled: {order.client_id} - {order.amount} @ ${order.price}")
            
            # Remove from active sells
            self.state.active_sell_orders = [o for o in self.state.active_sell_orders 
                                           if o.client_id != order.client_id]
            
            # Check if this completes a cycle
            for buy_id, (sell_id, amount) in list(self.state.pending_buy_sell_pairs.items()):
                if sell_id == order.client_id:
                    self.state.complete_cycle(buy_id)
                    
                    # Check if we should recalculate signals
                    if self.state.should_recalculate_signals():
                        await self._recalculate_signals_hybrid()
                    
                    # Place new buy order
                    await self._evaluate_buy_opportunity()
                    break
    
    async def _handle_position_update(self, data: Dict):
        """Handle position update from WebSocket"""
        try:
            event = data.get('event', '')
            position_data = data.get('position', {})
            
            if not position_data or not self.state:
                return
                
            # Check if it's our market
            if position_data.get('market') != self.state.market:
                return
                
            # Convert to Position object
            position = self._parse_position_data(position_data)
            
            logger.info(f"Position update: {position.market} - {position.side} - {position.size}")
            
            # Update state
            old_size = self.state.total_position_size
            self.state.update_position(position)
            
            # If position increased, we might have a fill without order update
            if position.size > old_size:
                logger.info(f"Position increased from {old_size} to {position.size}")
                
            # If position closed
            if old_size > 0 and position.size == 0:
                logger.info("Position closed completely")
                
        except Exception as e:
            logger.error(f"Error handling position update: {e}", exc_info=True)
    
    async def _recalculate_signals_hybrid(self):
        """Recalculate signals using hybrid approach"""
        if not self.state or not self.state.yesterday_ohlc or not self.state.today_ohlc:
            logger.warning("Cannot recalculate signals - missing OHLC data")
            return
            
        # Update today's OHLC first
        await self._update_ohlc_data()
        
        # Get prices for hybrid calculation
        high, low = self.state.get_price_calculation_params()
        
        # Generate new signal
        signal = self.strategy.generate_signal_with_custom_prices(
            self.state.market, high, low, "hybrid (today_low)"
        )
        
        # Apply buy price adjustment
        current_price = await self._get_current_price(self.state.market)
        adjusted_buy = min(signal.buy_price, current_price)
        
        # Update signal with adjusted price
        signal.buy_price = adjusted_buy
        self.state.current_signals = signal
        self.state.increment_calculation_generation()
        
        logger.info(f"Recalculated signals (gen {self.state.calculation_generation}): "
                   f"Buy=${adjusted_buy:.2f}, Sell=${signal.sell_price:.2f}")
    
    async def _evaluate_buy_opportunity(self):
        """Evaluate if we should place a buy order"""
        if not self.state or self.state.active_buy_order:
            return  # Already have a buy order
            
        if not self.state.current_signals:
            # Generate initial signals if needed
            await self._generate_initial_signals()
            
        if self.state.current_signals:
            # Place buy order at calculated price
            await self._place_buy_order()
    
    async def _generate_initial_signals(self):
        """Generate initial daily signals"""
        if not self.state:
            return
            
        signal = self.strategy.generate_daily_signal(self.state.market, force=True)
        if signal:
            # Apply buy price adjustment
            current_price = await self._get_current_price(self.state.market)
            signal.buy_price = min(signal.buy_price, current_price)
            
            self.state.current_signals = signal
            logger.info(f"Generated initial signals: Buy=${signal.buy_price:.2f}, Sell=${signal.sell_price:.2f}")
    
    async def _place_buy_order(self):
        """Place a buy order"""
        if not self.state or not self.state.current_signals:
            return
            
        # Calculate position size
        size = await self._calculate_position_size()
        if size <= 0:
            logger.warning("Position size is 0, skipping buy order")
            return
            
        # Place order
        order = await self.order_manager.place_order(
            market=self.state.market,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            amount=size / self.state.current_signals.buy_price,
            price=self.state.current_signals.buy_price
        )
        
        if order:
            self.state.add_buy_order(order)
    
    async def _place_sell_order(self, amount: float, price: float, 
                              corresponding_buy_id: Optional[str] = None):
        """Place a sell order"""
        if not self.state:
            return
            
        # Place order with reduce_only flag
        order = await self.order_manager.place_order(
            market=self.state.market,
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            amount=amount,
            price=price,
            reduce_only=True
        )
        
        if order:
            self.state.add_sell_order(order, corresponding_buy_id)
    
    async def _daily_reset_timer(self):
        """Timer to reset daily state at midnight UTC"""
        while not self._shutdown_requested:
            try:
                now = datetime.now(timezone.utc)
                
                # Calculate seconds until midnight UTC
                midnight = datetime.combine(
                    now.date() + timedelta(days=1),
                    time(0, 0, 0),
                    tzinfo=timezone.utc
                )
                seconds_until_midnight = (midnight - now).total_seconds()
                
                logger.info(f"Daily reset scheduled in {seconds_until_midnight/3600:.1f} hours")
                
                # Wait until midnight
                await asyncio.sleep(seconds_until_midnight)
                
                # Perform daily reset
                await self._perform_daily_reset()
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in daily reset timer: {e}", exc_info=True)
                await asyncio.sleep(3600)  # Wait an hour on error
    
    async def _perform_daily_reset(self):
        """Perform daily state reset"""
        if not self.state:
            return
            
        logger.info("Performing daily reset")
        
        # Update OHLC data
        await self._update_ohlc_data()
        
        # Reset state
        if self.state.yesterday_ohlc:
            self.state.reset_daily_state(self.state.yesterday_ohlc)
        
        # Generate new signals
        await self._generate_initial_signals()
        
        # Check if we should place orders
        await self._evaluate_buy_opportunity()
        
        logger.info("Daily reset complete")
    
    async def _heartbeat_loop(self):
        """Periodic safety checks (5 minutes)"""
        while not self._shutdown_requested:
            try:
                await asyncio.sleep(300)  # 5 minutes
                
                if self.state:
                    # Verify state consistency
                    await self._verify_state_consistency()
                    
                    # Log summary
                    self.state.log_state_summary()
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in heartbeat: {e}", exc_info=True)
    
    async def _verify_state_consistency(self):
        """Verify state matches exchange reality"""
        try:
            # Get actual position from exchange
            positions = await self.position_manager.get_positions()
            market_position = next((p for p in positions if p.market == self.state.market), None)
            
            # Compare with state
            state_size = self.state.total_position_size
            actual_size = market_position.size if market_position else 0
            
            if abs(state_size - actual_size) > 0.0001:  # Small tolerance for rounding
                logger.warning(f"State mismatch: state={state_size}, actual={actual_size}")
                self.state.update_position(market_position)
                
        except Exception as e:
            logger.error(f"Error verifying state: {e}")
    
    async def _get_current_price(self, market: str) -> float:
        """Get current market price"""
        ticker = await self.client.get_ticker(market)
        return float(ticker['last']) if ticker else 0.0
    
    async def _calculate_position_size(self) -> float:
        """Calculate position size for buy order"""
        # Use existing position sizer logic
        account_balance = await self.position_manager.get_account_balance()
        return self.position_sizer.calculate_position_size(
            self.state.market,
            account_balance,
            self.state.current_signals.buy_price if self.state.current_signals else 0
        )
    
    def _parse_order_data(self, data: Dict) -> Order:
        """Parse WebSocket order data into Order object"""
        # Implementation depends on exact WebSocket format
        # This is a placeholder
        return Order(
            exchange_order_id=str(data.get('order_id', '')),
            client_id=data.get('client_id', ''),
            market=data.get('market', ''),
            side=OrderSide(data.get('side', 'buy')),
            order_type=OrderType(data.get('type', 'limit')),
            price=float(data.get('price', 0)),
            amount=float(data.get('amount', 0)),
            filled=float(data.get('filled_amount', 0)),
            status=OrderStatus(data.get('status', 'pending')),
            created_at=datetime.fromtimestamp(data.get('created_at', 0) / 1000, tz=timezone.utc)
        )
    
    def _parse_position_data(self, data: Dict) -> Position:
        """Parse WebSocket position data into Position object"""
        return Position(
            position_id=data.get('position_id'),
            market=data.get('market', ''),
            side=PositionSide(data.get('side', 'long')),
            size=float(data.get('open_interest', 0)),
            avg_entry_price=float(data.get('avg_entry_price', 0)),
            unrealized_pnl=float(data.get('unrealized_pnl', 0)),
            realized_pnl=float(data.get('realized_pnl', 0)),
            margin_mode=data.get('margin_mode', 'cross'),
            leverage=int(float(data.get('leverage', 1))),
            liquidation_price=float(data.get('liq_price', 0)),
            bankruptcy_price=float(data.get('bkr_price', 0)),
            adl_level=data.get('adl_level', 0),
            created_at=datetime.fromtimestamp(data.get('created_at', 0) / 1000, tz=timezone.utc),
            updated_at=datetime.fromtimestamp(data.get('updated_at', 0) / 1000, tz=timezone.utc)
        )
    
    async def shutdown(self):
        """Shutdown the event-driven bot"""
        self._shutdown_requested = True
        
        # Cancel tasks
        if self.daily_reset_task:
            self.daily_reset_task.cancel()
        if self.heartbeat_task:
            self.heartbeat_task.cancel()
            
        # Call parent shutdown
        await super().shutdown()
        
        logger.info("Event-driven bot shutdown complete")