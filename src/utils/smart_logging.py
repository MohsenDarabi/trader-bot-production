"""
Smart logging utilities to reduce log noise and improve readability
"""
import time
from collections import defaultdict, deque
from typing import Dict
from dataclasses import dataclass
from threading import Lock

from src.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class LogSummary:
    """Summary of repeated log events"""
    event_type: str
    count: int
    first_occurrence: float
    last_occurrence: float
    sample_message: str


class RateLimitedLogger:
    """Logger that can rate limit and summarize repeated messages"""
    
    def __init__(self, summary_interval: int = 60):
        """
        Initialize rate-limited logger
        
        Args:
            summary_interval: Interval in seconds for summary reports
        """
        self.summary_interval = summary_interval
        self.event_counts: Dict[str, int] = defaultdict(int)
        self.event_first_seen: Dict[str, float] = {}
        self.event_last_seen: Dict[str, float] = {}
        self.event_samples: Dict[str, str] = {}
        self.last_summary_time = time.time()
        self._lock = Lock()
        
        # Specific rate limits for different event types
        self.rate_limits = {
            'price_update': 30.0,       # Max 1 per 30 seconds
            'balance_check': 60.0,      # Max 1 per minute
            'heartbeat': 300.0,         # Max 1 per 5 minutes
            'order_status': 5.0,        # Max 1 per 5 seconds
            'buy_status_sync': 30.0,    # Reduce repeated forced-sync logs
            'buy_status': 30.0,         # Only surface when counts change
            'buy_decision': 5.0,        # Avoid spamming decision updates
            'buy_status_update': 10.0,  # Throttle status update logs
        }
        
        self.last_logged: Dict[str, float] = defaultdict(float)
    
    def should_log(self, event_type: str) -> bool:
        """Check if event should be logged based on rate limits"""
        current_time = time.time()
        rate_limit = self.rate_limits.get(event_type, 0)
        
        if rate_limit == 0:
            return True  # No rate limit
        
        if current_time - self.last_logged[event_type] >= rate_limit:
            self.last_logged[event_type] = current_time
            return True
        
        return False
    
    def log_event(self, event_type: str, message: str, level: str = 'info') -> None:
        """
        Log an event with rate limiting and summarization
        
        Args:
            event_type: Type of event for rate limiting
            message: Log message
            level: Log level (info, debug, warning, error)
        """
        current_time = time.time()
        
        with self._lock:
            # Track event statistics
            self.event_counts[event_type] += 1
            
            if event_type not in self.event_first_seen:
                self.event_first_seen[event_type] = current_time
            
            self.event_last_seen[event_type] = current_time
            self.event_samples[event_type] = message
            
            # Check if we should log this specific instance
            if self.should_log(event_type):
                # Log the actual message
                if level == 'debug':
                    logger.debug(f"[{event_type}] {message}")
                elif level == 'info':
                    logger.info(f"[{event_type}] {message}")
                elif level == 'warning':
                    logger.warning(f"[{event_type}] {message}")
                elif level == 'error':
                    logger.error(f"[{event_type}] {message}")
            
            # Check if it's time for a summary
            if current_time - self.last_summary_time >= self.summary_interval:
                self._log_summary()
                self.last_summary_time = current_time
    
    def _log_summary(self) -> None:
        """Log summary of events since last summary"""
        if not self.event_counts:
            return
        
        summary_lines = ["📊 Event Summary (last 60s):"]
        
        # Sort events by frequency
        sorted_events = sorted(
            self.event_counts.items(), 
            key=lambda x: x[1], 
            reverse=True
        )
        
        for event_type, count in sorted_events:
            if count > 1:  # Only summarize repeated events
                duration = self.event_last_seen[event_type] - self.event_first_seen[event_type]
                rate = count / max(duration, 1)
                summary_lines.append(
                    f"  • {event_type}: {count} events ({rate:.1f}/s)"
                )
        
        if len(summary_lines) > 1:
            logger.info("\n".join(summary_lines))
        
        # Reset counters for next interval
        self.event_counts.clear()
        self.event_first_seen.clear()
        self.event_last_seen.clear()
        self.event_samples.clear()
    
    def force_summary(self) -> None:
        """Force a summary report immediately"""
        with self._lock:
            self._log_summary()




class SmartAPILogger:
    """Smart logger for API requests"""
    
    def __init__(self):
        self.rate_limited_logger = RateLimitedLogger(summary_interval=180)  # 3-minute summaries
        self.error_events = deque(maxlen=20)  # Track last 20 errors
    
    def log_request(self, method: str, endpoint: str, success: bool = True) -> None:
        """Log API request with rate limiting"""
        if success:
            message = f"{method} {endpoint} - success"
            self.rate_limited_logger.log_event('api_success', message, 'debug')
        else:
            message = f"{method} {endpoint} - failed"
            self.rate_limited_logger.log_event('api_error', message, 'warning')
            self.error_events.append((time.time(), message))
    
    def log_response_time(self, endpoint: str, duration: float) -> None:
        """Log slow API responses"""
        if duration > 5.0:  # Only log slow responses
            message = f"Slow response: {endpoint} took {duration:.2f}s"
            self.rate_limited_logger.log_event('api_slow', message, 'warning')


# Global instances for use throughout the application
smart_api_logger = SmartAPILogger()
rate_limited_logger = RateLimitedLogger()


def log_trading_event(event_type: str, message: str, level: str = 'info') -> None:
    """
    Convenience function for logging trading events with smart rate limiting
    
    Args:
        event_type: Type of event (order_placed, position_update, etc.)
        message: Log message
        level: Log level
    """
    rate_limited_logger.log_event(event_type, message, level)


def force_log_summaries() -> None:
    """Force all smart loggers to output their summaries"""
    smart_api_logger.rate_limited_logger.force_summary()
    rate_limited_logger.force_summary()
