#!/bin/bash
# Development Bot Docker Management Script

set -e

MARKET=${1:-"BTCUSDT"}
ACTION=${2:-"start"}

echo "🚀 CoinEx Development Bot Docker Manager"
echo "========================================"
echo "Market: $MARKET"
echo "Action: $ACTION"
echo ""

case $ACTION in
    "start")
        echo "Starting development bot container..."
        docker-compose up -d
        echo "✅ Development bot started successfully!"
        echo "📊 Use 'docker logs -f coinex-bot-development' to view logs"
        ;;
    "stop")
        echo "Stopping development bot container..."
        docker-compose down
        echo "🛑 Development bot stopped successfully!"
        ;;
    "restart")
        echo "Restarting development bot container..."
        docker-compose down
        docker-compose up -d
        echo "🔄 Development bot restarted successfully!"
        ;;
    "logs")
        echo "Showing development bot logs..."
        docker logs -f coinex-bot-development
        ;;
    "status")
        echo "Development bot container status:"
        docker-compose ps
        ;;
    "shell")
        echo "Opening shell in development bot container..."
        docker exec -it coinex-bot-development /bin/bash
        ;;
    "rebuild")
        echo "Rebuilding development bot container..."
        docker-compose down
        docker-compose build --no-cache
        docker-compose up -d
        echo "🔨 Development bot rebuilt and started!"
        ;;
    *)
        echo "Usage: $0 [MARKET] [ACTION]"
        echo ""
        echo "MARKET: Trading pair (default: BTCUSDT)"
        echo "Actions:"
        echo "  start   - Start the bot container"
        echo "  stop    - Stop the bot container"
        echo "  restart - Restart the bot container"
        echo "  logs    - Show container logs"
        echo "  status  - Show container status"
        echo "  shell   - Open shell in container"
        echo "  rebuild - Rebuild and restart container"
        echo ""
        echo "Examples:"
        echo "  $0 ETHUSDT start"
        echo "  $0 BTCUSDT logs"
        echo "  $0 restart"
        exit 1
        ;;
esac