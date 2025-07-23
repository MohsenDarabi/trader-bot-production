#!/usr/bin/env python3
"""
Quick script to cancel the test order we just placed
"""
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from src.exchange.coinex_client import CoinExClient

# Cancel the test order
client = CoinExClient()

# Order details from the test
client_id = "DEBUG_TEST_1753247481"
order_id = 178476493920
market = "ETHUSDT"

print(f"Cancelling order {order_id} (client_id: {client_id})...")

try:
    result = client.cancel_order(market=market, client_id=client_id)
    print("✅ Order cancelled successfully!")
    print(f"Result: {result}")
except Exception as e:
    print(f"❌ Failed to cancel: {e}")