"""
Funding Fee Settlement Handler

Handles CoinEx funding fee settlement periods that occur at 00:00, 08:00, and 16:00 UTC.
During these periods, order operations are temporarily blocked by the exchange.
"""

import asyncio
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Optional, TypeVar, Union
from functools import wraps
import logging

from src.utils.logger import logger

# Settlement times (UTC hours)
SETTLEMENT_HOURS = [0, 8, 16]

# Settlement window duration (minutes)
SETTLEMENT_WINDOW_MINUTES = 3  # Conservative estimate: 1-2 minutes actual + buffer

# Pre-settlement buffer (minutes) - avoid actions approaching settlement
PRE_SETTLEMENT_BUFFER_MINUTES = 1  # 1 minute buffer as suggested by user

# Retry configuration
MAX_RETRIES = 3
INITIAL_RETRY_DELAY = 10  # seconds
MAX_RETRY_DELAY = 180  # 3 minutes max wait

# Error messages that indicate funding fee settlement
SETTLEMENT_ERROR_MESSAGES = [
    "service is not available during funding fee settlement",
    "funding fee settlement",
    "settlement period",
    "service unavailable"
]

T = TypeVar('T')


def is_settlement_period(timestamp: Optional[datetime] = None) -> bool:
    """
    Check if the given time (or current time) is within a funding fee settlement window.
    
    Args:
        timestamp: Time to check (defaults to current UTC time)
        
    Returns:
        True if within settlement period, False otherwise
    """
    if timestamp is None:
        timestamp = datetime.now(timezone.utc)
    
    # Ensure we have a timezone-aware datetime
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    
    current_hour = timestamp.hour
    current_minute = timestamp.minute
    
    # Check if we're at a settlement hour and within the window
    if current_hour in SETTLEMENT_HOURS and current_minute < SETTLEMENT_WINDOW_MINUTES:
        return True
    
    return False


def is_approaching_settlement(timestamp: Optional[datetime] = None) -> bool:
    """
    Check if we're approaching a funding fee settlement period (within buffer time).
    This is used to proactively avoid order operations that might conflict with settlement.
    
    Args:
        timestamp: Optional datetime to check, uses current UTC time if not provided
        
    Returns:
        True if approaching settlement (within buffer), False otherwise
    """
    if timestamp is None:
        timestamp = datetime.now(timezone.utc)
    
    # Ensure we have a timezone-aware datetime
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    
    current_hour = timestamp.hour
    current_minute = timestamp.minute
    
    # Check if we're at a settlement hour and within pre-settlement buffer
    if current_hour in SETTLEMENT_HOURS:
        # Check if we're within the extended window (buffer + settlement window)
        total_window = PRE_SETTLEMENT_BUFFER_MINUTES + SETTLEMENT_WINDOW_MINUTES
        if current_minute < total_window:
            return True
    
    # Also check if we're approaching the next settlement hour
    for settlement_hour in SETTLEMENT_HOURS:
        if settlement_hour == (current_hour + 1) % 24:
            # We're in the hour before settlement, check if within buffer of next hour
            minutes_to_next_hour = 60 - current_minute
            if minutes_to_next_hour <= PRE_SETTLEMENT_BUFFER_MINUTES:
                return True
    
    return False


def get_next_settlement_time() -> datetime:
    """
    Get the next funding fee settlement time.
    
    Returns:
        Next settlement datetime (UTC)
    """
    now = datetime.now(timezone.utc)
    current_hour = now.hour
    
    # Find next settlement hour
    next_settlement_hour = None
    for hour in SETTLEMENT_HOURS:
        if hour > current_hour or (hour == current_hour and now.minute < SETTLEMENT_WINDOW_MINUTES):
            next_settlement_hour = hour
            break
    
    # If no settlement hour found today, use first one tomorrow
    if next_settlement_hour is None:
        next_day = now + timedelta(days=1)
        next_settlement = next_day.replace(
            hour=SETTLEMENT_HOURS[0], 
            minute=0, 
            second=0, 
            microsecond=0
        )
    else:
        next_settlement = now.replace(
            hour=next_settlement_hour, 
            minute=0, 
            second=0, 
            microsecond=0
        )
    
    return next_settlement


def get_time_until_settlement_end() -> Optional[float]:
    """
    Get seconds until current settlement period ends.
    
    Returns:
        Seconds until settlement ends, or None if not in settlement
    """
    if not is_settlement_period():
        return None
    
    now = datetime.now(timezone.utc)
    settlement_end = now.replace(
        minute=SETTLEMENT_WINDOW_MINUTES, 
        second=0, 
        microsecond=0
    )
    
    return (settlement_end - now).total_seconds()


def is_settlement_error(error: Exception) -> bool:
    """
    Check if an error is related to funding fee settlement.
    
    Args:
        error: Exception to check
        
    Returns:
        True if error is settlement-related
    """
    error_str = str(error).lower()
    return any(msg in error_str for msg in SETTLEMENT_ERROR_MESSAGES)


async def wait_for_settlement_end():
    """
    Wait until the current settlement period ends.
    """
    wait_time = get_time_until_settlement_end()
    if wait_time:
        logger.info(f"⏳ Waiting {wait_time:.0f} seconds for funding fee settlement to complete...")
        await asyncio.sleep(wait_time)
        # Add small buffer to ensure settlement is fully complete
        await asyncio.sleep(5)
        logger.info("✅ Funding fee settlement period has ended")


def settlement_retry(func: Callable[..., T]) -> Callable[..., T]:
    """
    Decorator for synchronous functions that retries on settlement errors.
    
    Args:
        func: Function to wrap
        
    Returns:
        Wrapped function with settlement retry logic
    """
    @wraps(func)
    def wrapper(*args, **kwargs) -> T:
        last_error = None
        retry_count = 0
        
        while retry_count < MAX_RETRIES:
            try:
                # First check if we're in settlement period
                if is_settlement_period():
                    wait_seconds = get_time_until_settlement_end() or SETTLEMENT_WINDOW_MINUTES * 60
                    logger.warning(
                        f"⏳ Currently in funding fee settlement period. "
                        f"Waiting {wait_seconds:.0f} seconds before {func.__name__}..."
                    )
                    time.sleep(wait_seconds + 5)  # Add small buffer
                
                # Try the operation
                return func(*args, **kwargs)
                
            except Exception as e:
                last_error = e
                
                # Check if it's a settlement error
                if is_settlement_error(e):
                    retry_count += 1
                    
                    if retry_count < MAX_RETRIES:
                        # Calculate delay with exponential backoff
                        delay = min(INITIAL_RETRY_DELAY * (2 ** (retry_count - 1)), MAX_RETRY_DELAY)
                        
                        logger.warning(
                            f"⚠️  Funding fee settlement error in {func.__name__}: {e}. "
                            f"Retry {retry_count}/{MAX_RETRIES} in {delay} seconds..."
                        )
                        time.sleep(delay)
                    else:
                        logger.error(
                            f"❌ {func.__name__} failed after {MAX_RETRIES} retries due to settlement: {e}"
                        )
                        raise
                else:
                    # Not a settlement error, raise immediately
                    raise
        
        # Should not reach here, but just in case
        if last_error:
            raise last_error
        
    return wrapper


def settlement_retry_async(func: Callable[..., T]) -> Callable[..., T]:
    """
    Decorator for async functions that retries on settlement errors.
    
    Args:
        func: Async function to wrap
        
    Returns:
        Wrapped async function with settlement retry logic
    """
    @wraps(func)
    async def wrapper(*args, **kwargs) -> T:
        last_error = None
        retry_count = 0
        
        while retry_count < MAX_RETRIES:
            try:
                # First check if we're in settlement period
                if is_settlement_period():
                    await wait_for_settlement_end()
                
                # Try the operation
                return await func(*args, **kwargs)
                
            except Exception as e:
                last_error = e
                
                # Check if it's a settlement error
                if is_settlement_error(e):
                    retry_count += 1
                    
                    if retry_count < MAX_RETRIES:
                        # Calculate delay with exponential backoff
                        delay = min(INITIAL_RETRY_DELAY * (2 ** (retry_count - 1)), MAX_RETRY_DELAY)
                        
                        logger.warning(
                            f"⚠️  Funding fee settlement error in {func.__name__}: {e}. "
                            f"Retry {retry_count}/{MAX_RETRIES} in {delay} seconds..."
                        )
                        await asyncio.sleep(delay)
                    else:
                        logger.error(
                            f"❌ {func.__name__} failed after {MAX_RETRIES} retries due to settlement: {e}"
                        )
                        raise
                else:
                    # Not a settlement error, raise immediately
                    raise
        
        # Should not reach here, but just in case
        if last_error:
            raise last_error
        
    return wrapper


class SettlementAwareOperation:
    """
    Context manager for settlement-aware operations.
    
    Usage:
        async with SettlementAwareOperation("place_order"):
            # Your order operation here
            pass
    """
    
    def __init__(self, operation_name: str):
        self.operation_name = operation_name
        
    async def __aenter__(self):
        if is_settlement_period():
            await wait_for_settlement_end()
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if exc_val and is_settlement_error(exc_val):
            logger.warning(
                f"Settlement error during {self.operation_name}: {exc_val}. "
                "Consider retrying after settlement period."
            )
        return False  # Don't suppress exceptions


def log_settlement_schedule():
    """Log the next few settlement times for visibility."""
    now = datetime.now(timezone.utc)
    logger.info("📅 Upcoming funding fee settlement times (UTC):")
    
    next_time = now
    for i in range(3):
        next_time = get_next_settlement_time()
        logger.info(f"  • {next_time.strftime('%Y-%m-%d %H:%M')} UTC")
        # Move to next one
        next_time = next_time + timedelta(minutes=SETTLEMENT_WINDOW_MINUTES + 1)