# Trading Bot Deployment Best Practices

This guide documents the proven deployment workflow to prevent critical mistakes that can cause bots to place unintended orders or disrupt trading operations.

## Overview

**Critical Learning**: Recent deployments revealed several dangerous patterns that led to:
- ADA bot placing multiple buy orders in one day (violated trading strategy)
- Authentication false alarms wasting development time
- Service isolation violations causing unnecessary downtime
- Emergency stops and reactive fixes

This guide provides the corrected deployment workflow to prevent these issues.

## Pre-Deployment Requirements

### 1. Docker Image Build Verification

**ALWAYS build and test Docker image locally before deployment:**

```bash
# Build with latest code
docker build --platform linux/amd64 -t trader-bot-final:amd64 .

# Test image loads properly
docker run --rm trader-bot-final:amd64 python -c "print('Docker image OK')"

# Test bot imports work
docker run --rm --env-file .env.eth trader-bot-final:amd64 \
    python -c "from src.core.trading_bot import DailyRangeBot; print('Bot imports OK')"

# Save for VM deployment
docker save trader-bot-final:amd64 | gzip > trader-bot-final-amd64.tar.gz
```

**Why This Matters:**
- Prevents bots from running old code that may have bugs
- Catches import errors before deployment
- Ensures all fixes are included in deployed image

### 2. Credential Verification

**Verify environment files contain correct credentials for intended accounts:**

```bash
# Check ETH bot credentials
echo "ETH Bot Credentials:"
grep "COINEX_API_KEY=" .env.eth
grep "DEFAULT_TRADING_MARKET=" .env.eth

# Check ADA bot credentials  
echo "ADA Bot Credentials:"
grep "COINEX_API_KEY=" .env.ada
grep "DEFAULT_TRADING_MARKET=" .env.ada

# Verify they are different (avoid credential displacement)
diff <(grep COINEX_API_KEY .env.eth) <(grep COINEX_API_KEY .env.ada)
```

**Critical**: Different bots must use different CoinEx accounts to avoid cross-contamination.

### 3. Container Naming Verification

**Check docker-compose.multi.yml uses clean, simple service names:**

```yaml
# ✅ CORRECT naming pattern
version: '3.8'
services:
  eth:  # Clean, simple
    build: .
    env_file: .env.eth
    environment:
      - DEFAULT_TRADING_MARKET=ETHUSDT
    
  ada:  # Easy to reference
    build: .
    env_file: .env.ada
    environment:
      - DEFAULT_TRADING_MARKET=ADAUSDT

# ❌ WRONG - redundant prefixes  
services:
  bot-eth:  # Unnecessarily verbose
  bot-ada:  # Creates script confusion
```

## Deployment Workflow

### Standard Deployment Process

```bash
#!/bin/bash
# Complete deployment workflow

echo "=== STEP 1: PRE-DEPLOYMENT VERIFICATION ==="
# Build fresh image
docker build --platform linux/amd64 -t trader-bot-final:amd64 .

# Test locally
docker run --rm --env-file .env.eth trader-bot-final:amd64 \
    python -c "from src.core.trading_bot import DailyRangeBot; print('✅ Image verification passed')"

echo "=== STEP 2: PREPARE FOR VM TRANSFER ==="
# Save image
docker save trader-bot-final:amd64 | gzip > trader-bot-final-amd64.tar.gz

# Verify credentials
echo "ETH credentials:" && grep COINEX_API_KEY .env.eth
echo "ADA credentials:" && grep COINEX_API_KEY .env.ada

echo "=== STEP 3: DEPLOY TO VM ==="
# Transfer image
scp -i ./ssh-key-2025-07-27.key trader-bot-final-amd64.tar.gz ubuntu@VM_IP:~/

# Deploy specific service
./deploy.sh eth update  # Only affects ETH service

echo "=== STEP 4: VERIFICATION ==="
# Check deployment success
ssh -i ./ssh-key-2025-07-27.key ubuntu@VM_IP "docker logs eth | head -20"

# Clean up local files
rm trader-bot-final-amd64.tar.gz

echo "✅ Deployment completed successfully"
```

### Service Isolation Principles

**Deploy individual services without affecting others:**

```bash
# ✅ CORRECT: Deploy ADA without affecting ETH
./deploy.sh ada update
# Result: ETH continues running, only ADA restarts

# ✅ CORRECT: Deploy ETH without affecting ADA
./deploy.sh eth restart  
# Result: ADA continues running, only ETH restarts

# ❌ WRONG: Stop unrelated services
docker stop eth  # Don't do this when deploying ADA
./deploy.sh ada update
```

**Verification Commands:**
```bash
# Verify service isolation worked
docker ps | grep eth  # Should be unaffected if deploying ADA
docker ps | grep ada  # Should be unaffected if deploying ETH

# Check only intended service restarted
docker logs eth | grep "Started at"  # Timestamp should be old if deploying ADA
docker logs ada | grep "Started at"  # Timestamp should be recent if deploying ADA
```

## Authentication Troubleshooting

### Debug Authentication Issues Properly

**NEVER assume authentication is broken without verification:**

```python
# Add debug logging to src/exchange/auth.py
from src.utils.logger import get_logger
logger = get_logger(__name__)

def sign_websocket_message(self, timestamp=None):
    # Debug credential loading
    logger.info(f"🔐 Auth Debug - Access ID: {self.access_id}")
    logger.info(f"🔐 Auth Debug - Secret Key (first 4 chars): {self.secret_key[:4]}****")
    logger.info(f"🔐 Auth Debug - Timestamp: {timestamp}")
    
    # Generate signature
    signature = hmac.new(...)
    logger.info(f"🔐 Auth Debug - Generated signature (first 8 chars): {signature[:8]}****")
    
    return {...}
```

**What Debug Logging Revealed:**
```
[INFO] 🔐 Auth Debug - Access ID: 377EB9E769AB4D0AAC13224A54B37946
[INFO] 🔐 Auth Debug - Secret Key (first 4 chars): 83AD****
[INFO] 🔐 Auth Debug - Generated signature (first 8 chars): a1b2c3d4****
```

**Result**: Authentication was working correctly all along. The "problem" was a false alarm.

### Authentication Verification Steps

1. **Add debug logging** before assuming auth failure
2. **Check recent working commits** (e.g., commit 5a25755)
3. **Distinguish error sources** (WebSocket vs HTTP API)
4. **Verify credential loading** from environment files
5. **Test with minimal cases** before complex fixes

## Container Management

### Correct Container Commands

```bash
# Use clean service names from docker-compose.multi.yml
docker logs eth          # Not "bot-eth"
docker logs ada          # Not "bot-ada"
docker stop eth          # Clean, simple
docker restart ada       # Easy to remember

# Service-specific operations
docker-compose -f docker-compose.multi.yml up -d eth     # Start ETH only
docker-compose -f docker-compose.multi.yml stop ada     # Stop ADA only
docker-compose -f docker-compose.multi.yml restart eth  # Restart ETH only
```

### Resource Monitoring

```bash
# Check service status
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.RunningFor}}"

# Monitor resource usage
docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}"

# Check logs for errors
docker logs eth | grep -i error | tail -10
docker logs ada | grep -i error | tail -10
```

## Emergency Procedures

### If Deployment Causes Trading Issues

```bash
# 1. IMMEDIATE STOP of affected service
ssh ubuntu@VM_IP "docker stop eth"  # Replace with affected service

# 2. CHECK LOGS for errors
ssh ubuntu@VM_IP "docker logs eth | tail -50 | grep -E '(ERROR|buy order|sell order)'"

# 3. ASSESS IMPACT
# - Check if multiple buy orders were placed
# - Verify positions aren't duplicated  
# - Review order history on exchange

# 4. ROLLBACK if necessary
ssh ubuntu@VM_IP "docker-compose -f docker-compose.multi.yml up -d eth"

# 5. ANALYZE before next deployment attempt
# - Review build logs
# - Verify image contains latest fixes
# - Test locally with small amounts
```

### If Authentication Errors Occur

```bash
# DON'T immediately assume auth is broken
# FIRST: Add debug logging to verify credentials

# 1. Check what credentials are actually loading
docker exec eth cat /app/.env.eth | grep COINEX_API_KEY

# 2. Look for successful auth messages in logs
docker logs eth | grep -i "auth\|websocket\|connected"

# 3. Compare with working commits
git log --oneline | grep -E "(auth|websocket|signature)"

# 4. Test minimal case
docker exec eth python -c "from src.exchange.auth import CoinExAuth; auth = CoinExAuth(); print('Auth loads OK')"
```

## Validation Checklist

### Before Deployment
- [ ] Docker image built with latest code
- [ ] Image tested locally (imports work)  
- [ ] Environment files have correct, different credentials
- [ ] docker-compose.multi.yml uses clean service names (eth, ada)
- [ ] Target service identified (don't affect unrelated services)
- [ ] Rollback plan prepared

### During Deployment  
- [ ] Monitor deployment output for errors
- [ ] Check container starts successfully
- [ ] Verify logs show expected startup messages
- [ ] Confirm authentication succeeds
- [ ] Watch for any multiple order placements

### After Deployment
- [ ] Service running and responsive  
- [ ] Logs show normal trading behavior
- [ ] No duplicate orders placed
- [ ] Other services unaffected (if single-service deployment)
- [ ] Resource usage within normal limits

## Common Deployment Mistakes

### 1. Container Naming Issues
```bash
# ❌ WRONG
docker logs bot-eth    # Redundant prefix
./deploy.sh bot-ada    # Script confusion

# ✅ CORRECT  
docker logs eth        # Clean, simple
./deploy.sh ada        # Easy to use
```

### 2. Service Isolation Violations
```bash
# ❌ WRONG
docker stop eth && docker stop ada  # Stops both when deploying one
./deploy.sh ada update               # Affects both services unnecessarily

# ✅ CORRECT
./deploy.sh ada update  # Only affects ADA, ETH continues running
```

### 3. No Image Build Before Deployment
```bash  
# ❌ WRONG
scp old-code.tar.gz vm:~/     # Transfers old code
./deploy.sh eth update        # Uses outdated code → bugs

# ✅ CORRECT
docker build ... -t trader-bot-final:amd64 .  # Fresh build
docker save ... > trader-bot-final-amd64.tar.gz
scp trader-bot-final-amd64.tar.gz vm:~/       # Updated code
```

### 4. Authentication False Alarms
```bash
# ❌ WRONG
# Sees error message → "Auth must be broken" → Start fixing auth

# ✅ CORRECT  
# Sees error message → Add debug logging → Verify actual auth status
```

### 5. No Immediate Verification
```bash
# ❌ WRONG
./deploy.sh eth update
# Walk away without checking

# ✅ CORRECT
./deploy.sh eth update
docker logs -f eth | head -20  # Watch startup immediately
```

## Success Indicators

### Deployment Success Logs
```
[INFO] 📊 Daily Trading Signals for ETHUSDT: Buy=$4218.8675 | Sell=$4319.6225 | Range=$50.3775
[INFO] 🔐 WebSocket Auth - Access ID: 377EB9E...
[INFO] ✅ Authentication confirmed  
[INFO] 🌐 WebSocket connected successfully
[INFO] ⚡ Real-time order monitoring active
```

### Authentication Success Logs
```
[INFO] 🔐 WebSocket Auth - Access ID: 377EB9E769AB4D0AAC13224A54B37946
[INFO] 🔐 WebSocket Auth - Secret Key (first 4 chars): 83AD****
[INFO] 🔐 WebSocket Auth - Generated signature (first 8 chars): a1b2c3d4****
[INFO] ✅ Authentication confirmed
```

### Trading Success Logs
```
[INFO] 🌅 First buy order of the day allowed for ETHUSDT
[INFO] Buy order placed successfully: DRA_1754434324_buy_ETHUSDT -> 179595936933
[INFO] 💰 Balance updated: $164.90 → $147.13
[INFO] ✅ WebSocket detected order fill: Order 179595936933 filled
```

## Conclusion

Following these practices prevents the critical deployment mistakes that led to:
- Multiple buy orders per day (ADA bot incident)
- Unnecessary service downtime (ETH bot during ADA deployment)
- Authentication debugging false alarms
- Emergency reactive fixes

**Key Principles:**
1. **Always build fresh Docker images** before deployment
2. **Use clean service naming** (eth, ada - not bot-eth, bot-ada) 
3. **Deploy services independently** without affecting others
4. **Debug with logging** before assuming problems exist
5. **Verify immediately** after deployment
6. **Have rollback plans** ready for emergencies

These practices ensure safe, reliable deployments that maintain trading bot stability and prevent costly trading errors.