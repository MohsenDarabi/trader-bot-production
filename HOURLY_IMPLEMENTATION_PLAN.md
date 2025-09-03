# Hourly Strategy Implementation Plan

## Repository Status Analysis

### Main Source (feature/comprehensive-testing-framework)
- **Path**: `/Users/mohsendarabi/Desktop/workspace/trader-bot-production`
- **Branch**: `feature/comprehensive-testing-framework`  
- **Status**: 2 running bot instances, latest working code
- **Latest commits**:
  - `9576ef8` - MAJOR DOCKER OPTIMIZATION: 77% smaller images, 253x faster processing
  - `adaeae5` - RESTORE: API documentation and update type safety investigation
  - `6685908` - CRITICAL FIX: Enhanced order completion detection with API verification

### Hourly Worktree (feature/hourly-strategy)  
- **Path**: `/Users/mohsendarabi/Desktop/workspace/trader-bot-hourly`
- **Branch**: `feature/hourly-strategy`
- **Status**: Has most critical fixes but missing recent optimizations
- **Latest commits**:
  - `8e84211` - VERSION MARKER: Stable Hourly Trading Bot - Critical Bug Fixes Applied
  - `b30026c` - BACKPORT: Apply critical bug fixes to hourly strategy while preserving 1% positions

### Gap Analysis
**✅ Already in Hourly Branch:**
- Critical bug fixes (commit b30026c - enhanced order completion detection)
- API documentation restoration
- Pre-commit hooks and type checking
- Hourly strategy infrastructure (`generate_hourly_signal`, timeframe logic)
- Docker optimization files (Dockerfile.optimized exists)

**❌ Missing from Hourly Branch:**
- Latest Docker optimization commit (9576ef8)
- Requirements-prod.txt updates (pandas removal)
- Deploy.sh Docker optimization support
- Alpine Linux migration changes

**🔍 Hourly Implementation Status (UPDATED ANALYSIS):**
- ❌ `is_new_trading_hour()` method missing from market_data.py (called at line 1543)
- ✅ Timeframe detection exists in trading logic (lines 1539-1544)
- ❌ Signal generation still hardcoded to daily (line 817 - needs timeframe check)

## Refined Implementation Plan

### Phase 1: Sync Latest Changes (Priority: HIGH)
1. **Port Docker optimizations from main source**
   - Copy requirements-prod.txt (pandas removal)
   - Update Dockerfile.optimized with Alpine changes
   - Update deploy.sh with Docker optimization support
   - Ensure 77% size reduction and 253x speed improvements

2. **Verify critical fixes are current**
   - Confirm API verification logic exists
   - Confirm order completion detection works
   - Test that phantoms buy order cleanup works

### Phase 2: Complete Missing Hourly Implementation (Priority: HIGH)

**ANALYSIS RESULTS:**
- ✅ Hourly timeframe logic exists (lines 1539-1544) - calls `is_new_trading_hour(market)`
- ✅ `generate_hourly_signal()` method exists in strategy.py  
- ❌ `is_new_trading_hour()` method missing from market_data.py (causing errors)
- ❌ Signal generation hardcoded to daily (line 817 in `_check_and_generate_signals`)

3. **Add missing `is_new_trading_hour` method** (CRITICAL - CALLED BUT MISSING)
   - File: `/Users/mohsendarabi/Desktop/workspace/trader-bot-hourly/src/data/market_data.py`
   - Logic: Return False during funding settlements (00:00-00:01, 08:00-08:01, 16:00-16:01)
   - Return True for all other times (allows trading 23h 57m per day)

4. **Fix `_check_and_generate_signals` timeframe logic** (line 817)
   - Currently hardcoded: `signal = self.strategy.generate_daily_signal(market)`  
   - Change to conditional based on `TRADING_TIMEFRAME`
   - Call `generate_hourly_signal` for hourly, `generate_daily_signal` for daily

5. **Add hour detection to execute_trading_cycle** (OPTIONAL - MAY ALREADY WORK)
   - Check if hour detection already works via existing timeframe logic
   - May only need logging improvements for hourly vs daily detection

6. **Rename misleading method**
   - `_check_daily_buy_status` → `_check_period_buy_status`

### Phase 3: Testing and Validation
7. **Test both strategies**
   - `TRADING_TIMEFRAME=daily` (regression test)
   - `TRADING_TIMEFRAME=hourly` (new functionality)
   - Verify funding fee avoidance (00:00, 08:00, 16:00 first minute)

8. **Commit and document changes**

## Key Corrections from Initial Analysis

### Funding Fee Logic (CORRECTED)
- **Only 3 settlement periods per day**: 00:00-00:01, 08:00-08:01, 16:00-16:01
- **Trading hours per day**: 24 hours (not 21!) - only skips 3 minutes total
- **Settlement duration**: Maximum 60 seconds, then normal trading resumes

### Existing Infrastructure (CONFIRMED)
- ✅ `generate_hourly_signal()` exists (line 241 in strategy.py)
- ✅ `TRADING_TIMEFRAME` config exists (settings.py line 85)  
- ✅ Timeframe conditionals exist (trading_bot.py lines 1539-1544, 1496-1501)
- ✅ `_last_reset_period` handles both daily/hourly (lines 1466-1467, 1541-1542)
- ❌ `is_new_trading_hour()` method missing (needs implementation)

## Implementation Checklist
- [ ] **Phase 1**: Port Docker optimizations from main source
- [ ] **Phase 1**: Verify critical fixes are current
- [ ] **Phase 2**: Add `is_new_trading_hour()` method to market_data.py
- [ ] **Phase 2**: Add hour detection to `execute_trading_cycle`
- [ ] **Phase 2**: Complete timeframe-aware signal generation
- [ ] **Phase 2**: Rename `_check_daily_buy_status` → `_check_period_buy_status`
- [ ] **Phase 3**: Test daily strategy (no regression)
- [ ] **Phase 3**: Test hourly strategy (new functionality)
- [ ] **Phase 3**: Commit and push changes

## Current Implementation Status (Latest Update)

### ✅ Completed Tasks
1. **Fixed critical signal variable scope issue** in `_check_and_generate_signals()` at line 808
   - Added `signal = None` initialization to prevent UnboundLocalError
   - This was the exact bug causing VSCode crash - now resolved

2. **Completed hourly support in `_check_daily_buy_status` method**
   - Already fully implemented with timeframe-aware period checking
   - Handles both hourly and daily order management correctly

3. **Ported Docker optimizations from main source** to deploy.sh
   - Updated usage function to include `[opt]` parameter
   - Added optimization validation in `validate_inputs()`
   - Updated `build_image_locally()` to support Dockerfile.optimized
   - Updated `execute_deployment()` and `restart_containers()` for optimization support
   - Files already present: requirements-prod.txt, Dockerfile.optimized

### 🔧 Additional Verification Needed
4. **Check trading_bot.py path reference**
   - Verify that "/Users/mohsendarabi/Desktop/workspace/trader-bot-hourly" path is properly referenced in code
   - Ensure no hardcoded absolute paths that could break deployment

### ⏳ Remaining Tasks
5. Test hourly strategy functionality
6. Test daily strategy regression  
7. Commit and document changes

## Expected Outcome
A unified codebase that:
- Handles both daily and hourly strategies via `TRADING_TIMEFRAME` environment variable
- Maintains all recent bug fixes and optimizations from main source
- Trades 24 hours per day, avoiding only 3 minutes of funding settlements
- Generates signals once per day (daily) or once per hour (hourly)
- Preserves existing daily bot functionality while adding hourly capability