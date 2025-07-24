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

## Contributing

When encountering new issues:
1. Document the exact error message
2. Identify the root cause through debugging
3. Implement and test the fix
4. Add the pattern to this guide
5. Include git commit reference for tracking

This guide should be the first place to check when debugging issues with the CoinEx trading bot.