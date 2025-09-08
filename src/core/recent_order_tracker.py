"""
Recent Order Tracker
Tracks recently placed orders to prevent duplicate buy orders and handle phantom order scenarios
"""
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Tuple, Optional
from src.utils.logger import get_logger

logger = get_logger(__name__)


class RecentOrderTracker:
    """Tracks recently placed orders to prevent duplicates and handle phantom orders"""
    
    def __init__(self, grace_period_seconds: int = 30):
        """
        Initialize the recent order tracker
        
        Args:
            grace_period_seconds: How long to protect against duplicate orders
        """
        self.grace_period = grace_period_seconds
        
        # Track recent order placements: {market: [(timestamp, client_id, amount)]}
        self._recent_orders: Dict[str, List[Tuple[datetime, str, float]]] = {}
        
        # Track phantom order candidates: {market: [(timestamp, client_id, amount)]}
        self._phantom_candidates: Dict[str, List[Tuple[datetime, str, float]]] = {}
        
        # Track position sizes at phantom detection time for verification
        self._position_snapshots: Dict[str, Tuple[datetime, float]] = {}
    
    def track_order_placed(self, market: str, client_id: str, amount: float):
        """
        Track a recently placed order
        
        Args:
            market: Market symbol
            client_id: Order client ID
            amount: Order amount
        """
        timestamp = datetime.now(timezone.utc)
        
        if market not in self._recent_orders:
            self._recent_orders[market] = []
        
        self._recent_orders[market].append((timestamp, client_id, amount))
        
        logger.info(f"🔗 Tracking recent order: {client_id} for {market} ({amount:.6f})")
        
        # Clean up old entries periodically
        self._cleanup_old_entries()
    
    def has_recent_order(self, market: str) -> Tuple[bool, Optional[str]]:
        """
        Check if a market has any recent orders within the grace period
        
        Args:
            market: Market symbol
            
        Returns:
            Tuple of (has_recent_order, most_recent_client_id)
        """
        if market not in self._recent_orders:
            return False, None
        
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(seconds=self.grace_period)
        
        recent_orders = [
            (ts, cid, amt) for ts, cid, amt in self._recent_orders[market]
            if ts > cutoff
        ]
        
        if recent_orders:
            # Return the most recent order
            most_recent = max(recent_orders, key=lambda x: x[0])
            return True, most_recent[1]
        
        return False, None
    
    def mark_as_phantom_candidate(self, market: str, client_id: str, amount: float):
        """
        Mark an order as a phantom candidate for later verification
        
        Args:
            market: Market symbol
            client_id: Order client ID
            amount: Order amount
        """
        timestamp = datetime.now(timezone.utc)
        
        if market not in self._phantom_candidates:
            self._phantom_candidates[market] = []
        
        self._phantom_candidates[market].append((timestamp, client_id, amount))
        
        logger.warning(f"👻 Marked phantom candidate: {client_id} for {market} ({amount:.6f})")
    
    def get_phantom_candidates(self, market: str) -> List[Tuple[datetime, str, float]]:
        """
        Get phantom candidates for a market that are still within verification window
        
        Args:
            market: Market symbol
            
        Returns:
            List of (timestamp, client_id, amount) tuples
        """
        if market not in self._phantom_candidates:
            return []
        
        now = datetime.now(timezone.utc)
        verification_window = 60  # 1 minute verification window
        cutoff = now - timedelta(seconds=verification_window)
        
        active_candidates = [
            (ts, cid, amt) for ts, cid, amt in self._phantom_candidates[market]
            if ts > cutoff
        ]
        
        return active_candidates
    
    def record_position_snapshot(self, market: str, position_size: float):
        """
        Record a position size snapshot for phantom verification
        
        Args:
            market: Market symbol
            position_size: Current position size
        """
        timestamp = datetime.now(timezone.utc)
        self._position_snapshots[market] = (timestamp, position_size)
    
    def get_recent_order_count(self, market: str) -> int:
        """
        Get count of recent orders for a market
        
        Args:
            market: Market symbol
            
        Returns:
            Number of recent orders within grace period
        """
        if market not in self._recent_orders:
            return 0
        
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(seconds=self.grace_period)
        
        recent_count = sum(
            1 for ts, _, _ in self._recent_orders[market]
            if ts > cutoff
        )
        
        return recent_count
    
    def _cleanup_old_entries(self):
        """Clean up old tracking entries to prevent memory growth"""
        now = datetime.now(timezone.utc)
        
        # Clean recent orders (keep 2x grace period)
        cleanup_cutoff = now - timedelta(seconds=self.grace_period * 2)
        for market in list(self._recent_orders.keys()):
            self._recent_orders[market] = [
                (ts, cid, amt) for ts, cid, amt in self._recent_orders[market]
                if ts > cleanup_cutoff
            ]
            
            # Remove empty entries
            if not self._recent_orders[market]:
                del self._recent_orders[market]
        
        # Clean phantom candidates (keep 2 minute window)
        phantom_cutoff = now - timedelta(seconds=120)
        for market in list(self._phantom_candidates.keys()):
            self._phantom_candidates[market] = [
                (ts, cid, amt) for ts, cid, amt in self._phantom_candidates[market]
                if ts > phantom_cutoff
            ]
            
            if not self._phantom_candidates[market]:
                del self._phantom_candidates[market]
        
        # Clean position snapshots (keep 5 minutes)
        snapshot_cutoff = now - timedelta(seconds=300)
        for market in list(self._position_snapshots.keys()):
            ts, _ = self._position_snapshots[market]
            if ts <= snapshot_cutoff:
                del self._position_snapshots[market]
    
    def get_status_summary(self) -> Dict[str, any]:
        """Get summary of tracker status for debugging"""
        return {
            'recent_orders_markets': list(self._recent_orders.keys()),
            'phantom_candidates_markets': list(self._phantom_candidates.keys()),
            'position_snapshots_markets': list(self._position_snapshots.keys()),
            'grace_period_seconds': self.grace_period
        }