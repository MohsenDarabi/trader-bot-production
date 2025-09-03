# Multi-Instance Trading Bot Deployment Guide

## Overview

The enhanced `deploy.sh` script now supports deploying trading bots to multiple Oracle Cloud instances with **mandatory instance selection** to prevent accidental deployments.

## 🚨 Important Changes

- **REQUIRED `--instance` parameter** - No default instance to prevent accidents
- **Two separate instances** - Each should run **different** bots to avoid conflicts
- **No "both" option** - Prevents duplicate bots running same strategy

## Instance Configuration

| Instance | IP Address | Purpose | Status |
|----------|------------|---------|---------|
| **first** | 89.168.111.195 | Current production (ada, aave running) | ✅ Active |
| **second** | 92.5.15.61 | New expansion instance | ✅ Ready |

## Usage

### Basic Syntax
```bash
./deploy.sh [SYMBOL] [ACTION] --instance [first|second] [opt]
```

### Available Actions

| Action | Description |
|--------|-------------|
| `setup` | Initialize instance with directory structure and .env files |
| `status` | Show running bots and instance information |
| `stop` | Stop specified bot |
| `update` | Build new image and restart bot |
| `restart` | Restart bot with existing image |

### Required Parameters

- **`--instance first|second`** - MANDATORY to prevent accidental deployments
- **`[SYMBOL]`** - 3-8 letter crypto symbol (required for stop/update/restart)

### Optional Parameters

- **`opt`** - Use optimized Docker build (70% smaller images)

## Examples

### Instance Management
```bash
# Initialize second instance (first time setup)
./deploy.sh setup --instance second

# Check what's running on each instance
./deploy.sh status --instance first
./deploy.sh status --instance second
```

### Bot Deployment
```bash
# Deploy ADA bot to first instance (optimized)
./deploy.sh ada update --instance first opt

# Deploy ETH bot to second instance (standard)  
./deploy.sh eth update --instance second

# Stop ADA bot on first instance
./deploy.sh ada stop --instance first

# Restart ETH bot on second instance
./deploy.sh eth restart --instance second
```

## 🎯 Deployment Strategy

### Recommended Bot Distribution

**First Instance (89.168.111.195)**
- ✅ ADA bot (currently running)
- ✅ AAVE bot (currently running)
- Keep existing profitable bots

**Second Instance (92.5.15.61)**
- 🆕 ETH bot (new deployment)
- 🆕 VET bot (potential expansion)
- 🆕 BTC bot (future expansion)

### Why Separate Instances?

1. **No Conflicts** - Different bots can't interfere with each other
2. **Resource Isolation** - Each bot gets dedicated CPU/memory
3. **Risk Distribution** - Issues on one instance don't affect others
4. **Easy Scaling** - Can optimize each instance for specific bot needs

## Safety Features

### Mandatory Instance Selection
```bash
# ❌ Will FAIL - no --instance parameter
./deploy.sh ada update

# ✅ Will work - explicit instance selection
./deploy.sh ada update --instance first
```

### Error Prevention
- Script **requires** `--instance` parameter
- Clear error messages when missing
- No default instance to prevent accidents
- Instance information shown in all log messages

## Setup Process

### First Time Setup (Second Instance)
```bash
# 1. Initialize second instance
./deploy.sh setup --instance second

# 2. Check status
./deploy.sh status --instance second

# 3. Deploy first bot
./deploy.sh eth update --instance second opt
```

### Environment Files
- Automatically copied from first instance during setup
- Each instance has separate .env files
- Can be customized per instance if needed

## Troubleshooting

### Common Issues

**"--instance parameter is REQUIRED"**
- Solution: Always specify `--instance first` or `--instance second`

**"Cannot connect to VM"**
- Check instance is running in Oracle Cloud Console
- Verify SSH key permissions: `chmod 600 ssh-key-2025-07-27.key`

**Docker permission denied (second instance)**
- Expected after Docker installation
- SSH logout/login or reboot instance to fix
- Deployment will work despite the permission warning

### Monitoring Both Instances
```bash
# Check both instances
./deploy.sh status --instance first
./deploy.sh status --instance second

# Watch logs from first instance
ssh -i ssh-key-2025-07-27.key ubuntu@89.168.111.195 "docker logs -f ada"

# Watch logs from second instance  
ssh -i ssh-key-2025-07-27.key ubuntu@92.5.15.61 "docker logs -f eth"
```

## Benefits

### ✅ Advantages of Multi-Instance Deployment

- **2x Resources** - 2 OCPUs + 2 GB RAM total (was 1 OCPU + 1 GB)
- **Parallel Trading** - Multiple bots trading different symbols
- **Fault Tolerance** - One instance down doesn't stop all trading
- **Easy Management** - Single script manages both instances
- **Cost Efficient** - Still 100% free with Oracle's Always Free tier

### 🎯 Future Expansion

With **Ampere A1** (when available):
- **Third instance** with 4 OCPUs + 24 GB RAM
- **Total capacity**: 6 OCPUs + 26 GB RAM across 3 instances
- **Advanced strategies** possible with high-performance ARM instance

## Quick Reference

```bash
# Initialize new instance
./deploy.sh setup --instance second

# Deploy different bots to different instances
./deploy.sh ada update --instance first opt   # Keep existing
./deploy.sh eth update --instance second opt  # New deployment

# Monitor both
./deploy.sh status --instance first
./deploy.sh status --instance second

# Stop any bot safely
./deploy.sh [symbol] stop --instance [first|second]
```

---

**Created**: 2025-09-03  
**Author**: Multi-instance deployment system  
**Status**: ✅ Production Ready