"""
Range Accumulation Trading Bot Core
Orchestrates all trading components and executes the strategy
Supports both hourly and daily trading intervals
"""
import asyncio
from datetime import datetime, timezone, time, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass

from src.exchange.exchange_factory import ExchangeFactory
from src.exchange.order_manager import OrderManager, OrderStatus
from src.exchange.order_tracker import OrderTracker, OrderSide
from src.core.order_pairing_manager import OrderPairingManager
from src.core.position_manager import PositionManager, PositionSide
from src.core.profitability import ProfitabilityValidator
from src.data.market_data import MarketDataManager
from src.core.strategy import DailyRangeStrategy, TradingSignal
from src.core.position_sizing import PositionSizer
from src.utils.logger import get_logger
from src.utils.smart_logging import log_trading_event
from src.utils.safe_conversions import safe_float, safe_int, safe_str_format
from src.core.recent_order_tracker import RecentOrderTracker


logger = get_logger(__name__)




class TradingCircuitBreaker:
    """Enhanced circuit breaker with priority levels, dynamic cooldowns, and position coverage checks"""
    
    def __init__(self, default_cooldown: int = 30):
        """
        Initialize enhanced circuit breaker
        
        Args:
            default_cooldown: Default cooldown period in seconds
        """
        self._last_actions = {}  # f"{market}_{action}" -> timestamp
        self._default_cooldown = default_cooldown
        
        # Track position coverage violations
        self._coverage_violations = {}  # market -> violation count
        self._last_coverage_check = {}  # market -> timestamp
        
        # Priority-based cooldown periods (seconds)
        self._action_cooldowns = {
            # Critical operations - shortest cooldowns
            "emergency_sell": 5,      # Emergency position closure
            "position_closure": 10,   # Manual position closure
            "orphaned_sell": 5,       # Orphaned position sell orders
            
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
        cooldown_period = self._get_cooldown_period(action_type, "normal")  # Use default priority for remaining time calc
        remaining = max(0, cooldown_period - elapsed)
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
    
    def check_position_coverage(self, market: str, position_size: float, sell_order_total: float) -> bool:
        """
        Check if position has adequate sell order coverage
        
        Args:
            market: Market symbol
            position_size: Current position size
            sell_order_total: Total amount in sell orders
            
        Returns:
            True if coverage is adequate, False if violation detected
        """
        now = datetime.now(timezone.utc)
        
        # Calculate coverage ratio
        if position_size <= 0:
            return True  # No position, no coverage needed
        
        coverage_ratio = sell_order_total / position_size
        
        # Check for violations (less than 95% coverage)
        if coverage_ratio < 0.95:
            # Track violation
            if market not in self._coverage_violations:
                self._coverage_violations[market] = 0
            self._coverage_violations[market] += 1
            
            # Log violation
            logger.warning(f"⚠️ Position coverage violation #{self._coverage_violations[market]} for {market}")
            logger.warning(f"   Position: {position_size:.6f}, Sell orders: {sell_order_total:.6f}")
            logger.warning(f"   Coverage ratio: {coverage_ratio:.1%} (minimum: 95%)")
            
            self._last_coverage_check[market] = now
            return False
        
        # Reset violation count on good coverage
        if market in self._coverage_violations:
            self._coverage_violations[market] = 0
        
        self._last_coverage_check[market] = now
        return True
    
    def should_allow_buy_with_coverage(self, market: str, position_manager, order_manager) -> bool:
        """
        Check if buy order should be allowed based on position coverage
        
        Args:
            market: Market symbol
            position_manager: Position manager instance
            order_manager: Order manager instance
            
        Returns:
            True if buy order can proceed, False if blocked by coverage issues
        """
        # Get current position
        position = position_manager.get_position(market)
        if not position or position.size <= 0:
            return True  # No position, buy allowed
        
        # Get sell orders
        pending_orders = order_manager.get_pending_orders(market)
        from src.exchange.order_tracker import OrderSide
        sell_orders = [order for order in pending_orders if order.side == OrderSide.SELL]
        total_sell_amount = sum(order.amount for order in sell_orders)
        
        # Check coverage
        if not self.check_position_coverage(market, position.size, total_sell_amount):
            logger.error(f"🚨 Circuit breaker: Blocking buy order due to inadequate position coverage for {market}")
            
            # Apply escalating cooldown based on violation count
            violations = self._coverage_violations.get(market, 0)
            cooldown = min(300, 30 * violations)  # Max 5 minutes
            
            # Set cooldown for buy orders
            action_key = f"{market}_buy_order"
            self._last_actions[action_key] = datetime.now(timezone.utc)
            
            logger.info(f"⏰ Applied {cooldown}s cooldown for buy orders on {market} (violation #{violations})")
            return False
        
        return True
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get circuit breaker statistics"""
        now = datetime.now(timezone.utc)
        active_cooldowns = {}
        
        for action_key, last_time in self._last_actions.items():
            # Extract action type from action_key (format: "market_actiontype")
            parts = action_key.split('_', 1)
            action_type = parts[1] if len(parts) > 1 else "unknown"
            cooldown_period = self._get_cooldown_period(action_type, "normal")
            
            remaining = max(0, cooldown_period - (now - last_time).total_seconds())
            if remaining > 0:
                active_cooldowns[action_key] = remaining
        
        # Add coverage violation statistics
        coverage_stats = {}
        for market, violations in self._coverage_violations.items():
            if violations > 0:
                coverage_stats[market] = {
                    "violations": violations,
                    "last_check": self._last_coverage_check.get(market).isoformat() if market in self._last_coverage_check else None
                }
        
        return {
            "default_cooldown_seconds": self._default_cooldown,
            "total_tracked_actions": len(self._last_actions),
            "active_cooldowns": active_cooldowns,
            "coverage_violations": coverage_stats
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
        self.client: Optional[Any] = None  # Can be CoinExClient or SafeModeClient
        self.market_data: Optional[MarketDataManager] = None
        self.strategy: Optional[DailyRangeStrategy] = None
        self.position_manager: Optional[PositionManager] = None
        self.order_manager: Optional[OrderManager] = None
        self.position_sizer: Optional[PositionSizer] = None
        
        # Order tracking (REST based)
        self.order_tracker: Optional[OrderTracker] = None
        self.pairing_manager: Optional[OrderPairingManager] = None
        
        self.status = BotStatus()
        
        # One-time startup cleanup tracking (not time-based, just a flag)
        self._startup_cleanup_completed = False
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

        # Recent order tracking to prevent duplicates and handle phantom orders
        self.recent_order_tracker = RecentOrderTracker(grace_period_seconds=30)

        # Track orphaned sells that are covering current-period buy fills
        self._tracked_orphaned_sells: Dict[str, Dict[str, Any]] = {}  # market -> {'sell_order_id': str, 'client_id': str, 'period_key': str, 'buy_fill_time': datetime, 'target_amount': float}
        
        # Daily reset tracking to ensure it's only done once per day
        self._daily_reset_completed = {}  # market -> date of last reset
        self._last_reset_date = {}  # market -> date when reset was done
        
    async def initialize(self):
        """Initialize all bot components"""
        from config.settings import TRADING_INTERVAL
        interval_name = "Hourly" if TRADING_INTERVAL == 'hourly' else "Daily"
        logger.info(f"Initializing {interval_name} Range Accumulation Bot...")
        
        try:
            # Initialize core components - Exchange Factory handles testing vs production
            self.client = ExchangeFactory.create_exchange()
            
            # Log the mode being used
            if ExchangeFactory.is_testing_mode():
                logger.info("🧪 Bot initialized in TESTING mode (SafeModeClient)")
                logger.info("   • Real market data will be used")
                logger.info("   • Orders will be SIMULATED (safe)")
            else:
                logger.warning("⚠️ Bot initialized in PRODUCTION mode (real trading)")
                logger.warning("   • Real orders will be placed!")
                logger.warning("   • Real money will be used!")
            
            # Initialize market data manager (HTTP polling only)
            self.market_data = MarketDataManager(self.client)
            self.strategy = DailyRangeStrategy(self.market_data)
            self.position_manager = PositionManager(self.client)
            
            # Initialize trading components
            validator = ProfitabilityValidator()
            self.order_manager = OrderManager(self.client, validator)
            self.position_sizer = PositionSizer(self.market_data, self.position_manager)
            
            # Link recent order tracker to order manager
            self.order_manager.recent_order_tracker = self.recent_order_tracker
            
            # Initialize order tracking components (REST-based)
            self.order_tracker = OrderTracker(self.client)
            self.pairing_manager = OrderPairingManager(
                self.order_tracker, self.order_manager, self.client
            )

            # Connect OrderManager to OrderTracker events for synchronized order cleanup
            self.order_tracker.add_order_complete_handler(self.order_manager.handle_order_completion)
            logger.info("✓ Connected OrderManager to OrderTracker for automatic order cleanup")

            # Establish unified coordination for immediate pairing
            self.order_manager.set_tracking_coordination(self.order_tracker, self.pairing_manager, self)
            logger.info("✓ Unified pairing coordination established between OrderManager and tracking system")

            # Track orphaned sell completion events so restart coverage is respected
            self.order_tracker.add_order_complete_handler(self._handle_order_completion_for_orphan_tracking)
            logger.info("✓ Registered orphaned sell tracking handler")

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
    
    async def _clean_startup_state(self, market: str):
        """Clean startup state to ensure fresh initialization"""
        try:
            logger.info(f"🧹 Performing startup cleanup for {market}...")
            
            # Clear in-memory signal cache for this market
            if hasattr(self.strategy, '_current_signals') and market in self.strategy._current_signals:
                del self.strategy._current_signals[market]
                logger.debug(f"Cleared in-memory signal cache for {market}")
            
            # Clear pairing rules for this market
            if self.pairing_manager and market in self.pairing_manager.pairing_rules:
                del self.pairing_manager.pairing_rules[market]
                logger.debug(f"Cleared pairing rules for {market}")
                
            logger.info(f"✅ Startup cleanup completed for {market}")
            
        except Exception as e:
            logger.warning(f"Error during startup cleanup for {market}: {e}")

    async def set_trading_market(self, market: str):
        """Set up a market for trading with proper initialization order"""
        
        # 1. VALIDATE MARKET FIRST  
        if not market or market == 'None':
            raise ValueError("No valid market specified - this should have been handled by main.py")
        
        # Additional validation for market format
        if not (len(market) >= 4 and market.endswith('USDT') and market[:-4].isalpha()):
            logger.error(f"Invalid market format: {market} (should be like ETHUSDT, ADAUSDT, etc.)")
            raise ValueError(f"Invalid market format: {market}")
        
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
                    logger.info("🛡️ Skipping leverage adjustment - trusting bot orders have correct settings")
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
            
            # Generate initial signal FIRST before configuring pairing rules
            await self._check_and_generate_signals(market)
            
            # Get current trading signal to configure pairing rule (after generation)
            current_signal = self.strategy.get_current_signal(market)
            if current_signal:
                # Configure pairing rules with current signal's sell price
                self.pairing_manager.configure_pairing_rule(
                    market=market,
                    sell_price_levels=[current_signal.sell_price],  # Use today's strategy sell price
                    min_fill_amount=0.001  # Minimum fill to trigger sell orders
                )
                logger.info(f"Configured pairing rules for {market} with sell price ${current_signal.sell_price:.2f}")
            else:
                # This should rarely happen now that we generate signals first
                logger.error(f"❌ CRITICAL: No signal available for {market} even after generation attempt")
                logger.error("This could cause the infinite buy loop bug!")
                
                # Force signal generation with force=True flag
                logger.info(f"🔧 Attempting forced signal generation for {market}")
                forced_signal = self.strategy.generate_daily_signal(market, force=True)
                
                if forced_signal:
                    logger.info(f"✅ Forced signal generation successful for {market}")
                    # Configure pairing rules with forced signal
                    self.pairing_manager.configure_pairing_rule(
                        market=market,
                        sell_price_levels=[forced_signal.sell_price],
                        min_fill_amount=0.001
                    )
                    logger.info(f"Configured pairing rules for {market} with forced signal sell price ${forced_signal.sell_price:.2f}")
                else:
                    # Absolute fallback - should never happen
                    logger.error(f"❌ FATAL: Cannot generate signal for {market} - refusing to start trading")
                    raise ValueError(f"Cannot generate trading signal for {market}. This prevents safe trading.")
            
            logger.info(f"✅ Configured pairing rules for {market} with Daily Range Strategy (one sell price per buy)")
            
            # Initialize tracking for this market
            # REMOVED: Cache update - now using direct exchange queries
            
            logger.debug(f"REST-only mode active for {market}; real-time subscriptions removed")
    
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
            
            
            # Display current trading strategy every cycle for observation (exact prices)
            from config.settings import TRADING_INTERVAL
            timeframe_label = 'HOURLY' if TRADING_INTERVAL == 'hourly' else 'DAILY'

            for market in self.trading_markets:
                signal = self.strategy.get_current_signal(market)
                if signal:
                    # Calculate exact amount based on current position sizing
                    account_balance = self.get_account_balance()
                    position_size = self.position_sizer.calculate_position_size(
                        market, signal.buy_price, account_balance
                    )
                    if position_size.is_valid:
                        # Enhanced fixed-position daily signal summary for easy monitoring
                        current_time = now.strftime("%H:%M:%S")
                        buy_price_str = safe_str_format(safe_float(signal.buy_price), '.8f')
                        sell_price_str = safe_str_format(safe_float(signal.sell_price), '.8f')
                        amount_str = safe_str_format(safe_float(position_size.quantity), '.8f')
                        size_str = safe_str_format(safe_float(position_size.size_usdt), '.2f')
                        logger.info(
                            f"📊 {timeframe_label} SIGNAL | {market} | Buy=${buy_price_str} | "
                            f"Sell=${sell_price_str} | Amount={amount_str} | "
                            f"Size=${size_str} USDT | Updated: {current_time}"
                        )
            
            # Update account and positions periodically with enhanced frequencies for better state consistency
            # Account update every 2 minutes (reduced from 5 min) for better balance tracking
            if (not self._last_account_update or 
                (now - self._last_account_update).seconds > 120):
                await self._update_account_status()
                self._last_account_update = now
            
            # Position sync every 1 minute for critical state validation and order status checking
            if (not self._last_positions_sync or 
                (now - self._last_positions_sync).seconds > 60):
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
            # Check if we need to generate new signals
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
        """Check if we need to generate new signals based on trading timeframe"""
        from config.settings import TRADING_INTERVAL, SIGNAL_GENERATION_TIME
        
        now = datetime.now(timezone.utc)
        signal = None
        generation_attempted = False

        if TRADING_INTERVAL == 'hourly':
            current_hour = now.strftime('%Y-%m-%d-%H')
            last_generation = self.last_signal_generation.get(market)
            if (not last_generation or
                (hasattr(last_generation, 'hour') and
                 last_generation.strftime('%Y-%m-%d-%H') != current_hour)):
                
                logger.info(f"Generating new hourly signal for {market} (hour: {current_hour})")
                generation_attempted = True
                signal = self.strategy.generate_hourly_signal(market)
        else:  # Daily
            signal_time = time.fromisoformat(SIGNAL_GENERATION_TIME)
            today_signal_time = datetime.combine(now.date(), signal_time, timezone.utc)
            last_generation = self.last_signal_generation.get(market)
            if (now >= today_signal_time and
                (not last_generation or last_generation.date() < now.date())):
                
                logger.info(f"Generating new daily signal for {market}")
                generation_attempted = True
                signal = self.strategy.generate_daily_signal(market)

        if signal:
            # If signal was generated successfully, record the time and update pairing rules
            self.last_signal_generation[market] = now
            self.status.last_signal_time = now
            timeframe_label = 'hourly' if TRADING_INTERVAL == 'hourly' else 'daily'
            buy_price_str = safe_str_format(safe_float(signal.buy_price), '.8f')
            sell_price_str = safe_str_format(safe_float(signal.sell_price), '.8f')
            log_trading_event(
                'signal_generation',
                f"Generated {timeframe_label} signal for {market}: Buy=${buy_price_str}, Sell=${sell_price_str}"
            )
            
            if self.pairing_manager:
                self.pairing_manager.configure_pairing_rule(
                    market=market,
                    sell_price_levels=[signal.sell_price],
                    min_fill_amount=0.001
                )
                logger.info(
                    f"Updated pairing rule for {market} with new sell price ${safe_str_format(safe_float(signal.sell_price), '.8f')}"
                )
        elif generation_attempted:
            # If we tried to generate a signal but failed, log it.
            # This structure prevents repeated generation attempts on failure.
            logger.warning(f"Failed to generate signal for {market}, will not retry until next period.")
            # We still update the timestamp to prevent rapid retries within the same period
            self.last_signal_generation[market] = now
    
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

                validator = ProfitabilityValidator()

                # Ensure the current signal's sell price clears the profit floor relative to the signal buy
                min_sell_price = validator.calculate_min_profitable_price(signal.buy_price)

                if exit_price < min_sell_price:
                    logger.debug(
                        f"Exit price ${exit_price:.4f} below minimum profitable price ${min_sell_price:.4f} for signal buy "
                        f"${signal.buy_price:.4f} - skipping exit"
                    )
                    return

                # Informational check against the aggregated position (long-only); do not block the exit,
                # but surface the expected PnL for observability.
                entry_cost = position.total_cost
                position_profit = validator.is_position_profitable(
                    position.avg_entry_price,
                    position.size,
                    exit_price,
                    entry_cost
                )

                if not position_profit.is_profitable:
                    logger.debug(
                        f"Aggregated position for {market} would exit at {position_profit.profit_percent:.2f}% "
                        "(below target) – proceeding because current signal leg is profitable"
                    )

                incremental_profit_percent = (
                    (exit_price * (1 - validator.maker_fee) - signal.buy_price * (1 + validator.taker_fee))
                    / signal.buy_price
                ) * 100

                log_trading_event(
                    'profitable_exit',
                    f"Profitable exit opportunity for {market}: signal_buy=${signal.buy_price:.2f}, "
                    f"exit=${exit_price:.2f}, profit={incremental_profit_percent:.2f}%"
                )

                # Place exit order
                await self._place_exit_order(position, exit_price)
                
            except Exception as e:
                logger.error(f"Error checking exit for position {position.position_id}: {e}")
    
    async def _check_and_cover_orphaned_positions(self, market: str):
        """
        Check for orphaned positions (positions without sell orders) and place sell orders
        This runs INDEPENDENTLY every cycle to ensure all positions have exit orders
        CRITICAL: This function ONLY places SELL orders, never BUY orders
        """
        try:
            # Get current position for this market
            position = self.position_manager.get_position(market)
            if not position or position.size <= 0:
                # No position to cover
                return
            
            # Get all pending sell orders for this market
            pending_orders = self.order_manager.get_pending_orders(market)
            sell_orders = [order for order in pending_orders if order.side == OrderSide.SELL]
            
            # Calculate total sell order amount
            total_sell_amount = sum(order.amount for order in sell_orders)
            
            # Check if position is fully covered
            uncovered_amount = position.size - total_sell_amount
            
            # Get minimum order amount for the market to use as tolerance
            market_info = self.market_data.get_market_info(market)
            min_amount = safe_float(market_info.get('min_amount', 0.000001))

            if uncovered_amount > min_amount:
                logger.warning(f"🔧 ORPHANED POSITION DETECTED: {market}")
                logger.warning(f"   Position size: {position.size:.6f}")
                logger.warning(f"   Sell orders total: {total_sell_amount:.6f}")
                logger.warning(f"   Uncovered amount: {uncovered_amount:.6f}")
                
                # Get current signal to determine sell price
                signal = self.strategy.get_current_signal(market)
                if not signal:
                    logger.error(f"❌ Cannot cover orphaned position - no signal available for {market}")
                    return
                
                # Place sell order for uncovered amount at strategy sell price
                sell_price_value = safe_float(signal.sell_price)
                logger.info(
                    f"📍 Placing sell order to cover orphaned position: {safe_str_format(uncovered_amount, '.8f')} "
                    f"@ ${safe_str_format(sell_price_value, '.8f')}"
                )
                
                sell_order = self.order_manager.place_sell_order(
                    market=market,
                    amount=uncovered_amount,
                    price=sell_price_value,
                    position_size=uncovered_amount * sell_price_value,
                    is_hide=True,
                    is_orphaned=True  # Mark as orphaned position sell
                )
                
                if sell_order:
                    logger.info(f"✅ Orphaned position covered with sell order: {sell_order.client_id}")
                    log_trading_event('orphaned_position_covered', 
                                    f"Placed sell order for uncovered position: {safe_str_format(uncovered_amount, '.8f')} "
                                    f"{market} @ ${safe_str_format(sell_price_value, '.8f')}")
                    self._track_orphaned_sell_if_needed(market, sell_order, uncovered_amount)
                else:
                    logger.error(f"❌ Failed to place sell order for orphaned position in {market}")
                    log_trading_event('orphaned_position_failed', 
                                    f"Failed to cover orphaned position: {uncovered_amount:.6f} {market}")
            
        except Exception as e:
            logger.error(f"Error checking orphaned positions for {market}: {e}")
            # Don't crash the bot on error - log and continue

    def _track_orphaned_sell_if_needed(self, market: str, sell_order, uncovered_amount: float) -> None:
        """Track orphaned sell if it matches a current-period buy fill"""
        try:
            from config.settings import TRADING_INTERVAL
            timeframe = TRADING_INTERVAL
            now = datetime.now(timezone.utc)
            period_key = self._get_period_key(now, timeframe)

            tolerance = self._get_market_amount_tolerance(market)

            unmatched_pairs = self.order_tracker.get_unmatched_buy_fills()
            matching_pair = None

            for pair in unmatched_pairs:
                if pair.buy_order.market != market:
                    continue
                buy_period_key = self._get_period_key(pair.buy_order.created_at, timeframe)
                if buy_period_key != period_key:
                    continue
                remaining_amount = pair.get_remaining_buy_amount()
                if abs(remaining_amount - uncovered_amount) <= tolerance:
                    matching_pair = pair
                    break

            if not matching_pair:
                logger.debug(f"No current-period unmatched buy fill matched orphaned sell for {market}")
                return

            self._tracked_orphaned_sells[market] = {
                'sell_order_id': str(sell_order.exchange_order_id),
                'client_id': sell_order.client_id,
                'period_key': period_key,
                'buy_fill_time': matching_pair.buy_order.updated_at,
                'target_amount': uncovered_amount
            }

            period_label = 'hour' if timeframe == 'hourly' else 'day'
            logger.info(f"📌 Tracking orphaned sell {sell_order.client_id} as exit for current {period_label} buy in {market}")
            log_trading_event('orphaned_sell_tracked', 
                              f"Tracking orphaned sell {sell_order.client_id} as exit for current {period_label} buy in {market}")

        except Exception as e:
            logger.debug(f"Unable to track orphaned sell for {market}: {e}")

    def _get_period_key(self, dt: datetime, timeframe: str) -> str:
        """Return normalized period key for datetime based on timeframe"""
        if timeframe == 'hourly':
            return dt.strftime('%Y-%m-%d-%H')
        return dt.date().isoformat()

    def _get_market_amount_tolerance(self, market: str) -> float:
        """Get tolerance for comparing order amounts based on market settings"""
        try:
            market_info = self.market_data.get_market_info(market)
            min_amount = safe_float(market_info.get('min_amount', 0.000001))
        except Exception:
            min_amount = 0.000001
        return max(min_amount, 0.000001)
    
    async def _check_entry_opportunities(self, market: str, signal: TradingSignal):
        """Check for new entry opportunities and manage existing positions"""
        try:
            current_price = self.market_data.get_current_price(market)
            if not current_price:
                return
            
            # Position balancing is now handled by _check_and_cover_orphaned_positions()
            # which runs independently at the start of each cycle
            
            # Second priority: Check for new buy opportunities
            # CRITICAL FIX: Clean up duplicate orders before checking if we should place buy
            buy_status = self._get_exchange_buy_status(market)
            total_buy_orders = buy_status.get('total_buy_orders', 0)

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
    
    def _check_period_buy_status(self, market: str) -> Dict[str, Any]:
        """Check buy order status for the active trading period (hour/day) with stale order cleanup"""
        try:
            # Import and check trading timeframe
            from config.settings import TRADING_INTERVAL
            timeframe = TRADING_INTERVAL
            # Force sync with exchange to ensure fresh data and cleanup stale orders
            log_trading_event('buy_status_sync', f"Forcing order sync with exchange for {market}")
            self.order_manager.load_existing_orders(market)
            
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
            
            # Set up period checking based on timeframe
            now = datetime.now(timezone.utc)
            if timeframe == 'hourly':
                current_period = now.strftime('%Y-%m-%d-%H')
                period_name = "current hour"
                # For hourly: orders older than 2 hours are stale
                stale_threshold = now - timedelta(hours=2)
            else:
                current_period = now.date()
                period_name = "current day"
                # For daily: orders older than 1 day are stale
                stale_threshold = now - timedelta(days=1)
            
            current_period_buy_orders = []
            stale_orders = []
            
            # Categorize orders by period
            for order in buy_orders:
                if timeframe == 'hourly':
                    order_period = order.created_at.strftime('%Y-%m-%d-%H')
                    is_current_period = order_period == current_period
                    is_stale = order.created_at < stale_threshold
                else:
                    order_period = order.created_at.date()
                    is_current_period = order_period == current_period
                    is_stale = order.created_at < stale_threshold
                
                if is_current_period:
                    current_period_buy_orders.append(order)
                elif is_stale:
                    stale_orders.append(order)
            
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
                'has_current_interval_buy': len(current_period_buy_orders) > 0,
                'current_interval_orders': current_period_buy_orders,
                'interval_label': period_name,
                'cancelled_stale': cancelled_count,
                'total_buy_orders': len(buy_orders),
                'all_buy_orders': buy_orders
            }
            
            # Validation: Cross-check with exchange API to detect discrepancies
            try:
                direct_exchange_orders = self.client.get_pending_orders(market=market)
                exchange_buy_orders = []
                if isinstance(direct_exchange_orders, dict):
                    exchange_data = direct_exchange_orders.get('data', [])
                    exchange_buy_orders = [o for o in exchange_data if o.get('side') == 'buy']
                elif isinstance(direct_exchange_orders, list):
                    exchange_buy_orders = [o for o in direct_exchange_orders if o.get('side') == 'buy']
                
                # Filter current period orders from exchange
                current_period_exchange_buys = []
                for order in exchange_buy_orders:
                    created_time = order.get('created_at', 0)
                    if isinstance(created_time, str):
                        from dateutil import parser
                        order_datetime = parser.parse(created_time)
                    else:
                        order_datetime = datetime.fromtimestamp(created_time / 1000, timezone.utc)
                    
                    if timeframe == 'hourly':
                        order_period = order_datetime.strftime('%Y-%m-%d-%H')
                        is_current_period = order_period == current_period
                    else:
                        order_period = order_datetime.date()
                        is_current_period = order_period == current_period
                    
                    if is_current_period:
                        current_period_exchange_buys.append(order)
                
                # Log discrepancy if found
                manager_count = len(current_period_buy_orders)
                exchange_count = len(current_period_exchange_buys)
                if manager_count != exchange_count:
                    log_trading_event('order_discrepancy', 
                        f"Buy order count mismatch for {market}: manager={manager_count}, exchange={exchange_count}")
                    logger.warning(f"Order manager vs exchange discrepancy detected for {market}:")
                    logger.warning(f"  Manager orders: {[o.client_id for o in current_period_buy_orders]}")
                    logger.warning(f"  Exchange orders: {[o.get('client_id', o.get('order_id')) for o in current_period_exchange_buys]}")
                    
                    # ENHANCED PHANTOM CLEANUP: Clean phantom buy orders with grace period
                    exchange_client_ids = {o.get('client_id') for o in current_period_exchange_buys if o.get('client_id')}
                    phantom_orders = []
                    for order in current_period_buy_orders:
                        if order.client_id not in exchange_client_ids:
                            # Check order age before marking as phantom
                            order_age = (datetime.now(timezone.utc) - order.created_at).total_seconds()
                            
                            if order_age < 30:
                                # Too young - keep it (exchange visibility delay)
                                logger.info(f"👻 Keeping recent order {order.client_id} ({order_age:.1f}s old) - waiting for exchange visibility")
                                continue
                                
                            elif order_age < 60:
                                # Track as phantom candidate for verification
                                self.recent_order_tracker.mark_as_phantom_candidate(
                                    market, order.client_id, order.amount
                                )
                                logger.warning(f"👻 Marking phantom candidate: {order.client_id} ({order_age:.1f}s old)")
                                # Still add to phantom_orders for cleanup
                                phantom_orders.append(order)
                            else:
                                # Old enough - safe to remove
                                logger.warning(f"👻 Old phantom order: {order.client_id} ({order_age:.1f}s old)")
                                phantom_orders.append(order)
                    
                    if phantom_orders:
                        logger.warning(f"Cleaning {len(phantom_orders)} phantom buy orders for {market}")
                        for phantom_order in phantom_orders:
                            logger.info(f"🗑️ Removing phantom buy order: {phantom_order.client_id}")
                            if phantom_order.client_id in self.order_manager.active_orders:
                                del self.order_manager.active_orders[phantom_order.client_id]
                            log_trading_event('phantom_cleanup', f"Removed phantom buy order {phantom_order.client_id} for {market}")
                        
                        # Recalculate result after cleanup
                        remaining_period_buys = [o for o in current_period_buy_orders if o.client_id in exchange_client_ids]
                        result['has_current_interval_buy'] = len(remaining_period_buys) > 0
                        result['current_interval_orders'] = remaining_period_buys
                        result['total_buy_orders'] = len([o for o in buy_orders if o.client_id in exchange_client_ids])
                        logger.info(f"✅ After phantom cleanup: has_{period_name.replace(' ', '_')}_buy={result['has_current_interval_buy']}")
                    
                    # REMOVED: Cache clearing logic - now using direct exchange queries only
                
            except Exception as validation_error:
                logger.debug(f"Validation check failed for {market}: {validation_error}")
            
            if result['has_current_interval_buy']:
                buy_state = len(current_period_buy_orders)
                if self._should_log_state_change(market, 'current_period_buy_count', buy_state):
                    log_trading_event(
                        'buy_status',
                        f"Found {buy_state} buy order(s) from {period_name} for {market} ({timeframe} strategy)"
                    )
            else:
                # Update state tracker so next non-zero count gets logged immediately
                self._should_log_state_change(market, 'current_period_buy_count', 0)
            
            if cancelled_count > 0:
                log_trading_event('stale_cleanup', f"Cancelled {cancelled_count} stale buy order(s) for {market}")
            
            return result
            
        except Exception as e:
            logger.error(f"Error checking daily buy status for {market}: {e}")
            return {
                'has_current_interval_buy': True,  # Conservative: assume we have buy to prevent multiple orders
                'current_interval_orders': [],
                'interval_label': "current period",
                'cancelled_stale': 0,
                'total_buy_orders': 0,
                'all_buy_orders': []
            }
    
    def _has_current_interval_uncovered_buy_fills(self, market: str) -> bool:
        """
        Determine whether the current trading period still has uncovered buy fills.

        Returns True when a buy fill in the active period exists without a completed sell,
        which should block placement of another buy for the same cycle.
        """
        try:
            uncovered = self._get_current_period_uncovered_buy_fills(market)
            if uncovered:
                return True

            # Fallback: use REST fills when tracker data is unavailable or empty
            from config.settings import TRADING_INTERVAL
            from src.utils.safe_conversions import safe_float

            timeframe = (TRADING_INTERVAL or '').lower()
            now_utc = datetime.now(timezone.utc)
            if timeframe == 'hourly':
                current_period_key = now_utc.strftime('%Y-%m-%d-%H')
            else:
                current_period_key = now_utc.date()

            if timeframe == 'hourly':
                period_start = now_utc.replace(minute=0, second=0, microsecond=0)
            else:
                period_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)

            transactions = self.client.get_user_deals(
                market=market,
                side='buy',
                start_time=int(period_start.timestamp() * 1000),
                limit=200
            )

            if transactions:
                if isinstance(transactions, dict):
                    deals = transactions.get('data', [])
                elif isinstance(transactions, list):
                    deals = transactions
                else:
                    deals = []

                for fill in deals:
                    fill_time_value = safe_float(fill.get('created_at', 0))
                    if fill_time_value <= 0:
                        continue

                    fill_dt = datetime.fromtimestamp(fill_time_value / 1000, timezone.utc)

                    if timeframe == 'hourly':
                        fill_key = fill_dt.strftime('%Y-%m-%d-%H')
                    else:
                        fill_key = fill_dt.date()

                    if fill_key != current_period_key:
                        continue

                    logger.debug(
                        f"Fallback uncovered buy detected via user_deals for {market}: order {fill.get('order_id')} at {fill_dt}"
                    )
                    return True

            logger.debug(f"🔍 No uncovered buy fills detected for {market} in current period")
            return False

        except Exception as e:
            logger.warning(f"Error checking current-period filled buys for {market}: {e}")
            return False
    
    def _calculate_position_sell_balance(self, market: str) -> Dict[str, Any]:
        """Calculate position vs sell order balance using proven endpoints with exchange-aware counting"""
        try:
            # Get current position using proven method
            position = self.position_manager.get_position(market)
            position_size = position.size if position else 0.0
            
            # Get bot-created pending sell orders
            pending_orders = self.order_manager.get_pending_orders(market)
            bot_sell_orders = [o for o in pending_orders if o.side.value == 'sell']
            bot_sell_amount = sum(order.amount for order in bot_sell_orders)
            
            # CRITICAL: Also get ALL pending sell orders from exchange (including manual orders)
            total_sell_amount = bot_sell_amount
            exchange_sell_amount = 0.0
            try:
                # Fetch all pending orders directly from exchange 
                response = self.order_manager.client.get_pending_orders(market=market)
                if isinstance(response, dict):
                    orders_data = response.get('data', [])
                elif isinstance(response, list):
                    orders_data = response
                else:
                    orders_data = []
                
                # Count ALL sell orders from exchange (bot + manual)
                for order_data in orders_data:
                    if order_data.get('side') == 'sell':
                        order_amount = safe_float(order_data.get('amount', 0))
                        exchange_sell_amount += order_amount
                
                # Use exchange total if it's higher (includes manual orders bot doesn't track)
                if exchange_sell_amount > bot_sell_amount:
                    total_sell_amount = exchange_sell_amount
                    logger.info(f"📊 Exchange has more sell orders than bot tracking: {safe_str_format(exchange_sell_amount, '.6f')} vs {safe_str_format(bot_sell_amount, '.6f')}")
                
            except Exception as e:
                logger.warning(f"Failed to fetch exchange sell orders for {market}, using bot tracking only: {e}")
                total_sell_amount = bot_sell_amount
            
            # Calculate missing sell amount
            missing_sell = max(0, position_size - total_sell_amount)
            
            # Get minimum order amount for tolerance
            market_info = self.market_data.get_market_info(market)
            min_amount = safe_float(market_info.get('min_amount', 0.000001))

            is_balanced = missing_sell < min_amount  # Allow tiny rounding differences
            
            result = {
                'position_size': position_size,
                'total_sells': total_sell_amount,
                'bot_sells': bot_sell_amount,
                'exchange_sells': exchange_sell_amount,
                'missing_sell': missing_sell,
                'is_balanced': is_balanced,
                'sell_orders': bot_sell_orders,
                'position_exists': position is not None,
                'exchange_error': False
            }
            
            if position_size > 0:
                balance_state = (
                    round(position_size, 6),
                    round(total_sell_amount, 6),
                    round(missing_sell, 6)
                )
                if self._should_log_state_change(market, 'position_balance', balance_state):
                    logger.info(
                        f"Position balance for {market}: {position_size:.6f} position, "
                        f"{total_sell_amount:.6f} total sells (bot: {bot_sell_amount:.6f}, exchange: {exchange_sell_amount:.6f}), "
                        f"{missing_sell:.6f} missing"
                    )
            
            return result
            
        except Exception as e:
            logger.error(f"Error calculating position-sell balance for {market}: {e}")
            return {
                'position_size': 0.0,
                'total_sells': 0.0,
                'bot_sells': 0.0,
                'exchange_sells': 0.0,
                'missing_sell': 0.0,
                'is_balanced': True,
                'sell_orders': [],
                'position_exists': False,
                'exchange_error': True
            }
    
    def _calculate_optimal_sell_price(self, market: str, position, current_price: float) -> Dict[str, float]:
        """Calculate optimal sell price for bot restart scenario"""
        try:
            entry_price = safe_float(position.avg_entry_price)
            current_price = safe_float(current_price)

            # Safety floor: ensure we target at least 1% profit over average entry
            base_sell_price = safe_float(entry_price * 1.01)

            strategy_price = None
            try:
                if self.strategy:
                    signal = self.strategy.get_current_signal(market)
                    if signal and getattr(signal, 'sell_price', None):
                        strategy_price = safe_float(signal.sell_price)
            except Exception as signal_error:
                logger.debug(f"Could not load strategy sell price for {market}: {signal_error}")

            # Always respect the higher of strategy or base sell price
            minimum_target = base_sell_price
            if strategy_price is not None:
                minimum_target = safe_float(max(minimum_target, strategy_price))

            if current_price and current_price > minimum_target:
                optimal_price = current_price
                improvement = safe_float(current_price - minimum_target)
                reason = "market_higher_than_minimum"
            else:
                optimal_price = minimum_target
                improvement = 0.0
                reason = "minimum_profit_floor"

            return {
                'price': optimal_price,
                'base_price': minimum_target,
                'improvement': improvement,
                'reason': reason
            }
        except Exception as e:
            logger.error(f"Error calculating optimal sell price for {market}: {e}")
            fallback_price = safe_float(safe_float(position.avg_entry_price) * 1.01)
            return {
                'price': fallback_price,
                'base_price': fallback_price,
                'improvement': 0.0,
                'reason': 'fallback_error'
            }
    
    def _place_missing_sell_order(self, market: str, missing_amount: float) -> bool:
        """Place sell order for missing position coverage with intelligent price adjustment and funding fee protection"""
        try:
            # CRITICAL: Pre-validate to prevent over-selling
            current_balance = self._calculate_position_sell_balance(market)
            if current_balance['position_size'] <= 0:
                logger.error(f"🚨 Cannot place sell order - no position exists for {market}")
                return False

            if current_balance.get('exchange_error'):
                logger.warning(f"⚠️ Skipping orphaned sell placement for {market} - exchange data unavailable")
                log_trading_event('orphaned_skip', f"Skipped orphaned sell for {market} due to pending order API error")
                return False
            
            # Determine precise uncovered amount using freshest balance data
            tolerance = self._get_market_amount_tolerance(market)
            uncovered_amount = max(0.0, current_balance['position_size'] - current_balance['total_sells'])

            if uncovered_amount <= tolerance:
                logger.debug(f"No meaningful uncovered amount remaining for {market} (<= tolerance)")
                return False

            if abs(missing_amount - uncovered_amount) > tolerance:
                logger.debug(f"Adjusting requested missing amount for {market}: {missing_amount:.6f} -> {uncovered_amount:.6f}")
            missing_amount = uncovered_amount

            # Check if adding this sell order would exceed position size
            total_sells_after = current_balance['total_sells'] + missing_amount
            if total_sells_after > current_balance['position_size'] + tolerance:
                logger.error("🚨 OVER-SELLING PREVENTED: Sell order would exceed position size!")
                logger.error(f"   Position: {current_balance['position_size']:.6f}")
                logger.error(f"   Current sells: {current_balance['total_sells']:.6f}")
                logger.error(f"   Requested sell: {missing_amount:.6f}")
                logger.error(f"   Would total: {total_sells_after:.6f} (EXCEEDS POSITION)")
                log_trading_event('overselling_prevented', f"Prevented over-selling for {market}: {total_sells_after:.6f} > {current_balance['position_size']:.6f}")
                return False
            
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
            logger.info(
                f"📊 Entry: ${safe_str_format(safe_float(position.avg_entry_price), '.8f')} | "
                f"Current: ${safe_str_format(safe_float(current_price), '.8f')}"
            )
            logger.info(
                f"📊 Minimum target (≥1% profit & strategy): "
                f"${safe_str_format(safe_float(optimal_sell_price['base_price']), '.8f')}"
            )
            logger.info(
                f"✅ Optimal sell price: ${safe_str_format(safe_float(optimal_sell_price['price']), '.8f')} "
                f"({optimal_sell_price['reason']})"
            )
            if safe_float(optimal_sell_price['improvement']) > 0:
                logger.info(
                    f"💰 Profit improvement: +${safe_str_format(safe_float(optimal_sell_price['improvement']), '.8f')}"
                )
            
            # Place the missing sell order using optimal price with orphaned marker
            order = self.order_manager.place_sell_order(
                market=market,
                amount=missing_amount,
                price=safe_float(optimal_sell_price['price']),
                position_size=missing_amount * safe_float(optimal_sell_price['price']),
                is_hide=True,
                is_orphaned=True  # Mark as orphaned position sell order
            )
            
            if order:
                logger.info(f"✅ Missing sell order placed: {order.client_id}")
                logger.info("📌 Orphaned sell order marked - will not block new buy orders")
                
                # CRITICAL: Immediately recalculate position balance with fresh exchange data
                updated_balance = self._calculate_position_sell_balance(market)
                logger.info("🔄 Position balance updated after order placement:")
                logger.info(f"   Position: {updated_balance['position_size']:.6f}, "
                           f"Total sells: {updated_balance['total_sells']:.6f}, "
                           f"Missing: {updated_balance['missing_sell']:.6f}")
                
                # Warn if still missing sell coverage (indicates potential issue)
                coverage_tolerance = self._get_market_amount_tolerance(market)
                if updated_balance['missing_sell'] > coverage_tolerance:
                    logger.warning(f"⚠️ Position still partially uncovered after sell placement: "
                                 f"{updated_balance['missing_sell']:.6f} {market} remaining")
                
                # Track the order for automatic pairing
                if self.order_tracker:
                    self.order_tracker.track_order(
                        order_id=str(order.exchange_order_id),
                        client_id=order.client_id,
                        market=market,
                        side=OrderSide.SELL,
                        amount=missing_amount,
                        price=optimal_sell_price['price']
                    )
                return True
            else:
                logger.error(f"❌ Failed to place missing sell order for {market}")
                return False
                
        except Exception as e:
            logger.error(f"Error placing missing sell order for {market}: {e}")
            return False
    
    def _get_period_pending_sell_orders(self, market: str) -> Dict[str, Any]:
        """Count pending sell orders for the current trading period (day/hour)."""
        from src.utils.safe_conversions import safe_float

        from config.settings import TRADING_INTERVAL

        timeframe = (TRADING_INTERVAL or '').lower()
        is_hourly = timeframe == 'hourly'

        now_utc = datetime.now(timezone.utc)
        if is_hourly:
            current_period = now_utc.strftime('%Y-%m-%d-%H')
            period_label = 'the current hour'
            descriptor = "this hour's"
        else:
            current_period = now_utc.date()
            period_label = 'today'
            descriptor = "today's"
        
        try:
            # Use proven direct API call logic from test_race_condition.py
            orders_response = self.client.get_pending_orders(market)
            if isinstance(orders_response, dict):
                orders = orders_response.get('data', [])
            else:
                orders = orders_response if isinstance(orders_response, list) else []
        
            # Enhanced order analysis logging with detailed information
            if len(orders) > 0:
                logger.info(f"📊 Analyzing {len(orders)} total pending orders for {market}")
        
            # Filter for sell orders placed during the current period
            sell_orders_current_period = []
            sell_orders_previous_period = []
            buy_orders = []
        
            for order in orders:
                created_at = order.get('created_at', 0)
                client_id = order.get('client_id', '')
                side = order.get('side', '')
                amount = safe_float(order.get('amount', 0))
                price = safe_float(order.get('price', 0))
        
                # DEBUG: Log raw timestamp data
                logger.debug(f"🔍 DEBUG Raw order data: created_at={created_at} (type: {type(created_at)}), client_id={client_id}")
        
                # Use proven timestamp parsing logic from test
                if isinstance(created_at, (int, float)):
                    order_datetime = datetime.fromtimestamp(created_at / 1000, timezone.utc)
                    logger.debug(f"🔍 DEBUG Timestamp parsed as int/float: {created_at} → {order_datetime}")
                else:
                    order_datetime = datetime.fromisoformat(str(created_at).replace('Z', '+00:00'))
                    if order_datetime.tzinfo is None:
                        order_datetime = order_datetime.replace(tzinfo=timezone.utc)
                    else:
                        order_datetime = order_datetime.astimezone(timezone.utc)
                    logger.debug(f"🔍 DEBUG Timestamp parsed as string: {created_at} → {order_datetime}")

                if is_hourly:
                    order_period = order_datetime.strftime('%Y-%m-%d-%H')
                else:
                    order_period = order_datetime.date()

                is_current_period = order_period == current_period
                period_display = order_datetime.strftime('%Y-%m-%d %H:%M:%S') if is_hourly else str(order_period)

                # DEBUG: Log detailed comparison
                logger.debug(
                    "🔍 DEBUG Period comparison: order_period=%s, current_period=%s, is_current=%s",
                    order_period,
                    current_period,
                    is_current_period
                )
        
                # Log order details for debugging
                logger.info(
                    f"📋 Order: {side.upper()} | ${price:.4f} | Amount: {amount:.6f} | {client_id} | {period_display} | "
                    f"{'CURRENT_PERIOD' if is_current_period else 'OLD'}"
                )
        
                if side == 'buy':
                    buy_orders.append(order)
                elif side == 'sell':
                    if is_current_period:
                        sell_orders_current_period.append(order)
                        logger.debug(f"🔍 DEBUG Added to current-period sell orders: {client_id}")
                    else:
                        sell_orders_previous_period.append(order)
                        logger.debug(f"🔍 DEBUG Added to previous-period sell orders: {client_id}")
        
            # Log summary
            if len(sell_orders_previous_period) > 0:
                logger.info(f"  └─ {len(sell_orders_previous_period)} sell orders from previous periods (ignored)")
            if len(buy_orders) > 0:
                logger.info(f"  └─ {len(buy_orders)} buy orders total")
        
            # Check for orphaned sell orders by client_id marker - exclude from count
            regular_sell_count = 0
            orphaned_sell_count = 0
        
            for order in sell_orders_current_period:
                client_id = order.get('client_id', '')
                logger.debug(f"🔍 DEBUG Classifying sell order: client_id={client_id}, contains_OS={'_OS_' in client_id}")
                if "_OS_" in client_id:
                    orphaned_sell_count += 1
                    logger.debug(f"📌 Orphaned sell order detected: {client_id}")
                else:
                    regular_sell_count += 1
                    logger.debug(f"🔍 DEBUG Regular sell order detected: {client_id}")
        
            sell_count = len(sell_orders_current_period)
        
            if orphaned_sell_count > 0:
                logger.info(
                    f"📊 {descriptor.capitalize()} sell orders: {sell_count} total, {orphaned_sell_count} orphaned (ignored), "
                    f"{regular_sell_count} blocking"
                )
                logger.info("✅ Orphaned sell orders do not block new buy orders")
        
            # Handle edge case of multiple pending sells from today
            if regular_sell_count > 1:
                logger.warning(f"⚠️ Found {regular_sell_count} pending sell orders from {period_label} for {market}")
                logger.warning("This indicates a previous bug occurred - blocking new buy orders")
                log_trading_event(
                    'multiple_sells_detected',
                    f"Found {regular_sell_count} {period_label} pending sells - blocking new buys for {market}"
                )
            elif regular_sell_count == 1:
                logger.info(f"ℹ️ Found 1 pending sell order from {period_label} - blocking new buy orders")

            return {
                'count': regular_sell_count,
                'period_label': period_label,
                'descriptor': descriptor,
                'orders': sell_orders_current_period
            }
        
        except Exception as e:
            logger.error(f"Error checking current-period pending sell orders for {market}: {e}")
            # Return 1 to be safe - block new buy orders if we can't determine state
            return {
                'count': 1,
                'period_label': 'the current period',
                'descriptor': "current period's",
                'orders': []
            }

    def _get_current_period_uncovered_buy_fills(self, market: str) -> List[Dict[str, Any]]:
        """Return current-period buy fills that lack any filled sell coverage."""
        results: List[Dict[str, Any]] = []

        if not getattr(self, 'order_tracker', None):
            logger.debug("Order tracker unavailable - cannot evaluate uncovered buy fills.")
            return results

        try:
            from config.settings import TRADING_INTERVAL

            timeframe = (TRADING_INTERVAL or '').lower()
            is_hourly = timeframe == 'hourly'

            now_utc = datetime.now(timezone.utc)
            if is_hourly:
                current_period_key = now_utc.strftime('%Y-%m-%d-%H')
            else:
                current_period_key = now_utc.date()

            tolerance = self._get_market_amount_tolerance(market)

            for pair in list(self.order_tracker.order_pairs.values()):
                buy_order = pair.buy_order

                if buy_order.market != market:
                    continue

                if buy_order.filled_amount <= tolerance:
                    continue

                # Determine most recent fill timestamp
                fills = buy_order.fills or []
                if fills:
                    last_fill_time = max((fill.timestamp for fill in fills if fill.timestamp), default=buy_order.created_at)
                else:
                    last_fill_time = buy_order.created_at

                if last_fill_time is None:
                    continue

                if is_hourly:
                    fill_period_key = last_fill_time.strftime('%Y-%m-%d-%H')
                else:
                    fill_period_key = last_fill_time.date()

                if fill_period_key != current_period_key:
                    continue

                filled_sell_amount = sum(sell.filled_amount for sell in pair.sell_orders)
                remaining_after_filled = max(0.0, buy_order.filled_amount - filled_sell_amount)

                if remaining_after_filled <= tolerance:
                    continue

                sell_details = []
                for sell in pair.sell_orders:
                    sell_details.append({
                        'order_id': str(sell.order_id),
                        'client_id': sell.client_id,
                        'status': sell.status.value,
                        'filled_amount': sell.filled_amount,
                        'remaining_amount': sell.remaining_amount
                    })

                logger.debug(
                    "Uncovered buy detected | market=%s buy_id=%s remaining=%.6f filled_sells=%.6f",
                    market,
                    buy_order.order_id,
                    remaining_after_filled,
                    filled_sell_amount
                )

                results.append({
                    'buy_order_id': str(buy_order.order_id),
                    'buy_client_id': buy_order.client_id,
                    'filled_amount': buy_order.filled_amount,
                    'filled_sell_amount': filled_sell_amount,
                    'remaining_after_filled': remaining_after_filled,
                    'last_fill_timestamp': last_fill_time,
                    'sell_orders': sell_details
                })

            if results:
                logger.info(f"📊 Current-period uncovered buy fills for {market}: {len(results)}")
                for entry in results:
                    ts = entry['last_fill_timestamp'].strftime('%Y-%m-%d %H:%M:%S')
                    logger.info(
                        f"   Buy {entry['buy_order_id']} ({entry['buy_client_id']}) remaining {entry['remaining_after_filled']:.6f} "
                        f"after sells filled {entry['filled_sell_amount']:.6f} | last fill {ts}"
                    )

            return results

        except Exception as e:
            logger.error(f"Error gathering current-period uncovered buy fills for {market}: {e}")
            return results
    
    def _should_place_buy_order(self, market: str, signal: TradingSignal, current_price: float) -> bool:
        """Buy order decision using fresh exchange data with period-start cancellation and funding fee protection"""
        
        # Import and check trading timeframe
        from config.settings import TRADING_INTERVAL
        timeframe = TRADING_INTERVAL
        period_label = 'hour' if timeframe == 'hourly' else 'day'
        
        # Initialize period tracking if not exists
        if not hasattr(self, '_last_reset_period'):
            self._last_reset_period = {}
        
        # CRITICAL PHASE 0: Signal availability check
        if not signal:
            logger.error(f"❌ CRITICAL: Cannot place buy order for {market} - no trading signal available!")
            logger.error("This is the root cause of the infinite buy loop bug!")
            log_trading_event('signal_missing', f"❌ Buy order blocked - no signal for {market}")
            return False
        
        # Verify signal has required attributes
        if not hasattr(signal, 'buy_price') or not hasattr(signal, 'sell_price'):
            logger.error(f"❌ CRITICAL: Invalid signal for {market} - missing buy/sell prices!")
            logger.error("This could cause order placement failures or missing pairing rules!")
            log_trading_event('signal_invalid', f"❌ Buy order blocked - invalid signal for {market}")
            return False
        
        # Verify signal is for current period (not stale)
        if timeframe == 'hourly':
            current_period = datetime.now(timezone.utc).strftime('%Y-%m-%d-%H')
            signal_period = getattr(signal, 'hour_key', signal.date)
        else:
            current_period = datetime.now(timezone.utc).date().isoformat()
            signal_period = signal.date
            
        if signal_period != current_period:
            logger.warning(f"⚠️ Signal for {market} is stale (signal period: {signal_period}, current: {current_period})")
            logger.warning("Attempting to generate fresh signal...")
            
            # Try to generate a fresh signal
            if timeframe == 'hourly':
                fresh_signal = self.strategy.generate_hourly_signal(market, force=True)
                is_fresh = fresh_signal and getattr(fresh_signal, 'hour_key', fresh_signal.date) == current_period
            else:
                fresh_signal = self.strategy.generate_daily_signal(market, force=True)
                is_fresh = fresh_signal and fresh_signal.date == current_period
                
            if is_fresh:
                logger.info(f"✅ Generated fresh {timeframe} signal for {market}")
                # Note: We can't update the signal parameter, but the caller should get a fresh one next cycle
                return False  # Skip this cycle, let caller get fresh signal
            else:
                logger.error(f"❌ Could not generate fresh {timeframe} signal for {market}")
                log_trading_event('signal_stale', f"❌ Buy order blocked - stale {timeframe} signal for {market}")
                return False
        
        logger.debug(f"✅ Signal validation passed for {market}: Buy=${signal.buy_price:.2f}, Sell=${signal.sell_price:.2f}")
        
        # Using fresh exchange data directly - no validation needed
        logger.debug(f"🔄 Using fresh exchange data for buy decision for {market}")
        
        # EMERGENCY SAFETY CHECK moved after fresh data loading for consistency
        
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
        
        # Phase 1: Period reset check (daily or hourly based on timeframe)
        now = datetime.now(timezone.utc)
        current_hour = now.hour
        current_minute = now.minute
        
        if timeframe == 'hourly':
            # Hourly reset logic - check if hour changed
            current_period = now.strftime('%Y-%m-%d-%H')
            
            if not hasattr(self, '_last_reset_period'):
                self._last_reset_period = {}
            
            last_reset = self._last_reset_period.get(market)
            
            # Check if hour changed (regardless of funding windows)
            is_new_period = (last_reset != current_period)
            
            # Skip reset ONLY if currently IN funding window (not just detecting new hour)
            in_funding_window = (current_hour in [0, 8, 16] and 
                                current_minute == 0 and 
                                now.second <= 60)
            
            needs_reset = is_new_period and not in_funding_window
            reset_type = "hourly"
        else:
            # Daily reset logic - check if it's a new day
            today = now.date()
            current_period = today
            
            # Check if we've moved to a new day
            if not hasattr(self, '_last_reset_date'):
                self._last_reset_date = {}
            
            last_reset = self._last_reset_date.get(market)
            
            # New day if date changed (not time-window based)
            is_new_period = (last_reset != today)
            needs_reset = is_new_period
            reset_type = "daily"
        
        if needs_reset:
            logger.info(f"🌅 Performing one-time {reset_type} reset for {market} (last reset: {last_reset}, current: {current_period})")
            
            # Don't cancel orders during settlement periods
            if is_settlement_period() or is_approaching_settlement():
                logger.info(f"⏳ {reset_type.title()} reset delayed - waiting for settlement period to end for {market}")
                log_trading_event('settlement_delay', f"{reset_type.title()} reset delayed due to settlement period for {market}")
                # Don't mark as complete, try again next cycle
                return False
            
            # Get current buy orders for daily reset cleanup only (NOT SELL ORDERS)
            current_buy_status = self._get_exchange_buy_status(market)
            all_buy_orders = current_buy_status.get('all_buy_orders', [])
            
            cancelled_count = 0
            if len(all_buy_orders) > 0:
                for order in all_buy_orders:
                    # Cancel ALL buy orders during daily reset (cleanup any buggy duplicates)
                    try:
                        order_age = (now - order.created_at).total_seconds() / 3600
                        logger.info(f"🗑️ {reset_type.title()} reset - cancelling buy order: {order.client_id} from {order.created_at.date()} (age: {order_age:.1f}h)")
                        if self.order_manager.cancel_order(order.client_id):
                            cancelled_count += 1
                            log_trading_event(f'{reset_type}_reset_cleanup', f"Cancelled buy order {order.client_id} during {reset_type} reset")
                    except Exception as e:
                        logger.error(f"Failed to cancel buy order {order.client_id}: {e}")
            
            if cancelled_count > 0:
                logger.info(f"✅ {reset_type.title()} reset complete: Cancelled {cancelled_count} buy orders for {market}")
                # Small delay to let cancellations process
                import time
                time.sleep(0.5)
            else:
                logger.info(f"✅ {reset_type.title()} reset complete: No buy orders to cancel for {market}")
            
            # Mark reset as completed for current period
            if timeframe == 'hourly':
                self._last_reset_period[market] = current_period
                logger.info(f"✅ Hourly reset marked complete for {market} on {current_period}")
            else:
                if not hasattr(self, '_last_reset_date'):
                    self._last_reset_date = {}
                self._last_reset_date[market] = current_period
                logger.info(f"✅ Daily reset marked complete for {market} on {current_period}")
            
            # IMPORTANT: Continue to buy decision logic after reset
            # Don't return here - allow first buy of the day to proceed
        
        # REFINED BUY LOGIC: Get fresh data and apply our conditions
        logger.debug(f"🔄 Checking refined buy conditions for {market}")
        
        # Get fresh data using existing methods
        period_sell_status = self._get_period_pending_sell_orders(market)  # Already excludes orphaned
        pending_sells_today = period_sell_status['count']
        sell_period_label = period_sell_status['period_label']
        
        # Check period and buy conditions based on timeframe
        current_time = datetime.now(timezone.utc)
        
        if timeframe == 'hourly':
            # For hourly: Check if we're in a new hour (not in funding window)
            current_hour = current_time.hour
            current_minute = current_time.minute
            current_second = current_time.second
            
            # Not in funding window means we can trade
            in_funding_window = (current_hour in [0, 8, 16] and 
                               current_minute == 0 and 
                               current_second <= 60)
            is_new_period = not in_funding_window
            
            buy_status = self._check_period_buy_status(market)
            has_interval_buy_status = buy_status.get('has_current_interval_buy', False)
            period_name = "hour"
        else:
            # For daily: Always allow trading (not time-window restricted)
            is_new_period = True
            
            buy_status = self._check_period_buy_status(market)
            has_interval_buy_status = buy_status.get('has_current_interval_buy', False)
            period_name = "day"
        
        # Check cycle completion flag
        cycle_complete = self._cycle_completion_flags.get(market, False)
        
        # CONDITION 1: Either (new period + first buy) OR cycle complete
        can_proceed = (is_new_period and not has_interval_buy_status) or cycle_complete

        logger.info(
            f"🧭 Buy state for {market}: period={period_name}, is_new_period={is_new_period}, "
            f"has_buy_this_period={has_interval_buy_status}, cycle_complete={cycle_complete}, "
            f"pending_sells={pending_sells_today} ({sell_period_label})"
        )

        if not can_proceed:
            logger.info(f"❌ Cannot buy - not new {period_name} first buy and cycle not complete for {market}")
            return False

        # If pending sells from current period exist, cannot buy
        if pending_sells_today > 0 and not cycle_complete:
            logger.info(
                f"❌ Cannot buy - {pending_sells_today} pending sell order(s) from {sell_period_label} for {market}"
            )
            log_trading_event(
                'buy_blocked_pending_sells',
                f"{market}: {pending_sells_today} sells from {sell_period_label}"
            )
            return False
        
        # CONDITION 2: Position coverage check using existing method
        balance = self._calculate_position_sell_balance(market)
        position_size = balance.get('position_size', 0)
        uncovered = balance.get('uncovered_position', 0)
        
        # Get dynamic minimum order size for this market
        current_price = self.market_data.get_current_price(market)
        min_order_size = self.market_data.get_minimum_order_value(market, current_price)
        
        if position_size > 0 and uncovered > min_order_size:
            logger.info(f"⚠️ Position {position_size} not fully covered. Uncovered: {uncovered}")
            # Place sell order for uncovered amount
            self._place_missing_sell_order(market, uncovered)
            return False  # Don't buy this cycle
        
        # Clear cycle completion flag after using it
        if cycle_complete:
            self._cycle_completion_flags[market] = False
            logger.info(f"🔄 Cleared cycle completion flag for {market}")

        logger.debug(f"✅ All refined buy conditions met for {market}")
        
        # Continue with remaining safety checks from original code
        exchange_state = self._get_exchange_position_and_orders_direct(market)
        pending_buy_orders = exchange_state['current_interval_buy_orders']
        pending_sell_orders = exchange_state['current_interval_sell_orders'] + exchange_state['previous_interval_sell_orders']
        
        # Final safety check for pending buy orders
        if pending_buy_orders:
            if self._should_log_state_change(market, 'fresh_buy_orders_exist', True):
                logger.warning(f"❌ Cannot place buy - {len(pending_buy_orders)} pending buy orders exist for {market}")
                log_trading_event('fresh_buy_block', f"Buy blocked - {len(pending_buy_orders)} pending buy orders exist for {market}")
            return False
        
        # Phase 2.5: Position and cycle analysis with fresh data
        if position_size > 0:
            # Calculate total sell order amount from fresh data
            total_sell_amount = sum(safe_float(s.get('amount', 0)) for s in pending_sell_orders)
            uncovered_amount = position_size - total_sell_amount
            
            # Check for today's pending sells to determine if cycle is in progress
            from config.settings import TRADING_INTERVAL
            timeframe = (TRADING_INTERVAL or '').lower()
            now_utc = datetime.now(timezone.utc)

            if timeframe == 'hourly':
                current_period_key = now_utc.strftime('%Y-%m-%d-%H')
            else:
                current_period_key = now_utc.date()

            period_sells = []

            for sell in pending_sell_orders:
                created_str = sell.get('created_at', '')
                client_id = sell.get('client_id', '')

                try:
                    # Parse timestamp (handle different formats)
                    if isinstance(created_str, (int, float)):
                        created_at = datetime.fromtimestamp(created_str/1000, timezone.utc)
                    else:
                        created_at = datetime.fromisoformat(str(created_str).replace('Z', '+00:00'))
                
                    if timeframe == 'hourly':
                        sell_period_key = created_at.strftime('%Y-%m-%d-%H')
                    else:
                        sell_period_key = created_at.date()

                    # Check if from current period and not orphaned
                    if sell_period_key == current_period_key and '_OS_' not in client_id:
                        period_sells.append(sell)
                except Exception as e:
                    logger.debug(f"Could not parse sell order date: {e}")

            if period_sells:
                # Today's cycle in progress - wait for completion
                state_key = 'period_cycle_pending'
                if self._should_log_state_change(market, state_key, True):
                    logger.warning(f"❌ Cannot place buy - {period_label}'s cycle in progress for {market}")
                    logger.info(f"   Position size: {position_size:.6f}")
                    logger.info(f"   Pending sells this {period_label}: {len(period_sells)}")
                    log_trading_event('cycle_pending', 
                                    f"Buy blocked - current {period_label} cycle pending: {len(period_sells)} sells for {market}")
                return False

            # Position exists but only with old sells - can start new daily cycle
            logger.info(f"Position exists ({position_size:.6f}) with only old sells - allowing new {period_label} buy cycle for {market}")
            
            # Get min_amount for tolerance
            market_info = self.market_data.get_market_info(market)
            min_amount = safe_float(market_info.get('min_amount', 0.000001))

            # Check for uncovered amount and handle if needed
            if uncovered_amount > min_amount:
                logger.warning(f"⚠️ Uncovered position detected: {uncovered_amount:.6f} for {market}")
                # Don't block buy but log for monitoring
                log_trading_event('uncovered_detected', 
                                f"Uncovered position: {uncovered_amount:.6f} for {market}")
        
        # Phase 3: Check for pending buy orders (redundant sell check removed - handled by emergency check above)
        total_buy_orders = buy_status.get('total_buy_orders', 0)
        
        # CRITICAL SAFETY CHECK: Multiple buy orders indicate a serious bug
        if total_buy_orders > 1:
            logger.error(f"🚨 CRITICAL BUG: {total_buy_orders} buy orders detected for {market}!")
            logger.error("Daily Range Strategy should NEVER have multiple buy orders!")
            logger.error("This violates the core trading logic - investigating...")
            
            all_orders = buy_status.get('all_buy_orders', [])
            for i, order in enumerate(all_orders, 1):
                logger.error(f"  Buy #{i}: {order.client_id} @ ${order.price:.2f} placed {order.created_at}")
            
            log_trading_event('critical_bug', f"🚨 MULTIPLE BUY ORDERS BUG: {total_buy_orders} orders for {market}")
            
            # Attempt to fix by cancelling all but the most recent order
            if len(all_orders) > 1:
                # Sort by creation time, keep newest
                sorted_orders = sorted(all_orders, key=lambda x: x.created_at)
                orders_to_cancel = sorted_orders[:-1]  # All but the last (newest)
                
                logger.warning(f"🔧 Attempting to fix by cancelling {len(orders_to_cancel)} older buy orders")
                for order in orders_to_cancel:
                    try:
                        if self.order_manager.cancel_order(order.client_id):
                            logger.info(f"✅ Cancelled duplicate buy order: {order.client_id}")
                    except Exception as e:
                        logger.error(f"Failed to cancel duplicate order {order.client_id}: {e}")
            
            return False  # Block new orders until issue is resolved
        
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
        
        # Phase 4: Intelligent buy decision with fresh exchange data
        cycle_complete_flag = self._cycle_completion_flags.get(market, False)
        
        # Use FRESH exchange data for decision making (not cached)
        fresh_data = self._get_exchange_position_and_orders_direct(market)
        fresh_interval_buy_orders = fresh_data['current_interval_buy_orders']
        fresh_interval_sell_orders = fresh_data['current_interval_sell_orders']
        fresh_previous_sell_orders = fresh_data['previous_interval_sell_orders']
        is_consistent = fresh_data['is_consistent']
        period_label = 'hour' if timeframe == 'hourly' else 'day'

        # Block if an orphaned sell is actively covering the current-period buy
        orphan_tracking = self._tracked_orphaned_sells.get(market)
        if orphan_tracking:
            current_period_key = self._get_period_key(datetime.now(timezone.utc), timeframe)
            if orphan_tracking.get('period_key') == current_period_key:
                if self._should_log_state_change(market, 'orphan_sell_pending', True):
                    logger.info(f"⏳ Waiting for tracked orphaned sell to fill before new buy in {market}")
                log_trading_event('orphaned_sell_pending',
                                  f"Buy blocked - orphaned sell covering current {period_label} still pending for {market}")
                return False
            else:
                # Period rolled over - drop stale tracking entry
                self._tracked_orphaned_sells.pop(market, None)
        # CRITICAL: Block trading if position-order state is inconsistent
        if not is_consistent:
            if self._should_log_state_change(market, 'inconsistent_state', True):
                logger.error(f"🚨 BLOCKING BUY ORDER for {market} - Position-order state is inconsistent!")
                log_trading_event('consistency_block', f"Buy blocked - dangerous position-order state for {market}")
            return False
        
        # 🚨 CRITICAL EMERGENCY CHECK: Block if ANY pending sells from today exist (excluding orphaned)
        # This prevents duplicate buy orders during bot restarts when sells already exist from today
        period_sell_status = self._get_period_pending_sell_orders(market)
        pending_sells_today = period_sell_status['count']
        sell_period_label = period_sell_status['period_label']

        if pending_sells_today > 0:
            logger.error(
                f"🚨 EMERGENCY BLOCK: {pending_sells_today} pending sell orders from {sell_period_label} - "
                f"CANNOT PLACE BUY for {market}"
            )
            logger.error("   This prevents duplicate buy orders when the bot restarts mid-period with existing sells")
            log_trading_event(
                'emergency_block',
                f"🚨 Critical safety check blocked buy - {pending_sells_today} {sell_period_label} pending sells for {market}"
            )
            return False
        
        # CRITICAL FIX: Check if ANY buy orders were placed today (pending OR filled)
        # Previous bug: Only checked pending orders, which become empty after filling
        # This caused "startup" logic to trigger repeatedly after each filled order
        interval_label = buy_status.get('interval_label', f"current {period_label}")

        has_interval_buy_pending = len(fresh_interval_buy_orders) > 0
        has_interval_uncovered_fill = self._has_current_interval_uncovered_buy_fills(market)
        has_interval_buy = has_interval_buy_pending or has_interval_uncovered_fill

        decision_snapshot = (
            has_interval_buy_pending,
            has_interval_uncovered_fill,
            len(fresh_interval_buy_orders),
            len(fresh_interval_sell_orders),
            pending_sells_today,
            len(fresh_previous_sell_orders),
            cycle_complete_flag,
            is_consistent
        )

        if self._should_log_state_change(market, 'interval_decision_snapshot', decision_snapshot):
            logger.info(
                f"📊 {interval_label.capitalize()} snapshot for {market}: "
                f"pending_buys={len(fresh_interval_buy_orders)}, uncovered_fills={has_interval_uncovered_fill}, "
                f"current_sells={len(fresh_interval_sell_orders)} (pending_regular={pending_sells_today}), "
                f"previous_sells={len(fresh_previous_sell_orders)}, cycle_complete={cycle_complete_flag}, "
                f"consistency={'✅' if is_consistent else '🚨'}"
            )
            logger.info(
                f"🎯 Final buy decision for {market}: has_interval_buy={has_interval_buy} "
                f"(pending: {has_interval_buy_pending}, uncovered_fills: {has_interval_uncovered_fill})"
            )
        
        # Decision logic using fresh data
        if cycle_complete_flag:
            # Cycle just completed - generate fresh signal for new cycle
            logger.info(f"🔄 Cycle completion detected for {market} - generating fresh signal for new buy order")
            
            # Force fresh signal generation to ensure current OHLC data and avoid stale prices
            fresh_signal = self.strategy.generate_daily_signal(market, force=True)
            if fresh_signal:
                logger.info(f"✅ Fresh signal generated for new cycle: Buy=${fresh_signal.buy_price:.8f}")
                log_trading_event('fresh_signal', f"Fresh signal generated for {market}: Buy=${fresh_signal.buy_price:.8f}")
            else:
                logger.warning(f"⚠️ Failed to generate fresh signal for {market} - using cached signal")
            
            log_trading_event('cycle_buy', f"🔄 Placing new buy order after cycle completion for {market}")
            
            # Flag will be cleared after successful buy order placement
            
        elif not has_interval_buy:
            # STARTUP SCENARIO: No buy order placed today
            # Note: Emergency check above already prevents this scenario if pending sells exist
            # This logic is now primarily for logging clarity and double-verification
            
            # Redundant safety check (already handled by emergency check above, but kept for explicit clarity)
            if pending_sells_today > 0:
                # This should never be reached due to emergency check above, but keeping for safety
                logger.error("🚨 CRITICAL: Startup safety check triggered - this should have been caught by emergency check!")
                logger.error(f"❌ Cannot place first buy - {pending_sells_today} pending sell orders from today for {market}")
                log_trading_event('startup_safety_block', f"🚨 Startup safety check blocked buy - {pending_sells_today} today's sells for {market}")
                return False
            
            # Startup scenario: No pending sells from today - allow first buy of day
            logger.info(f"🌅 STARTUP: First buy order of the {period_label} allowed for {market} - no pending sells detected")
            log_trading_event('startup_buy', f"🌅 STARTUP: Placing first buy order of the {period_label} for {market}")
            
        else:
            # Normal operation: ONLY cycle completion allows new buys
            if self._should_log_state_change(market, 'normal_operation_cycle_wait', True):
                logger.debug(f"❌ Normal operation - waiting for cycle completion for {market}")
                log_trading_event('buy_decision', f"❌ Normal operation - waiting for cycle completion for {market}")
            return False
        
        # Phase 5: Price validation removed as per user feedback
        # User: "we do not need any MAX_RANGE_DEVIATION as buying with lower price 
        # than the originally generated signal is better and favorable"
        # Price adjustment will happen in place_buy_order() method
        
        # Phase 6: Account balance and position sizing
        account_balance = self.get_account_balance()
        position_size = self.position_sizer.calculate_position_size(
            market, signal.buy_price, account_balance
        )
        
        if not position_size.is_valid:
            if self._should_log_state_change(market, 'position_size_invalid', position_size.reason):
                log_trading_event('position_sizing', f"❌ Cannot place buy - position sizing invalid: {position_size.reason} for {market}")
            return False
        
        # All checks passed - positions are covered, no pending buy orders
        # Reset states when we're ready to buy
        if market in self._logged_states:
            self._logged_states[market] = {}  # Clear logged states for fresh start
        
        # FINAL VALIDATION LAYER: Multi-source cross-check to prevent duplicate orders
        # This happens AFTER all other checks pass and provides additional protection
        
        # Check 1: Recent Order Protection (30-second cooldown)
        has_recent, recent_client_id = self.recent_order_tracker.has_recent_order(market)
        if has_recent:
            logger.info(f"🛡️ Recent order protection: {recent_client_id} placed within 30s for {market}")
            log_trading_event('recent_order_block', f"Buy blocked - recent order {recent_client_id} for {market}")
            return False

        # Check 2: Phantom Order Verification 
        # If we removed a "phantom" order recently, check if position might have increased
        phantom_candidates = self.recent_order_tracker.get_phantom_candidates(market)
        if phantom_candidates:
            # Get current position
            current_position = self.position_manager.get_position(market)
            current_size = current_position.size if current_position else 0
            
            for phantom_time, phantom_client_id, phantom_amount in phantom_candidates:
                age = (datetime.now(timezone.utc) - phantom_time).total_seconds()
                if age < 60:  # Within 1 minute
                    logger.warning(f"⚠️ Phantom order {phantom_client_id} verification pending (age: {age:.1f}s)")
                    logger.warning(f"   Current position: {current_size:.6f}, Phantom amount: {phantom_amount:.6f}")
                    # Block buy for safety until phantom is verified
                    log_trading_event('phantom_verification_block', 
                                    f"Buy blocked - phantom order {phantom_client_id} verification pending for {market}")
                    return False

        # Check 3: Enhanced Position Coverage (FINAL RE-CHECK with fresh data)
        # Re-check position coverage to catch any race conditions
        balance = self._calculate_position_sell_balance(market)
        position_size_final = balance.get('position_size', 0)
        uncovered_final = balance.get('missing_sell', 0)  # Use 'missing_sell' key from the method

        # Get dynamic minimum order size for this market
        current_price = self.market_data.get_current_price(market)  
        min_order_size_final = self.market_data.get_minimum_order_value(market, current_price)
        
        if uncovered_final > min_order_size_final:
            logger.warning(f"⚠️ FINAL CHECK: Uncovered position detected: {uncovered_final:.6f} for {market}")
            logger.warning(f"   Position size: {position_size_final:.6f}")
            # Try to place sell order for uncovered amount
            self._place_missing_sell_order(market, uncovered_final)
            log_trading_event('final_position_check_block', 
                            f"Buy blocked - uncovered position {uncovered_final:.6f} for {market}")
            return False

        # Check 4: Order Visibility Grace Period
        # Even if order manager shows an order, wait for exchange visibility
        manager_orders = self.order_manager.get_pending_orders(market)
        for order in manager_orders:
            if order.side.value == 'buy':  # Check OrderSide enum value
                order_age = (datetime.now(timezone.utc) - order.created_at).total_seconds()
                if order_age < 30:
                    logger.info(f"⏳ Buy order {order.client_id} waiting for exchange visibility ({order_age:.1f}s)")
                    log_trading_event('order_visibility_wait', 
                                    f"Buy blocked - order {order.client_id} waiting for visibility for {market}")
                    return False

        # All enhanced checks passed
        logger.info(f"🚀 All buy checks passed for {market} - proceeding to order placement")
        log_trading_event('buy_decision', f"✅ All checks passed including enhanced validation - ready to place buy order for {market}")
        log_trading_event('order_details', f"💰 Order details: ${signal.buy_price:.4f} x {position_size.quantity:.6f} = ${position_size.size_usdt:.4f} for {market}")
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
            
            from config.settings import TRADING_INTERVAL
            timeframe = (TRADING_INTERVAL or '').lower()
            now_utc = datetime.now(timezone.utc)

            buy_orders.sort(key=lambda x: x.created_at)

            if timeframe == 'hourly':
                period_key = now_utc.strftime('%Y-%m-%d-%H')
                interval_label = 'current hour'
            else:
                period_key = now_utc.date()
                interval_label = 'current day'

            current_period_orders = []
            previous_period_orders = []

            for order in buy_orders:
                created_at = order.created_at
                if timeframe == 'hourly':
                    order_period = created_at.strftime('%Y-%m-%d-%H')
                else:
                    order_period = created_at.date()

                if order_period == period_key:
                    current_period_orders.append(order)
                elif created_at < now_utc:
                    previous_period_orders.append(order)

            # Cancel ALL old orders
            for order in previous_period_orders:
                try:
                    logger.warning(f"🗑️  Cancelling buy order from previous period: {order.client_id} ({order.created_at})")
                    if self.order_manager.cancel_order(order.client_id):
                        log_trading_event('cleanup', f"Cancelled previous-period buy order {order.client_id}")
                except Exception as e:
                    if "invalid argument" in str(e).lower():
                        logger.info(f"⚠️ Order {order.client_id} may already be filled/cancelled: {e}")
                        log_trading_event('order_gone', f"Order {order.client_id} not found - likely filled/cancelled")
                    else:
                        logger.error(f"Failed to cancel old order {order.client_id}: {e}")

            # Handle today's orders - be more aggressive about old ones
            if len(current_period_orders) > 1:
                logger.warning(f"⚠️  Multiple buy orders from {interval_label} detected! Keeping newest, cancelling {len(current_period_orders)-1} older ones")
                current_period_orders.sort(key=lambda x: x.created_at)
                for order in current_period_orders[:-1]:  # Cancel all except the newest
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
            elif len(current_period_orders) == 1:
                # Sentence changed
                order = current_period_orders[0]
                age_hours = (datetime.now(timezone.utc) - order.created_at).total_seconds() / 3600
                logger.info(f"✅ Keeping single buy order from {interval_label}: {order.client_id} ({age_hours:.1f}h old)")
                logger.info("📋 Conservative approach: Respecting existing current-period order, letting order tracking handle fills")
                        
            # REMOVED: Cache clearing - using direct exchange queries
                
            # Wait a moment for exchange to process cancellations
            await asyncio.sleep(0.5)
            
            # Verify cleanup results with fresh sync
            logger.info(f"🔍 Verifying cleanup results for {market}")
            self.order_manager.load_existing_orders(market)
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
            # Let API response drive decisions, not artificial time delays
            action_type = f"{side}_order"
            logger.debug(f"Placing {action_type} for {market} - using API response for immediate actions")
            
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
            exit_price = signal.sell_price #if side == 'buy' else signal.buy_price
            
            is_profitable = validator.is_signal_profitable(price, exit_price, position_size.size_usdt)
            if not is_profitable.is_profitable:
                logger.warning(f"Order not profitable: {is_profitable.reason}")

                if side != 'buy':
                    return

                optimized_buy, optimized_sell, was_optimized = validator.optimize_prices_for_profit(
                    signal.buy_price,
                    signal.sell_price,
                    signal.range_value,
                    position_size.size_usdt,
                    None,
                    None
                )

                if not was_optimized:
                    logger.warning("❌ Profitability adjustments unavailable; skipping buy placement")
                    return

                market_info = self.market_data.get_market_info(market)
                tick_size = safe_float(market_info.get('tick_size', 0.0001))

                from decimal import Decimal, ROUND_DOWN, ROUND_UP
                tick_decimal = Decimal(str(tick_size))

                buy_decimal = Decimal(str(optimized_buy)) / tick_decimal
                sell_decimal = Decimal(str(optimized_sell)) / tick_decimal

                adjusted_buy = float((buy_decimal.quantize(Decimal('1'), rounding=ROUND_DOWN)) * tick_decimal)
                adjusted_sell = float((sell_decimal.quantize(Decimal('1'), rounding=ROUND_UP)) * tick_decimal)

                if adjusted_buy <= 0 or adjusted_sell <= adjusted_buy:
                    logger.warning("❌ Rounded prices invalid after expansion - skipping")
                    return

                signal.buy_price = adjusted_buy
                signal.sell_price = adjusted_sell
                price = adjusted_buy
                exit_price = adjusted_sell

                position_size = self.position_sizer.calculate_position_size(
                    market, price, account_balance
                )

                if not position_size.is_valid:
                    logger.warning(f"Cannot place {side} order for {market} after expansion: {position_size.reason}")
                    return

                is_profitable = validator.is_signal_profitable(price, exit_price, position_size.size_usdt)
                if not is_profitable.is_profitable:
                    logger.warning(
                        f"❌ Range expansion still below profitability: {safe_str_format(safe_float(is_profitable.profit_percent), '.4f')}% - skipping"
                    )
                    return

                logger.info(
                    f"📈 Range expanded symmetrically for profitability: Buy {safe_str_format(price, '.8f')} | "
                    f"Sell {safe_str_format(exit_price, '.8f')} (target ≥ {validator.min_profit_percent:.2f}%)"
                )
                log_trading_event(
                    'range_expansion',
                    f"Symmetric expansion for {market}: Buy={safe_str_format(price, '.8f')}, "
                    f"Sell={safe_str_format(exit_price, '.8f')}, Profit={safe_str_format(safe_float(is_profitable.profit_percent), '.4f')}%"
                )
            
            # Apply price adjustments for better entries/exits
            adjusted_price = price
            if side == 'buy':
                # Buy price adjustment: Use cheaper price for better entry
                current_price = self.market_data.get_current_price(market)
                if current_price and current_price < price:
                    adjusted_price = current_price
                    log_trading_event('price_adjustment', f"💰 PRICE ADJUST | Signal=${price:.8f} → Market=${adjusted_price:.8f} (cheaper entry) | {market}")
                else:
                    market_price_str = f"${current_price:.8f}" if current_price else "N/A"
                    log_trading_event('price_no_adjustment', f"📊 PRICE OPTIMAL | Signal=${price:.8f} | Market={market_price_str} | Using signal price | {market}")
            
            # Place the order
            if side == 'buy':
                order = self.order_manager.place_buy_order(
                    market=market,
                    amount=position_size.quantity,
                    price=adjusted_price,
                    position_size=position_size.size_usdt,
                    is_hide=True  # Hidden orders for production
                )
                
                # Order tracking is now handled by the unified system in OrderManager
                if order:
                    log_trading_event('order_placed', f"Buy order placed {order.exchange_order_id} - unified tracking active in {market}")
                    
                    # Clear cycle completion flag only after successful buy order placement
                    if market in self._cycle_completion_flags and self._cycle_completion_flags[market]:
                        self._cycle_completion_flags[market] = False
                        logger.info(f"✅ Cycle completion flag cleared after successful buy order placement for {market}")
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
            
            side = 'sell' if position.side == PositionSide.LONG else 'buy'
            
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
            
            # Check for stale sell orders (every hour)
            now = datetime.now(timezone.utc)
            if not hasattr(self, '_last_stale_check'):
                self._last_stale_check = now
            elif (now - self._last_stale_check).seconds > 3600:  # 1 hour
                stale_count = await self.pairing_manager.check_and_handle_stale_sell_orders()
                if stale_count > 0:
                    logger.warning(f"Found {stale_count} stale sell orders")
                self._last_stale_check = now
            
            # Periodic emergency balance check (every 10 minutes)
            if not hasattr(self, '_last_balance_check'):
                self._last_balance_check = now
            elif (now - self._last_balance_check).seconds > 600:  # 10 minutes
                imbalanced_markets = await self.pairing_manager.emergency_balance_check()
                if imbalanced_markets:
                    logger.error(f"CRITICAL: Position imbalances detected in markets: {imbalanced_markets}")
                self._last_balance_check = now
            
            # Fallback detection for missed fills (every 5 minutes)
            if not hasattr(self, '_last_missed_fill_check'):
                self._last_missed_fill_check = now
            elif (now - self._last_missed_fill_check).seconds > 300:  # 5 minutes
                await self._check_for_missed_fills()
                self._last_missed_fill_check = now
                
        except Exception as e:
            logger.error(f"Error in pairing tasks: {e}")
    
    async def _check_for_missed_fills(self):
        """Check for buy orders that filled but have no corresponding sell orders"""
        try:
            logger.debug("🔍 Running fallback detection for missed fills...")
            
            for market in self.trading_markets:
                # Get current position and sell orders
                balance = self._calculate_position_sell_balance(market)
                
                if not balance['position_exists']:
                    continue
                    
                position_size = balance['position_size']
                total_sells = balance['total_sells']
                missing_sell = balance['missing_sell']
                
                # Check if we have a significant amount missing sell coverage
                if missing_sell > 0.001:  # More than 0.001 units uncovered
                    logger.warning(f"🚨 MISSED FILL DETECTED: {market} has {missing_sell:.6f} uncovered position")
                    logger.warning(f"   Position: {position_size:.6f}, Sell orders: {total_sells:.6f}")
                    
                    # Get current signal to determine if we should create sell orders
                    signal = self.strategy.get_current_signal(market)
                    if signal and hasattr(signal, 'sell_price'):
                        logger.info(f"🔧 Creating recovery sell order for missed fill using strategy price: ${signal.sell_price:.2f}")
                        
                        # Create sell order for the missing amount
                        try:
                            recovery_order = self.order_manager.place_tracked_sell(
                                market=market,
                                amount=missing_sell,
                                price=signal.sell_price,
                                position_size=missing_sell * signal.sell_price,
                                source='recovery'
                            )

                            if recovery_order:
                                logger.info(
                                    f"✅ Recovery sell order created: {missing_sell:.6f} {market} @ ${signal.sell_price:.2f}"
                                )
                            else:
                                logger.error(f"❌ Failed to create recovery sell order for {market}")
                                
                        except Exception as e:
                            logger.error(f"❌ Exception creating recovery sell order for {market}: {e}")
                    else:
                        logger.warning(f"⚠️ No valid signal available for {market} recovery sell order")
                        
        except Exception as e:
            logger.error(f"Error in missed fill detection: {e}")
    
    def _handle_balance_update(self, data: Dict[str, Any]):
        """Legacy no-op retained for backwards compatibility (websocket removed)."""
        return
    
    def _handle_position_update(self, data: Dict[str, Any]):
        """Legacy no-op retained for backwards compatibility (websocket removed)."""
        return
    
    def _handle_order_update(self, data: Dict[str, Any]):
        """Legacy no-op retained for backwards compatibility (websocket removed)."""
        return
    
    def _handle_user_deals_update(self, data: Dict[str, Any]):
        """Legacy no-op retained for backwards compatibility (websocket removed)."""
        return
    
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
            
            # Using fresh exchange data - no validation needed
            
            # Trigger fresh buy status check for critical decisions
            fresh_status = self._get_exchange_buy_status(market)
            logger.debug(f"🔍 Post-reconciliation buy status for {market}: {fresh_status.get('total_buy_orders', 0)} orders")
            
        except Exception as e:
            logger.error(f"Error reconciling order state for {market}: {e}")
    
    async def _reconcile_after_fill(self, market: str, side: str, amount: float, price: float):
        """Reconcile state after order fill"""
        try:
            logger.debug(f"🔄 Reconciling state after {side} fill: {amount} @ ${price} for {market}")
            
            # Using fresh exchange data - sync position only
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
            self.position_manager.sync_with_exchange()
            await self._update_account_status()
            
            # Verify trading cycle is actually complete
            position = self.position_manager.get_position(market)
            buy_status = self._get_exchange_buy_status(market)
            
            if not position or position.size < 0.000001:
                logger.info(f"✅ Trading cycle confirmed complete for {market}")
                logger.info(f"📊 Final state: Position={position.size if position else 0:.6f}, "
                           f"Buy orders={buy_status.get('total_buy_orders', 0)}")
                
                # DO NOT set cycle completion flag here - only when sell orders actually fill
                # The cycle is only complete when the sell order fills and position closes
                logger.info(f"🔄 Buy order processed for {market} - waiting for sell order to complete cycle")
                
                # Log buy processing event for monitoring  
                log_trading_event('buy_processed', f"✅ Buy order processed for {market} - sell order pending")
            else:
                logger.warning(f"⚠️ Trading cycle not fully complete for {market}: position={position.size:.6f}")
            
        except Exception as e:
            logger.error(f"Error reconciling trading cycle completion for {market}: {e}")

    def _handle_order_completion_for_orphan_tracking(self, tracked_order) -> None:
        """Handle tracked orphaned sell completion events"""
        try:
            if tracked_order.side != OrderSide.SELL:
                return

            market = tracked_order.market
            tracking = self._tracked_orphaned_sells.get(market)
            if not tracking:
                return

            sell_order_match = False
            if tracking.get('sell_order_id') and str(tracked_order.order_id) == str(tracking['sell_order_id']):
                sell_order_match = True
            elif tracking.get('client_id') and tracked_order.client_id == tracking['client_id']:
                sell_order_match = True

            if not sell_order_match:
                return

            from config.settings import TRADING_INTERVAL
            timeframe = TRADING_INTERVAL
            current_period_key = self._get_period_key(datetime.now(timezone.utc), timeframe)

            # Remove tracking regardless of period match to avoid stale entries
            self._tracked_orphaned_sells.pop(market, None)

            if tracking.get('period_key') == current_period_key:
                self._cycle_completion_flags[market] = True
                logger.info(f"🔄 Orphaned sell {tracked_order.client_id} filled for {market} - cycle completion flagged")
                log_trading_event('orphaned_cycle_complete',
                                  f"Cycle completion detected after orphaned sell fill for {market}")
            else:
                logger.info(f"ℹ️ Orphaned sell {tracked_order.client_id} filled for {market} from previous period")

        except Exception as e:
            logger.debug(f"Error handling orphaned sell completion for {tracked_order.client_id}: {e}")
    
    def _get_exchange_buy_status(self, market: str) -> Dict[str, Any]:
        """Get buy status directly from exchange - single source of truth"""
        try:
            # Get orders directly from exchange via OrderManager
            pending_orders = self.order_manager.get_pending_orders(market)
            buy_orders = [o for o in pending_orders if o.side.value == 'buy']
            
            from config.settings import TRADING_INTERVAL
            timeframe = (TRADING_INTERVAL or '').lower()
            now_utc = datetime.now(timezone.utc)

            if timeframe == 'hourly':
                current_period = now_utc.strftime('%Y-%m-%d-%H')
            else:
                current_period = now_utc.date()

            period_buy_orders = []

            for order in buy_orders:
                created_at = getattr(order, 'created_at', None)
                if not created_at:
                    continue

                if timeframe == 'hourly':
                    order_period = created_at.strftime('%Y-%m-%d-%H')
                else:
                    order_period = created_at.date()

                if order_period == current_period:
                    period_buy_orders.append(order)

            # Return consistent format for buy status
            interval_label = 'current hour' if timeframe == 'hourly' else 'current day'

            return {
                'total_buy_orders': len(buy_orders),
                'all_buy_orders': buy_orders,
                'current_interval_orders': period_buy_orders,
                'interval_label': interval_label,
                'cancelled_stale': 0  # Always 0 with direct exchange queries
            }
            
        except Exception as e:
            logger.error(f"Error getting exchange buy status for {market}: {e}")
            # Return safe default
            return {
                'total_buy_orders': 0,
                'all_buy_orders': [],
                'current_interval_orders': [],
                'interval_label': 'current period',
                'cancelled_stale': 0
            }
    
    def _classify_orders_by_date(self, market: str, orders: List[Dict], now_utc: datetime) -> tuple:
        """
        Classify orders into current period vs old orders based on the TRADING_INTERVAL.
        """
        from config.settings import TRADING_INTERVAL
        from datetime import timezone

        current_period_orders = []
        old_orders = []

        if TRADING_INTERVAL == 'hourly':
            current_period_key = now_utc.strftime('%Y-%m-%d-%H')
            logger.debug(f"Classifying orders against current hour: {current_period_key}")
        else:  # daily
            current_period_key = now_utc.date()
            logger.debug(f"Classifying orders against current date: {current_period_key}")

        for order in orders:
            try:
                created_at_ts = safe_int(order.get('created_at', 0))
                if created_at_ts:
                    order_datetime = datetime.fromtimestamp(created_at_ts / 1000, timezone.utc)
                    
                    if TRADING_INTERVAL == 'hourly':
                        order_period_key = order_datetime.strftime('%Y-%m-%d-%H')
                    else:  # daily
                        order_period_key = order_datetime.date()

                    if order_period_key == current_period_key:
                        current_period_orders.append(order)
                    else:
                        old_orders.append(order)
                else:
                    old_orders.append(order)
            except Exception as e:
                logger.warning(f"Failed to parse order timestamp for {order.get('client_id', 'N/A')}: {e}")
                old_orders.append(order)
        
        classification_state = (len(current_period_orders), len(old_orders))
        if self._should_log_state_change(market, 'period_order_classification', classification_state):
            logger.info(
                f"📊 Period classification ({TRADING_INTERVAL}): {classification_state[0]} current, "
                f"{classification_state[1]} old orders"
            )
        return current_period_orders, old_orders

    def _validate_position_order_consistency(self, market: str, position_size: float, 
                                           all_sell_orders: List[Dict]) -> bool:
        """
        Validate position-order consistency to detect dangerous states
        
        Args:
            market: Market symbol
            position_size: Current position size from exchange
            all_sell_orders: All sell orders (today + old)
            
        Returns:
            True if state is consistent and safe, False if dangerous
        """
        if not all_sell_orders:
            return True  # No sell orders - always safe
        
        total_sell_amount = sum(safe_float(o.get('amount', 0)) for o in all_sell_orders)
        
        # CRITICAL: Position = 0 but sell orders exist
        if position_size == 0 and len(all_sell_orders) > 0:
            logger.error(f"🚨 DANGEROUS STATE for {market}: No position but {len(all_sell_orders)} sell orders exist!")
            logger.error(f"   Sell orders total: {safe_str_format(total_sell_amount, '.6f')}")
            
            # Log details of each sell order for investigation
            for i, order in enumerate(all_sell_orders, 1):
                client_id = order.get('client_id', 'N/A')
                amount = safe_float(order.get('amount', 0))
                price = safe_float(order.get('price', 0))
                logger.error(f"   Sell #{i}: {client_id} - {safe_str_format(amount, '.6f')} @ ${safe_str_format(price, '.4f')}")
            
            logger.error("   This indicates either:")
            logger.error("   1. Stale position query (position actually exists)")
            logger.error("   2. Orphaned sell orders (position was closed)")
            logger.error("   3. Data synchronization issue")
            
            # Attempt to clean up orphaned orders
            logger.info("🔧 Attempting to clean up potentially orphaned sell orders...")
            cancelled_count = self._handle_orphaned_sell_orders(market, all_sell_orders)
            
            if cancelled_count > 0:
                logger.info(f"✅ Cleaned up {cancelled_count} orphaned orders - state may be recovered")
                # Return False still - let next cycle verify if state is actually fixed
            else:
                logger.error("❌ Could not clean up orders - may indicate real position exists with stale query")
            
            logger.error("   ⚠️ BLOCKING TRADING until state is verified in next cycle!")
            return False
        
        # Check for potential over-selling (sells > position + 10% tolerance)
        if position_size > 0 and total_sell_amount > position_size * 1.1:
            logger.error(f"🚨 OVERSELLING DETECTED for {market}:")
            logger.error(f"   Position size: {position_size:.6f}")
            logger.error(f"   Total sell orders: {total_sell_amount:.6f}")
            logger.error(f"   Excess sells: {total_sell_amount - position_size:.6f}")
            logger.error("   ⚠️ BLOCKING TRADING to prevent short position!")
            return False
        
        # Log normal state for transparency
        if position_size > 0:
            coverage_pct = (total_sell_amount / position_size) * 100 if position_size > 0 else 0
            logger.debug(f"✅ Position-order consistency OK for {market}: "
                        f"Position {position_size:.6f}, Sells {total_sell_amount:.6f} ({coverage_pct:.1f}%)")
        
        return True

    def _handle_orphaned_sell_orders(self, market: str, sell_orders: List[Dict]) -> int:
        """
        Handle orphaned sell orders when no position exists
        
        Args:
            market: Market symbol
            sell_orders: List of sell orders to potentially clean up
            
        Returns:
            Number of orders cancelled
        """
        if not sell_orders:
            return 0
            
        logger.warning(f"🔧 Detected {len(sell_orders)} potentially orphaned sell orders for {market}")
        cancelled_count = 0
        
        for order in sell_orders:
            try:
                client_id = order.get('client_id', '')
                amount = order.get('amount', 0)
                price = order.get('price', 0)
                
                if not client_id:
                    logger.warning(f"⚠️ Skipping order without client_id: amount={amount}")
                    continue
                    
                logger.info(f"🗑️ Attempting to cancel orphaned sell order: {client_id} - {amount:.6f} @ ${price:.4f}")
                
                if self.order_manager.cancel_order(client_id):
                    logger.info(f"✅ Cancelled orphaned sell order: {client_id}")
                    cancelled_count += 1
                    log_trading_event('orphaned_cleanup', f"Cancelled orphaned sell order {client_id} for {market}")
                else:
                    logger.warning(f"⚠️ Failed to cancel orphaned sell order: {client_id}")
                    
            except Exception as e:
                logger.error(f"Error cancelling orphaned order {order.get('client_id', 'N/A')}: {e}")
        
        if cancelled_count > 0:
            logger.info(f"✅ Orphaned sell cleanup complete: Cancelled {cancelled_count}/{len(sell_orders)} orders for {market}")
        else:
            logger.warning(f"⚠️ Orphaned sell cleanup failed: Could not cancel any of {len(sell_orders)} orders for {market}")
            
        return cancelled_count

    def _get_exchange_position_and_orders_direct(self, market: str) -> Dict[str, Any]:
        """
        Query exchange directly for position and order state using REST-only data.
        """
        try:
            # Direct query to exchange for positions
            positions_response = self.client.get_positions(market)
            position_size = 0.0
            
            if positions_response and positions_response.get('data'):
                positions_data = positions_response['data']
                if isinstance(positions_data, list):
                    for pos in positions_data:
                        if pos.get('market') == market:
                            position_size = safe_float(pos.get('open_interest', 0))
                            break
                elif isinstance(positions_data, dict):
                    if market in positions_data:
                        position_size = safe_float(positions_data[market].get('open_interest', 0))
            
            # Direct query to exchange for orders
            orders_response = self.client.get_pending_orders(market)
            buy_orders = []
            sell_orders = []
            
            if orders_response and orders_response.get('data'):
                for order in orders_response['data']:
                    if order.get('market') == market:
                        if order.get('side') == 'buy':
                            buy_orders.append(order)
                        elif order.get('side') == 'sell':
                            sell_orders.append(order)
            
            # Use the new interval-aware classifier
            from datetime import datetime, timezone
            now_utc = datetime.now(timezone.utc)
            
            current_period_buy_orders, old_buy_orders = self._classify_orders_by_date(market, buy_orders, now_utc)
            current_period_sell_orders, old_sell_orders = self._classify_orders_by_date(market, sell_orders, now_utc)
            
            # CRITICAL: Validate position-order consistency
            all_sell_orders = current_period_sell_orders + old_sell_orders
            is_consistent = self._validate_position_order_consistency(market, position_size, all_sell_orders)
            
            snapshot = (
                round(position_size, 6),
                len(current_period_buy_orders),
                len(current_period_sell_orders),
                len(old_buy_orders),
                len(old_sell_orders),
                is_consistent
            )

            if self._should_log_state_change(market, 'direct_exchange_snapshot', snapshot):
                logger.info(
                    f"📡 Direct exchange snapshot for {market}: position={position_size:.6f}, "
                    f"current_buys={len(current_period_buy_orders)}, current_sells={len(current_period_sell_orders)}, "
                    f"old_buys={len(old_buy_orders)}, old_sells={len(old_sell_orders)}, "
                    f"consistency={'✅' if is_consistent else '🚨'}"
                )
            
            # Return dictionary with old keys for compatibility with calling functions
            return {
                'position_size': position_size,
                'current_interval_buy_orders': current_period_buy_orders,
                'current_interval_sell_orders': current_period_sell_orders,
                'previous_interval_sell_orders': old_sell_orders,
                'is_consistent': is_consistent,
                'total_buy_amount': sum(safe_float(o.get('amount', 0)) for o in current_period_buy_orders),
                'total_sell_amount': sum(safe_float(o.get('amount', 0)) for o in all_sell_orders)
            }
            
        except Exception as e:
            logger.error(f"Error querying exchange directly for {market}: {e}")
            # Return safe defaults on error
            return {
                'position_size': 0.0,
                'current_interval_buy_orders': [],
                'current_interval_sell_orders': [],
                'previous_interval_sell_orders': [],
                'is_consistent': True,
                'total_buy_amount': 0.0,
                'total_sell_amount': 0.0
            }
    
    async def _get_todays_uncovered_buy_fill(self, market: str, uncovered_amount: float) -> Optional[Dict]:
        """
        Find today's buy fill that matches the uncovered amount using order tracker and REST data
        Returns fill details or None
        """
        try:
            today = datetime.now(timezone.utc).date()
            logger.debug(f"Looking for buy fill matching {safe_str_format(uncovered_amount, '.6f')} {market} from today ({today})")
            
            # Method 1: Check order tracker for recent buy fills from today
            if hasattr(self, 'order_tracker') and self.order_tracker:
                try:
                    # Get all tracked orders and filter by market
                    tracked_orders = self.order_tracker.tracked_orders.values()
                    market_orders = [order for order in tracked_orders if order.market == market]
                    
                    for order in market_orders:
                        # Only consider buy orders from today that have fills
                        if (order.side == OrderSide.BUY and 
                            order.created_at.date() == today and 
                            hasattr(order, 'fills') and order.fills):
                            
                            # Check if any fill matches the uncovered amount
                            for fill in order.fills:
                                fill_amount = safe_float(fill.amount)
                                if abs(fill_amount - uncovered_amount) < 0.000001:  # Precise match
                                    logger.info(f"✓ Found matching buy fill: {safe_str_format(fill_amount, '.6f')} @ ${safe_str_format(fill.price, '.4f')}")
                                    return {
                                        'order_id': order.order_id,
                                        'client_id': order.client_id,
                                        'amount': fill_amount,
                                        'price': safe_float(fill.price),
                                        'timestamp': fill.timestamp,
                                        'market': market
                                    }
                    
                    logger.debug(f"No matching fills found in order tracker for amount {uncovered_amount:.6f}")
                    
                except Exception as e:
                    logger.debug(f"Order tracker fill lookup failed: {e}")
            
            # Method 2: Use new user_deals API to get actual fills
            if hasattr(self.client, 'get_user_deals'):
                try:
                    # Get today's buy fills from the API
                    start_time = int(datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
                    logger.debug(
                        "[coverage_check] Fetching user deals | market=%s side=buy start=%s",
                        market,
                        start_time
                    )
                    deals_response = self.client.get_user_deals(
                        market=market,
                        side='buy',
                        start_time=start_time,
                        limit=100
                    )
                    
                    if deals_response and deals_response.get('data'):
                        for deal in deals_response['data']:
                            if deal.get('market') != market:
                                continue
                            deal_amount = safe_float(deal.get('amount', 0))
                            if abs(deal_amount - uncovered_amount) < 0.000001:
                                logger.info(f"✓ Found matching buy fill from user_deals: {safe_str_format(deal_amount, '.6f')} @ ${safe_str_format(safe_float(deal.get('price', 0)), '.4f')}")
                                return {
                                    'deal_id': deal.get('deal_id'),
                                    'order_id': deal.get('order_id'),
                                    'amount': deal_amount,
                                    'price': safe_float(deal.get('price', 0)),
                                    'created_at': safe_int(deal.get('created_at', 0)),
                                    'market': market
                                }
                    
                    logger.debug("No matching fills found in user_deals")
                    
                except Exception as e:
                    logger.debug(f"User deals API lookup failed: {e}")
            
            # Method 3: Fallback to pending orders check
            try:
                # Get recent order history through order status queries
                # This is less efficient but works as a fallback
                
                pending_orders_response = self.client.get_pending_orders(market)
                if pending_orders_response and pending_orders_response.get('data'):
                    orders_data = pending_orders_response['data']
                    
                    # Look for partially filled orders that might contain the missing fill
                    for order_data in orders_data:
                        if (order_data.get('side') == 'buy' and 
                            safe_float(order_data.get('filled_amount', 0)) > 0):
                            
                            filled_amount = safe_float(order_data.get('filled_amount', 0))
                            if abs(filled_amount - uncovered_amount) < 0.000001:
                                logger.info(f"✓ Found matching filled buy order via API: {safe_str_format(filled_amount, '.6f')}")
                                return {
                                    'order_id': order_data.get('order_id'),
                                    'client_id': order_data.get('client_id'),
                                    'amount': filled_amount,
                                    'price': safe_float(order_data.get('price', 0)),
                                    'market': market
                                }
                
                logger.debug("No matching fills found via order status queries")
                
            except Exception as e:
                logger.debug(f"Order status fill lookup failed: {e}")
            
            # Method 3: Return None if no match found - let periodic REST reconciliation handle future fills
            logger.debug(f"No buy fill found matching {safe_str_format(uncovered_amount, '.6f')} - future REST reconciliation will handle fills")
            return None
                
        except Exception as e:
            logger.error(f"Error finding today's buy fill: {e}")
            return None
    
    
    async def _perform_startup_order_cleanup(self):
        """One-time comprehensive order cleanup and orphaned position check during bot startup"""
        try:
            # Skip if already completed (ensure this only runs once)
            if self._startup_cleanup_completed:
                logger.debug("Startup cleanup already completed - skipping")
                return

            # Force a position sync right before cleanup to prevent race conditions
            logger.info("Force syncing positions before startup cleanup...")
            self.position_manager.sync_with_exchange()
            await asyncio.sleep(1)  # Brief pause to allow data to settle
            
            logger.info("🧹 Starting one-time startup order cleanup...")
            
            total_cancelled = 0
            cleanup_summary = []
            
            # Get all pending orders across all markets
            all_orders = self.order_manager.get_pending_orders()
            logger.info(f"📋 Found {len(all_orders)} total pending orders across all markets")
            
            if not all_orders:
                logger.info("✓ No pending orders found during startup cleanup")
                
                # Check if we have any open positions that might need sell orders
                positions = self.position_manager.get_all_positions()
                if not positions:
                    logger.info("✓ No positions found - startup cleanup complete")
                    # Mark as completed and return since there's nothing to clean up
                    self._startup_cleanup_completed = True
                    return
                else:
                    logger.info(f"📍 Found {len(positions)} open positions - checking for orphaned positions...")
                    # Continue to orphaned position check below
            
            # Only process orders if there are any
            if all_orders:
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
                    
                    market_cancelled = 0
                    
                    # SIMPLIFIED CLEANUP: Cancel ALL buy orders (regardless of age)
                    if len(buy_orders) > 0:
                        logger.warning(f"🧹 Found {len(buy_orders)} buy orders for {market} - cancelling ALL")
                        
                        # Cancel ALL buy orders unconditionally
                        for order in buy_orders:
                            try:
                                age_info = f"from {order.created_at.date()}" if order.created_at.date() != today else "from today"
                                logger.info(f"🗑️  Cancelling buy order: {order.client_id} {age_info} in {market}")
                                self.order_manager.cancel_order(order.client_id)
                                market_cancelled += 1
                                total_cancelled += 1
                            except Exception as e:
                                logger.error(f"Failed to cancel buy order {order.client_id}: {e}")
                    
                    # REMOVED: Sell order cleanup - preserve all existing sell orders
                    # Daily Range Strategy: Sell orders remain until filled (no time limit)
                    
                    if market_cancelled > 0:
                        cleanup_summary.append(f"{market}: {market_cancelled} orders")
                
                if total_cancelled > 0:
                    logger.info(f"✓ Startup cleanup completed: Cancelled {total_cancelled} stale orders")
                    logger.info(f"  Details: {', '.join(cleanup_summary)}")
                else:
                    logger.info("✓ No stale orders found during startup cleanup")
            
            # CRITICAL: Check for orphaned positions after cancelling all buy orders
            # This ensures we have a clean base for trading
            logger.info("🔧 Checking for orphaned positions after buy order cleanup...")
            
            # Get markets from actual positions that exist (instead of empty trading_markets list)
            positions = self.position_manager.get_all_positions()
            position_markets = {pos.market for pos in positions}
            
            if not position_markets:
                logger.info("✅ No positions found - no orphaned position check needed")
            else:
                logger.info(f"🔍 Checking {len(position_markets)} markets with positions: {', '.join(sorted(position_markets))}")
            
            for market in position_markets:
                try:
                    # Calculate position vs sell order balance
                    balance = self._calculate_position_sell_balance(market)
                    
                    if balance['position_exists'] and balance['missing_sell'] > 0.000001:
                        logger.info(f"🔧 Startup: Found {balance['missing_sell']:.6f} uncovered position for {market}")
                        logger.info(f"   Position: {balance['position_size']:.6f}, Total sells: {balance['total_sells']:.6f}")
                        
                        # Place orphaned sell order for uncovered amount
                        success = self._place_missing_sell_order(market, balance['missing_sell'])
                        
                        if success:
                            logger.info(f"✅ Orphaned sell order placed for {market} - clean base established")
                            log_trading_event('startup_orphaned_sell', f"Placed orphaned sell for {balance['missing_sell']:.6f} {market} at startup")
                        else:
                            logger.error(f"❌ Failed to place orphaned sell order for {market}")
                            log_trading_event('startup_orphaned_fail', f"Failed to place orphaned sell for {market} at startup")
                    else:
                        logger.info(f"✅ No orphaned positions for {market} - position fully covered or no position")
                        
                except Exception as e:
                    logger.error(f"Error checking orphaned position for {market}: {e}")
            
            logger.info("✅ Startup order cleanup and orphaned position check completed")
            
            # Mark startup cleanup as completed (one-time execution)
            self._startup_cleanup_completed = True
                
        except Exception as e:
            logger.error(f"Error during startup order cleanup: {e}")
    
    async def _update_account_status(self):
        """Update account balance and status using REST API polling."""
        try:
            # Only update balance via HTTP if recently cached data is stale or missing
            update_balance_via_http = True
            
            if hasattr(self.status, 'balance_details') and self.status.balance_details:
                last_ws_update = self.status.balance_details.get('last_update')
                if last_ws_update:
                    age = (datetime.now(timezone.utc) - last_ws_update).total_seconds()
                    if age < 120:  # Cached balance data is fresh (< 2 minutes)
                        update_balance_via_http = False
                        logger.debug("Using cached balance data (HTTP request skipped)")
            
            if update_balance_via_http:
                logger.info("Updating balance via HTTP")
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
        """Sync positions with exchange using REST API polling."""
        try:
            logger.info("Performing periodic position sync via HTTP")
            
            self.position_manager.sync_with_exchange()
            
            # Sync existing orders with order tracker
            await self.order_tracker.sync_existing_orders()
            
            # CRITICAL: Also sync OrderManager state to prevent stale order issues
            logger.info("Syncing existing orders from exchange...")
            orders_synced = self.order_manager.load_existing_orders()
            logger.info(f"Synced {orders_synced} existing orders")
            
            # Check for completed sell orders to set cycle completion flags
            self._check_for_completed_sell_orders()
            
            # Using fresh exchange data - no validation needed
            
            # Enhanced position sync logging with details
            positions = self.position_manager.get_all_positions()
            logger.info(f"Position sync completed: {len(positions)} positions")
            
            # Display detailed position information
            for position in positions:
                value = position.size * position.avg_entry_price
                logger.info(f"📍 Position: {position.market} | Size: {position.size:.6f} | Avg Entry: ${position.avg_entry_price:.4f} | Value: ${value:.2f}")
            
        except Exception as e:
            logger.error(f"Error syncing with exchange: {e}")
    
    def _check_for_completed_sell_orders(self):
        """Check for completed sell orders and set cycle completion flags"""
        try:
            # Import and check trading timeframe
            from config.settings import TRADING_INTERVAL
            timeframe = TRADING_INTERVAL
            
            # Set up period checking based on timeframe
            now = datetime.now(timezone.utc)
            if timeframe == 'hourly':
                current_period = now.strftime('%Y-%m-%d-%H')
                period_name = "current hour"
            else:
                current_period = now.date()
                period_name = "today"
            
            completed_sells = []
            
            # Check all active orders for completed sells
            for client_id, order in list(self.order_manager.active_orders.items()):
                if order.side == OrderSide.SELL:
                    # Store order data before status update (to avoid race condition)
                    order_market = order.market
                    order_created_at = order.created_at
                    
                    # Update order status from exchange
                    updated_order = self.order_manager.update_order_status(client_id)
                    
                    # Check if order was completed and removed during status update
                    if updated_order is None or client_id not in self.order_manager.active_orders:
                        # Order was removed - verify actual status via API
                        if timeframe == 'hourly':
                            order_period = order_created_at.strftime('%Y-%m-%d-%H')
                            is_current_period = order_period == current_period
                        else:
                            order_period = order_created_at.date()
                            is_current_period = order_period == current_period
                            
                        is_orphaned = "_OS_" in client_id
                        
                        # Try to verify actual order status
                        if order.exchange_order_id:
                            try:
                                order_data = self.client.get_order_status(
                                    market=order_market,
                                    order_id=safe_int(order.exchange_order_id)
                                )
                                if order_data:
                                    status = order_data.get('status', 'unknown')
                                    fill_time = order_data.get('finished_at', 'unknown')
                                    avg_price = safe_float(order_data.get('avg_price', 0))
                                    
                                    if status == 'filled':
                                        if is_current_period and not is_orphaned:
                                            self._cycle_completion_flags[order_market] = True
                                            completed_sells.append(order)
                                            logger.info(f"✅ SELL order {client_id} confirmed FILLED at {fill_time} @ ${avg_price:.4f} - cycle complete for {order_market}")
                                            log_trading_event('cycle_complete', f"Confirmed sell fill for {order_market} - cycle complete ({timeframe} strategy)")
                                        elif is_current_period and is_orphaned:
                                            logger.info(f"📌 Orphaned sell order {client_id} confirmed FILLED at {fill_time} @ ${avg_price:.4f}")
                                        else:
                                            logger.debug(f"Sell order {client_id} confirmed FILLED from {order_period}")
                                    elif status in ['cancelled', 'canceled']:
                                        logger.info(f"❌ Sell order {client_id} confirmed CANCELLED for {order_market}")
                                    else:
                                        logger.warning(f"⚠️ Sell order {client_id} has unknown status: {status}")
                                        
                            except Exception as e:
                                logger.warning(f"Could not verify sell order {client_id}: {e}")
                                # Fallback to old assumption logic
                                if is_current_period and not is_orphaned:
                                    self._cycle_completion_flags[order_market] = True
                                    completed_sells.append(order)
                                    logger.info(f"🔄 Cycle completion flag set for {order_market} - sell order {client_id} disappeared (assumed filled)")
                        else:
                            # No exchange order ID - use fallback
                            if is_current_period and not is_orphaned:
                                self._cycle_completion_flags[order_market] = True
                                completed_sells.append(order)
                                logger.info(f"🔄 Cycle completion flag set for {order_market} - sell order {client_id} completed (no exchange ID)")
            
            if completed_sells:
                logger.info(f"✅ Detected {len(completed_sells)} completed sell orders from {period_name} ({timeframe} strategy)")
            
        except Exception as e:
            logger.error(f"Error checking for completed sell orders: {e}")
    
            return False
    
    async def _get_exchange_orders_with_retry(self, market: str, max_retries: int = 3):
        """Get exchange orders with retry logic for reliability"""
        for attempt in range(max_retries):
            try:
                response = self.client.get_pending_orders(market=market)
                if response:
                    logger.debug(f"✅ API call successful for {market} (attempt {attempt + 1})")
                    return response
                else:
                    logger.warning(f"⚠️ API returned empty response for {market} (attempt {attempt + 1})")
                    
            except Exception as e:
                if attempt < max_retries - 1:
                    delay = 2 ** attempt  # 2s, 4s, 8s exponential backoff
                    logger.warning(f"⚠️ API call failed for {market} (attempt {attempt + 1}), retrying in {delay}s: {e}")
                    import asyncio
                    await asyncio.sleep(delay)
                else:
                    logger.error(f"❌ API call failed for {market} after {max_retries} attempts: {e}")
                    raise
        return None

    
    def get_pairing_status(self) -> Dict[str, Any]:
        """Get current pairing system status"""
        try:
            if not self.pairing_manager or not self.order_tracker:
                return {"error": "Pairing system not initialized"}
            
            pairing_stats = self.pairing_manager.get_pairing_statistics()
            tracker_stats = self.order_tracker.get_statistics()
            
            return {
                "pairing_enabled": pairing_stats["auto_pairing_enabled"],
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
        """Get runtime statistics (legacy API name retained)."""
        stats: Dict[str, Any] = {}

        if self.market_data:
            stats["market_data"] = self.market_data.get_data_source_stats()

        if self.pairing_manager and self.order_tracker:
            pairing_stats = self.pairing_manager.get_pairing_statistics()
            tracker_stats = self.order_tracker.get_statistics()
            stats["order_tracking"] = {
                **pairing_stats,
                **tracker_stats,
            }

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
