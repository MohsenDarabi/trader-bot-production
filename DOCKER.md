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