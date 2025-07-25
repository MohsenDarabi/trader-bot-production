#!/bin/sh
# Docker entrypoint that removes stale lock files

echo "Cleaning up any stale lock files..."
rm -f /app/main_trading_bot.lock
rm -f /app/*.lock
rm -f main_trading_bot.lock
rm -f *.lock 2>/dev/null || true

# Kill any existing Python processes (in case of zombie processes)
pkill -f "python.*main.py" 2>/dev/null || true
pkill -f "python" 2>/dev/null || true

# Wait to ensure cleanup
sleep 3

# Also clean up in current directory where Python will run
cd /app
rm -f main_trading_bot.lock
rm -f *.lock 2>/dev/null || true

echo "Starting trading bot with market: $1"
# Use a subshell to ensure clean environment
exec python main.py "$@"