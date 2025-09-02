# CoinEx Trading Bot Troubleshooting Guide

This guide documents common issues encountered with the CoinEx trading bot and their proven solutions.

## Table of Contents

1. [API Signature Errors](#api-signature-errors)
2. [WebSocket Connection Issues](#websocket-connection-issues)
3. [Order Placement Problems](#order-placement-problems)
4. [Process Lock Issues](#process-lock-issues)
5. [Authentication Problems](#authentication-problems)
6. [Rate Limiting Issues](#rate-limiting-issues)
7. [Testing and Debugging](#testing-and-debugging)

## API Signature Errors

### Problem: "Signature Incorrect" Errors
**Symptoms:**
- API requests return error code with "Signature Incorrect" message
- Endpoints like `pending-orders` or `positions` fail with signature errors
- Same credentials work for some endpoints but not others

**Root Cause:**
CoinEx API has signature issues with **optional** `market` parameters in authenticated endpoints. When the `market` parameter is included in the request, it causes signature generation to fail.

**Affected Endpoints:**
- `/v2/futures/pending-order` - ✅ Fixed (commit c4d08f8)
- `/v2/futures/pending-position` - ✅ Fixed (commit 60d487a)

**Solution Pattern:**
1. Remove the optional `market` parameter from the API request
2. Fetch all data from the endpoint
3. Apply client-side filtering if market-specific data is needed

**Example Fix:**
```python
# BEFORE (causes signature error)
params = {
    'market_type': 'FUTURES',
    'market': market  # This causes signature issues
}

# AFTER (working solution)
params = {'market_type': 'FUTURES'}
# Remove market parameter, filter client-side later

# Client-side filtering
if market and response_data.get('data'):
    filtered_data = [item for item in response_data['data'] 
                     if item.get('market') == market]
    response_data['data'] = filtered_data
```

**Note:** This issue only affects endpoints where `market` is **optional**. Required `market` parameters (like in `order-status` endpoint) work correctly.

## WebSocket Connection Issues

### Problem: UTF-8 Decode Errors
**Symptoms:**
```
UnicodeDecodeError: 'utf-8' codec can't decode byte 0x1f in position 0
```

**Root Cause:**
CoinEx WebSocket sends compressed (gzip) binary messages that need decompression before UTF-8 decoding.

**Solution:**
```python
import gzip

if isinstance(raw_message, bytes):
    try:
        # Try to decompress gzip data
        decompressed = gzip.decompress(raw_message)
        message_str = decompressed.decode('utf-8')
    except gzip.BadGzipFile:
        # Fallback to direct decode for non-compressed messages
        message_str = raw_message.decode('utf-8')
```

### Problem: WebSocket Authentication Failures
**Symptoms:**
- Connection established but authentication fails
- Warnings about invalid authentication responses

**Root Cause:**
- Timestamp format (string vs integer)
- Response parsing expecting wrong field names

**Solution:**
```python
# Use integer timestamp for WebSocket auth
timestamp = int(time.time() * 1000)  # Not str()

# Check response 'code' field (0 = success)
if response.get('code') == 0:
    self.authenticated = True
```

### Problem: Invalid Subscription Arguments
**Symptoms:**
```
{"id": 123, "error": {"code": 1, "message": "invalid argument"}}
```

**Root Cause:**
WebSocket subscription uses `market_list` parameter, not `market`.

**Solution:**
```python
# BEFORE (invalid argument error)
params = {"market": ["BTCUSDT"]}

# AFTER (working)
params = {"market_list": ["BTCUSDT"]}
```

### Problem: WebSocket Disconnections After ~30 Seconds
**Symptoms:**
- Connection drops after idle period  
- No automatic reconnection

**Root Cause:**
Missing or insufficient heartbeat/ping mechanism.

**Solution:**
```python
# Reduced heartbeat interval in config/settings.py
WS_HEARTBEAT_INTERVAL = 20  # seconds (was 30)

# Improved WebSocket connection parameters
self.websocket = await websockets.connect(
    COINEX_WS_URL,
    ping_interval=None,  # Use custom heartbeat instead
    ping_timeout=30,
    close_timeout=10
)

# Enhanced heartbeat with proper logging
async def _heartbeat(self):
    while self.is_connected:
        await asyncio.sleep(WS_HEARTBEAT_INTERVAL)
        ping_message = {"method": "server.ping", "params": {}, "id": self.get_next_message_id()}
        await self._send_message(ping_message)
        logger.info(f"Sent WebSocket heartbeat #{count} - keeping connection alive")
```

**Verification:** WebSocket maintains connection for 60+ seconds, heartbeat messages logged every 20 seconds.

### Problem: WebSocket Not Detecting Real-Time Orders  
**Symptoms:**
- WebSocket connects and authenticates successfully
- Existing orders detected via REST API sync
- New orders placed during connection are NOT detected
- Order updates not received in real-time

**Root Cause:**
Critical message parsing bug - CoinEx sends data in `"data"` field but code expected `"params"` field.

**Solution:**
```python
# BEFORE (incorrect)
def _handle_subscription_update(self, message: Dict):
    params = message.get("params", {})  # ❌ Wrong field
    self.message_handlers[method](params)

# AFTER (correct)  
def _handle_subscription_update(self, message: Dict):
    data = message.get("data", {})  # ✅ Correct field
    self.message_handlers[method](data)

# Also fix message structure expectations
def _handle_order_update(self, data: Dict):
    order_data = data.get("order", {})  # Single object, not array
    # Process single order...
```

**CoinEx Message Structure:**
```json
{
    "method": "order.update", 
    "data": {
        "event": "put", 
        "order": {"order_id": 123, "market": "ETHUSDT", ...}
    }
}
```

**Verification:** 
- See `📨 Received important message: order.update` in logs
- Order details correctly extracted and processed
- Real-time detection of order placement, fills, and cancellations

## Order Placement Problems

### Problem: Order Object Attribute Errors
**Symptoms:**
```
AttributeError: 'Order' object has no attribute 'order_id'
```

**Root Cause:**
Code tries to access `order.order_id` but Order objects use `exchange_order_id` attribute instead.

**Solution:**
```python
# BEFORE (causes AttributeError)
order_id = order.order_id  # ❌ Wrong attribute

# AFTER (correct)
order_id = order.exchange_order_id  # ✅ Correct attribute

# Complete fix example
self.order_tracker.track_order(
    order_id=str(order.exchange_order_id),  # Fixed
    client_id=order.client_id,
    market=market,
    side=OrderSide.BUY,
    amount=quantity,
    price=price
)
```

**Fixed in commit:** 8269c92 - All instances of `order.order_id` changed to `order.exchange_order_id`

### Problem: Order Status Check Signature Errors
**Symptoms:**
- Periodic "Signature Incorrect" errors every 30-120 seconds
- Errors from `update_order_status()` method in order manager
- Bot continues to work but logs show API failures

**Root Cause:**
The `/v2/futures/order-status` endpoint has signature validation issues when called frequently, even though the `market` parameter is required.

**Solution:**
Added graceful error handling to continue bot operation:
```python
except Exception as e:
    # Handle common API signature errors gracefully
    if "Signature Incorrect" in str(e):
        logger.warning(f"Order status check failed due to signature error for {client_id}: {e}")
        logger.debug("This is a known CoinEx API issue - continuing without status update")
        # Return order without modification - WebSocket updates will handle status changes
        return order
    elif "Rate limit" in str(e) or "429" in str(e):
        logger.warning(f"Rate limit hit checking order status for {client_id} - will retry later")
        return order
    else:
        logger.error(f"Failed to update order status {client_id}: {e}")
        return order
```

**Additional Optimization:**
Reduced sync frequency from 30 seconds to 2 minutes to minimize API calls:
```python
# Sync positions less frequently to avoid rate limits
if (not self._last_positions_sync or 
    (now - self._last_positions_sync).seconds > 120):  # Was 30s
    await self._sync_positions()
```

**Note:** WebSocket updates handle real-time order status changes, so periodic REST API checks are less critical.

**Latest Solution (commit c5c904a):**
Added configurable status check intervals and option to disable REST API checks completely:
```bash
# In .env file or environment:
ORDER_STATUS_CHECK_INTERVAL=300  # Check every 5 minutes (default)
ORDER_STATUS_CHECK_INTERVAL=-1   # Disable REST API checks completely (recommended)
```

When set to `-1`, the bot relies entirely on WebSocket updates for order status changes, eliminating signature errors from periodic status checks. Since WebSocket provides real-time updates, this is the recommended approach.

### Problem: Credential Displacement Between Bot Accounts - CRITICAL
**Symptoms:**
- ETH bot connects to ADA account despite correct environment variables
- Orders placed by ETH bot appear in wrong CoinEx account
- Bot logs show "loaded X existing orders" from different account
- Balance updates reflect wrong account values

**Root Cause:**
Multi-bot deployments can have credential displacement when:
1. **External SSD Environment Files**: Build location environment files contain wrong credentials
2. **Docker Build Context**: Wrong credentials copied into Docker image layers during build
3. **Cached Credentials**: Old credentials embedded in existing Docker images

**Critical Impact:**
- ETH orders placed in ADA account (cross-account contamination)
- Trading strategy disrupted across multiple accounts
- Positions opened in unintended accounts

**Complete Fix Process:**
```bash
# 1. Audit all credential storage locations
find /path/to/build/location -name "*.env*" -exec cat {} \;
grep -r "COINEX_" /path/to/external/ssd/builds/

# 2. Fix External SSD environment files
# Update .env.eth with correct ETH credentials:
COINEX_API_KEY="377EB9E769AB4D0AAC13224A54B37946"
COINEX_API_SECRET="83ADBF2285D42A626FB0612E0D020EBE1452236217992B53"

# 3. Clean all Docker images with wrong credentials  
docker rmi $(docker images | grep trader-bot | awk '{print $3}')
docker system prune -f

# 4. Build fresh Docker image from clean source
cd /external/ssd/build/location
docker build --platform linux/amd64 -t trader-bot-final:amd64 /path/to/source
docker save trader-bot-final:amd64 | gzip > trader-bot-final-amd64.tar.gz

# 5. Deploy with verified credential separation
./deploy-final.sh
```

**Verification Steps:**
1. **Check order isolation**: ETH bot should show 0 existing orders (if new account)
2. **Verify balance updates**: Balance changes should reflect correct account
3. **Monitor order placement**: New orders should appear in intended account only
4. **Test cross-account separation**: ADA bot stopped, ETH bot running independently

**Example Success Indicators:**
```
# BEFORE (credential displacement)
[INFO] Loading existing orders from exchange...
[INFO] ✅ Loaded 7 existing orders for ETHUSDT  # ❌ From ADA account

# AFTER (correct separation)  
[INFO] Buy order placed successfully: DRA_1754434324_buy_ETHUSDT -> 179595936933
[INFO] 💰 Balance updated: $164.90 → $147.13  # ✅ Correct ETH account
```

**Prevention:**
- Always verify environment files in build locations match intended credentials
- Use separate build directories for different bot accounts
- Test credential separation before production deployment
- Regular audit of all credential storage locations

**Fixed in commit:** [Current commit] - Complete credential separation and documentation

### Problem: Missing Position Detection After Bot Restart - CRITICAL
**Symptoms:**
- Bot restarts and places new buy order despite existing uncovered position
- Duplicate positions created instead of placing missing sell orders
- Bot doesn't recognize existing positions without corresponding sell orders
- Multiple buy orders accumulate over restarts

**Root Cause:**
Position detection logic had critical gaps:
1. **Orphaned position detection only ran during cleanup phases** (day reset or startup with old orders)
2. **Normal buy flow had NO position checking** despite comment claiming otherwise
3. **Position balancing logic was completely DISABLED** (lines 683-686, 1214-1216)
4. **Second restart scenario**: No cleanup triggered → No position check → Duplicate buy order

**Example Bug Scenario:**
```
First restart:  ETH bot → places buy order → order fills → creates position
Second restart: No old orders to clean up → Cleanup phase SKIPPED
                → Position detection SKIPPED → Places duplicate buy order
Result:         Two positions instead of missing sell order
```

**Complete Fix Applied:**
```python
# 1. Independent position check for ALL scenarios (Phase 1.9)
if not should_cancel_orders:  # When cleanup didn't run
    balance = self._calculate_position_sell_balance(market)
    if balance['position_exists'] and balance['missing_sell'] > 0.000001:
        # Place missing sell order, delay buy until covered
        return False

# 2. Position check before cycle completion buys
if cycle_complete_flag:
    balance = self._calculate_position_sell_balance(market)
    if balance['position_exists']:
        # Handle position first, delay cycle buy
        return False

# 3. Position check before daily first buy
elif not has_today_buy:
    balance = self._calculate_position_sell_balance(market)
    if balance['position_exists']:
        # Handle position first, delay first buy
        return False
```

**Fix Strategy:**
- **DELAY (not block)** buy orders when uncovered positions exist
- **Place missing sell orders first**, then allow buy orders in next cycle
- **Maintains trading flow** while ensuring position safety
- **Preserves all existing safety logic** (pending sell blocks, funding fee protection, etc.)

**Verification Steps:**
1. **Restart with uncovered position**: Should detect position and place missing sell order
2. **Second cycle**: Position now covered, allows normal buy order
3. **No duplicate positions**: Never places buy while positions are uncovered
4. **All existing protections maintained**: Pending sell blocking still works

**Log Indicators:**
```
# SUCCESS: Position detected and handled
[INFO] 🔧 Independent position check: Uncovered position detected for ETHUSDT: 0.001000 uncovered
[INFO] ✅ Missing sell order placed during independent check - buy can proceed next cycle

# THEN: Buy order proceeds in next cycle
[INFO] 🌅 First buy order of the day allowed for ETHUSDT
```

**Fixed in commit:** [Current commit] - Critical position detection restored with delayed buy logic

### Problem: Orphaned Position Sell Orders Blocking New Buys
**Symptoms:**
- Day reset cancels old buy orders and detects uncovered positions (good)
- Bot places missing sell orders for orphaned positions (good)
- Fresh sell orders count as "today's sell orders" and block new buys (bad)
- Bot gets stuck unable to place new buy orders despite proper position handling

**Root Cause:**
The `_get_today_pending_sell_orders()` function doesn't distinguish between:
- **Orphaned position sell orders**: Placed to cover old uncovered positions
- **Regular trading sell orders**: From today's fresh buy-sell trading cycles

Result: Orphaned position recovery prevents new trading indefinitely.

**Example Scenario:**
```
09:00 - Day reset → Cancels old orders
09:01 - Detects uncovered ETH position from weeks ago
09:01 - Places sell order to cover orphaned position
09:02 - Tries to place new buy → BLOCKED by "today's sell order"
Result: Trading stuck despite correct position handling
```

**Grace Period Solution Applied:**
```python
# When placing orphaned sell order:
self._orphaned_sell_grace_until[market] = now + timedelta(minutes=10)

# When checking today's sell orders:
if in_grace_period:
    # Exclude sells placed during grace period from blocking count
    return only_regular_sells_count
```

**Fix Behavior:**
1. **Orphaned sell placed**: 10-minute grace period starts
2. **During grace period**: Orphaned sells don't count as blocking sells
3. **New buys allowed**: Trading resumes immediately
4. **After 10 minutes**: Normal sell blocking resumes

**Log Indicators:**
```
[INFO] ✅ Missing sell order placed for orphaned position
[INFO] ⏰ Grace period active for 10 minutes - new buys allowed despite orphaned sell
[INFO] 📊 Grace period active (8 min remaining): 1 total sells, 1 orphaned, 0 blocking
[INFO] ⏰ Orphaned sells ignored during grace period - new buys allowed
[INFO] 🌅 First buy order of the day allowed for ETHUSDT
```

**Benefits:**
- Allows immediate trading resumption after position recovery
- Preserves safety for regular trading sell orders
- Time-limited grace period prevents abuse
- Works with any position age (weeks old or today's)

**Fixed in commit:** [Current commit] - Grace period for orphaned position sell orders

### Problem: CoinEx API Client_ID Length Limit - "Invalid Argument" Errors
**Symptoms:**
```
ERROR | API error: invalid argument
ERROR | Failed to place sell order DRA_1754446528302_orphaned_sell_ETHUSDT: CoinEx API error: invalid argument
```
- Orphaned position sell orders consistently fail with "invalid argument" error
- Regular buy/sell orders work fine
- API authentication succeeds but order placement fails

**Root Cause:**
CoinEx futures API v2 has a **32-byte maximum length limit** for the `client_id` parameter. Our orphaned sell client_id format was exceeding this limit:

```
Original format: DRA_1754446528302_orphaned_sell_ETHUSDT (42 characters)
API limit:       32 bytes maximum
Result:          "invalid argument" API error
```

**Solution Applied:**
Shortened the orphaned sell client_id format using "OS" abbreviation:

```python
# BEFORE (42 characters - exceeds limit)
client_id = f"DRA_{timestamp}_orphaned_sell_{market}"

# AFTER (24 characters - within limit)  
client_id = f"DRA_{timestamp}_OS_{market}"
```

**Updated Detection Logic:**
```python
# Updated orphaned sell detection in trading_bot.py
if "_OS_" in order.client_id:  # Was: "orphaned_sell" in order.client_id
    orphaned_sell_count += 1
```

**Verification:**
- Orphaned sell orders now place successfully: `DRA_1754446581616_OS_ETHUSDT`
- API "invalid argument" errors eliminated
- All orphaned sell detection logic continues to work
- Client_id length: ~24 characters (well within 32-byte limit)

**Example Success Logs:**
```
[INFO] Placing sell order: ETHUSDT 0.02 @ $3838.96 [DRA_1754446581616_OS_ETHUSDT]  
[INFO] ✅ Missing sell order placed for orphaned position
[INFO] 📊 Today's sell orders: 2 total, 2 orphaned (ignored), 0 blocking
[INFO] ✅ Orphaned sell orders do not block new buy orders
```

**API Documentation Reference:**
According to CoinEx futures API v2 documentation (https://docs.coinex.com/api/v2/), the `client_id` parameter has specific length restrictions that must be observed for successful order placement.

**Fixed in commit:** [Current commit] - Shortened orphaned sell client_id format to comply with CoinEx API 32-byte limit

### Problem: Order Validation Never Runs - AttributeError and Logic Failures - CRITICAL
**Symptoms:**
- Bot shows "EMERGENCY BLOCK" for 4+ hours with stale sell orders from earlier in the day
- Sell orders filled hours ago still showing as "pending" in bot cache
- WebSocket missed fill detection with no HTTP API fallback working
- AttributeError: 'DailyRangeBot' object has no attribute 'exchange_client'
- Normal trading flow completely blocked despite orders being actually filled

**Example Scenario:**
```
11:34:47 UTC - Sell order fills and position closes
11:35:00 UTC - WebSocket misses the fill event  
15:45:00 UTC - Bot still shows sell as "pending", blocks all new buy orders
```

**Root Cause Analysis:**
Multiple critical failures in validation system:
1. **`_validate_order_manager_state()` never executed** - used undefined `_current_market` variable
2. **Attribute error prevented API calls** - code used `self.exchange_client` instead of `self.client`
3. **Validation frequency too slow** - only every 3 minutes, insufficient for timely cleanup
4. **No forced validation before buy decisions** - relied entirely on broken periodic validation

**Technical Details:**
```python
# BROKEN CODE PATTERNS:
async def _validate_order_manager_state(self):
    market = getattr(self, '_current_market', None)  # ❌ Always None - never runs
    response = self.exchange_client.get_pending_orders(market=market)  # ❌ Wrong attribute

# ERROR LOGS:
[ERROR] ❌ API call failed for ETHUSDT after 3 attempts: 'DailyRangeBot' object has no attribute 'exchange_client'
```

**Comprehensive Fix Applied:**
```python
# 1. FIXED MARKET DETECTION - Loop through actual trading markets
async def _validate_order_manager_state(self):
    for market in self.trading_markets:  # ✅ Uses real markets from env/command line
        
# 2. FIXED ATTRIBUTE ERROR - Use correct client reference  
        response = self.client.get_pending_orders(market=market)  # ✅ Correct attribute

# 3. ADDED RETRY LOGIC - Handle API failures gracefully
async def _get_exchange_orders_with_retry(self, market: str, max_retries: int = 3):
    for attempt in range(max_retries):
        try:
            response = self.client.get_pending_orders(market=market)
            return response
        except Exception as e:
            if attempt < max_retries - 1:
                delay = 2 ** attempt  # 2s, 4s, 8s exponential backoff
                await asyncio.sleep(delay)

# 4. INCREASED VALIDATION FREQUENCY - From 3 minutes to 1 minute
if (now - self._last_positions_sync).seconds > 60:  # Was 180s

# 5. FORCE VALIDATION BEFORE BUY DECISIONS - Ensure fresh status
def _should_place_buy_order(self, market: str, signal: TradingSignal, current_price: float) -> bool:
    # Force fresh validation before emergency check
    asyncio.create_task(self._validate_order_manager_state())
    time.sleep(0.5)  # Let validation complete
```

**Impact and Recovery:**
- **ETH Bot**: Fixed immediately, validation now runs every ~6 seconds successfully
- **ADA Bot**: Detected 6+ hour old stale sell order, cleanup process initiated
- **Both Bots**: Normal trading flow restored, no more "EMERGENCY BLOCK" from stale orders
- **API Reliability**: Retry logic handles temporary CoinEx API failures gracefully

**Verification Patterns:**
```
# SUCCESS: Validation actually running
[INFO] 🔍 Validating order status for ETHUSDT
[INFO] ✅ API call successful for ETHUSDT (attempt 1)
[INFO] 📊 Internal cache has 1 orders for ETHUSDT
[INFO] 🔍 Exchange has 0 pending orders for ETHUSDT

# SUCCESS: Stale order cleanup
[WARNING] 🧹 Removed stale order from OrderManager: DRA_xxx_sell_ETHUSDT
[WARNING] ⚠️ Cleaned 1 stale orders for ETHUSDT that were filled but missed by WebSocket

# SUCCESS: Trading recovery
[INFO] 🌅 First buy order of the day allowed for ETHUSDT
```

**Prevention Measures:**
- Enhanced logging shows API call success/failure with attempt numbers
- Validation runs every 60 seconds instead of 180 seconds
- Force validation before critical buy decisions ensures fresh state
- Retry logic with exponential backoff handles temporary API issues
- Proper error handling distinguishes between different failure types

**Critical Learning:**
- Never assume periodic validation is working without explicit verification
- Always test attribute references, especially in error scenarios
- WebSocket reliability requires robust HTTP API fallback mechanisms  
- Validation frequency must match typical fill detection latency requirements

**Fixed in commit:** 3d82fd5 - Fix critical order validation issues: attribute error and stale order detection

### Problem: WebSocket Authentication False Alarms - CRITICAL DEBUGGING
**Symptoms:**
- Persistent reports of "31002: Signature Incorrect" errors
- Assumption that WebSocket authentication is failing
- Multiple attempts to "fix" working authentication code
- Time wasted debugging non-existent authentication issues

**Root Cause:**
**The authentication was never actually broken!** This was a case of:
1. **Misinterpreted error sources**: Errors may have been from different API calls, not WebSocket auth
2. **Assumption without verification**: Jumping to conclusion that WebSocket auth was failing
3. **No debug logging**: Lack of visibility into actual credential loading and usage
4. **Previous working commits ignored**: Not checking recent commits where auth worked

**Critical Learning:**
```
❌ WRONG: "We're getting auth errors → WebSocket auth must be broken"
✅ RIGHT: "Let's add debug logging to see what credentials are actually being used"
```

**Debug Solution Applied:**
```python
# Added to src/exchange/auth.py:121-132
from src.utils.logger import get_logger
logger = get_logger(__name__)
logger.info(f"🔐 WebSocket Auth - Access ID: {self.access_id}")
logger.info(f"🔐 WebSocket Auth - Secret Key (first 4 chars): {self.secret_key[:4]}****")
logger.info(f"🔐 WebSocket Auth - Timestamp: {timestamp}")
logger.info(f"🔐 WebSocket Auth - Generated signature (first 8 chars): {signature[:8]}****")
```

**Debug Results Revealed Truth:**
```
[INFO] 🔐 WebSocket Auth - Access ID: 377EB9E769AB4D0AAC13224A54B37946
[INFO] 🔐 WebSocket Auth - Secret Key (first 4 chars): 83AD****
[INFO] 🔐 WebSocket Auth - Generated signature (first 8 chars): a1b2c3d4****
```

**Verification Confirmed:**
- ✅ Correct credentials loaded from `.env.eth`
- ✅ WebSocket authentication working normally
- ✅ No actual "31002: Signature Incorrect" errors in WebSocket flow
- ✅ Bot functioning with proper authentication all along

**Prevention Protocol:**
1. **ALWAYS add debug logging before assuming auth failure**
2. **Check recent working commits first** (like commit 5a25755 mentioned)
3. **Isolate error sources** - distinguish WebSocket auth from HTTP API signature errors  
4. **Verify actual credential loading** instead of assuming credential problems
5. **Review TROUBLESHOOTING.md patterns** before creating new "fixes"

**Debug Logging Template for Future Auth Issues:**
```python
# Add to any auth-related debugging
logger.info(f"🔐 Auth Debug - Access ID: {self.access_id}")
logger.info(f"🔐 Auth Debug - Secret Key (first 4 chars): {self.secret_key[:4]}****")
logger.info(f"🔐 Auth Debug - Timestamp: {timestamp}")
logger.info(f"🔐 Auth Debug - Generated signature (first 8 chars): {signature[:8]}****")
logger.info(f"🔐 Auth Debug - Request method: {method}")
logger.info(f"🔐 Auth Debug - Request path: {path}")
```

**Time Cost of This False Alarm:**
- ~2 hours spent "debugging" working authentication
- Unnecessary code changes and documentation updates
- Deployment delays while chasing non-existent auth issues
- Could have been avoided with 5 minutes of debug logging

**Fixed in commit:** [Current commit] - Document WebSocket authentication false alarm and debug protocol

### Problem: API Error 3007 - Funding Fee Settlement Period
**Symptoms:**
```
{"code": 3007, "message": "Service is not available during funding fee settlement"}
```

**Root Cause:**
CoinEx temporarily restricts order placement during funding fee calculation periods (every 8 hours). This is a normal exchange operation, not a code bug.

**Solution:**
Added graceful handling with automatic retry logic:
```python
# In order_manager.py
except Exception as e:
    # Handle funding fee settlement period gracefully
    if "3007" in str(e) or "funding fee settlement" in str(e).lower():
        logger.warning(f"Order placement delayed due to funding fee settlement: {client_id}")
        logger.info("This is a temporary exchange restriction - will retry in next cycle")
        # Keep client_id reserved for retry
        return None
    else:
        logger.error(f"Failed to place order {client_id}: {e}")
        self.used_client_ids.discard(client_id)
        return None
```

**Timing:**
- Funding fee settlement occurs every 8 hours (00:00, 08:00, 16:00 UTC)
- Restriction period lasts 1-2 minutes
- Bot automatically retries in next trading cycle

**Verification:**
- Bot logs show "Order placement delayed due to funding fee settlement"
- Orders are successfully placed after settlement period ends
- No order duplication or bot crashes

### Problem: Position Detection Failure - Critical Trading Logic Bug
**Symptoms:**
```
ERROR | Failed to sync positions with exchange: 'list' object has no attribute 'get'
```
- Bot places multiple buy orders per day despite having existing positions
- Position balance checks always show no position exists
- Trading logic Phase 2 protection fails

**Root Cause:**
Position manager expects CoinEx API response to have `'items'` key, but CoinEx actually returns `'data'` key containing the positions list.

**Affected Code:**
```python
# BEFORE (broken)
exchange_positions = response if isinstance(response, list) else response.get('items', [])

# AFTER (fixed)  
exchange_positions = response if isinstance(response, list) else response.get('data', [])
```

**CoinEx API Response Structure:**
```json
{
  "code": 0,
  "message": "OK",
  "data": [
    {"market": "ETHUSDT", "open_interest": "0.001", ...}
  ]
}
```

**Impact:**
- **CRITICAL**: Bot could place multiple buy orders per day (violating strategy)
- Position-sell balance calculations always show no position
- Missing sell order placement logic never triggers
- Reliance on Phase 1 (buy order detection) as only protection

**Fix Applied:**
Changed position manager to use `'data'` key for CoinEx API response parsing.

**Verification:**
- Position sync logs show successful position loading
- Phase 2 protection properly detects existing positions  
- Bot places sell orders for existing positions instead of new buy orders
- No more `'list' object has no attribute 'get'` errors

### Problem: Order Placement Hangs
**Symptoms:**
- `place_order()` calls hang indefinitely
- No response from API after long wait

**Root Cause:**
- Network connectivity issues
- API endpoint problems
- Authentication header issues

**Solution:**
1. Check network connectivity
2. Verify authentication headers are correct
3. Use request timeout settings
4. Add detailed logging to identify where hanging occurs

### Problem: Validation Errors
**Symptoms:**
- "Invalid amount" or "Invalid price" errors
- Orders rejected due to parameter format

**Solution:**
```python
# Ensure proper number formatting
amount = f"{float(amount):.8f}".rstrip('0').rstrip('.')
price = f"{float(price):.2f}"

# Validate ranges
if float(amount) <= 0:
    raise ValueError("Amount must be positive")
```

## Process Lock Issues

### Problem: Multiple Bot Instances Running
**Symptoms:**
- Database conflicts
- Rate limiting exceeded
- Inconsistent order states

**Root Cause:**
Multiple bot instances running simultaneously without coordination.

**Solution:**
Use process lock to ensure single instance:
```python
from process_lock import ProcessLock

# At bot startup
lock = ProcessLock('trading_bot')
if not lock.acquire():
    logger.error("Another trading bot instance is already running")
    exit(1)

# Ensure cleanup on exit
try:
    # Bot main logic
    pass
finally:
    lock.release()
```

## Authentication Problems

### Problem: Invalid API Credentials
**Symptoms:**
- All authenticated endpoints fail
- "Access key not found" errors

**Solution:**
1. Verify `.env` file has correct credentials:
   ```
   COINEX_ACCESS_ID=your_access_id
   COINEX_SECRET_KEY=your_secret_key
   ```
2. Check credentials have futures trading permissions
3. Verify API key is not expired

### Problem: Time Synchronization Issues
**Symptoms:**
- Intermittent authentication failures
- "Timestamp expired" errors

**Solution:**
Ensure system time is synchronized:
```bash
# On Linux/Mac
sudo ntpdate -s time.nist.gov

# Check time difference
curl -s http://worldtimeapi.org/api/timezone/UTC
```

## Rate Limiting Issues

### Problem: Rate Limit Exceeded
**Symptoms:**
- HTTP 429 errors
- API responses with rate limit messages

**Solution:**
Built-in rate limiter handles this automatically:
```python
# Rate limiter waits automatically
self.rate_limiter.wait_if_needed()

# Configure limits if needed
rate_limiter = RateLimiter(max_requests=10, window_seconds=10)
```

## Testing and Debugging

### Problem: Testing with Real Orders
**Risk:** Using real money for testing

**Solution:**
Use provided test scripts with small amounts:
```bash
# Test with small amounts (~$3-5)
python3 place_test_order.py ETHUSDT 0.001

# Monitor in real-time
python3 test_order_pairing.py ETHUSDT

# Clean up test orders
python3 cancel_test_order.py --all
```

### Problem: Debugging WebSocket Issues
**Solution:**
```bash
# Check WebSocket status
python3 check_websocket_status.py

# Test authentication separately
python3 test_websocket_connection.py

# Enable debug logging
export LOG_LEVEL=DEBUG
```

### Problem: Verifying Fixes Work
**Procedure:**
1. Create minimal test case reproducing the issue
2. Apply fix
3. Test with small amounts/safe parameters
4. Verify logs show expected behavior
5. Test edge cases (disconnections, partial fills)

## Common Error Patterns

### Pattern 1: Signature + Optional Parameters
**Error:** "Signature Incorrect" on authenticated endpoints
**Fix:** Remove optional parameters, use client-side filtering

### Pattern 2: WebSocket + Binary Data  
**Error:** UTF-8 decode failures
**Fix:** Add gzip decompression support

### Pattern 3: Multiple Instances + Database
**Error:** Database locks, inconsistent state
**Fix:** Implement process locking

### Pattern 4: Parameter Format + API Requirements
**Error:** "Invalid argument" in requests  
**Fix:** Check API documentation for exact parameter names/formats

## Emergency Procedures

### If Bot Gets Stuck with Open Orders:
1. **Stop bot immediately:** Kill process or Ctrl+C
2. **Check open orders:** `python3 cancel_test_order.py --list`
3. **Cancel if needed:** `python3 cancel_test_order.py --all`
4. **Check positions:** Verify no unintended short positions
5. **Review logs:** Identify what caused the issue

### If WebSocket Stops Working:
1. **Test connection:** `python3 check_websocket_status.py`
2. **Check credentials:** Verify API keys still valid
3. **Restart connection:** Kill and restart bot
4. **Check network:** Firewall/proxy issues

### If Signature Errors Appear:
1. **Check recent changes:** What endpoints were modified?
2. **Look for optional parameters:** Market, limit, page parameters
3. **Apply proven fix:** Remove optional parameters
4. **Test with minimal case:** Single endpoint test
5. **Document new pattern:** Add to this guide

## Version History

- **v1.0** - Initial troubleshooting guide created
- **Recent fixes:**
  - c4d08f8: Fixed pending-orders signature issue  
  - 60d487a: Fixed positions signature issue
  - 8269c92: Fixed critical order tracking errors and API signature issues
  - WebSocket compression and authentication fixes
  - Process lock implementation
  - **2025-08-05**: Fixed critical credential displacement between bot accounts
    - External SSD environment files credential correction
    - Docker image credential separation and cleanup
    - Complete multi-bot deployment credential isolation

## Contributing

When encountering new issues:
1. Document the exact error message
2. Identify the root cause through debugging
3. Implement and test the fix
4. Add the pattern to this guide
5. Include git commit reference for tracking

### Problem: Container Naming Inconsistencies
**Symptoms:**
- Deploy scripts fail with "container not found" errors
- Manual deployments use different container names than automated scripts
- Container management commands fail due to name mismatches

**Root Cause:**
Different deployment methods may use different container naming conventions:
- Manual deployments might use: `ada-bot`, `eth-bot`
- Docker Compose uses: `bot-ada`, `bot-eth`
- Direct docker run might use: `ada`, `eth`

**Solution:**
1. **Check existing container names:**
   ```bash
   docker ps -a | grep -E "(ada|eth)"
   ```

2. **For manual deployment with simple names:**
   ```bash
   # Stop and remove old container
   docker stop ada-bot && docker rm ada-bot
   
   # Run with simple name
   docker run -d --name ada --env-file .env.ada --restart always trader-bot:latest python main.py ADAUSDT
   ```

3. **For automated deployment:**
   ```bash
   # Use the deploy.sh script which handles naming correctly
   ./deploy.sh ada update
   ```

**Best Practice:**
- Use the `deploy.sh` script for consistency
- If deploying manually, use simple names: `ada`, `eth`
- Document any custom container names in your deployment notes

### Problem: Deployment After Code Updates
**Symptoms:**
- Bot running old code despite repository updates
- Fixes not applied after pulling latest changes
- Container still showing old error messages

**Root Cause:**
Docker containers run from images that need to be rebuilt after code changes.

**Solution:**
```bash
# 1. Build new image with latest code
docker build --platform linux/amd64 -t trader-bot:latest .

# 2. Save for transfer to VM
docker save trader-bot:latest | gzip > trader-bot-latest-amd64.tar.gz

# 3. Transfer to VM
scp -i ssh-key-2025-07-27.key trader-bot-latest-amd64.tar.gz ubuntu@VM_IP:~

# 4. On VM: Load and deploy
docker load < trader-bot-latest-amd64.tar.gz
docker stop OLD_CONTAINER_NAME && docker rm OLD_CONTAINER_NAME
docker run -d --name ada --env-file .env.ada --restart always trader-bot:latest python main.py ADAUSDT
```

**Verification:**
- Check logs for absence of previously fixed errors
- Verify new features/fixes are working as expected
- Monitor initial startup for any configuration issues

### Problem: Critical Deployment Mistakes - LESSONS LEARNED
**Symptoms:**
- Container naming inconsistencies causing deploy script failures
- Wrong services stopped during single-bot deployments
- Bots running old code despite repository updates
- ADA bot placing multiple buy orders due to deployment errors
- Unnecessary service interruptions during independent deployments

**Root Cause Analysis:**
Multiple critical deployment mistakes made during recent ETH/ADA bot deployments:

**Mistake #1: Redundant Container Naming**
```bash
# ❌ WRONG: Used redundant naming
docker-compose.multi.yml:
  services:
    bot-eth:  # Redundant "bot-" prefix
    bot-ada:  # User feedback: "why are you using that naming? bot-eth, is redundant"

# ✅ CORRECT: Clean, simple naming
docker-compose.multi.yml:
  services:
    eth:  # Clean, direct naming
    ada:  # Easy to reference
```

**Mistake #2: Service Isolation Violation**
```bash
# ❌ WRONG: Stopped ETH bot when only deploying ADA
docker stop eth-bot  # Unnecessary interruption to working ETH bot
./deploy.sh ada update  # Should only affect ADA bot

# ✅ CORRECT: Independent service deployment
# ETH bot continues running while ADA is deployed
./deploy.sh ada update  # Only affects ADA bot
```

**Mistake #3: No Docker Image Build Before Deployment**
```bash
# ❌ WRONG: Deploy without building updated image
scp old-code.tar.gz vm:~/
docker run old-image  # Bot runs old code → places multiple buy orders

# ✅ CORRECT: Build → Save → Transfer → Load → Deploy
docker build --platform linux/amd64 -t trader-bot-final:amd64 .
docker save trader-bot-final:amd64 | gzip > trader-bot-final-amd64.tar.gz
scp trader-bot-final-amd64.tar.gz vm:~/
# On VM: docker load < trader-bot-final-amd64.tar.gz
```

**Mistake #4: Improper Multi-Bot Deployment Flow**
```bash
# ❌ WRONG: Reactive deployment (fix issues after deployment)
1. Deploy without proper testing
2. Bot places wrong orders
3. Emergency stop and fix
4. Multiple deployment attempts

# ✅ CORRECT: Proactive deployment workflow
1. Build and test Docker image locally
2. Verify credentials and configuration  
3. Deploy with proper service isolation
4. Monitor logs immediately after deployment
```

**Impact of These Mistakes:**
- **ADA Bot**: Placed multiple buy orders (violated trading strategy)
- **ETH Bot**: Unnecessary downtime during ADA deployment
- **Time Lost**: 2+ hours fixing deployment-caused issues
- **User Feedback**: "why are you making so many mistakes lately??? it is very dangerous"

**Corrected Deployment Best Practices:**

**1. Container Naming Standards:**
```yaml
# docker-compose.multi.yml
version: '3.8'
services:
  eth:  # Simple, clean names
    build: .
    environment:
      - DEFAULT_TRADING_MARKET=ETHUSDT
    env_file: .env.eth
    
  ada:  # No redundant prefixes
    build: .
    environment:
      - DEFAULT_TRADING_MARKET=ADAUSDT
    env_file: .env.ada
```

**2. Service Isolation Protocol:**
```bash
# Deploy single bot without affecting others
./deploy.sh ada update    # Only affects ADA service
./deploy.sh eth restart   # Only affects ETH service

# Verify isolation
docker ps | grep eth      # ETH should remain unaffected
docker logs ada          # Only ADA logs should show restart
```

**3. Mandatory Build-First Workflow:**
```bash
# ALWAYS build Docker image locally before deployment
echo "Building fresh Docker image..."
docker build --platform linux/amd64 -t trader-bot-final:amd64 .

echo "Saving image for VM transfer..."
docker save trader-bot-final:amd64 | gzip > trader-bot-final-amd64.tar.gz

echo "Transferring to VM..."
scp trader-bot-final-amd64.tar.gz vm:~/

echo "Loading and deploying on VM..."
ssh vm "docker load < trader-bot-final-amd64.tar.gz && docker-compose up -d service_name"
```

**4. Pre-Deployment Verification:**
```bash
# Before any deployment, verify:
1. Docker image builds successfully
2. Environment files have correct credentials
3. Target service name matches docker-compose.multi.yml
4. No unintended service dependencies
5. Backup of current working configuration
```

**Emergency Response Protocol:**
```bash
# If deployment causes issues:
1. IMMEDIATELY stop affected service
   docker stop service_name
   
2. Check logs for errors
   docker logs service_name
   
3. Restore previous working image if needed
   docker run previous_working_image
   
4. Analyze what went wrong before next attempt
   # Review build logs, configuration, credentials
```

**Prevention Checklist:**
- [ ] Build Docker image with latest code locally
- [ ] Test image functionality before deployment  
- [ ] Use clean, simple container names (eth, ada, not bot-eth)
- [ ] Deploy only the intended service (don't stop unrelated services)
- [ ] Monitor logs immediately after deployment
- [ ] Have rollback plan ready

**Fixed in commit:** [Current commit] - Document critical deployment mistakes and prevention protocols

### Future Investigation Required - Type Conversion Safety

**Task**: Audit safe_int() vs int() usage across entire codebase
- **Scope**: Find all int() calls and validate against CoinEx API specifications  
- **Focus**: Exchange order IDs, timestamps, amounts, and other numeric API parameters
- **Goal**: Ensure all CoinEx API responses use proper safe_int() for type safety
- **Files**: Especially src/exchange/*, but scan entire codebase
- **Reference**: Use `api_docs/coinex_api_schema.yaml` for proper type conversion requirements
- **Validation Tools**: Use `scripts/api_type_validator.py` to scan for unsafe patterns
- **Risk**: int() failures could cause bot crashes on malformed API responses

This guide should be the first place to check when debugging issues with the CoinEx trading bot.