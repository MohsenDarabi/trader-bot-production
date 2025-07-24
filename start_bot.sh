#!/bin/bash
# CoinEx Daily Range Bot Startup Script - Native & Docker Support

set -e

MARKET=${1:-"BTCUSDT"}
RUN_MODE=${2:-"native"}  # native or docker

echo "🚀 Starting CoinEx Daily Range Accumulation Bot"
echo "=============================================="
echo "Market: $MARKET"
echo "Mode: $RUN_MODE"
echo

# Docker mode
if [ "$RUN_MODE" = "docker" ]; then
    echo "🐳 Starting bot in Docker container..."
    ./docker-start.sh $MARKET start
    exit 0
fi

# Native mode continues below
# Check if we're in virtual environment
if [[ "$VIRTUAL_ENV" != *"trader-bot-liveTesting-coinex"* ]]; then
    echo "📦 Activating virtual environment..."
    source bin/activate
    echo "✅ Virtual environment activated"
fi

# Check if environment variables are set
if [ -z "$COINEX_API_KEY" ] && [ ! -f ".env" ]; then
    echo "❌ Error: CoinEx API credentials not found"
    echo "Please either:"
    echo "  1. Set environment variables:"
    echo "     export COINEX_API_KEY='your_access_id'"
    echo "     export COINEX_API_SECRET='your_secret_key'"
    echo "  2. Or create a .env file:"
    echo "     cp .env.example .env"
    echo "     # Edit .env with your credentials"
    exit 1
fi

# Check if .env file exists and show mode
if [ -f ".env" ]; then
    TRADING_MODE=$(grep "TRADING_MODE" .env | cut -d'=' -f2 | tr -d '"' || echo "TEST")
    echo "💼 Trading Mode: $TRADING_MODE"
    
    if [ "$TRADING_MODE" = "TEST" ]; then
        echo "⚠️  TEST MODE ACTIVE:"
        echo "   • Minimum order sizes only"
        echo "   • 5-day test period"
        echo "   • Lower risk approach"
    else
        echo "🔴 LIVE MODE ACTIVE:"
        echo "   • Full position sizing"
        echo "   • Real money at risk"
        echo "   • Monitor carefully"
    fi
    echo
fi

# Final confirmation for live mode
if [ "$TRADING_MODE" = "NORMAL" ] || [ "$TRADING_MODE" = "LIVE" ]; then
    echo "⚠️  WARNING: About to start LIVE TRADING with real money!"
    read -p "Are you sure you want to continue? (yes/no): " confirm
    if [ "$confirm" != "yes" ]; then
        echo "🛑 Cancelled by user"
        exit 0
    fi
fi

echo "🤖 Starting bot natively..."
echo "   Press Ctrl+C to stop gracefully"
echo

# Start the bot with market argument
python3 main.py $MARKET

echo
echo "👋 Bot stopped. Goodbye!"