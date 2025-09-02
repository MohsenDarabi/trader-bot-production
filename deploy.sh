#!/bin/bash

# Enhanced Trading Bot Deployment Script with Cross-Platform Build Support
# Usage: ./deploy.sh [SYMBOL] [ACTION] [opt]

set -e  # Exit on any error

# Configuration
VM_USER="ubuntu"
VM_HOST="89.168.111.195"
SSH_KEY="./ssh-key-2025-07-27.key"
VM_DIR="/home/ubuntu/trader-bot-production"

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
    echo "Enhanced Trading Bot Deployment Script"
    echo "Usage: $0 [SYMBOL] [ACTION] [opt]"
    echo ""
    echo "SYMBOL:"
    echo "  Any 3-8 letter crypto symbol (e.g. ada, eth, btc, aave, doge)"
    echo "  Automatically uses available credentials or creates .env file"
    echo ""
    echo "ACTION:"
    echo "  stop    - Stop specified bot(s)"
    echo "  update  - Update code and restart bot(s)"
    echo "  restart - Restart bot(s) without code update"
    echo ""
    echo "OPTIMIZATION (optional):"
    echo "  opt     - Use optimized Docker build (70% smaller image)"
    echo "            Standard: ~400-500MB, Optimized: ~100-150MB"
    echo ""
    echo "Credential Management:"
    echo "  • Script automatically finds unused credentials from VM"
    echo "  • Creates .env files by borrowing from non-running bots"
    echo "  • Updates trading market automatically (e.g. AAVE → AAVEUSDT)"
    echo ""
    echo "Examples:"
    echo "  $0 ada update       # Standard deployment (~400-500MB image)"
    echo "  $0 ada update opt   # Optimized deployment (~100-150MB image)"
    echo "  $0 aave restart     # Restart with standard image"
    echo "  $0 aave restart opt # Restart with optimized image"
    echo "  $0 btc stop         # Stop BTC bot"
}

# Function to validate inputs
validate_inputs() {
    # Accept any 3-8 character lowercase asset name (typical crypto symbols)
    if [[ ! "$1" =~ ^[a-z]{3,8}$ ]]; then
        print_error "Invalid symbol: $1 (must be 3-8 lowercase letters, e.g. ada, eth, btc, aave)"
        show_usage
        exit 1
    fi
    
    if [[ ! "$2" =~ ^(stop|update|restart)$ ]]; then
        print_error "Invalid action: $2"
        show_usage
        exit 1
    fi
    
    # Validate optional third parameter (optimization flag)
    if [[ -n "$3" && "$3" != "opt" ]]; then
        print_error "Invalid optimization flag: $3 (must be 'opt' or omitted)"
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
    print_status "Cleaning up old ${symbol} Docker images on VM..."
    
    # Get list of old images (keep newest 2 for safety)
    local old_images=$(ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$VM_USER@$VM_HOST" \
        "docker images --format 'table {{.Repository}}\t{{.Tag}}\t{{.ID}}\t{{.CreatedAt}}' | \
         grep '${symbol}-bot' | tail -n +3 | awk '{print \$3}'" 2>/dev/null || echo "")
    
    if [[ -n "$old_images" ]]; then
        local count=$(echo "$old_images" | wc -l)
        print_status "Found $count old ${symbol} images to remove..."
        
        echo "$old_images" | while read image_id; do
            if [[ -n "$image_id" ]]; then
                ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$VM_USER@$VM_HOST" \
                    "docker rmi $image_id 2>/dev/null || true"
            fi
        done
        
        print_success "Cleaned up old ${symbol} images"
    else
        print_status "No old ${symbol} images to clean"
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
    local optimization="$2"
    local timestamp=$(date '+%Y%m%d_%H%M%S')
    
    # Choose Dockerfile and image naming based on optimization flag
    local dockerfile="Dockerfile"
    local image_name="${symbol}-bot-${timestamp}"
    local build_type="standard"
    
    if [[ "$optimization" == "opt" ]]; then
        dockerfile="Dockerfile.optimized"
        image_name="${symbol}-bot-opt-${timestamp}"
        build_type="optimized"
    fi
    
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
    local optimization="$2"  # New optimization parameter
    local image_name="ada-bot-fixed:latest"  # Default fallback
    
    # Get the image name from last build
    if [[ -f ".last_built_${symbol}" ]]; then
        image_name=$(cat ".last_built_${symbol}")
        rm -f ".last_built_${symbol}"
    fi
    
    # CRITICAL: Use correct env file for each bot
    local env_file=".env.${symbol}"
    
    print_status "Verifying ${env_file} exists on VM..."
    if ! ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$VM_USER@$VM_HOST" \
        "test -f $VM_DIR/${env_file}"; then
        print_warning "Environment file ${env_file} not found on VM!"
        
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
    
    print_status "Starting ${symbol} container with ${env_file}..."
    local market=$(echo "${symbol}" | tr '[:lower:]' '[:upper:]')USDT
    
    # Conditional docker run command based on optimization flag
    local docker_cmd
    if [[ "$optimization" == "opt" ]]; then
        # Optimized image uses ENTRYPOINT, only pass market parameter
        docker_cmd="cd $VM_DIR && docker run -d --name ${symbol} \
         --env-file ${env_file} \
         --restart always \
         --memory=128m \
         --cpus=0.25 \
         ${image_name} \
         ${market}"
        print_status "Using optimized deployment command (entrypoint-based)"
    else
        # Standard image, pass full python command
        docker_cmd="cd $VM_DIR && docker run -d --name ${symbol} \
         --env-file ${env_file} \
         --restart always \
         --memory=128m \
         --cpus=0.25 \
         ${image_name} \
         python main.py ${market}"
        print_status "Using standard deployment command (direct python)"
    fi
    
    local container_id=$(ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$VM_USER@$VM_HOST" "${docker_cmd}")
    
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
    print_status "Stopping $symbol container..."
    
    ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$VM_USER@$VM_HOST" \
        "docker stop ${symbol} 2>/dev/null && docker rm ${symbol} 2>/dev/null || true"
    
    print_success "$symbol container stopped"
}

# Main execution function
execute_deployment() {
    local symbol="$1"
    local action="$2"
    local optimization="$3"
    
    local deployment_type="standard"
    if [[ "$optimization" == "opt" ]]; then
        deployment_type="optimized"
    fi
    
    print_status "Starting deployment: $symbol $action ($deployment_type)"
    
    # Ensure Docker is running
    ensure_docker_running
    
    # Clean up old builds before starting
    cleanup_old_builds
    
    # Clean up old VM archives to free disk space
    cleanup_old_vm_archives
    
    case "$action" in
        "stop")
            stop_containers "$symbol"
            ;;
        "update")
            print_success "Using local Docker buildx for cross-platform build ($deployment_type)"
            if build_image_locally "$symbol" "$optimization"; then
                restart_containers "$symbol" "$optimization"
                cleanup_old_vm_images "$symbol"
                print_success "Deployment completed successfully!"
            else
                print_error "Build failed"
                exit 1
            fi
            ;;
        "restart")
            restart_containers "$symbol" "$optimization"
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
    
    # Validate inputs
    validate_inputs "$1" "$2" "$3"
    
    # Check prerequisites
    check_prerequisites
    
    # Execute deployment with cleanup
    execute_deployment "$1" "$2" "$3"
}

# Run main function with all arguments
main "$@"