#!/bin/bash
"""
CoinEx Daily Range Bot Startup Script
"""

set -e

echo "🚀 Starting CoinEx Daily Range Accumulation Bot"
echo "=============================================="
echo

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

echo "🤖 Starting bot..."
echo "   Press Ctrl+C to stop gracefully"
echo

# Start the bot with optional market argument
# Usage: ./start_bot.sh [MARKET]
# Examples: ./start_bot.sh ETHUSDT
#          ./start_bot.sh BTCUSDT
#          ./start_bot.sh (defaults to BTCUSDT)
python3 main.py ${1:-BTCUSDT}

echo
echo "👋 Bot stopped. Goodbye!"