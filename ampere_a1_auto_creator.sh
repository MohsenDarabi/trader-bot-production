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

# Retry Configuration
readonly RETRY_INTERVAL_SECONDS=300  # 5 minutes
readonly MAX_ATTEMPTS=1440           # 5 days (1440 * 5 minutes)
readonly BACKOFF_MULTIPLIER=1.2      # Increase interval by 20% after failures
readonly MAX_INTERVAL_SECONDS=1800   # Maximum 30 minutes between retries

# Logging Configuration
readonly LOG_FILE="/Users/mohsendarabi/Desktop/workspace/trader-bot-hourly/ampere_creation.log"
readonly SUCCESS_FILE="/Users/mohsendarabi/Desktop/workspace/trader-bot-hourly/ampere_creation_success.json"

# =============================================================================
# Utility Functions
# =============================================================================

log() {
    local level="$1"
    shift
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S UTC')
    local message="[$timestamp] [$level] $*"
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
        
        log_success "Instance ID: $instance_id"
        update_tracker "$attempt" "$ad" "✅ SUCCESS" "Instance created: $instance_id"
        
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
    log_info "Max attempts: $MAX_ATTEMPTS, Interval: ${RETRY_INTERVAL_SECONDS}s"
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
        echo ""
    } >> "$TRACKER_FILE"
    
    local attempt=1
    local current_interval=$RETRY_INTERVAL_SECONDS
    local consecutive_failures=0
    
    while [[ $attempt -le $MAX_ATTEMPTS ]]; do
        log_info "=== Attempt $attempt of $MAX_ATTEMPTS ==="
        
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
                echo "**🎉 FINAL RESULT: SUCCESS** (Attempt $attempt of $MAX_ATTEMPTS)"
                echo "**Completed**: $(date '+%Y-%m-%d %H:%M:%S UTC')"
            } >> "$TRACKER_FILE"
            
            exit 0
        fi
        
        # All ADs failed for this attempt
        consecutive_failures=$((consecutive_failures + 1))
        
        # Check if we should continue
        if [[ $attempt -ge $MAX_ATTEMPTS ]]; then
            log_error "Maximum attempts reached. Giving up."
            
            # Update final tracking status
            {
                echo ""
                echo "**❌ FINAL RESULT: FAILED** (All $MAX_ATTEMPTS attempts exhausted)"
                echo "**Stopped**: $(date '+%Y-%m-%d %H:%M:%S UTC')"
            } >> "$TRACKER_FILE"
            
            exit 1
        fi
        
        # Calculate next retry interval (with exponential backoff)
        if [[ $consecutive_failures -gt 3 ]]; then
            current_interval=$(echo "$current_interval * $BACKOFF_MULTIPLIER" | bc -l | cut -d. -f1)
            if [[ $current_interval -gt $MAX_INTERVAL_SECONDS ]]; then
                current_interval=$MAX_INTERVAL_SECONDS
            fi
        fi
        
        log_info "All availability domains failed. Waiting ${current_interval}s before next attempt..."
        log_info "Next attempt: $((attempt + 1)) of $MAX_ATTEMPTS"
        
        sleep "$current_interval"
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