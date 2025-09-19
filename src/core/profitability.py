"""
Three-Layer Profitability Validation System
Ensures all trades meet minimum profit requirements
"""
from typing import Dict, Tuple, Optional
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
        is_profitable = profit_percent >= self.min_profit_percent

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
                                 high: float, low: float) -> Tuple[float, float, bool]:
        """
        Layer 2: Optimize prices to meet profit requirements
        
        Args:
            buy_price: Initial buy price
            sell_price: Initial sell price
            range_value: Calculated range value
            margin: User's margin/collateral in USDT
            high: Previous day high
            low: Previous day low
            
        Returns:
            Tuple of (optimized_buy_price, optimized_sell_price, was_optimized)
        """
        # First check if current prices are profitable
        result = self.is_signal_profitable(buy_price, sell_price, margin)
        
        if result.is_profitable:
            logger.debug("Prices already profitable, no optimization needed")
            return buy_price, sell_price, False
        
        # Calculate minimum required spread for profitability
        # NO LIMITS - optimize prices with unlimited range
        
        # Calculate the required sell/buy price ratio (multiplier) for profitability.
        # The formula accounts for leverage and separate buy/sell fees.
        # Formula: sell_price = buy_price * (1 + buy_fee + (min_profit / leverage)) / (1 - sell_fee)
        min_profit_decimal = self.min_profit_percent / 100  # Convert from percent to decimal

        # Minimum multiplier needed so that sell price covers fees + desired profit
        required_multiplier = (1 + self.taker_fee + min_profit_decimal) / (1 - self.maker_fee)
        min_spread_factor = required_multiplier  # Use a consistent name with the old code

        # Calculate minimum sell price needed for profitability
        min_sell_price = buy_price * min_spread_factor
        
        if sell_price >= min_sell_price:
            # Already profitable - no optimization needed
            return buy_price, sell_price, False
        
        # Need to optimize - calculate options with NO LIMITS
        # Option 1: Keep buy price, increase sell price
        option1_buy = buy_price
        option1_sell = min_sell_price
        
        # Option 2: Keep sell price, decrease buy price
        option2_sell = sell_price
        option2_buy = sell_price / min_spread_factor
        
        # Option 3: Symmetric adjustment - expand both directions
        current_spread = sell_price - buy_price
        required_spread = buy_price * (min_spread_factor - 1)
        expansion_needed = (required_spread - current_spread) / 2
        
        option3_buy = buy_price - expansion_needed
        option3_sell = sell_price + expansion_needed
        
        # Choose the option that minimizes deviation from original prices
        # Calculate total deviation for each option
        dev1 = abs(option1_sell - sell_price)
        dev2 = abs(option2_buy - buy_price) 
        dev3 = abs(option3_buy - buy_price) + abs(option3_sell - sell_price)
        
        if dev1 <= dev2 and dev1 <= dev3:
            # Option 1: Keep buy, adjust sell
            best_buy, best_sell = option1_buy, option1_sell
            adjustment_type = "increased sell price"
        elif dev2 <= dev3:
            # Option 2: Adjust buy, keep sell
            best_buy, best_sell = option2_buy, option2_sell
            adjustment_type = "decreased buy price"
        else:
            # Option 3: Symmetric adjustment
            best_buy, best_sell = option3_buy, option3_sell
            adjustment_type = "symmetric adjustment"
        
        # Final validation
        final_result = self.is_signal_profitable(best_buy, best_sell, margin)
        if final_result.is_profitable:
            logger.info(f"✅ Optimized prices for profitability ({adjustment_type}):")
            logger.info(f"   Original: Buy=${buy_price:.2f}, Sell=${sell_price:.2f}")
            logger.info(f"   Optimized: Buy=${best_buy:.2f}, Sell=${best_sell:.2f}")
            logger.info(f"   Expected profit: {final_result.profit_percent:.2f}%")
            return best_buy, best_sell, True
        else:
            logger.error(f"❌ Optimization failed - calculation error")
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
        is_profitable = profit_percent >= self.min_profit_percent

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
