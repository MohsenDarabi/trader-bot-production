#!/bin/bash

# Enhanced Trading Bot Deployment Script with Cross-Platform Build Support
# Usage: ./deploy.sh [ada|eth] [stop|update|restart]

set -e  # Exit on any error

# Configuration
VM_USER="ubuntu"
VM_HOST="89.168.111.195"
SSH_KEY="./ssh-key-2025-07-27.key"
VM_DIR="/home/ubuntu/trader-bot-production"

# ENHANCED: Exclude build artifacts and temporary files
LOCAL_EXCLUDE=".git,.gitignore,logs/*,*.tar.gz,*.tar,__pycache__,*.pyc,env-templates,vm-deploy-*.sh,*-bot-*.tar.gz,ada-bot-*,eth-bot-*"

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
    echo "Usage: $0 [SYMBOL] [ACTION]"
    echo ""
    echo "SYMBOL:"
    echo "  ada     - Deploy ADA bot only"
    echo "  eth     - Deploy ETH bot only" 
    echo ""
    echo "ACTION:"
    echo "  stop    - Stop specified bot(s)"
    echo "  update  - Update code and restart bot(s)"
    echo "  restart - Restart bot(s) without code update"
    echo ""
    echo "Credentials:"
    echo "  ADA bot uses: .env.ada (API key: 0744B5...)"
    echo "  ETH bot uses: .env.eth (API key: 377EB9...)"
    echo ""
    echo "Examples:"
    echo "  $0 ada update     # Update ADA bot with latest code"
    echo "  $0 eth restart    # Restart ETH bot"
    echo "  $0 ada stop       # Stop ADA bot only"
}

# Function to validate inputs
validate_inputs() {
    if [[ ! "$1" =~ ^(ada|eth)$ ]]; then
        print_error "Invalid symbol: $1"
        show_usage
        exit 1
    fi
    
    if [[ ! "$2" =~ ^(stop|update|restart)$ ]]; then
        print_error "Invalid action: $2"
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

# Enhanced local build function
build_image_locally() {
    local symbol="$1"
    local timestamp=$(date '+%Y%m%d_%H%M%S')
    local image_name="${symbol}-bot-${timestamp}"
    local tar_file="${symbol}-bot-${timestamp}.tar.gz"
    
    print_status "Building $symbol image locally for linux/amd64..."
    
    # Clean up any existing builds first
    cleanup_old_builds
    
    # Build with buildx for linux/amd64
    if ! docker buildx build --platform linux/amd64 \
        -t "${image_name}:latest" \
        -f Dockerfile \
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
    print_success "Image compressed: ${tar_file} (${size})"
    
    # Transfer to VM
    print_status "Transferring image to VM..."
    if ! scp -i "$SSH_KEY" -o StrictHostKeyChecking=no \
        "${tar_file}" \
        "$VM_USER@$VM_HOST:~/"; then
        print_error "Failed to transfer image"
        rm -f "${tar_file}"
        return 1
    fi
    
    print_success "Image transferred successfully"
    
    # Load on VM
    print_status "Loading image on VM (this may take a minute)..."
    if ! ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$VM_USER@$VM_HOST" \
        "gunzip -c ~/${tar_file} | docker load && rm -f ~/${tar_file}"; then
        print_error "Failed to load image on VM"
        rm -f "${tar_file}"
        return 1
    fi
    
    print_success "Image loaded on VM"
    
    # Clean up local files
    print_status "Cleaning up local temporary files..."
    rm -f "${tar_file}"
    docker rmi "${image_name}:latest" 2>/dev/null || true
    
    print_success "Local cleanup completed"
    
    # Store image name for container restart
    echo "${image_name}:latest" > ".last_built_${symbol}"
}

# Enhanced restart function with proper credential handling
restart_containers() {
    local symbol="$1"
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
        print_error "Environment file ${env_file} not found on VM!"
        print_error "Please ensure ${env_file} exists with correct API credentials"
        exit 1
    fi
    
    print_success "Found ${env_file} - will use these credentials"
    
    print_status "Stopping old ${symbol} container..."
    ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$VM_USER@$VM_HOST" \
        "docker stop ${symbol} 2>/dev/null || true; docker rm ${symbol} 2>/dev/null || true"
    
    print_status "Starting ${symbol} container with ${env_file}..."
    ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$VM_USER@$VM_HOST" \
        "cd $VM_DIR && docker run -d --name ${symbol} \
         --env-file ${env_file} \
         --restart always \
         --memory=128m \
         --cpus=0.25 \
         ${image_name} \
         python main.py ${symbol^^}USDT"
    
    print_success "${symbol} container started with credentials from ${env_file}"
    
    # Wait for container to start and show logs
    sleep 3
    print_status "Checking ${symbol} bot logs..."
    ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$VM_USER@$VM_HOST" \
        "docker logs --tail 20 ${symbol}"
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
    
    print_status "Starting deployment: $symbol $action"
    
    # Ensure Docker is running
    ensure_docker_running
    
    # Clean up old builds before starting
    cleanup_old_builds
    
    case "$action" in
        "stop")
            stop_containers "$symbol"
            ;;
        "update")
            print_success "Using local Docker buildx for cross-platform build"
            if build_image_locally "$symbol"; then
                restart_containers "$symbol"
                print_success "Deployment completed successfully!"
            else
                print_error "Build failed"
                exit 1
            fi
            ;;
        "restart")
            restart_containers "$symbol"
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
    validate_inputs "$1" "$2"
    
    # Check prerequisites
    check_prerequisites
    
    # Execute deployment with cleanup
    execute_deployment "$1" "$2"
}

# Run main function with all arguments
main "$@"