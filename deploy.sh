#!/bin/bash

# Enhanced Trading Bot Deployment Script with Interval and Position Sizing Support
# Usage: ./deploy.sh [interval] [symbol] [action] --instance [first|second] [position_percent]

set -e  # Exit on any error

# Instance Configuration
VM_USER="ubuntu"
SSH_KEY="/Users/mohsendarabi/Desktop/workspace/trader-bot-production/ssh-key-2025-07-27.key"
VM_DIR="/home/ubuntu/trader-bot-production"

# Global variables set by parse_arguments
SELECTED_INSTANCE=""
VM_HOST=""

# Function to get VM host by instance name
get_vm_host() {
    case "$1" in
        "first")
            echo "89.168.111.195"
            ;;
        "second")
            echo "92.5.15.61"
            ;;
        *)
            echo ""
            ;;
    esac
}

# ENHANCED: Exclude build artifacts and temporary files
LOCAL_EXCLUDE=".git,.gitignore,logs/*,*.tar.gz,*.tar,__pycache__,*.pyc,env-templates,vm-deploy-*.sh,*-bot-*.tar.gz,ada-bot-*,eth-bot-*"

# Transfer timeout configuration (in seconds)
TRANSFER_TIMEOUT=900  # 15 minutes for large files
SMALL_FILE_TIMEOUT=120  # 2 minutes for small operations

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${BLUE}[$(date '+%H:%M:%S')]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[$(date '+%H:%M:%S')] ✅${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[$(date '+%H:%M:%S')] ⚠️${NC} $1"
}

print_error() {
    echo -e "${RED}[$(date '+%H:%M:%S')] ❌${NC} $1"
}

# Function to show usage
show_usage() {
    echo "Enhanced Trading Bot Deployment Script with Interval and Position Sizing Support"
    echo "Usage: $0 [INTERVAL] [SYMBOL] [ACTION] --instance [INSTANCE] [POSITION_PERCENT]"
    echo ""
    echo "INTERVAL:"
    echo "  hourly  - Trade every hour (1-2.5% position sizing)"
    echo "  daily   - Trade every day (10-12% position sizing)"
    echo ""
    echo "SYMBOL:"
    echo "  Any 3-8 letter crypto symbol (e.g. ada, eth, btc, aave, sui)"
    echo ""
    echo "ACTION:"
    echo "  stop    - Stop specified bot"
    echo "  update  - Update code and restart bot"
    echo "  restart - Restart bot without code update"
    echo "  setup   - Initialize instance with directory structure"
    echo "  status  - Show running bots on instance"
    echo ""
    echo "INSTANCE (REQUIRED):"
    echo "  --instance first   - Deploy to first instance (89.168.111.195)"
    echo "  --instance second  - Deploy to second instance (92.5.15.61)"
    echo ""
    echo "POSITION_PERCENT (optional):"
    echo "  Decimal number for position size (e.g. 1.5 for 1.5%)"
    echo "  Defaults: hourly=1.0%, daily=10.0%"
    echo ""
    echo "Instance Usage Strategy:"
    echo "  • Each instance should run DIFFERENT bots to avoid conflicts"
    echo "  • Hourly bots: smaller positions, more frequent trades"
    echo "  • Daily bots: larger positions, once per day trades"
    echo ""
    echo "Examples:"
    echo "  $0 setup --instance second                    # Initialize second instance"
    echo "  $0 hourly sui update --instance second        # Deploy hourly SUI bot (1% default)"
    echo "  $0 hourly sui update --instance second 1.5    # Deploy hourly SUI bot (1.5% positions)"
    echo "  $0 daily ada update --instance first          # Deploy daily ADA bot (10% default)"
    echo "  $0 daily ada update --instance first 12.0     # Deploy daily ADA bot (12% positions)"
    echo "  $0 status --instance first                     # Check what's running on first instance"
    echo "  $0 hourly sui stop --instance second          # Stop hourly SUI bot"
    echo ""
    echo "Note: All deployments now use optimized Docker builds by default"
    echo "ERROR: --instance parameter is REQUIRED to prevent accidental deployments!"
}

# Function to parse arguments and set global variables
parse_arguments() {
    local interval=""
    local symbol=""
    local action=""
    local instance=""
    local position_percent=""
    local instance_found=false
    
    # Parse all arguments
    while [[ $# -gt 0 ]]; do
        case $1 in
            --instance)
                if [[ -n "$2" && "$2" =~ ^(first|second)$ ]]; then
                    instance="$2"
                    instance_found=true
                    shift 2
                else
                    print_error "Invalid --instance value: $2 (must be 'first' or 'second')"
                    show_usage
                    exit 1
                fi
                ;;
            setup|status|stop|update|restart)
                action="$1"
                shift
                ;;
            hourly|daily)
                interval="$1"
                shift
                ;;
            *)
                # Check if it's a decimal number for position percent
                if [[ "$1" =~ ^[0-9]+\.?[0-9]*$ ]]; then
                    position_percent="$1"
                    shift
                elif [[ -z "$symbol" && "$1" =~ ^[a-z]{3,8}$ ]]; then
                    symbol="$1"
                    shift
                elif [[ -z "$action" && "$1" =~ ^(setup|status)$ ]]; then
                    action="$1"
                    shift
                else
                    print_error "Invalid argument: $1"
                    show_usage
                    exit 1
                fi
                ;;
        esac
    done
    
    # Validate required --instance parameter
    if [[ "$instance_found" != true ]]; then
        print_error "ERROR: --instance parameter is REQUIRED!"
        print_error "You must specify either --instance first or --instance second"
        print_error "This prevents accidental deployments to the wrong VM."
        show_usage
        exit 1
    fi
    
    # Set defaults: if interval is missing, default to daily
    if [[ -z "$interval" ]]; then
        interval="daily"
    fi
    
    # Set defaults for position_percent based on interval
    if [[ -z "$position_percent" ]]; then
        if [[ "$interval" == "hourly" ]]; then
            position_percent="1.0"
        elif [[ "$interval" == "daily" ]]; then
            position_percent="10.0"
        else
            position_percent="1.0"  # Default fallback (hourly - safer default)
        fi
    fi
    
    # Output shell commands to set global variables
    echo "SELECTED_INSTANCE='$instance'"
    echo "VM_HOST='$(get_vm_host "$instance")'"
    echo "interval='$interval'"
    echo "symbol='$symbol'"
    echo "action='$action'"
    echo "position_percent='$position_percent'"
    
    # Print status message to stderr
    print_status "Selected instance: $instance ($(get_vm_host "$instance"))" >&2
}

# Function to validate inputs
validate_inputs() {
    local interval="$1"
    local symbol="$2"
    local action="$3"
    local position_percent="$4"
    
    # Validate action
    if [[ ! "$action" =~ ^(stop|update|restart|setup|status)$ ]]; then
        print_error "Invalid action: $action"
        show_usage
        exit 1
    fi
    
    # For setup and status, symbol and interval are optional
    if [[ "$action" =~ ^(setup|status)$ ]]; then
        return 0
    fi
    
    # For other actions, symbol is required
    if [[ ! "$symbol" =~ ^[a-z]{3,8}$ ]]; then
        print_error "Invalid symbol: $symbol (must be 3-8 lowercase letters, e.g. ada, eth, btc, aave, sui)"
        show_usage
        exit 1
    fi
    
    # Validate interval (should always be set by defaults, but double-check)
    if [[ -n "$interval" && ! "$interval" =~ ^(hourly|daily)$ ]]; then
        print_error "Invalid interval: $interval (must be 'hourly' or 'daily')"
        show_usage
        exit 1
    fi
    
    # Validate position percent
    if [[ -n "$position_percent" && ! "$position_percent" =~ ^[0-9]+\.?[0-9]*$ ]]; then
        print_error "Invalid position percent: $position_percent (must be a decimal number, e.g. 1.5)"
        show_usage
        exit 1
    fi
}

# Function to ensure Docker is running
ensure_docker_running() {
    print_status "Checking Docker status..."
    
    if ! docker info &>/dev/null; then
        print_warning "Docker is not running. Starting Docker..."
        
        # macOS Docker Desktop
        if [[ "$OSTYPE" == "darwin"* ]]; then
            open -a Docker
            print_status "Waiting for Docker to start..."
            
            # Wait up to 60 seconds for Docker to start
            local count=0
            while ! docker info &>/dev/null && [ $count -lt 60 ]; do
                sleep 1
                count=$((count + 1))
                echo -n "."
            done
            echo ""
            
            if docker info &>/dev/null; then
                print_success "Docker started successfully"
            else
                print_error "Failed to start Docker. Please start Docker manually."
                exit 1
            fi
        else
            # Linux
            print_error "Docker is not running. Please start Docker service:"
            print_error "  sudo systemctl start docker"
            exit 1
        fi
    else
        print_success "Docker is running"
    fi
}

# Function to clean up old build artifacts
cleanup_old_builds() {
    print_status "Cleaning up old build artifacts..."
    
    # Remove old compressed builds
    rm -f ada-bot-*.tar.gz eth-bot-*.tar.gz 2>/dev/null || true
    rm -f daily-range-bot*.tar* 2>/dev/null || true
    rm -f trader-bot-update-*.tar.gz 2>/dev/null || true
    rm -f vm-deploy-*.sh 2>/dev/null || true
    
    print_success "Old build artifacts cleaned"
}

# Function to clean up old Docker images on VM
cleanup_old_vm_images() {
    local symbol="$1"
    print_status "Cleaning up old ${symbol} Docker images on $SELECTED_INSTANCE instance..."
    
    # Get list of old images (keep newest 2 for safety)
    local old_images=$(ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$VM_USER@$VM_HOST" \
        "docker images --format 'table {{.Repository}}\t{{.Tag}}\t{{.ID}}\t{{.CreatedAt}}' | \
         grep '${symbol}-bot' | tail -n +3 | awk '{print \$3}'" 2>/dev/null || echo "")
    
    if [[ -n "$old_images" ]]; then
        local count=$(echo "$old_images" | wc -l)
        print_status "Found $count old ${symbol} images to remove on $SELECTED_INSTANCE instance..."
        
        echo "$old_images" | while read image_id; do
            if [[ -n "$image_id" ]]; then
                ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$VM_USER@$VM_HOST" \
                    "docker rmi $image_id 2>/dev/null || true"
            fi
        done
        
        print_success "Cleaned up old ${symbol} images on $SELECTED_INSTANCE instance"
    else
        print_status "No old ${symbol} images to clean on $SELECTED_INSTANCE instance"
    fi
}

# Function to create missing .env file using available VM credentials
create_missing_env_from_vm() {
    local symbol="$1"
    local env_file=".env.${symbol}"
    local market="${symbol^^}USDT"  # Convert to uppercase + USDT
    
    print_status "Missing ${env_file} - checking for available credentials on VM..."
    
    # Get list of running containers on VM
    local running_bots=$(ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$VM_USER@$VM_HOST" \
        "docker ps --format '{{.Names}}' 2>/dev/null" || echo "")
    
    if [[ -n "$running_bots" ]]; then
        print_status "Currently running bots on VM: $running_bots"
    else
        print_status "No bots currently running on VM"
    fi
    
    # Get available .env files on VM
    local available_envs=$(ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$VM_USER@$VM_HOST" \
        "cd '$VM_DIR' 2>/dev/null && find . -name '.env.*' -maxdepth 1 2>/dev/null | sed 's|./||' | sort" || echo "")
    
    if [[ -z "$available_envs" ]]; then
        print_error "No .env files found on VM at $VM_DIR"
        print_error "Please create at least one .env file (e.g. .env.ada) with valid credentials"
        exit 1
    fi
    
    print_status "Available .env files on VM: $available_envs"
    
    # Find unused credential set (where corresponding bot is not running)
    local source_env=""
    while IFS= read -r env_file_vm; do
        if [[ "$env_file_vm" =~ ^\.env\.(.+)$ ]]; then
            local bot_name="${BASH_REMATCH[1]}"
            # Skip if this is the target env we're trying to create
            if [[ "$bot_name" == "$symbol" ]]; then
                continue
            fi
            # Check if this bot is not currently running
            if [[ ! "$running_bots" =~ (^|[[:space:]])${bot_name}([[:space:]]|$) ]]; then
                source_env="$env_file_vm"
                print_success "Found unused credentials: $source_env (bot '$bot_name' not running)"
                break
            fi
        fi
    done <<< "$available_envs"
    
    if [[ -n "$source_env" ]]; then
        print_status "Creating ${env_file} on VM using credentials from $source_env..."
        
        # Copy and modify on VM
        if ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$VM_USER@$VM_HOST" \
            "cd '$VM_DIR' && cp '$source_env' '$env_file' && \
             sed -i 's/^DEFAULT_TRADING_MARKET=.*/DEFAULT_TRADING_MARKET=$market/' '$env_file'"; then
            print_success "Created $env_file on VM with trading market: $market"
            return 0
        else
            print_error "Failed to create $env_file on VM"
            return 1
        fi
    else
        print_error "No unused credentials available on VM."
        print_error "All available credential sets are currently in use by running bots:"
        echo "$running_bots"
        print_error ""
        print_error "To fix this, either:"
        print_error "1. Stop an unused bot: ./deploy.sh SYMBOL stop"
        print_error "2. Create new .env file manually with different API credentials"
        exit 1
    fi
}

# Function to safely clean up old compressed archives on VM (ONLY tar.gz files)
cleanup_old_vm_archives() {
    print_status "Cleaning up old compressed archives on VM..."
    
    # SAFE: Only remove specific compressed archive patterns
    # This preserves .env files, source code, logs, and other important files
    ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$VM_USER@$VM_HOST" \
        "rm -f ~/ada-bot-*.tar.gz ~/eth-bot-*.tar.gz ~/trader-bot*.tar.gz ~/daily-range-bot*.tar* 2>/dev/null || true"
    
    # Check remaining disk space after cleanup
    local disk_usage=$(ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$VM_USER@$VM_HOST" \
        "df -h / | tail -1 | awk '{print \$5}'" 2>/dev/null || echo "unknown")
    
    print_success "VM archive cleanup completed (disk usage: ${disk_usage})"
}

# Function to check if buildx is available
check_buildx_support() {
    if docker buildx version &>/dev/null; then
        echo "true"
    else
        echo "false"
    fi
}

# Function to check prerequisites
check_prerequisites() {
    print_status "Checking prerequisites..."
    
    # Check SSH key exists
    if [[ ! -f "$SSH_KEY" ]]; then
        print_error "SSH key not found: $SSH_KEY"
        exit 1
    fi
    
    # Check SSH connectivity
    if ! ssh -i "$SSH_KEY" -o ConnectTimeout=10 -o StrictHostKeyChecking=no "$VM_USER@$VM_HOST" "echo 'Connection OK'" >/dev/null 2>&1; then
        print_error "Cannot connect to VM: $VM_USER@$VM_HOST"
        print_error "Please check your SSH key and VM status"
        exit 1
    fi
    
    # Check buildx support
    if [[ $(check_buildx_support) != "true" ]]; then
        print_error "Docker buildx not available. Please install buildx or update Docker."
        exit 1
    fi
    
    print_success "Prerequisites check passed"
}

# Function to verify file transfer integrity
verify_transfer() {
    local local_file="$1"
    local remote_file="$2"
    
    print_status "Verifying transfer integrity..."
    
    # Get local file size
    local local_size=$(wc -c < "${local_file}")
    
    # Get remote file size
    local remote_size=$(ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$VM_USER@$VM_HOST" \
        "wc -c < ~/${remote_file}" 2>/dev/null || echo "0")
    
    if [[ "$local_size" -eq "$remote_size" && "$remote_size" -gt 0 ]]; then
        print_success "Transfer verified: ${local_size} bytes"
        return 0
    else
        print_error "Transfer verification failed: local=${local_size}, remote=${remote_size}"
        return 1
    fi
}

# Enhanced transfer function with retry and verification
transfer_with_retry() {
    local local_file="$1"
    local remote_file="$2"
    local max_attempts=3
    
    for attempt in $(seq 1 $max_attempts); do
        print_status "Transfer attempt ${attempt}/${max_attempts}..."
        
        # Remove any partial file from previous attempts
        ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$VM_USER@$VM_HOST" \
            "rm -f ~/${remote_file}" 2>/dev/null || true
        
        # Transfer with progress indication and configurable timeout
        local transfer_exit_code=0
        
        # Cross-platform timeout handling
        if command -v gtimeout >/dev/null 2>&1; then
            # macOS with gnu coreutils (brew install coreutils)
            local timeout_cmd="gtimeout"
        elif command -v timeout >/dev/null 2>&1; then
            # Linux/Unix systems
            local timeout_cmd="timeout"
        else
            # Fallback: no timeout, let scp use its own timeout
            local timeout_cmd=""
        fi
        
        if [[ -n "$timeout_cmd" ]]; then
            if $timeout_cmd "${TRANSFER_TIMEOUT}" scp -i "$SSH_KEY" -o StrictHostKeyChecking=no -v \
                -o ConnectTimeout=60 -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
                "${local_file}" "$VM_USER@$VM_HOST:~/${remote_file}"; then
                transfer_exit_code=0
            else
                transfer_exit_code=$?
            fi
        else
            # Fallback without timeout wrapper
            if scp -i "$SSH_KEY" -o StrictHostKeyChecking=no -v \
                -o ConnectTimeout=60 -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
                "${local_file}" "$VM_USER@$VM_HOST:~/${remote_file}"; then
                transfer_exit_code=0
            else
                transfer_exit_code=$?
            fi
        fi
        
        # Handle different failure modes
        if [[ $transfer_exit_code -eq 0 ]]; then
            # Verify transfer integrity
            if verify_transfer "${local_file}" "${remote_file}"; then
                print_success "Transfer completed successfully on attempt ${attempt}"
                return 0
            else
                print_warning "Transfer verification failed on attempt ${attempt}"
                # Clean up corrupted partial file
                ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$VM_USER@$VM_HOST" \
                    "rm -f ~/${remote_file}" 2>/dev/null || true
            fi
        elif [[ $transfer_exit_code -eq 124 ]]; then
            print_warning "Transfer timed out after ${TRANSFER_TIMEOUT} seconds on attempt ${attempt}"
            # Clean up partial file after timeout
            ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$VM_USER@$VM_HOST" \
                "rm -f ~/${remote_file}" 2>/dev/null || true
        else
            print_warning "Transfer failed with exit code ${transfer_exit_code} on attempt ${attempt}"
            # Clean up any partial file
            ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$VM_USER@$VM_HOST" \
                "rm -f ~/${remote_file}" 2>/dev/null || true
        fi
        
        if [[ $attempt -lt $max_attempts ]]; then
            print_status "Retrying in 3 seconds..."
            sleep 3
        fi
    done
    
    print_error "Transfer failed after ${max_attempts} attempts"
    return 1
}

# Enhanced local build function with robust transfer
build_image_locally() {
    local symbol="$1"
    local interval="$2"
    local position_percent="$3"
    local timestamp=$(date '+%Y%m%d_%H%M%S')
    
    # Always use optimized build (default now)
    local dockerfile="Dockerfile.optimized"
    local image_name="${symbol}-bot-${interval}-${timestamp}"
    local build_type="optimized"
    
    local tar_file="${image_name}.tar.gz"
    
    print_status "Building $symbol image locally for linux/amd64 ($build_type build using $dockerfile)..."
    
    # Clean up any existing builds first
    cleanup_old_builds
    
    # Build with buildx for linux/amd64 (--no-cache ensures fresh builds)
    if ! docker buildx build --platform linux/amd64 --no-cache \
        -t "${image_name}:latest" \
        -f "$dockerfile" \
        --load \
        . ; then
        print_error "Failed to build image"
        return 1
    fi
    
    print_success "Image built successfully: ${image_name}:latest"
    
    # Save as compressed tar
    print_status "Compressing image (this may take a minute)..."
    docker save "${image_name}:latest" | gzip > "${tar_file}"
    
    local size=$(ls -lh "${tar_file}" | awk '{print $5}')
    local size_bytes=$(wc -c < "${tar_file}")
    print_success "Image compressed: ${tar_file} (${size})"
    
    # Enhanced transfer with retry and verification
    print_status "Transferring image to VM (${size}, may take several minutes)..."
    if ! transfer_with_retry "${tar_file}" "${tar_file}"; then
        print_error "Failed to transfer image after all attempts"
        rm -f "${tar_file}"
        return 1
    fi
    
    # Load on VM with verification
    print_status "Loading image on VM (this may take a minute)..."
    if ! ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$VM_USER@$VM_HOST" \
        "gunzip -c ~/${tar_file} | docker load"; then
        print_error "Failed to load image on VM"
        rm -f "${tar_file}"
        # Clean up corrupted file on VM
        ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$VM_USER@$VM_HOST" \
            "rm -f ~/${tar_file}" 2>/dev/null || true
        return 1
    fi
    
    # Verify image was loaded correctly
    print_status "Verifying image loaded correctly..."
    if ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$VM_USER@$VM_HOST" \
        "docker images | grep -q '${image_name}'"; then
        print_success "Image verified on VM: ${image_name}:latest"
    else
        print_error "Image verification failed - image not found on VM"
        return 1
    fi
    
    # Clean up files
    print_status "Cleaning up temporary files..."
    rm -f "${tar_file}"
    ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$VM_USER@$VM_HOST" \
        "rm -f ~/${tar_file}" 2>/dev/null || true
    docker rmi "${image_name}:latest" 2>/dev/null || true
    
    print_success "Build and transfer completed successfully"
    
    # Store image name for container restart
    echo "${image_name}:latest" > ".last_built_${symbol}"
}

# Enhanced restart function with proper credential handling and verification
restart_containers() {
    local symbol="$1"
    local interval="$2"
    local position_percent="$3"
    local image_name="${symbol}-bot-${interval}-latest"  # Default fallback
    
    # Get the image name from last build
    if [[ -f ".last_built_${symbol}" ]]; then
        image_name=$(cat ".last_built_${symbol}")
        rm -f ".last_built_${symbol}"
    fi
    
    # CRITICAL: Use correct env file for each bot
    local env_file=".env.${symbol}"
    
    print_status "Verifying ${env_file} exists on $SELECTED_INSTANCE instance ($VM_HOST)..."
    if ! ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$VM_USER@$VM_HOST" \
        "test -f $VM_DIR/${env_file}"; then
        print_warning "Environment file ${env_file} not found on $SELECTED_INSTANCE instance!"
        
        # Try to create it using available credentials
        if create_missing_env_from_vm "$symbol"; then
            print_success "Successfully created ${env_file} using available credentials"
        else
            print_error "Failed to create ${env_file} automatically"
            print_error "Please create ${env_file} manually with correct API credentials"
            exit 1
        fi
    fi
    
    print_success "Found ${env_file} - will use these credentials"
    
    # Verify image exists on VM before attempting to use it
    print_status "Verifying image ${image_name} exists on VM..."
    if ! ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$VM_USER@$VM_HOST" \
        "docker images | grep -q '$(echo ${image_name} | cut -d: -f1)'"; then
        print_error "Image ${image_name} not found on VM!"
        print_error "Please run 'update' action first to build and transfer the image"
        exit 1
    fi
    
    print_success "Image ${image_name} verified on VM"
    
    # Check if container is already running
    local container_running=$(ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$VM_USER@$VM_HOST" \
        "docker ps -q -f name=^${symbol}$" 2>/dev/null || echo "")
    
    if [[ -n "$container_running" ]]; then
        print_status "Stopping running ${symbol} container..."
        ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$VM_USER@$VM_HOST" \
            "docker stop ${symbol}"
    fi
    
    # Remove any existing container (running or stopped)
    local container_exists=$(ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$VM_USER@$VM_HOST" \
        "docker ps -aq -f name=^${symbol}$" 2>/dev/null || echo "")
    
    if [[ -n "$container_exists" ]]; then
        print_status "Removing existing ${symbol} container..."
        ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$VM_USER@$VM_HOST" \
            "docker rm ${symbol}"
    fi
    
    # Update .env file with current interval and position_percent
    print_status "Updating ${env_file} with interval=${interval} and position=${position_percent}%..."
    ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$VM_USER@$VM_HOST" \
        "cd $VM_DIR && \
         sed -i 's/^TRADING_INTERVAL=.*/TRADING_INTERVAL=${interval}/' ${env_file} && \
         sed -i 's/^POSITION_SIZE_PERCENT=.*/POSITION_SIZE_PERCENT=${position_percent}/' ${env_file} && \
         grep -q '^TRADING_INTERVAL=' ${env_file} || echo 'TRADING_INTERVAL=${interval}' >> ${env_file} && \
         grep -q '^POSITION_SIZE_PERCENT=' ${env_file} || echo 'POSITION_SIZE_PERCENT=${position_percent}' >> ${env_file}"
    
    print_status "Starting ${symbol} container with ${env_file} (${interval}, ${position_percent}%)..."
    local market=$(echo "${symbol}" | tr '[:lower:]' '[:upper:]')USDT
    local container_id=$(ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$VM_USER@$VM_HOST" \
        "cd $VM_DIR && docker run -d --name ${symbol} \
         --env-file ${env_file} \
         --restart always \
         --memory=128m \
         --cpus=0.25 \
         ${image_name} \
         python main.py ${market} --interval ${interval} --position-percent ${position_percent}")
    
    if [[ -n "$container_id" ]]; then
        print_success "${symbol} container started with credentials from ${env_file}"
        print_success "Container ID: ${container_id:0:12}"
    else
        print_error "Failed to start ${symbol} container"
        return 1
    fi
    
    # Wait for container to start and verify it's running
    sleep 5
    local running_check=$(ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$VM_USER@$VM_HOST" \
        "docker ps -q -f name=^${symbol}$" 2>/dev/null || echo "")
    
    if [[ -n "$running_check" ]]; then
        print_success "${symbol} container is running successfully"
        
        # Show recent logs
        print_status "Checking ${symbol} bot logs..."
        ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$VM_USER@$VM_HOST" \
            "docker logs --tail 20 ${symbol}"
    else
        print_error "${symbol} container failed to start or exited"
        print_status "Showing container logs for debugging..."
        ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$VM_USER@$VM_HOST" \
            "docker logs ${symbol}" 2>/dev/null || true
        return 1
    fi
}

# Stop containers function
stop_containers() {
    local symbol="$1"
    print_status "Stopping $symbol container on $SELECTED_INSTANCE instance ($VM_HOST)..."
    
    ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$VM_USER@$VM_HOST" \
        "docker stop ${symbol} 2>/dev/null && docker rm ${symbol} 2>/dev/null || true"
    
    print_success "$symbol container stopped on $SELECTED_INSTANCE instance"
}

# Setup instance function
setup_instance() {
    print_status "Setting up $SELECTED_INSTANCE instance ($VM_HOST)..."
    
    # Create directory structure
    print_status "Creating directory structure..."
    ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$VM_USER@$VM_HOST" \
        "mkdir -p $VM_DIR"
    
    # Copy .env files from backup if second instance and no .env files exist
    if [[ "$SELECTED_INSTANCE" == "second" ]]; then
        local existing_envs=$(ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$VM_USER@$VM_HOST" \
            "ls $VM_DIR/.env.* 2>/dev/null | wc -l" || echo "0")
        
        if [[ "$existing_envs" -eq 0 ]]; then
            print_status "Copying .env files from backup to second instance..."
            
            # Create temporary archive locally with just the main .env files
            cd /Users/mohsendarabi/Desktop/workspace/trader-bot-hourly/env-backup
            tar -czf setup-envs.tar.gz .env.ada .env.aave .env.eth .env.example 2>/dev/null || true
            
            # Transfer and extract on second instance
            scp -i "$SSH_KEY" -o StrictHostKeyChecking=no setup-envs.tar.gz "$VM_USER@$VM_HOST:~/"
            ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$VM_USER@$VM_HOST" \
                "cd $VM_DIR && tar -xzf ~/setup-envs.tar.gz && rm -f ~/setup-envs.tar.gz"
            
            # Clean up local temp file
            rm -f setup-envs.tar.gz
            
            print_success ".env files copied to second instance"
        else
            print_status ".env files already exist on second instance (count: $existing_envs)"
        fi
    fi
    
    # Check Docker installation
    print_status "Checking Docker installation..."
    if ! ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$VM_USER@$VM_HOST" "docker --version" &>/dev/null; then
        print_status "Docker not found. Installing Docker..."
        ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$VM_USER@$VM_HOST" \
            "curl -fsSL https://get.docker.com -o get-docker.sh && sudo sh get-docker.sh && sudo usermod -aG docker ubuntu"
        print_status "Docker installed. Please logout and login again, then rerun this command."
        exit 0
    else
        print_success "Docker is installed"
    fi
    
    print_success "Instance setup completed for $SELECTED_INSTANCE ($VM_HOST)"
}

# Status function
show_instance_status() {
    print_status "Checking status of $SELECTED_INSTANCE instance ($VM_HOST)..."
    
    # Check connectivity
    if ! ssh -i "$SSH_KEY" -o ConnectTimeout=10 -o StrictHostKeyChecking=no "$VM_USER@$VM_HOST" "echo 'Connected'" &>/dev/null; then
        print_error "Cannot connect to $SELECTED_INSTANCE instance ($VM_HOST)"
        return 1
    fi
    
    print_success "Connected to $SELECTED_INSTANCE instance ($VM_HOST)"
    
    # Show running containers
    print_status "Running containers:"
    ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$VM_USER@$VM_HOST" \
        "docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'" || print_warning "No containers running"
    
    # Show available .env files
    print_status "Available .env files:"
    ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$VM_USER@$VM_HOST" \
        "ls -la $VM_DIR/.env.* 2>/dev/null || echo 'No .env files found'"
    
    # Show disk usage
    print_status "Disk usage:"
    ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$VM_USER@$VM_HOST" \
        "df -h / | tail -1"
}

# Main execution function
execute_deployment() {
    local interval="$1"
    local symbol="$2"
    local action="$3"
    local position_percent="$4"
    
    local deployment_type="optimized"  # Always optimized now
    
    print_status "Starting operation: $action on $SELECTED_INSTANCE instance ($VM_HOST)"
    
    case "$action" in
        "setup")
            setup_instance
            return 0
            ;;
        "status")
            show_instance_status
            return 0
            ;;
        "stop")
            stop_containers "$symbol"
            return 0
            ;;
        "update")
            print_status "Deployment: ${interval} ${symbol} ${action} (${position_percent}%, ${deployment_type})"
            
            # Ensure Docker is running locally for build
            ensure_docker_running
            
            # Clean up old builds before starting
            cleanup_old_builds
            
            # Clean up old VM archives to free disk space
            cleanup_old_vm_archives
            
            print_success "Using local Docker buildx for cross-platform build (${deployment_type})"
            if build_image_locally "$symbol" "$interval" "$position_percent"; then
                restart_containers "$symbol" "$interval" "$position_percent"
                cleanup_old_vm_images "$symbol"
                print_success "Deployment completed successfully on $SELECTED_INSTANCE instance!"
            else
                print_error "Build failed"
                exit 1
            fi
            ;;
        "restart")
            restart_containers "$symbol" "$interval" "$position_percent"
            ;;
    esac
    
    # Final cleanup
    cleanup_old_builds
    
    print_success "All operations completed. Temporary files cleaned."
}

# Main execution
main() {
    # Check if help is requested
    if [[ "$1" == "-h" ]] || [[ "$1" == "--help" ]] || [[ $# -eq 0 ]]; then
        show_usage
        exit 0
    fi
    
    # Parse arguments and set global variables directly
    eval "$(parse_arguments "$@")"
    
    # Validate inputs
    validate_inputs "$interval" "$symbol" "$action" "$position_percent"
    
    # Check prerequisites (SSH connectivity to selected instance)
    check_prerequisites
    
    # Execute deployment
    execute_deployment "$interval" "$symbol" "$action" "$position_percent"
}

# Run main function with all arguments
main "$@"