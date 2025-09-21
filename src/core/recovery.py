"""
Startup recovery system to safely resume trading after crashes or restarts
"""
from datetime import datetime, timezone
from typing import List, Tuple
from dataclasses import dataclass

from src.exchange.coinex_client import CoinExClient
from src.exchange.order_manager import OrderManager, Order, OrderStatus, OrderSide
from src.core.position_manager import PositionManager, Position, PositionSide
from src.data.database import DatabaseManager
from src.data.market_data import MarketDataManager
from src.core.strategy import DailyRangeStrategy
from src.utils.logger import get_logger


logger = get_logger(__name__)


@dataclass
class RecoveryReport:
    """Recovery process report"""
    positions_loaded: int
    positions_synced: int
    orders_loaded: int
    orders_synced: int
    signals_loaded: int
    inconsistencies_found: List[str]
    recovery_successful: bool
    
    def __str__(self) -> str:
        status = "SUCCESS" if self.recovery_successful else "FAILED"
        report = f"Recovery {status}:\n"
        report += f"  Positions: {self.positions_loaded} loaded, {self.positions_synced} synced\n"
        report += f"  Orders: {self.orders_loaded} loaded, {self.orders_synced} synced\n"
        report += f"  Signals: {self.signals_loaded} loaded\n"
        
        if self.inconsistencies_found:
            report += f"  Inconsistencies: {len(self.inconsistencies_found)}\n"
            for issue in self.inconsistencies_found:
                report += f"    - {issue}\n"
        
        return report


class StartupRecovery:
    """Handles bot startup recovery and state synchronization"""
    
    def __init__(self, client: CoinExClient, database: DatabaseManager):
        """
        Initialize recovery system
        
        Args:
            client: CoinEx API client
            database: Database manager
        """
        self.client = client
        self.database = database
        self.market_data = MarketDataManager(client)
        self.inconsistencies = []
    
    def perform_full_recovery(self, order_manager: OrderManager,
                            position_manager: PositionManager,
                            strategy: DailyRangeStrategy) -> RecoveryReport:
        """
        Perform complete startup recovery
        
        Args:
            order_manager: Order manager instance
            position_manager: Position manager instance
            strategy: Trading strategy instance
            
        Returns:
            Recovery report with results
        """
        logger.info("Starting bot recovery process...")
        self.inconsistencies = []
        
        try:
            # Step 1: Recover positions
            positions_loaded, positions_synced = self._recover_positions(position_manager)
            
            # Step 2: Recover orders
            orders_loaded, orders_synced = self._recover_orders(order_manager)
            
            # Step 3: Recover signals
            signals_loaded = self._recover_signals(strategy)
            
            # Step 4: Validate consistency
            self._validate_consistency(order_manager, position_manager)
            
            # Step 5: Clean up stale data
            self._cleanup_stale_data(order_manager, position_manager)
            
            recovery_successful = len(self.inconsistencies) == 0
            
            report = RecoveryReport(
                positions_loaded=positions_loaded,
                positions_synced=positions_synced,
                orders_loaded=orders_loaded,
                orders_synced=orders_synced,
                signals_loaded=signals_loaded,
                inconsistencies_found=self.inconsistencies.copy(),
                recovery_successful=recovery_successful
            )
            
            logger.info(f"Recovery completed: {report}")
            return report
            
        except Exception as e:
            logger.error(f"Recovery failed: {e}")
            return RecoveryReport(
                positions_loaded=0, positions_synced=0,
                orders_loaded=0, orders_synced=0,
                signals_loaded=0,
                inconsistencies_found=[f"Recovery error: {e}"],
                recovery_successful=False
            )
    
    def _recover_positions(self, position_manager: PositionManager) -> Tuple[int, int]:
        """
        Recover positions from database and sync with exchange
        
        Args:
            position_manager: Position manager instance
            
        Returns:
            Tuple of (loaded_count, synced_count)
        """
        logger.info("Recovering positions...")
        
        # Load positions from database
        stored_positions = self.database.load_positions()
        loaded_count = 0
        
        for pos_data in stored_positions:
            try:
                # Convert database data back to Position object
                position = Position(
                    market=pos_data['market'],
                    side=PositionSide(pos_data['side']),
                    size=pos_data['size'],
                    avg_entry_price=pos_data['avg_entry_price'],
                    total_cost=pos_data['total_cost'],
                    unrealized_pnl=pos_data['unrealized_pnl'],
                    liquidation_price=pos_data['liquidation_price'],
                    created_at=datetime.fromisoformat(pos_data['created_at']),
                    updated_at=datetime.fromisoformat(pos_data['updated_at'])
                )
                
                position_manager.positions[pos_data['market']] = position
                loaded_count += 1
                
                logger.debug(f"Loaded position: {pos_data['market']} "
                           f"{pos_data['size']} @ ${pos_data['avg_entry_price']:.2f}")
                
            except Exception as e:
                logger.error(f"Failed to load position {pos_data['market']}: {e}")
                self.inconsistencies.append(f"Failed to load position {pos_data['market']}")
        
        # Sync with exchange to get current state
        synced_count = position_manager.sync_with_exchange()
        
        # Update database with current exchange data
        for position in position_manager.get_all_positions():
            self.database.save_position(position.to_dict())
        
        logger.info(f"Positions recovered: {loaded_count} loaded, {synced_count} synced")
        return loaded_count, synced_count
    
    def _recover_orders(self, order_manager: OrderManager) -> Tuple[int, int]:
        """
        Recover orders from database and sync with exchange
        
        Args:
            order_manager: Order manager instance
            
        Returns:
            Tuple of (loaded_count, synced_count)
        """
        logger.info("Recovering orders...")
        
        # Load active orders from database
        stored_orders = self.database.load_active_orders()
        loaded_count = 0
        
        for order_data in stored_orders:
            try:
                # Convert database data back to Order object
                order = Order(
                    client_id=order_data['client_id'],
                    market=order_data['market'],
                    side=OrderSide(order_data['side']),
                    order_type=order_data['order_type'],
                    amount=order_data['amount'],
                    price=order_data['price'],
                    status=OrderStatus(order_data['status']),
                    exchange_order_id=order_data['exchange_order_id'],
                    filled_amount=order_data['filled_amount'],
                    created_at=datetime.fromisoformat(order_data['created_at']),
                    updated_at=datetime.fromisoformat(order_data['updated_at'])
                )
                
                order_manager.active_orders[order_data['client_id']] = order
                order_manager.used_client_ids.add(order_data['client_id'])
                loaded_count += 1
                
                logger.debug(f"Loaded order: {order_data['client_id']} "
                           f"{order_data['side']} {order_data['amount']} @ ${order_data['price']}")
                
            except Exception as e:
                logger.error(f"Failed to load order {order_data['client_id']}: {e}")
                self.inconsistencies.append(f"Failed to load order {order_data['client_id']}")
        
        # Load existing orders from exchange to prevent duplicates
        synced_count = order_manager.load_existing_orders()
        
        # Update all order statuses
        for client_id in list(order_manager.active_orders.keys()):
            order_manager.update_order_status(client_id)
        
        # Save updated order states to database
        for order in order_manager.active_orders.values():
            self.database.save_order(order.to_dict())
        
        logger.info(f"Orders recovered: {loaded_count} loaded, {synced_count} synced from exchange")
        return loaded_count, synced_count
    
    def _recover_signals(self, strategy: DailyRangeStrategy) -> int:
        """
        Recover trading signals from database
        
        Args:
            strategy: Trading strategy instance
            
        Returns:
            Number of signals loaded
        """
        logger.info("Recovering trading signals...")
        
        signals_loaded = 0
        logger.info(f"Signals recovery: {signals_loaded} loaded (will generate fresh signals)")
        return signals_loaded
    
    def _validate_consistency(self, order_manager: OrderManager,
                            position_manager: PositionManager):
        """
        Validate consistency between positions, orders, and exchange state
        
        Args:
            order_manager: Order manager instance
            position_manager: Position manager instance
        """
        logger.info("Validating state consistency...")
        
        # Check for orders without corresponding exchange orders
        for client_id, order in order_manager.active_orders.items():
            if order.exchange_order_id is None and order.status == OrderStatus.PENDING:
                self.inconsistencies.append(
                    f"Order {client_id} has no exchange_order_id but is marked as pending"
                )
        
        # Check for positions that might need price updates
        for market, position in position_manager.positions.items():
            try:
                current_price = self.market_data.get_current_price(market)
                position_manager.update_position_pnl(market, current_price)
            except Exception as e:
                logger.warning(f"Could not update P&L for {market}: {e}")
        
        # Validate that pending sell orders have corresponding positions
        for order in order_manager.active_orders.values():
            if order.side == OrderSide.SELL:
                if not position_manager.has_position(order.market):
                    self.inconsistencies.append(
                        f"Sell order {order.client_id} exists but no position found for {order.market}"
                    )
    
    def _cleanup_stale_data(self, order_manager: OrderManager,
                          position_manager: PositionManager):
        """
        Clean up stale or invalid data
        
        Args:
            order_manager: Order manager instance
            position_manager: Position manager instance
        """
        logger.info("Cleaning up stale data...")
        
        # Remove orders that are filled/cancelled but still in active_orders
        orders_to_remove = []
        for client_id, order in order_manager.active_orders.items():
            if order.status in [OrderStatus.FILLED, OrderStatus.CANCELLED]:
                orders_to_remove.append(client_id)
        
        for client_id in orders_to_remove:
            del order_manager.active_orders[client_id]
            logger.debug(f"Removed completed order from active tracking: {client_id}")
        
        # Remove positions with zero size
        markets_to_remove = []
        for market, position in position_manager.positions.items():
            if position.size <= 0.0001:  # Small threshold for rounding
                markets_to_remove.append(market)
        
        for market in markets_to_remove:
            del position_manager.positions[market]
            self.database.delete_position(market)
            logger.debug(f"Removed zero-size position: {market}")
        
        # Clean up old database records
        self.database.cleanup_old_data(days=30)
    
    def save_current_state(self, order_manager: OrderManager,
                          position_manager: PositionManager,
                          strategy: DailyRangeStrategy):
        """
        Save current bot state to database
        
        Args:
            order_manager: Order manager instance
            position_manager: Position manager instance
            strategy: Trading strategy instance
        """
        logger.info("Saving current bot state...")
        
        try:
            # Save all positions
            for position in position_manager.get_all_positions():
                self.database.save_position(position.to_dict())
            
            # Save all active orders
            for order in order_manager.active_orders.values():
                self.database.save_order(order.to_dict())
            
            # Save current signals
            for market, signal in strategy._current_signals.items():
                self.database.save_daily_signal(signal.to_dict())
            
            # Save recovery timestamp
            self.database.save_bot_state('last_save_time', datetime.now(timezone.utc).isoformat())
            
            logger.info("Bot state saved successfully")
            
        except Exception as e:
            logger.error(f"Failed to save bot state: {e}")
    
    def is_safe_to_trade(self, order_manager: OrderManager,
                        position_manager: PositionManager) -> Tuple[bool, List[str]]:
        """
        Check if it's safe to start trading
        
        Args:
            order_manager: Order manager instance
            position_manager: Position manager instance
            
        Returns:
            Tuple of (is_safe, list_of_issues)
        """
        issues = []
        
        # Check for too many inconsistencies
        if len(self.inconsistencies) > 5:
            issues.append(f"Too many inconsistencies found: {len(self.inconsistencies)}")
        
        # Check for API connectivity
        try:
            self.client.get_futures_markets()
        except Exception as e:
            issues.append(f"API connectivity issue: {e}")
        
        # Check for position liquidation risk
        for position in position_manager.get_all_positions():
            if position.liquidation_price > 0:
                try:
                    current_price = self.market_data.get_current_price(position.market)
                    # Check if we're within 20% of liquidation price
                    if position.side == PositionSide.LONG:
                        if current_price <= position.liquidation_price * 1.2:
                            issues.append(f"{position.market} position near liquidation")
                except Exception:
                    pass  # Skip if can't get current price
        
        is_safe = len(issues) == 0
        return is_safe, issues
