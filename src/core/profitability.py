"""
Three-Layer Profitability Validation System
Ensures all trades meet minimum profit requirements
"""
from typing import Tuple, Optional, List
from dataclasses import dataclass

from config.settings import (
    MAKER_FEE, TAKER_FEE, MIN_PROFIT_PERCENT, LEVERAGE
)
from src.utils.safe_conversions import safe_float
from src.utils.logger import get_logger


logger = get_logger(__name__)


@dataclass
class ProfitabilityResult:
    """Result of profitability check"""
    is_profitable: bool
    net_profit: float
    profit_percent: float
    total_fees: float
    reason: Optional[str] = None
    
    def __str__(self) -> str:
        status = "PROFITABLE" if self.is_profitable else "NOT PROFITABLE"
        return (f"{status}: {self.profit_percent:.2f}% "
                f"(${self.net_profit:.2f} after ${self.total_fees:.2f} fees)")


class ProfitabilityValidator:
    """Validates profitability at three layers"""
    
    def __init__(self):
        self.min_profit_percent = safe_float(MIN_PROFIT_PERCENT)
        self.maker_fee = safe_float(MAKER_FEE)
        self.taker_fee = safe_float(TAKER_FEE)
        self.leverage = safe_float(LEVERAGE)
        self._profit_epsilon = 1e-6  # tolerance for floating point comparisons
    
    # Layer 1: Signal Profitability
    def is_signal_profitable(self, buy_price: float, sell_price: float,
                           margin: float) -> ProfitabilityResult:
        """
        Layer 1: Check if a trading signal can be profitable
        
        Args:
            buy_price: Planned entry price
            sell_price: Planned exit price
            margin: User's margin/collateral in USDT
            
        Returns:
            ProfitabilityResult with validation details
        """
        # We evaluate profitability per-contract, independent of the account margin.
        # Assume the entry executes as a taker order and the exit as a maker order.
        if buy_price <= 0:
            raise ValueError("buy_price must be positive")

        # Derive quantity from margin for reporting purposes, but evaluate ratios directly.
        notional_value = margin * self.leverage if margin > 0 else buy_price
        quantity = notional_value / buy_price

        # Net proceeds after closing the position
        net_proceeds = sell_price * quantity * (1 - self.maker_fee)

        # Total cost of opening the position (price + taker fee)
        total_cost = buy_price * quantity * (1 + self.taker_fee)

        net_profit = net_proceeds - total_cost
        profit_percent = (net_profit / (buy_price * quantity)) * 100  # Relative to entry notional
        total_fees = (buy_price * quantity * self.taker_fee) + (sell_price * quantity * self.maker_fee)

        # Check profitability
        is_profitable = (profit_percent + self._profit_epsilon) >= self.min_profit_percent

        reason = None
        if not is_profitable:
            reason = (f"Signal profit {profit_percent:.2f}% is below "
                     f"minimum {self.min_profit_percent}%")
        
        logger.debug(f"Signal validation: Buy=${buy_price:.2f}, Sell=${sell_price:.2f}, "
                    f"Profit={profit_percent:.2f}%")
        
        return ProfitabilityResult(
            is_profitable=is_profitable,
            net_profit=net_profit,
            profit_percent=profit_percent,
            total_fees=total_fees,
            reason=reason
        )
    
    # Layer 2: Price Optimization
    def optimize_prices_for_profit(self, buy_price: float, sell_price: float,
                                 range_value: float, margin: float,
                                 high: Optional[float], low: Optional[float]) -> Tuple[float, float, bool]:
        """
        Layer 2: Optimize prices to meet profit requirements
        
        Args:
            buy_price: Initial buy price
            sell_price: Initial sell price
            range_value: Calculated range value
            margin: User's margin/collateral in USDT
            high: Optional upper boundary to keep sell price within
            low: Optional lower boundary to keep buy price within
            
        Returns:
            Tuple of (optimized_buy_price, optimized_sell_price, was_optimized)
        """
        # First check if current prices are profitable
        result = self.is_signal_profitable(buy_price, sell_price, margin)
        
        if result.is_profitable:
            logger.debug("Prices already profitable, no optimization needed")
            return buy_price, sell_price, False
        
        min_profit_decimal = self.min_profit_percent / 100
        required_multiplier = (1 + self.taker_fee + min_profit_decimal) / (1 - self.maker_fee)

        current_ratio = sell_price / buy_price if buy_price > 0 else 0.0
        target_ratio = max(required_multiplier, current_ratio + 1e-6)
        midpoint = (buy_price + sell_price) / 2

        # Collect candidate adjustments in priority order so the first profitable
        # option we find becomes the selected spread update.
        candidates: List[Tuple[str, float, float]] = []

        # 1) Symmetric expansion around the midpoint (preferred when feasible).
        if midpoint > 0 and target_ratio > current_ratio:
            delta = midpoint * (target_ratio - 1) / (target_ratio + 1)
            candidates.append(("symmetric", midpoint - delta, midpoint + delta))

        # 2) Raise only the sell side enough to clear fees + profit target.
        min_sell_required = max(sell_price, self.calculate_min_profitable_price(buy_price))
        if high is not None:
            if min_sell_required <= high:
                candidates.append(("raise-sell", buy_price, min_sell_required))
        else:
            candidates.append(("raise-sell", buy_price, min_sell_required))

        # 3) Lower only the buy side if there is room above the low bound.
        if required_multiplier > 0:
            max_buy_allowed = sell_price / required_multiplier
            lowered_buy = min(buy_price, max_buy_allowed)
            if low is not None:
                if lowered_buy >= low:
                    candidates.append(("lower-buy", lowered_buy, sell_price))
            else:
                candidates.append(("lower-buy", lowered_buy, sell_price))

        # 4) Anchor to the low bound if available, expanding the sell side enough
        # to satisfy the minimum profit. This keeps spreads within the prior range.
        if low is not None:
            low_anchor_sell = max(sell_price, low * required_multiplier)
            if high is None or low_anchor_sell <= high:
                candidates.append(("anchor-low", low, low_anchor_sell))

        # 5) Anchor to the high bound where possible.
        if high is not None and required_multiplier > 0:
            high_anchor_buy = min(buy_price, high / required_multiplier)
            if high_anchor_buy > 0 and (low is None or high_anchor_buy >= low):
                candidates.append(("anchor-high", high_anchor_buy, high))

        tested = set()
        for label, candidate_buy, candidate_sell in candidates:
            if candidate_buy <= 0 or candidate_sell <= candidate_buy:
                continue
            if low is not None and candidate_buy < low:
                continue
            if high is not None and candidate_sell > high:
                continue

            key = (round(candidate_buy, 10), round(candidate_sell, 10))
            if key in tested:
                continue
            tested.add(key)

            candidate_result = self.is_signal_profitable(candidate_buy, candidate_sell, margin)
            if candidate_result.is_profitable:
                logger.info(f"✅ Optimized prices for profitability ({label}):")
                logger.info(f"   Original: Buy=${buy_price:.2f}, Sell=${sell_price:.2f}")
                logger.info(f"   Optimized: Buy=${candidate_buy:.2f}, Sell=${candidate_sell:.2f}")
                logger.info(f"   Expected profit: {candidate_result.profit_percent:.2f}%")
                return candidate_buy, candidate_sell, True

        logger.debug("No profitable price adjustment found within allowable bounds.")
        return buy_price, sell_price, False
    
    # Layer 3: Position Exit Validation
    def is_position_profitable(self, entry_price: float, quantity: float,
                             sell_price: float, entry_cost: float) -> ProfitabilityResult:
        """
        Layer 3: Validate profitability before closing a position
        
        Args:
            entry_price: Average entry price of position
            quantity: Position quantity
            sell_price: Current market price or limit sell price
            entry_cost: Total cost of entry (including fees)
            
        Returns:
            ProfitabilityResult with validation details
        """
        if entry_price <= 0 or quantity <= 0:
            raise ValueError("entry_price and quantity must be positive for profitability checks")

        # Entry cost should represent the notional exposure; however, on leveraged products the
        # supplied `entry_cost` is often just the margin. Recompute using price & quantity to ensure
        # we compare apples-to-apples.
        entry_notional = entry_price * quantity

        buy_fees = entry_notional * self.taker_fee
        sell_revenue = sell_price * quantity
        sell_fees = sell_revenue * self.maker_fee

        net_profit = (sell_revenue - sell_fees) - (entry_notional + buy_fees)
        profit_percent = (net_profit / entry_notional) * 100
        total_fees = buy_fees + sell_fees

        # Check profitability
        is_profitable = (profit_percent + self._profit_epsilon) >= self.min_profit_percent

        reason = None
        if not is_profitable:
            reason = (f"Position profit {profit_percent:.2f}% is below "
                     f"minimum {self.min_profit_percent}%")
            min_sell_price = self.calculate_min_profitable_price(entry_price)
            reason += f" (need price >= ${min_sell_price:.4f})"

        logger.debug(f"Position validation: Entry=${entry_price:.2f}, "
                    f"Sell=${sell_price:.2f}, Profit={profit_percent:.2f}%")

        return ProfitabilityResult(
            is_profitable=is_profitable,
            net_profit=net_profit,
            profit_percent=profit_percent,
            total_fees=total_fees,
            reason=reason
        )

    def calculate_min_profitable_price(self, buy_price: float) -> float:
        """Return the minimum sell price that satisfies fees + min profit for a long entry."""
        min_profit_decimal = self.min_profit_percent / 100
        return buy_price * (1 + self.taker_fee + min_profit_decimal) / (1 - self.maker_fee)

    
    def validate_range_prices(self, high: float, low: float, 
                            buy_price: float, sell_price: float) -> bool:
        """
        Validate that calculated prices are within reasonable bounds
        
        Args:
            high: Previous day high
            low: Previous day low
            buy_price: Calculated buy price
            sell_price: Calculated sell price
            
        Returns:
            True if prices are valid
        """
        # Buy price should be above low but below midpoint
        midpoint = (high + low) / 2
        
        if buy_price <= low:
            logger.warning(f"Buy price ${buy_price} is below low ${low}")
            return False
        
        if buy_price >= midpoint:
            logger.warning(f"Buy price ${buy_price} is above midpoint ${midpoint}")
            return False
        
        # Sell price should be below high but above midpoint
        if sell_price >= high:
            logger.warning(f"Sell price ${sell_price} is above high ${high}")
            return False
        
        if sell_price <= midpoint:
            logger.warning(f"Sell price ${sell_price} is below midpoint ${midpoint}")
            return False
        
        # Buy should be lower than sell
        if buy_price >= sell_price:
            logger.warning(f"Buy price ${buy_price} >= sell price ${sell_price}")
            return False
        
        return True
