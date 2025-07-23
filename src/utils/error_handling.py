"""
Comprehensive error handling system for the trading bot
"""
import traceback
from enum import Enum
from typing import Optional, Any, Dict
from dataclasses import dataclass
from datetime import datetime

from src.utils.logger import get_logger

logger = get_logger(__name__)


class ErrorSeverity(Enum):
    """Error severity levels"""
    LOW = "low"          # Minor issues, bot can continue
    MEDIUM = "medium"    # Moderate issues, may affect performance
    HIGH = "high"        # Serious issues, trading should pause
    CRITICAL = "critical"  # Fatal errors, bot should stop


class ErrorCategory(Enum):
    """Error categories for better classification"""
    NETWORK = "network"
    API = "api"
    DATABASE = "database"
    STRATEGY = "strategy"
    ORDER_MANAGEMENT = "order_management"
    POSITION_MANAGEMENT = "position_management"
    AUTHENTICATION = "authentication"
    VALIDATION = "validation"
    SYSTEM = "system"


@dataclass
class TradingError:
    """Standardized error representation"""
    category: ErrorCategory
    severity: ErrorSeverity
    message: str
    context: Dict[str, Any]
    timestamp: datetime
    exception: Optional[Exception] = None
    stacktrace: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for logging/storage"""
        return {
            'category': self.category.value,
            'severity': self.severity.value,
            'message': self.message,
            'context': self.context,
            'timestamp': self.timestamp.isoformat(),
            'exception_type': type(self.exception).__name__ if self.exception else None,
            'stacktrace': self.stacktrace
        }


class ErrorHandler:
    """Centralized error handling system"""
    
    def __init__(self):
        self.error_count = 0
        self.recent_errors = []
        self.max_recent_errors = 100
    
    def handle_error(
        self,
        category: ErrorCategory,
        severity: ErrorSeverity,
        message: str,
        context: Optional[Dict[str, Any]] = None,
        exception: Optional[Exception] = None
    ) -> TradingError:
        """
        Handle and log an error
        
        Args:
            category: Error category
            severity: Error severity level
            message: Human-readable error message
            context: Additional context information
            exception: Original exception if available
            
        Returns:
            TradingError object
        """
        if context is None:
            context = {}
        
        # Create error object
        error = TradingError(
            category=category,
            severity=severity,
            message=message,
            context=context,
            timestamp=datetime.utcnow(),
            exception=exception,
            stacktrace=traceback.format_exc() if exception else None
        )
        
        # Update counters
        self.error_count += 1
        self.recent_errors.append(error)
        
        # Keep recent errors list manageable
        if len(self.recent_errors) > self.max_recent_errors:
            self.recent_errors = self.recent_errors[-self.max_recent_errors:]
        
        # Log based on severity
        error_dict = error.to_dict()
        log_message = f"[{category.value.upper()}] {message}"
        
        if severity == ErrorSeverity.LOW:
            logger.warning(log_message, extra=error_dict)
        elif severity == ErrorSeverity.MEDIUM:
            logger.warning(log_message, extra=error_dict)
        elif severity == ErrorSeverity.HIGH:
            logger.error(log_message, extra=error_dict)
        elif severity == ErrorSeverity.CRITICAL:
            logger.critical(log_message, extra=error_dict)
        
        # Add context details to log
        if context:
            logger.debug(f"Error context: {context}")
        
        if exception:
            logger.debug(f"Exception details: {type(exception).__name__}: {str(exception)}")
        
        return error
    
    def get_error_summary(self) -> Dict[str, Any]:
        """Get summary of recent errors"""
        category_counts = {}
        severity_counts = {}
        
        for error in self.recent_errors:
            # Count by category
            cat = error.category.value
            category_counts[cat] = category_counts.get(cat, 0) + 1
            
            # Count by severity
            sev = error.severity.value
            severity_counts[sev] = severity_counts.get(sev, 0) + 1
        
        return {
            'total_errors': self.error_count,
            'recent_errors': len(self.recent_errors),
            'category_breakdown': category_counts,
            'severity_breakdown': severity_counts,
            'last_error': self.recent_errors[-1].to_dict() if self.recent_errors else None
        }
    
    def should_pause_trading(self) -> bool:
        """Determine if trading should be paused based on recent errors"""
        if not self.recent_errors:
            return False
        
        # Check for critical errors in last 5 minutes
        recent_critical = [
            error for error in self.recent_errors[-10:]
            if error.severity == ErrorSeverity.CRITICAL
        ]
        
        if recent_critical:
            return True
        
        # Check for too many high severity errors
        recent_high = [
            error for error in self.recent_errors[-20:]
            if error.severity == ErrorSeverity.HIGH
        ]
        
        if len(recent_high) >= 5:
            return True
        
        return False
    
    def reset_error_count(self):
        """Reset error counters (useful for testing)"""
        self.error_count = 0
        self.recent_errors = []


# Global error handler instance
error_handler = ErrorHandler()


def handle_api_error(exception: Exception, context: Dict[str, Any] = None) -> TradingError:
    """Convenience function for API errors"""
    return error_handler.handle_error(
        category=ErrorCategory.API,
        severity=ErrorSeverity.HIGH,
        message=f"API error: {str(exception)}",
        context=context or {},
        exception=exception
    )


def handle_network_error(exception: Exception, context: Dict[str, Any] = None) -> TradingError:
    """Convenience function for network errors"""
    return error_handler.handle_error(
        category=ErrorCategory.NETWORK,
        severity=ErrorSeverity.MEDIUM,
        message=f"Network error: {str(exception)}",
        context=context or {},
        exception=exception
    )


def handle_validation_error(message: str, context: Dict[str, Any] = None) -> TradingError:
    """Convenience function for validation errors"""
    return error_handler.handle_error(
        category=ErrorCategory.VALIDATION,
        severity=ErrorSeverity.MEDIUM,
        message=f"Validation error: {message}",
        context=context or {}
    )


def handle_critical_error(exception: Exception, context: Dict[str, Any] = None) -> TradingError:
    """Convenience function for critical errors"""
    return error_handler.handle_error(
        category=ErrorCategory.SYSTEM,
        severity=ErrorSeverity.CRITICAL,
        message=f"Critical system error: {str(exception)}",
        context=context or {},
        exception=exception
    )