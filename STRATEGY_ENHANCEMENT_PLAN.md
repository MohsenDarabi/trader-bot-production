# Hourly Strategy Enhancement Plan

## Executive Summary

This document outlines the enhancement plan for the hourly trading strategy, focusing on porting critical fixes from the production branch, adding environment variable configuration, and implementing simple JSON reporting with dual timezone support.

## Phase 1: Core Fixes (Immediate Priority)

### 1.1 Port Startup Bug Fix ✅ CRITICAL
**Issue**: Bot places multiple "startup" buy orders when filled orders disappear from pending orders
**Solution**: Port `_check_today_filled_buy_orders()` method from production branch
```python
def _check_today_filled_buy_orders(self, market: str) -> bool:
    """Check if any buy orders were filled today for the given market"""
    # Method 1: Check order tracker for filled buy orders
    # Method 2: Check recent transactions via API
```

**Modified Logic**:
```python
# OLD (buggy)
has_today_buy = len(fresh_today_buy_orders) > 0

# NEW (fixed) - Check both pending AND filled
has_today_buy_pending = len(fresh_today_buy_orders) > 0
has_today_buy_filled = self._check_today_filled_buy_orders(market)
has_today_buy = has_today_buy_pending or has_today_buy_filled
```

### 1.2 Port REST Fill Notification Fix ✅ CRITICAL
**Issue**: OrderManager doesn't notify OrderTracker when REST API discovers fills
**Solution**: Already ported to hourly worktree as TEMP fix
```python
# In _handle_discovered_fill() - Add notification after order removal
if self.order_tracker and order_side == OrderSide.BUY:
    fill = OrderFill(...)
    for handler in self.order_tracker.fill_handlers:
        handler(fill)
```

## Phase 2: Environment Configuration

### 2.1 Position Sizing Variables
Add to settings.py:
```python
# Position Sizing Configuration
POSITION_SIZE_PERCENT = float(os.getenv('POSITION_SIZE_PERCENT', '1.0'))  # 1% default for hourly
MAX_POSITION_SIZE_PERCENT = float(os.getenv('MAX_POSITION_SIZE_PERCENT', '2.5'))  # 2.5% max for hourly
MIN_POSITION_SIZE_PERCENT = float(os.getenv('MIN_POSITION_SIZE_PERCENT', '0.5'))  # 0.5% min for safety
```

### 2.2 Trading Strategy Variables
```python
# Trading Strategy Configuration
TRADING_INTERVAL = os.getenv('TRADING_INTERVAL', 'hourly')  # hourly vs daily
MIN_PROFIT_PERCENT = float(os.getenv('MIN_PROFIT_PERCENT', '0.8'))  # Default 0.8%
LEVERAGE = float(os.getenv('LEVERAGE', '2.0'))  # Default 2x leverage
```

## Phase 3: JSON Reporting System

### 3.1 Simple JSON Reports
Create reporting system that outputs to `reports/` directory:
```python
class SimpleJSONReporter:
    def __init__(self, report_dir="./reports"):
        self.report_dir = Path(report_dir)
        self.report_dir.mkdir(exist_ok=True)
    
    def generate_daily_summary(self, market: str):
        """Generate JSON report with UTC and Berlin timestamps"""
        berlin_tz = pytz.timezone('Europe/Berlin')
        utc_now = datetime.utcnow()
        berlin_now = utc_now.replace(tzinfo=pytz.UTC).astimezone(berlin_tz)
        
        report = {
            "market": market,
            "timestamp_utc": utc_now.isoformat(),
            "timestamp_berlin": berlin_now.isoformat(),
            "positions": self.get_positions_summary(),
            "orders": self.get_orders_summary(),
            "performance": self.get_performance_summary()
        }
        
        filename = f"{market}_{utc_now.strftime('%Y%m%d')}.json"
        self.save_report(report, filename)
```

### 3.2 Report Structure
```json
{
  "market": "ADAUSDT",
  "timestamp_utc": "2025-09-05T10:30:00Z",
  "timestamp_berlin": "2025-09-05T12:30:00+02:00",
  "positions": {
    "open_positions": 2,
    "total_value": 1543.21,
    "unrealized_pnl": 23.45
  },
  "orders": {
    "active_buy_orders": 1,
    "active_sell_orders": 3,
    "completed_today": 5
  },
  "performance": {
    "trades_today": 2,
    "profit_today": 12.34,
    "success_rate": 85.5
  }
}
```

### 3.3 File Locking for Thread Safety
```python
import fcntl
import json

def save_report_safely(self, report: dict, filename: str):
    """Save JSON report with file locking"""
    filepath = self.report_dir / filename
    with open(filepath, 'w') as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        json.dump(report, f, indent=2, default=str)
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
```

## Phase 4: Configuration Integration

### 4.1 Environment Files Example
**.env.hourly**:
```env
# Hourly Strategy Configuration
TRADING_INTERVAL=hourly
POSITION_SIZE_PERCENT=1.0
MAX_POSITION_SIZE_PERCENT=2.5
MIN_POSITION_SIZE_PERCENT=0.5
MIN_PROFIT_PERCENT=0.8
LEVERAGE=2.0

# JSON Reporting
ENABLE_JSON_REPORTS=true
REPORT_DIRECTORY=./reports
REPORT_TIMEZONE_BERLIN=true
```

### 4.2 Runtime Configuration Validation
```python
def validate_hourly_config():
    """Validate hourly-specific configuration"""
    errors = []
    
    if POSITION_SIZE_PERCENT > MAX_POSITION_SIZE_PERCENT:
        errors.append(f"POSITION_SIZE_PERCENT ({POSITION_SIZE_PERCENT}) exceeds MAX ({MAX_POSITION_SIZE_PERCENT})")
    
    if POSITION_SIZE_PERCENT < MIN_POSITION_SIZE_PERCENT:
        errors.append(f"POSITION_SIZE_PERCENT ({POSITION_SIZE_PERCENT}) below MIN ({MIN_POSITION_SIZE_PERCENT})")
    
    if TRADING_INTERVAL not in ['hourly', 'daily']:
        errors.append(f"Invalid TRADING_INTERVAL: {TRADING_INTERVAL}")
    
    return errors
```

## Implementation Priority

### Immediate (This Week)
1. ✅ Port startup bug fix to hourly strategy
2. ✅ Port REST fill notification fix (already done as TEMP)
3. ✅ Test both fixes with hourly intervals
4. ✅ Add environment variable support for position sizing

### Short Term (Next Week)
1. Implement JSON reporting system
2. Add dual timezone support (UTC + Berlin)
3. Create report file structure and locking
4. Test reporting with actual trading data

### Medium Term (Future)
1. Enhanced error reporting in JSON format
2. Performance metrics integration
3. MongoDB Atlas integration for historical data
4. API endpoints for report access

## Testing Strategy

### Unit Testing
- Test startup bug fix with hourly intervals
- Test position sizing with environment variables
- Test JSON report generation and file locking
- Test timezone conversion accuracy

### Integration Testing
- Run hourly strategy with new fixes for 24 hours
- Verify no duplicate startup buy orders
- Verify correct position sizing calculations
- Verify JSON reports are generated correctly

### Production Testing
- Deploy to test environment first
- Monitor for 48 hours before production deployment
- Compare performance with daily strategy
- Validate reporting accuracy

## Success Metrics

### Reliability
- Zero duplicate startup buy orders
- 100% REST fill notification success rate
- Position sizing within configured limits

### Reporting
- JSON reports generated every hour
- Dual timezone accuracy
- File locking prevents corruption
- Reports accessible without log diving

### Performance
- No degradation in trading performance
- Environment variables loaded correctly
- Configuration validation catches errors

## Risk Mitigation

### High Risk
1. **Startup Bug Reappears**: Comprehensive testing with edge cases
2. **Position Sizing Errors**: Strict validation and limits
3. **Report File Corruption**: File locking and backup strategy

### Medium Risk
1. **Environment Variable Conflicts**: Clear naming conventions
2. **JSON Serialization Issues**: Proper error handling
3. **Timezone Conversion Errors**: Use standard libraries

## Future Considerations

### Low Priority TODOs
1. Support for 15-minute, 30-minute, 4-hour intervals
2. MongoDB Atlas integration for long-term storage
3. Web dashboard for report visualization
4. Automated alerting based on report metrics
5. Cross-strategy performance comparison tools

---

*This plan focuses on simplicity and reliability. Each phase builds on the previous one, ensuring stable functionality before adding new features. The JSON reporting system provides immediate value for monitoring without log diving, while environment variables enable flexible configuration without code changes.*