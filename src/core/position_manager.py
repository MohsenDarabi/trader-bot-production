"""
Position tracking and management for futures trading
"""
from datetime import datetime, timezone
from typing import Dict, List, Optional
from dataclasses import dataclass
from enum import Enum

from src.exchange.coinex_client import CoinExClient
from src.utils.logger import get_logger


logger = get_logger(__name__)


class PositionSide(Enum):
    """Position side enumeration"""
    LONG = "long"
    SHORT = "short"


@dataclass
class Position:
    """Position data structure"""
    position_id: Optional[int]  # CoinEx position ID
    market: str
    side: PositionSide
    size: float  # Total position size
    avg_entry_price: float  # Weighted average entry price
    total_cost: float  # Total capital used (including fees)
    unrealized_pnl: float  # Current unrealized P&L
    liquidation_price: float  # Liquidation price
    created_at: datetime
    updated_at: datetime
    
    def calculate_unrealized_pnl(self, current_price: float) -> float:
        """
        Calculate current unrealized P&L
        
        Args:
            current_price: Current market price
            
        Returns:
            Unrealized P&L in USDT
        """
        if self.size == 0:
            return 0.0
        
        # Calculate value at current price
        current_value = self.size * current_price
        
        # P&L is current value minus total cost
        pnl = current_value - self.total_cost
        
        # For short positions, P&L is inverted
        if self.side == PositionSide.SHORT:
            pnl = -pnl
        
        return pnl
    
    def calculate_pnl_percentage(self, current_price: float) -> float:
        """
        Calculate P&L as percentage of total cost
        
        Args:
            current_price: Current market price
            
        Returns:
            P&L percentage
        """
        if self.total_cost == 0:
            return 0.0
        
        pnl = self.calculate_unrealized_pnl(current_price)
        return (pnl / self.total_cost) * 100
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for storage"""
        return {
            'market': self.market,
            'side': self.side.value,
            'size': self.size,
            'avg_entry_price': self.avg_entry_price,
            'total_cost': self.total_cost,
            'unrealized_pnl': self.unrealized_pnl,
            'liquidation_price': self.liquidation_price,
            'created_at': self.created_at.isoformat(),
            'updated_at': self.updated_at.isoformat()
        }


class PositionManager:
    """Manages futures positions with accumulation support"""
    
    def __init__(self, client: CoinExClient):
        """
        Initialize position manager
        
        Args:
            client: CoinEx API client
        """
        self.client = client
        self.positions: Dict[str, Position] = {}
    
    def add_to_position(self, market: str, fill_price: float, 
                       fill_quantity: float, fill_fees: float) -> Position:
        """
        Add to existing position or create new one (accumulation)
        
        Args:
            market: Market symbol
            fill_price: Fill price of the order
            fill_quantity: Filled quantity
            fill_fees: Fees paid for this fill
            
        Returns:
            Updated position
        """
        now = datetime.now(timezone.utc)
        
        if market in self.positions:
            # Add to existing position
            position = self.positions[market]
            
            # Calculate new weighted average entry price
            old_total_value = position.size * position.avg_entry_price
            new_fill_value = fill_quantity * fill_price
            
            new_total_size = position.size + fill_quantity
            new_avg_price = (old_total_value + new_fill_value) / new_total_size
            new_total_cost = position.total_cost + (fill_quantity * fill_price) + fill_fees
            
            # Update position
            position.size = new_total_size
            position.avg_entry_price = new_avg_price
            position.total_cost = new_total_cost
            position.updated_at = now
            
            logger.info(f"Added to {market} position: +{fill_quantity} @ ${fill_price:.2f}, "
                       f"New size: {new_total_size}, Avg entry: ${new_avg_price:.2f}")
            
        else:
            # Create new position
            position = Position(
                market=market,
                side=PositionSide.LONG,  # Strategy only uses long positions
                size=fill_quantity,
                avg_entry_price=fill_price,
                total_cost=(fill_quantity * fill_price) + fill_fees,
                unrealized_pnl=0.0,
                liquidation_price=0.0,  # Will be updated from exchange
                created_at=now,
                updated_at=now
            )
            
            self.positions[market] = position
            
            logger.info(f"Created new {market} position: {fill_quantity} @ ${fill_price:.2f}")
        
        return position
    
    def reduce_position(self, market: str, fill_price: float, 
                       fill_quantity: float, fill_fees: float) -> Optional[Position]:
        """
        Reduce position size (sell order filled)
        
        Args:
            market: Market symbol
            fill_price: Fill price of the sell order
            fill_quantity: Filled quantity (amount sold)
            fill_fees: Fees paid for this fill
            
        Returns:
            Updated position or None if position closed
        """
        if market not in self.positions:
            logger.warning(f"No position found for {market} to reduce")
            return None
        
        position = self.positions[market]
        
        if fill_quantity > position.size:
            logger.error(f"Cannot reduce {market} position by {fill_quantity}, "
                        f"only {position.size} available")
            return position
        
        # Calculate profit/loss on this portion
        portion_cost = position.avg_entry_price * fill_quantity
        portion_revenue = fill_price * fill_quantity - fill_fees
        portion_pnl = portion_revenue - portion_cost
        
        # Update position
        position.size -= fill_quantity
        position.total_cost -= portion_cost  # Remove cost of sold portion
        position.updated_at = datetime.now(timezone.utc)
        
        logger.info(f"Reduced {market} position: -{fill_quantity} @ ${fill_price:.2f}, "
                   f"Portion P&L: ${portion_pnl:.2f}, Remaining size: {position.size}")
        
        # Remove position if fully closed
        if position.size <= 0.0001:  # Small threshold for rounding
            logger.info(f"Position {market} fully closed")
            del self.positions[market]
            return None
        
        return position
    
    def update_position_pnl(self, market: str, current_price: float) -> Optional[Position]:
        """
        Update position's unrealized P&L
        
        Args:
            market: Market symbol
            current_price: Current market price
            
        Returns:
            Updated position or None if not found
        """
        if market not in self.positions:
            return None
        
        position = self.positions[market]
        position.unrealized_pnl = position.calculate_unrealized_pnl(current_price)
        position.updated_at = datetime.now(timezone.utc)
        
        return position
    
    def get_position(self, market: str) -> Optional[Position]:
        """
        Get position for market
        
        Args:
            market: Market symbol
            
        Returns:
            Position or None if not found
        """
        return self.positions.get(market)
    
    def get_all_positions(self) -> List[Position]:
        """
        Get all active positions
        
        Returns:
            List of all positions
        """
        return list(self.positions.values())
    
    def has_position(self, market: str) -> bool:
        """
        Check if we have an active position in market
        
        Args:
            market: Market symbol
            
        Returns:
            True if position exists
        """
        return market in self.positions
    
    def sync_with_exchange(self, market: Optional[str] = None) -> int:
        """
        Synchronize positions with exchange data
        
        Args:
            market: Optional market filter
            
        Returns:
            Number of positions synced
        """
        try:
            # Get positions from exchange
            response = self.client.get_positions(market=market)
            # CoinEx returns dict with 'data' key containing positions list
            exchange_positions = response if isinstance(response, list) else response.get('data', [])
            
            synced_count = 0
            
            for pos_data in exchange_positions:
                market_name = pos_data['market']
                open_interest = float(pos_data.get('open_interest', 0))
                
                # Only sync positions with open interest
                if open_interest > 0:
                    avg_entry_price = float(pos_data.get('avg_entry_price', 0))
                    unrealized_pnl = float(pos_data.get('unrealized_pnl', 0))
                    liquidation_price = float(pos_data.get('liq_price', 0))
                    
                    # Calculate total cost (this is an approximation)
                    total_cost = open_interest * avg_entry_price
                    
                    if market_name in self.positions:
                        # Update existing position
                        position = self.positions[market_name]
                        position.position_id = pos_data.get('position_id')
                        position.size = open_interest
                        position.avg_entry_price = avg_entry_price
                        position.unrealized_pnl = unrealized_pnl
                        position.liquidation_price = liquidation_price
                        position.updated_at = datetime.now(timezone.utc)
                    else:
                        # Create new position from exchange data
                        position_id = pos_data.get('position_id')
                        position = Position(
                            position_id=position_id,
                            market=market_name,
                            side=PositionSide.LONG,
                            size=open_interest,
                            avg_entry_price=avg_entry_price,
                            total_cost=total_cost,
                            unrealized_pnl=unrealized_pnl,
                            liquidation_price=liquidation_price,
                            created_at=datetime.now(timezone.utc),
                            updated_at=datetime.now(timezone.utc)
                        )
                        self.positions[market_name] = position
                    
                    synced_count += 1
                    logger.debug(f"Synced {market_name} position: {open_interest} @ ${avg_entry_price:.2f}")
            
            # Remove positions that no longer exist on exchange
            markets_to_remove = []
            for market_name in self.positions:
                if market and market != market_name:
                    continue
                
                # Check if this market is in exchange data
                found = any(pos['market'] == market_name and 
                          float(pos.get('open_interest', 0)) > 0 
                          for pos in exchange_positions)
                
                if not found:
                    markets_to_remove.append(market_name)
            
            for market_name in markets_to_remove:
                logger.info(f"Removing closed position: {market_name}")
                del self.positions[market_name]
            
            logger.info(f"Synced {synced_count} positions with exchange")
            return synced_count
            
        except Exception as e:
            logger.error(f"Failed to sync positions with exchange: {e}")
            return 0
    
    def calculate_total_unrealized_pnl(self, market_prices: Dict[str, float]) -> float:
        """
        Calculate total unrealized P&L across all positions
        
        Args:
            market_prices: Dictionary of market -> current_price
            
        Returns:
            Total unrealized P&L in USDT
        """
        total_pnl = 0.0
        
        for market, position in self.positions.items():
            if market in market_prices:
                pnl = position.calculate_unrealized_pnl(market_prices[market])
                total_pnl += pnl
        
        return total_pnl
    
    def get_position_summary(self, market_prices: Dict[str, float]) -> Dict:
        """
        Get summary of all positions
        
        Args:
            market_prices: Dictionary of market -> current_price
            
        Returns:
            Summary dictionary with statistics
        """
        if not self.positions:
            return {
                'total_positions': 0,
                'total_cost': 0.0,
                'total_unrealized_pnl': 0.0,
                'total_pnl_percent': 0.0
            }
        
        total_cost = sum(pos.total_cost for pos in self.positions.values())
        total_pnl = self.calculate_total_unrealized_pnl(market_prices)
        total_pnl_percent = (total_pnl / total_cost * 100) if total_cost > 0 else 0.0
        
        return {
            'total_positions': len(self.positions),
            'total_cost': total_cost,
            'total_unrealized_pnl': total_pnl,
            'total_pnl_percent': total_pnl_percent,
            'positions': [pos.to_dict() for pos in self.positions.values()]
        }