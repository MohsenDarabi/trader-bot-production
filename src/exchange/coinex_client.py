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
from src.utils.error_handling import handle_api_error, handle_network_error, handle_validation_error


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
        logger.info("Checking rate limiter...")
        self.rate_limiter.wait_if_needed()
        logger.info("Rate limiter check complete")
        
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
            logger.info("Generating authentication headers...")
            headers = self.auth.get_auth_headers(
                method=method,
                path=endpoint,
                params=params,
                body=body_str
            )
            kwargs['headers'] = headers
            logger.info("Authentication headers generated successfully")
        
        # Log request
        logger.info(f"Making {method} request to {url} with params={params} auth={auth_required}")
        logger.info(f"Request kwargs: {kwargs}")
        
        try:
            # Make request
            logger.info("About to call session.request() - this is where hangs typically occur")
            response = self.session.request(method, url, **kwargs)
            logger.info(f"Received response with status {response.status_code}")
            
            # Log response
            logger.debug(f"Response {response.status_code}: {response.text[:200]}")
            
            # Parse response
            logger.info(f"Raw response text: '{response.text}'")
            logger.info(f"Response headers: {dict(response.headers)}")
            
            # Handle HTTP errors
            if response.status_code >= 400:
                context = {
                    'method': method,
                    'url': url,
                    'status_code': response.status_code,
                    'response_text': response.text[:500]
                }
                if response.status_code == 429:
                    handle_api_error(
                        Exception(f"Rate limit exceeded: {response.status_code}"),
                        context
                    )
                elif response.status_code >= 500:
                    handle_api_error(
                        Exception(f"Server error: {response.status_code}"),
                        context
                    )
                else:
                    handle_api_error(
                        Exception(f"HTTP error: {response.status_code}"),
                        context
                    )
            
            if not response.text.strip():
                context = {
                    'method': method,
                    'url': url,
                    'status_code': response.status_code
                }
                handle_api_error(
                    Exception("Server returned empty response"),
                    context
                )
                raise ValueError(f"Server returned empty response. Status: {response.status_code}")
            
            try:
                response_data = response.json()
            except Exception as json_error:
                context = {
                    'method': method,
                    'url': url,
                    'response_text': response.text[:500],
                    'json_error': str(json_error)
                }
                handle_api_error(json_error, context)
                logger.error(f"Failed to parse JSON response: {json_error}")
                logger.error(f"Raw response text: '{response.text}'")
                raise ValueError(f"Invalid JSON response from server: {response.text[:200]}")
            
            # Check for API errors
            if response_data.get('code') != 0:
                error_msg = response_data.get('message', 'Unknown error')
                context = {
                    'method': method,
                    'url': url,
                    'api_code': response_data.get('code'),
                    'api_message': error_msg,
                    'params': params,
                    'data': data
                }
                handle_api_error(
                    Exception(f"CoinEx API error: {error_msg}"),
                    context
                )
                logger.error(f"API error: {error_msg}")
                raise ValueError(f"CoinEx API error: {error_msg}")
            
            return response_data.get('data', {})
            
        except requests.RequestException as e:
            context = {
                'method': method,
                'url': url,
                'params': params,
                'data': data,
                'error_type': type(e).__name__
            }
            handle_network_error(e, context)
            logger.error(f"Request failed: {e}")
            raise
        except json.JSONDecodeError as e:
            context = {
                'method': method,
                'url': url,
                'response_text': response.text[:500] if 'response' in locals() else 'No response'
            }
            handle_api_error(e, context)
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
        
        return self._request('GET', '/v2/futures/market', params=params, auth_required=False)
    
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
        
        return self._request('GET', '/v2/futures/kline', params=params, auth_required=False)
    
    def get_ticker(self, market: str) -> Dict:
        """
        Get 24-hour ticker data
        
        Args:
            market: Market symbol
            
        Returns:
            Ticker data dictionary
        """
        params = {'market': market}
        return self._request('GET', '/v2/futures/ticker', params=params, auth_required=False)
    
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
        # Validate parameters
        if not market:
            handle_validation_error("Market symbol is required")
            raise ValueError("Market symbol is required")
        
        if side not in ['buy', 'sell']:
            handle_validation_error(f"Invalid side: {side}. Must be 'buy' or 'sell'")
            raise ValueError(f"Invalid side: {side}. Must be 'buy' or 'sell'")
        
        if order_type not in ['limit', 'market']:
            handle_validation_error(f"Invalid order type: {order_type}. Must be 'limit' or 'market'")
            raise ValueError(f"Invalid order type: {order_type}. Must be 'limit' or 'market'")
        
        if order_type == 'limit' and not price:
            handle_validation_error("Price is required for limit orders")
            raise ValueError("Price is required for limit orders")
        
        try:
            amount_float = float(amount)
            if amount_float <= 0:
                handle_validation_error(f"Invalid amount: {amount}. Must be positive")
                raise ValueError(f"Invalid amount: {amount}. Must be positive")
        except ValueError:
            handle_validation_error(f"Invalid amount format: {amount}")
            raise ValueError(f"Invalid amount format: {amount}")
        
        if price:
            try:
                price_float = float(price)
                if price_float <= 0:
                    handle_validation_error(f"Invalid price: {price}. Must be positive")
                    raise ValueError(f"Invalid price: {price}. Must be positive")
            except ValueError:
                handle_validation_error(f"Invalid price format: {price}")
                raise ValueError(f"Invalid price format: {price}")
        
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
        
        # Log order placement attempt
        logger.info(f"Placing {side} order: {amount} {market} @ {price if price else 'market'}")
        
        return self._request('POST', '/v2/futures/order', data=data)
    
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
        
        return self._request('POST', '/v2/futures/cancel-order', data=data)
    
    def get_pending_orders(self, market: Optional[str] = None, 
                          page: int = 1, limit: Optional[int] = None) -> Dict:
        """
        Get list of pending orders
        
        Args:
            market: Optional market filter (applied client-side due to API signature issues)
            page: Page number
            limit: Results per page
            
        Returns:
            Dictionary with orders list and pagination info
        """
        params = {
            'market_type': 'FUTURES',
            'page': page
        }
        
        # Only add limit if specified (causes signature issues when set to default value)
        if limit is not None:
            params['limit'] = limit
        
        # NOTE: The 'market' parameter causes signature issues with CoinEx API
        # We'll fetch all orders and filter client-side if market is specified
        
        # Wait for rate limiter
        self.rate_limiter.wait_if_needed()
        
        # Construct full URL
        url = urljoin(self.base_url, '/v2/futures/pending-order')
        
        # Add authentication headers
        headers = self.auth.get_auth_headers('GET', '/v2/futures/pending-order', params=params)
        
        try:
            response = self.session.request('GET', url, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
            response_data = response.json()
            
            # Check for API errors
            if response_data.get('code') != 0:
                error_msg = response_data.get('message', 'Unknown error')
                logger.error(f"API error: {error_msg}")
                raise ValueError(f"CoinEx API error: {error_msg}")
            
            # Apply client-side market filter if specified
            if market and response_data.get('data'):
                filtered_orders = [order for order in response_data['data'] if order.get('market') == market]
                response_data['data'] = filtered_orders
                # Update pagination count
                if 'pagination' in response_data:
                    response_data['pagination']['total'] = len(filtered_orders)
            
            # Return full response for pending orders (includes pagination)
            return response_data
            
        except Exception as e:
            logger.error(f"Pending orders request failed: {e}")
            raise
    
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
        
        return self._request('GET', '/v2/futures/order-status', params=params)
    
    def get_positions(self, market: Optional[str] = None) -> Dict:
        """
        Get current futures positions
        
        Args:
            market: Optional market filter (applied client-side due to API signature issues)
            
        Returns:
            Dictionary with positions list
        """
        params = {'market_type': 'FUTURES'}
        
        # NOTE: The 'market' parameter causes signature issues with CoinEx API
        # We'll fetch all positions and filter client-side if market is specified
        
        # Wait for rate limiter
        self.rate_limiter.wait_if_needed()
        
        # Construct full URL
        url = urljoin(self.base_url, '/v2/futures/pending-position')
        
        # Add authentication headers
        headers = self.auth.get_auth_headers('GET', '/v2/futures/pending-position', params=params)
        
        try:
            response = self.session.request('GET', url, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
            response_data = response.json()
            
            # Check for API errors
            if response_data.get('code') != 0:
                error_msg = response_data.get('message', 'Unknown error')
                logger.error(f"API error: {error_msg}")
                raise ValueError(f"CoinEx API error: {error_msg}")
            
            # Apply client-side market filter if specified
            if market and response_data.get('data'):
                if isinstance(response_data['data'], list):
                    filtered_positions = [pos for pos in response_data['data'] if pos.get('market') == market]
                    response_data['data'] = filtered_positions
                elif isinstance(response_data['data'], dict):
                    # Handle case where data is a dict with market-keyed positions
                    filtered_data = {k: v for k, v in response_data['data'].items() 
                                   if k == market or (isinstance(v, dict) and v.get('market') == market)}
                    response_data['data'] = filtered_data
            
            return response_data
            
        except Exception as e:
            logger.error(f"Positions request failed: {e}")
            raise
    
    def get_account_info(self) -> Dict:
        """
        Get futures account information
        
        Returns:
            Account details including balance
        """
        return self._request('GET', '/v2/assets/futures/balance')
    
    def adjust_position_leverage(self, market: str, leverage: int, 
                               margin_mode: str = 'cross') -> Dict:
        """
        Adjust position leverage for a specific market
        
        Args:
            market: Market symbol (e.g., ETHUSDT)
            leverage: Leverage ratio (e.g., 2 for 2x leverage)
            margin_mode: Position type ('cross' or 'isolated')
            
        Returns:
            Response containing leverage adjustment result
        """
        if not market:
            raise ValueError("Market symbol is required")
        
        if not isinstance(leverage, int) or leverage < 1:
            raise ValueError("Leverage must be a positive integer")
        
        if margin_mode not in ['cross', 'isolated']:
            raise ValueError("margin_mode must be 'cross' or 'isolated'")
        
        data = {
            'market': market,
            'market_type': 'FUTURES',
            'margin_mode': margin_mode,
            'leverage': leverage
        }
        
        logger.info(f"Setting leverage for {market}: {leverage}x ({margin_mode} margin)")
        
        try:
            # _request() returns only the 'data' portion and raises exception on API error
            data_response = self._request('POST', '/v2/futures/adjust-position-leverage', data=data)
            
            # If we reach here, the API call was successful (no exception thrown)
            logger.info(f"Leverage set successfully for {market}: "
                      f"{data_response.get('leverage')}x {data_response.get('margin_mode')} margin")
            
            return data_response
            
        except Exception as e:
            logger.error(f"Error setting leverage for {market}: {e}")
            raise
    
    def close(self):
        """Close the session"""
        self.session.close()