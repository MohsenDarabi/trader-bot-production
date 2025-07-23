# CoinEx Trading Bot - Test Setup Instructions

## Overview
The test runner validates all bot functionality using real API calls with completely safe, non-executable orders.

## 🔐 Safety Features
- **Test orders placed 50% below market price** (won't execute)
- **All orders marked as hidden** (`is_hide=True`)
- **Minimum order sizes only**
- **Immediate cancellation** after testing
- **No risk to capital**

## 📋 Prerequisites

### 1. CoinEx Account Setup
1. Create a CoinEx account at https://www.coinex.com/
2. Complete KYC verification for futures trading
3. Deposit at least $100 USDT for testing (minimum $50 required)

### 2. API Credentials
1. Go to Account → API Management
2. Create a new API key with these permissions:
   - ✅ **Futures Trading** (required)
   - ✅ **Futures Account** (required)  
   - ❌ Withdrawal (not needed)
3. Save your `access_id` and `secret_key`

### 3. Environment Configuration
1. Copy the example environment file:
   ```bash
   cp .env.example .env
   ```

2. Edit `.env` with your credentials:
   ```bash
   # CoinEx API Credentials
   COINEX_ACCESS_ID=your_access_id_here
   COINEX_SECRET_KEY=your_secret_key_here
   
   # Trading Configuration
   TRADING_MODE=TEST
   TEST_MODE_START_DATE=2024-01-20T00:00:00
   
   # Database
   DATABASE_PATH=./storage/bot_state.db
   
   # Logging
   LOG_LEVEL=INFO
   ```

### 4. Install Dependencies
```bash
pip install -r requirements.txt
```

## 🚀 Running the Test

### Command
```bash
python test_runner.py
```

### What the Test Does

#### Phase 1: System Validation
- ✅ Validates configuration and API credentials
- ✅ Initializes all bot components
- ✅ Tests API connectivity (public and authenticated)
- ✅ Fetches account balance and information
- ✅ Tests startup recovery system

#### Phase 2: Market Selection & Analysis  
- ✅ Selects trading asset (BTCUSDT recommended)
- ✅ Fetches real market data (OHLC, current price)
- ✅ Calculates daily range strategy signals
- ✅ Validates profitability calculations
- ✅ Tests position sizing logic

#### Phase 3: Safe Order Testing
- ✅ Places test buy order at 50% below market price
- ✅ Order marked as hidden (`is_hide=True`)
- ✅ **USER VERIFICATION**: You confirm order appears in CoinEx
- ✅ Cancels test order immediately
- ✅ **USER VERIFICATION**: You confirm order is removed

#### Phase 4: State Management
- ✅ Tests database operations
- ✅ Tests state persistence 
- ✅ Validates recovery system

## 📱 User Interaction Points

The test will pause and ask you to:

1. **Confirm test order is visible** in your CoinEx account:
   - Go to CoinEx → Futures → Open Orders
   - Look for order with client ID `DRA_xxxxx_buy_BTCUSDT`
   - Order should be marked as "Open" but won't execute (price too low)

2. **Confirm test order is cancelled**:
   - Check that the order is no longer in Open Orders
   - Order should disappear after cancellation

## 🎯 Expected Results

### ✅ All Tests Pass
```
🎉 ALL TESTS PASSED
Results: 17/17 tests passed

🚀 NEXT STEPS:
✅ All systems validated successfully  
✅ Bot is ready for careful live deployment
✅ Consider starting with test mode (minimum orders)
✅ Monitor closely for first few trades
```

### ❌ If Tests Fail
Common issues and solutions:

#### API Authentication Errors
- Double-check your `access_id` and `secret_key`
- Ensure API has futures trading permissions
- Check if API key is restricted by IP address

#### Insufficient Balance
- Ensure at least $50 USDT in futures account
- Transfer funds from spot to futures if needed

#### Network/Connection Issues
- Check internet connection stability
- Try running test again (temporary issues are common)

#### Order Placement Failures
- Verify futures trading is enabled on your account
- Check if you have any open positions that might affect balance

## 🔧 Test Configuration

### Market Selection
- Default: BTCUSDT (recommended for testing)
- Alternative: Any major USDT futures pair
- The test will show available markets with analysis

### Order Safety
- Test orders placed at **50% below current market price**
- Example: If BTC is $40,000, test order at $20,000
- **Impossible to execute accidentally**

### Position Sizing
- Uses minimum order size for the selected market
- Respects exchange minimum requirements
- Test mode enabled (even smaller positions)

## 📊 Sample Test Output

```
🚀 Phase 1: System Validation
────────────────────────────────────────────────────────
✅ Validate Configuration
✅ Initialize Components  
✅ Test API Connectivity
✅ Fetch Account Information
✅ Test Startup Recovery

🚀 Phase 2: Market Selection & Analysis
────────────────────────────────────────────────────────
✅ Select Trading Asset
✅ Fetch Market Data
✅ Calculate Strategy Signals
✅ Validate Profitability
✅ Calculate Position Sizing

🚀 Phase 3: Safe Order Testing  
────────────────────────────────────────────────────────
🔒 PLACING SAFE TEST ORDER:
Market: BTCUSDT
Current Price: $43,250.00
Test Order Price: $21,625.00 (50% below market)
Quantity: 0.00116 BTC
Hidden: Yes (is_hide=True)
⚠️ This order will NOT execute due to low price

✅ Place Safe Test Order
✅ Verify Order Status  
✅ Cancel Test Order
✅ Verify Order Removal
```

## ⚠️ Important Notes

1. **No Real Trading Risk**: Orders are placed far below market price and cancelled immediately
2. **Hidden Orders**: All orders use `is_hide=True` so they don't appear in public order book
3. **Minimal Fees**: Only network fees for order placement/cancellation (usually < $0.01)
4. **Account Verification**: You'll personally verify orders appear and disappear in CoinEx
5. **State Persistence**: Test creates database files in `storage/` directory

## 🆘 Getting Help

If you encounter issues:

1. **Check logs**: Look in `logs/trading_bot.log` for detailed error messages
2. **Verify setup**: Double-check all prerequisites above
3. **API permissions**: Ensure futures trading is enabled
4. **Balance**: Confirm sufficient USDT in futures account
5. **Network**: Test on stable internet connection

## ✅ Ready for Production

After successful testing, the bot is ready for careful live deployment with:
- All API integrations verified
- Order management tested and working
- State persistence validated
- Hidden order functionality confirmed
- Profitability calculations accurate

**Next step**: Run the main bot with test mode enabled for 5 days of minimum orders before switching to full position sizing.