"""
CoinEx WebSocket Client
Handles real-time data streams for order monitoring and market data
"""
import json
import time
import asyncio
import threading
import gzip
from typing import Dict, Optional, Callable, Any, List
from dataclasses import dataclass
from enum import Enum

import websockets
from websockets.exceptions import ConnectionClosed, InvalidMessage

from config.settings import COINEX_WS_URL, WS_RECONNECT_DELAY, WS_HEARTBEAT_INTERVAL
from src.exchange.auth import CoinExAuth
from src.utils.logger import get_logger


logger = get_logger(__name__)


class SubscriptionType(Enum):
    """WebSocket subscription types"""
    ORDER = "order"
    USER_DEALS = "user_deals"
    DEALS = "deals"
    DEPTH = "depth"


@dataclass
class WebSocketMessage:
    """WebSocket message structure"""
    method: str
    params: Dict[str, Any]
    id: Optional[int] = None


class CoinExWebSocketClient:
    """WebSocket client for CoinEx futures trading"""
    
    def __init__(self, auth: Optional[CoinExAuth] = None):
        """
        Initialize WebSocket client
        
        Args:
            auth: Authentication instance
        """
        self.auth = auth or CoinExAuth()
        self.websocket = None
        self.is_connected = False
        self.is_authenticated = False
        self.subscriptions: Dict[str, Dict] = {}
        self.message_handlers: Dict[str, Callable] = {}
        self.message_id = 0
        self.reconnect_task = None
        self.heartbeat_task = None
        self._loop = None
        self._thread = None
        
    def register_handler(self, method: str, handler: Callable[[Dict], None]):
        """
        Register a message handler for specific WebSocket methods
        
        Args:
            method: WebSocket method (e.g., 'order.update', 'user_deals.update')
            handler: Callback function to handle the message
        """
        self.message_handlers[method] = handler
        logger.info(f"Registered handler for method: {method}")
    
    def get_next_message_id(self) -> int:
        """Get next message ID for request tracking"""
        self.message_id += 1
        return self.message_id
    
    async def connect(self) -> bool:
        """
        Connect to CoinEx WebSocket server
        
        Returns:
            True if connection successful, False otherwise
        """
        try:
            logger.info(f"Connecting to CoinEx WebSocket: {COINEX_WS_URL}")
            self.websocket = await websockets.connect(
                COINEX_WS_URL,
                ping_interval=20,
                ping_timeout=10,
                close_timeout=10
            )
            self.is_connected = True
            logger.info("WebSocket connection established")
            
            # Start message handling
            asyncio.create_task(self._handle_messages())
            
            # Start heartbeat
            self.heartbeat_task = asyncio.create_task(self._heartbeat())
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to connect to WebSocket: {e}")
            self.is_connected = False
            return False
    
    async def authenticate(self) -> bool:
        """
        Authenticate WebSocket connection
        
        Returns:
            True if authentication successful, False otherwise
        """
        if not self.is_connected:
            logger.error("Cannot authenticate: WebSocket not connected")
            return False
        
        try:
            # Reset authentication state
            self.is_authenticated = False
            
            # Generate authentication data
            auth_data = self.auth.sign_websocket_message()
            
            # Send authentication message
            auth_message = {
                "method": "server.sign",
                "params": auth_data,
                "id": self.get_next_message_id()
            }
            
            await self._send_message(auth_message)
            logger.info("Authentication message sent")
            
            # Wait for authentication response with timeout
            max_wait = 5  # seconds
            wait_interval = 0.1
            elapsed = 0
            
            while elapsed < max_wait and not self.is_authenticated:
                await asyncio.sleep(wait_interval)
                elapsed += wait_interval
            
            if self.is_authenticated:
                logger.info("Authentication confirmed")
                return True
            else:
                logger.error("Authentication timeout - no response received")
                return False
            
        except Exception as e:
            logger.error(f"Authentication failed: {e}")
            return False
    
    async def subscribe_orders(self, markets: Optional[List[str]] = None) -> bool:
        """
        Subscribe to order updates
        
        Args:
            markets: List of markets to monitor, None for all markets
            
        Returns:
            True if subscription successful, False otherwise
        """
        if not self.is_authenticated:
            logger.warning("Attempting to subscribe to orders without authentication")
        
        try:
            # CoinEx expects market_list parameter, empty array for all markets
            market_list = markets if markets is not None else []
            params = {"market_list": market_list}
            
            message = {
                "method": "order.subscribe",
                "params": params,
                "id": self.get_next_message_id()
            }
            
            await self._send_message(message)
            self.subscriptions["order"] = params
            logger.info(f"Subscribed to order updates for markets: {market_list if market_list else 'ALL'}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to subscribe to orders: {e}")
            return False
    
    async def subscribe_user_deals(self, markets: Optional[List[str]] = None) -> bool:
        """
        Subscribe to user deal/trade updates
        
        Args:
            markets: List of markets to monitor, None for all markets
            
        Returns:
            True if subscription successful, False otherwise
        """
        if not self.is_authenticated:
            logger.warning("Attempting to subscribe to user deals without authentication")
        
        try:
            # CoinEx expects market_list parameter, empty array for all markets
            market_list = markets if markets is not None else []
            params = {"market_list": market_list}
            
            message = {
                "method": "user_deals.subscribe",
                "params": params,
                "id": self.get_next_message_id()
            }
            
            await self._send_message(message)
            self.subscriptions["user_deals"] = params
            logger.info(f"Subscribed to user deals for markets: {market_list if market_list else 'ALL'}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to subscribe to user deals: {e}")
            return False
    
    async def subscribe_market_deals(self, markets: List[str]) -> bool:
        """
        Subscribe to public market deals
        
        Args:
            markets: List of markets to monitor
            
        Returns:
            True if subscription successful, False otherwise
        """
        try:
            # CoinEx expects market_list parameter for deals subscription too
            params = {"market_list": markets}
            
            message = {
                "method": "deals.subscribe",
                "params": params,
                "id": self.get_next_message_id()
            }
            
            await self._send_message(message)
            self.subscriptions["deals"] = params
            logger.info(f"Subscribed to market deals for: {markets}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to subscribe to market deals: {e}")
            return False
    
    async def _send_message(self, message: Dict) -> None:
        """
        Send message to WebSocket server
        
        Args:
            message: Message dictionary to send
        """
        if not self.websocket or not self.is_connected:
            raise ConnectionError("WebSocket not connected")
        
        message_str = json.dumps(message)
        logger.debug(f"Sending WebSocket message: {message_str}")
        await self.websocket.send(message_str)
    
    async def _handle_messages(self) -> None:
        """Handle incoming WebSocket messages"""
        try:
            async for raw_message in self.websocket:
                try:
                    # Check if message is binary (compressed)
                    if isinstance(raw_message, bytes):
                        try:
                            # Decompress gzip message
                            decompressed = gzip.decompress(raw_message)
                            message_str = decompressed.decode('utf-8')
                        except gzip.BadGzipFile:
                            # Not gzipped, try direct decode
                            message_str = raw_message.decode('utf-8')
                    else:
                        message_str = raw_message
                    
                    message = json.loads(message_str)
                    logger.debug(f"Received WebSocket message: {message}")
                    
                    # Handle different message types
                    method = message.get("method")
                    
                    # If message has an ID, it's a response to our request
                    if "id" in message and method is None:
                        self._handle_generic_response(message)
                    elif method == "server.sign":
                        # Authentication response
                        self._handle_auth_response(message)
                    elif method in ["order.update", "user_deals.update", "deals.update", "depth.update"]:
                        # Subscription updates
                        self._handle_subscription_update(message)
                    elif "error" in message and "id" in message:
                        # Error response to a request
                        self._handle_error_response(message)
                    else:
                        # Other messages
                        self._handle_generic_response(message)
                        
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse WebSocket message: {e}")
                    logger.debug(f"Raw message type: {type(raw_message)}, content: {raw_message[:100] if isinstance(raw_message, (str, bytes)) else raw_message}")
                except Exception as e:
                    logger.error(f"Error handling WebSocket message: {e}")
                    
        except ConnectionClosed:
            logger.warning("WebSocket connection closed")
            self.is_connected = False
            self.is_authenticated = False
        except Exception as e:
            logger.error(f"WebSocket message handling error: {e}")
            self.is_connected = False
    
    def _handle_auth_response(self, message: Dict) -> None:
        """Handle authentication response"""
        code = message.get("code")
        msg = message.get("message", "")
        error = message.get("error")
        
        # CoinEx uses code 0 for success
        if code == 0:
            logger.info("WebSocket authentication successful")
            self.is_authenticated = True
        elif error:
            logger.error(f"Authentication failed: {error}")
            self.is_authenticated = False
        elif code is not None and code != 0:
            logger.error(f"Authentication failed with code {code}: {msg}")
            self.is_authenticated = False
        else:
            logger.warning(f"Unexpected auth response: {message}")
            self.is_authenticated = False
    
    def _handle_subscription_update(self, message: Dict) -> None:
        """Handle subscription update messages"""
        method = message.get("method")
        params = message.get("params", {})
        
        # Call registered handler if available
        if method in self.message_handlers:
            try:
                self.message_handlers[method](params)
            except Exception as e:
                logger.error(f"Error in message handler for {method}: {e}")
        else:
            logger.debug(f"No handler registered for method: {method}")
    
    def _handle_error_response(self, message: Dict) -> None:
        """Handle error responses"""
        error = message.get("error", {})
        logger.error(f"WebSocket error: {error}")
    
    def _handle_generic_response(self, message: Dict) -> None:
        """Handle generic responses"""
        # Check if this is a response to our request
        msg_id = message.get("id")
        code = message.get("code")
        msg_text = message.get("message", "")
        error = message.get("error")
        
        if msg_id is not None:
            # This is a response to a specific request
            if code is not None:
                if code == 0:
                    logger.debug(f"Request {msg_id} successful")
                    
                    # Check if this was an auth request by looking at recent message IDs
                    # (auth requests typically have low IDs)
                    if msg_id <= 5 and not self.is_authenticated:
                        logger.info("Authentication confirmation received")
                        self.is_authenticated = True
                else:
                    logger.error(f"Request {msg_id} failed with code {code}: {msg_text}")
            elif error:
                logger.error(f"Request {msg_id} failed: {error}")
            else:
                logger.debug(f"Request {msg_id} response: {message}")
        else:
            logger.debug(f"Generic WebSocket response: {message}")
    
    async def _heartbeat(self) -> None:
        """Send periodic heartbeat to keep connection alive"""
        while self.is_connected:
            try:
                await asyncio.sleep(WS_HEARTBEAT_INTERVAL)
                if self.is_connected and self.websocket:
                    # Send ping message
                    ping_message = {
                        "method": "server.ping",
                        "params": {},
                        "id": self.get_next_message_id()
                    }
                    await self._send_message(ping_message)
                    logger.debug("Sent WebSocket heartbeat")
            except Exception as e:
                logger.error(f"Heartbeat error: {e}")
                break
    
    async def disconnect(self) -> None:
        """Disconnect from WebSocket server"""
        try:
            self.is_connected = False
            self.is_authenticated = False
            
            # Cancel tasks
            if self.heartbeat_task:
                self.heartbeat_task.cancel()
                
            # Close WebSocket connection
            if self.websocket:
                await self.websocket.close()
                
            logger.info("WebSocket disconnected")
            
        except Exception as e:
            logger.error(f"Error during WebSocket disconnect: {e}")
    
    async def reconnect(self) -> bool:
        """
        Reconnect to WebSocket server
        
        Returns:
            True if reconnection successful, False otherwise
        """
        logger.info("Attempting WebSocket reconnection...")
        
        # Disconnect first
        await self.disconnect()
        
        # Wait before reconnecting
        await asyncio.sleep(WS_RECONNECT_DELAY)
        
        # Reconnect
        if await self.connect():
            # Re-authenticate if we have credentials
            if await self.authenticate():
                # Re-subscribe to previous subscriptions
                for sub_type, params in self.subscriptions.items():
                    if sub_type == "order":
                        await self.subscribe_orders(params.get("market_list"))
                    elif sub_type == "user_deals":
                        await self.subscribe_user_deals(params.get("market_list"))
                    elif sub_type == "deals":
                        await self.subscribe_market_deals(params.get("market_list", []))
                
                logger.info("WebSocket reconnection successful")
                return True
        
        logger.error("WebSocket reconnection failed")
        return False
    
    def start_background_thread(self) -> None:
        """Start WebSocket client in background thread"""
        if self._thread and self._thread.is_alive():
            logger.warning("WebSocket client already running in background")
            return
        
        def run_websocket():
            """Run WebSocket in event loop"""
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            
            async def websocket_main():
                """Main WebSocket coroutine"""
                while True:
                    try:
                        if await self.connect():
                            if await self.authenticate():
                                # Keep connection alive
                                while self.is_connected:
                                    await asyncio.sleep(1)
                        
                        # Reconnect on failure
                        if not await self.reconnect():
                            logger.error("Failed to reconnect, waiting before retry...")
                            await asyncio.sleep(WS_RECONNECT_DELAY * 2)
                            
                    except Exception as e:
                        logger.error(f"WebSocket main loop error: {e}")
                        await asyncio.sleep(WS_RECONNECT_DELAY)
            
            self._loop.run_until_complete(websocket_main())
        
        self._thread = threading.Thread(target=run_websocket, daemon=True)
        self._thread.start()
        logger.info("WebSocket client started in background thread")
    
    def stop_background_thread(self) -> None:
        """Stop WebSocket client background thread"""
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        
        logger.info("WebSocket client background thread stopped")