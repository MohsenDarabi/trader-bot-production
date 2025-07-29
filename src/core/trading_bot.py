"""
Daily Range Accumulation Trading Bot Core
Orchestrates all trading components and executes the strategy
"""
import asyncio
from datetime import datetime, timezone, time, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass

from src.exchange.coinex_client import CoinExClient
from src.exchange.order_manager import OrderManager, OrderStatus
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


class TradingCircuitBreaker:
    """Enhanced circuit breaker with priority levels and dynamic cooldowns"""
    
    def __init__(self, default_cooldown: int = 30):
        """
        Initialize enhanced circuit breaker
        
        Args:
            default_cooldown: Default cooldown period in seconds
        """
        self._last_actions = {}  # f"{market}_{action}" -> timestamp
        self._default_cooldown = default_cooldown
        
        # Priority-based cooldown periods (seconds)
        self._action_cooldowns = {
            # Critical operations - shortest cooldowns
            "emergency_sell": 5,      # Emergency position closure
            "position_closure": 10,   # Manual position closure
            
            # Normal operations - standard cooldowns  
            "buy_order": 30,          # Buy order placement
            "sell_order": 30,         # Sell order placement
            "cleanup": 45,            # Order cleanup operations
            
            # Administrative operations - longer cooldowns
            "sync": 60,               # State synchronization
            "validation": 60          # Manual validation
        }
        
    def should_allow_action(self, market: str, action_type: str, priority: str = "normal") -> bool:
        """
        Check if action should be allowed with priority-based cooldowns
        
        Args:
            market: Market symbol
            action_type: Type of action
            priority: Priority level ('emergency', 'high', 'normal', 'low')
            
        Returns:
            True if action is allowed, False if in cooldown period
        """
        action_key = f"{market}_{action_type}"
        now = datetime.now(timezone.utc)
        
        # Determine cooldown period based on action type and priority
        cooldown_period = self._get_cooldown_period(action_type, priority)
        
        last_time = self._last_actions.get(action_key)
        if last_time:
            elapsed = (now - last_time).total_seconds()
            if elapsed < cooldown_period:
                if priority == "emergency":
                    logger.warning(f"🚨 EMERGENCY: Allowing {action_type} for {market} despite {elapsed:.1f}s < {cooldown_period}s cooldown")
                else:
                    logger.info(f"🚧 Circuit breaker: {action_type} for {market} blocked - {elapsed:.1f}s < {cooldown_period}s cooldown (priority: {priority})")
                    return False
        
        # Record this action
        self._last_actions[action_key] = now
        logger.debug(f"✅ Circuit breaker: {action_type} for {market} allowed (priority: {priority}, cooldown: {cooldown_period}s)")
        return True
    
    def _get_cooldown_period(self, action_type: str, priority: str) -> int:
        """Get cooldown period based on action type and priority"""
        base_cooldown = self._action_cooldowns.get(action_type, self._default_cooldown)
        
        # Priority multipliers
        priority_multipliers = {
            "emergency": 0.0,   # No cooldown for emergencies
            "high": 0.3,        # 30% of normal cooldown
            "normal": 1.0,      # Full cooldown
            "low": 1.5          # 150% of normal cooldown
        }
        
        multiplier = priority_multipliers.get(priority, 1.0)
        return int(base_cooldown * multiplier)
    
    def get_remaining_cooldown(self, market: str, action_type: str) -> float:
        """
        Get remaining cooldown time for action
        
        Args:
            market: Market symbol
            action_type: Action type
            
        Returns:
            Remaining cooldown time in seconds (0 if none)
        """
        action_key = f"{market}_{action_type}"
        last_time = self._last_actions.get(action_key)
        
        if not last_time:
            return 0.0
        
        elapsed = (datetime.now(timezone.utc) - last_time).total_seconds()
        remaining = max(0, self._cooldown_period - elapsed)
        return remaining
    
    def reset_action(self, market: str, action_type: str) -> None:
        """
        Reset cooldown for specific action (use sparingly)
        
        Args:
            market: Market symbol
            action_type: Action type
        """
        action_key = f"{market}_{action_type}"
        self._last_actions.pop(action_key, None)
        logger.info(f"🔄 Circuit breaker: Reset cooldown for {action_type} on {market}")
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get circuit breaker statistics"""
        now = datetime.now(timezone.utc)
        active_cooldowns = {}
        
        for action_key, last_time in self._last_actions.items():
            remaining = max(0, self._cooldown_period - (now - last_time).total_seconds())
            if remaining > 0:
                active_cooldowns[action_key] = remaining
        
        return {
            "cooldown_period_seconds": self._cooldown_period,
            "total_tracked_actions": len(self._last_actions),
            "active_cooldowns": active_cooldowns
        }


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
        
        # Event-driven buy status management
        # REMOVED: _buy_status_cache - now using direct exchange queries for single source of truth
        self._last_trading_day = None
        # REMOVED: _buy_status_triggers - no longer needed with direct exchange queries
        
        # State tracking for logging optimization
        self._logged_states = {}  # market -> {state_type -> last_logged_value}
        
        # Circuit breaker for order placement
        self._order_circuit_breaker = TradingCircuitBreaker()
        
        # Cycle completion tracking for continuous trading
        self._cycle_completion_flags = {}  # market -> bool (allows immediate new buy after cycle complete)
        
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
            
            # Connect OrderManager to OrderTracker events for synchronized order cleanup
            self.order_tracker.add_order_complete_handler(self.order_manager.handle_order_completion)
            logger.info("✓ Connected OrderManager to OrderTracker for automatic order cleanup")
            
            # Initialize WebSocket state tracking for reconnection detection
            self._ws_connection_state = {
                'last_connected': None,
                'last_authenticated': None,
                'reconnection_count': 0
            }
            
            # Set WebSocket provider reference for enhanced availability monitoring
            self.market_data.set_websocket_provider(self.websocket_market_data)
            
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
            self.websocket_client.register_handler(
                "order.update",
                self._handle_order_update
            )
            self.websocket_client.register_handler(
                "user_deals.update",
                self._handle_user_deals_update
            )
            
            # Subscribe to balance updates for real-time account balance (no market filter needed)
            await self.websocket_client.subscribe_balance(["USDT"])
            logger.info("✓ Subscribed to WebSocket balance updates")
            
            # Note: Market-specific subscriptions (orders, user_deals, market_state, positions) 
            # will be set up in set_trading_market() after market is selected
            
            # Load current state from exchange (no database)
            logger.info("Loading current state from exchange...")
            await self._initialize_from_exchange()
            logger.info("✓ Exchange state loaded successfully")
            
            # Initialize trading day tracking
            logger.info("Initializing bot and cleaning stale orders...")
            self._last_trading_day = datetime.now(timezone.utc).date()
            if self.trading_markets:
                total_cleaned = 0
                for market in self.trading_markets:
                    # REMOVED: Cache update - now using direct exchange queries
                    logger.info(f"✅ Bot startup completed for {market}")
                
                logger.info(f"✓ Bot initialization completed for {len(self.trading_markets)} markets")
            else:
                logger.info("✓ Bot initialized (no markets yet)")
            
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
            
            # Check if we're in funding fee settlement period
            from src.utils.settlement_handler import is_settlement_period, wait_for_settlement_end, log_settlement_schedule
            
            if is_settlement_period():
                logger.warning("⚠️  Bot starting during funding fee settlement period")
                await wait_for_settlement_end()
            
            # Log upcoming settlement times for visibility
            log_settlement_schedule()
            
            # Perform comprehensive stale order cleanup on startup
            logger.info("Performing startup stale order cleanup...")
            await self._perform_startup_order_cleanup()
            
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
                
                # Check if this market was already initialized (has orders from previous runs)
                existing_orders = self.order_manager.get_pending_orders(market)
                bot_orders = [
                    order for order in existing_orders 
                    if order.client_id and order.client_id.startswith('DRA_')
                ]
                
                if bot_orders:
                    logger.info(f"📌 Found {len(bot_orders)} existing bot order(s) for {market}")
                    logger.info(f"🛡️ Skipping leverage adjustment - trusting bot orders have correct settings")
                    logger.info(f"✅ Bot orders trusted: {[order.client_id for order in bot_orders]}")
                    
                    # Skip leverage adjustment entirely - bot orders are trusted
                    # Continue with the rest of the market setup
                    
                else:
                    # No bot orders found, safe to attempt leverage adjustment
                    logger.info(f"🔧 No existing bot orders found - configuring leverage for {market}")
                    
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
                                action = resolution_result.get('action', 'unknown')
                                
                                if action in ['bot_orders_trusted_early', 'bot_order_trusted']:
                                    logger.info(f"✅ {resolution_result['message']}")
                                    logger.info(f"💡 Trust reason: {resolution_result.get('trust_reason', 'N/A')}")
                                else:
                                    logger.info(f"✅ Leverage conflict resolved for {market}: {resolution_result['message']}")
                            
                                # Check for critical bug detection
                                if resolution_result.get('bug_detected'):
                                    logger.error("🚨 CRITICAL BUG DETECTED AND FIXED!")
                                    logger.error("⚠️ Multiple buy orders found - this violates Daily Range Strategy!")
                                    logger.error("🔍 Please investigate order management logic immediately!")
                                
                            else:
                                # Check if it's a partial success (leverage set but order recreation failed)
                                if resolution_result.get('action') == 'single_buy_order_partial':
                                    logger.warning(f"⚠️ Leverage set for {market} but order recreation failed")
                                    logger.warning(f"Recreation error: {resolution_result.get('recreation_error', 'Unknown error')}")
                                    logger.info("🔄 Bot will create a new buy order through normal signal processing")
                                    
                                    # REMOVED: Cache clearing - no longer needed with direct exchange queries
                                    
                                else:
                                    # Intelligent resolution completely failed
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
            
            # Initialize tracking for this market
            # REMOVED: Cache update - now using direct exchange queries
            
            # Subscribe to market-specific WebSocket streams for real-time data
            if self.websocket_client and self.websocket_client.is_connected:
                logger.info(f"Setting up WebSocket subscriptions for {market}...")
                
                # Subscribe to order updates for this market only
                await self.websocket_client.subscribe_orders([market])
                logger.info(f"✓ Subscribed to order updates for {market}")
                
                # Subscribe to user deals/trades for this market only
                await self.websocket_client.subscribe_user_deals([market])
                logger.info(f"✓ Subscribed to user deals for {market}")
                
                # Subscribe to market state updates for this market only (real-time price data)
                await self.websocket_client.subscribe_market_state([market])
                logger.info(f"✓ Subscribed to market state updates for {market}")
                
                # Subscribe to position updates for this market only
                await self.websocket_client.subscribe_positions([market])
                logger.info(f"✓ Subscribed to position updates for {market}")
                
                logger.info(f"🔗 All WebSocket subscriptions configured for {market}")
            else:
                logger.warning(f"⚠️ WebSocket not connected - market subscriptions for {market} will be set up on reconnect")
    
    async def execute_trading_cycle(self):
        """Execute one complete trading cycle"""
        if self._shutdown_requested:
            return
        
        try:
            # Check for new trading day
            now = datetime.now(timezone.utc)
            current_day = now.date()
            if self._last_trading_day and current_day > self._last_trading_day:
                logger.info(f"New trading day detected: {current_day}")
                self._last_trading_day = current_day
                # REMOVED: Cache clearing for new day - using direct exchange queries
                log_trading_event('new_day', f"New trading day detected: {current_day}")
                logger.info(f"🌅 New trading day {current_day} - using fresh exchange data")
            
            # Update account and positions periodically with enhanced frequencies for better state consistency
            # Account update every 2 minutes (reduced from 5 min) for better balance tracking
            if (not self._last_account_update or 
                (now - self._last_account_update).seconds > 120):
                await self._update_account_status()
                self._last_account_update = now
            
            # Position sync every 3 minutes (reduced from 10 min) for critical state validation
            if (not self._last_positions_sync or 
                (now - self._last_positions_sync).seconds > 180):
                await self._sync_positions()
                self._last_positions_sync = now
            
            # Process unmatched buy fills periodically
            await self._process_pairing_tasks()
            
            # Daily disk space check (simple storage monitoring)
            if not hasattr(self, '_last_disk_check'):
                self._last_disk_check = now
            if (now - self._last_disk_check).seconds > 86400:  # Once per day
                from src.utils.logger import check_disk_space
                check_disk_space()
                self._last_disk_check = now
            
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
                log_trading_event('signal_generation', f"Generated signal for {market}: Buy=${signal.buy_price:.2f}, Sell=${signal.sell_price:.2f}")
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
                    log_trading_event('profitable_exit', f"Profitable exit opportunity for {market} position: "
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
                # Only log position if state changed
                if self._should_log_state_change(market, 'position_in_entry_check', balance['position_size']):
                    log_trading_event('position_found', f"🎯 Found existing position in {market}: {balance['position_size']:.6f}")
                
                if not balance['is_balanced']:
                    # Only log imbalance if state changed
                    imbalance_key = f"missing_sell_{balance['missing_sell']:.6f}"
                    if self._should_log_state_change(market, imbalance_key, True):
                        log_trading_event('position_imbalance', f"⚠️ Position needs sell orders: missing {balance['missing_sell']:.6f} for {market}")
                    
                    # Place missing sell order for existing position
                    if self._place_missing_sell_order(market, balance['missing_sell']):
                        log_trading_event('sell_order', f"✅ Placed missing sell order for existing position in {market}")
                    else:
                        log_trading_event('sell_order_error', f"❌ Failed to place missing sell order for {market}")
                else:
                    # Only log balanced state if it changed
                    balance_key = f"balanced_with_{len(balance['sell_orders'])}_orders"
                    if self._should_log_state_change(market, balance_key, True):
                        log_trading_event('position_balance', f"✅ Position properly balanced with {len(balance['sell_orders'])} sell orders for {market}")
                
            
            # Second priority: Check for new buy opportunities
            # CRITICAL FIX: Clean up duplicate orders before checking if we should place buy
            buy_status = self._get_exchange_buy_status(market)
            total_buy_orders = buy_status.get('total_buy_orders', 0)
            all_buy_orders = buy_status.get('all_buy_orders', [])
            
            # Check if we need cleanup (only for multiple orders - NOT age-based)
            needs_cleanup = False
            cleanup_reason = ""
            
            if total_buy_orders > 1:
                needs_cleanup = True
                cleanup_reason = f"multiple orders ({total_buy_orders})"
            # REMOVED: Age-based cleanup for single same-day orders
            # Conservative approach: Keep valid orders from today regardless of age
            
            if needs_cleanup:
                logger.warning(f"🧹 Cleanup needed for {market}: {cleanup_reason}")
                await self._ensure_single_buy_order(market)
                
                # Verify cleanup worked
                # REMOVED: Cache clearing - using direct exchange queries
                    
                # Immediately verify cleanup worked by checking exchange
                updated_status = self._get_exchange_buy_status(market)
                remaining_orders = updated_status.get('total_buy_orders', 0)
                
                if remaining_orders == 0:
                    log_trading_event('order_cleanup', f"✅ All orders cleaned up for {market}")
                elif remaining_orders == 1:
                    log_trading_event('order_cleanup', f"✅ Cleanup successful - 1 order remaining for {market}")
                else:
                    logger.error(f"🚨 Cleanup failed - still {remaining_orders} buy orders for {market}")
                    log_trading_event('cleanup_failure', f"❌ Cleanup failed - {remaining_orders} orders still exist for {market}")
                    # Force another cleanup attempt
                    await self._ensure_single_buy_order(market)
            
            if self._should_place_buy_order(market, signal, current_price):
                log_trading_event('buy_order', f"🚀 Placing new buy order for {market}")
                await self._place_entry_order(market, 'buy', signal.buy_price, signal)
            else:
                # Only log "no buy opportunity" if it's a state change
                # This prevents logging the same message every 5 seconds
                if self._should_log_state_change(market, 'no_buy_opportunity', True):
                    log_trading_event('buy_decision', f"⏸️ No buy opportunity for {market} at current conditions")
            
        except Exception as e:
            logger.error(f"Error checking entry opportunities for {market}: {e}")
    
    def _check_daily_buy_status(self, market: str) -> Dict[str, Any]:
        """Check buy order status for current day with stale order cleanup"""
        try:
            # Force sync with exchange to ensure fresh data and cleanup stale orders
            log_trading_event('buy_status_sync', f"Forcing order sync with exchange for {market}")
            sync_count = self.order_manager.load_existing_orders(market)
            
            # Get all pending buy orders using proven endpoint
            pending_orders = self.order_manager.get_pending_orders(market)
            buy_orders = [o for o in pending_orders if o.side.value == 'buy']
            
            # Force status update for all buy orders to catch recently filled orders
            updated_orders = []
            for order in buy_orders:
                updated_order = self.order_manager.update_order_status(order.client_id)
                if updated_order and updated_order.status.value == 'pending':
                    updated_orders.append(updated_order)
                elif updated_order:
                    log_trading_event('stale_cleanup', f"Order {order.client_id} status changed to {updated_order.status.value}")
            
            buy_orders = updated_orders
            
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
            # Note: cancel_order now has @settlement_retry decorator
            cancelled_count = 0
            for stale_order in stale_orders:
                try:
                    logger.warning(f"Cancelling stale buy order: {stale_order.client_id} from {stale_order.created_at.date()}")
                    # This will automatically retry on settlement errors
                    if self.order_manager.cancel_order(stale_order.client_id):
                        cancelled_count += 1
                except Exception as e:
                    # Only log if not a settlement error (those are handled by decorator)
                    from src.utils.settlement_handler import is_settlement_error
                    if not is_settlement_error(e):
                        logger.error(f"Failed to cancel stale order {stale_order.client_id}: {e}")
            
            result = {
                'has_today_buy': len(today_buy_orders) > 0,
                'today_orders': today_buy_orders,
                'cancelled_stale': cancelled_count,
                'total_buy_orders': len(buy_orders),
                'all_buy_orders': buy_orders  # Return ALL orders for validation
            }
            
            # Validation: Cross-check with exchange API to detect discrepancies
            try:
                direct_exchange_orders = self.exchange_client.get_pending_orders(market=market)
                exchange_buy_orders = []
                if isinstance(direct_exchange_orders, dict):
                    exchange_data = direct_exchange_orders.get('data', [])
                    exchange_buy_orders = [o for o in exchange_data if o.get('side') == 'buy']
                elif isinstance(direct_exchange_orders, list):
                    exchange_buy_orders = [o for o in direct_exchange_orders if o.get('side') == 'buy']
                
                # Filter today's orders from exchange
                today_exchange_buys = []
                for order in exchange_buy_orders:
                    created_time = order.get('created_at', 0)
                    if isinstance(created_time, str):
                        from dateutil import parser
                        order_date = parser.parse(created_time).date()
                    else:
                        order_date = datetime.fromtimestamp(created_time / 1000, timezone.utc).date()
                    
                    if order_date == today:
                        today_exchange_buys.append(order)
                
                # Log discrepancy if found
                manager_count = len(today_buy_orders)
                exchange_count = len(today_exchange_buys)
                if manager_count != exchange_count:
                    log_trading_event('order_discrepancy', 
                        f"Buy order count mismatch for {market}: manager={manager_count}, exchange={exchange_count}")
                    logger.warning(f"Order manager vs exchange discrepancy detected for {market}:")
                    logger.warning(f"  Manager orders: {[o.client_id for o in today_buy_orders]}")
                    logger.warning(f"  Exchange orders: {[o.get('client_id', o.get('order_id')) for o in today_exchange_buys]}")
                    
                    # REMOVED: Cache clearing logic - now using direct exchange queries only
                
            except Exception as validation_error:
                logger.debug(f"Validation check failed for {market}: {validation_error}")
            
            if result['has_today_buy']:
                log_trading_event('buy_status', f"Found {len(today_buy_orders)} buy order(s) from today for {market}")
            
            if cancelled_count > 0:
                log_trading_event('stale_cleanup', f"Cancelled {cancelled_count} stale buy order(s) for {market}")
            
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
        """Place sell order for missing position coverage with intelligent price adjustment and funding fee protection"""
        try:
            # Funding fee protection check
            from src.utils.settlement_handler import is_settlement_period, is_approaching_settlement
            
            if is_settlement_period():
                logger.warning(f"⚠️ Cannot place sell order - currently in funding fee settlement period for {market}")
                log_trading_event('settlement_block', f"Missing sell order blocked - settlement period active for {market}")
                return False
            
            if is_approaching_settlement():
                logger.warning(f"⚠️ Cannot place sell order - approaching funding fee settlement period for {market}")
                log_trading_event('settlement_approach', f"Missing sell order blocked - approaching settlement for {market}")
                return False
            
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
    
    def _get_today_pending_sell_orders(self, market: str) -> int:
        """Count pending sell orders placed today for the given market"""
        from src.exchange.order_manager import OrderSide
        
        today = datetime.now(timezone.utc).date()
        
        try:
            # Get all pending orders for this market
            pending_orders = self.order_manager.get_pending_orders(market)
            
            # Filter for sell orders placed today
            today_pending_sells = [
                order for order in pending_orders
                if order.side == OrderSide.SELL and order.created_at.date() == today
            ]
            
            sell_count = len(today_pending_sells)
            
            # Handle edge case of multiple pending sells from today
            if sell_count > 1:
                logger.warning(f"⚠️ Found {sell_count} pending sell orders from today for {market}")
                logger.warning("This indicates a previous bug occurred - blocking new buy orders")
                log_trading_event('multiple_sells_detected', f"Found {sell_count} today's pending sells - blocking new buys for {market}")
                
                # Log details for debugging
                for i, sell_order in enumerate(today_pending_sells, 1):
                    logger.info(f"  Sell #{i}: {sell_order.client_id} placed at {sell_order.created_at.time()}")
            elif sell_count == 1:
                sell_order = today_pending_sells[0]
                logger.info(f"ℹ️ Found 1 pending sell order from today: {sell_order.client_id} placed at {sell_order.created_at.time()}")
            
            return sell_count
            
        except Exception as e:
            logger.error(f"Error checking today's pending sell orders for {market}: {e}")
            # Return 1 to be safe - block new buy orders if we can't determine state
            return 1
    
    def _should_place_buy_order(self, market: str, signal: TradingSignal, current_price: float) -> bool:
        """Buy order decision using fresh exchange data with day-start cancellation and funding fee protection"""
        
        # Phase 0: Funding fee protection check
        from src.utils.settlement_handler import is_settlement_period, is_approaching_settlement
        
        if is_settlement_period():
            if self._should_log_state_change(market, 'settlement_period', True):
                logger.warning(f"⚠️ Cannot place buy order - currently in funding fee settlement period for {market}")
                log_trading_event('settlement_block', f"Buy order blocked - settlement period active for {market}")
            return False
        
        if is_approaching_settlement():
            if self._should_log_state_change(market, 'approaching_settlement', True):
                logger.warning(f"⚠️ Cannot place buy order - approaching funding fee settlement period for {market}")
                log_trading_event('settlement_approach', f"Buy order blocked - approaching settlement for {market}")
            return False
        
        # Phase 1: Day-start cancellation check (respecting settlement periods)
        # Check if it's a new day and cancel old buy orders if needed
        is_new_day = self.market_data.is_new_trading_day(market)
        if is_new_day:
            logger.info(f"🌅 New trading day detected for {market} - checking for old buy orders to cancel")
            
            # Don't cancel orders during settlement periods
            if is_settlement_period() or is_approaching_settlement():
                logger.info(f"⏳ Day-start cleanup delayed - waiting for settlement period to end for {market}")
                log_trading_event('settlement_delay', f"Day-start cleanup delayed due to settlement period for {market}")
                return False
            
            # Get current buy orders before making decision
            current_buy_status = self._get_exchange_buy_status(market)
            old_orders = current_buy_status.get('all_buy_orders', [])
            
            if old_orders:
                today = datetime.now(timezone.utc).date()
                cancelled_count = 0
                
                for order in old_orders:
                    if order.created_at.date() < today:
                        try:
                            logger.info(f"🗑️ Cancelling yesterday's buy order: {order.client_id} from {order.created_at.date()}")
                            if self.order_manager.cancel_order(order.client_id):
                                cancelled_count += 1
                                log_trading_event('day_start_cleanup', f"Cancelled old buy order {order.client_id} from {order.created_at.date()}")
                        except Exception as e:
                            logger.error(f"Failed to cancel old buy order {order.client_id}: {e}")
                
                if cancelled_count > 0:
                    logger.info(f"✅ Day-start cleanup: Cancelled {cancelled_count} old buy orders for {market}")
                    # Small delay to let cancellations process
                    import time
                    time.sleep(0.5)
        
        # Use direct exchange queries for single source of truth
        buy_status = self._get_exchange_buy_status(market)
        
        # Phase 2: Check for pending sell orders from today
        today_pending_sells = self._get_today_pending_sell_orders(market)
        if today_pending_sells > 0:
            if self._should_log_state_change(market, f'pending_sells_{today_pending_sells}', True):
                logger.info(f"❌ Cannot place buy - {today_pending_sells} pending sell orders from today for {market}")
                log_trading_event('pending_sells_block', f"Buy blocked - {today_pending_sells} today's sell orders pending for {market}")
            return False
        
        # Phase 3: Check for pending buy orders (after day-start cancellation and sell check)
        total_buy_orders = buy_status.get('total_buy_orders', 0)
        if total_buy_orders > 0:
            # Log detailed information about remaining buy orders
            all_orders = buy_status.get('all_buy_orders', [])
            order_summary = []
            for order in all_orders:
                order_date = order.created_at.date()
                order_time = order.created_at.time()
                age_hours = (datetime.now(timezone.utc) - order.created_at).total_seconds() / 3600
                order_summary.append(f"{order.client_id} ({order_date} {order_time.strftime('%H:%M:%S')}, {age_hours:.1f}h old)")
            
            # Only log when state changes to avoid spam
            order_state_key = f"existing_buy_orders_{total_buy_orders}"
            if self._should_log_state_change(market, order_state_key, ', '.join([o.client_id for o in all_orders])):
                logger.info(f"📋 Pending buy orders exist - waiting: {', '.join(order_summary)}")
                log_trading_event('buy_waiting', f"❌ Cannot place buy - pending buy orders exist for {market}: {len(all_orders)} orders")
            
            return False  # Wait for existing buy orders to fill first
        
        # Phase 4: Check cycle completion or first buy of day conditions
        cycle_complete_flag = self._cycle_completion_flags.get(market, False)
        today_orders = buy_status.get('today_orders', [])
        has_today_buy = len(today_orders) > 0
        
        # Simplified decision logic as requested by user
        if cycle_complete_flag:
            # Cycle just completed - allow immediate new buy order
            logger.info(f"🔄 Cycle completion detected for {market} - allowing new buy order")
            log_trading_event('cycle_buy', f"🔄 Placing new buy order after cycle completion for {market}")
            
            # Clear the flag once we use it
            self._cycle_completion_flags[market] = False
            
        elif not has_today_buy:
            # No buy order placed today - allow first buy of day
            logger.info(f"🌅 First buy order of the day allowed for {market}")
            log_trading_event('daily_buy', f"🌅 Placing first buy order of the day for {market}")
            
            # Balance any existing positions before placing new buy
            balance = self._calculate_position_sell_balance(market)
            if balance['position_exists'] and not balance['is_balanced']:
                log_trading_event('position_balance', f"🔧 Position unbalanced: {balance['position_size']:.6f} position vs {balance['total_sells']:.6f} sells for {market}")
                log_trading_event('missing_sell', f"🔧 Placing missing sell order: {balance['missing_sell']:.6f} for {market}")
                
                if self._place_missing_sell_order(market, balance['missing_sell']):
                    log_trading_event('sell_order', f"✅ Missing sell order placed for existing position in {market}")
                else:
                    log_trading_event('sell_order_error', f"❌ Failed to place missing sell order for {market}")
                    return False  # Don't place buy if we can't balance the position
            
        else:
            # Already placed buy today and no cycle completion - block additional buys
            if self._should_log_state_change(market, 'daily_buy_limit', True):
                log_trading_event('buy_decision', f"❌ Cannot place buy - daily buy already placed for {market}")
            return False
        
        # Phase 5: Price validation
        price_diff_percent = abs(current_price - signal.buy_price) / signal.buy_price * 100
        if price_diff_percent > MAX_RANGE_DEVIATION:
            if self._should_log_state_change(market, 'price_out_of_range', True):
                log_trading_event('price_validation', f"❌ Cannot place buy - price too far from signal: {price_diff_percent:.2f}% deviation (max: {MAX_RANGE_DEVIATION}%) for {market}")
            return False
        
        # Phase 6: Account balance and position sizing
        account_balance = self.get_account_balance()
        position_size = self.position_sizer.calculate_position_size(
            market, signal.buy_price, account_balance
        )
        
        if not position_size.is_valid:
            if self._should_log_state_change(market, 'position_size_invalid', position_size.reason):
                log_trading_event('position_sizing', f"❌ Cannot place buy - position sizing invalid: {position_size.reason} for {market}")
            return False
        
        # All checks passed - no existing position, no pending buy orders
        # Reset states when we're ready to buy
        if market in self._logged_states:
            self._logged_states[market] = {}  # Clear logged states for fresh start
        
        log_trading_event('buy_decision', f"✅ All checks passed - ready to place buy order for {market}")
        log_trading_event('order_details', f"💰 Order details: ${signal.buy_price:.2f} x {position_size.quantity:.6f} = ${position_size.size_usdt:.2f} for {market}")
        return True
    
    async def _ensure_single_buy_order(self, market: str) -> None:
        """
        Ensure only ONE buy order exists for the market.
        Cancel ALL other buy orders (from any day).
        
        Strategy Rule: Only ONE buy order should exist at any time.
        """
        try:
            # Circuit breaker: Prevent rapid successive cleanup operations (high priority for cleanup)
            if not self._order_circuit_breaker.should_allow_action(market, "cleanup", "high"):
                remaining = self._order_circuit_breaker.get_remaining_cooldown(market, "cleanup")
                logger.info(f"🚧 Order cleanup blocked by circuit breaker - {remaining:.1f}s remaining cooldown")
                return
            
            logger.info(f"🔍 Enforcing single buy order rule for {market}")
            
            # Force a fresh sync with exchange before cleanup
            logger.info(f"🔄 Forcing fresh order sync before cleanup for {market}")
            sync_count = self.order_manager.load_existing_orders(market)
            logger.info(f"📥 Synced {sync_count} orders from exchange for {market}")
            
            # Get ALL pending orders for this market
            all_pending_orders = self.order_manager.get_pending_orders(market)
            buy_orders = [o for o in all_pending_orders if o.side.value == 'buy']
            
            if not buy_orders:
                logger.info(f"✅ No existing buy orders found for {market} after sync")
                return
                
            logger.warning(f"⚠️  Found {len(buy_orders)} buy order(s) for {market} - cleaning up...")
            
            # Sort by creation time (keep the oldest/first one if from today)
            buy_orders.sort(key=lambda x: x.created_at)
            
            today = datetime.now(timezone.utc).date()
            today_orders = [o for o in buy_orders if o.created_at.date() == today]
            old_orders = [o for o in buy_orders if o.created_at.date() < today]
            
            # Cancel ALL old orders
            for order in old_orders:
                try:
                    logger.warning(f"🗑️  Cancelling old buy order: {order.client_id} from {order.created_at.date()}")
                    if self.order_manager.cancel_order(order.client_id):
                        log_trading_event('cleanup', f"Cancelled old buy order {order.client_id}")
                except Exception as e:
                    if "invalid argument" in str(e).lower():
                        logger.info(f"⚠️ Order {order.client_id} may already be filled/cancelled: {e}")
                        log_trading_event('order_gone', f"Order {order.client_id} not found - likely filled/cancelled")
                    else:
                        logger.error(f"Failed to cancel old order {order.client_id}: {e}")
            
            # Handle today's orders - be more aggressive about old ones
            if len(today_orders) > 1:
                logger.warning(f"⚠️  Multiple buy orders from today detected! Keeping newest, cancelling {len(today_orders)-1} older ones")
                # Sort by creation time, keep the newest (last one)
                today_orders.sort(key=lambda x: x.created_at)
                for order in today_orders[:-1]:  # Cancel all except the newest
                    try:
                        age_hours = (datetime.now(timezone.utc) - order.created_at).total_seconds() / 3600
                        logger.warning(f"🗑️  Cancelling older buy order: {order.client_id} from {order.created_at.time()} ({age_hours:.1f}h old)")
                        
                        # Add small delay to prevent race conditions with rapid order creation
                        await asyncio.sleep(0.1)
                        
                        if self.order_manager.cancel_order(order.client_id):
                            log_trading_event('cleanup', f"Cancelled older buy order {order.client_id}")
                        else:
                            logger.warning(f"⚠️ Cancel returned False for {order.client_id} - order may be processing")
                            
                    except Exception as e:
                        if "invalid argument" in str(e).lower():
                            logger.info(f"⚠️ Order {order.client_id} may already be filled/cancelled: {e}")
                            log_trading_event('order_gone', f"Order {order.client_id} not found - likely filled/cancelled")
                        else:
                            logger.error(f"Failed to cancel older order {order.client_id}: {e}")
            elif len(today_orders) == 1:
                # Single order from today - KEEP IT (conservative approach)
                order = today_orders[0]
                age_hours = (datetime.now(timezone.utc) - order.created_at).total_seconds() / 3600
                logger.info(f"✅ Keeping single buy order from today: {order.client_id} ({age_hours:.1f}h old)")
                logger.info("📋 Conservative approach: Respecting existing same-day order, letting order tracking handle fills")
                        
            # REMOVED: Cache clearing - using direct exchange queries
                
            # Wait a moment for exchange to process cancellations
            await asyncio.sleep(0.5)
            
            # Verify cleanup results with fresh sync
            logger.info(f"🔍 Verifying cleanup results for {market}")
            final_sync_count = self.order_manager.load_existing_orders(market)
            final_pending_orders = self.order_manager.get_pending_orders(market)
            final_buy_orders = [o for o in final_pending_orders if o.side.value == 'buy']
            
            if len(final_buy_orders) <= 1:
                logger.info(f"✅ Cleanup verification successful - {len(final_buy_orders)} buy order(s) remaining for {market}")
                if len(final_buy_orders) == 1:
                    remaining_order = final_buy_orders[0]
                    logger.info(f"   Keeping order: {remaining_order.client_id} from {remaining_order.created_at}")
            else:
                logger.error(f"🚨 Cleanup verification failed - {len(final_buy_orders)} buy orders still exist for {market}")
                # Check if any of these might be recently filled
                for order in final_buy_orders:
                    logger.error(f"   Remaining order: {order.client_id} from {order.created_at}")
                    # Force a status update to see if it's actually still pending
                    try:
                        updated_order = self.order_manager.update_order_status(order.client_id)
                        if updated_order and updated_order.status.value != 'pending':
                            logger.info(f"   📝 Order {order.client_id} status updated to: {updated_order.status.value}")
                    except Exception as e:
                        logger.debug(f"   Could not update status for {order.client_id}: {e}")
            
            logger.info(f"🏁 Single buy order enforcement completed for {market}")
                
        except Exception as e:
            logger.error(f"Error in single buy order enforcement for {market}: {e}")
    
    async def _place_entry_order(self, market: str, side: str, price: float, signal: TradingSignal):
        """Place an entry order"""
        try:
            # Circuit breaker: Prevent rapid successive order placements (normal priority for standard trading)
            action_type = f"{side}_order"
            if not self._order_circuit_breaker.should_allow_action(market, action_type, "normal"):
                remaining = self._order_circuit_breaker.get_remaining_cooldown(market, action_type)
                logger.info(f"🚧 Order placement blocked by circuit breaker - {remaining:.1f}s remaining cooldown")
                return
            
            # CRITICAL: Ensure only ONE buy order exists before placing new one
            if side == 'buy':
                await self._ensure_single_buy_order(market)
                log_trading_event('order_cleanup', f"✅ Single buy order rule enforced for {market}")
            
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
                    log_trading_event('price_adjustment', f"💰 Buy price adjusted: ${price:.2f} → ${adjusted_price:.2f} (cheaper entry) for {market}")
                else:
                    log_trading_event('price_no_adjustment', f"📊 Buy price unchanged: ${price:.2f} (signal price optimal) for {market}")
            
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
                    log_trading_event('order_tracking', f"Started tracking buy order {order.exchange_order_id} for automatic sell pairing in {market}")
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
                log_trading_event('order_placed', f"Placed {side} order for {market}: "
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
            # Circuit breaker: Prevent rapid successive exit order placements (high priority for position closure)
            if not self._order_circuit_breaker.should_allow_action(position.market, "position_closure", "high"):
                remaining = self._order_circuit_breaker.get_remaining_cooldown(position.market, "position_closure")
                logger.info(f"🚧 Exit order placement blocked by circuit breaker - {remaining:.1f}s remaining cooldown")
                return
            
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
                        
                        # REMOVED: Cache clearing - using direct exchange queries ensures fresh data
                
                # Update bot status
                self.status.total_positions = len(self.position_manager.get_all_positions())
                
                logger.debug(f"Processed position update for {market}: event={event}, size={open_interest:.6f}")
                
            except (ValueError, TypeError) as e:
                logger.error(f"Error parsing position data for {market}: {e}")
                return
            
        except Exception as e:
            logger.error(f"Error handling position update: {e}")
    
    def _handle_order_update(self, data: Dict[str, Any]):
        """Handle order.update message from WebSocket with real-time state updates"""
        try:
            order_data = data.get("order", {})
            if not order_data:
                return
            
            market = order_data.get("market")
            side = order_data.get("side")
            status = order_data.get("status")
            client_id = order_data.get("client_id")
            order_id = order_data.get("order_id")
            
            if market and side == "buy":
                log_trading_event('buy_status_update', f"Buy order update for {market}: {status}")
                
                # CRITICAL: Trigger OrderManager state update for buy orders
                if status in ["done", "filled", "cancelled"] and client_id:
                    logger.info(f"🔄 Buy order {status} - triggering state reconciliation for {market}")
                    asyncio.create_task(self._reconcile_order_state(market, client_id, status))
            
            elif market and side == "sell" and status in ["done", "filled"]:
                logger.info(f"🔄 Trading cycle complete for {market} - sell order {status}")
                
                # CRITICAL: Trigger comprehensive state validation after sell completion
                logger.info(f"🔍 Sell order completed - triggering full state validation for {market}")
                asyncio.create_task(self._reconcile_trading_cycle_completion(market))
                
        except Exception as e:
            logger.error(f"Error handling order update: {e}")
    
    def _handle_user_deals_update(self, data: Dict[str, Any]):
        """Handle user_deals.update message from WebSocket with comprehensive state updates"""
        try:
            deals = data.get("user_deals", [])
            if not deals:
                return
            
            for deal in deals:
                market = deal.get("market")
                side = deal.get("side")
                amount = deal.get("amount", 0)
                price = deal.get("price", 0)
                
                if market and side == "buy":
                    log_trading_event('buy_status_update', f"Buy order filled for {market}: {amount} @ ${price}")
                    
                    # CRITICAL: Trigger immediate state validation after buy fill
                    logger.info(f"💰 Buy order filled - triggering state validation for {market}")
                    asyncio.create_task(self._reconcile_after_fill(market, "buy", amount, price))
                
                elif market and side == "sell":
                    log_trading_event('sell_fill', f"Sell order filled for {market}: {amount} @ ${price}")
                    
                    # NEW: Check if sell order was placed today for cycle completion
                    order_id = deal.get("order_id")
                    was_today_order = False
                    
                    if order_id:
                        # Try to find the order by order_id in active orders
                        matching_order = None
                        for client_id, order in self.order_manager.active_orders.items():
                            if str(order.exchange_order_id) == str(order_id):
                                matching_order = order
                                break
                        
                        if matching_order and matching_order.created_at:
                            today = datetime.now(timezone.utc).date()
                            order_date = matching_order.created_at.date()
                            was_today_order = (order_date == today)
                            
                            if was_today_order:
                                logger.info(f"✅ Sell order {order_id} was placed today ({order_date}) - cycle completion detected")
                            else:
                                logger.info(f"ℹ️  Sell order {order_id} was placed on {order_date} (not today) - no cycle completion")
                        else:
                            logger.debug(f"Could not find creation date for sell order {order_id}")
                    
                    # Set cycle completion flag if sell was from today
                    if was_today_order:
                        self._cycle_completion_flags[market] = True
                        logger.info(f"🔄 Cycle completion flag set for {market} - sell from today completed")
                        log_trading_event('cycle_complete', f"Cycle completion detected for {market} - sell order from today filled")
                    
                    # CRITICAL: Check if this completes a trading cycle (original logic)
                    position = self.position_manager.get_position(market)
                    if not position or position.size < 0.000001:
                        logger.info(f"🔄 Trading cycle complete for {market} via sell fill")
                        
                        # CRITICAL: Trigger comprehensive state reconciliation
                        logger.info(f"🔍 Trading cycle completed - triggering full state reconciliation for {market}")
                        asyncio.create_task(self._reconcile_trading_cycle_completion(market))
                    else:
                        # Partial fill - validate remaining position balance
                        logger.info(f"🔄 Partial sell fill - validating position balance for {market}")
                        asyncio.create_task(self._reconcile_after_fill(market, "sell", amount, price))
                    
        except Exception as e:
            logger.error(f"Error handling user deals update: {e}")
    
    async def _reconcile_order_state(self, market: str, client_id: str, status: str):
        """Reconcile OrderManager state after order status change"""
        try:
            logger.debug(f"🔄 Reconciling order state for {client_id} ({status}) in {market}")
            
            # Update OrderManager state if order is completed
            if status in ["done", "filled", "cancelled"]:
                if client_id in self.order_manager.active_orders:
                    # Update order status in OrderManager
                    order = self.order_manager.active_orders[client_id]
                    
                    if status in ["done", "filled"]:
                        order.status = OrderStatus.FILLED
                    elif status == "cancelled":
                        order.status = OrderStatus.CANCELLED
                    
                    # Remove completed orders from active tracking
                    del self.order_manager.active_orders[client_id]
                    logger.info(f"✅ Removed completed order {client_id} from OrderManager")
            
            # Force immediate validation against exchange
            await self._validate_order_manager_state()
            
            # Trigger fresh buy status check for critical decisions
            fresh_status = self._get_exchange_buy_status(market)
            logger.debug(f"🔍 Post-reconciliation buy status for {market}: {fresh_status.get('total_buy_orders', 0)} orders")
            
        except Exception as e:
            logger.error(f"Error reconciling order state for {market}: {e}")
    
    async def _reconcile_after_fill(self, market: str, side: str, amount: float, price: float):
        """Reconcile state after order fill"""
        try:
            logger.debug(f"🔄 Reconciling state after {side} fill: {amount} @ ${price} for {market}")
            
            # Force OrderManager and PositionManager sync
            await self._validate_order_manager_state()
            self.position_manager.sync_with_exchange()
            
            # Get fresh state for decision making
            buy_status = self._get_exchange_buy_status(market)
            position = self.position_manager.get_position(market)
            
            logger.info(f"📊 Post-fill state for {market}: {buy_status.get('total_buy_orders', 0)} buy orders, "
                       f"position: {position.size if position else 0:.6f}")
            
        except Exception as e:
            logger.error(f"Error reconciling state after {side} fill for {market}: {e}")
    
    async def _reconcile_trading_cycle_completion(self, market: str):
        """Comprehensive state reconciliation after trading cycle completion"""
        try:
            logger.info(f"🔍 Performing comprehensive state reconciliation for {market}")
            
            # Full state sync with exchange
            await self._validate_order_manager_state()
            self.position_manager.sync_with_exchange()
            
            # Verify trading cycle is actually complete
            position = self.position_manager.get_position(market)
            buy_status = self._get_exchange_buy_status(market)
            
            if not position or position.size < 0.000001:
                logger.info(f"✅ Trading cycle confirmed complete for {market}")
                logger.info(f"📊 Final state: Position={position.size if position else 0:.6f}, "
                           f"Buy orders={buy_status.get('total_buy_orders', 0)}")
                
                # CRITICAL: Set cycle completion flag to allow immediate new buy order
                self._cycle_completion_flags[market] = True
                logger.info(f"🔄 Cycle completion flag set for {market} - new buy order now allowed")
                
                # Log completion event for monitoring
                log_trading_event('cycle_complete', f"✅ Trading cycle completed for {market}")
            else:
                logger.warning(f"⚠️ Trading cycle not fully complete for {market}: position={position.size:.6f}")
            
        except Exception as e:
            logger.error(f"Error reconciling trading cycle completion for {market}: {e}")
    
    def _get_exchange_buy_status(self, market: str) -> Dict[str, Any]:
        """Get buy status directly from exchange - single source of truth"""
        try:
            # Get orders directly from exchange via OrderManager
            pending_orders = self.order_manager.get_pending_orders(market)
            buy_orders = [o for o in pending_orders if o.side.value == 'buy']
            
            # Filter for today's orders
            today = datetime.now(timezone.utc).date()
            today_buy_orders = []
            
            for order in buy_orders:
                if order.created_at.date() == today:
                    today_buy_orders.append(order)
            
            # Return consistent format for buy status
            return {
                'total_buy_orders': len(buy_orders),
                'all_buy_orders': buy_orders,
                'today_orders': today_buy_orders,
                'cancelled_stale': 0  # Always 0 with direct exchange queries
            }
            
        except Exception as e:
            logger.error(f"Error getting exchange buy status for {market}: {e}")
            # Return safe default
            return {
                'total_buy_orders': 0,
                'all_buy_orders': [],
                'today_orders': [],
                'cancelled_stale': 0
            }
    
    
    async def _perform_startup_order_cleanup(self):
        """Comprehensive stale order cleanup during bot startup"""
        try:
            from src.utils.settlement_handler import settlement_retry_async
            logger.info("🧹 Starting aggressive startup order cleanup...")
            
            total_cancelled = 0
            cleanup_summary = []
            
            # Get all pending orders across all markets
            all_orders = self.order_manager.get_pending_orders()
            logger.info(f"📋 Found {len(all_orders)} total pending orders across all markets")
            
            if not all_orders:
                logger.info("✓ No pending orders found during startup cleanup")
                return
            
            now = datetime.now(timezone.utc)
            today = now.date()
            
            # Group orders by market and type
            orders_by_market = {}
            for order in all_orders:
                market = order.market
                if market not in orders_by_market:
                    orders_by_market[market] = {'buy': [], 'sell': []}
                orders_by_market[market][order.side.value].append(order)
            
            # Process each market
            for market, orders in orders_by_market.items():
                buy_orders = orders['buy']
                sell_orders = orders['sell']
                
                market_cancelled = 0
                
                # AGGRESSIVE CLEANUP: Cancel ALL buy orders except the first one from today
                if len(buy_orders) > 0:
                    logger.warning(f"⚠️  Found {len(buy_orders)} buy orders for {market}")
                    
                    # Separate today's orders from old orders
                    today_buy_orders = [o for o in buy_orders if o.created_at.date() == today]
                    old_buy_orders = [o for o in buy_orders if o.created_at.date() < today]
                    
                    # Cancel ALL old orders
                    for order in old_buy_orders:
                        try:
                            logger.info(f"🗑️  Cancelling old buy order: {order.client_id} from {order.created_at.date()} in {market}")
                            self.order_manager.cancel_order(order.client_id)
                            market_cancelled += 1
                            total_cancelled += 1
                        except Exception as e:
                            logger.error(f"Failed to cancel old buy order {order.client_id}: {e}")
                    
                    # If multiple orders from today, keep only first one
                    if len(today_buy_orders) > 1:
                        logger.warning(f"⚠️  Multiple buy orders from today! Keeping first, cancelling {len(today_buy_orders)-1} duplicates")
                        for order in today_buy_orders[1:]:
                            try:
                                logger.info(f"🗑️  Cancelling duplicate buy order: {order.client_id} from today in {market}")
                                self.order_manager.cancel_order(order.client_id)
                                market_cancelled += 1
                                total_cancelled += 1
                            except Exception as e:
                                logger.error(f"Failed to cancel duplicate buy order {order.client_id}: {e}")
                
                # Clean up very old sell orders (older than 7 days) that might be stuck
                week_ago = today - timedelta(days=7)
                for order in sell_orders:
                    order_date = order.created_at.date()
                    if order_date < week_ago:
                        try:
                            logger.warning(f"Cancelling very old sell order: {order.client_id} from {order_date} in {market}")
                            self.order_manager.cancel_order(order.client_id)
                            market_cancelled += 1
                            total_cancelled += 1
                        except Exception as e:
                            logger.error(f"Failed to cancel old sell order {order.client_id}: {e}")
                
                if market_cancelled > 0:
                    cleanup_summary.append(f"{market}: {market_cancelled} orders")
            
            if total_cancelled > 0:
                logger.info(f"✓ Startup cleanup completed: Cancelled {total_cancelled} stale orders")
                logger.info(f"  Details: {', '.join(cleanup_summary)}")
            else:
                logger.info("✓ No stale orders found during startup cleanup")
                
        except Exception as e:
            logger.error(f"Error during startup order cleanup: {e}")
    
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
            # Check for WebSocket reconnections that might have caused missed events
            ws_reconnected = self._detect_websocket_reconnection()
            if ws_reconnected:
                logger.warning("WebSocket reconnection detected - performing thorough state validation")
            
            # Log that we're doing periodic sync - should be rare with WebSocket
            logger.info("Performing periodic position sync via HTTP (WebSocket fallback)")
            
            self.position_manager.sync_with_exchange()
            
            # Sync existing orders with order tracker
            await self.order_tracker.sync_existing_orders()
            
            # CRITICAL: Also sync OrderManager state to prevent stale order issues
            logger.info("Syncing existing orders from exchange...")
            orders_synced = self.order_manager.load_existing_orders()
            logger.info(f"Synced {orders_synced} existing orders")
            
            # Force validation of OrderManager state against exchange state
            # More thorough validation if we detected a reconnection
            await self._validate_order_manager_state()
            
            logger.info(f"Position sync completed: {len(self.position_manager.get_all_positions())} positions")
            
        except Exception as e:
            logger.error(f"Error syncing with exchange: {e}")
    
    def _detect_websocket_reconnection(self) -> bool:
        """Detect if WebSocket has reconnected since last check"""
        try:
            if not self.websocket_client:
                return False
                
            current_connected = self.websocket_client.is_connected
            current_authenticated = self.websocket_client.is_authenticated
            
            # Check if connection state changed from disconnected to connected
            reconnected = (
                self._ws_connection_state['last_connected'] is False and
                current_connected is True
            )
            
            # Update state tracking
            if self._ws_connection_state['last_connected'] != current_connected:
                logger.info(f"WebSocket connection state changed: {self._ws_connection_state['last_connected']} -> {current_connected}")
                self._ws_connection_state['last_connected'] = current_connected
                
            if self._ws_connection_state['last_authenticated'] != current_authenticated:
                logger.info(f"WebSocket authentication state changed: {self._ws_connection_state['last_authenticated']} -> {current_authenticated}")
                self._ws_connection_state['last_authenticated'] = current_authenticated
                
            if reconnected:
                self._ws_connection_state['reconnection_count'] += 1
                logger.warning(f"WebSocket reconnection #{self._ws_connection_state['reconnection_count']} detected")
                
            return reconnected
            
        except Exception as e:
            logger.error(f"Error detecting WebSocket reconnection: {e}")
            return False
    
    async def _validate_order_manager_state(self):
        """Validate OrderManager internal state against live exchange data"""
        try:
            # Get current trading market
            market = getattr(self, '_current_market', None)
            if not market:
                return
                
            # Get pending orders from OrderManager state
            cached_orders = self.order_manager.get_pending_orders(market)
            
            # Get actual pending orders from exchange
            exchange_response = self.exchange_client.get_pending_orders(market=market)
            exchange_orders = []
            if isinstance(exchange_response, dict):
                exchange_orders = exchange_response.get('data', [])
            elif isinstance(exchange_response, list):
                exchange_orders = exchange_response
            
            # Find orders in OrderManager state that are not on exchange (stale/filled orders)
            stale_client_ids = []
            exchange_order_ids = {str(order.get('order_id')) for order in exchange_orders}
            exchange_client_ids = {order.get('client_id') for order in exchange_orders if order.get('client_id')}
            
            for cached_order in cached_orders:
                # Check if this tracked order exists on exchange
                order_exists = (
                    cached_order.exchange_order_id in exchange_order_ids or
                    cached_order.client_id in exchange_client_ids
                )
                
                if not order_exists:
                    stale_client_ids.append(cached_order.client_id)
            
            # Remove stale orders from OrderManager state
            stale_removed = 0
            for client_id in stale_client_ids:
                if client_id in self.order_manager.active_orders:
                    del self.order_manager.active_orders[client_id]
                    self.order_manager._last_status_check.pop(client_id, None)
                    stale_removed += 1
                    logger.warning(f"Removed stale order from OrderManager: {client_id}")
            
            if stale_removed > 0:
                logger.info(f"HTTP fallback: Cleaned up {stale_removed} stale orders from OrderManager")
                
        except Exception as e:
            logger.error(f"Error validating OrderManager state: {e}")
    
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
        
        # WebSocket market data statistics  
        if self.websocket_market_data:
            stats["websocket_market_data"] = self.websocket_market_data.get_cache_stats()
        
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
        
        # Circuit breaker statistics
        stats["circuit_breaker"] = self._order_circuit_breaker.get_statistics()
        
        return stats
    
    # REMOVED: get_buy_status_cache_stats() method - using direct exchange queries only
    
    def _should_log_state_change(self, market: str, state_type: str, current_value: Any) -> bool:
        """Check if a state has changed and should be logged"""
        if market not in self._logged_states:
            self._logged_states[market] = {}
        
        last_value = self._logged_states[market].get(state_type)
        
        # If this is the first time or value changed, log it
        if last_value != current_value:
            self._logged_states[market][state_type] = current_value
            return True
        
        return False
    
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