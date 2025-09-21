"""
CoinEx API Authentication Module
Handles HMAC-SHA256 signing for API requests
"""
import hmac
import hashlib
import time
from typing import Dict, Optional
from urllib.parse import urlencode

from config.settings import COINEX_ACCESS_ID, COINEX_SECRET_KEY


class CoinExAuth:
    """Handles authentication for CoinEx API requests"""
    
    def __init__(self, access_id: Optional[str] = None, secret_key: Optional[str] = None):
        """
        Initialize authentication with API credentials
        
        Args:
            access_id: CoinEx API access ID
            secret_key: CoinEx API secret key
        """
        self.access_id = access_id or COINEX_ACCESS_ID
        self.secret_key = secret_key or COINEX_SECRET_KEY
        
        if not self.access_id or not self.secret_key:
            raise ValueError("CoinEx API credentials not provided")
    
    def generate_signature(self, method: str, path: str, 
                         params: Optional[Dict] = None, 
                         body: Optional[str] = None,
                         timestamp: Optional[str] = None) -> str:
        """
        Generate HMAC-SHA256 signature for API request
        
        Args:
            method: HTTP method (GET, POST, etc.)
            path: API endpoint path (e.g., /v2/futures/order)
            params: Query parameters for GET requests
            body: JSON body for POST requests
            timestamp: Request timestamp in milliseconds
            
        Returns:
            Lowercase hexadecimal signature string
        """
        if timestamp is None:
            timestamp = str(int(time.time() * 1000))
        
        # Construct the prepared string
        prepared_parts = [method.upper(), path]
        
        # Add query parameters for GET requests
        if params and method.upper() == 'GET':
            # Preserve parameter ordering exactly as sent to the API
            if isinstance(params, dict):
                ordered_items = list(params.items())
            else:
                ordered_items = list(params)
            query_string = urlencode(ordered_items, doseq=True)
            prepared_parts[1] = f"{path}?{query_string}"
        
        # Add body for POST/PUT requests
        if body and method.upper() in ['POST', 'PUT', 'DELETE']:
            prepared_parts.append(body)
        
        # Add timestamp
        prepared_parts.append(timestamp)
        
        # Join all parts
        prepared_string = ''.join(prepared_parts)
        
        # Generate HMAC-SHA256 signature
        signature = hmac.new(
            self.secret_key.encode('utf-8'),
            prepared_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest().lower()
        
        return signature
    
    def get_auth_headers(self, method: str, path: str,
                        params: Optional[Dict] = None,
                        body: Optional[str] = None) -> Dict[str, str]:
        """
        Generate authentication headers for API request
        
        Args:
            method: HTTP method
            path: API endpoint path
            params: Query parameters
            body: Request body
            
        Returns:
            Dictionary with authentication headers
        """
        timestamp = str(int(time.time() * 1000))
        signature = self.generate_signature(method, path, params, body, timestamp)
        
        return {
            'X-COINEX-KEY': self.access_id,
            'X-COINEX-SIGN': signature,
            'X-COINEX-TIMESTAMP': timestamp,
            'Content-Type': 'application/json'
        }
    
