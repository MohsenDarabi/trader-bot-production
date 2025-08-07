# ADA Bot Deployment Instructions - Linear Architecture Fixes

## Files Ready for Deployment:
- `trader-bot-linear-fixes.tar.gz` (313MB) - Docker image with all fixes
- `.env.ada` - ADA bot environment configuration

## SSH Key:
- `/Users/mohsendarabi/Desktop/workspace/trader-bot-production/ssh-key-2025-07-27.key`

## Deployment Commands:

### 1. Upload files to server:
```bash
scp -i "/Users/mohsendarabi/Desktop/workspace/trader-bot-production/ssh-key-2025-07-27.key" \
    trader-bot-linear-fixes.tar.gz .env.ada \
    root@161.35.219.240:/root/
```

### 2. SSH into server:
```bash
ssh -i "/Users/mohsendarabi/Desktop/workspace/trader-bot-production/ssh-key-2025-07-27.key" root@161.35.219.240
```

### 3. Load Docker image:
```bash
docker load -i trader-bot-linear-fixes.tar.gz
```

### 4. Stop existing ADA container (if running):
```bash
docker stop ada-bot || true
docker rm ada-bot || true
```

### 5. Start new ADA bot with linear fixes:
```bash
docker run -d \
    --name ada-bot \
    --restart unless-stopped \
    --env-file .env.ada \
    trader-bot:linear-fixes
```

### 6. Monitor logs:
```bash
docker logs -f ada-bot
```

## Key Fixes Included:
1. ✅ Startup cleanup only cancels BUY orders (preserves all SELL orders)
2. ✅ Over-selling protection in OrderManager 
3. ✅ Fixed absolute vs multiplier price logic in OrderPairingManager
4. ✅ Enhanced WebSocket fill monitoring
5. ✅ 5-minute periodic fallback detection for missed fills
6. ✅ Linear sequence: cleanup → orphaned check → signal → buy → monitor

## Expected Behavior After Deployment:
- ADA bot should preserve existing sell orders during startup
- New buy orders that fill immediately should create corresponding sell orders
- No over-selling protection should prevent position imbalances
- Linear execution prevents race conditions

## Current Status:
❌ SSH connection timing out - server may be unreachable
📁 Docker image and configs ready for deployment when connectivity restored