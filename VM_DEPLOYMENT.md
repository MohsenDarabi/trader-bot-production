# Trading Bot VM Deployment Guide

This guide covers deploying and managing the CoinEx trading bots on Oracle Cloud VM using Docker.

## Prerequisites

- Oracle Cloud VM (Ubuntu 22.04 Minimal)
- SSH access to the VM
- Docker and Docker Compose installed
- Two separate CoinEx accounts with API credentials

## VM Setup

### Initial Connection
```bash
# SSH into VM
ssh -i ./ssh-key-2025-07-27.key ubuntu@your-vm-ip

# Check VM resources
free -h
df -h
```

### Docker Installation
```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install Docker
sudo apt install -y docker.io docker-compose

# Add user to docker group
sudo usermod -aG docker ubuntu

# Logout and login again for group change
exit
ssh -i ./ssh-key-2025-07-27.key ubuntu@your-vm-ip
```

## Bot Configuration

### File Structure
```
trader-bot-production/
├── .env.ada              # ADA bot credentials
├── .env.eth              # ETH bot credentials
├── docker-compose.multi.yml
├── Dockerfile
├── entrypoint.sh
└── src/
```

### Environment Files

**`.env.ada` (First CoinEx Account)**
```env
COINEX_API_KEY="your_ada_api_key"
COINEX_API_SECRET="your_ada_secret"
TRADING_MODE=NORMAL
DEFAULT_TRADING_MARKET=ADAUSDT
LOG_LEVEL=INFO
```

**`.env.eth` (Second CoinEx Account)**
```env
COINEX_API_KEY="your_eth_api_key"
COINEX_API_SECRET="your_eth_secret"
TRADING_MODE=NORMAL
DEFAULT_TRADING_MARKET=ETHUSDT
LOG_LEVEL=INFO
```

## Docker Commands

### Building the Image
```bash
# Build Docker image
docker build -t trading-bot .

# Or build via compose
docker-compose -f docker-compose.multi.yml build --no-cache
```

### Managing Both Bots

#### Start Both Bots
```bash
# Start both ADA and ETH bots
docker-compose -f docker-compose.multi.yml up -d

# Verify both are running
docker ps
```

#### Stop Both Bots
```bash
# Stop both bots
docker-compose -f docker-compose.multi.yml down

# Stop and remove containers
docker-compose -f docker-compose.multi.yml down --volumes
```

#### Restart Both Bots
```bash
# Restart both bots
docker-compose -f docker-compose.multi.yml restart

# Rebuild and restart
docker-compose -f docker-compose.multi.yml up -d --build
```

### Managing Individual Bots

#### ADA Bot Commands
```bash
# Start only ADA bot
docker-compose -f docker-compose.multi.yml up -d bot-ada

# Stop ADA bot
docker-compose -f docker-compose.multi.yml stop bot-ada

# Restart ADA bot
docker-compose -f docker-compose.multi.yml restart bot-ada

# View ADA bot logs
docker logs -f bot-ada

# Execute command in ADA bot
docker exec -it bot-ada /bin/bash
```

#### ETH Bot Commands
```bash
# Start only ETH bot
docker-compose -f docker-compose.multi.yml up -d bot-eth

# Stop ETH bot
docker-compose -f docker-compose.multi.yml stop bot-eth

# Restart ETH bot
docker-compose -f docker-compose.multi.yml restart bot-eth

# View ETH bot logs
docker logs -f bot-eth

# Execute command in ETH bot
docker exec -it bot-eth /bin/bash
```

### Updating Bots

#### Update Single Bot
```bash
# Update ADA bot only
docker-compose -f docker-compose.multi.yml stop bot-ada
docker-compose -f docker-compose.multi.yml build bot-ada
docker-compose -f docker-compose.multi.yml up -d bot-ada

# Update ETH bot only
docker-compose -f docker-compose.multi.yml stop bot-eth
docker-compose -f docker-compose.multi.yml build bot-eth
docker-compose -f docker-compose.multi.yml up -d bot-eth
```

#### Update Both Bots
```bash
# Stop all, rebuild, restart
docker-compose -f docker-compose.multi.yml down
docker-compose -f docker-compose.multi.yml build --no-cache
docker-compose -f docker-compose.multi.yml up -d
```

## Monitoring Commands

### Container Status
```bash
# View running containers
docker ps

# View all containers (including stopped)
docker ps -a

# Check container health
docker inspect bot-ada | grep Health -A 10
docker inspect bot-eth | grep Health -A 10
```

### Resource Monitoring
```bash
# View resource usage
docker stats --no-stream

# Monitor continuously (Ctrl+C to exit)
docker stats

# System resources
free -h      # Memory usage
df -h        # Disk usage
htop         # CPU and processes
```

### Log Management
```bash
# View recent logs
docker logs --tail 50 bot-ada
docker logs --tail 50 bot-eth

# Follow logs in real-time
docker logs -f bot-ada
docker logs -f bot-eth

# View logs with timestamps
docker logs -f -t bot-ada

# Save logs to file
docker logs bot-ada > ada-bot.log
docker logs bot-eth > eth-bot.log
```

## Troubleshooting

### Common Issues

#### Container Won't Start
```bash
# Check container status
docker ps -a

# View container logs
docker logs bot-ada

# Rebuild image
docker-compose -f docker-compose.multi.yml build --no-cache bot-ada
```

#### Permission Errors
```bash
# Fix log permissions
sudo chmod -R 777 logs/

# Check container user
docker exec bot-ada whoami
```

#### Memory Issues
```bash
# Check memory usage
free -h
docker stats --no-stream

# Clean up unused containers/images
docker system prune -f
```

#### API Connection Issues
```bash
# Test inside container
docker exec -it bot-ada python -c "from src.exchange.coinex_client import CoinExClient; print('OK')"

# Check environment variables
docker exec bot-ada env | grep COINEX
```

### Debugging Commands
```bash
# Enter container shell
docker exec -it bot-ada /bin/bash
docker exec -it bot-eth /bin/bash

# Check running processes inside container
docker exec bot-ada ps aux

# View container configuration
docker inspect bot-ada
```

## Maintenance

### Daily Checks
```bash
# Quick status check
docker ps && docker stats --no-stream

# Check logs for errors
docker logs --tail 20 bot-ada | grep -i error
docker logs --tail 20 bot-eth | grep -i error
```

### Weekly Maintenance
```bash
# Clean up unused Docker resources
docker system prune -f

# Update system packages
sudo apt update && sudo apt upgrade -y

# Check disk space
df -h
```

### Backup
```bash
# Backup configuration files
tar czf bot-backup.tar.gz .env.ada .env.eth docker-compose.multi.yml

# Copy backup to local machine
scp ubuntu@your-vm-ip:~/trader-bot-production/bot-backup.tar.gz ./
```

## Auto-Start on Boot

Bots automatically restart due to `restart: always` in docker-compose.yml.

To verify auto-start:
```bash
# Reboot VM
sudo reboot

# After reboot, check bots started
docker ps
```

## Performance Optimization

### Resource Limits
Both bots are configured with:
- Memory limit: 128MB each
- CPU limit: 0.25 cores each
- Total usage: ~256MB RAM, 0.5 CPU cores

### Monitoring Script
Create `~/monitor.sh`:
```bash
#!/bin/bash
echo "=== Bot Status ==="
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.RunningFor}}"
echo -e "\n=== Resource Usage ==="
docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}"
echo -e "\n=== System Resources ==="
free -h | grep Mem
df -h / | tail -1
```

Make executable and run:
```bash
chmod +x ~/monitor.sh
./monitor.sh
```

## Security Notes

- API keys are stored in separate .env files
- Containers run as non-root user
- No exposed ports (internal communication only)
- Regular system updates recommended

## Critical Deployment Best Practices

### Pre-Deployment Checklist
**ALWAYS complete these steps before any deployment:**

```bash
# 1. Build Docker image locally with latest code
docker build --platform linux/amd64 -t trader-bot-final:amd64 .

# 2. Test image functionality locally
docker run --rm --env-file .env.eth trader-bot-final:amd64 python -c "from src.core.trading_bot import DailyRangeBot; print('Image OK')"

# 3. Save image for VM transfer
docker save trader-bot-final:amd64 | gzip > trader-bot-final-amd64.tar.gz

# 4. Verify environment files have correct credentials
grep "COINEX_API_KEY" .env.eth  # Should match ETH account
grep "COINEX_API_KEY" .env.ada  # Should match ADA account

# 5. Check target container names in docker-compose.multi.yml
grep -A 10 "services:" docker-compose.multi.yml
```

### Container Naming Standards
**Use clean, simple names without redundant prefixes:**

```yaml
# ✅ CORRECT: Clean naming in docker-compose.multi.yml
version: '3.8'
services:
  eth:  # Simple, direct
    build: .
    env_file: .env.eth
    environment:
      - DEFAULT_TRADING_MARKET=ETHUSDT
    
  ada:  # Easy to reference  
    build: .
    env_file: .env.ada
    environment:
      - DEFAULT_TRADING_MARKET=ADAUSDT

# ❌ WRONG: Redundant naming
services:
  bot-eth:  # Unnecessarily verbose
  bot-ada:  # Creates confusion in scripts
```

**Deployment Commands with Correct Names:**
```bash
# Deploy single service
docker-compose -f docker-compose.multi.yml up -d eth
docker-compose -f docker-compose.multi.yml up -d ada

# Check status
docker logs eth
docker logs ada

# Stop/restart specific service
docker-compose -f docker-compose.multi.yml stop eth
docker-compose -f docker-compose.multi.yml restart ada
```

### Service Isolation Protocol
**Deploy individual bots without affecting others:**

```bash
# ✅ CORRECT: Deploy ADA without affecting ETH
./deploy.sh ada update
# ETH bot continues running uninterrupted

# ✅ CORRECT: Deploy ETH without affecting ADA  
./deploy.sh eth restart
# ADA bot continues running uninterrupted

# Verify isolation
docker ps | grep eth  # ETH status unchanged if deploying ADA
docker ps | grep ada  # ADA status unchanged if deploying ETH
```

### Mandatory Build-First Workflow
**Never deploy without building fresh Docker image:**

```bash
# Step 1: BUILD on local machine
echo "Building trader-bot with latest code..."
docker build --platform linux/amd64 -t trader-bot-final:amd64 .

# Step 2: SAVE for VM transfer
echo "Saving Docker image..."
docker save trader-bot-final:amd64 | gzip > trader-bot-final-amd64.tar.gz

# Step 3: TRANSFER to VM
echo "Transferring to VM..."
scp -i ./ssh-key-2025-07-27.key trader-bot-final-amd64.tar.gz ubuntu@VM_IP:~/

# Step 4: LOAD on VM  
echo "Loading image on VM..."
ssh -i ./ssh-key-2025-07-27.key ubuntu@VM_IP "docker load < trader-bot-final-amd64.tar.gz"

# Step 5: DEPLOY with updated image
ssh -i ./ssh-key-2025-07-27.key ubuntu@VM_IP "cd trader-bot-production && docker-compose -f docker-compose.multi.yml up -d eth"
```

### Deployment Verification
**Always verify deployment success immediately:**

```bash
# Check container is running
ssh ubuntu@VM_IP "docker ps | grep eth"

# Monitor logs for successful startup
ssh ubuntu@VM_IP "docker logs -f eth" | head -20

# Look for key success indicators:
# - "📊 Daily Trading Signals for ETHUSDT: Buy=$4218.8675 | Sell=$4319.6225"
# - "🔐 WebSocket Auth - Access ID: 377EB9..."
# - "✅ Authentication confirmed"
# - No ERROR messages in first 50 log lines
```

### Emergency Response Protocol
**If deployment causes issues:**

```bash
# 1. IMMEDIATE STOP
ssh ubuntu@VM_IP "docker stop eth"  # Replace 'eth' with affected service

# 2. CHECK LOGS
ssh ubuntu@VM_IP "docker logs eth | tail -50"

# 3. ROLLBACK if needed
ssh ubuntu@VM_IP "docker-compose -f docker-compose.multi.yml up -d eth"  # Using previous image

# 4. ANALYZE before next attempt
# - Review build logs
# - Check environment file credentials  
# - Verify docker-compose.multi.yml service names
# - Test image locally before redeploying
```

### Common Deployment Mistakes to Avoid
1. **❌ Using redundant container names** (bot-eth vs eth)
2. **❌ Stopping unrelated services** during single-bot deployment
3. **❌ Deploying without building fresh Docker image** locally
4. **❌ Not monitoring logs** immediately after deployment
5. **❌ No rollback plan** when deployment fails

## Support

For issues:
1. **First**: Check TROUBLESHOOTING.md deployment mistakes section
2. Check logs: `docker logs -f service-name` (use clean names: eth, ada)  
3. Verify credentials in .env files match intended accounts
4. Check system resources: `free -h`
5. Follow service isolation protocol when restarting individual bots
6. **Always** build fresh Docker image before deployment attempts