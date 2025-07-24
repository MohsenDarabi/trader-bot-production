"""
Daily Range Accumulation Trading Bot Core
Orchestrates all trading components and executes the strategy
"""
import asyncio
from datetime import datetime, timezone, time, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass

from src.exchange.coinex_client import CoinExClient
from src.exchange.order_manager import OrderManager
from src.exchange.websocket_client import CoinExWebSocketClient
from src.exchange.order_tracker import OrderTracker, OrderSide
from src.core.order_pairing_manager import OrderPairingManager
from src.core.position_manager import PositionManager
from src.core.profitability import ProfitabilityValidator
from src.data.market_data import MarketDataManager
from src.data.database import DatabaseManager
from src.core.strategy import DailyRangeStrategy, TradingSignal
from src.core.position_sizing import PositionSizer
from src.core.recovery import StartupRecovery
from src.utils.logger import get_logger
from config.settings import (
    SIGNAL_GENERATION_TIME, TIMEZONE, is_test_mode, 
    MIN_PROFIT_PERCENT, MAX_RANGE_DEVIATION
)


logger = get_logger(__name__)


@dataclass
class BotStatus:
    """Current bot status information"""
    is_running: bool = False
    last_signal_time: Optional[datetime] = None
    last_trade_time: Optional[datetime] = None
    active_markets: List[str] = None
    total_positions: int = 0
    total_orders: int = 0
    account_balance: float = 0.0
    daily_pnl: float = 0.0
    
    def __post_init__(self):
        if self.active_markets is None:
            self.active_markets = []


class DailyRangeBot:
    """
    Daily Range Accumulation Trading Bot
    
    Strategy:
    - Generate daily signals: Buy at (Low + Range), Sell at (High - Range)
    - Range = (Previous High - Previous Low) / 4
    - No stop losses - accumulate positions during downtrends
    - Exit when profitable (minimum 1.2% after fees)
    """
    
    def __init__(self):
        self.client: Optional[CoinExClient] = None
        self.database: Optional[DatabaseManager] = None
        self.market_data: Optional[MarketDataManager] = None
        self.strategy: Optional[DailyRangeStrategy] = None
        self.position_manager: Optional[PositionManager] = None
        self.order_manager: Optional[OrderManager] = None
        self.position_sizer: Optional[PositionSizer] = None
        self.recovery: Optional[StartupRecovery] = None
        
        # New WebSocket and order tracking components
        self.websocket_client: Optional[CoinExWebSocketClient] = None
        self.order_tracker: Optional[OrderTracker] = None
        self.pairing_manager: Optional[OrderPairingManager] = None
        
        self.status = BotStatus()
        self.trading_markets: List[str] = []
        self.last_signal_generation: Dict[str, datetime] = {}
        
        # Trading state
        self._shutdown_requested = False
        self._last_account_update = None
        self._last_positions_sync = None
        
    async def initialize(self):
        """Initialize all bot components"""
        logger.info("Initializing Daily Range Accumulation Bot...")
        
        try:
            # Initialize core components
            self.client = CoinExClient()
            self.database = DatabaseManager()
            self.market_data = MarketDataManager(self.client)
            self.strategy = DailyRangeStrategy(self.market_data)
            self.position_manager = PositionManager(self.client)
            
            # Initialize trading components
            validator = ProfitabilityValidator()
            self.order_manager = OrderManager(self.client, validator)
            self.position_sizer = PositionSizer(self.market_data, self.position_manager)
            self.recovery = StartupRecovery(self.client, self.database)
            
            # Initialize WebSocket and order tracking components
            self.websocket_client = CoinExWebSocketClient()
            self.order_tracker = OrderTracker(self.websocket_client, self.client)
            self.pairing_manager = OrderPairingManager(
                self.order_tracker, self.order_manager, self.client
            )
            
            # Initialize WebSocket connection using proven pattern from tests
            logger.info("Starting WebSocket connection for real-time order tracking...")
            
            # Connect and authenticate WebSocket (same pattern as working tests)
            connected = await self.websocket_client.connect()
            if not connected:
                raise Exception("Failed to connect WebSocket")
            logger.info("✓ WebSocket connected")
            
            # Authenticate
            authenticated = await self.websocket_client.authenticate()
            if not authenticated:
                raise Exception("Failed to authenticate WebSocket")
            logger.info("✓ WebSocket authenticated")
            
            # Subscribe to order and user deals updates
            await self.websocket_client.subscribe_orders()
            await self.websocket_client.subscribe_user_deals()
            logger.info("✓ Subscribed to WebSocket order tracking")
            
            # Perform startup recovery
            logger.info("Performing startup recovery...")
            recovery_report = self.recovery.perform_full_recovery(
                self.order_manager, self.position_manager, self.strategy
            )
            
            if not recovery_report.recovery_successful:
                raise Exception(f"Startup recovery failed: {recovery_report.issues}")
            
            logger.info(f"Recovery completed: {recovery_report}")
            
            # Update initial status
            await self._update_account_status()
            self.status.is_running = True
            
            logger.info("Bot initialization completed successfully")
            
        except Exception as e:
            logger.error(f"Bot initialization failed: {e}", exc_info=True)
            raise
    
    async def set_trading_market(self, market: str):
        """Set up a market for trading"""
        if market not in self.trading_markets:
            logger.info(f"Adding {market} to trading markets")
            self.trading_markets.append(market)
            self.status.active_markets.append(market)
            
            # Validate market and fetch initial data
            market_info = self.market_data.get_market_info(market)
            if not market_info:
                raise ValueError(f"Market {market} not found or not available")
            
            # Set leverage to strategy requirement (2x)
            try:
                from config.settings import LEVERAGE
                logger.info(f"Setting leverage for {market} to {int(LEVERAGE)}x")
                # adjust_position_leverage returns the data portion or raises exception on error
                leverage_data = self.client.adjust_position_leverage(
                    market=market,
                    leverage=int(LEVERAGE),
                    margin_mode='cross'
                )
                # If we reach here, leverage was set successfully
                logger.info(f"✅ Leverage configured for {market}: "
                          f"{leverage_data.get('leverage', int(LEVERAGE))}x "
                          f"{leverage_data.get('margin_mode', 'cross')} margin")
            except Exception as e:
                logger.error(f"Critical error setting leverage for {market}: {e}")
                raise ValueError(f"Cannot proceed without setting correct leverage: {e}")
            
            # Configure pairing rules for this market
            # Multiple sell levels based on the daily range strategy
            sell_price_levels = [1.002, 1.005, 1.010, 1.015, 1.020]  # 0.2%, 0.5%, 1%, 1.5%, 2% above buy price
            self.pairing_manager.configure_pairing_rule(
                market=market,
                sell_price_levels=sell_price_levels,
                max_age_minutes=120,  # 2 hours max age for unmatched fills
                min_fill_amount=0.001  # Minimum fill to trigger sell orders
            )
            
            logger.info(f"Configured pairing rules for {market} with {len(sell_price_levels)} sell levels")
            
            # Generate initial signal if needed
            await self._check_and_generate_signals(market)
    
    async def execute_trading_cycle(self):
        """Execute one complete trading cycle"""
        if self._shutdown_requested:
            return
        
        try:
            # Update account and positions periodically
            now = datetime.now(timezone.utc)
            if (not self._last_account_update or 
                (now - self._last_account_update).seconds > 60):
                await self._update_account_status()
                self._last_account_update = now
            
            if (not self._last_positions_sync or 
                (now - self._last_positions_sync).seconds > 120):  # Reduced from 30s to 2 minutes
                await self._sync_positions()
                self._last_positions_sync = now
            
            # Process unmatched buy fills periodically
            await self._process_pairing_tasks()
            
            # Process each trading market
            for market in self.trading_markets:
                await self._process_market(market)
            
            # Save current state
            self._save_current_state()
            
        except Exception as e:
            logger.error(f"Error in trading cycle: {e}", exc_info=True)
    
    async def _process_market(self, market: str):
        """Process trading logic for a specific market"""
        try:
            # Check if we need to generate new daily signals
            await self._check_and_generate_signals(market)
            
            # Get current signal
            signal = self.strategy.get_current_signal(market)
            if not signal:
                logger.debug(f"No active signal for {market}")
                return
            
            # Check for exit opportunities (profitable positions)
            await self._check_exit_opportunities(market, signal)
            
            # Check for entry opportunities (new signals)
            await self._check_entry_opportunities(market, signal)
            
        except Exception as e:
            logger.error(f"Error processing market {market}: {e}", exc_info=True)
    
    async def _check_and_generate_signals(self, market: str):
        """Check if we need to generate new daily signals"""
        now = datetime.now(timezone.utc)
        
        # Check if it's time to generate new signals (daily at configured time)
        signal_time = time.fromisoformat(SIGNAL_GENERATION_TIME)
        today_signal_time = datetime.combine(now.date(), signal_time, timezone.utc)
        
        # If it's past signal time and we haven't generated today's signal
        last_generation = self.last_signal_generation.get(market)
        if (now >= today_signal_time and 
            (not last_generation or last_generation.date() < now.date())):
            
            logger.info(f"Generating new daily signal for {market}")
            signal = self.strategy.generate_daily_signal(market)
            
            if signal:
                self.last_signal_generation[market] = now
                self.status.last_signal_time = now
                logger.info(f"Generated signal for {market}: Buy=${signal.buy_price:.2f}, Sell=${signal.sell_price:.2f}")
            else:
                logger.warning(f"Failed to generate signal for {market}")
    
    async def _check_exit_opportunities(self, market: str, signal: TradingSignal):
        """Check for profitable exit opportunities"""
        position = self.position_manager.get_position(market)
        
        if position:
            try:
                # Check if position is profitable for exit
                current_price = self.market_data.get_current_price(market)
                if not current_price:
                    return
                
                # Determine exit price based on position side
                exit_price = signal.sell_price if position.side.value == 'buy' else signal.buy_price
                
                # Validate profitability
                validator = ProfitabilityValidator()
                is_profitable = validator.is_position_profitable(
                    position.avg_entry_price, position.quantity, exit_price
                )
                
                if is_profitable.is_profitable:
                    logger.info(f"Profitable exit opportunity for {market} position: "
                              f"Entry=${position.avg_entry_price:.2f}, Exit=${exit_price:.2f}, "
                              f"Profit={is_profitable.profit_percent:.2f}%")
                    
                    # Place exit order
                    await self._place_exit_order(position, exit_price)
                
            except Exception as e:
                logger.error(f"Error checking exit for position {position.position_id}: {e}")
    
    async def _check_entry_opportunities(self, market: str, signal: TradingSignal):
        """Check for new entry opportunities"""
        try:
            current_price = self.market_data.get_current_price(market)
            if not current_price:
                return
            
            # Check buy signal
            if self._should_place_buy_order(market, signal, current_price):
                await self._place_entry_order(market, 'buy', signal.buy_price, signal)
            
            # Check sell signal (for short positions if supported)
            # Note: For now, focusing on long positions only as per strategy
            
        except Exception as e:
            logger.error(f"Error checking entry opportunities for {market}: {e}")
    
    def _should_place_buy_order(self, market: str, signal: TradingSignal, current_price: float) -> bool:
        """Determine if we should place a buy order"""
        # Don't place if price is too far from signal
        price_diff_percent = abs(current_price - signal.buy_price) / signal.buy_price * 100
        if price_diff_percent > MAX_RANGE_DEVIATION:
            logger.debug(f"Price too far from buy signal: {price_diff_percent:.2f}% deviation")
            return False
        
        # Check if we already have pending buy orders near this price
        active_orders = self.order_manager.get_pending_orders(market)
        for order in active_orders:
            if (order.side.value == 'buy' and 
                abs(order.price - signal.buy_price) / signal.buy_price < 0.01):  # Within 1%
                logger.debug(f"Buy order already exists near signal price")
                return False
        
        # Check position size limits and account balance
        account_balance = self.get_account_balance()
        position_size = self.position_sizer.calculate_position_size(
            market, signal.buy_price, account_balance
        )
        
        return position_size.is_valid
    
    async def _place_entry_order(self, market: str, side: str, price: float, signal: TradingSignal):
        """Place an entry order"""
        try:
            account_balance = self.get_account_balance()
            position_size = self.position_sizer.calculate_position_size(
                market, price, account_balance
            )
            
            if not position_size.is_valid:
                logger.warning(f"Cannot place {side} order for {market}: {position_size.reason}")
                return
            
            # Validate profitability before placing order
            validator = ProfitabilityValidator()
            exit_price = signal.sell_price if side == 'buy' else signal.buy_price
            
            is_profitable = validator.is_signal_profitable(price, exit_price, position_size.size_usdt)
            if not is_profitable.is_profitable:
                logger.warning(f"Order not profitable: {is_profitable.reason}")
                return
            
            # Place the order
            if side == 'buy':
                order = self.order_manager.place_buy_order(
                    market=market,
                    amount=position_size.quantity,
                    price=price,
                    position_size=position_size.size_usdt,
                    is_hide=True  # Hidden orders for production
                )
                
                # Track the buy order for automatic sell pairing
                if order:
                    self.order_tracker.track_order(
                        order_id=str(order.exchange_order_id),
                        client_id=order.client_id,
                        market=market,
                        side=OrderSide.BUY,
                        amount=position_size.quantity,
                        price=price
                    )
                    logger.info(f"Started tracking buy order {order.exchange_order_id} for automatic sell pairing")
            else:
                # For sell orders, use the traditional approach since this is for closing positions
                order = self.order_manager.place_sell_order(
                    market=market,
                    amount=position_size.quantity,
                    price=price,
                    position_size=position_size.size_usdt,
                    is_hide=True
                )
                
                # Track the sell order
                if order:
                    self.order_tracker.track_order(
                        order_id=str(order.exchange_order_id),
                        client_id=order.client_id,
                        market=market,
                        side=OrderSide.SELL,
                        amount=position_size.quantity,
                        price=price
                    )
            
            if order:
                logger.info(f"Placed {side} order for {market}: "
                          f"Price=${price:.2f}, Amount={position_size.quantity:.6f}, "
                          f"Size=${position_size.size_usdt:.2f}")
                self.status.last_trade_time = datetime.now(timezone.utc)
            else:
                logger.error(f"Failed to place {side} order for {market}")
                
        except Exception as e:
            logger.error(f"Error placing {side} order for {market}: {e}", exc_info=True)
    
    async def _place_exit_order(self, position, exit_price: float):
        """
        Place an exit order for a position
        NOTE: With the new pairing system, most sell orders are automatically created
        when buy orders fill. This method is kept for manual position closure if needed.
        """
        try:
            side = 'sell' if position.side.value == 'buy' else 'buy'
            
            # Check if we have automatic sell orders already in place for this position
            if side == 'sell':
                # Look for existing sell orders for this market
                existing_sells = [
                    order for order in self.order_tracker.tracked_orders.values()
                    if order.market == position.market and order.side == OrderSide.SELL
                    and order.status.value in ['pending', 'partial']
                ]
                
                if existing_sells:
                    total_sell_amount = sum(order.remaining_amount for order in existing_sells)
                    if total_sell_amount >= position.quantity * 0.95:  # Allow 5% tolerance
                        logger.info(f"Position {position.market} already has sufficient sell orders: "
                                  f"{total_sell_amount:.6f} >= {position.quantity:.6f}")
                        return
                
                # Place manual exit order if needed
                order = self.order_manager.place_sell_order(
                    market=position.market,
                    amount=position.quantity,
                    price=exit_price,
                    position_size=position.quantity * exit_price,
                    is_hide=True
                )
                
                # Track the manual exit order
                if order:
                    self.order_tracker.track_order(
                        order_id=str(order.exchange_order_id),
                        client_id=order.client_id,
                        market=position.market,
                        side=OrderSide.SELL,
                        amount=position.quantity,
                        price=exit_price
                    )
            else:
                # For buy orders (closing short positions - rare in this strategy)
                order = self.order_manager.place_buy_order(
                    market=position.market,
                    amount=position.quantity,
                    price=exit_price,
                    position_size=position.quantity * exit_price,
                    is_hide=True
                )
                
                if order:
                    self.order_tracker.track_order(
                        order_id=str(order.exchange_order_id),
                        client_id=order.client_id,
                        market=position.market,
                        side=OrderSide.BUY,
                        amount=position.quantity,
                        price=exit_price
                    )
            
            if order:
                logger.info(f"Placed manual exit order for {position.market}: "
                          f"Side={side}, Price=${exit_price:.2f}, Amount={position.quantity:.6f}")
            else:
                logger.error(f"Failed to place exit order for {position.market}")
                
        except Exception as e:
            logger.error(f"Error placing exit order: {e}", exc_info=True)
    
    async def _process_pairing_tasks(self):
        """Process pairing-related tasks periodically"""
        try:
            # Process unmatched buy fills
            unmatched_count = await self.pairing_manager.process_unmatched_fills()
            if unmatched_count > 0:
                logger.info(f"Processed {unmatched_count} unmatched buy fills")
            
            # Periodic emergency balance check (every 10 minutes)
            now = datetime.now(timezone.utc)
            if not hasattr(self, '_last_balance_check'):
                self._last_balance_check = now
            elif (now - self._last_balance_check).seconds > 600:  # 10 minutes
                imbalanced_markets = await self.pairing_manager.emergency_balance_check()
                if imbalanced_markets:
                    logger.error(f"CRITICAL: Position imbalances detected in markets: {imbalanced_markets}")
                self._last_balance_check = now
                
        except Exception as e:
            logger.error(f"Error in pairing tasks: {e}")
    
    async def _update_account_status(self):
        """Update account balance and status"""
        try:
            account_info = self.client.get_account_info()
            if isinstance(account_info, list):
                for asset in account_info:
                    if asset.get('ccy') == 'USDT':
                        self.status.account_balance = float(asset.get('available', 0))
                        break
            
            # Update position and order counts
            self.status.total_positions = len(self.position_manager.get_all_positions())
            self.status.total_orders = len(self.order_manager.get_pending_orders())
            
        except Exception as e:
            logger.error(f"Error updating account status: {e}")
    
    async def _sync_positions(self):
        """Sync positions with exchange"""
        try:
            self.position_manager.sync_with_exchange()
            
            # Sync existing orders with order tracker
            await self.order_tracker.sync_existing_orders()
            
        except Exception as e:
            logger.error(f"Error syncing with exchange: {e}")
    
    def get_pairing_status(self) -> Dict[str, Any]:
        """Get current pairing system status"""
        try:
            if not self.pairing_manager or not self.order_tracker:
                return {"error": "Pairing system not initialized"}
            
            pairing_stats = self.pairing_manager.get_pairing_statistics()
            tracker_stats = self.order_tracker.get_statistics()
            
            return {
                "pairing_enabled": pairing_stats["auto_pairing_enabled"],
                "websocket_connected": self.websocket_client.is_connected if self.websocket_client else False,
                "websocket_authenticated": self.websocket_client.is_authenticated if self.websocket_client else False,
                "total_tracked_orders": tracker_stats["total_orders"],
                "buy_orders": tracker_stats["buy_orders"],
                "sell_orders": tracker_stats["sell_orders"],
                "filled_orders": tracker_stats["filled_orders"],
                "order_pairs": tracker_stats["total_pairs"],
                "complete_pairs": tracker_stats["complete_pairs"],
                "unmatched_pairs": tracker_stats["unmatched_pairs"],
                "pairs_created": pairing_stats["total_pairs_created"],
                "sell_orders_placed": pairing_stats["total_sell_orders_placed"],
                "failed_pairings": pairing_stats["failed_pairings"],
                "success_rate": pairing_stats["success_rate"],
                "configured_markets": pairing_stats["configured_markets"]
            }
        except Exception as e:
            logger.error(f"Error getting pairing status: {e}")
            return {"error": str(e)}
    
    def _save_current_state(self):
        """Save current bot state to database"""
        try:
            self.recovery.save_current_state(
                self.order_manager, self.position_manager, self.strategy
            )
        except Exception as e:
            logger.error(f"Error saving state: {e}")
    
    # Public API methods for display
    
    def get_account_balance(self) -> float:
        """Get current account balance"""
        return self.status.account_balance
    
    def get_current_price(self, market: str) -> Optional[float]:
        """Get current price for a market"""
        try:
            return self.market_data.get_current_price(market)
        except:
            return None
    
    def get_current_signals(self, market: str) -> Optional[TradingSignal]:
        """Get current trading signals for a market"""
        return self.strategy.get_current_signal(market)
    
    def get_open_positions(self) -> List:
        """Get all open positions"""
        return self.position_manager.get_all_positions()
    
    def get_active_orders(self) -> List:
        """Get all active orders"""
        return self.order_manager.get_pending_orders()
    
    def get_status(self) -> BotStatus:
        """Get current bot status"""
        return self.status
    
    async def shutdown(self):
        """Gracefully shutdown the bot"""
        logger.info("Shutting down bot...")
        self._shutdown_requested = True
        self.status.is_running = False
        
        # Save final state
        self._save_current_state()
        
        # Close connections
        if self.client:
            self.client.close()
        
        logger.info("Bot shutdown completed")