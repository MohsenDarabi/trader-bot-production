"""
SQLite database schema and state persistence for trading bot
"""
import sqlite3
import json
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any
from pathlib import Path
import threading

from config.settings import DATABASE_PATH
from src.utils.logger import get_logger


logger = get_logger(__name__)


class DatabaseManager:
    """Manages SQLite database operations with thread safety"""
    
    def __init__(self, db_path: str = DATABASE_PATH):
        """
        Initialize database manager
        
        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path
        self._lock = threading.Lock()
        self._ensure_database_exists()
        self._create_tables()
    
    def _ensure_database_exists(self):
        """Ensure database directory exists"""
        db_dir = Path(self.db_path).parent
        db_dir.mkdir(parents=True, exist_ok=True)
    
    def _get_connection(self) -> sqlite3.Connection:
        """Get database connection with proper configuration"""
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row  # Enable dict-like access
        conn.execute("PRAGMA journal_mode=WAL")  # Enable WAL mode for better concurrency
        conn.execute("PRAGMA synchronous=NORMAL")  # Balance safety and performance
        return conn
    
    def _create_tables(self):
        """Create database tables if they don't exist"""
        with self._lock:
            conn = self._get_connection()
            try:
                # Positions table
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS positions (
                        market TEXT PRIMARY KEY,
                        side TEXT NOT NULL,
                        size REAL NOT NULL,
                        avg_entry_price REAL NOT NULL,
                        total_cost REAL NOT NULL,
                        unrealized_pnl REAL DEFAULT 0,
                        liquidation_price REAL DEFAULT 0,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                """)
                
                # Orders table
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS orders (
                        client_id TEXT PRIMARY KEY,
                        market TEXT NOT NULL,
                        side TEXT NOT NULL,
                        order_type TEXT NOT NULL,
                        amount REAL NOT NULL,
                        price REAL NOT NULL,
                        status TEXT NOT NULL,
                        exchange_order_id INTEGER,
                        filled_amount REAL DEFAULT 0,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                """)
                
                # Daily signals table
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS daily_signals (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        market TEXT NOT NULL,
                        date TEXT NOT NULL,
                        buy_price REAL NOT NULL,
                        sell_price REAL NOT NULL,
                        range_value REAL NOT NULL,
                        previous_high REAL NOT NULL,
                        previous_low REAL NOT NULL,
                        created_at TEXT NOT NULL,
                        UNIQUE(market, date)
                    )
                """)
                
                # Trade history table
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS trade_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        market TEXT NOT NULL,
                        side TEXT NOT NULL,
                        quantity REAL NOT NULL,
                        price REAL NOT NULL,
                        fees REAL NOT NULL,
                        pnl REAL,
                        order_client_id TEXT,
                        exchange_trade_id TEXT,
                        executed_at TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )
                """)
                
                # Bot state table (for configuration and status)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS bot_state (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                """)
                
                # Create indexes for better performance
                conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_market ON orders(market)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_signals_market_date ON daily_signals(market, date)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_market ON trade_history(market)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_executed_at ON trade_history(executed_at)")
                
                conn.commit()
                logger.info("Database tables created/verified successfully")
                
            except Exception as e:
                logger.error(f"Failed to create database tables: {e}")
                raise
            finally:
                conn.close()
    
    # Position persistence methods
    
    def save_position(self, position_dict: Dict) -> bool:
        """
        Save or update position in database
        
        Args:
            position_dict: Position data dictionary
            
        Returns:
            True if successful
        """
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute("""
                    INSERT OR REPLACE INTO positions 
                    (market, side, size, avg_entry_price, total_cost, unrealized_pnl, 
                     liquidation_price, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    position_dict['market'],
                    position_dict['side'],
                    position_dict['size'],
                    position_dict['avg_entry_price'],
                    position_dict['total_cost'],
                    position_dict['unrealized_pnl'],
                    position_dict['liquidation_price'],
                    position_dict['created_at'],
                    position_dict['updated_at']
                ))
                conn.commit()
                return True
                
            except Exception as e:
                logger.error(f"Failed to save position {position_dict['market']}: {e}")
                return False
            finally:
                conn.close()
    
    def load_positions(self) -> List[Dict]:
        """
        Load all positions from database
        
        Returns:
            List of position dictionaries
        """
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.execute("SELECT * FROM positions")
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
                
            except Exception as e:
                logger.error(f"Failed to load positions: {e}")
                return []
            finally:
                conn.close()
    
    def delete_position(self, market: str) -> bool:
        """
        Delete position from database
        
        Args:
            market: Market symbol
            
        Returns:
            True if successful
        """
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute("DELETE FROM positions WHERE market = ?", (market,))
                conn.commit()
                return True
                
            except Exception as e:
                logger.error(f"Failed to delete position {market}: {e}")
                return False
            finally:
                conn.close()
    
    # Order persistence methods
    
    def save_order(self, order_dict: Dict) -> bool:
        """
        Save or update order in database
        
        Args:
            order_dict: Order data dictionary
            
        Returns:
            True if successful
        """
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute("""
                    INSERT OR REPLACE INTO orders 
                    (client_id, market, side, order_type, amount, price, status, 
                     exchange_order_id, filled_amount, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    order_dict['client_id'],
                    order_dict['market'],
                    order_dict['side'],
                    order_dict['order_type'],
                    order_dict['amount'],
                    order_dict['price'],
                    order_dict['status'],
                    order_dict.get('exchange_order_id'),
                    order_dict['filled_amount'],
                    order_dict['created_at'],
                    order_dict['updated_at']
                ))
                conn.commit()
                return True
                
            except Exception as e:
                logger.error(f"Failed to save order {order_dict['client_id']}: {e}")
                return False
            finally:
                conn.close()
    
    def load_active_orders(self) -> List[Dict]:
        """
        Load active orders from database
        
        Returns:
            List of active order dictionaries
        """
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.execute("""
                    SELECT * FROM orders 
                    WHERE status IN ('pending', 'partially_filled')
                    ORDER BY created_at
                """)
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
                
            except Exception as e:
                logger.error(f"Failed to load active orders: {e}")
                return []
            finally:
                conn.close()
    
    def delete_order(self, client_id: str) -> bool:
        """
        Delete order from database
        
        Args:
            client_id: Client order ID
            
        Returns:
            True if successful
        """
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute("DELETE FROM orders WHERE client_id = ?", (client_id,))
                conn.commit()
                return True
                
            except Exception as e:
                logger.error(f"Failed to delete order {client_id}: {e}")
                return False
            finally:
                conn.close()
    
    # Signal persistence methods
    
    def save_daily_signal(self, signal_dict: Dict) -> bool:
        """
        Save daily signal in database
        
        Args:
            signal_dict: Signal data dictionary
            
        Returns:
            True if successful
        """
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute("""
                    INSERT OR REPLACE INTO daily_signals 
                    (market, date, buy_price, sell_price, range_value, 
                     previous_high, previous_low, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    signal_dict['market'],
                    signal_dict['date'],
                    signal_dict['buy_price'],
                    signal_dict['sell_price'],
                    signal_dict['range_value'],
                    signal_dict['previous_high'],
                    signal_dict['previous_low'],
                    signal_dict['created_at']
                ))
                conn.commit()
                return True
                
            except Exception as e:
                logger.error(f"Failed to save signal for {signal_dict['market']}: {e}")
                return False
            finally:
                conn.close()
    
    def load_daily_signal(self, market: str, date: str) -> Optional[Dict]:
        """
        Load daily signal for market and date
        
        Args:
            market: Market symbol
            date: Date string (YYYY-MM-DD)
            
        Returns:
            Signal dictionary or None
        """
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.execute("""
                    SELECT * FROM daily_signals 
                    WHERE market = ? AND date = ?
                """, (market, date))
                row = cursor.fetchone()
                return dict(row) if row else None
                
            except Exception as e:
                logger.error(f"Failed to load signal for {market} on {date}: {e}")
                return None
            finally:
                conn.close()
    
    # Trade history methods
    
    def save_trade(self, trade_dict: Dict) -> bool:
        """
        Save trade execution in history
        
        Args:
            trade_dict: Trade data dictionary
            
        Returns:
            True if successful
        """
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute("""
                    INSERT INTO trade_history 
                    (market, side, quantity, price, fees, pnl, order_client_id, 
                     exchange_trade_id, executed_at, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    trade_dict['market'],
                    trade_dict['side'],
                    trade_dict['quantity'],
                    trade_dict['price'],
                    trade_dict['fees'],
                    trade_dict.get('pnl'),
                    trade_dict.get('order_client_id'),
                    trade_dict.get('exchange_trade_id'),
                    trade_dict['executed_at'],
                    datetime.now(timezone.utc).isoformat()
                ))
                conn.commit()
                return True
                
            except Exception as e:
                logger.error(f"Failed to save trade: {e}")
                return False
            finally:
                conn.close()
    
    # Bot state methods
    
    def save_bot_state(self, key: str, value: Any) -> bool:
        """
        Save bot state value
        
        Args:
            key: State key
            value: State value (will be JSON serialized)
            
        Returns:
            True if successful
        """
        with self._lock:
            conn = self._get_connection()
            try:
                value_str = json.dumps(value) if not isinstance(value, str) else value
                conn.execute("""
                    INSERT OR REPLACE INTO bot_state (key, value, updated_at)
                    VALUES (?, ?, ?)
                """, (key, value_str, datetime.now(timezone.utc).isoformat()))
                conn.commit()
                return True
                
            except Exception as e:
                logger.error(f"Failed to save bot state {key}: {e}")
                return False
            finally:
                conn.close()
    
    def load_bot_state(self, key: str, default: Any = None) -> Any:
        """
        Load bot state value
        
        Args:
            key: State key
            default: Default value if not found
            
        Returns:
            State value or default
        """
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.execute("SELECT value FROM bot_state WHERE key = ?", (key,))
                row = cursor.fetchone()
                
                if row:
                    try:
                        return json.loads(row[0])
                    except json.JSONDecodeError:
                        return row[0]  # Return as string if not JSON
                
                return default
                
            except Exception as e:
                logger.error(f"Failed to load bot state {key}: {e}")
                return default
            finally:
                conn.close()
    
    def cleanup_old_data(self, days: int = 30) -> bool:
        """
        Clean up old data to prevent database bloat
        
        Args:
            days: Number of days to keep
            
        Returns:
            True if successful
        """
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)
        cutoff_str = cutoff_date.isoformat()
        
        with self._lock:
            conn = self._get_connection()
            try:
                # Delete old completed orders
                conn.execute("""
                    DELETE FROM orders 
                    WHERE status IN ('filled', 'cancelled') 
                    AND updated_at < ?
                """, (cutoff_str,))
                
                # Delete old signals (keep last 7 days)
                signal_cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
                conn.execute("DELETE FROM daily_signals WHERE created_at < ?", (signal_cutoff,))
                
                conn.commit()
                logger.info(f"Cleaned up data older than {days} days")
                return True
                
            except Exception as e:
                logger.error(f"Failed to cleanup old data: {e}")
                return False
            finally:
                conn.close()
    
    # Signal persistence methods
    
    def save_daily_signal(self, signal_dict: Dict) -> bool:
        """
        Save or update daily signal in database
        
        Args:
            signal_dict: Signal data dictionary
            
        Returns:
            True if successful
        """
        with self._lock:
            conn = self._get_connection()
            try:
                conn.execute("""
                    INSERT OR REPLACE INTO daily_signals 
                    (market, date, buy_price, sell_price, range_value, previous_high, previous_low, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    signal_dict['market'],
                    signal_dict['date'],
                    signal_dict['buy_price'],
                    signal_dict['sell_price'],
                    signal_dict['range_value'],
                    signal_dict['previous_high'],
                    signal_dict['previous_low'],
                    signal_dict['created_at']
                ))
                conn.commit()
                logger.debug(f"Saved daily signal for {signal_dict['market']} on {signal_dict['date']}")
                return True
                
            except Exception as e:
                logger.error(f"Failed to save daily signal: {e}")
                return False
            finally:
                conn.close()
    
    def load_daily_signal(self, market: str, date: str) -> Optional[Dict]:
        """
        Load daily signal from database
        
        Args:
            market: Market symbol
            date: Date in YYYY-MM-DD format
            
        Returns:
            Signal dictionary or None if not found
        """
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.execute("""
                    SELECT market, date, buy_price, sell_price, range_value, 
                           previous_high, previous_low, created_at
                    FROM daily_signals 
                    WHERE market = ? AND date = ?
                """, (market, date))
                
                row = cursor.fetchone()
                if row:
                    return {
                        'market': row['market'],
                        'date': row['date'],
                        'buy_price': row['buy_price'],
                        'sell_price': row['sell_price'],
                        'range_value': row['range_value'],
                        'previous_high': row['previous_high'],
                        'previous_low': row['previous_low'],
                        'created_at': row['created_at']
                    }
                return None
                
            except Exception as e:
                logger.error(f"Failed to load daily signal for {market} on {date}: {e}")
                return None
            finally:
                conn.close()
    
    def get_latest_signal(self, market: str) -> Optional[Dict]:
        """
        Get the most recent signal for a market
        
        Args:
            market: Market symbol
            
        Returns:
            Signal dictionary or None if not found
        """
        with self._lock:
            conn = self._get_connection()
            try:
                cursor = conn.execute("""
                    SELECT market, date, buy_price, sell_price, range_value, 
                           previous_high, previous_low, created_at
                    FROM daily_signals 
                    WHERE market = ? 
                    ORDER BY created_at DESC 
                    LIMIT 1
                """, (market,))
                
                row = cursor.fetchone()
                if row:
                    return {
                        'market': row['market'],
                        'date': row['date'],
                        'buy_price': row['buy_price'],
                        'sell_price': row['sell_price'],
                        'range_value': row['range_value'],
                        'previous_high': row['previous_high'],
                        'previous_low': row['previous_low'],
                        'created_at': row['created_at']
                    }
                return None
                
            except Exception as e:
                logger.error(f"Failed to get latest signal for {market}: {e}")
                return None
            finally:
                conn.close()