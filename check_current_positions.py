#!/usr/bin/env python3
"""
Check current positions on CoinEx
"""
import sys
sys.path.insert(0, 'src')

from src.exchange.coinex_client import CoinExClient

def check_positions():
    client = CoinExClient()
    try:
        print("Fetching current positions...")
        positions = client.get_positions()
        
        if isinstance(positions, dict) and 'data' in positions:
            data = positions['data']
            print(f"Found {len(data)} position records")
            
            active_positions = []
            for pos in data:
                amount = float(pos.get('amount', 0))
                if amount != 0:
                    active_positions.append(pos)
                    print(f"\n🔸 Active Position:")
                    print(f"  Market: {pos.get('market')}")
                    print(f"  Amount: {amount}")
                    print(f"  Side: {pos.get('side')}")
                    print(f"  Entry Price: {pos.get('open_price')}")
                    print(f"  Mark Price: {pos.get('mark_price')}")
                    print(f"  Unrealized PnL: {pos.get('unrealized_pnl')}")
            
            if not active_positions:
                print("\n✅ No active positions found")
            else:
                print(f"\n📊 Total active positions: {len(active_positions)}")
                
        else:
            print("Unexpected response format:", positions)
            
    except Exception as e:
        print(f"❌ Error checking positions: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    check_positions()