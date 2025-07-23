# WebSocket Buy-Sell Pairing System Testing Guide

This guide explains how to test the real-time buy-sell order pairing system to verify it prevents unintended short positions.

## Overview

The system automatically creates multiple sell orders when buy orders fill, ensuring exact amount matching to prevent short positions. This test verifies that functionality works in real-time.

## Testing Setup

### Prerequisites
- WebSocket connection working (verify with `python3 check_websocket_status.py`)
- API credentials configured in `.env` file
- Small amount of USDT in futures account for testing (~$10-20)

### Test Scripts Available

1. **`place_test_order.py`** - Places small buy orders for testing
2. **`cancel_test_order.py`** - Cancels test orders and cleanup
3. **`test_order_pairing.py`** - Monitors real-time pairing activity
4. **`check_websocket_status.py`** - Verifies WebSocket connection

## Testing Workflow

### Step 1: Verify WebSocket Connection
```bash
python3 check_websocket_status.py
```
Expected output: All ✓ checks should pass.

### Step 2: Start Real-Time Monitoring
Open **Terminal 1** and run:
```bash
python3 test_order_pairing.py ETHUSDT
```
This will:
- Initialize the pairing system
- Configure sell price levels (0.2%, 0.5%, 1%, 1.5%, 2%)
- Monitor for 60 seconds
- Show real-time fill detection and sell order creation

### Step 3: Place Test Buy Order
Open **Terminal 2** and run:
```bash
python3 place_test_order.py
```

Or specify market and amount:
```bash
python3 place_test_order.py ETHUSDT 0.001
```

The script will:
- Show current price and account balance
- Calculate buy price (0.05% below market for quick fill)
- Ask for confirmation before placing real order
- Display order details and next steps

### Step 4: Observe Real-Time Pairing
Watch **Terminal 1** for immediate activity:
```
[FILL DETECTED] BUY order filled:
  Order ID: 123456789
  Amount: 0.001 @ 3500.00
  Market: ETHUSDT
  → Automatic sell orders should be created soon...

[SELL ORDER CREATED] Order ID: 123456790
  Amount: 0.0002 @ 3507.00
[SELL ORDER CREATED] Order ID: 123456791
  Amount: 0.0002 @ 3517.50
[SELL ORDER CREATED] Order ID: 123456792
  Amount: 0.0002 @ 3535.00
[SELL ORDER CREATED] Order ID: 123456793
  Amount: 0.0002 @ 3552.50
[SELL ORDER CREATED] Order ID: 123456794
  Amount: 0.0002 @ 3570.00
```

### Step 5: Verify Order Pairing
The system should show:
- **Fill detection** within seconds of order execution
- **5 sell orders created** automatically
- **Exact amount matching**: Total sell amount = buy fill amount
- **Order pairing completion** confirmation

### Step 6: Cleanup (Optional)
List current orders:
```bash
python3 cancel_test_order.py --list
```

Cancel specific order:
```bash
python3 cancel_test_order.py <order_id>
```

Cancel all test orders:
```bash
python3 cancel_test_order.py --all
```

## Expected Results

### ✅ Successful Test Shows:
1. **Real-time fill detection** (< 1 second delay)
2. **Automatic sell order creation** (5 orders)
3. **Exact amount matching** (no risk of short positions)
4. **WebSocket connectivity** (no disconnections)
5. **Order tracking statistics** (all orders tracked)

### ❌ Issues to Watch For:
- **No fill detection**: WebSocket not receiving updates
- **No sell orders created**: Pairing manager not working
- **Wrong amounts**: Sell orders don't match buy fill exactly
- **WebSocket errors**: Connection or authentication issues
- **Missing orders**: Some sells not created

## Safety Guidelines

### Recommended Test Parameters:
- **Markets**: ETHUSDT, BTCUSDT (high liquidity)
- **Amounts**: 0.001 - 0.01 (small test sizes)
- **Total cost**: ~$3-40 per test

### Risk Management:
- ⚠️ **Real money**: Test orders use real funds
- ⚠️ **Multiple sells**: System creates 5 sell orders per buy
- ⚠️ **Market orders**: May fill at unexpected prices
- ⚠️ **Account balance**: Ensure sufficient USDT available

## Troubleshooting

### WebSocket Issues:
```bash
# Check connection
python3 check_websocket_status.py

# Check existing orders
python3 cancel_test_order.py --list
```

### No Fill Detection:
- Verify WebSocket subscriptions are active
- Check order actually filled (may take time)
- Ensure pairing rules configured for test market

### No Sell Orders Created:
- Check pairing manager initialization
- Verify minimum fill amount threshold
- Review error logs for failed order placement

### Partial Functionality:
- Some sells created but not all: Check account balance
- Wrong amounts: Review fill amount calculation
- Timing issues: WebSocket message delay

## Production Readiness Verification

The system is ready for live trading when tests show:
- ✅ Consistent real-time fill detection
- ✅ Reliable 5-sell-order creation 
- ✅ Exact amount matching (no rounding errors)
- ✅ WebSocket stability (no disconnections)
- ✅ Error handling works properly
- ✅ Emergency balance checks pass

## Advanced Testing

### Multi-Market Testing:
```bash
# Test different markets
python3 test_order_pairing.py BTCUSDT
python3 place_test_order.py BTCUSDT 0.0001
```

### Partial Fill Testing:
- Place larger order with limit price far from market
- Manually adjust price to cause partial fills
- Verify pairing works for each partial fill

### Stress Testing:
- Multiple simultaneous buy orders
- Rapid order placement/cancellation
- WebSocket disconnection/reconnection scenarios

This comprehensive testing ensures the buy-sell pairing system reliably prevents unintended short positions in your Daily Range Accumulation Strategy.