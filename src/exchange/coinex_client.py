"""
CoinEx REST API Client
Handles all HTTP requests to CoinEx API with retry logic and rate limiting
"""
import json
import time
from typing import Dict, Optional, Any, List
from urllib.parse import urljoin
import requests
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry

from config.settings import (
    COINEX_BASE_URL, 
    MAX_REQUESTS_PER_10_SECONDS,
    REQUEST_TIMEOUT
)
from src.exchange.auth import CoinExAuth
from src.utils.logger import get_logger


logger = get_logger(__name__)


class RateLimiter:
    """Simple rate limiter for API requests"""
    
    def __init__(self, max_requests: int = MAX_REQUESTS_PER_10_SECONDS, 
                 window_seconds: int = 10):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.requests = []
    
    def wait_if_needed(self):
        """Wait if rate limit would be exceeded"""
        now = time.time()
        # Remove old requests outside the window
        self.requests = [req_time for req_time in self.requests 
                        if now - req_time < self.window_seconds]
        
        if len(self.requests) >= self.max_requests:
            # Wait until the oldest request is outside the window
            sleep_time = self.window_seconds - (now - self.requests[0]) + 0.1
            logger.warning(f"Rate limit reached, sleeping for {sleep_time:.2f}s")
            time.sleep(sleep_time)
            # Clean up again after sleeping
            now = time.time()
            self.requests = [req_time for req_time in self.requests 
                           if now - req_time < self.window_seconds]
        
        self.requests.append(now)


class CoinExClient:
    """REST API client for CoinEx futures trading"""
    
    def __init__(self, auth: Optional[CoinExAuth] = None):
        """
        Initialize CoinEx API client
        
        Args:
            auth: Authentication instance, will create one if not provided
        """
        self.auth = auth or CoinExAuth()
        self.base_url = COINEX_BASE_URL
        self.rate_limiter = RateLimiter()
        self.session = self._create_session()
    
    def _create_session(self) -> requests.Session:
        """Create requests session with retry logic"""
        session = requests.Session()
        
        # Configure retry strategy
        retry_strategy = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "PUT", "DELETE", "OPTIONS", "TRACE", "POST"]
        )
        
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        
        return session
    
    def _request(self, method: str, endpoint: str, 
                params: Optional[Dict] = None,
                data: Optional[Dict] = None,
                auth_required: bool = True) -> Dict[str, Any]:
        """
        Make HTTP request to CoinEx API
        
        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint (e.g., /futures/market)
            params: Query parameters
            data: Request body data
            auth_required: Whether authentication is required
            
        Returns:
            API response as dictionary
            
        Raises:
            requests.RequestException: On request failure
            ValueError: On API error response
        """
        # Wait if rate limit would be exceeded
        self.rate_limiter.wait_if_needed()
        
        # Construct full URL
        url = urljoin(self.base_url, endpoint)
        
        # Prepare request arguments
        kwargs = {
            'timeout': REQUEST_TIMEOUT
        }
        
        # Add parameters
        if params:
            kwargs['params'] = params
        
        # Add body data
        body_str = None
        if data:
            body_str = json.dumps(data)
            kwargs['data'] = body_str
        
        # Add authentication headers if required
        if auth_required:
            headers = self.auth.get_auth_headers(
                method=method,
                path=endpoint,
                params=params,
                body=body_str
            )
            kwargs['headers'] = headers
        
        # Log request
        logger.debug(f"{method} {url} params={params} auth={auth_required}")
        
        try:
            # Make request
            response = self.session.request(method, url, **kwargs)
            
            # Log response
            logger.debug(f"Response {response.status_code}: {response.text[:200]}")
            
            # Parse response
            response_data = response.json()
            
            # Check for API errors
            if response_data.get('code') != 0:
                error_msg = response_data.get('message', 'Unknown error')
                logger.error(f"API error: {error_msg}")
                raise ValueError(f"CoinEx API error: {error_msg}")
            
            return response_data.get('data', {})
            
        except requests.RequestException as e:
            logger.error(f"Request failed: {e}")
            raise
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse response: {e}")
            raise ValueError(f"Invalid response format: {e}")
    
    # Market Data Endpoints (Public - No Auth Required)
    
    def get_futures_markets(self, market: Optional[str] = None) -> List[Dict]:
        """
        Get futures market information
        
        Args:
            market: Optional specific market, returns all if not specified
            
        Returns:
            List of market information dictionaries
        """
        params = {}
        if market:
            params['market'] = market
        
        return self._request('GET', '/futures/market', params=params, auth_required=False)
    
    def get_kline(self, market: str, period: str = '1day', 
                  limit: int = 100) -> List[Dict]:
        """
        Get K-line/candlestick data
        
        Args:
            market: Market symbol (e.g., BTCUSDT)
            period: Time period (1day, 1hour, etc.)
            limit: Number of data points
            
        Returns:
            List of K-line data
        """
        params = {
            'market': market,
            'period': period,
            'limit': limit
        }
        
        return self._request('GET', '/futures/kline', params=params, auth_required=False)
    
    def get_ticker(self, market: str) -> Dict:
        """
        Get 24-hour ticker data
        
        Args:
            market: Market symbol
            
        Returns:
            Ticker data dictionary
        """
        params = {'market': market}
        return self._request('GET', '/futures/ticker', params=params, auth_required=False)
    
    # Trading Endpoints (Auth Required)
    
    def place_order(self, market: str, side: str, amount: str, 
                   order_type: str = 'limit', price: Optional[str] = None,
                   client_id: Optional[str] = None, is_hide: bool = False) -> Dict:
        """
        Place a futures order
        
        Args:
            market: Market symbol (e.g., BTCUSDT)
            side: Order side ('buy' or 'sell')
            amount: Order amount
            order_type: Order type ('limit' or 'market')
            price: Order price (required for limit orders)
            client_id: Custom order ID for tracking
            is_hide: Whether to hide order from public order book
            
        Returns:
            Order details dictionary
        """
        data = {
            'market': market,
            'market_type': 'FUTURES',
            'side': side,
            'type': order_type,
            'amount': amount
        }
        
        if order_type == 'limit' and price:
            data['price'] = price
        
        if client_id:
            data['client_id'] = client_id
        
        if is_hide:
            data['is_hide'] = is_hide
        
        return self._request('POST', '/futures/order', data=data)
    
    def cancel_order(self, market: str, order_id: Optional[int] = None,
                    client_id: Optional[str] = None) -> Dict:
        """
        Cancel a futures order
        
        Args:
            market: Market symbol
            order_id: Exchange order ID
            client_id: Custom order ID
            
        Returns:
            Cancellation result
        """
        data = {
            'market': market,
            'market_type': 'FUTURES'
        }
        
        if order_id:
            data['order_id'] = order_id
        elif client_id:
            data['client_id'] = client_id
        else:
            raise ValueError("Either order_id or client_id must be provided")
        
        return self._request('DELETE', '/futures/order', data=data)
    
    def get_pending_orders(self, market: Optional[str] = None, 
                          page: int = 1, limit: int = 100) -> Dict:
        """
        Get list of pending orders
        
        Args:
            market: Optional market filter
            page: Page number
            limit: Results per page
            
        Returns:
            Dictionary with orders list and pagination info
        """
        params = {
            'market_type': 'FUTURES',
            'page': page,
            'limit': limit
        }
        
        if market:
            params['market'] = market
        
        return self._request('GET', '/futures/pending-order', params=params)
    
    def get_order_status(self, market: str, order_id: Optional[int] = None,
                        client_id: Optional[str] = None) -> Dict:
        """
        Get order status
        
        Args:
            market: Market symbol
            order_id: Exchange order ID
            client_id: Custom order ID
            
        Returns:
            Order status details
        """
        params = {
            'market': market,
            'market_type': 'FUTURES'
        }
        
        if order_id:
            params['order_id'] = order_id
        elif client_id:
            params['client_id'] = client_id
        else:
            raise ValueError("Either order_id or client_id must be provided")
        
        return self._request('GET', '/futures/order-status', params=params)
    
    def get_positions(self, market: Optional[str] = None) -> Dict:
        """
        Get current futures positions
        
        Args:
            market: Optional market filter
            
        Returns:
            Dictionary with positions list
        """
        params = {'market_type': 'FUTURES'}
        
        if market:
            params['market'] = market
        
        return self._request('GET', '/futures/pending-position', params=params)
    
    def get_account_info(self) -> Dict:
        """
        Get futures account information
        
        Returns:
            Account details including balance
        """
        return self._request('GET', '/assets/futures/balance')
    
    def close(self):
        """Close the session"""
        self.session.close()