#!/bin/bash

# =============================================================================
# Oracle Cloud Ampere A1 Automated Instance Creator
# =============================================================================
# Purpose: Automatically retry creating Ampere A1 instance until successful
# Created: 2025-09-03 
# Author: Claude Code assistance
# 
# This script addresses all known issues:
# - Out of host capacity (retry mechanism)
# - Subnet authorization errors (use working subnet)
# - Connection timeouts (exponential backoff)
# - Proper error handling and logging
# =============================================================================

set -euo pipefail  # Exit on any error, undefined vars, or pipe failures

# =============================================================================
# Configuration
# =============================================================================

# Oracle Cloud Configuration
readonly COMPARTMENT_ID="ocid1.tenancy.oc1..aaaaaaaavixexmhtwf4wgqav3gyun6rxbppk5vuoz32jqdjmbc5cvhvt7usa"
readonly SUBNET_ID="ocid1.subnet.oc1.eu-frankfurt-1.aaaaaaaaw3bxhsk2yxt2feplmn5vuakhzrcxnyuiuxw2tovetfmdph6rf3qa"
readonly IMAGE_ID="ocid1.image.oc1.eu-frankfurt-1.aaaaaaaaww5bbjvzql4bvtt3nrok7v2k6atg55ldvgffg36jqdd4wc7ecssa"
readonly SSH_KEY_FILE="/Users/mohsendarabi/Desktop/workspace/trader-bot-hourly/ssh-key-2025-07-27.key.pub"
readonly TRACKER_FILE="/Users/mohsendarabi/Desktop/workspace/trader-bot-hourly/OCI_INSTANCE_CREATION_TRACKER.md"

# Instance Configuration
readonly INSTANCE_NAME="ampere-trading-bot-auto"
readonly SHAPE="VM.Standard.A1.Flex"
readonly OCPUS=4
readonly MEMORY_GB=24
readonly BOOT_VOLUME_GB=100  # Maximum allowed within free tier budget

# Availability Domains to try
readonly AVAILABILITY_DOMAINS=(
    "Clau:EU-FRANKFURT-1-AD-1"
    "Clau:EU-FRANKFURT-1-AD-2" 
    "Clau:EU-FRANKFURT-1-AD-3"
)

# Optimized Retry Configuration (Based on community best practices)
readonly MIN_RETRY_INTERVAL=120      # 2 minutes minimum (aggressive start)
readonly MAX_RETRY_INTERVAL=600      # 10 minutes maximum
readonly INITIAL_FAST_ATTEMPTS=10    # First 10 attempts use minimum interval
readonly MEDIUM_ATTEMPTS=50          # Attempts 11-50 use gradual increase
readonly MAX_ATTEMPTS=0              # 0 = unlimited (run until success)
readonly BACKOFF_MULTIPLIER=1.15     # Gradual 15% increase per failure batch
readonly RANDOMIZE_SECONDS=30        # Add ±30s randomization to avoid patterns
readonly RATE_LIMIT_BACKOFF=1800     # 30 minutes backoff after 429 errors

# Logging Configuration
readonly LOG_FILE="/Users/mohsendarabi/Desktop/workspace/trader-bot-hourly/ampere_creation.log"
readonly SUCCESS_FILE="/Users/mohsendarabi/Desktop/workspace/trader-bot-hourly/ampere_creation_success.json"
readonly SUCCESS_NOTIFICATION_FILE="/Users/mohsendarabi/Desktop/workspace/trader-bot-hourly/🎉_AMPERE_A1_SUCCESS_🎉.txt"
readonly MAX_LOG_SIZE_MB=50  # Rotate log when it exceeds this size

# =============================================================================
# Utility Functions
# =============================================================================

log() {
    local level="$1"
    shift
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S UTC')
    local message="[$timestamp] [$level] $*"
    
    # Check log size and rotate if needed
    check_log_rotation
    
    echo "$message" | tee -a "$LOG_FILE"
}

log_info() {
    log "INFO" "$@"
}

log_warn() {
    log "WARN" "$@"
}

log_error() {
    log "ERROR" "$@"
}

log_success() {
    log "SUCCESS" "$@"
}

# Check and rotate log file if it's too large
check_log_rotation() {
    if [[ -f "$LOG_FILE" ]]; then
        local size_mb=$(du -m "$LOG_FILE" 2>/dev/null | cut -f1)
        if [[ $size_mb -gt $MAX_LOG_SIZE_MB ]]; then
            local backup_file="${LOG_FILE}.$(date '+%Y%m%d_%H%M%S').bak"
            log_info "Log file size ($size_mb MB) exceeds limit ($MAX_LOG_SIZE_MB MB). Rotating to: $backup_file"
            mv "$LOG_FILE" "$backup_file"
            log_info "Log rotation completed. Starting fresh log file."
        fi
    fi
}

# Send unmissable success notifications
send_success_notifications() {
    local instance_id="$1"
    local public_ip="$2"
    local attempt_count="$3"
    
    # Create very visible success file
    {
        echo "🎉🎉🎉 AMPERE A1 INSTANCE CREATED SUCCESSFULLY! 🎉🎉🎉"
        echo ""
        echo "SUCCESS TIME: $(date '+%Y-%m-%d %H:%M:%S UTC')"
        echo "INSTANCE ID: $instance_id"
        echo "PUBLIC IP: $public_ip"
        echo "ATTEMPTS NEEDED: $attempt_count"
        echo "CONFIGURATION: $OCPUS OCPUs, ${MEMORY_GB}GB RAM, ${BOOT_VOLUME_GB}GB storage"
        echo ""
        echo "🚀 Your free Ampere A1 instance is now running!"
        echo "💡 SSH Access: ssh -i ssh-key-2025-07-27.key ubuntu@$public_ip"
        echo ""
        echo "⚠️  DELETE THIS FILE AFTER READING IT"
        echo ""
        echo "This file was created to ensure you don't miss the success notification."
        echo "The script ran for potentially days/weeks before succeeding!"
        echo ""
        echo "🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉🎉"
    } > "$SUCCESS_NOTIFICATION_FILE"
    
    # Try macOS notification (if available)
    if command -v osascript >/dev/null 2>&1; then
        osascript -e "display notification \"Your Ampere A1 instance is ready! IP: $public_ip\" with title \"🎉 Oracle Cloud Success!\" sound name \"Glass\"" 2>/dev/null || true
    fi
    
    # Create desktop success indicator (visible file)
    local desktop_success_file="$HOME/Desktop/🎉_AMPERE_A1_READY_🎉.txt"
    {
        echo "🎉 AMPERE A1 INSTANCE CREATED!"
        echo "IP: $public_ip"
        echo "Time: $(date)"
        echo "Check: $SUCCESS_NOTIFICATION_FILE"
    } > "$desktop_success_file" 2>/dev/null || true
    
    log_success "📢 Success notifications sent - check desktop and project folder!"
}

# Update tracking document with current attempt
update_tracker() {
    local attempt="$1"
    local ad="$2"
    local status="$3"
    local message="$4"
    
    # Append to tracking document
    {
        echo ""
        echo "**Attempt $attempt** ($(date '+%Y-%m-%d %H:%M:%S UTC')): $ad - $status"
        echo "$message"
    } >> "$TRACKER_FILE"
}

# Calculate dynamic retry interval based on attempt number and failures
calculate_retry_interval() {
    local attempt="$1"
    local consecutive_failures="$2"
    local rate_limited="$3"
    
    # If rate limited, use long backoff
    if [[ "$rate_limited" == "true" ]]; then
        echo $RATE_LIMIT_BACKOFF
        return
    fi
    
    local base_interval
    
    # Dynamic interval based on attempt number (using integer arithmetic)
    if [[ $attempt -le $INITIAL_FAST_ATTEMPTS ]]; then
        # Fast initial attempts (2 minutes)
        base_interval=$MIN_RETRY_INTERVAL
        # Note: Log message moved outside function to avoid mixing with return value
    elif [[ $attempt -le $MEDIUM_ATTEMPTS ]]; then
        # Gradual increase for medium attempts (simple linear increase)
        local extra_attempts=$((attempt - INITIAL_FAST_ATTEMPTS))
        local increase=$((extra_attempts * 30))  # 30 seconds per attempt over fast threshold
        base_interval=$((MIN_RETRY_INTERVAL + increase))
    else
        # Long-term attempts with larger intervals
        base_interval=$((MIN_RETRY_INTERVAL * 3))  # 6 minutes for long-term attempts
    fi
    
    # Apply consecutive failure backoff (simple doubling after 5 failures)
    if [[ $consecutive_failures -gt 5 ]]; then
        local extra_failures=$((consecutive_failures - 5))
        local backoff_multiplier=$((2 ** (extra_failures > 3 ? 3 : extra_failures)))  # Cap at 2^3 = 8x
        base_interval=$((base_interval * backoff_multiplier))
    fi
    
    # Cap at maximum
    if [[ $base_interval -gt $MAX_RETRY_INTERVAL ]]; then
        base_interval=$MAX_RETRY_INTERVAL
    fi
    
    # Add randomization (±30 seconds)
    local random_offset=$((RANDOM % (2 * RANDOMIZE_SECONDS + 1) - RANDOMIZE_SECONDS))
    local final_interval=$((base_interval + random_offset))
    
    # Ensure minimum
    if [[ $final_interval -lt $MIN_RETRY_INTERVAL ]]; then
        final_interval=$MIN_RETRY_INTERVAL
    fi
    
    echo $final_interval
}

# Check if current time is optimal for attempts (off-peak hours)
is_optimal_time() {
    local current_hour=$(date '+%H' | sed 's/^0*//')  # Remove leading zeros
    
    # Off-peak hours: 2-6 AM UTC (better success rates reported by community)
    if [[ $current_hour -ge 2 && $current_hour -le 6 ]]; then
        return 0  # true
    fi
    
    return 1  # false
}

# Detect rate limiting from error output
is_rate_limited() {
    local error_output="$1"
    
    if echo "$error_output" | grep -q "429\|TooManyRequests\|Rate limit\|Too many requests"; then
        return 0  # true
    fi
    
    return 1  # false
}

# Get human-readable time format
format_time() {
    local seconds="$1"
    local minutes=$((seconds / 60))
    local remaining_seconds=$((seconds % 60))
    
    if [[ $minutes -gt 0 ]]; then
        echo "${minutes}m ${remaining_seconds}s"
    else
        echo "${remaining_seconds}s"
    fi
}

# Check if OCI CLI is configured
check_prerequisites() {
    log_info "Checking prerequisites..."
    
    if ! command -v oci >/dev/null 2>&1; then
        log_error "OCI CLI not found. Please install Oracle Cloud CLI first."
        exit 1
    fi
    
    if [[ ! -f "$SSH_KEY_FILE" ]]; then
        log_error "SSH key file not found: $SSH_KEY_FILE"
        exit 1
    fi
    
    # Test OCI authentication  
    export SUPPRESS_LABEL_WARNING=True
    if ! oci iam user get --user-id "ocid1.user.oc1..aaaaaaaaf4pjnftjw26atorkivciy5l2bk3ermyokhwcci72vpapx72fch2a" >/dev/null 2>&1; then
        log_error "OCI CLI authentication failed. Please configure OCI CLI first."
        exit 1
    fi
    
    log_info "Prerequisites check passed"
}

# Create instance in specific availability domain
create_instance() {
    local ad="$1"
    local attempt="$2"
    
    log_info "Attempt $attempt: Trying to create Ampere A1 instance in $ad"
    
    # Suppress OCI CLI warnings
    export SUPPRESS_LABEL_WARNING=True
    
    # Build OCI command
    local cmd=(
        oci compute instance launch
        --availability-domain "$ad"
        --compartment-id "$COMPARTMENT_ID"
        --shape "$SHAPE"
        --shape-config "{\"ocpus\": $OCPUS, \"memoryInGBs\": $MEMORY_GB}"
        --image-id "$IMAGE_ID"
        --subnet-id "$SUBNET_ID"
        --display-name "$INSTANCE_NAME"
        --assign-public-ip true
        --ssh-authorized-keys-file "$SSH_KEY_FILE"
        --boot-volume-size-in-gbs "$BOOT_VOLUME_GB"
        --wait-for-state RUNNING
        --max-wait-seconds 300
    )
    
    # Execute command and capture output
    local output
    if output=$("${cmd[@]}" 2>&1); then
        # Success!
        log_success "Instance created successfully in $ad!"
        
        # Save success details
        echo "$output" > "$SUCCESS_FILE"
        
        # Extract key information
        local instance_id
        instance_id=$(echo "$output" | grep -o 'ocid1\.instance[^"]*' | head -1)
        
        local public_ip
        public_ip=$(echo "$output" | grep -o '"public-ip": "[^"]*"' | cut -d'"' -f4 | head -1)
        if [[ -z "$public_ip" ]]; then
            public_ip="(extracting...)"
        fi
        
        log_success "Instance ID: $instance_id"
        log_success "Public IP: $public_ip"
        update_tracker "$attempt" "$ad" "✅ SUCCESS" "Instance created: $instance_id, IP: $public_ip"
        
        # Send unmissable success notifications
        send_success_notifications "$instance_id" "$public_ip" "$attempt"
        
        return 0
    else
        # Failed - analyze error
        local error_type="Unknown"
        local retry_recommended=true
        
        if echo "$output" | grep -q "Out of host capacity"; then
            error_type="Out of host capacity"
        elif echo "$output" | grep -q "NotAuthorizedOrNotFound"; then
            error_type="Authorization/Subnet error"
        elif echo "$output" | grep -q "RequestException"; then
            error_type="Connection timeout"
        elif echo "$output" | grep -q "InternalError"; then
            error_type="Oracle internal error"
        elif echo "$output" | grep -q "ServiceLimit"; then
            error_type="Service limit exceeded"
            retry_recommended=false
        fi
        
        log_warn "Attempt $attempt failed in $ad: $error_type"
        update_tracker "$attempt" "$ad" "❌ FAILED" "$error_type"
        
        # Return appropriate code
        if [[ "$retry_recommended" == "true" ]]; then
            return 1  # Temporary failure, retry
        else
            return 2  # Permanent failure, don't retry
        fi
    fi
}

# Main retry loop
main() {
    log_info "=== Oracle Cloud Ampere A1 Automated Creator Started ==="
    log_info "Instance configuration: $OCPUS OCPUs, ${MEMORY_GB}GB RAM, ${BOOT_VOLUME_GB}GB storage"
    local max_display=$([ $MAX_ATTEMPTS -eq 0 ] && echo "unlimited" || echo "$MAX_ATTEMPTS")
    log_info "Max attempts: $max_display, Dynamic intervals: ${MIN_RETRY_INTERVAL}-${MAX_RETRY_INTERVAL}s"
    log_info "Log file: $LOG_FILE"
    
    check_prerequisites
    
    # Initialize tracking
    {
        echo ""
        echo "---"
        echo ""
        echo "## 🤖 Automated Ampere A1 Creation Log"
        echo ""
        echo "**Started**: $(date '+%Y-%m-%d %H:%M:%S UTC')"
        echo "**Configuration**: $OCPUS OCPUs, ${MEMORY_GB}GB RAM, ${BOOT_VOLUME_GB}GB storage"
        echo "**Target ADs**: ${AVAILABILITY_DOMAINS[*]}"
        echo "**Strategy**: Dynamic intervals (${MIN_RETRY_INTERVAL}-${MAX_RETRY_INTERVAL}s), unlimited attempts"
        echo ""
    } >> "$TRACKER_FILE"
    
    local attempt=1
    local consecutive_failures=0
    local rate_limited=false
    
    # Main retry loop (unlimited if MAX_ATTEMPTS = 0)
    while [[ $MAX_ATTEMPTS -eq 0 || $attempt -le $MAX_ATTEMPTS ]]; do
        local max_display=$([ $MAX_ATTEMPTS -eq 0 ] && echo "∞" || echo "$MAX_ATTEMPTS")
        log_info "=== Attempt $attempt of $max_display ==="
        
        # Check if current time is optimal
        if is_optimal_time; then
            log_info "✨ Optimal time detected (off-peak hours) - better success chances"
        fi
        
        # Try each availability domain
        local success=false
        for ad in "${AVAILABILITY_DOMAINS[@]}"; do
            if create_instance "$ad" "$attempt"; then
                success=true
                break
            fi
            
            # Small delay between AD attempts  
            sleep 10
        done
        
        if [[ "$success" == "true" ]]; then
            log_success "🎉 Ampere A1 instance created successfully!"
            
            # Update final tracking status
            {
                echo ""
                echo "**🎉 FINAL RESULT: SUCCESS** (Attempt $attempt of $max_display)"
                echo "**Completed**: $(date '+%Y-%m-%d %H:%M:%S UTC')"
            } >> "$TRACKER_FILE"
            
            exit 0
        fi
        
        # All ADs failed for this attempt
        consecutive_failures=$((consecutive_failures + 1))
        
        # Only check MAX_ATTEMPTS limit if it's not 0 (unlimited)
        if [[ $MAX_ATTEMPTS -ne 0 && $attempt -ge $MAX_ATTEMPTS ]]; then
            log_error "Maximum attempts reached. Giving up."
            
            # Update final tracking status
            {
                echo ""
                echo "**❌ FINAL RESULT: FAILED** (All $MAX_ATTEMPTS attempts exhausted)"
                echo "**Stopped**: $(date '+%Y-%m-%d %H:%M:%S UTC')"
            } >> "$TRACKER_FILE"
            
            exit 1
        fi
        
        # Calculate dynamic retry interval
        local retry_interval
        retry_interval=$(calculate_retry_interval "$attempt" "$consecutive_failures" "$rate_limited")
        
        # Reset rate limiting after backoff period
        if [[ "$rate_limited" == "true" && $retry_interval -lt $RATE_LIMIT_BACKOFF ]]; then
            rate_limited=false
        fi
        
        local time_display
        time_display=$(format_time "$retry_interval")
        
        log_info "All availability domains failed. Waiting $time_display before next attempt..."
        log_info "Next attempt: $((attempt + 1)) of $max_display (consecutive failures: $consecutive_failures)"
        
        # Show strategy info for first few attempts
        if [[ $attempt -le 3 ]]; then
            if [[ $attempt -le $INITIAL_FAST_ATTEMPTS ]]; then
                log_info "Using aggressive retry strategy (attempt $attempt/$INITIAL_FAST_ATTEMPTS)"
            fi
        fi
        
        sleep "$retry_interval"
        attempt=$((attempt + 1))
    done
}

# Handle script termination
cleanup() {
    log_info "Script terminated. Cleaning up..."
    {
        echo ""
        echo "**⚠️ SCRIPT TERMINATED** ($(date '+%Y-%m-%d %H:%M:%S UTC'))"
    } >> "$TRACKER_FILE"
}

trap cleanup EXIT

# =============================================================================
# Script Execution
# =============================================================================

# Check if running as background process
if [[ "${1:-}" == "--background" ]]; then
    log_info "Running in background mode"
    nohup "$0" > "$LOG_FILE" 2>&1 &
    echo "Background process started. PID: $!"
    echo "Monitor progress: tail -f $LOG_FILE"
    echo "Check status: ps aux | grep ampere_a1_auto_creator"
    exit 0
fi

# Run main function
main "$@"