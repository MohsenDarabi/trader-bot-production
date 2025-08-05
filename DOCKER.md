# 🐳 **CoinEx Trading Bot - Docker Guide**

This guide covers all Docker commands and workflows for running the CoinEx Trading Bot in containerized environments.

## **Prerequisites**
- Docker Desktop installed and running
- Project files in current directory
- `.env` file configured with API credentials

## **Quick Start**
```bash
# 1. Start the bot with ADAUSDT market
./docker-start.sh start ADAUSDT

# 2. View live logs (same as terminal output)
./docker-start.sh logs

# 3. Stop when done
./docker-start.sh stop
```

## **Available Commands**

### **🚀 Bot Lifecycle**
```bash
# Start bot (default: BTCUSDT market)
./docker-start.sh start

# Start with specific market
./docker-start.sh start ADAUSDT
./docker-start.sh start ETHUSDT

# Stop bot
./docker-start.sh stop

# Restart bot (default market)
./docker-start.sh restart

# Restart with specific market
./docker-start.sh restart ADAUSDT
```

### **📊 Monitoring & Logs**
```bash
# View live logs (follows like terminal)
./docker-start.sh logs

# Check container status
./docker-start.sh status
docker ps

# View recent logs (last 50 lines)
docker logs coinex-bot-production --tail 50

# Monitor resource usage
docker stats coinex-bot-production

# Check health status
docker inspect coinex-bot-production --format='{{.State.Health.Status}}'
```

### **🔧 Development & Debugging**
```bash
# Rebuild after code changes
./docker-start.sh rebuild

# Get interactive shell inside container
./docker-start.sh shell

# View container configuration
docker inspect coinex-bot-production

# Copy files from container
docker cp coinex-bot-production:/app/logs/trading_bot.log ./
```

### **🗂️ File & Data Management**
```bash
# View logs on host system (persisted)
tail -f logs/trading_bot.log
ls -la logs/

# Check log file sizes
du -sh logs/

# Clear old log files (if needed)
rm logs/*.zip

# Backup configuration
cp .env .env.backup
```

### **🛠️ Advanced Docker Commands**
```bash
# View all bot-related containers
docker ps -a --filter name=coinex

# Remove stopped containers
docker container prune -f

# View Docker images
docker images | grep trading-bot

# Clean up unused images
docker image prune -f

# Full system cleanup (careful!)
docker system prune -a -f
```

### **📋 Docker Compose Commands**
```bash
# Start with docker-compose
docker-compose up -d

# View logs
docker-compose logs -f

# Stop and remove
docker-compose down

# Rebuild and start
docker-compose up -d --build

# Scale to multiple instances (if configured)
docker-compose up -d --scale trading-bot=2
```

### **🔍 Troubleshooting**
```bash
# Check if container is running
docker ps | grep coinex-bot

# View detailed error logs
docker logs coinex-bot-production --tail 100 | grep ERROR

# Check container exit code
docker inspect coinex-bot-production --format='{{.State.ExitCode}}'

# View environment variables
docker exec coinex-bot-production env | grep COINEX

# Test container health
docker exec coinex-bot-production python -c "print('Container is healthy')"
```

### **📦 Building & Publishing**
```bash
# Build with tag
docker build -t coinex-bot:latest .

# Tag for registry
docker tag coinex-bot:latest username/coinex-bot:latest

# Push to registry (after docker login)
docker push username/coinex-bot:latest

# Pull from registry
docker pull username/coinex-bot:latest
```

### **🔒 Security Best Practices**
```bash
# Run without root privileges (configured in Dockerfile)
docker exec coinex-bot-production whoami  # Should show 'botuser'

# Check file permissions
docker exec coinex-bot-production ls -la /app

# View secrets (be careful!)
docker exec coinex-bot-production printenv | grep -E "API|SECRET"
```

### **📈 Performance Monitoring**
```bash
# Real-time stats
docker stats coinex-bot-production

# Check memory limit
docker inspect coinex-bot-production | grep -i memory

# View CPU usage
docker inspect coinex-bot-production | grep -i cpu

# Export metrics
docker stats --no-stream --format "table {{.Container}}\t{{.CPUPerc}}\t{{.MemUsage}}"
```

## **Environment Variables**
The bot uses these environment variables from `.env`:
- `COINEX_API_KEY` - Your CoinEx API key
- `COINEX_API_SECRET` - Your CoinEx API secret
- `TRADING_MODE` - TEST or NORMAL
- `DEFAULT_TRADING_MARKET` - Default market to trade

## **Volume Mounts**
- `./logs:/app/logs` - Persist log files on host
- `./.env:/app/.env:ro` - Mount environment file (read-only)

## **Network**
- No exposed ports (bot doesn't run a server)
- Outbound HTTPS to CoinEx API endpoints

## **Resource Limits**
Default limits in docker-compose.yml:
- Memory: 512MB
- CPU: 0.5 cores
- Restart policy: always

## **Tips**
1. Always use `./docker-start.sh` for consistent behavior
2. Logs are persisted in `./logs` directory
3. Container auto-restarts on failure
4. Use specific market parameter to avoid trading BTCUSDT
5. Monitor resource usage during first few hours

## **Common Issues**
- **Container exits immediately**: Check `.env` file and API credentials
- **Permission denied**: Run `chmod +x docker-start.sh`
- **Cannot connect to API**: Check internet connectivity and firewall
- **High memory usage**: Check for memory leaks in logs
- **Logs not persisting**: Ensure `./logs` directory exists

For more troubleshooting, see `TROUBLESHOOTING.md`

## **📦 New Build and Deploy Method (2025)**

### **🚀 Optimized Build Process**
This new method significantly reduces Docker image size by excluding unnecessary files and using efficient compression:

```bash
# 1. Build with improved .dockerignore (excludes .tar.gz files)
cd /Volumes/External_ssd_mohsen/docker-builds/trader-bot
docker build --platform linux/amd64 -t trader-bot-final:amd64 /path/to/trader-bot-production

# 2. Save with efficient compression (results in ~300MB vs 5GB)
docker save trader-bot-final:amd64 | gzip > trader-bot-final-amd64.tar.gz

# 3. Deploy to VM
scp -i ssh-key.key trader-bot-final-amd64.tar.gz user@vm:/tmp/
ssh -i ssh-key.key user@vm "sudo docker load < /tmp/trader-bot-final-amd64.tar.gz"
```

### **🔧 What Makes This Method Better**

**Updated .dockerignore excludes:**
```bash
# Archive files (CRITICAL for size reduction)
*.tar.gz
*.tar
*.zip

# Temporary and build files
tmp/
temp/
logs/
*.log
*.lock
```

**Key Improvements:**
- **Size Reduction**: 300MB vs 5GB (95% reduction)
- **Faster Transfers**: Optimized compression using `gzip`
- **Build Exclusions**: `.tar.gz` files are excluded from Docker context
- **External SSD**: Build location moved to avoid local disk space issues
- **Architecture Specific**: `--platform linux/amd64` for VM compatibility

### **📋 Complete Deployment Workflow**

```bash
# Step 1: Clean up old builds
docker system prune -f
rm -f /Volumes/External_ssd_mohsen/docker-builds/trader-bot/*.tar.gz

# Step 2: Build with latest code
cd /Volumes/External_ssd_mohsen/docker-builds/trader-bot
docker build --platform linux/amd64 -t trader-bot-final:amd64 /Users/path/trader-bot-production

# Step 3: Create compressed archive
docker save trader-bot-final:amd64 | gzip > trader-bot-final-amd64.tar.gz

# Step 4: Deploy to VM
./deploy-final.sh  # Uses the deployment script

# Step 5: Verify deployment
ssh -i ssh-key.key user@vm "sudo docker ps | grep eth-bot"
```

### **🛠️ Deployment Script Template**

```bash
#!/bin/bash
# Deploy script with latest method

VM_HOST="your-vm-ip"
VM_USER="ubuntu"
SSH_KEY="ssh-key.key"
DOCKER_IMAGE="/path/to/trader-bot-final-amd64.tar.gz"

# Upload and deploy
scp -i "$SSH_KEY" "$DOCKER_IMAGE" "$VM_USER@$VM_HOST:/tmp/"
ssh -i "$SSH_KEY" "$VM_USER@$VM_HOST" '
    sudo docker load < /tmp/trader-bot-final-amd64.tar.gz
    sudo docker stop eth-bot || true
    sudo docker rm eth-bot || true
    sudo docker run -d --name eth-bot --restart unless-stopped \
        --env-file .env.eth \
        --volume $(pwd)/logs:/app/logs \
        --volume $(pwd)/data:/app/data \
        trader-bot-final:amd64 python main.py ETHUSDT
'
```

### **⚡ Benefits Summary**
- **95% smaller** Docker images
- **Faster** build and transfer times  
- **No storage crashes** on local machine
- **Automated** exclusion of build artifacts
- **VM compatible** architecture builds
- **Efficient** gzip compression for transfers

## **🔐 Multi-Bot Credential Management**

### **Environment Variable Setup**
For managing multiple bots (ADA, ETH) with separate credentials, add to your `~/.zshrc`:

```bash
# CoinEx API Credentials - Separate accounts for each bot
export COINEX_API_KEY_ADA="your_ada_access_id"
export COINEX_API_SECRET_ADA="your_ada_secret_key"

export COINEX_API_KEY_ETH="your_eth_access_id" 
export COINEX_API_SECRET_ETH="your_eth_secret_key"
```

Then reload: `source ~/.zshrc`

### **Bot Environment Files**

**ADA Bot (`.env.ada`):**
```bash
# Use ADA-specific credentials from environment
COINEX_ACCESS_ID=${COINEX_API_KEY_ADA}
COINEX_SECRET_KEY=${COINEX_API_SECRET_ADA}
DEFAULT_TRADING_MARKET=ADAUSDT
DATABASE_PATH=./storage/bot_state_ada.db
```

**ETH Bot (`.env.eth`):**
```bash
# Use ETH-specific credentials from environment  
COINEX_ACCESS_ID=${COINEX_API_KEY_ETH}
COINEX_SECRET_KEY=${COINEX_API_SECRET_ETH}
DEFAULT_TRADING_MARKET=ETHUSDT
DATABASE_PATH=./storage/bot_state_eth.db
```

### **Deployment Commands**

```bash
# Deploy ADA bot
docker run -d --name ada-bot --env-file .env.ada trader-bot-final:amd64 python main.py ADAUSDT

# Deploy ETH bot  
docker run -d --name eth-bot --env-file .env.eth trader-bot-final:amd64 python main.py ETHUSDT

# Check both bots
docker ps | grep bot
docker logs ada-bot --tail 20
docker logs eth-bot --tail 20
```

### **Benefits of This Approach**
- **Clear Separation:** Each bot uses distinct credentials
- **Environment Management:** Credentials stored securely in shell environment
- **Easy Switching:** Change credentials by updating environment variables
- **Documentation:** Clear which credentials belong to which bot
- **Deployment Safety:** Prevents credential mix-ups during deployment

## **🚨 Critical: Credential Displacement Prevention**

### **Problem Overview**
Multi-bot deployments can suffer from credential displacement where bots connect to wrong accounts, causing cross-account contamination.

### **Root Causes & Prevention**

**1. External SSD Build Files**
```bash
# ❌ WRONG: Build location has displaced credentials
/Volumes/External_ssd_mohsen/docker-builds/trader-bot/.env.eth  # Contains ADA credentials

# ✅ CORRECT: Verify build location credentials match intended bot
cat /Volumes/External_ssd_mohsen/docker-builds/trader-bot/.env.eth
# Should show: COINEX_API_KEY="377EB9E769AB4D0AAC13224A54B37946"  # ETH credentials
```

**2. Docker Image Credential Contamination**
```bash
# ❌ WRONG: Building with contaminated credentials
docker build -t trader-bot:latest .  # May copy wrong .env files

# ✅ CORRECT: Clean build process
docker rmi $(docker images | grep trader-bot)  # Remove old images
docker system prune -f                         # Clean cached layers
docker build --platform linux/amd64 -t trader-bot-final:amd64 /source/path
```

**3. Deployment Verification**
```bash
# ✅ CORRECT: Verify credential separation after deployment
ssh user@vm "docker logs eth-bot --tail 20"
# Should show fresh orders, not existing orders from other account

# Example success indicators:
# ✅ "Buy order placed successfully: DRA_1754434324_buy_ETHUSDT -> 179595936933"
# ✅ "Balance updated: $164.90 → $147.13" (correct account balance)
# ❌ "Loaded 7 existing orders for ETHUSDT" (wrong account)
```

### **Complete Fix Process**
```bash
# 1. Audit all credential locations
find /build/location -name "*.env*" -exec cat {} \;

# 2. Fix external SSD environment files
cp /source/env-templates/.env.eth /external/ssd/builds/trader-bot/.env.eth
cp /source/env-templates/.env.ada /external/ssd/builds/trader-bot/.env.ada

# 3. Clean contaminated Docker images
docker rmi $(docker images | grep trader-bot | awk '{print $3}')
docker system prune -f

# 4. Fresh build with correct credentials
cd /external/ssd/build/location
docker build --platform linux/amd64 -t trader-bot-final:amd64 /source/path
docker save trader-bot-final:amd64 | gzip > trader-bot-final-amd64.tar.gz

# 5. Deploy with verification
./deploy-final.sh
ssh user@vm "docker logs eth-bot --tail 20"  # Verify correct account connection
```

### **Regular Maintenance**
- **Before each deployment**: Verify environment files match intended credentials
- **After deployment**: Check logs for correct account connection
- **Monthly audit**: Review all credential storage locations
- **Test separation**: Run only one bot at a time to verify isolation

**Fixed:** 2025-08-05 - Complete credential separation implemented and verified