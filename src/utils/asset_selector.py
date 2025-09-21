"""
Asset selection menu for choosing trading pairs at startup
"""
from typing import List, Optional, Dict
from rich.console import Console
from rich.table import Table
from rich.prompt import Prompt, Confirm
from rich.panel import Panel
from rich.text import Text

from src.data.market_data import MarketDataManager
from src.core.position_sizing import PositionSizer
from src.utils.logger import get_logger
from src.utils.safe_conversions import safe_float


logger = get_logger(__name__)


class AssetSelector:
    """Interactive asset selection system"""
    
    def __init__(self, market_data: MarketDataManager, position_sizer: PositionSizer):
        """
        Initialize asset selector
        
        Args:
            market_data: Market data manager
            position_sizer: Position sizing calculator
        """
        self.market_data = market_data
        self.position_sizer = position_sizer
        self.console = Console()
    
    def select_trading_asset(self, available_capital: float) -> Optional[str]:
        """
        Interactive asset selection with market analysis
        
        Args:
            available_capital: Available trading capital
            
        Returns:
            Selected market symbol or None if cancelled
        """
        self.console.clear()
        
        # Show welcome banner
        self._show_welcome_banner(available_capital)
        
        try:
            # Get available markets
            self.console.print("🔍 Fetching available markets...", style="yellow")
            markets = self._get_filtered_markets()
            
            if not markets:
                self.console.print("❌ No suitable markets found!", style="red")
                return None
            
            # Display markets with analysis
            self._display_market_table(markets, available_capital)
            
            # Let user select
            selected_market = self._prompt_market_selection(markets)
            
            if selected_market:
                # Show final confirmation with details
                if self._confirm_selection(selected_market, available_capital):
                    return selected_market
            
            return None
            
        except KeyboardInterrupt:
            self.console.print("\n❌ Selection cancelled by user", style="red")
            return None
        except Exception as e:
            self.console.print(f"❌ Error during selection: {e}", style="red")
            logger.error(f"Asset selection error: {e}")
            return None
    
    def _show_welcome_banner(self, available_capital: float):
        """Show welcome banner with bot info"""
        title = Text("CoinEx Daily Range Accumulation Bot", style="bold blue")
        subtitle = Text("Asset Selection", style="italic")
        
        info_text = f"""
💰 Available Capital: ${available_capital:,.2f}
📊 Strategy: Daily Range Accumulation (No Stop Loss)
⚡ Mode: {'TEST (5 days minimum orders)' if self.position_sizer.get_sizing_summary(available_capital)['test_mode'] else 'NORMAL (10% position sizing)'}
🎯 Target: 1.2% minimum profit per trade
        """.strip()
        
        panel = Panel(
            f"{title}\n{subtitle}\n\n{info_text}",
            border_style="blue",
            padding=(1, 2)
        )
        
        self.console.print(panel)
        self.console.print()
    
    def _get_filtered_markets(self) -> List[Dict]:
        """
        Get filtered list of suitable markets
        
        Returns:
            List of market dictionaries with analysis
        """
        try:
            # Get all available USDT futures markets
            all_markets = self.market_data.get_available_markets()
            
            # Filter for major cryptocurrencies and analyze
            major_cryptos = [
                'BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'ADAUSDT', 'SOLUSDT',
                'DOTUSDT', 'MATICUSDT', 'AVAXUSDT', 'LINKUSDT', 'LTCUSDT',
                'XRPUSDT', 'UNIUSDT', 'ATOMUSDT', 'FILUSDT', 'VETUSDT'
            ]
            
            filtered_markets = []
            
            for market in all_markets:
                if market in major_cryptos:
                    try:
                        # Get market analysis
                        analysis = self._analyze_market(market)
                        if analysis:
                            filtered_markets.append(analysis)
                    except Exception as e:
                        logger.warning(f"Failed to analyze {market}: {e}")
                        continue
            
            # Sort by 24h volume (descending)
            filtered_markets.sort(key=lambda x: x.get('volume_24h', 0), reverse=True)
            
            return filtered_markets[:15]  # Top 15 markets
            
        except Exception as e:
            logger.error(f"Failed to get filtered markets: {e}")
            return []
    
    def _analyze_market(self, market: str) -> Optional[Dict]:
        """
        Analyze a market for suitability
        
        Args:
            market: Market symbol
            
        Returns:
            Market analysis dictionary or None
        """
        try:
            # Get market info
            market_info = self.market_data.get_market_info(market)
            
            # Get current price and ticker data
            current_price = self.market_data.get_current_price(market)
            
            # Get previous day OHLC for range analysis
            prev_ohlc = self.market_data.get_previous_day_ohlc(market)
            
            # Calculate daily range
            daily_range = prev_ohlc['high'] - prev_ohlc['low']
            range_percent = (daily_range / prev_ohlc['close']) * 100
            
            # Get minimum order value
            min_order_value = self.market_data.get_minimum_order_value(market, current_price)
            
            return {
                'market': market,
                'current_price': current_price,
                'daily_range': daily_range,
                'range_percent': range_percent,
                'volume_24h': prev_ohlc.get('volume', 0),
                'min_order_value': min_order_value,
                'min_amount': safe_float(market_info.get('min_amount', 0)),
                'prev_high': prev_ohlc['high'],
                'prev_low': prev_ohlc['low'],
                'prev_close': prev_ohlc['close']
            }
            
        except Exception as e:
            logger.warning(f"Failed to analyze {market}: {e}")
            return None
    
    def _display_market_table(self, markets: List[Dict], available_capital: float):
        """
        Display markets in a formatted table
        
        Args:
            markets: List of market analysis dictionaries
            available_capital: Available capital
        """
        table = Table(title="Available Trading Markets", show_header=True, header_style="bold magenta")
        
        table.add_column("#", style="cyan", width=3)
        table.add_column("Market", style="white", width=10)
        table.add_column("Price", style="green", width=12)
        table.add_column("Daily Range", style="yellow", width=15)
        table.add_column("Range %", style="yellow", width=10)
        table.add_column("Min Order", style="blue", width=12)
        table.add_column("Tradeable", style="white", width=10)
        
        for i, market_data in enumerate(markets, 1):
            market = market_data['market']
            price = market_data['current_price']
            daily_range = market_data['daily_range']
            range_percent = market_data['range_percent']
            min_order = market_data['min_order_value']
            
            # Check if tradeable with current capital
            can_trade = available_capital >= min_order
            tradeable_status = "✅ Yes" if can_trade else "❌ No"
            
            table.add_row(
                str(i),
                market.replace('USDT', ''),
                f"${price:,.2f}",
                f"${daily_range:,.2f}",
                f"{range_percent:.2f}%",
                f"${min_order:.2f}",
                tradeable_status,
                style="" if can_trade else "dim"
            )
        
        self.console.print(table)
        self.console.print()
    
    def _prompt_market_selection(self, markets: List[Dict]) -> Optional[str]:
        """
        Prompt user to select a market
        
        Args:
            markets: List of available markets
            
        Returns:
            Selected market symbol or None
        """
        while True:
            try:
                self.console.print("Select a trading pair:", style="bold")
                self.console.print("• Enter number (1-{}) to select a market".format(len(markets)))
                self.console.print("• Enter 'q' to quit")
                self.console.print("• Enter 'r' to refresh market data")
                
                choice = Prompt.ask("Your choice", default="1").strip().lower()
                
                if choice == 'q':
                    return None
                
                if choice == 'r':
                    self.console.print("🔄 Refreshing market data...", style="yellow")
                    return self.select_trading_asset(self.position_sizer.get_available_capital(10000))  # Recursive refresh
                
                # Try to parse as number
                try:
                    index = int(choice) - 1
                    if 0 <= index < len(markets):
                        return markets[index]['market']
                    else:
                        self.console.print(f"❌ Please enter a number between 1 and {len(markets)}", style="red")
                except ValueError:
                    self.console.print("❌ Invalid input. Please enter a number, 'r', or 'q'", style="red")
                
            except KeyboardInterrupt:
                return None
    
    def _confirm_selection(self, market: str, available_capital: float) -> bool:
        """
        Show final confirmation with trading details
        
        Args:
            market: Selected market
            available_capital: Available capital
            
        Returns:
            True if confirmed
        """
        try:
            # Get fresh market data
            current_price = self.market_data.get_current_price(market)
            
            # Calculate position sizing
            position_size = self.position_sizer.calculate_position_size(
                market, current_price, available_capital
            )
            
            # Get sizing summary
            sizing_summary = self.position_sizer.get_sizing_summary(available_capital)
            
            # Show confirmation panel
            details = f"""
🎯 Selected Market: {market}
💵 Current Price: ${current_price:,.2f}
💰 Position Size: ${position_size.size_usdt:.2f} ({position_size.mode.value} mode)
📏 Order Quantity: {position_size.quantity:.6f}
🔄 Leverage: {sizing_summary['leverage']}x
⚙️  Mode: {sizing_summary['mode']}
🧪 Test Mode: {'Yes' if sizing_summary['test_mode'] else 'No'}

📊 Trading Summary:
• Strategy: Daily Range Accumulation
• No stop losses (accumulation strategy)
• Minimum 1.2% profit target per trade
• Maximum {sizing_summary['max_positions']} concurrent positions
            """.strip()
            
            panel = Panel(
                details,
                title="Trading Configuration",
                border_style="green",
                padding=(1, 2)
            )
            
            self.console.print(panel)
            self.console.print()
            
            # Final confirmation
            if not position_size.is_valid:
                self.console.print(f"⚠️  Warning: {position_size.reason}", style="yellow")
                self.console.print()
            
            return Confirm.ask("Start trading with this configuration?", default=True)
            
        except Exception as e:
            self.console.print(f"❌ Error getting confirmation details: {e}", style="red")
            return False
    
    def show_market_analysis(self, market: str) -> Dict:
        """
        Show detailed analysis for a specific market
        
        Args:
            market: Market symbol
            
        Returns:
            Analysis dictionary
        """
        try:
            analysis = self._analyze_market(market)
            if not analysis:
                return {}
            
            # Create analysis table
            table = Table(title=f"{market} Market Analysis", show_header=True)
            table.add_column("Metric", style="cyan")
            table.add_column("Value", style="white")
            
            table.add_row("Current Price", f"${analysis['current_price']:,.2f}")
            table.add_row("Previous High", f"${analysis['prev_high']:,.2f}")
            table.add_row("Previous Low", f"${analysis['prev_low']:,.2f}")
            table.add_row("Daily Range", f"${analysis['daily_range']:,.2f}")
            table.add_row("Range Percentage", f"{analysis['range_percent']:.2f}%")
            table.add_row("24h Volume", f"{analysis['volume_24h']:,.2f}")
            table.add_row("Minimum Order", f"${analysis['min_order_value']:.2f}")
            
            self.console.print(table)
            return analysis
            
        except Exception as e:
            self.console.print(f"❌ Error analyzing {market}: {e}", style="red")
            return {}
