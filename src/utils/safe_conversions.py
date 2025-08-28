"""
Safe conversion utilities for handling CoinEx API responses
All CoinEx API numeric values are returned as strings and need safe conversion
"""

def safe_float(value, default=0.0):
    """
    Safely convert value to float, handling strings and None
    
    Args:
        value: Value to convert (can be string, number, or None)
        default: Default value if conversion fails
        
    Returns:
        Float value or default
    """
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default

def safe_int(value, default=0):
    """
    Safely convert value to int, handling strings and None
    
    Args:
        value: Value to convert (can be string, number, or None)
        default: Default value if conversion fails
        
    Returns:
        Integer value or default
    """
    if value is None or value == "":
        return default
    try:
        # Handle float strings by converting to float first, then int
        if isinstance(value, str) and '.' in value:
            return int(float(value))
        return int(value)
    except (ValueError, TypeError):
        return default

def safe_str_format(value, format_spec):
    """
    Safely format a value as string, handling potential string values from API
    
    Args:
        value: Value to format (could be string or number)
        format_spec: Format specification (e.g., '.2f', '.6f')
        
    Returns:
        Formatted string
    """
    try:
        # Convert to float first if it's a string
        if isinstance(value, str):
            float_value = safe_float(value)
            return f"{float_value:{format_spec}}"
        else:
            return f"{value:{format_spec}}"
    except (ValueError, TypeError):
        return str(value)