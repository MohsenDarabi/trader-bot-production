"""
Configuration settings for CoinEx Daily Range Accumulation Bot
"""
import os
from typing import Optional
from datetime import datetime

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

# Trading Environment Configuration
TRADING_ENV = os.getenv('TRADING_ENV', 'testing').lower()

# CoinEx API Configuration
COINEX_ACCESS_ID = os.getenv('COINEX_API_KEY')
COINEX_SECRET_KEY = os.getenv('COINEX_API_SECRET')
COINEX_BASE_URL = os.getenv('COINEX_API_URL', 'https://api.coinex.com')
COINEX_WS_URL = os.getenv('COINEX_WS_URL', 'wss://socket.coinex.com/v2/futures')

# Virtual Trading Configuration (for testing mode)
VIRTUAL_BALANCE = float(os.getenv('VIRTUAL_BALANCE', '1000.0'))
SIMULATE_ORDERS = os.getenv('SIMULATE_ORDERS', 'true').lower() == 'true'

# Trading Strategy Parameters
RANGE_DIVISOR = 4  # (High - Low) / 4 formula
POSITION_SIZE_PERCENT = float(os.getenv('POSITION_SIZE_PERCENT', '1.0'))  # Default 1% of available capital per trade
MAX_POSITION_SIZE_PERCENT = float(os.getenv('MAX_POSITION_SIZE_PERCENT', '2.5'))  # Maximum position size
MIN_POSITION_SIZE_PERCENT = float(os.getenv('MIN_POSITION_SIZE_PERCENT', '0.5'))  # Minimum position size
LEVERAGE = float(os.getenv('LEVERAGE', '2.0'))  # Default 2x leverage on positions
MAKER_FEE = 0.0005  # 0.05% maker fee (conservative approach)
TAKER_FEE = 0.0005  # 0.05% taker fee (actual CoinEx rate)
MIN_PROFIT_PERCENT = float(os.getenv('MIN_PROFIT_PERCENT', '1.0'))  # Default 1.0% minimum profit after fees

# Risk Management
TRADING_MODE = os.getenv('TRADING_MODE', 'TEST')  # TEST or NORMAL
TEST_MODE_DAYS = 5  # Number of days to run in test mode
TEST_MODE_START_DATE = os.getenv('TEST_MODE_START_DATE', datetime.now().isoformat())
USE_MINIMUM_ORDERS_ONLY = TRADING_MODE == 'TEST'

# API Rate Limits
MAX_REQUESTS_PER_10_SECONDS = 400
REQUEST_TIMEOUT = 30  # seconds

# API Retry Configuration
API_MAX_RETRIES = int(os.getenv('API_MAX_RETRIES', '3'))
API_INITIAL_RETRY_DELAY = float(os.getenv('API_INITIAL_RETRY_DELAY', '2.0'))  # seconds
API_RETRY_BACKOFF_MULTIPLIER = float(os.getenv('API_RETRY_BACKOFF_MULTIPLIER', '2.0'))
API_MAX_RETRY_DELAY = float(os.getenv('API_MAX_RETRY_DELAY', '30.0'))  # seconds
API_RETRY_JITTER = os.getenv('API_RETRY_JITTER', 'True').lower() == 'true'

# Database Configuration
DATABASE_PATH = os.getenv('DATABASE_PATH', './storage/bot_state.db')

# Logging Configuration
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
LOG_FILE = './logs/trading_bot.log'

# Enhanced Logging Settings for Noise Reduction
CONSOLE_LOG_LEVEL = os.getenv('CONSOLE_LOG_LEVEL', 'INFO')    # Console verbosity
FILE_LOG_LEVEL = os.getenv('FILE_LOG_LEVEL', 'DEBUG')         # File verbosity (can be more detailed)
ENABLE_SMART_LOGGING = os.getenv('ENABLE_SMART_LOGGING', 'True').lower() == 'true'
LOG_SUMMARY_INTERVAL = int(os.getenv('LOG_SUMMARY_INTERVAL', '120'))  # Smart logging summary interval

# Display Settings
TERMINAL_REFRESH_INTERVAL = 1  # seconds
SHOW_DEBUG_INFO = os.getenv('SHOW_DEBUG_INFO', 'False').lower() == 'true'

# WebSocket Settings
WS_RECONNECT_DELAY = 5  # seconds
WS_HEARTBEAT_INTERVAL = 20  # seconds

# Order Status Check Settings
# How often to perform REST API status checks (in seconds)
# Set to -1 to disable periodic REST API checks (rely on WebSocket only)
ORDER_STATUS_CHECK_INTERVAL = int(os.getenv('ORDER_STATUS_CHECK_INTERVAL', '300'))  # 5 minutes default
ENABLE_REST_API_STATUS_CHECKS = ORDER_STATUS_CHECK_INTERVAL > 0

# Telegram Configuration (Low Priority - Only after core features)
TELEGRAM_ENABLED = os.getenv('TELEGRAM_ENABLED', 'False').lower() == 'true'
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

# Trading Time Settings
SIGNAL_GENERATION_TIME = "00:00"  # UTC time for daily signal generation
TIMEZONE = "UTC"
TRADING_TIMEFRAME = os.getenv('TRADING_TIMEFRAME', 'daily')  # 'daily' or 'hourly'
TRADING_INTERVAL = os.getenv('TRADING_INTERVAL', 'daily')  # Primary interval setting

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
    
    # Validate trading environment
    if TRADING_ENV not in ['testing', 'production']:
        errors.append(f"Invalid TRADING_ENV: {TRADING_ENV}. Must be 'testing' or 'production'")
    
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

def is_testing_mode() -> bool:
    """Check if running in testing environment (SafeMode)"""
    return TRADING_ENV == 'testing'

def is_production_mode() -> bool:
    """Check if running in production environment (real trading)"""
    return TRADING_ENV == 'production'

def get_trading_mode_description() -> str:
    """Get human-readable description of current trading mode"""
    if is_testing_mode():
        return "🧪 SAFE MODE - Orders simulated locally, no real trading"
    elif is_production_mode():
        return "🚀 PRODUCTION MODE - Real orders placed on exchange"
    else:
        return f"❓ UNKNOWN MODE - {TRADING_ENV}"