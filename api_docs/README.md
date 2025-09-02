# 📚 API Documentation & Type Validation

This directory contains API response documentation and validation tools to prevent type-related bugs in the trading bot.

## 🎯 Purpose

The CoinEx API returns many numeric values as **strings**, not numbers. This can cause:
- `TypeError` when doing math operations on string values
- `ValueError` when converting invalid strings
- Silent bugs when comparisons fail

**Example Bug:**
```python
# ❌ WRONG - Will crash!
if quantity < market_info.get('min_amount'):  # Comparing float to string
    ...

# ✅ CORRECT
if quantity < safe_float(market_info.get('min_amount', 0)):
    ...
```

## 📁 Structure

```
api_docs/
├── coinex_api_schema.yaml    # API response field documentation
├── README.md                  # This file
└── examples/                  # Example responses (if needed)
```

## 🔧 How It Works

### 1. API Schema (`coinex_api_schema.yaml`)
Documents each API endpoint's response fields:
- Field name
- Actual type returned by API (usually string)
- Required conversion type (float, int, etc.)
- Validation patterns to check proper usage

### 2. Validation Scripts
Located in `scripts/`:

#### `api_type_validator.py`
- Checks that all string fields are converted with `safe_float()`
- Detects dangerous patterns like direct `float()` conversion
- Finds math operations on unconverted strings

#### `scan_api_usage.py`
- Scans codebase to find all API field usages
- Identifies undocumented fields
- Reports dangerous usage patterns

#### `dangerous_code_detector.py`
- Finds TODO comments and mock code
- Detects placeholder values
- Identifies empty exception handlers
- Checks for debug code in production

## 🚀 Usage

### Manual Checking
```bash
# Check API type conversions
make check-api-types

# Scan API usage patterns
make scan-api-usage

# Check for dangerous code
make check-dangerous

# Full deployment check
make deploy-check
```

### Automatic Checking (Pre-commit)
These checks run automatically before every commit:
1. API type validation
2. Dangerous code detection
3. Import checking
4. Syntax validation

### Adding New API Endpoints

When adding a new API endpoint, update `coinex_api_schema.yaml`:

```yaml
endpoints:
  your_new_endpoint:
    path: "/v2/futures/your-endpoint"
    method: "GET"
    response_fields:
      - name: "some_amount"
        type: "string"           # What API returns
        converts_to: "float"      # What we need
        notes: "ALWAYS use safe_float()"
```

## 🐛 Common Type Bugs to Avoid

### 1. Direct String Usage
```python
# ❌ BAD
price = data.get('last')
total = price * quantity  # TypeError!

# ✅ GOOD
price = safe_float(data.get('last', 0))
total = price * quantity
```

### 2. Direct Type Conversion
```python
# ❌ BAD
amount = float(response.get('amount'))  # Can crash on None

# ✅ GOOD
amount = safe_float(response.get('amount', 0))
```

### 3. Comparisons with Strings
```python
# ❌ BAD
if position['profit_unreal'] > 0:  # Comparing string to int!

# ✅ GOOD
if safe_float(position.get('profit_unreal', 0)) > 0:
```

## 📊 API Fields Reference

### Always String (Need Conversion):
- `min_amount` - Minimum order size
- `tick_size` - Price precision
- `last` - Last traded price
- `open_interest` - Position size
- `profit_unreal` - Unrealized PnL
- `available` - Available balance
- `amount` - Order/trade amounts
- `price` - All prices
- `fee` - Trading fees

### Always Integer:
- `order_id` - Order IDs
- `deal_id` - Trade IDs
- `amount_precision` - Decimal places
- `price_precision` - Decimal places

### Always String (No Conversion):
- `market` - Market symbols
- `side` - buy/sell
- `status` - Order status
- `client_id` - Client order IDs

## 🔍 Validation Results

When validation fails, you'll see:
```
📁 src/exchange/coinex_client.py
   Line 123: Direct float() conversion
   Field: 'min_amount' (API returns: string)
   Code: min_val = float(data.get('min_amount'))
   Fix: Wrap with safe_float()
   Suggested: min_val = safe_float(data.get('min_amount', 0))
```

## 🛡️ Safety Rules

1. **NEVER** use `float()` or `int()` directly on API responses
2. **ALWAYS** use `safe_float()` or `safe_int()` helpers
3. **ALWAYS** provide default values in `.get()` calls
4. **TEST** with actual API responses, not assumptions

## 📈 Maintenance

### Weekly Tasks:
1. Run `make scan-api-usage` to find new undocumented fields
2. Update schema with any new fields found
3. Check for new dangerous patterns

### Before Each Deploy:
1. Run `make deploy-check`
2. Fix any critical issues
3. Document any new API endpoints

## 🚨 Emergency

If the bot crashes with type errors:
1. Check recent commits for missing `safe_float()` calls
2. Run `make check-api-types` to find violations
3. Check API responses - the exchange might have changed field types
4. Update this documentation with new findings

---

*Remember: The API returns strings for numbers. Always convert them safely!*