"""
Three-Layer Profitability Validation System
Ensures all trades meet minimum profit requirements
"""
from typing import Dict, Tuple, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from src.data.market_data import MarketDataManager
from dataclasses import dataclass

from config.settings import (
    MAKER_FEE, TAKER_FEE, MIN_PROFIT_PERCENT, LEVERAGE
)
from src.utils.logger import get_logger
from src.utils.precision_helper import precision_helper


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
        self.min_profit_percent = MIN_PROFIT_PERCENT
        self.maker_fee = MAKER_FEE
        self.taker_fee = TAKER_FEE
        self.leverage = LEVERAGE
    
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
        # Calculate with leverage
        notional_value = margin * self.leverage  # Total position value with leverage
        quantity = notional_value / buy_price
        
        # Calculate fees
        buy_fees = margin * self.taker_fee  # Pay fee on margin
        sell_revenue = quantity * sell_price
        sell_fees = sell_revenue * self.maker_fee
        total_fees = buy_fees + sell_fees
        
        # Calculate profit
        gross_profit = sell_revenue - notional_value
        net_profit = gross_profit - total_fees
        profit_percent = (net_profit / margin) * 100
        
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
                                 high: float, low: float,
                                 market: Optional[str] = None, 
                                 market_data: Optional['MarketDataManager'] = None) -> Tuple[float, float, bool]:
        """
        Simple price optimization for profitability
        If not profitable, symmetrically expand the spread
        
        Args:
            buy_price: Initial buy price
            sell_price: Initial sell price
            range_value: Calculated range value (not used as limit)
            margin: User's margin/collateral in USDT
            high: Previous day high (not used as limit)
            low: Previous day low (not used as limit)
            market: Market symbol for precision formatting (optional)
            market_data: Market data manager for getting market info (optional)
            
        Returns:
            Tuple of (optimized_buy_price, optimized_sell_price, was_optimized)
        """
        # First check if current prices are profitable
        result = self.is_signal_profitable(buy_price, sell_price, margin)
        
        if result.is_profitable:
            logger.debug("Prices already profitable, no optimization needed")
            return buy_price, sell_price, False
        
        # Calculate minimum required spread for profitability
        # With leverage, fees, and minimum profit requirement
        total_fee_percent = (self.taker_fee + self.maker_fee) * 100
        required_price_increase = (self.min_profit_percent + total_fee_percent) / self.leverage
        min_spread_factor = 1 + (required_price_increase / 100)
        
        # Calculate required spread in absolute terms
        required_spread = buy_price * (min_spread_factor - 1)
        current_spread = sell_price - buy_price
        
        # How much more spread do we need?
        spread_deficit = required_spread - current_spread
        
        if spread_deficit <= 0:
            # Should already be profitable (sanity check)
            return buy_price, sell_price, False
        
        # Split the deficit equally: buy lower, sell higher
        adjustment = spread_deficit / 2
        
        # Apply symmetric adjustment (NO rounding here - exchange handles it)
        new_buy = buy_price - adjustment
        new_sell = sell_price + adjustment
        
        # Dynamic precision logging
        if market and market_data:
            try:
                market_info = market_data.get_market_info(market)
                # Format prices with market-specific precision
                current_spread_str = precision_helper.format_price(current_spread, market_info, market)
                required_spread_str = precision_helper.format_price(required_spread, market_info, market)
                adjustment_str = precision_helper.format_price(adjustment, market_info, market)
                new_buy_str = precision_helper.format_price(new_buy, market_info, market)
                new_sell_str = precision_helper.format_price(new_sell, market_info, market)
                
                logger.info(f"📊 Price adjustment for profitability:")
                logger.info(f"   Current spread: ${current_spread_str}")
                logger.info(f"   Required spread: ${required_spread_str}")
                logger.info(f"   Adjustment: ±${adjustment_str}")
                logger.info(f"   New prices: Buy=${new_buy_str}, Sell=${new_sell_str}")
            except Exception:
                # Fallback to default formatting if market info not available
                logger.info(f"📊 Price adjustment for profitability:")
                logger.info(f"   Current spread: ${current_spread:.4f}")
                logger.info(f"   Required spread: ${required_spread:.4f}")
                logger.info(f"   Adjustment: ±${adjustment:.4f}")
                logger.info(f"   New prices: Buy=${new_buy:.4f}, Sell=${new_sell:.4f}")
        else:
            # No market info - use default formatting
            logger.info(f"📊 Price adjustment for profitability:")
            logger.info(f"   Current spread: ${current_spread:.4f}")
            logger.info(f"   Required spread: ${required_spread:.4f}")
            logger.info(f"   Adjustment: ±${adjustment:.4f}")
            logger.info(f"   New prices: Buy=${new_buy:.4f}, Sell=${new_sell:.4f}")
        
        return new_buy, new_sell, True
    
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
        # Calculate exit values
        sell_revenue = quantity * sell_price
        sell_fees = sell_revenue * self.maker_fee
        
        # Net proceeds after selling
        net_proceeds = sell_revenue - sell_fees
        
        # Calculate profit
        net_profit = net_proceeds - entry_cost
        profit_percent = (net_profit / entry_cost) * 100
        
        # Check profitability
        is_profitable = profit_percent >= self.min_profit_percent
        
        reason = None
        if not is_profitable:
            reason = (f"Position profit {profit_percent:.2f}% is below "
                     f"minimum {self.min_profit_percent}%")
            min_sell_price = self.calculate_min_profitable_price(
                quantity, entry_cost
            )
            reason += f" (need price >= ${min_sell_price:.2f})"
        
        logger.debug(f"Position validation: Entry=${entry_price:.2f}, "
                    f"Sell=${sell_price:.2f}, Profit={profit_percent:.2f}%")
        
        return ProfitabilityResult(
            is_profitable=is_profitable,
            net_profit=net_profit,
            profit_percent=profit_percent,
            total_fees=sell_fees,
            reason=reason
        )
    
    def calculate_min_profitable_price(self, quantity: float, 
                                     entry_cost: float) -> float:
        """
        Calculate minimum sell price needed for profitability
        
        Args:
            quantity: Position quantity
            entry_cost: Total entry cost including fees
            
        Returns:
            Minimum profitable sell price
        """
        # Required net proceeds
        required_proceeds = entry_cost * (1 + self.min_profit_percent / 100)
        
        # Account for sell fees
        # net_proceeds = sell_revenue - (sell_revenue * maker_fee)
        # net_proceeds = sell_revenue * (1 - maker_fee)
        # sell_revenue = net_proceeds / (1 - maker_fee)
        required_revenue = required_proceeds / (1 - self.maker_fee)
        
        # Calculate price
        min_price = required_revenue / quantity
        
        return min_price
    
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