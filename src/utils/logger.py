"""
Logging configuration for the trading bot
"""
import logging
import sys
from pathlib import Path
from loguru import logger

from config.settings import LOG_LEVEL, LOG_FILE
import os


# Remove default logger
logger.remove()

# Create logs directory if it doesn't exist
log_path = Path(LOG_FILE).parent
log_path.mkdir(exist_ok=True)

# Get console and file log levels from environment
CONSOLE_LOG_LEVEL = os.getenv('CONSOLE_LOG_LEVEL', 'INFO')  # Console shows less detail
FILE_LOG_LEVEL = os.getenv('FILE_LOG_LEVEL', LOG_LEVEL)     # File can be more verbose

# Add console handler with reduced verbosity and cleaner format
logger.add(
    sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <4}</level> | <level>{message}</level>",
    level=CONSOLE_LOG_LEVEL,
    colorize=True,
    filter=lambda record: record["level"].name not in ["DEBUG"]  # Never show DEBUG on console
)

# Add file handler with full details and aggressive rotation
logger.add(
    LOG_FILE,
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
    level=FILE_LOG_LEVEL,
    rotation="5 MB",      # Smaller files for faster rotation
    retention="3 days",   # Keep fewer days to save space
    compression="gz",     # Better compression
    enqueue=True         # Thread-safe logging
)

# Add error-only file for critical issues
error_log_file = LOG_FILE.replace('.log', '_errors.log')
logger.add(
    error_log_file,
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
    level="ERROR",
    rotation="1 MB",
    retention="7 days",
    compression="gz"
)


def get_logger(name: str):
    """Get a logger instance for a module"""
    return logger.bind(name=name)