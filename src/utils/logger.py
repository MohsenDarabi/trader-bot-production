"""
Logging configuration for the trading bot
"""
import logging
import sys
from pathlib import Path
from loguru import logger

from config.settings import LOG_LEVEL, LOG_FILE


# Remove default logger
logger.remove()

# Create logs directory if it doesn't exist
log_path = Path(LOG_FILE).parent
log_path.mkdir(exist_ok=True)

# Add console handler with color
logger.add(
    sys.stdout,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
    level=LOG_LEVEL,
    colorize=True
)

# Add file handler
logger.add(
    LOG_FILE,
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
    level=LOG_LEVEL,
    rotation="10 MB",
    retention="7 days",
    compression="zip"
)


def get_logger(name: str):
    """Get a logger instance for a module"""
    return logger.bind(name=name)