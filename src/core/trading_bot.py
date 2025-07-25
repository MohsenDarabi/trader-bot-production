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
from src.data.websocket_market_data import WebSocketMarketDataProvider
from src.core.strategy import DailyRangeStrategy, TradingSignal
from src.core.position_sizing import PositionSizer
from src.utils.logger import get_logger
from src.utils.smart_logging import log_trading_event, force_log_summaries
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
        self.market_data: Optional[MarketDataManager] = None
        self.strategy: Optional[DailyRangeStrategy] = None
        self.position_manager: Optional[PositionManager] = None
        self.order_manager: Optional[OrderManager] = None
        self.position_sizer: Optional[PositionSizer] = None
        
        # WebSocket and real-time data components
        self.websocket_client: Optional[CoinExWebSocketClient] = None
        self.websocket_market_data: Optional[WebSocketMarketDataProvider] = None
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
            
            # Initialize WebSocket market data provider
            self.websocket_market_data = WebSocketMarketDataProvider()
            
            # Initialize market data manager with WebSocket support
            self.market_data = MarketDataManager(self.client, self.websocket_market_data)
            self.strategy = DailyRangeStrategy(self.market_data)
            self.position_manager = PositionManager(self.client)
            
            # Initialize trading components
            validator = ProfitabilityValidator()
            self.order_manager = OrderManager(self.client, validator)
            self.position_sizer = PositionSizer(self.market_data, self.position_manager)
            
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
            
            # Register WebSocket message handlers
            self.websocket_client.register_handler(
                "state.update", 
                self.websocket_market_data.handle_state_update
            )
            self.websocket_client.register_handler(
                "balance.update",
                self._handle_balance_update
            )
            self.websocket_client.register_handler(
                "position.update",
                self._handle_position_update
            )
            
            # Subscribe to order and user deals updates
            await self.websocket_client.subscribe_orders()
            await self.websocket_client.subscribe_user_deals()
            logger.info("✓ Subscribed to WebSocket order tracking")
            
            # Subscribe to market state updates for real-time price data
            await self.websocket_client.subscribe_market_state()
            logger.info("✓ Subscribed to WebSocket market state updates")
            
            # Subscribe to balance updates for real-time account balance
            await self.websocket_client.subscribe_balance(["USDT"])
            logger.info("✓ Subscribed to WebSocket balance updates")
            
            # Subscribe to position updates for real-time position tracking
            await self.websocket_client.subscribe_positions()
            logger.info("✓ Subscribed to WebSocket position updates")
            
            # Load current state from exchange (no database)
            logger.info("Loading current state from exchange...")
            await self._initialize_from_exchange()
            logger.info("✓ Exchange state loaded successfully")
            
            # Update initial status
            await self._update_account_status()
            self.status.is_running = True
            
            logger.info("Bot initialization completed successfully")
            
        except Exception as e:
            logger.error(f"Bot initialization failed: {e}", exc_info=True)
            raise
    
    async def _initialize_from_exchange(self):
        """Initialize bot state from exchange API (stateless approach)"""
        try:
            logger.info("Getting current orders from exchange...")
            # Load existing orders from exchange 
            loaded_orders = self.order_manager.load_existing_orders()
            logger.info(f"Loaded {loaded_orders} existing orders from exchange")
            
            logger.info("Getting current positions from exchange...")  
            # Sync positions with exchange
            self.position_manager.sync_with_exchange()
            positions = self.position_manager.get_all_positions()
            logger.info(f"Loaded {len(positions)} positions from exchange")
            
            # Sync order tracker with current exchange state
            logger.info("Syncing order tracker...")
            await self.order_tracker.sync_existing_orders()
            
            logger.info("Exchange state initialization completed")
            
        except Exception as e:
            logger.error(f"Failed to initialize from exchange: {e}")
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
            
            # Set leverage to strategy requirement (2x) with intelligent conflict resolution
            try:
                from config.settings import LEVERAGE
                target_leverage = int(LEVERAGE)
                margin_mode = 'cross'
                
                logger.info(f"🔧 Configuring leverage for {market} to {target_leverage}x {margin_mode}")
                
                # First, try direct leverage adjustment
                try:
                    leverage_data = self.client.adjust_position_leverage(
                        market=market,
                        leverage=target_leverage,
                        margin_mode=margin_mode
                    )
                    
                    # If we reach here, leverage was set successfully
                    logger.info(f"✅ Leverage configured for {market}: "
                              f"{leverage_data.get('leverage', target_leverage)}x "
                              f"{leverage_data.get('margin_mode', margin_mode)} margin")
                    
                except Exception as direct_error:
                    # Check if it's an "order exist" error that we can handle intelligently
                    if "order exist" in str(direct_error).lower():
                        logger.warning(f"⚠️ Order conflict detected for {market}: {direct_error}")
                        logger.info("🧠 Using intelligent leverage conflict resolution")
                        
                        # Use intelligent conflict resolution
                        resolution_result = self.client.handle_leverage_conflict_intelligently(
                            market=market,
                            target_leverage=target_leverage,
                            margin_mode=margin_mode
                        )
                        
                        if resolution_result['success']:
                            logger.info(f"✅ Leverage conflict resolved for {market}: {resolution_result['message']}")
                            
                            # Check for critical bug detection
                            if resolution_result.get('bug_detected'):
                                logger.error("🚨 CRITICAL BUG DETECTED AND FIXED!")
                                logger.error("⚠️ Multiple buy orders found - this violates Daily Range Strategy!")
                                logger.error("🔍 Please investigate order management logic immediately!")
                            
                        else:
                            # Intelligent resolution failed, but we can continue
                            logger.warning(f"⚠️ Leverage conflict resolution failed for {market}")
                            logger.warning(f"Message: {resolution_result['message']}")
                            logger.info("🤖 Bot will continue with existing leverage settings")
                            
                            # Don't crash the bot - just log the issue
                            if resolution_result.get('bug_detected'):
                                logger.error("🚨 CRITICAL BUG DETECTED but cleanup failed!")
                                logger.error("⚠️ Manual intervention required to fix order state!")
                    
                    elif "service too busy" in str(direct_error).lower():
                        # Handle service busy errors as before
                        logger.error(f"⚠️ CoinEx API is consistently busy for {market}: {direct_error}")
                        logger.error("The bot will attempt to continue, but leverage may not be optimal.")
                        logger.error("Please monitor positions closely and manually set leverage if needed.")
                        
                        raise ValueError(f"Failed to set leverage for {market} after retries. "
                                       f"API consistently busy: {direct_error}")
                    else:
                        # Other errors - re-raise
                        raise direct_error
                        
            except Exception as e:
                # Final fallback error handling
                logger.error(f"Critical error configuring leverage for {market}: {e}")
                raise ValueError(f"Cannot proceed without resolving leverage configuration: {e}")
            
            # Configure pairing rules for this market
            # Daily Range Strategy: One sell price per buy order (signal.sell_price)
            self.pairing_manager.configure_pairing_rule(
                market=market,
                sell_price_levels=[],  # Will use signal.sell_price directly for each buy-sell pair
                max_age_minutes=120,  # 2 hours max age for unmatched fills
                min_fill_amount=0.001  # Minimum fill to trigger sell orders
            )
            
            logger.info(f"Configured pairing rules for {market} with Daily Range Strategy (one sell price per buy)")
            
            # Generate initial signal if needed
            await self._check_and_generate_signals(market)
    
    async def execute_trading_cycle(self):
        """Execute one complete trading cycle"""
        if self._shutdown_requested:
            return
        
        try:
            # Update account and positions periodically
            now = datetime.now(timezone.utc)
            # Reduced account update frequency from 60s to 300s (5 min) since we have WebSocket balance updates
            if (not self._last_account_update or 
                (now - self._last_account_update).seconds > 300):
                await self._update_account_status()
                self._last_account_update = now
            
            # Reduced position sync frequency from 120s to 600s (10 min) since we have WebSocket position updates
            if (not self._last_positions_sync or 
                (now - self._last_positions_sync).seconds > 600):
                await self._sync_positions()
                self._last_positions_sync = now
            
            # Process unmatched buy fills periodically
            await self._process_pairing_tasks()
            
            # Process each trading market
            for market in self.trading_markets:
                await self._process_market(market)
            
            # State is maintained in memory only (no database persistence)
            
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
                entry_cost = position.total_cost  # Use position's total cost
                is_profitable = validator.is_position_profitable(
                    position.avg_entry_price, position.size, exit_price, entry_cost
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
        """Check for new entry opportunities and manage existing positions"""
        try:
            current_price = self.market_data.get_current_price(market)
            if not current_price:
                return
            
            # First priority: Check if we have existing position that needs sell orders
            balance = self._calculate_position_sell_balance(market)
            if balance['position_exists']:
                logger.info(f"🎯 Found existing position in {market}: {balance['position_size']:.6f}")
                
                if not balance['is_balanced']:
                    logger.info(f"⚠️ Position needs sell orders: missing {balance['missing_sell']:.6f}")
                    # Place missing sell order for existing position
                    if self._place_missing_sell_order(market, balance['missing_sell']):
                        logger.info(f"✅ Placed missing sell order for existing position")
                    else:
                        logger.error(f"❌ Failed to place missing sell order")
                else:
                    logger.info(f"✅ Position properly balanced with {len(balance['sell_orders'])} sell orders")
                
                # With existing position, no new buy orders should be placed
                # The _should_place_buy_order method will prevent this anyway
                return
            
            # Second priority: Check for new buy opportunities (only if no position exists)
            if self._should_place_buy_order(market, signal, current_price):
                logger.info(f"🚀 Placing new buy order for {market}")
                await self._place_entry_order(market, 'buy', signal.buy_price, signal)
            else:
                logger.debug(f"⏸️ No buy opportunity for {market} at current conditions")
            
        except Exception as e:
            logger.error(f"Error checking entry opportunities for {market}: {e}")
    
    def _check_daily_buy_status(self, market: str) -> Dict[str, Any]:
        """Check buy order status for current day with stale order cleanup"""
        try:
            # Get all pending buy orders using proven endpoint
            pending_orders = self.order_manager.get_pending_orders(market)
            buy_orders = [o for o in pending_orders if o.side.value == 'buy']
            
            today = datetime.now(timezone.utc).date()
            today_buy_orders = []
            stale_orders = []
            
            # Categorize orders by date
            for order in buy_orders:
                order_date = order.created_at.date()
                if order_date == today:
                    today_buy_orders.append(order)
                elif order_date < today:
                    stale_orders.append(order)  # Older than 1 day
            
            # Cancel stale buy orders using proven cancel method
            cancelled_count = 0
            for stale_order in stale_orders:
                try:
                    logger.warning(f"Cancelling stale buy order: {stale_order.client_id} from {stale_order.created_at.date()}")
                    self.order_manager.cancel_order(stale_order.client_id)
                    cancelled_count += 1
                except Exception as e:
                    logger.error(f"Failed to cancel stale order {stale_order.client_id}: {e}")
            
            result = {
                'has_today_buy': len(today_buy_orders) > 0,
                'today_orders': today_buy_orders,
                'cancelled_stale': cancelled_count,
                'total_buy_orders': len(buy_orders)
            }
            
            if result['has_today_buy']:
                logger.info(f"Found {len(today_buy_orders)} buy order(s) from today for {market}")
            
            if cancelled_count > 0:
                logger.info(f"Cancelled {cancelled_count} stale buy order(s) for {market}")
            
            return result
            
        except Exception as e:
            logger.error(f"Error checking daily buy status for {market}: {e}")
            return {
                'has_today_buy': True,  # Conservative: assume we have buy to prevent multiple orders
                'today_orders': [],
                'cancelled_stale': 0,
                'total_buy_orders': 0
            }
    
    def _calculate_position_sell_balance(self, market: str) -> Dict[str, Any]:
        """Calculate position vs sell order balance using proven endpoints"""
        try:
            # Get current position using proven method
            position = self.position_manager.get_position(market)
            position_size = position.size if position else 0.0
            
            # Get all pending sell orders using proven method
            pending_orders = self.order_manager.get_pending_orders(market)
            sell_orders = [o for o in pending_orders if o.side.value == 'sell']
            total_sell_amount = sum(order.amount for order in sell_orders)
            
            # Calculate missing sell amount
            missing_sell = max(0, position_size - total_sell_amount)
            is_balanced = missing_sell < 0.000001  # Allow tiny rounding differences
            
            result = {
                'position_size': position_size,
                'total_sells': total_sell_amount,
                'missing_sell': missing_sell,
                'is_balanced': is_balanced,
                'sell_orders': sell_orders,
                'position_exists': position is not None
            }
            
            if position_size > 0:
                logger.debug(f"Position balance for {market}: {position_size:.6f} position, "
                           f"{total_sell_amount:.6f} sells, {missing_sell:.6f} missing")
            
            return result
            
        except Exception as e:
            logger.error(f"Error calculating position-sell balance for {market}: {e}")
            return {
                'position_size': 0.0,
                'total_sells': 0.0,
                'missing_sell': 0.0,
                'is_balanced': True,
                'sell_orders': [],
                'position_exists': False
            }
    
    def _calculate_optimal_sell_price(self, market: str, position, current_price: float) -> Dict[str, float]:
        """Calculate optimal sell price for bot restart scenario"""
        try:
            # Calculate base sell price (1.5% profit minimum)
            base_sell_price = position.avg_entry_price * 1.015
            
            # If current market price is higher, use current price for more profit
            if current_price > base_sell_price:
                optimal_price = current_price
                improvement = current_price - base_sell_price
                reason = "market_higher_than_calculated"
            else:
                optimal_price = base_sell_price
                improvement = 0
                reason = "minimum_profit_1.5%"
            
            return {
                'price': optimal_price,
                'base_price': base_sell_price,
                'improvement': improvement,
                'reason': reason
            }
        except Exception as e:
            logger.error(f"Error calculating optimal sell price for {market}: {e}")
            fallback_price = position.avg_entry_price * 1.015
            return {
                'price': fallback_price,
                'base_price': fallback_price,
                'improvement': 0,
                'reason': 'fallback_error'
            }
    
    def _place_missing_sell_order(self, market: str, missing_amount: float) -> bool:
        """Place sell order for missing position coverage with intelligent price adjustment"""
        try:
            # Get position for entry price reference
            position = self.position_manager.get_position(market)
            if not position:
                logger.error(f"Cannot place missing sell - no position found for {market}")
                return False
            
            # Get current market price for intelligent adjustment
            current_price = self.market_data.get_current_price(market)
            if not current_price:
                logger.error(f"Cannot get current price for {market} - using fallback pricing")
                current_price = position.avg_entry_price
            
            # Calculate intelligent sell price
            optimal_sell_price = self._calculate_optimal_sell_price(market, position, current_price)
            
            logger.info(f"💡 Bot restart sell pricing for {market}:")
            logger.info(f"📊 Entry: ${position.avg_entry_price:.2f} | Current: ${current_price:.2f}")
            logger.info(f"📊 Base sell (1.5%): ${optimal_sell_price['base_price']:.2f}")
            logger.info(f"✅ Optimal sell price: ${optimal_sell_price['price']:.2f} ({optimal_sell_price['reason']})")
            if optimal_sell_price['improvement'] > 0:
                logger.info(f"💰 Profit improvement: +${optimal_sell_price['improvement']:.2f}")
            
            # Place the missing sell order using optimal price
            order = self.order_manager.place_sell_order(
                market=market,
                amount=missing_amount,
                price=optimal_sell_price['price'],
                position_size=missing_amount * optimal_sell_price['price'],
                is_hide=True
            )
            
            if order:
                logger.info(f"✅ Missing sell order placed: {order.client_id}")
                # Track the order for automatic pairing
                if self.order_tracker:
                    self.order_tracker.track_order(
                        order_id=str(order.exchange_order_id),
                        client_id=order.client_id,
                        market=market,
                        side=OrderSide.SELL,
                        amount=missing_amount,
                        price=sell_price
                    )
                return True
            else:
                logger.error(f"❌ Failed to place missing sell order for {market}")
                return False
                
        except Exception as e:
            logger.error(f"Error placing missing sell order for {market}: {e}")
            return False
    
    def _should_place_buy_order(self, market: str, signal: TradingSignal, current_price: float) -> bool:
        """Enhanced buy order decision with comprehensive strategy validation"""
        
        # Phase 1: Daily buy order check with stale cleanup
        logger.info(f"🔍 Phase 1: Checking daily buy status for {market}")
        buy_status = self._check_daily_buy_status(market)
        if buy_status['has_today_buy']:
            logger.info(f"❌ Cannot place buy - already have {len(buy_status['today_orders'])} buy order(s) today for {market}")
            return False
        
        # Phase 2: CRITICAL - Check if we have existing position from today's trading
        logger.info(f"🔍 Phase 2: Checking for existing position that prevents new buy orders")
        balance = self._calculate_position_sell_balance(market)
        
        if balance['position_exists']:
            logger.info(f"⚠️ FOUND EXISTING POSITION: {balance['position_size']:.6f} {market}")
            
            # STRATEGY RULE: Only ONE buy order per day per market
            # If position exists, we should NEVER place another buy order the same day
            # Instead, ensure the position has proper sell orders
            
            if not balance['is_balanced']:
                # Position exists but not balanced - place missing sell order
                logger.info(f"🔧 Position unbalanced: {balance['position_size']:.6f} position vs {balance['total_sells']:.6f} sells")
                logger.info(f"🔧 Placing missing sell order: {balance['missing_sell']:.6f}")
                
                if self._place_missing_sell_order(market, balance['missing_sell']):
                    logger.info(f"✅ Missing sell order placed for existing position")
                else:
                    logger.error(f"❌ Failed to place missing sell order")
            else:
                logger.info(f"✅ Position is properly balanced with sell orders")
            
            # NEVER place buy order when position exists - this is the core strategy rule
            logger.info(f"❌ Cannot place buy - position already exists from today's trading")
            logger.info(f"💡 Strategy: Maximum one buy order per day per market")
            return False
        
        # Phase 3: Price validation
        logger.info(f"🔍 Phase 3: Price validation for {market}")
        price_diff_percent = abs(current_price - signal.buy_price) / signal.buy_price * 100
        if price_diff_percent > MAX_RANGE_DEVIATION:
            logger.info(f"❌ Cannot place buy - price too far from signal: {price_diff_percent:.2f}% deviation (max: {MAX_RANGE_DEVIATION}%)")
            logger.info(f"💡 Current: ${current_price:.2f}, Signal: ${signal.buy_price:.2f}")
            return False
        
        # Phase 4: Account balance and position sizing
        logger.info(f"🔍 Phase 4: Account balance and position sizing for {market}")
        account_balance = self.get_account_balance()
        position_size = self.position_sizer.calculate_position_size(
            market, signal.buy_price, account_balance
        )
        
        if not position_size.is_valid:
            logger.info(f"❌ Cannot place buy - position sizing invalid: {position_size.reason}")
            return False
        
        # All checks passed - no existing position, no pending buy orders
        logger.info(f"✅ All checks passed - ready to place buy order for {market}")
        logger.info(f"💰 Order details: ${signal.buy_price:.2f} x {position_size.quantity:.6f} = ${position_size.size_usdt:.2f}")
        return True
    
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
            
            # Apply price adjustments for better entries/exits
            adjusted_price = price
            if side == 'buy':
                # Buy price adjustment: Use cheaper price for better entry
                current_price = self.market_data.get_current_price(market)
                if current_price and current_price < price:
                    adjusted_price = current_price
                    logger.info(f"💰 Buy price adjusted: ${price:.2f} → ${adjusted_price:.2f} (cheaper entry)")
                else:
                    logger.info(f"📊 Buy price unchanged: ${price:.2f} (signal price optimal)")
            
            # Place the order
            if side == 'buy':
                order = self.order_manager.place_buy_order(
                    market=market,
                    amount=position_size.quantity,
                    price=adjusted_price,
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
                        price=adjusted_price
                    )
                    logger.info(f"Started tracking buy order {order.exchange_order_id} for automatic sell pairing")
            else:
                # For sell orders, use the traditional approach since this is for closing positions
                # Note: Normal sell orders don't get price adjustment (as discussed)
                order = self.order_manager.place_sell_order(
                    market=market,
                    amount=position_size.quantity,
                    price=adjusted_price,
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
                        price=adjusted_price
                    )
            
            if order:
                logger.info(f"Placed {side} order for {market}: "
                          f"Price=${adjusted_price:.2f}, Amount={position_size.quantity:.6f}, "
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
                    if total_sell_amount >= position.size * 0.95:  # Allow 5% tolerance
                        logger.info(f"Position {position.market} already has sufficient sell orders: "
                                  f"{total_sell_amount:.6f} >= {position.size:.6f}")
                        return
                
                # Place manual exit order if needed
                order = self.order_manager.place_sell_order(
                    market=position.market,
                    amount=position.size,
                    price=exit_price,
                    position_size=position.size * exit_price,
                    is_hide=True
                )
                
                # Track the manual exit order
                if order:
                    self.order_tracker.track_order(
                        order_id=str(order.exchange_order_id),
                        client_id=order.client_id,
                        market=position.market,
                        side=OrderSide.SELL,
                        amount=position.size,
                        price=exit_price
                    )
            else:
                # For buy orders (closing short positions - rare in this strategy)
                order = self.order_manager.place_buy_order(
                    market=position.market,
                    amount=position.size,
                    price=exit_price,
                    position_size=position.size * exit_price,
                    is_hide=True
                )
                
                if order:
                    self.order_tracker.track_order(
                        order_id=str(order.exchange_order_id),
                        client_id=order.client_id,
                        market=position.market,
                        side=OrderSide.BUY,
                        amount=position.size,
                        price=exit_price
                    )
            
            if order:
                logger.info(f"Placed manual exit order for {position.market}: "
                          f"Side={side}, Price=${exit_price:.2f}, Amount={position.size:.6f}")
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
    
    def _handle_balance_update(self, data: Dict[str, Any]):
        """
        Handle balance.update message from WebSocket
        
        Args:
            data: WebSocket message data containing balance_list
        """
        try:
            balance_list = data.get("balance_list", [])
            if not balance_list:
                logger.warning("Received empty balance_list in balance.update")
                return
            
            for balance_data in balance_list:
                ccy = balance_data.get("ccy")
                if ccy == "USDT":
                    try:
                        # Extract balance information
                        available = float(balance_data.get("available", 0))
                        frozen = float(balance_data.get("frozen", 0))
                        margin = float(balance_data.get("margin", 0))
                        unrealized_pnl = float(balance_data.get("unrealized_pnl", 0))
                        equity = float(balance_data.get("equity", 0))
                        
                        # Update bot status with new balance
                        old_balance = self.status.account_balance
                        self.status.account_balance = available
                        
                        # Log significant balance changes
                        if old_balance > 0:
                            balance_change = available - old_balance
                            if abs(balance_change) > 0.01:  # Log changes > $0.01
                                logger.info(
                                    f"💰 Balance updated: ${old_balance:.2f} → ${available:.2f} "
                                    f"(change: {balance_change:+.2f}, PnL: {unrealized_pnl:+.2f})"
                                )
                        else:
                            logger.info(f"💰 Initial balance received: ${available:.2f}")
                        
                        # Store additional balance info for monitoring
                        if not hasattr(self.status, 'balance_details'):
                            self.status.balance_details = {}
                        
                        self.status.balance_details = {
                            "available": available,
                            "frozen": frozen,
                            "margin": margin,
                            "unrealized_pnl": unrealized_pnl,
                            "equity": equity,
                            "last_update": datetime.now(timezone.utc)
                        }
                        
                        logger.debug(
                            f"Balance details updated: Available=${available:.2f}, "
                            f"Frozen=${frozen:.2f}, Margin=${margin:.2f}, "
                            f"PnL={unrealized_pnl:+.2f}, Equity=${equity:.2f}"
                        )
                        
                        break  # We only care about USDT balance
                        
                    except (ValueError, TypeError) as e:
                        logger.error(f"Error parsing balance data for {ccy}: {e}")
                        continue
            
            logger.debug(f"Processed balance update with {len(balance_list)} currencies")
            
        except Exception as e:
            logger.error(f"Error handling balance update: {e}")
    
    def _handle_position_update(self, data: Dict[str, Any]):
        """
        Handle position.update message from WebSocket
        
        Args:
            data: WebSocket message data containing position info
        """
        try:
            event = data.get("event")
            position_data = data.get("position", {})
            
            if not position_data:
                logger.warning("Received empty position data in position.update")
                return
            
            market = position_data.get("market")
            if not market:
                logger.warning("Received position update without market name")
                return
            
            try:
                # Extract position information
                position_id = int(position_data.get("position_id", 0))
                side = position_data.get("side", "long")  # 'long' or 'short'
                open_interest = float(position_data.get("open_interest", 0))
                avg_entry_price = float(position_data.get("avg_entry_price", 0))
                unrealized_pnl = float(position_data.get("unrealized_pnl", 0))
                liq_price = float(position_data.get("liq_price", 0))
                
                # Update position in position manager
                from src.core.position_manager import PositionSide, Position
                
                if open_interest > 0:
                    # Position exists or was updated
                    pos_side = PositionSide.LONG if side == "long" else PositionSide.SHORT
                    
                    # Create or update position
                    position = Position(
                        position_id=position_id,
                        market=market,
                        side=pos_side,
                        size=open_interest,
                        avg_entry_price=avg_entry_price,
                        total_cost=open_interest * avg_entry_price,  # Approximation
                        unrealized_pnl=unrealized_pnl,
                        liquidation_price=liq_price,
                        created_at=datetime.now(timezone.utc),
                        updated_at=datetime.now(timezone.utc)
                    )
                    
                    # Update position manager
                    old_position = self.position_manager.positions.get(market)
                    self.position_manager.positions[market] = position
                    
                    # Log position changes
                    if old_position:
                        size_change = open_interest - old_position.size
                        pnl_change = unrealized_pnl - old_position.unrealized_pnl
                        if abs(size_change) > 0.000001 or abs(pnl_change) > 0.01:
                            logger.info(
                                f"📊 Position updated {market}: Size {old_position.size:.6f} → {open_interest:.6f} "
                                f"({size_change:+.6f}), PnL {old_position.unrealized_pnl:+.2f} → {unrealized_pnl:+.2f} "
                                f"({pnl_change:+.2f}), Entry: ${avg_entry_price:.2f}"
                            )
                    else:
                        logger.info(
                            f"📊 New position {market}: {side.upper()} {open_interest:.6f} @ ${avg_entry_price:.2f}, "
                            f"PnL: {unrealized_pnl:+.2f}, Liq: ${liq_price:.2f}"
                        )
                        
                else:
                    # Position was closed
                    if market in self.position_manager.positions:
                        old_position = self.position_manager.positions[market]
                        del self.position_manager.positions[market]
                        logger.info(
                            f"📊 Position closed {market}: {old_position.side.value.upper()} "
                            f"{old_position.size:.6f} @ ${old_position.avg_entry_price:.2f}, "
                            f"Final PnL: {unrealized_pnl:+.2f}"
                        )
                
                # Update bot status
                self.status.total_positions = len(self.position_manager.get_all_positions())
                
                logger.debug(f"Processed position update for {market}: event={event}, size={open_interest:.6f}")
                
            except (ValueError, TypeError) as e:
                logger.error(f"Error parsing position data for {market}: {e}")
                return
            
        except Exception as e:
            logger.error(f"Error handling position update: {e}")
    
    async def _update_account_status(self):
        """Update account balance and status (fallback for WebSocket)"""
        try:
            # Only update balance via HTTP if WebSocket data is stale or missing
            update_balance_via_http = True
            
            if hasattr(self.status, 'balance_details') and self.status.balance_details:
                last_ws_update = self.status.balance_details.get('last_update')
                if last_ws_update:
                    age = (datetime.now(timezone.utc) - last_ws_update).total_seconds()
                    if age < 120:  # WebSocket data is fresh (< 2 minutes)
                        update_balance_via_http = False
                        logger.debug("Using WebSocket balance data (HTTP fallback skipped)")
            
            if update_balance_via_http:
                logger.info("Updating balance via HTTP (WebSocket data stale or missing)")
                account_info = self.client.get_account_info()
                if isinstance(account_info, list):
                    for asset in account_info:
                        if asset.get('ccy') == 'USDT':
                            self.status.account_balance = float(asset.get('available', 0))
                            logger.info(f"HTTP balance update: ${self.status.account_balance:.2f}")
                            break
            
            # Always update position and order counts (these are still HTTP-based)
            self.status.total_positions = len(self.position_manager.get_all_positions())
            self.status.total_orders = len(self.order_manager.get_pending_orders())
            
        except Exception as e:
            logger.error(f"Error updating account status: {e}")
    
    async def _sync_positions(self):
        """Sync positions with exchange (periodic fallback for WebSocket)"""
        try:
            # Log that we're doing periodic sync - should be rare with WebSocket
            logger.info("Performing periodic position sync via HTTP (WebSocket fallback)")
            
            self.position_manager.sync_with_exchange()
            
            # Sync existing orders with order tracker
            await self.order_tracker.sync_existing_orders()
            
            logger.info(f"Position sync completed: {len(self.position_manager.get_all_positions())} positions")
            
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
    
    # Note: State persistence removed - using stateless approach
    
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
    
    def get_websocket_stats(self) -> Dict[str, Any]:
        """Get WebSocket and data source statistics"""
        stats = {}
        
        # Market data statistics
        if self.market_data:
            stats["market_data"] = self.market_data.get_data_source_stats()
        
        # WebSocket market data cache statistics  
        if self.websocket_market_data:
            stats["websocket_cache"] = self.websocket_market_data.get_cache_stats()
        
        # Pairing statistics
        if self.pairing_manager and self.order_tracker:
            pairing_stats = self.pairing_manager.get_pairing_statistics()
            tracker_stats = self.order_tracker.get_statistics()
            stats["order_tracking"] = {
                **pairing_stats,
                **tracker_stats
            }
        
        # WebSocket connection status
        if self.websocket_client:
            stats["websocket_connection"] = {
                "connected": self.websocket_client.is_connected,
                "authenticated": self.websocket_client.is_authenticated,
                "subscriptions": list(self.websocket_client.subscriptions.keys())
            }
        
        return stats
    
    async def shutdown(self):
        """Gracefully shutdown the bot"""
        logger.info("Shutting down bot...")
        self._shutdown_requested = True
        self.status.is_running = False
        
        # Final state is not persisted (stateless approach)
        
        # Close connections
        if self.client:
            self.client.close()
        
        logger.info("Bot shutdown completed")