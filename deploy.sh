#!/bin/bash

# Trading Bot Deployment Script - Local Coordinator
# Usage: ./deploy.sh [ada|eth|all] [stop|update|restart]

set -e  # Exit on any error

# Configuration
VM_USER="ubuntu"
VM_HOST="89.168.111.195"
SSH_KEY="./ssh-key-2025-07-27.key"
VM_DIR="/home/ubuntu/trader-bot-production"
LOCAL_EXCLUDE=".git,.gitignore,logs/*,*.tar.gz,__pycache__,*.pyc,env-templates"

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
    echo "Usage: $0 [SYMBOL] [ACTION]"
    echo ""
    echo "SYMBOL:"
    echo "  ada     - Deploy ADA bot only"
    echo "  eth     - Deploy ETH bot only" 
    echo "  all     - Deploy both ADA and ETH bots"
    echo ""
    echo "ACTION:"
    echo "  stop    - Stop specified bot(s)"
    echo "  update  - Update code and restart bot(s)"
    echo "  restart - Restart bot(s) without code update"
    echo ""
    echo "Examples:"
    echo "  $0 ada update     # Update ADA bot with latest code"
    echo "  $0 all restart    # Restart both bots"
    echo "  $0 eth stop       # Stop ETH bot only"
}

# Function to validate inputs
validate_inputs() {
    if [[ ! "$1" =~ ^(ada|eth|all)$ ]]; then
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
    
    print_success "Prerequisites check passed"
}

# Function to create VM deployment script
create_vm_script() {
    local symbol="$1"
    local action="$2"
    local timestamp=$(date '+%Y%m%d_%H%M%S')
    local script_name="vm-deploy-${timestamp}.sh"
    
    print_status "Creating VM deployment script for $symbol $action..."
    
    cat > "$script_name" << 'EOF'
#!/bin/bash

# VM Deployment Executor Script
# This script runs entirely on the VM for reliable execution

set -e  # Exit on any error

SYMBOL="$1"
ACTION="$2" 
TIMESTAMP="$3"
VM_DIR="/home/ubuntu/trader-bot-production"

# Colors for VM output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_vm_status() {
    echo -e "${BLUE}[VM-$(date '+%H:%M:%S')]${NC} $1"
}

print_vm_success() {
    echo -e "${GREEN}[VM-$(date '+%H:%M:%S')] ✅${NC} $1"
}

print_vm_error() {
    echo -e "${RED}[VM-$(date '+%H:%M:%S')] ❌${NC} $1"
}

# Function to stop containers safely
stop_containers() {
    local target="$1"
    print_vm_status "Stopping $target container(s)..."
    
    cd "$VM_DIR"
    
    case "$target" in
        "ada")
            if docker ps -q -f name=ada | grep -q .; then
                docker stop ada && print_vm_success "ADA bot stopped"
                docker rm ada 2>/dev/null || true
            else
                print_vm_status "ADA bot not running"
            fi
            ;;
        "eth")
            if docker ps -q -f name=eth | grep -q .; then
                docker stop eth && print_vm_success "ETH bot stopped"
                docker rm eth 2>/dev/null || true
            else
                print_vm_status "ETH bot not running"
            fi
            ;;
        "all")
            docker-compose -f docker-compose.multi.yml down && print_vm_success "All bots stopped"
            ;;
    esac
    
    # Wait for containers to fully stop
    sleep 3
}

# Function to clean Docker safely
clean_docker() {
    print_vm_status "Cleaning unused Docker resources..."
    
    # Remove unused images (keep base images)
    docker image prune -f && print_vm_success "Unused images cleaned"
    
    # Remove unused containers
    docker container prune -f && print_vm_success "Unused containers cleaned"
    
    # Remove unused networks
    docker network prune -f && print_vm_success "Unused networks cleaned"
    
    # Show disk usage
    print_vm_status "Docker disk usage after cleanup:"
    docker system df
}

# Function to preserve important files
preserve_files() {
    print_vm_status "Preserving important files..."
    
    cd "$VM_DIR"
    
    # Create backup directory with timestamp
    BACKUP_DIR="backup_${TIMESTAMP}"
    mkdir -p "$BACKUP_DIR"
    
    # Preserve environment files
    if [[ -f ".env.ada" ]]; then
        cp ".env.ada" "$BACKUP_DIR/" && print_vm_success "Preserved .env.ada"
    fi
    
    if [[ -f ".env.eth" ]]; then
        cp ".env.eth" "$BACKUP_DIR/" && print_vm_success "Preserved .env.eth"
    fi
    
    # Preserve other important configs
    for file in ".env" "ssh-key-2025-07-27.key" "docker-compose.multi.yml"; do
        if [[ -f "$file" ]]; then
            cp "$file" "$BACKUP_DIR/" && print_vm_success "Preserved $file"
        fi
    done
    
    print_vm_success "Files preserved in $BACKUP_DIR"
}

# Function to extract and update code
update_code() {
    print_vm_status "Extracting updated code..."
    
    cd "$VM_DIR"
    
    # Extract the uploaded code
    if [[ -f "trader-bot-update-${TIMESTAMP}.tar.gz" ]]; then
        tar -xzf "trader-bot-update-${TIMESTAMP}.tar.gz" && print_vm_success "Code extracted"
        
        # Restore preserved files
        BACKUP_DIR="backup_${TIMESTAMP}"
        if [[ -d "$BACKUP_DIR" ]]; then
            cp "$BACKUP_DIR"/.env* . 2>/dev/null || true
            cp "$BACKUP_DIR"/ssh-key* . 2>/dev/null || true
            print_vm_success "Important files restored"
        fi
        
        # Clean up
        rm -f "trader-bot-update-${TIMESTAMP}.tar.gz"
    else
        print_vm_error "Update archive not found!"
        exit 1
    fi
}

# Function to build and start containers
start_containers() {
    local target="$1"
    print_vm_status "Building and starting $target container(s)..."
    
    cd "$VM_DIR"
    
    case "$target" in
        "ada")
            docker-compose -f docker-compose.multi.yml build --no-cache ada
            docker-compose -f docker-compose.multi.yml up -d ada
            print_vm_success "ADA bot started"
            ;;
        "eth")
            docker-compose -f docker-compose.multi.yml build --no-cache eth
            docker-compose -f docker-compose.multi.yml up -d eth
            print_vm_success "ETH bot started"
            ;;
        "all")
            docker-compose -f docker-compose.multi.yml build --no-cache
            docker-compose -f docker-compose.multi.yml up -d
            print_vm_success "All bots started"
            ;;
    esac
    
    # Wait for containers to start
    sleep 5
}

# Function to check bot health
check_health() {
    local target="$1"
    print_vm_status "Checking $target bot(s) health..."
    
    cd "$VM_DIR"
    
    case "$target" in
        "ada"|"all")
            if docker ps -q -f name=ada | grep -q .; then
                print_vm_success "ADA bot is running"
                docker logs --tail 5 ada
            else
                print_vm_error "ADA bot is not running!"
            fi
            ;;&
        "eth"|"all")
            if docker ps -q -f name=eth | grep -q .; then
                print_vm_success "ETH bot is running"
                docker logs --tail 5 eth
            else
                print_vm_error "ETH bot is not running!"
            fi
            ;;
    esac
    
    # Show container status
    print_vm_status "Container status:"
    docker ps --format "table {{.Names}}\t{{.Status}}\t{{.RunningFor}}"
}

# Main execution flow
main() {
    print_vm_status "Starting VM deployment: $SYMBOL $ACTION"
    
    case "$ACTION" in
        "stop")
            stop_containers "$SYMBOL"
            clean_docker
            ;;
        "update")
            preserve_files
            stop_containers "$SYMBOL"
            clean_docker
            update_code
            start_containers "$SYMBOL"
            check_health "$SYMBOL"
            ;;
        "restart")
            stop_containers "$SYMBOL"
            clean_docker
            start_containers "$SYMBOL"
            check_health "$SYMBOL"
            ;;
    esac
    
    print_vm_success "VM deployment completed: $SYMBOL $ACTION"
}

# Execute main function with parameters
main "$@"
EOF

    echo "$script_name"
}

# Function to upload code and execute deployment
execute_deployment() {
    local symbol="$1"
    local action="$2"
    local timestamp=$(date '+%Y%m%d_%H%M%S')
    
    print_status "Starting deployment: $symbol $action"
    
    # Create VM script
    vm_script=$(create_vm_script "$symbol" "$action")
    
    # Upload VM script
    print_status "Uploading VM deployment script..."
    scp -i "$SSH_KEY" -o StrictHostKeyChecking=no "$vm_script" "$VM_USER@$VM_HOST:$VM_DIR/"
    
    # If action is update, upload code
    if [[ "$action" == "update" ]]; then
        print_status "Creating code archive..."
        tar -czf "trader-bot-update-${timestamp}.tar.gz" \
            --exclude-from=<(echo -e "${LOCAL_EXCLUDE//,/\\n}") \
            --exclude="trader-bot-update-*.tar.gz" \
            --exclude="vm-deploy-*.sh" \
            .
        
        print_status "Uploading code archive..."
        scp -i "$SSH_KEY" -o StrictHostKeyChecking=no "trader-bot-update-${timestamp}.tar.gz" "$VM_USER@$VM_HOST:$VM_DIR/"
        
        # Clean up local archive
        rm -f "trader-bot-update-${timestamp}.tar.gz"
    fi
    
    # Execute deployment on VM
    print_status "Executing deployment on VM..."
    ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$VM_USER@$VM_HOST" \
        "cd $VM_DIR && chmod +x $vm_script && ./$vm_script $symbol $action $timestamp"
    
    # Clean up VM script
    ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$VM_USER@$VM_HOST" "rm -f $VM_DIR/$vm_script"
    rm -f "$vm_script"
    
    print_success "Deployment completed successfully!"
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
    
    # Execute deployment
    execute_deployment "$1" "$2"
}

# Run main function with all arguments
main "$@"