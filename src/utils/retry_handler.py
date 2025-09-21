"""
Retry mechanism for handling transient API errors with exponential backoff
"""
import time
import random
from functools import wraps
from typing import Any, Callable, Optional, Type, Tuple
from dataclasses import dataclass

from src.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class RetryConfig:
    """Configuration for retry mechanism"""
    max_retries: int = 3
    initial_delay: float = 2.0
    backoff_multiplier: float = 2.0
    jitter: bool = True
    max_delay: float = 60.0


class RetryableError(Exception):
    """Exception that indicates an operation should be retried"""
    def __init__(self, message: str, error_code: Optional[int] = None, original_exception: Optional[Exception] = None):
        super().__init__(message)
        self.error_code = error_code
        self.original_exception = original_exception


def is_retryable_coinex_error(error_message: str, error_code: Optional[int] = None) -> bool:
    """
    Determine if a CoinEx API error should be retried
    
    Args:
        error_message: Error message from API
        error_code: Error code from API (if available)
        
    Returns:
        True if error should be retried, False otherwise
    """
    # Error code 3008 is "service too busy" - definitely retryable
    if error_code == 3008:
        return True
    
    error_lower = error_message.lower()
    
    # Special non-retryable errors that need specific handling
    non_retryable_patterns = [
        "order exist",           # Need to cancel orders first
        "invalid parameters",    # Configuration issue
        "insufficient balance",  # Not enough funds
        "market not found",      # Invalid market
        "unauthorized",          # Auth issue
        "forbidden"              # Permission issue
    ]
    
    if any(pattern in error_lower for pattern in non_retryable_patterns):
        return False
    
    # Common transient error patterns
    retryable_patterns = [
        "service too busy",
        "temporarily unavailable", 
        "timeout",
        "connection",
        "network",
        "server error",
        "internal error",
        "rate limit",
        "overloaded"
    ]
    
    return any(pattern in error_lower for pattern in retryable_patterns)


def extract_coinex_error_info(exception: Exception) -> Tuple[Optional[int], str]:
    """
    Extract error code and message from CoinEx API exception
    
    Args:
        exception: Exception from API call
        
    Returns:
        Tuple of (error_code, error_message)
    """
    error_message = str(exception)
    error_code = None
    
    # Try to extract error code from ValueError messages like "CoinEx API error: service too busy"
    if isinstance(exception, ValueError) and "CoinEx API error:" in error_message:
        # For now, we'll check message content since error codes aren't always available
        # In the future, we could enhance the CoinEx client to preserve error codes
        pass
    
    return error_code, error_message


def retry_on_transient_error(
    config: Optional[RetryConfig] = None,
    retryable_exceptions: Tuple[Type[Exception], ...] = (ValueError, ConnectionError, TimeoutError),
    custom_retry_check: Optional[Callable[[Exception], bool]] = None
):
    """
    Decorator that retries function calls on transient errors with exponential backoff
    
    Args:
        config: Retry configuration
        retryable_exceptions: Exception types that should trigger retries
        custom_retry_check: Optional function to determine if an exception should be retried
        
    Returns:
        Decorated function
    """
    if config is None:
        config = RetryConfig()
    
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            
            for attempt in range(config.max_retries + 1):  # +1 for initial attempt
                try:
                    result = func(*args, **kwargs)
                    
                    # Log successful retry if this wasn't the first attempt
                    if attempt > 0:
                        logger.info(f"{func.__name__} succeeded on attempt {attempt + 1}")
                    
                    return result
                    
                except Exception as e:
                    last_exception = e
                    
                    # Check if this is the last attempt
                    if attempt >= config.max_retries:
                        break
                    
                    # Determine if we should retry this exception
                    should_retry = False
                    
                    # Check if exception type is retryable
                    if isinstance(e, retryable_exceptions):
                        should_retry = True
                        
                        # For ValueError (which CoinEx API errors use), check if it's a transient error
                        if isinstance(e, ValueError):
                            error_code, error_message = extract_coinex_error_info(e)
                            should_retry = is_retryable_coinex_error(error_message, error_code)
                    
                    # Use custom retry check if provided
                    if custom_retry_check:
                        should_retry = custom_retry_check(e)
                    
                    if not should_retry:
                        logger.warning(f"{func.__name__} failed with non-retryable error: {e}")
                        raise e
                    
                    # Calculate delay for next attempt
                    delay = config.initial_delay * (config.backoff_multiplier ** attempt)
                    delay = min(delay, config.max_delay)
                    
                    # Add jitter to avoid thundering herd
                    if config.jitter:
                        delay = delay * (0.5 + random.random() * 0.5)
                    
                    logger.warning(
                        f"{func.__name__} failed on attempt {attempt + 1}/{config.max_retries + 1}: {e}. "
                        f"Retrying in {delay:.2f} seconds..."
                    )
                    
                    time.sleep(delay)
            
            # All attempts failed
            logger.error(f"{func.__name__} failed after {config.max_retries + 1} attempts")
            raise last_exception
        
        return wrapper
    return decorator


def retry_coinex_api_call(
    func: Callable,
    *args,
    max_retries: int = 3,
    initial_delay: float = 2.0,
    **kwargs
) -> Any:
    """
    Retry a CoinEx API call with exponential backoff
    
    Args:
        func: Function to call
        *args: Arguments to pass to function
        max_retries: Maximum number of retry attempts
        initial_delay: Initial delay in seconds
        **kwargs: Keyword arguments to pass to function
        
    Returns:
        Result of the function call
        
    Raises:
        Exception: If all retry attempts fail
    """
    config = RetryConfig(max_retries=max_retries, initial_delay=initial_delay)
    
    @retry_on_transient_error(config=config)
    def _wrapped_call():
        return func(*args, **kwargs)
    
    return _wrapped_call()


class RetryableAPIClient:
    """
    Base class for API clients that need retry functionality
    """
    
    def __init__(self, retry_config: Optional[RetryConfig] = None):
        self.retry_config = retry_config or RetryConfig()
    
    def with_retry(self, func: Callable, *args, **kwargs) -> Any:
        """
        Execute a function with retry logic
        
        Args:
            func: Function to execute
            *args: Arguments for the function
            **kwargs: Keyword arguments for the function
            
        Returns:
            Result of the function call
        """
        return retry_coinex_api_call(
            func, 
            *args, 
            max_retries=self.retry_config.max_retries,
            initial_delay=self.retry_config.initial_delay,
            **kwargs
        )


# Default retry configuration for CoinEx API - will be updated with settings values
COINEX_RETRY_CONFIG = RetryConfig(
    max_retries=3,
    initial_delay=2.0,
    backoff_multiplier=2.0,
    jitter=True,
    max_delay=30.0
)

def update_retry_config_from_settings():
    """Update retry configuration with values from settings"""
    try:
        from config.settings import (
            API_MAX_RETRIES,
            API_INITIAL_RETRY_DELAY,
            API_RETRY_BACKOFF_MULTIPLIER,
            API_MAX_RETRY_DELAY,
            API_RETRY_JITTER
        )
        
        global COINEX_RETRY_CONFIG
        COINEX_RETRY_CONFIG = RetryConfig(
            max_retries=API_MAX_RETRIES,
            initial_delay=API_INITIAL_RETRY_DELAY,
            backoff_multiplier=API_RETRY_BACKOFF_MULTIPLIER,
            jitter=API_RETRY_JITTER,
            max_delay=API_MAX_RETRY_DELAY
        )
        
        logger.info(f"Updated retry configuration: max_retries={API_MAX_RETRIES}, "
                   f"initial_delay={API_INITIAL_RETRY_DELAY}s, "
                   f"backoff_multiplier={API_RETRY_BACKOFF_MULTIPLIER}, "
                   f"max_delay={API_MAX_RETRY_DELAY}s, jitter={API_RETRY_JITTER}")
        
    except ImportError:
        logger.warning("Could not import retry settings, using defaults")

# Initialize with settings values
update_retry_config_from_settings()
