# Bot Analysis Notes - Issues to Investigate Later

*Notes collected while walking through the bot logic step by step*

## 1. WebSocket vs REST API Investigation

**Issue:** WebSocket is DISABLED in current bot logic (line 398-409 in trading_bot.py)
```python
# DISABLED: WebSocket connection - using REST API only for reliability
logger.info("WebSocket disabled - using REST API polling for order tracking...")
```

**Questions to investigate:**
- How does `order_tracker` work without WebSocket real-time updates?
- Are we making sufficient REST API calls to compensate for missing real-time data?
- Is this causing delays in order fill detection?
- Could this be related to the duplicate buy order bug (orders not detected fast enough)?

**Code locations:**
- `/src/core/trading_bot.py:398-409` - WebSocket disabled
- `OrderTracker` class - needs investigation of REST polling frequency
- Order fill detection logic - may need more frequent polling

---

## 2. State Management and Circuit Breakers

**Issue:** Bot has complex state management and circuit breaker systems
```python
# Initialize trading day tracking
self._last_trading_day = datetime.now(timezone.utc).date()
# Load current state from exchange (no database)
await self._initialize_from_exchange()
```

**Why `_initialize_from_exchange()` is CRITICAL:**
```python
async def _initialize_from_exchange(self):
    # 1. Load ALL existing orders from exchange
    loaded_orders = self.order_manager.load_existing_orders()
    
    # 2. Sync ALL positions with exchange  
    self.position_manager.sync_with_exchange()
    
    # 3. Sync order tracker with current state
    await self.order_tracker.sync_existing_orders()
    
    # 4. Clean up stale orders on startup
    await self._perform_startup_order_cleanup()
```

**Questions to investigate:**
- Does this properly detect existing positions/orders that caused our duplicate buy bug?
- If the bot restarts, does it correctly see the 10 SUI position from before?
- Is `_perform_startup_order_cleanup()` too aggressive (removing valid orders)?
- Could this be where the "phantom order" cleanup happens?

**Code locations:**
- `/src/core/trading_bot.py:470-503` - Full state loading method  
- `_perform_startup_order_cleanup()` method - may be related to phantom order bug

---

## 3. Stateless Architecture Investigation

**MAJOR DESIGN CONSIDERATION:** Move to purely stateless, exchange-based operation

**Current Issues:**
- Bot maintains internal state (order_manager, position_manager, order_tracker)
- State can become inconsistent with exchange reality
- Startup state loading can fail or be incomplete
- Local caches can have stale data

**Proposed Investigation:**
```python
# Instead of maintaining local state, always query exchange:
def should_place_buy_order(self, market):
    # 1. Get ALL orders from current trading period (hour/day) from exchange
    current_period_orders = exchange.get_orders_for_period(market, current_hour_or_day)
    
    # 2. Get current positions directly from exchange  
    current_positions = exchange.get_positions(market)
    
    # 3. Analyze exchange data to make decisions
    has_buy_today = any(order.side == 'buy' for order in current_period_orders)
    uncovered_position = position_size - sum(sell_orders)
    
    # 4. Make decision based purely on exchange reality
    return not has_buy_today and no_pending_sells
```

**Benefits of stateless approach:**
- Always operates on current exchange reality
- No state synchronization issues
- No phantom order problems
- Restart-safe by design
- Easier to debug (just look at exchange)

**Questions to investigate:**
- Would API rate limits allow frequent fresh queries?
- How to optimize exchange queries for performance?
- Would this eliminate the duplicate buy order bug?
- How to handle trading period boundaries (hour/day transitions)?

**Code locations to investigate:**
- All `order_manager.load_existing_orders()` calls
- All `position_manager.sync_with_exchange()` calls  
- All local caching logic

---

## 4. Trading Cycle Detection Bug

**CRITICAL ISSUE:** Bot only checks for new trading DAY, not new trading PERIOD

**Current Code in `execute_trading_cycle()`:**
```python
# Check for new trading day
current_day = now.date()
if current_day > self._last_trading_day:
    logger.info(f"New trading day detected: {current_day}")
    self._last_trading_day = current_day
```

**Problem:** 
- This only detects new DAYS, not new HOURS
- For hourly trading, we need to detect new HOURS (e.g., 14:00 → 15:00)
- The bot should reset signals and allow new trades at each hour boundary for hourly mode

**Expected Logic:**
```python
# For hourly mode:
if TRADING_INTERVAL == 'hourly':
    current_hour = now.strftime('%Y-%m-%d-%H')  # "2025-09-05-16"
    if current_hour > self._last_trading_period:
        logger.info(f"New trading hour detected: {current_hour}")

# For daily mode:  
elif TRADING_INTERVAL == 'daily':
    current_day = now.date()
    if current_day > self._last_trading_day:
        logger.info(f"New trading day detected: {current_day}")
```

**UPDATE - RESOLVED:** Signal generation DOES have proper hour detection!

**Found in `_check_and_generate_signals()` method:**
```python
if TRADING_INTERVAL == 'hourly':
    current_hour = now.strftime('%Y-%m-%d-%H')  # "2025-09-05-16"
    needs_signal = (not last_generation or 
                   last_generation.strftime('%Y-%m-%d-%H') != current_hour)
    if needs_signal:
        signal = self.strategy.generate_hourly_signal(market)
```

**Conclusion:** The main trading cycle only checks for new days (for cleanup), but signal generation correctly handles hourly boundaries. This is likely NOT the source of our bugs.

**Code locations:**
- `/src/core/trading_bot.py:726-733` - Main cycle (day detection only)
- `/src/core/trading_bot.py:813-825` - Signal generation (proper hour detection)

---

## 5. Number Precision and Safe Conversions

**ISSUE:** Potential precision loss in number formatting and conversions

**Current Code:**
```python
logger.info(f"📊 DAILY SIGNAL | {market} | Buy=${signal.buy_price:.8f} | Sell=${signal.sell_price:.8f}")
```

**Questions to investigate:**
- Are we losing precision with `.8f` formatting in logs and calculations?
- Should we use `safe_float()` consistently for all price/amount handling?
- Are exchange API responses parsed with full precision?
- Could precision issues cause order placement failures?

**Code locations to check:**
- All price formatting in logs
- All `safe_float()` usage vs direct float conversion
- Order placement amount/price calculations

---

## 6. Order Placement Response Processing

**ISSUE:** Need to verify complete order placement response handling

**Questions to investigate:**
- Are we getting the FULL response from order placement requests?
- Are we processing all response fields correctly (order_id, status, filled_amount, etc.)?
- Are we handling partial fills properly in the response?
- Are we catching all possible error conditions from exchange?

**Code locations to investigate:**
- Order placement methods
- Response parsing logic
- Error handling in order requests

---

## 7. Previous Bug Analysis Verification

**TIMELINE FROM LOGS - NEEDS VERIFICATION:**

**Bug 1: Duplicate Buy Orders**
1. **22:08:40** - First buy order placed: `DRA_1757110119_buy_SUIUSDT`
2. **22:08:53** - Bot detected order wasn't on exchange (phantom order detection) 
3. **22:08:53** - Removed "phantom" buy order from internal tracking
4. **22:08:55** - Bot checked `has_today_buy = False` (because phantom was removed)
5. **22:08:56** - Second buy order placed: `DRA_1757110135_buy_SUIUSDT` (filled immediately)

**Root Cause:** First buy order took **~13 seconds** to appear on exchange. Phantom cleanup logic removed it from tracking before exchange confirmed it.

**Bug 2: Delayed Sell Order** 
1. **22:08:40** - First buy order (10 SUI) - pending
2. **22:08:56** - Second buy order (10 SUI) - filled immediately  
3. **22:08:57** - First sell order placed for second buy only
4. **22:13:38** - Bot detected "MISSED FILL" - 20 SUI position but only 10 SUI covered
5. **22:13:39** - Second sell order placed as "recovery sell order"

**Root Cause:** Only immediate fills trigger sell orders. Pending orders that fill later don't get paired sells until position checking (5+ minutes later).

**CRITICAL: 13-Second Exchange Delay Issue**
- **Problem:** Order placement → Exchange visibility has 13+ second delay
- **Current timeout:** Too aggressive (removes orders before they appear)
- **Solution needed:** Handle exchange confirmation delays properly
- **Questions:** 
  - Is this normal for CoinEx API?
  - Should we wait longer before phantom cleanup?
  - Should we verify order placement success differently?
  - Are there better ways to track order lifecycle?

**VERIFIED: Exact Bug Location Found!**

**The Phantom Order Cleanup in `_check_daily_buy_status()` method (lines 1126-1146):**
```python
# Compare manager orders vs exchange orders
exchange_client_ids = {o.get('client_id') for o in current_period_exchange_buys}
phantom_orders = []
for order in current_period_buy_orders:
    if order.client_id not in exchange_client_ids:  # Order not visible on exchange
        phantom_orders.append(order)

if phantom_orders:
    logger.warning(f"Cleaning {len(phantom_orders)} phantom buy orders")
    for phantom_order in phantom_orders:
        # REMOVES the order from local tracking
        del self.order_manager.active_orders[phantom_order.client_id]
    
    # RECALCULATE has_today_buy after phantom removal
    remaining_period_buys = [o for o in current_period_buy_orders if o.client_id in exchange_client_ids]
    result['has_today_buy'] = len(remaining_period_buys) > 0  # NOW BECOMES FALSE!
```

**Exact Timeline Match:**
1. **22:08:40** - Order placed, stored in `order_manager.active_orders`
2. **22:08:53** - `_check_daily_buy_status()` called
3. **22:08:53** - Order not visible on exchange yet (13-second delay)
4. **22:08:53** - Order marked as "phantom" and **REMOVED** from tracking
5. **22:08:53** - `has_today_buy` recalculated → becomes `False`
6. **22:08:55** - Bot checks buy conditions, sees `has_today_buy = False`
7. **22:08:56** - Bot places duplicate order thinking no orders exist

**Root Cause:** Phantom cleanup is too aggressive and doesn't account for exchange visibility delays!

---

## 8. Order Placement Response Processing and Exchange Delay Management

**CRITICAL ISSUE:** How to handle order placement responses and exchange visibility delays efficiently

**Current Problems:**
1. **Immediate Fills:** In bearish markets, buy orders may fill instantly
2. **Exchange Delay:** Orders take 5-15 seconds to appear on exchange after placement
3. **Bot Logic Confusion:** During delay, bot thinks no orders exist → places duplicates
4. **Phantom Order Cleanup:** Aggressive cleanup removes valid orders before they appear

**Current Order Placement Flow (PROBLEMATIC):**
```python
# 1. Place order via API
order_response = await exchange.place_order(...)

# 2. Process response (immediate info only)
if order_response.success:
    # Store order in local tracking
    
# 3. Continue bot logic (PROBLEM: Exchange may not show order yet!)
# 4. Bot checks exchange → sees no orders → places duplicate
```

**Efficient Solutions (RECOMMENDATIONS):**

**Solution A: Immediate Response Tracking with Timeout Protection**
```python
async def place_buy_order_with_protection(self, market, price, amount):
    # 1. Place order and get immediate response
    response = await self.exchange.place_order(market, 'buy', price, amount)
    
    if response.success:
        order_id = response.order_id
        filled_amount = response.filled_amount or 0
        
        # 2. Immediate tracking - don't wait for exchange visibility
        self._track_pending_order(market, order_id, amount, filled_amount)
        
        # 3. For immediate fills, create sell order right away
        if filled_amount > 0:
            await self._create_immediate_sell_order(market, filled_amount, signal)
        
        # 4. Set protection flag to prevent duplicates for next 30 seconds
        self._set_order_placement_protection(market, 30)  # 30-second cooldown
        
        return True
    return False

def _should_place_buy_order(self, market):
    # Check protection flag FIRST before any other logic
    if self._is_order_placement_protected(market):
        logger.info(f"Order placement protected for {market} - recent order placed")
        return False
    
    # Continue with normal logic...
```

**Solution B: Two-Phase Order Confirmation**
```python
async def place_order_with_confirmation(self, market, price, amount):
    # Phase 1: Place order
    response = await self.exchange.place_order(market, 'buy', price, amount)
    
    if response.success:
        order_id = response.order_id
        
        # Phase 2: Verify visibility (with timeout)
        for attempt in range(10):  # Max 10 attempts = 10 seconds
            await asyncio.sleep(1)
            
            exchange_orders = await self.exchange.get_orders(market)
            if any(o.order_id == order_id for o in exchange_orders):
                logger.info(f"✅ Order {order_id} confirmed on exchange")
                return True
                
        # If not visible after 10 seconds, still consider it placed
        logger.warning(f"⚠️ Order {order_id} not visible on exchange but may be valid")
        return True
    
    return False
```

**Solution C: State-Based Protection (RECOMMENDED)**
```python
class OrderPlacementTracker:
    def __init__(self):
        self._recent_placements = {}  # {market: (timestamp, order_id)}
        
    def mark_order_placed(self, market: str, order_id: str):
        self._recent_placements[market] = (datetime.now(), order_id)
        
    def can_place_order(self, market: str) -> bool:
        if market in self._recent_placements:
            timestamp, _ = self._recent_placements[market]
            # Prevent duplicates for 30 seconds after placement
            if (datetime.now() - timestamp).total_seconds() < 30:
                return False
        return True
        
    def cleanup_old_protections(self):
        # Remove protections older than 60 seconds
        cutoff = datetime.now() - timedelta(seconds=60)
        self._recent_placements = {
            k: v for k, v in self._recent_placements.items() 
            if v[0] > cutoff
        }
```

**Best Practice Implementation:**
1. **Immediate tracking:** Track orders locally the moment API returns success
2. **Protection window:** 30-second cooldown after any order placement
3. **Handle immediate fills:** Process fills from the placement response immediately
4. **Async verification:** Verify exchange visibility in background, don't block
5. **Graceful degradation:** If verification fails, assume order is valid

**Questions to investigate:**
- How does current `_place_entry_order()` handle immediate fills?
- Are we processing the full order placement response?
- Is there any protection against rapid successive placements?
- How long is the typical exchange visibility delay?

**Code locations to check:**
- Order placement methods
- Response processing logic
- Phantom order cleanup timing
- Order tracking and state management

---

## 9. Future Investigation Items

*(Will be added as we continue through the code)*
