#!/bin/bash
# Production Bot Docker Management Script

set -e

# Load .env file if it exists
if [ -f .env ]; then
    # Parse .env safely - only export valid KEY=VALUE pairs
    while IFS= read -r line; do
        # Skip empty lines and comments
        [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
        # Extract KEY=VALUE (ignore inline comments)
        if [[ "$line" =~ ^[[:space:]]*([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]]; then
            key="${BASH_REMATCH[1]}"
            value="${BASH_REMATCH[2]}"
            # Remove inline comments and trim whitespace
            value=$(echo "$value" | sed 's/[[:space:]]*#.*$//' | xargs)
            export "$key=$value"
        fi
    done < .env
fi

ACTION=${1:-"start"}
# Use environment variable first, then command line argument, then fallback
DEFAULT_MARKET=${DEFAULT_TRADING_MARKET:-"BTCUSDT"}
MARKET=${2:-"$DEFAULT_MARKET"}

# Validate market format (should end with USDT and contain only alphanumeric)
if [[ ! "$MARKET" =~ ^[A-Z0-9]+USDT$ ]]; then
    echo "⚠️  Warning: '$MARKET' doesn't look like a valid market pair."
    echo "   Valid format: BTCUSDT, ADAUSDT, ETHUSDT, etc."
    echo "   Continuing with fallback: BTCUSDT" 
    MARKET="BTCUSDT"
fi

# Auto-detect docker compose command (modern vs legacy)
if command -v "docker-compose" &> /dev/null; then
    DOCKER_COMPOSE="docker-compose"
elif docker compose version &> /dev/null; then
    DOCKER_COMPOSE="docker compose"
else
    echo "❌ Error: Neither 'docker-compose' nor 'docker compose' found!"
    echo "Please install Docker Compose or Docker Desktop"
    exit 1
fi

echo "🚀 CoinEx Production Bot Docker Manager"
echo "======================================="
echo "Action: $ACTION"
echo "Market: $MARKET"

# Show configuration source for user feedback
if [ "$MARKET" = "$DEFAULT_MARKET" ] && [ "$DEFAULT_MARKET" != "BTCUSDT" ]; then
    echo "📝 Using default from .env: DEFAULT_TRADING_MARKET=$DEFAULT_MARKET"
elif [ "$MARKET" != "${2:-}" ]; then
    echo "💡 To change default market, edit DEFAULT_TRADING_MARKET in .env file"
fi
echo ""

case $ACTION in
    "start")
        echo "Starting production bot container with market: $MARKET"
        MARKET=$MARKET $DOCKER_COMPOSE up -d
        echo "✅ Production bot started successfully!"
        echo "📊 Use 'docker logs -f coinex-bot-production' to view logs"
        ;;
    "stop")
        echo "Stopping production bot container..."
        $DOCKER_COMPOSE down
        echo "🛑 Production bot stopped successfully!"
        ;;
    "restart")
        echo "Restarting production bot container with market: $MARKET"
        $DOCKER_COMPOSE down
        MARKET=$MARKET $DOCKER_COMPOSE up -d
        echo "🔄 Production bot restarted successfully!"
        ;;
    "logs")
        echo "Showing production bot logs..."
        docker logs -f coinex-bot-production
        ;;
    "status")
        echo "Production bot container status:"
        $DOCKER_COMPOSE ps
        ;;
    "shell")
        echo "Opening shell in production bot container..."
        docker exec -it coinex-bot-production /bin/bash
        ;;
    "rebuild")
        echo "Rebuilding production bot container with market: $MARKET"
        $DOCKER_COMPOSE down
        # Clean up any volumes to remove stale lock files
        docker volume prune -f 2>/dev/null || true
        $DOCKER_COMPOSE build --no-cache
        MARKET=$MARKET $DOCKER_COMPOSE up -d
        echo "🔨 Production bot rebuilt and started!"
        ;;
    *)
        echo "Usage: $0 [ACTION] [MARKET]"
        echo ""
        echo "Actions:"
        echo "  start   - Start the bot container"
        echo "  stop    - Stop the bot container"
        echo "  restart - Restart the bot container"
        echo "  logs    - Show container logs"
        echo "  status  - Show container status"
        echo "  shell   - Open shell in container"
        echo "  rebuild - Rebuild and restart container"
        echo ""
        echo "MARKET: Trading pair (default: BTCUSDT)"
        echo ""
        echo "Examples:"
        echo "  $0 start ADAUSDT"
        echo "  $0 stop"
        echo "  $0 restart ADAUSDT"
        echo "  $0 logs              # Show logs"
        exit 1
        ;;
esac