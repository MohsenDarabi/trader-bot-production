# CoinEx Daily Range Accumulation Trading Bot

A production-ready futures trading bot for CoinEx implementing the Daily Range Accumulation Strategy.

## Setup Instructions

1. **Clone and install dependencies:**
```bash
pip install -r requirements.txt
```

2. **Configure environment:**
```bash
cp .env.example .env
# Edit .env with your CoinEx API credentials
```

3. **Run the bot:**
```bash
python src/main.py
```

## Project Structure
```
.
├── src/
│   ├── core/           # Trading strategy logic
│   ├── exchange/       # CoinEx API integration
│   ├── data/           # Market data and state
│   ├── monitoring/     # Performance tracking
│   └── utils/          # Helper functions
├── config/             # Configuration files
├── storage/            # Database and state files
└── logs/               # Application logs
```

## Safety Features
- Test mode for first 5 days (minimum orders only)
- Position size validation
- Duplicate order prevention
- State persistence and recovery

## Strategy Overview
- Daily range calculation: (High - Low) / 4
- Buy at: Previous Low + Range
- Sell at: Previous High - Range
- No stop losses (accumulation strategy)
- Minimum 1.2% profit target

**WARNING**: This bot trades with real funds. Start with test mode and monitor carefully.