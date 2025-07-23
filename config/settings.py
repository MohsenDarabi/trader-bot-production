"""
Configuration settings for CoinEx Daily Range Accumulation Bot
"""
import os
from typing import Optional
from datetime import datetime

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

# CoinEx API Configuration
COINEX_ACCESS_ID = os.getenv('COINEX_API_KEY')
COINEX_SECRET_KEY = os.getenv('COINEX_API_SECRET')
COINEX_BASE_URL = 'https://api.coinex.com'
COINEX_WS_URL = 'wss://socket.coinex.com/v2/futures'

# Trading Strategy Parameters
RANGE_DIVISOR = 4  # (High - Low) / 4 formula
POSITION_SIZE_PERCENT = 10.0  # 10% of available capital per trade
LEVERAGE = 2.0  # 2x leverage on positions
MAKER_FEE = 0.001  # 0.1% maker fee
TAKER_FEE = 0.001  # 0.1% taker fee
MIN_PROFIT_PERCENT = 1.2  # 1.2% minimum profit after fees
MAX_RANGE_DEVIATION = 10.0  # Max 10% range expansion allowed

# Risk Management
TRADING_MODE = os.getenv('TRADING_MODE', 'TEST')  # TEST or NORMAL
TEST_MODE_DAYS = 5  # Number of days to run in test mode
TEST_MODE_START_DATE = os.getenv('TEST_MODE_START_DATE', datetime.now().isoformat())
USE_MINIMUM_ORDERS_ONLY = TRADING_MODE == 'TEST'

# API Rate Limits
MAX_REQUESTS_PER_10_SECONDS = 400
REQUEST_TIMEOUT = 30  # seconds

# Database Configuration
DATABASE_PATH = os.getenv('DATABASE_PATH', './storage/bot_state.db')

# Logging Configuration
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
LOG_FILE = './logs/trading_bot.log'

# Display Settings
TERMINAL_REFRESH_INTERVAL = 1  # seconds
SHOW_DEBUG_INFO = os.getenv('SHOW_DEBUG_INFO', 'False').lower() == 'true'

# WebSocket Settings
WS_RECONNECT_DELAY = 5  # seconds
WS_HEARTBEAT_INTERVAL = 20  # seconds

# Telegram Configuration (Low Priority - Only after core features)
TELEGRAM_ENABLED = os.getenv('TELEGRAM_ENABLED', 'False').lower() == 'true'
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

# Trading Time Settings
SIGNAL_GENERATION_TIME = "00:00"  # UTC time for daily signal generation
TIMEZONE = "UTC"

# Safety Settings
MAX_OPEN_POSITIONS = 10  # Maximum number of concurrent positions
EMERGENCY_STOP_LOSS_PERCENT = None  # None means no stop loss (as per strategy)
MAX_POSITION_AGE_DAYS = 60  # Alert if position is older than this

def validate_config():
    """Validate that all required configuration is present"""
    errors = []
    
    if not COINEX_ACCESS_ID:
        errors.append("COINEX_API_KEY is not set")
    if not COINEX_SECRET_KEY:
        errors.append("COINEX_API_SECRET is not set")
    
    if TELEGRAM_ENABLED:
        if not TELEGRAM_BOT_TOKEN:
            errors.append("TELEGRAM_BOT_TOKEN is required when TELEGRAM_ENABLED is True")
        if not TELEGRAM_CHAT_ID:
            errors.append("TELEGRAM_CHAT_ID is required when TELEGRAM_ENABLED is True")
    
    return errors

def is_test_mode() -> bool:
    """Check if bot is still in test mode"""
    if TRADING_MODE != 'TEST':
        return False
    
    start_date = datetime.fromisoformat(TEST_MODE_START_DATE)
    days_running = (datetime.now() - start_date).days
    return days_running < TEST_MODE_DAYS

def get_position_size_mode() -> str:
    """Get current position sizing mode"""
    return "MINIMUM" if is_test_mode() else "NORMAL"