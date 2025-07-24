#!/usr/bin/env python3
"""
CoinEx Daily Range Accumulation Trading Bot
Main entry point for the live trading system
"""
import sys
import asyncio
import signal
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.layout import Layout
from rich.text import Text

# Import bot components
from src.core.event_driven_bot import EventDrivenBot
from src.core.trading_state import CalculationMode
from src.utils.logger import get_logger
from config.settings import validate_config, is_test_mode, get_position_size_mode
from src.utils.asset_selector import AssetSelector
from process_lock import ProcessLock


logger = get_logger(__name__)
console = Console()


class TradingBotManager:
    """Main manager for the trading bot with live terminal display"""
    
    def __init__(self):
        self.bot: Optional[EventDrivenBot] = None
        self.console = Console()
        self.running = False
        self.selected_market = None
        
    def create_display_layout(self) -> Layout:
        """Create the live terminal display layout"""
        layout = Layout()
        
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="main"),
            Layout(name="footer", size=3)
        )
        
        layout["main"].split_row(
            Layout(name="left"),
            Layout(name="right")
        )
        
        layout["left"].split_column(
            Layout(name="status", size=8),
            Layout(name="positions")
        )
        
        layout["right"].split_column(
            Layout(name="signals", size=8),
            Layout(name="orders")
        )
        
        return layout
    
    def update_display(self, layout: Layout):
        """Update the live display with current bot status"""
        if not self.bot:
            layout["header"].update(Panel("🤖 CoinEx Daily Range Bot - Initializing...", style="blue"))
            return
        
        # Header
        mode = "TEST MODE" if is_test_mode() else "LIVE MODE"
        status = "RUNNING" if self.running else "STOPPED"
        header_text = f"🤖 CoinEx Daily Range Bot - {mode} - {status}"
        layout["header"].update(Panel(header_text, style="green" if self.running else "red"))
        
        # Status panel
        status_table = Table(title="Bot Status", show_header=True)
        status_table.add_column("Metric", style="cyan", width=20)
        status_table.add_column("Value", style="white", width=25)
        
        status_table.add_row("Market", self.selected_market or "Not selected")
        status_table.add_row("Mode", get_position_size_mode())
        status_table.add_row("Last Update", datetime.now().strftime("%H:%M:%S"))
        status_table.add_row("Account Balance", f"${self.bot.get_account_balance():.2f}" if self.bot else "Loading...")
        
        current_price = self.bot.get_current_price(self.selected_market) if self.bot and self.selected_market else None
        status_table.add_row("Current Price", f"${current_price:,.2f}" if current_price else "Loading...")
        
        layout["status"].update(Panel(status_table))
        
        # Signals panel
        if self.bot and self.bot.state and self.bot.state.current_signals:
            signals = self.bot.state.current_signals
            signals_table = Table(title="Daily Range Signals", show_header=True)
            signals_table.add_column("Signal", style="cyan")
            signals_table.add_column("Price", style="green")
            signals_table.add_column("Status", style="yellow")
            
            calc_mode = "Hybrid" if self.bot.state.calculation_mode == CalculationMode.HYBRID_TODAY_LOW else "Initial"
            signals_table.add_row("Buy Signal", f"${signals.buy_price:,.2f}", calc_mode)
            signals_table.add_row("Sell Signal", f"${signals.sell_price:,.2f}", calc_mode)
            signals_table.add_row("Range Value", f"${signals.range_value:,.2f}", f"Gen {self.bot.state.calculation_generation}")
            signals_table.add_row("Cycles Today", str(self.bot.state.completed_cycles_today), "Completed")
            
            layout["signals"].update(Panel(signals_table))
        else:
            layout["signals"].update(Panel("No signals generated yet", style="yellow"))
        
        # Positions panel
        if self.bot:
            positions = self.bot.get_open_positions()
            if positions:
                pos_table = Table(title="Open Positions", show_header=True)
                pos_table.add_column("Market", style="cyan")
                pos_table.add_column("Side", style="white")
                pos_table.add_column("Size", style="green")
                pos_table.add_column("Entry", style="blue")
                pos_table.add_column("Current", style="yellow")
                pos_table.add_column("PnL", style="red")
                
                for pos in positions[:5]:  # Show max 5 positions
                    pnl_color = "green" if pos.unrealized_pnl >= 0 else "red"
                    pos_table.add_row(
                        pos.market,
                        pos.side.value,
                        f"{pos.size:.6f}",
                        f"${pos.avg_entry_price:.2f}",
                        f"${current_price:.2f}" if current_price else "N/A",
                        f"[{pnl_color}]${pos.unrealized_pnl:.2f}[/{pnl_color}]"
                    )
                
                layout["positions"].update(Panel(pos_table))
            else:
                layout["positions"].update(Panel("No open positions", style="dim"))
        else:
            layout["positions"].update(Panel("Loading positions...", style="dim"))
        
        # Orders panel
        if self.bot:
            orders = self.bot.get_active_orders()
            if orders:
                orders_table = Table(title="Active Orders", show_header=True)
                orders_table.add_column("Market", style="cyan")
                orders_table.add_column("Side", style="white")
                orders_table.add_column("Price", style="green")
                orders_table.add_column("Amount", style="blue")
                orders_table.add_column("Status", style="yellow")
                
                for order in orders[:5]:  # Show max 5 orders
                    orders_table.add_row(
                        order.market,
                        order.side.value,
                        f"${order.price:.2f}",
                        f"{order.amount:.6f}",
                        order.status.value
                    )
                
                layout["orders"].update(Panel(orders_table))
            else:
                layout["orders"].update(Panel("No active orders", style="dim"))
        else:
            layout["orders"].update(Panel("Loading orders...", style="dim"))
        
        # Footer
        footer_text = "Press Ctrl+C to stop | Updates every 5 seconds"
        if is_test_mode():
            footer_text += " | TEST MODE: Minimum orders only for 5 days"
        
        layout["footer"].update(Panel(footer_text, style="dim"))
    
    async def initialize_bot(self):
        """Initialize the trading bot"""
        try:
            self.console.print("🚀 Initializing CoinEx Daily Range Trading Bot...", style="bold blue")
            
            # Validate configuration
            errors = validate_config()
            if errors:
                self.console.print("❌ Configuration errors:", style="red")
                for error in errors:
                    self.console.print(f"  • {error}", style="red")
                return False
            
            # Create bot instance
            self.bot = EventDrivenBot()
            await self.bot.initialize()
            
            # Select trading market
            asset_selector = AssetSelector(self.bot.market_data, self.bot.position_sizer)
            
            # Update account status to get current balance
            await self.bot._update_account_status()
            account_balance = self.bot.get_account_balance()
            
            self.console.print(f"💰 Account Balance: ${account_balance:.2f} USDT", style="green")
            
            # Get market from command line argument or use default
            import sys
            self.selected_market = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT"
            self.console.print(f"📈 Selected Market: {self.selected_market}", style="cyan")
            
            # Initialize market for trading
            await self.bot.set_trading_market(self.selected_market)
            
            self.console.print("✅ Bot initialized successfully!", style="green")
            return True
            
        except Exception as e:
            self.console.print(f"❌ Failed to initialize bot: {e}", style="red")
            logger.error(f"Bot initialization failed: {e}", exc_info=True)
            return False
    
    async def run_bot(self):
        """Main bot execution loop with live display"""
        if not await self.initialize_bot():
            return
        
        self.running = True
        layout = self.create_display_layout()
        
        try:
            with Live(layout, refresh_per_second=1.0, screen=True):
                # Bot is now event-driven, just update display
                while self.running:
                    try:
                        # Update display only
                        self.update_display(layout)
                        
                        # Check bot health
                        if self.bot.state:
                            self.bot.state.log_state_summary()
                        
                        # Wait before display update
                        await asyncio.sleep(5)
                        
                    except KeyboardInterrupt:
                        break
                    except Exception as e:
                        logger.error(f"Error in display loop: {e}", exc_info=True)
                        await asyncio.sleep(10)  # Wait longer on error
                        
        except KeyboardInterrupt:
            pass
        finally:
            self.running = False
            if self.bot:
                await self.bot.shutdown()
            self.console.print("\n🛑 Bot stopped gracefully", style="yellow")
    
    def handle_shutdown(self, signum, frame):
        """Handle shutdown signal"""
        self.console.print("\n⚠️ Shutdown signal received...", style="yellow")
        self.running = False


async def main():
    """Main entry point"""
    # Setup signal handlers
    manager = TradingBotManager()
    signal.signal(signal.SIGINT, manager.handle_shutdown)
    signal.signal(signal.SIGTERM, manager.handle_shutdown)
    
    # Show startup banner
    startup_text = f"""
🚀 CoinEx Daily Range Accumulation Bot
=====================================

Strategy: Daily Range Accumulation (No Stop Losses)
Mode: {get_position_size_mode()} ({'Test Mode - Minimum Orders' if is_test_mode() else 'Live Mode - Full Position Sizing'})

⚠️ This bot trades with real money. Monitor carefully.
    """
    
    console.print(Panel(startup_text.strip(), title="Starting Bot", border_style="blue"))
    
    try:
        await manager.run_bot()
    except Exception as e:
        console.print(f"❌ Fatal error: {e}", style="red")
        logger.error(f"Fatal error in main: {e}", exc_info=True)
        return 1
    
    return 0


if __name__ == "__main__":
    # Ensure only one instance runs at a time
    with ProcessLock("main_trading_bot.lock"):
        try:
            exit_code = asyncio.run(main())
            sys.exit(exit_code)
        except KeyboardInterrupt:
            console.print("\n👋 Goodbye!", style="blue")
            sys.exit(0)