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
from src.utils.retry_handler import retry_on_transient_error, COINEX_RETRY_CONFIG
from src.utils.smart_logging import smart_api_logger
from src.utils.safe_conversions import safe_float


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
                 auth_required: bool = True,
                 return_full: bool = False) -> Any:
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
        logger.debug("Checking rate limiter...")
        self.rate_limiter.wait_if_needed()
        logger.debug("Rate limiter check complete")
        
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
            logger.debug("Authentication headers generated successfully")
        
        # Log request
        logger.debug(f"Making {method} request to {url} with params={params} auth={auth_required}")
        logger.debug(f"Request kwargs: {kwargs}")
        
        start_time = time.time()
        try:
            # Make request
            logger.debug("About to call session.request() - this is where hangs typically occur")
            response = self.session.request(method, url, **kwargs)
            
            # Track response time and log with smart logger
            duration = time.time() - start_time
            success = response.status_code < 400
            smart_api_logger.log_request(method, endpoint, success)
            smart_api_logger.log_response_time(endpoint, duration)
            
            logger.debug(f"Received response with status {response.status_code}")
            
            # Log response
            logger.debug(f"Response {response.status_code}: {response.text[:200]}")
            
            # Parse response
            logger.debug(f"Raw response text: '{response.text}'")
            logger.debug(f"Response headers: {dict(response.headers)}")
            
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
            
            return response_data if return_full else response_data.get('data', {})
            
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
    
    def cancel_order(self, market: str, order_id: int) -> Dict:
        """
        Cancel a futures order
        
        Args:
            market: Market symbol
            order_id: Exchange order ID (required)
            
        Returns:
            Cancellation result
        """
        data = {
            'market': market,
            'market_type': 'FUTURES',
            'order_id': order_id
        }
        
        return self._request('POST', '/v2/futures/cancel-order', data=data)
    
    def _format_order_params(self, market: str, amount: float, price: float) -> tuple:
        """
        Format order parameters with proper precision for CoinEx API
        
        Args:
            market: Market symbol (e.g., ETHUSDT)
            amount: Order amount
            price: Order price
            
        Returns:
            Tuple of (formatted_amount_str, formatted_price_str)
        """
        from decimal import Decimal, ROUND_HALF_UP
        
        try:
            # Get market info to ensure proper precision
            market_info = self.get_futures_markets()
            market_data = next((m for m in market_info if m.get('market') == market), None)
            
            if market_data:
                min_amount = safe_float(market_data.get('min_amount', 0))
                tick_size = safe_float(market_data.get('tick_size', 0.0001))
                
                # Get amount precision from market data or derive from min_amount
                amount_precision = market_data.get('amount_precision')
                if amount_precision is None:
                    # Derive precision from min_amount (e.g., 0.001 → 3 decimal places)
                    if min_amount > 0:
                        amount_precision = max(0, len(str(min_amount).split('.')[-1]) if '.' in str(min_amount) else 0)
                    else:
                        amount_precision = 6  # Default for crypto pairs
                else:
                    amount_precision = int(amount_precision)
                
                # Format amount with proper precision to fix floating point issues
                amount_decimal = Decimal(str(amount))  # Convert to string first to avoid float precision issues
                
                # Round to proper decimal places
                if amount_precision > 0:
                    amount_rounded = amount_decimal.quantize(Decimal('0.' + '0' * amount_precision), rounding=ROUND_HALF_UP)
                else:
                    amount_rounded = amount_decimal.quantize(Decimal('1'), rounding=ROUND_HALF_UP)
                
                amount_float = float(amount_rounded)
                
                # Ensure amount meets minimum requirement
                if amount_float < min_amount:
                    logger.warning(f"Adjusting amount from {amount_float} to minimum {min_amount}")
                    amount_float = min_amount
                
                # Format amount with proper decimal places
                if amount_precision > 0:
                    amount_str = f"{amount_float:.{amount_precision}f}".rstrip('0').rstrip('.')
                else:
                    amount_str = f"{int(amount_float)}"
                
                # Round price to proper tick size (existing logic)
                price_decimal = Decimal(str(price))
                tick_decimal = Decimal(str(tick_size))
                rounded_price_decimal = (price_decimal / tick_decimal).quantize(Decimal('1'), rounding=ROUND_HALF_UP) * tick_decimal
                price_float = float(rounded_price_decimal)
                
                # Format price with proper decimal places
                tick_precision = len(str(tick_size).split('.')[-1]) if '.' in str(tick_size) else 0
                price_str = f"{price_float:.{tick_precision}f}".rstrip('0').rstrip('.')
                
                logger.debug(f"Formatted order params: {amount_str} @ {price_str} (amount_precision={amount_precision}, tick_size={tick_size})")
                
            else:
                # Fallback for unknown markets - use reasonable defaults
                logger.warning(f"Market data not found for {market}, using fallback precision")
                
                # Fix floating point precision with defaults
                amount_decimal = Decimal(str(amount))
                amount_rounded = amount_decimal.quantize(Decimal('0.000001'), rounding=ROUND_HALF_UP)  # 6 decimal places default
                amount_str = f"{float(amount_rounded):.6f}".rstrip('0').rstrip('.')
                
                price_decimal = Decimal(str(price))
                price_rounded = price_decimal.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)  # 2 decimal places default for price
                price_str = f"{float(price_rounded):.2f}".rstrip('0').rstrip('.')
                
        except Exception as format_error:
            logger.warning(f"Could not format order parameters for {market}: {format_error}")
            # Fall back to basic formatting with floating point fix
            from decimal import Decimal, ROUND_HALF_UP
            try:
                amount_decimal = Decimal(str(amount))
                amount_rounded = amount_decimal.quantize(Decimal('0.000001'), rounding=ROUND_HALF_UP)
                amount_str = f"{float(amount_rounded):.6f}".rstrip('0').rstrip('.')
                
                price_str = f"{float(price):.4f}".rstrip('0').rstrip('.')
            except:
                # Last resort - basic string conversion
                amount_str = str(float(amount))
                price_str = str(float(price))
        
        return amount_str, price_str
    
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
            'market_type': 'FUTURES'
        }

        response = self._request(
            method='GET',
            endpoint='/v2/futures/pending-order',
            params=params,
            return_full=True
        )

        if isinstance(response, dict):
            orders = response.get('data', [])
        else:
            orders = response if isinstance(response, list) else []
            response = {'data': orders}

        if market:
            filtered_orders = [order for order in orders if order.get('market') == market]
            response['data'] = filtered_orders
            if 'pagination' in response:
                response['pagination']['total'] = len(filtered_orders)

        return response
    
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
    
    def get_user_deals(self, market: Optional[str] = None, side: Optional[str] = None,
                      start_time: Optional[int] = None, end_time: Optional[int] = None,
                      page: int = 1, limit: int = 100) -> Dict:
        """
        Get user transaction/fill history
        
        Args:
            market: Market name (e.g., 'BTCUSDT')
            side: Optional order side filter ('buy' or 'sell')
            start_time: Optional start timestamp in milliseconds
            end_time: Optional end timestamp in milliseconds
            page: Page number (default 1)
            limit: Number per page (default 100, max 500)
            
        Returns:
            Dictionary with user deals/fills data
        """
        params = {
            'market_type': 'FUTURES',
            'page': page,
            'limit': min(limit, 500)  # API max is 500
        }

        if market:
            params['market'] = market
        
        if side:
            params['side'] = side
        if start_time:
            params['start_time'] = start_time
        if end_time:
            params['end_time'] = end_time
            
        return self._request('GET', '/v2/futures/user-deals', params=params)
    
    def get_order_deals(self, market: str, order_id: int,
                       page: int = 1, limit: int = 100) -> Dict:
        """
        Get transaction/fill details for a specific order
        
        Args:
            market: Market name (e.g., 'BTCUSDT')
            order_id: Order ID to get fills for
            page: Page number (default 1)
            limit: Number per page (default 100)
            
        Returns:
            Dictionary with order fills data
        """
        params = {
            'market': market,
            'market_type': 'FUTURES',
            'order_id': order_id,
            'page': page,
            'limit': min(limit, 100)
        }
        
        return self._request('GET', '/v2/futures/order-deals', params=params)
    
    def get_batch_order_status(self, market: str, order_ids: List[int]) -> Dict:
        """
        Get status for multiple orders in a single request
        
        Args:
            market: Market name (e.g., 'BTCUSDT')
            order_ids: List of order IDs to query
            
        Returns:
            Dictionary with batch order status data
        """
        # Convert list of IDs to comma-separated string
        order_ids_str = ','.join(str(oid) for oid in order_ids)
        
        params = {
            'market': market,
            'order_ids': order_ids_str
        }
        
        return self._request('GET', '/v2/futures/batch-order-status', params=params)
    
    @retry_on_transient_error(config=COINEX_RETRY_CONFIG)
    def adjust_position_leverage(self, market: str, leverage: int, 
                               margin_mode: str = 'cross') -> Dict:
        """
        Adjust position leverage for a specific market with retry on transient errors
        
        Args:
            market: Market symbol (e.g., ETHUSDT)
            leverage: Leverage ratio (e.g., 2 for 2x leverage)
            margin_mode: Position type ('cross' or 'isolated')
            
        Returns:
            Response containing leverage adjustment result
            
        Raises:
            ValueError: On validation errors or API errors after retries
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
        
        # _request() returns only the 'data' portion and raises exception on API error
        # The retry decorator will handle transient errors like "service too busy"
        data_response = self._request('POST', '/v2/futures/adjust-position-leverage', data=data)
        
        # If we reach here, the API call was successful (no exception thrown)
        logger.info(f"Leverage set successfully for {market}: "
                  f"{data_response.get('leverage')}x {data_response.get('margin_mode')} margin")
        
        return data_response
    
    def get_current_leverage(self, market: str) -> Dict:
        """
        Get current leverage settings for a specific market
        
        Args:
            market: Market symbol (e.g., BTCUSDT)
            
        Returns:
            Dictionary containing current leverage and margin mode information
            
        Raises:
            ValueError: If market not found or API error
        """
        if not market:
            raise ValueError("Market symbol is required")
        
        try:
            # Get positions to check current leverage setting
            positions_response = self.get_positions(market=market)
            
            # Handle both list and dict responses
            positions = positions_response if isinstance(positions_response, list) else positions_response.get('data', [])
            
            # Look for position with this market
            for position in positions:
                if position.get('market') == market:
                    return {
                        'market': market,
                        'leverage': int(position.get('leverage', 1)),
                        'margin_mode': position.get('margin_mode', 'cross'),
                        'has_position': safe_float(position.get('open_interest', 0)) > 0,
                        'open_interest': safe_float(position.get('open_interest', 0))
                    }
            
            # If no position found in the response, it could mean:
            # 1. Leverage was never set (true default)
            # 2. Leverage was set but no position/orders exist
            # 3. Leverage was set and there are orders but no filled position yet
            
            # For safety, if we're being called during order conflict resolution,
            # we should assume leverage might already be set correctly
            logger.info(f"No position data found for {market} - leverage state uncertain")
            logger.info("⚠️ Cannot reliably determine current leverage without position data")
            
            return {
                'market': market,
                'leverage': None,  # Unknown - don't assume
                'margin_mode': 'cross',  # CoinEx default mode
                'has_position': False,
                'open_interest': 0.0,
                'leverage_unknown': True
            }
            
        except Exception as e:
            logger.error(f"Failed to get current leverage for {market}: {e}")
            raise ValueError(f"Could not retrieve leverage information: {e}")
    
    def analyze_complete_market_state(self, market: str) -> Dict:
        """
        Analyze complete market state including orders, positions, and leverage
        
        Args:
            market: Market symbol (e.g., BTCUSDT)
            
        Returns:
            Dictionary containing complete market analysis
        """
        try:
            logger.info(f"Analyzing complete market state for {market}")
            
            # Get current leverage information
            leverage_info = self.get_current_leverage(market)
            
            # Get pending orders
            orders_response = self.get_pending_orders(market=market)
            all_orders = orders_response.get('data', []) if isinstance(orders_response, dict) else orders_response
            
            # Filter orders for this specific market
            market_orders = [order for order in all_orders if order.get('market') == market]
            
            # Categorize orders
            buy_orders = [order for order in market_orders if order.get('side') == 'buy']
            sell_orders = [order for order in market_orders if order.get('side') == 'sell']
            
            # Analyze order situation
            analysis = {
                'market': market,
                'leverage_info': leverage_info,
                'total_orders': len(market_orders),
                'buy_orders': buy_orders,
                'sell_orders': sell_orders,
                'buy_count': len(buy_orders),
                'sell_count': len(sell_orders),
                'has_position': leverage_info['has_position'],
                'open_interest': leverage_info['open_interest'],
                'current_leverage': leverage_info['leverage'],
                'margin_mode': leverage_info['margin_mode']
            }
            
            # Determine situation category
            if analysis['buy_count'] == 0 and analysis['sell_count'] == 0:
                analysis['situation'] = 'no_orders'
            elif analysis['buy_count'] == 1 and analysis['sell_count'] == 0:
                analysis['situation'] = 'single_buy_order'
            elif analysis['buy_count'] > 1:
                analysis['situation'] = 'multiple_buy_orders_bug'
            elif analysis['sell_count'] > 0:
                analysis['situation'] = 'sell_orders_present'
            else:
                analysis['situation'] = 'mixed_orders'
            
            # Log summary
            logger.info(f"Market state analysis for {market}:")
            logger.info(f"  Situation: {analysis['situation']}")
            logger.info(f"  Orders: {analysis['buy_count']} buy, {analysis['sell_count']} sell")
            logger.info(f"  Position: {'Yes' if analysis['has_position'] else 'No'} "
                       f"({analysis['open_interest']} open_interest)")
            if leverage_info.get('leverage_unknown'):
                logger.info("  Current leverage: Unknown (no position data)")
            else:
                logger.info(f"  Current leverage: {analysis['current_leverage']}x {analysis['margin_mode']}")
            
            return analysis
            
        except Exception as e:
            logger.error(f"Failed to analyze market state for {market}: {e}")
            raise ValueError(f"Could not analyze market state: {e}")
    
    def handle_leverage_conflict_intelligently(self, market: str, target_leverage: int, margin_mode: str = 'cross') -> Dict:
        """
        Handle leverage setting conflicts with intelligent order management
        
        Args:
            market: Market symbol
            target_leverage: Desired leverage
            margin_mode: Margin mode (cross/isolated)
            
        Returns:
            Dictionary with resolution results and actions taken
        """
        logger.info(f"🔧 Handling leverage conflict for {market} - target: {target_leverage}x {margin_mode}")
        
        try:
            # Analyze current market state
            analysis = self.analyze_complete_market_state(market)
            
            # Check if leverage adjustment is even needed
            current_leverage = analysis.get('current_leverage')
            
            # Check if we have bot-created orders - if so, trust them and skip adjustment
            if current_leverage is None and analysis['buy_count'] > 0:
                # Check if any orders are bot-created
                bot_orders = [o for o in analysis.get('buy_orders', []) if o.get('client_id', '').startswith('DRA_')]
                if bot_orders:
                    logger.info(f"⚠️ Found {len(bot_orders)} bot-created order(s) - trusting leverage is correct")
                    logger.info("🛡️ Skipping leverage adjustment for bot orders to prevent unnecessary manipulation")
                    
                    return {
                        'action': 'bot_orders_trusted_early',
                        'success': True,
                        'bot_orders_count': len(bot_orders),
                        'message': f'Found {len(bot_orders)} bot order(s) - trusting leverage is already correct',
                        'trust_reason': 'Bot orders from previous runs are assumed to have correct leverage'
                    }
                
                # Only attempt direct adjustment for non-bot orders
                logger.info("⚠️ Current leverage unknown with non-bot orders - attempting direct adjustment")
                direct_result = self._attempt_direct_leverage_adjustment(market, target_leverage, margin_mode)
                
                # If direct adjustment succeeds, we're done
                if direct_result['success']:
                    return direct_result
                    
                # If it fails with "order exist", proceed with conflict resolution
                if "order exist" in str(direct_result.get('error', '')):
                    logger.info("Direct adjustment failed with 'order exist' - proceeding with conflict resolution")
                else:
                    # Other error - return the failure
                    return direct_result
                    
            # If we know the current leverage and it matches target, no change needed
            elif current_leverage == target_leverage and analysis['margin_mode'] == margin_mode:
                logger.info(f"✅ Leverage already correct for {market}: {target_leverage}x {margin_mode}")
                return {
                    'action': 'no_change_needed',
                    'current_leverage': current_leverage,
                    'target_leverage': target_leverage,
                    'success': True,
                    'message': 'Leverage already at target value'
                }
            
            # Handle different situations
            situation = analysis['situation']
            
            if situation == 'no_orders':
                # Simple case - no orders blocking leverage adjustment
                logger.info("No orders present, attempting direct leverage adjustment")
                return self._attempt_direct_leverage_adjustment(market, target_leverage, margin_mode)
                
            elif situation == 'single_buy_order':
                # Check if this is a bot-created order (client_id starts with 'DRA_')
                buy_order = analysis['buy_orders'][0] if analysis['buy_orders'] else {}
                client_id = buy_order.get('client_id', '')
                
                if client_id.startswith('DRA_'):
                    logger.info(f"📌 Found existing bot order: {client_id}")
                    logger.info("🛡️ CONSERVATIVE APPROACH: Bot-created orders are trusted to have correct leverage")
                    logger.info("⚠️ Skipping leverage adjustment to avoid unnecessary order manipulation")
                    
                    return {
                        'action': 'bot_order_trusted',
                        'success': True,
                        'bot_order_id': client_id,
                        'message': f'Bot order {client_id} trusted to have correct leverage - no changes made',
                        'trust_reason': 'Bot-created orders are assumed to have correct leverage from previous run'
                    }
                
                # Only manipulate non-bot orders
                logger.info("Non-bot order detected, will temporarily cancel and recreate")
                return self._handle_single_buy_order_conflict(market, target_leverage, margin_mode, analysis)
                
            elif situation == 'multiple_buy_orders_bug':
                # Critical bug - clean up and restore correct state
                logger.error("🚨 CRITICAL BUG: Multiple buy orders detected for Daily Range Strategy!")
                return self._handle_multiple_buy_orders_bug(market, target_leverage, margin_mode, analysis)
                
            elif situation == 'sell_orders_present':
                # Research needed - test if sell orders block leverage
                logger.warning("Sell orders present, testing leverage adjustment behavior")
                return self._handle_sell_orders_present(market, target_leverage, margin_mode, analysis)
                
            else:
                # Mixed or unknown situation - conservative approach
                logger.warning(f"Complex order situation detected: {situation}")
                return self._handle_complex_situation(market, target_leverage, margin_mode, analysis)
                
        except Exception as e:
            logger.error(f"Error in intelligent leverage conflict handling: {e}")
            return {
                'action': 'error',
                'success': False,
                'error': str(e),
                'message': f'Failed to handle leverage conflict: {e}'
            }
    
    def _attempt_direct_leverage_adjustment(self, market: str, target_leverage: int, margin_mode: str) -> Dict:
        """Attempt direct leverage adjustment without order conflicts"""
        try:
            logger.info(f"Attempting direct leverage adjustment: {market} -> {target_leverage}x {margin_mode}")
            result = self.adjust_position_leverage(market, target_leverage, margin_mode)
            
            logger.info(f"✅ Direct leverage adjustment successful for {market}")
            return {
                'action': 'direct_adjustment',
                'success': True,
                'result': result,
                'message': f'Leverage set to {target_leverage}x {margin_mode}'
            }
        except Exception as e:
            logger.error(f"Direct leverage adjustment failed: {e}")
            return {
                'action': 'direct_adjustment_failed',
                'success': False,
                'error': str(e),
                'message': f'Direct adjustment failed: {e}'
            }
    
    def _handle_single_buy_order_conflict(self, market: str, target_leverage: int, margin_mode: str, analysis: Dict) -> Dict:
        """Handle single buy order blocking leverage adjustment"""
        try:
            buy_order = analysis['buy_orders'][0]
            logger.info(f"Temporarily canceling single buy order: {buy_order.get('client_id', 'N/A')}")
            
            # Store order details for recreation
            order_details = {
                'market': buy_order.get('market'),
                'side': buy_order.get('side'),
                'amount': buy_order.get('amount'),
                'price': buy_order.get('price'),
                'client_id': buy_order.get('client_id'),
                'order_type': buy_order.get('type', 'limit')
            }
            
            # Verify order status before attempting cancellation
            client_id = buy_order.get('client_id')
            order_id = buy_order.get('order_id')
            
            logger.info("Verifying order status before cancellation...")
            
            try:
                # Check current order status
                if client_id:
                    order_status = self.get_order_status(market, client_id=client_id)
                elif order_id:
                    order_status = self.get_order_status(market, order_id=order_id)
                else:
                    raise ValueError("Cannot cancel order - no order_id or client_id found")
                
                current_status = order_status.get('status', 'unknown')
                logger.info(f"Order status check: {current_status}")
                
                # Only attempt cancellation if order is still pending
                if current_status in ['pending', 'partially_filled']:
                    logger.info(f"Order is {current_status}, proceeding with cancellation")
                    
                    # Cancel the order using order_id (required by CoinEx API)
                    if order_id:
                        cancel_result = self.cancel_order(market, order_id)
                    else:
                        logger.error(f"Cannot cancel order - missing order_id for {client_id}")
                        raise ValueError("order_id is required for cancellation")
                    
                    logger.info(f"Cancel order result: {cancel_result}")
                    
                elif current_status in ['filled', 'cancelled']:
                    logger.warning(f"Order is already {current_status}, skipping cancellation")
                    # Continue with leverage adjustment anyway
                else:
                    logger.warning(f"Unknown order status: {current_status}, attempting cancellation anyway")
                    
                    if order_id:
                        cancel_result = self.cancel_order(market, order_id)
                    else:
                        logger.error(f"Cannot cancel order - missing order_id for {client_id}")
                        raise ValueError("order_id is required for cancellation")
                        
            except Exception as cancel_error:
                logger.error(f"Order cancellation failed: {cancel_error}")
                logger.error(f"Order details: market={market}, client_id={client_id}, order_id={order_id}")
                
                # Don't give up completely - check if the error indicates order is already gone
                if "not found" in str(cancel_error).lower() or "invalid argument" in str(cancel_error).lower():
                    logger.info("Order may have been filled or already cancelled, continuing with leverage adjustment")
                else:
                    # Re-raise other types of errors
                    raise ValueError(f"Failed to cancel order: {cancel_error}")
            
            logger.info("Buy order canceled, now setting leverage")
            
            # Set leverage
            leverage_result = self.adjust_position_leverage(market, target_leverage, margin_mode)
            
            logger.info("Leverage set, recreating buy order")
            
            # Recreate the order with proper parameter formatting and validation
            logger.info(f"Placing buy order: {order_details['amount']} {market} @ {order_details['price']}")
            
            # Validate and format parameters
            amount_str = str(float(order_details['amount']))  # Convert to float then string to normalize
            price_str = str(float(order_details['price']))    # Convert to float then string to normalize
            
            # Get market info to ensure proper precision
            try:
                market_info = self.get_futures_markets()
                market_data = next((m for m in market_info if m.get('market') == market), None)
                if market_data:
                    min_amount = safe_float(market_data.get('min_amount', 0))
                    tick_size = safe_float(market_data.get('tick_size', 0.0001))
                    
                    # Ensure amount meets minimum requirement
                    amount_float = float(amount_str)
                    if amount_float < min_amount:
                        logger.warning(f"Adjusting amount from {amount_float} to minimum {min_amount}")
                        amount_str = str(min_amount)
                    
                    # Round price to proper tick size
                    price_float = float(price_str)
                    rounded_price = round(price_float / tick_size) * tick_size
                    price_str = f"{rounded_price:.{len(str(tick_size).split('.')[-1])}f}"
                    
                    logger.info(f"Formatted order: {amount_str} @ {price_str} (tick_size={tick_size})")
                    
            except Exception as format_error:
                logger.warning(f"Could not validate market parameters: {format_error}")
            
            try:
                recreate_result = self.place_order(
                    market=order_details['market'],
                    side=order_details['side'], 
                    amount=amount_str,
                    price=price_str,
                    order_type=order_details['order_type'],
                    client_id=f"recreated_{order_details['client_id']}",
                    is_hide=True  # Use hidden orders as in normal bot operation
                )
                logger.info(f"✅ Order recreation successful: {recreate_result.get('order_id', 'N/A')}")
                
            except Exception as order_error:
                logger.error(f"Failed to recreate order: {order_error}")
                logger.error(f"Order details used: market={order_details['market']}, "
                           f"side={order_details['side']}, amount={order_details['amount']}, "
                           f"price={order_details['price']}, type={order_details['order_type']}")
                
                # Return partial success - leverage was set, but order recreation failed
                return {
                    'action': 'single_buy_order_partial',
                    'success': False,  # Mark as failed since order wasn't recreated
                    'canceled_order': order_details,
                    'leverage_result': leverage_result,
                    'recreation_error': str(order_error),
                    'message': f'Leverage set to {target_leverage}x but order recreation failed: {order_error}'
                }
            
            logger.info(f"✅ Successfully handled single buy order conflict for {market}")
            return {
                'action': 'single_buy_order_handled',
                'success': True,
                'canceled_order': order_details,
                'leverage_result': leverage_result,
                'recreated_order': recreate_result,
                'message': f'Temporarily canceled and recreated buy order, leverage set to {target_leverage}x'
            }
            
        except Exception as e:
            logger.error(f"Failed to handle single buy order conflict: {e}")
            return {
                'action': 'single_buy_order_failed',
                'success': False,
                'error': str(e),
                'message': f'Failed to handle single buy order: {e}'
            }
    
    def _handle_multiple_buy_orders_bug(self, market: str, target_leverage: int, margin_mode: str, analysis: Dict) -> Dict:
        """Handle critical bug where multiple buy orders exist"""
        try:
            buy_orders = analysis['buy_orders']
            logger.error(f"🚨 CRITICAL BUG: {len(buy_orders)} buy orders found for {market}")
            logger.error("Daily Range Strategy should only have 1 buy order at a time!")
            
            # Log all problematic orders
            for i, order in enumerate(buy_orders):
                logger.error(f"  Buy order {i+1}: {order.get('client_id', 'N/A')} - "
                           f"{order.get('amount')} @ ${order.get('price')}")
            
            # Cancel ALL buy orders to clean up the bug
            canceled_orders = []
            for order in buy_orders:
                try:
                    if order.get('order_id'):
                        self.cancel_order(market=market, order_id=order['order_id'])
                    else:
                        logger.error(f"Cannot cancel order - missing order_id for {order.get('client_id', 'N/A')}")
                    canceled_orders.append(order)
                    logger.info(f"Canceled problematic buy order: {order.get('client_id', 'N/A')}")
                except Exception as cancel_error:
                    logger.error(f"Failed to cancel order {order.get('client_id', 'N/A')}: {cancel_error}")
            
            # Set leverage
            logger.info("Setting leverage after cleaning up multiple buy orders bug")
            leverage_result = self.adjust_position_leverage(market, target_leverage, margin_mode)
            
            logger.error(f"🔧 Bug cleanup completed for {market}. "
                        f"Canceled {len(canceled_orders)} problematic buy orders.")
            logger.error("⚠️ Strategy logic needs investigation to prevent multiple buy orders!")
            
            return {
                'action': 'multiple_buy_orders_bug_cleaned',
                'success': True,
                'bug_detected': True,
                'canceled_orders': canceled_orders,
                'leverage_result': leverage_result,
                'message': f'CRITICAL BUG FIXED: Canceled {len(canceled_orders)} buy orders, set leverage to {target_leverage}x. '
                          f'Investigate strategy logic!'
            }
            
        except Exception as e:
            logger.error(f"Failed to handle multiple buy orders bug: {e}")
            return {
                'action': 'multiple_buy_orders_bug_failed',
                'success': False,
                'bug_detected': True,
                'error': str(e),
                'message': f'Failed to clean up multiple buy orders bug: {e}'
            }
    
    def _handle_sell_orders_present(self, market: str, target_leverage: int, margin_mode: str, analysis: Dict) -> Dict:
        """Test behavior when sell orders are present"""
        try:
            logger.info(f"Testing leverage adjustment with {analysis['sell_count']} sell orders present")
            
            # Attempt leverage adjustment to test if sell orders block it
            try:
                leverage_result = self.adjust_position_leverage(market, target_leverage, margin_mode)
                logger.info("✅ Leverage adjustment succeeded with sell orders present!")
                
                return {
                    'action': 'leverage_set_with_sell_orders',
                    'success': True,
                    'leverage_result': leverage_result,
                    'sell_orders_count': analysis['sell_count'],
                    'message': f'Leverage set to {target_leverage}x despite {analysis["sell_count"]} sell orders present'
                }
                
            except Exception as leverage_error:
                if "order exist" in str(leverage_error).lower():
                    logger.warning("⚠️ Sell orders DO block leverage adjustment")
                    logger.info("Continuing with existing leverage to preserve sell orders")
                    
                    return {
                        'action': 'leverage_blocked_by_sell_orders',
                        'success': False,
                        'sell_orders_count': analysis['sell_count'],
                        'current_leverage': analysis['current_leverage'],
                        'message': f'Leverage adjustment blocked by {analysis["sell_count"]} sell orders. '
                                  f'Continuing with {analysis["current_leverage"]}x leverage.'
                    }
                else:
                    # Different error, re-raise
                    raise leverage_error
                    
        except Exception as e:
            logger.error(f"Error testing sell orders behavior: {e}")
            return {
                'action': 'sell_orders_test_failed',
                'success': False,
                'error': str(e),
                'message': f'Failed to test sell orders behavior: {e}'
            }
    
    def _handle_complex_situation(self, market: str, target_leverage: int, margin_mode: str, analysis: Dict) -> Dict:
        """Handle complex or unknown order situations conservatively"""
        logger.warning(f"Complex situation for {market}: {analysis['situation']}")
        logger.warning(f"Buy orders: {analysis['buy_count']}, Sell orders: {analysis['sell_count']}")
        logger.warning(f"Position: {'Yes' if analysis['has_position'] else 'No'}")
        logger.warning("Taking conservative approach - continuing with existing leverage")
        
        return {
            'action': 'conservative_fallback',
            'success': False,
            'situation': analysis['situation'],
            'current_leverage': analysis['current_leverage'],
            'target_leverage': target_leverage,
            'message': f'Complex situation detected. Continuing with existing {analysis["current_leverage"]}x leverage.'
        }
    
    def close(self):
        """Close the session"""
        self.session.close()
