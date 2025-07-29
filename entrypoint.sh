#!/bin/sh
# Docker entrypoint that removes stale lock files and ensures clean startup

echo "🐳 Docker container startup - performing cleanup..."

# Comprehensive lock file cleanup (multiple locations)
echo "🧹 Cleaning up any stale lock files..."
rm -f /app/main_trading_bot.lock
rm -f /app/trading_bot.lock
rm -f /app/*.lock
rm -f /tmp/main_trading_bot.lock
rm -f /tmp/trading_bot.lock
rm -f /tmp/*.lock 2>/dev/null || true
rm -f main_trading_bot.lock
rm -f trading_bot.lock
rm -f *.lock 2>/dev/null || true

# Kill any existing Python processes (in case of zombie processes)
echo "🔄 Stopping any existing processes..."
pkill -f "python.*main.py" 2>/dev/null || true
pkill -f "python" 2>/dev/null || true

# Wait to ensure cleanup
sleep 3

# Change to app directory and perform final cleanup
cd /app
rm -f main_trading_bot.lock
rm -f trading_bot.lock
rm -f *.lock 2>/dev/null || true

# Create necessary directories if they don't exist
echo "📁 Ensuring required directories exist..."
mkdir -p /app/logs
mkdir -p /app/storage
mkdir -p /tmp

# Set proper permissions for VM environments
chmod 755 /app/logs 2>/dev/null || true
chmod 755 /app/storage 2>/dev/null || true

# Display environment info for debugging
echo "🔧 Environment: $(python --version)"
echo "📈 Starting trading bot with market: $1"
echo "🕒 Container started at: $(date)"

# Set environment variable for container detection
export DOCKER_CONTAINER=true

# Use exec to replace shell process and ensure clean shutdown
exec python main.py "$@"