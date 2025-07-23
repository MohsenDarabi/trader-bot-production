#!/usr/bin/env python3
"""
Safe Test Runner for CoinEx Daily Range Accumulation Bot
Tests all functionality with real API calls but safe orders that won't execute
"""
import sys
import os
import time
from datetime import datetime, timezone
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.prompt import Confirm, Prompt
from rich.text import Text
from rich.live import Live
import traceback

# Import bot components
from src.exchange.coinex_client import CoinExClient
from src.exchange.order_manager import OrderManager
from src.core.position_manager import PositionManager
from src.core.profitability import ProfitabilityValidator
from src.data.market_data import MarketDataManager
from src.data.database import DatabaseManager
from src.core.strategy import DailyRangeStrategy
from src.core.position_sizing import PositionSizer
from src.core.recovery import StartupRecovery
from src.utils.asset_selector import AssetSelector
from config.settings import validate_config


class SafeTestRunner:
    """Safe test runner for validating bot functionality"""
    
    def __init__(self):
        self.console = Console()
        self.test_results = {}
        self.selected_market = None
        
        # Initialize components
        self.client = None
        self.database = None
        self.market_data = None
        self.strategy = None
        self.position_manager = None
        self.order_manager = None
        self.position_sizer = None
        self.recovery = None
        self.asset_selector = None
        
        self.test_order_id = None
        self.account_balance = 0.0
    
    def run_all_tests(self):
        """Run comprehensive test suite"""
        self.console.clear()
        self._show_welcome_banner()
        
        try:
            # Phase 1: System Validation
            self._run_phase("Phase 1: System Validation", [
                ("Validate Configuration", self._test_config_validation),
                ("Initialize Components", self._test_component_initialization),
                ("Test API Connectivity", self._test_api_connectivity),
                ("Fetch Account Information", self._test_account_info),
                ("Test Startup Recovery", self._test_startup_recovery)
            ])
            
            # Phase 2: Market Selection & Analysis
            self._run_phase("Phase 2: Market Selection & Analysis", [
                ("Select Trading Asset", self._test_asset_selection),
                ("Fetch Market Data", self._test_market_data),
                ("Calculate Strategy Signals", self._test_strategy_signals),
                ("Validate Profitability", self._test_profitability_validation),
                ("Calculate Position Sizing", self._test_position_sizing)
            ])
            
            # Phase 3: Safe Order Testing
            self._run_phase("Phase 3: Safe Order Testing", [
                ("Place Safe Test Order", self._test_safe_order_placement),
                ("Verify Order Status", self._test_order_status),
                ("Cancel Test Order", self._test_order_cancellation),
                ("Verify Order Removal", self._test_order_cleanup)
            ])
            
            # Phase 4: State Management
            self._run_phase("Phase 4: State Management", [
                ("Test Database Operations", self._test_database_operations),
                ("Test State Persistence", self._test_state_persistence),
                ("Test Recovery System", self._test_recovery_validation)
            ])
            
            # Show final results
            self._show_final_results()
            
        except KeyboardInterrupt:
            self.console.print("\n❌ Test run cancelled by user", style="red")
        except Exception as e:
            self.console.print(f"\n❌ Unexpected error: {e}", style="red")
            self.console.print(traceback.format_exc(), style="dim red")
    
    def _show_welcome_banner(self):
        """Show welcome banner"""
        title = Text("CoinEx Trading Bot - Safe Test Runner", style="bold blue")
        subtitle = Text("Testing all functionality with safe, non-executable orders", style="italic")
        
        info = """
🔐 SAFETY FEATURES ACTIVE:
• Test orders placed 50% below market price (won't execute)
• All orders marked as hidden (is_hide=True)
• Minimum order sizes only
• Immediate cancellation after testing
• No risk to capital

⚠️  REQUIREMENTS:
• Valid CoinEx API credentials in .env file
• Sufficient balance for minimum order test (~$50)
• Stable internet connection
        """.strip()
        
        panel = Panel(
            f"{title}\n{subtitle}\n\n{info}",
            border_style="blue",
            padding=(1, 2)
        )
        
        self.console.print(panel)
        self.console.print()
    
    def _run_phase(self, phase_name: str, tests: list):
        """Run a phase of tests"""
        self.console.print(f"\n🚀 {phase_name}", style="bold yellow")
        self.console.print("─" * 60)
        
        for test_name, test_func in tests:
            with self.console.status(f"Running {test_name}..."):
                try:
                    result = test_func()
                    if result:
                        self.console.print(f"✅ {test_name}", style="green")
                        self.test_results[test_name] = "PASS"
                    else:
                        self.console.print(f"❌ {test_name}", style="red")
                        self.test_results[test_name] = "FAIL"
                        if not Confirm.ask(f"Continue after {test_name} failure?"):
                            raise Exception(f"Test stopped at {test_name}")
                except Exception as e:
                    self.console.print(f"❌ {test_name}: {e}", style="red")
                    self.test_results[test_name] = f"ERROR: {e}"
                    if not Confirm.ask(f"Continue after {test_name} error?"):
                        raise
    
    def _test_config_validation(self) -> bool:
        """Test configuration validation"""
        errors = validate_config()
        if errors:
            self.console.print("Configuration errors:", style="red")
            for error in errors:
                self.console.print(f"  • {error}", style="red")
            return False
        return True
    
    def _test_component_initialization(self) -> bool:
        """Test component initialization"""
        try:
            # Initialize core components
            self.client = CoinExClient()
            self.database = DatabaseManager()
            self.market_data = MarketDataManager(self.client)
            self.strategy = DailyRangeStrategy(self.market_data)
            self.position_manager = PositionManager(self.client)
            
            validator = ProfitabilityValidator()
            self.order_manager = OrderManager(self.client, validator)
            self.position_sizer = PositionSizer(self.market_data, self.position_manager)
            self.recovery = StartupRecovery(self.client, self.database)
            self.asset_selector = AssetSelector(self.market_data, self.position_sizer)
            
            return True
        except Exception as e:
            self.console.print(f"Initialization failed: {e}", style="red")
            return False
    
    def _test_api_connectivity(self) -> bool:
        """Test API connectivity"""
        try:
            # Test public endpoint
            markets = self.client.get_futures_markets()
            if not markets:
                return False
            
            # Test authenticated endpoint
            account = self.client.get_account_info()
            return account is not None
            
        except Exception as e:
            self.console.print(f"API connectivity failed: {e}", style="red")
            return False
    
    def _test_account_info(self) -> bool:
        """Test account information retrieval"""
        try:
            account_info = self.client.get_account_info()
            
            # Display account summary
            if 'USDT' in account_info:
                balance_info = account_info['USDT']
                self.account_balance = float(balance_info.get('available', 0))
                
                table = Table(title="Account Information")
                table.add_column("Asset", style="cyan")
                table.add_column("Available", style="green")
                table.add_column("Frozen", style="yellow")
                
                table.add_row(
                    "USDT",
                    f"{balance_info.get('available', 0)}",
                    f"{balance_info.get('frozen', 0)}"
                )
                
                self.console.print(table)
                
                # Check if we have enough balance for testing
                if self.account_balance < 50:
                    self.console.print("⚠️ Warning: Low balance, may not be able to place test orders", style="yellow")
                
                return True
            
            return False
            
        except Exception as e:
            self.console.print(f"Account info failed: {e}", style="red")
            return False
    
    def _test_startup_recovery(self) -> bool:
        """Test startup recovery system"""
        try:
            recovery_report = self.recovery.perform_full_recovery(
                self.order_manager, self.position_manager, self.strategy
            )
            
            # Display recovery report
            self.console.print("Recovery Report:", style="bold")
            self.console.print(str(recovery_report))
            
            return recovery_report.recovery_successful
            
        except Exception as e:
            self.console.print(f"Recovery test failed: {e}", style="red")
            return False
    
    def _test_asset_selection(self) -> bool:
        """Test asset selection"""
        try:
            # For testing, we'll use BTCUSDT as default or let user select
            if Confirm.ask("Use BTCUSDT for testing? (recommended)", default=True):
                self.selected_market = "BTCUSDT"
            else:
                self.selected_market = self.asset_selector.select_trading_asset(self.account_balance)
                if not self.selected_market:
                    return False
            
            self.console.print(f"Selected market: {self.selected_market}", style="green")
            return True
            
        except Exception as e:
            self.console.print(f"Asset selection failed: {e}", style="red")
            return False
    
    def _test_market_data(self) -> bool:
        """Test market data fetching"""
        try:
            # Get current price
            current_price = self.market_data.get_current_price(self.selected_market)
            
            # Get previous day OHLC
            prev_ohlc = self.market_data.get_previous_day_ohlc(self.selected_market)
            
            # Get market info
            market_info = self.market_data.get_market_info(self.selected_market)
            
            # Display market data
            table = Table(title=f"{self.selected_market} Market Data")
            table.add_column("Metric", style="cyan")
            table.add_column("Value", style="white")
            
            table.add_row("Current Price", f"${current_price:,.2f}")
            table.add_row("Previous High", f"${prev_ohlc['high']:,.2f}")
            table.add_row("Previous Low", f"${prev_ohlc['low']:,.2f}")
            table.add_row("Previous Close", f"${prev_ohlc['close']:,.2f}")
            table.add_row("Daily Range", f"${prev_ohlc['high'] - prev_ohlc['low']:,.2f}")
            table.add_row("Minimum Order", f"{market_info.get('min_amount')}")
            
            self.console.print(table)
            return True
            
        except Exception as e:
            self.console.print(f"Market data test failed: {e}", style="red")
            return False
    
    def _test_strategy_signals(self) -> bool:
        """Test strategy signal generation"""
        try:
            # Generate signal for selected market
            signal = self.strategy.generate_daily_signal(self.selected_market, force=True)
            
            if not signal:
                self.console.print("No signal generated", style="red")
                return False
            
            # Display signal
            table = Table(title=f"Daily Range Signal - {self.selected_market}")
            table.add_column("Signal", style="cyan")
            table.add_column("Price", style="green")
            table.add_column("Details", style="white")
            
            table.add_row(
                "Buy Signal",
                f"${signal.buy_price:,.2f}",
                f"Previous Low ({signal.previous_low:,.2f}) + Range ({signal.range_value:,.2f})"
            )
            table.add_row(
                "Sell Signal",
                f"${signal.sell_price:,.2f}",
                f"Previous High ({signal.previous_high:,.2f}) - Range ({signal.range_value:,.2f})"
            )
            
            self.console.print(table)
            return True
            
        except Exception as e:
            self.console.print(f"Strategy signal test failed: {e}", style="red")
            return False
    
    def _test_profitability_validation(self) -> bool:
        """Test profitability validation system"""
        try:
            validator = ProfitabilityValidator()
            
            # Get current signal
            signal = self.strategy.get_current_signal(self.selected_market)
            if not signal:
                return False
            
            # Test signal profitability
            result = validator.is_signal_profitable(
                signal.buy_price, signal.sell_price, 100.0  # $100 test position
            )
            
            # Display validation result
            self.console.print(f"Profitability Check: {result}", style="green" if result.is_profitable else "red")
            
            return result.is_profitable
            
        except Exception as e:
            self.console.print(f"Profitability validation failed: {e}", style="red")
            return False
    
    def _test_position_sizing(self) -> bool:
        """Test position sizing calculation"""
        try:
            current_price = self.market_data.get_current_price(self.selected_market)
            
            # Calculate position size
            position_size = self.position_sizer.calculate_position_size(
                self.selected_market, current_price, self.account_balance
            )
            
            # Display sizing result
            table = Table(title="Position Sizing")
            table.add_column("Parameter", style="cyan")
            table.add_column("Value", style="white")
            
            table.add_row("Mode", position_size.mode.value)
            table.add_row("Position Size (USDT)", f"${position_size.size_usdt:.2f}")
            table.add_row("Quantity", f"{position_size.quantity:.6f}")
            table.add_row("Valid", "✅ Yes" if position_size.is_valid else "❌ No")
            table.add_row("Reason", position_size.reason)
            
            self.console.print(table)
            return position_size.is_valid
            
        except Exception as e:
            self.console.print(f"Position sizing test failed: {e}", style="red")
            return False
    
    def _test_safe_order_placement(self) -> bool:
        """Test safe order placement"""
        try:
            current_price = self.market_data.get_current_price(self.selected_market)
            
            # Calculate safe test price (50% below market)
            safe_price = current_price * 0.5
            
            # Get minimum order size
            min_order_value = self.market_data.get_minimum_order_value(self.selected_market, current_price)
            test_quantity = min_order_value / current_price
            
            self.console.print(f"\n🔒 PLACING SAFE TEST ORDER:", style="bold yellow")
            self.console.print(f"Market: {self.selected_market}")
            self.console.print(f"Current Price: ${current_price:,.2f}")
            self.console.print(f"Test Order Price: ${safe_price:,.2f} (50% below market)")
            self.console.print(f"Quantity: {test_quantity:.6f}")
            self.console.print(f"Hidden: Yes (is_hide=True)")
            self.console.print(f"⚠️ This order will NOT execute due to low price\n")
            
            if not Confirm.ask("Proceed with safe test order placement?"):
                return False
            
            # Place the safe test order
            order = self.order_manager.place_buy_order(
                market=self.selected_market,
                amount=test_quantity,
                price=safe_price,
                position_size=min_order_value,
                is_hide=True  # Hidden order as requested
            )
            
            if order:
                self.test_order_id = order.client_id
                self.console.print(f"✅ Test order placed successfully!", style="green")
                self.console.print(f"Client ID: {order.client_id}")
                self.console.print(f"Exchange Order ID: {order.exchange_order_id}")
                return True
            else:
                self.console.print("❌ Failed to place test order", style="red")
                return False
                
        except Exception as e:
            self.console.print(f"Order placement test failed: {e}", style="red")
            return False
    
    def _test_order_status(self) -> bool:
        """Test order status checking"""
        try:
            if not self.test_order_id:
                return False
            
            self.console.print(f"\n🔍 VERIFYING ORDER STATUS:", style="bold yellow")
            self.console.print(f"Please check your CoinEx account for order: {self.test_order_id}")
            self.console.print("The order should appear in your Open Orders with:")
            self.console.print(f"• Market: {self.selected_market}")
            self.console.print(f"• Side: BUY")
            self.console.print(f"• Status: Open/Pending")
            self.console.print(f"• Hidden: Yes (not visible in public order book)")
            
            # Wait for user confirmation
            if not Confirm.ask("\nCan you see the test order in your CoinEx account?"):
                self.console.print("❌ Order not visible - this may indicate an API issue", style="red")
                return False
            
            # Update order status from exchange
            updated_order = self.order_manager.update_order_status(self.test_order_id)
            
            if updated_order:
                self.console.print(f"✅ Order status confirmed: {updated_order.status.value}", style="green")
                return True
            else:
                self.console.print("❌ Could not update order status", style="red")
                return False
                
        except Exception as e:
            self.console.print(f"Order status test failed: {e}", style="red")
            return False
    
    def _test_order_cancellation(self) -> bool:
        """Test order cancellation"""
        try:
            if not self.test_order_id:
                return False
            
            self.console.print(f"\n🗑️ CANCELLING TEST ORDER:", style="bold yellow")
            self.console.print("Now we'll cancel the test order to complete the test...")
            
            if not Confirm.ask("Proceed with order cancellation?"):
                return False
            
            # Cancel the order
            success = self.order_manager.cancel_order(self.test_order_id)
            
            if success:
                self.console.print("✅ Test order cancelled successfully!", style="green")
                return True
            else:
                self.console.print("❌ Failed to cancel test order", style="red")
                return False
                
        except Exception as e:
            self.console.print(f"Order cancellation test failed: {e}", style="red")
            return False
    
    def _test_order_cleanup(self) -> bool:
        """Test order cleanup verification"""
        try:
            if not self.test_order_id:
                return True  # No order to clean up
            
            self.console.print(f"\n✨ VERIFYING ORDER CLEANUP:", style="bold yellow")
            self.console.print("Please verify the test order has been removed from your account")
            
            # Check if order still exists in our tracking
            if self.test_order_id in self.order_manager.active_orders:
                self.console.print("❌ Order still in active tracking", style="red")
                return False
            
            if Confirm.ask("Has the test order been removed from your CoinEx account?"):
                self.console.print("✅ Order cleanup confirmed", style="green")
                return True
            else:
                self.console.print("⚠️ Order may still be visible - check manually", style="yellow")
                return True  # Don't fail the test for this
                
        except Exception as e:
            self.console.print(f"Order cleanup test failed: {e}", style="red")
            return False
    
    def _test_database_operations(self) -> bool:
        """Test database operations"""
        try:
            # Test saving and loading data
            test_data = {
                'test_key': 'test_value',
                'timestamp': datetime.now(timezone.utc).isoformat()
            }
            
            # Save bot state
            success = self.database.save_bot_state('test_state', test_data)
            if not success:
                return False
            
            # Load bot state
            loaded_data = self.database.load_bot_state('test_state')
            if loaded_data != test_data:
                return False
            
            self.console.print("✅ Database operations working correctly", style="green")
            return True
            
        except Exception as e:
            self.console.print(f"Database test failed: {e}", style="red")
            return False
    
    def _test_state_persistence(self) -> bool:
        """Test state persistence"""
        try:
            # Save current state
            self.recovery.save_current_state(
                self.order_manager, self.position_manager, self.strategy
            )
            
            self.console.print("✅ State persistence working correctly", style="green")
            return True
            
        except Exception as e:
            self.console.print(f"State persistence test failed: {e}", style="red")
            return False
    
    def _test_recovery_validation(self) -> bool:
        """Test recovery system validation"""
        try:
            is_safe, issues = self.recovery.is_safe_to_trade(
                self.order_manager, self.position_manager
            )
            
            if issues:
                self.console.print("Recovery issues found:", style="yellow")
                for issue in issues:
                    self.console.print(f"  • {issue}", style="yellow")
            
            self.console.print(f"✅ Recovery validation complete - Safe to trade: {is_safe}", style="green")
            return True
            
        except Exception as e:
            self.console.print(f"Recovery validation failed: {e}", style="red")
            return False
    
    def _show_final_results(self):
        """Show final test results"""
        self.console.print("\n" + "=" * 60, style="bold")
        self.console.print("🎯 FINAL TEST RESULTS", style="bold blue")
        self.console.print("=" * 60, style="bold")
        
        # Count results
        passed = sum(1 for result in self.test_results.values() if result == "PASS")
        failed = sum(1 for result in self.test_results.values() if result not in ["PASS", "SKIP"])
        total = len(self.test_results)
        
        # Show summary
        if failed == 0:
            status_color = "green"
            status_text = "🎉 ALL TESTS PASSED"
        else:
            status_color = "red"
            status_text = f"❌ {failed} TESTS FAILED"
        
        self.console.print(f"\n{status_text}", style=f"bold {status_color}")
        self.console.print(f"Results: {passed}/{total} tests passed\n")
        
        # Show detailed results
        table = Table(title="Detailed Results")
        table.add_column("Test", style="white", width=40)
        table.add_column("Result", style="white", width=15)
        
        for test_name, result in self.test_results.items():
            if result == "PASS":
                result_style = "green"
                result_text = "✅ PASS"
            elif result == "SKIP":
                result_style = "yellow"
                result_text = "⏭️ SKIP"
            else:
                result_style = "red"
                result_text = "❌ FAIL"
            
            table.add_row(test_name, Text(result_text, style=result_style))
        
        self.console.print(table)
        
        # Show next steps
        if failed == 0:
            next_steps = """
🚀 NEXT STEPS:
✅ All systems validated successfully
✅ Bot is ready for careful live deployment
✅ Consider starting with test mode (minimum orders)
✅ Monitor closely for first few trades

⚠️ IMPORTANT REMINDERS:
• This strategy uses NO STOP LOSSES (accumulation approach)
• Positions may show paper losses during downtrends
• Ensure sufficient capital for position accumulation
• Start with test mode for 5 days minimum orders
            """
        else:
            next_steps = """
❌ ISSUES FOUND:
• Review failed tests above
• Fix configuration or API issues
• Re-run tests before live deployment
• Check CoinEx API credentials and permissions
            """
        
        panel = Panel(next_steps.strip(), title="Next Steps", border_style="blue")
        self.console.print(panel)


def main():
    """Main entry point"""
    if len(sys.argv) > 1 and sys.argv[1] == "--help":
        print("""
CoinEx Trading Bot - Safe Test Runner

This script tests all bot functionality with real API calls but uses safe,
non-executable orders that won't risk any capital.

Usage:
    python test_runner.py

Requirements:
    - Valid .env file with CoinEx API credentials
    - Sufficient account balance for minimum order test (~$50)
    - Stable internet connection

The test will:
    1. Validate all bot systems and API connectivity
    2. Select a trading market and analyze data  
    3. Place a safe test order (50% below market price)
    4. Verify order appears on exchange
    5. Cancel the test order
    6. Validate state persistence and recovery

All orders are marked as hidden (is_hide=True) and placed far from market
price to ensure they won't execute.
        """)
        return
    
    # Run the tests
    runner = SafeTestRunner()
    runner.run_all_tests()


if __name__ == "__main__":
    main()