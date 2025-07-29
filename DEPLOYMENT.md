# Trading Bot Deployment Guide

## Overview

The deployment system uses a **two-script approach** for reliable VM deployment:
- **Local Coordinator** (`deploy.sh`) - Runs on your PC, orchestrates deployment
- **VM Executor** (generated dynamically) - Runs entirely on VM for reliable execution

## Quick Start

```bash
# Update ADA bot with latest code
./deploy.sh ada update

# Restart both bots without code changes
./deploy.sh all restart

# Stop ETH bot only
./deploy.sh eth stop
```

## Usage

```
./deploy.sh [SYMBOL] [ACTION]
```

### Symbols
- `ada` - Deploy ADA bot only (ADAUSDT)
- `eth` - Deploy ETH bot only (ETHUSDT)  
- `all` - Deploy both ADA and ETH bots

### Actions
- `stop` - Stop specified bot(s)
- `update` - Update code and restart bot(s)
- `restart` - Restart bot(s) without code update

## What It Does

### For `stop` Action:
1. ✅ Connects to VM via SSH
2. ✅ Stops specified Docker containers
3. ✅ Cleans unused Docker resources
4. ✅ Reports final status

### For `update` Action:
1. ✅ Creates code archive (excludes logs, git, temp files)
2. ✅ Uploads VM deployment script to VM
3. ✅ Uploads code archive to VM
4. ✅ **VM executes locally:**
   - Preserves all .env files (ada, eth, future symbols)
   - Stops specified containers safely
   - Cleans Docker to free disk space
   - Extracts new code
   - Restores preserved configuration files
   - Rebuilds and starts containers
   - Performs health checks
5. ✅ Cleans up temporary files
6. ✅ Reports deployment status

### For `restart` Action:
1. ✅ Uploads VM deployment script
2. ✅ **VM executes locally:**
   - Stops specified containers
   - Cleans Docker resources
   - Starts containers with existing code
   - Performs health checks
3. ✅ Reports status

## File Preservation

The deployment system **automatically preserves**:
- All environment files (`.env.ada`, `.env.eth`, `.env`)
- SSH keys (`ssh-key-2025-07-27.key`)
- Docker configuration (`docker-compose.multi.yml`)
- Log directories and important configs

## Safety Features

### ✅ Atomic Operations
- All VM operations execute locally on VM (no network delays)
- Each step waits for previous completion
- Single SSH connection prevents connection issues

### ✅ Backup System
- Creates timestamped backup before changes
- Automatically restores preserved files
- Rollback capability in case of issues

### ✅ Health Checks
- Verifies containers are running after deployment
- Shows recent logs for troubleshooting
- Container status monitoring

### ✅ Docker Cleanup
- Removes unused images to free disk space
- Preserves essential base images
- Cleans containers and networks safely

## Examples

### Daily Code Update
```bash
# Update both bots with latest code changes
./deploy.sh all update
```

### Fix Single Bot
```bash
# Restart only ADA bot if it's having issues
./deploy.sh ada restart
```

### Maintenance Mode
```bash
# Stop all bots for maintenance
./deploy.sh all stop

# Later, restart without code changes
./deploy.sh all restart
```

### Selective Deployment
```bash
# Update only ETH bot (ADA keeps running)
./deploy.sh eth update
```

## Troubleshooting

### Connection Issues
```bash
# Test VM connectivity
ssh -i ./ssh-key-2025-07-27.key ubuntu@89.168.111.195 "echo 'Connection OK'"
```

### Check Bot Status on VM
```bash
# Connect to VM and check status
ssh -i ./ssh-key-2025-07-27.key ubuntu@89.168.111.195
cd trader-bot-production
docker ps
docker logs bot-ada
docker logs bot-eth
```

### Disk Space Issues
```bash
# Check disk usage on VM
./deploy.sh all stop  # This includes Docker cleanup
df -h  # Check disk space
```

### View Recent Logs
```bash
# ADA bot logs
docker logs --tail 50 bot-ada

# ETH bot logs  
docker logs --tail 50 bot-eth

# Follow live logs
docker logs -f bot-ada
```

## Configuration

### VM Settings (in deploy.sh)
```bash
VM_USER="ubuntu"
VM_HOST="89.168.111.195"
SSH_KEY="./ssh-key-2025-07-27.key"
VM_DIR="/home/ubuntu/trader-bot-production"
```

### Files Excluded from Upload
- `.git` directory and `.gitignore`
- `logs/*` directory
- `*.tar.gz` archives
- `__pycache__` and `*.pyc` files
- `env-templates` directory

## Adding New Trading Symbols

To add support for new trading symbols (e.g., BTC):

1. **Create environment file:**
   ```bash
   cp .env.ada .env.btc
   # Edit .env.btc with BTC-specific settings
   ```

2. **Update docker-compose.multi.yml:**
   ```yaml
   bot-btc:
     build:
       context: .
       dockerfile: Dockerfile
     container_name: bot-btc
     command: ["BTCUSDT"]
     env_file: .env.btc
     # ... rest of configuration
   ```

3. **Update deploy.sh:**
   - Add `btc` to symbol validation pattern
   - Add BTC handling in VM script template

## Security Notes

- SSH key is used for secure VM communication
- Environment files with API credentials are preserved
- No sensitive data is logged or transmitted in plain text
- Docker containers run with limited resources and privileges

## Performance

### Resource Usage Per Bot
- **Memory**: 128MB limit per bot
- **CPU**: 0.25 cores per bot  
- **Logs**: 10MB max, 3 file rotation
- **Health checks**: Every 60 seconds

### Deployment Speed
- **Code upload**: ~10-30 seconds (depends on code size)
- **Docker build**: ~30-60 seconds per bot
- **Container start**: ~5-10 seconds
- **Total time**: Usually 1-3 minutes for full update

---

## Need Help?

Run with help flag for quick reference:
```bash
./deploy.sh --help
```

Or check the current bot status:
```bash
./deploy.sh all restart  # Safe restart to check everything is working
```