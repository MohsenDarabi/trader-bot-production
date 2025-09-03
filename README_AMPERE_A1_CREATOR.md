# Oracle Cloud Ampere A1 Automated Instance Creator

🤖 **Automated script to create Oracle Cloud's free tier Ampere A1 instances**

## Overview

Oracle Cloud's Ampere A1 instances (4 OCPUs, 24 GB RAM) are extremely popular and often show "Out of host capacity" errors. This script automates the retry process, running continuously until an instance is successfully created.

## 🎯 What You Get

- **4 OCPUs** (ARM-based Ampere processors)
- **24 GB RAM** 
- **100 GB boot storage**
- **100% FREE** (Always Free tier)
- **Automatic creation** when capacity becomes available

## 🚀 Quick Start

### Prerequisites

1. **Oracle Cloud Account** with Always Free tier
2. **OCI CLI installed and configured**
3. **SSH key pair** for instance access
4. **macOS/Linux environment** (bash script)

### Installation & Setup

```bash
# 1. Clone or download the script
chmod +x ampere_a1_auto_creator.sh

# 2. Verify prerequisites
./ampere_a1_auto_creator.sh --check-only  # (if implemented)

# 3. Run the script
./ampere_a1_auto_creator.sh
```

## 📖 Usage

### Interactive Mode (Recommended for first run)
```bash
./ampere_a1_auto_creator.sh
```
Shows live progress and logs to console. Good for testing and monitoring initial attempts.

### Background Mode (Recommended for long waits)
```bash
./ampere_a1_auto_creator.sh --background
```
Runs in background and logs to file. Best for unattended operation.

### Monitor Background Execution
```bash
# View live log output
tail -f ampere_creation.log

# Check if process is running
ps aux | grep ampere_a1_auto_creator

# View recent attempts
tail -20 ampere_creation.log
```

## 📊 Configuration

### Default Settings (Optimized)
- **Instance Shape**: VM.Standard.A1.Flex
- **OCPUs**: 4 (maximum free tier)
- **RAM**: 24 GB (maximum free tier)  
- **Storage**: 100 GB boot volume
- **Retry Strategy**: Dynamic intervals (2-10 minutes)
- **Maximum Attempts**: Unlimited (runs until success)
- **Availability Domains**: All 3 in eu-frankfurt-1
- **Log Rotation**: Auto-rotates at 50 MB to prevent disk issues
- **Success Notifications**: Desktop alerts + visible files

### Customization
Edit the script's configuration section to modify:
```bash
# Instance Configuration
readonly OCPUS=4
readonly MEMORY_GB=24
readonly BOOT_VOLUME_GB=100

# Optimized Retry Configuration  
readonly MIN_RETRY_INTERVAL=120      # 2 minutes (aggressive start)
readonly MAX_RETRY_INTERVAL=600      # 10 minutes maximum
readonly MAX_ATTEMPTS=0              # 0 = unlimited attempts
readonly MAX_LOG_SIZE_MB=50          # Auto-rotate logs
```

## 🛠️ Features

### ✅ Comprehensive Error Handling
- **Out of host capacity** - Retries automatically
- **Connection timeouts** - Exponential backoff
- **Authentication errors** - Clear error messages
- **Subnet/network issues** - Uses verified network configuration

### ✅ Optimized Retry Logic  
- Tries all 3 availability domains per attempt
- Dynamic intervals: 2 minutes (fast start) → 10 minutes (steady state)
- Unlimited attempts (runs until success)
- Smart backoff based on failure patterns + randomization
- Off-peak time detection for better success rates

### ✅ Detailed Logging & Progress Tracking
- Real-time progress updates
- Comprehensive error logging
- Success notification with instance details
- Integration with tracking document

### ✅ Background Process Support
- Daemon-style background execution
- Process monitoring capabilities
- Automatic log rotation (prevents disk space issues)
- Clean termination handling

### ✅ Unmissable Success Notifications
- macOS system notifications with sound
- Visible success files on desktop
- Detailed success report with SSH instructions
- Multiple notification methods to ensure visibility

## 📋 Output Files

### `ampere_creation.log` (Auto-rotated)
Real-time execution log with timestamps (auto-rotates at 50MB):
```
[2025-09-03 16:06:17 UTC] [INFO] === Oracle Cloud Ampere A1 Automated Creator Started ===
[2025-09-03 16:06:17 UTC] [INFO] Max attempts: unlimited, Dynamic intervals: 120-600s
[2025-09-03 16:06:18 UTC] [INFO] === Attempt 1 of ∞ ===
```

### `ampere_creation_success.json`
Created upon successful instance creation with full Oracle Cloud response including instance OCID, IP addresses, and configuration.

### `🎉_AMPERE_A1_SUCCESS_🎉.txt`
**UNMISSABLE** success notification file with SSH instructions and celebration message.

### `🎉_AMPERE_A1_READY_🎉.txt` (Desktop)
Visible desktop notification file so you can't miss the success.

### `OCI_INSTANCE_CREATION_TRACKER.md`
Automatically updated with attempt history and results.

## 🔧 Troubleshooting

### Common Issues

**Script exits immediately**
- Check OCI CLI authentication: `oci iam user get --user-id YOUR_USER_OCID`
- Verify SSH key file exists and is readable

**"Authentication failed" error**
- Reconfigure OCI CLI: `oci setup config`
- Check API key permissions in Oracle Cloud Console

**"Subnet not found" error**  
- Verify you have the correct region subscription
- Check VCN and subnet configuration in Oracle Cloud Console

**Script runs but never succeeds**
- This is normal! Ampere A1 instances are in very high demand
- Script now runs UNLIMITED attempts - it will eventually succeed
- Try different times of day (early morning/late night often better)  
- May run for days/weeks - that's expected and handled
- Logs auto-rotate to prevent disk space issues

### Manual Testing
Test individual components:
```bash
# Test OCI CLI authentication
oci iam user get --user-id YOUR_USER_OCID

# Test instance creation manually (will likely fail)
oci compute instance launch \
  --availability-domain "Clau:EU-FRANKFURT-1-AD-1" \
  --compartment-id "YOUR_COMPARTMENT_ID" \
  --shape "VM.Standard.A1.Flex" \
  --shape-config '{"ocpus": 4, "memoryInGBs": 24}'
```

### 📊 Monitoring Long-Running Script

Since the script may run for days/weeks:

```bash
# Check if script is running
ps aux | grep ampere_a1_auto_creator | grep -v grep

# Monitor live progress
tail -f ampere_creation.log

# Check recent attempts (last 20 lines)
tail -20 ampere_creation.log

# View current attempt count and strategy
grep "=== Attempt" ampere_creation.log | tail -5

# Check log file size
ls -lh ampere_creation.log*
```

The script will:
- ✅ Run unlimited attempts automatically
- ✅ Handle rate limiting and backoff
- ✅ Rotate logs to prevent disk issues
- ✅ Send unmissable notifications on success

## 📈 Success Tips

### Timing Strategies
- **Early morning** (2-6 AM local time) often has better availability
- **Late evening** (10 PM - 2 AM) can also work
- **Weekdays** typically better than weekends
- **Different months** have varying capacity

### Resource Optimization
- Consider smaller configurations initially (1-2 OCPUs) then resize
- Monitor Oracle Cloud status page for capacity updates
- Join Oracle Cloud communities for capacity alerts

## 🔐 Security Notes

- Script uses existing OCI CLI credentials (secure)
- No credentials stored in script files
- SSH keys handled via file references only
- All Oracle Cloud API calls use official OCI CLI

## 📜 Prerequisites Details

### Oracle Cloud Setup Required
1. **Free Tier Account**: [oracle.com/cloud/free](https://oracle.com/cloud/free)
2. **Region Subscription**: eu-frankfurt-1 (or modify script)
3. **API Key**: Added to your user profile
4. **VCN & Subnet**: Default or custom (script uses existing)

### Local Environment
- **OCI CLI**: `brew install oci-cli` or [official installer](https://docs.oracle.com/iaas/tools/oci-cli/latest/oci_cli_docs/install.html)  
- **SSH Key Pair**: For instance access
- **bash**: Most Unix-like systems (macOS, Linux)

## 📊 Free Tier Limits

This script respects Oracle's Always Free tier limits:
- ✅ **Ampere A1**: Up to 4 OCPUs and 24 GB RAM total
- ✅ **Storage**: Up to 200 GB total across all instances
- ✅ **Network**: 10 TB outbound transfer per month
- ✅ **Load Balancer**: 1 instance, 10 Mbps

## 🤝 Contributing

Found a bug or have an improvement? 
- Report issues via GitHub Issues
- Submit pull requests for enhancements
- Share success stories and timing tips

## 📄 License

This script is provided as-is for educational and personal use. Oracle Cloud terms of service apply to all instance creation and usage.

## 🔗 Related Resources

- [Oracle Cloud Always Free](https://oracle.com/cloud/free/)
- [OCI CLI Documentation](https://docs.oracle.com/iaas/tools/oci-cli/latest/)
- [Ampere A1 Compute](https://docs.oracle.com/iaas/Content/Compute/References/arm.htm)
- [Oracle Cloud Status](https://status.cloud.oracle.com/)

---

**Created**: 2025-09-03  
**Version**: 1.0  
**Author**: Automated with Claude Code assistance  
**Tested**: Oracle Cloud Always Free tier, eu-frankfurt-1 region