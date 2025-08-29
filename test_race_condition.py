#!/usr/bin/env python3
"""
Test for race conditions in buy order placement logic.
This test simulates the exact scenario where duplicate buy orders might occur.
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from src.exchange.coinex_client import CoinExClient
from src.utils.safe_conversions import safe_float
from datetime import datetime, timezone
import time

def test_both_markets_state():
    """Test both AAVE and ADA for current state and recent activity"""
    print("🔍 TESTING BOTH MARKETS FOR RACE CONDITIONS")
    print("=" * 60)
    
    client = CoinExClient()
    markets = ["AAVEUSDT", "ADAUSDT"]
    
    for market in markets:
        print(f"\n📊 ANALYZING {market}")
        print("-" * 30)
        
        try:
            # 1. Check current positions
            print(f"🔍 Current Position:")
            positions_response = client.get_positions(market=market)
            if isinstance(positions_response, dict):
                positions = positions_response.get('data', [])
            else:
                positions = positions_response if isinstance(positions_response, list) else []
            
            position_size = 0
            for pos in positions:
                if pos.get('market') == market:
                    position_size = safe_float(pos.get('open_interest', 0))
                    if position_size > 0:
                        print(f"   Position: {position_size} units @ ${safe_float(pos.get('avg_entry_price', 0)):.4f}")
                        print(f"   Unrealized PnL: ${safe_float(pos.get('unrealized_pnl', 0)):.2f}")
                    break
            
            if position_size == 0:
                print(f"   No position")
            
            # 2. Check ALL pending orders (not just today's)
            print(f"\n🔍 All Pending Orders:")
            orders_response = client.get_pending_orders(market)
            if isinstance(orders_response, dict):
                orders = orders_response.get('data', [])
            else:
                orders = orders_response if isinstance(orders_response, list) else []
            
            print(f"   Total orders: {len(orders)}")
            
            today = datetime.now(timezone.utc).date()
            buy_orders = []
            sell_orders_today = []
            sell_orders_old = []
            
            for order in orders:
                created_at = order.get('created_at', 0)
                if isinstance(created_at, (int, float)):
                    order_date = datetime.fromtimestamp(created_at / 1000, timezone.utc).date()
                else:
                    order_date = datetime.fromisoformat(str(created_at).replace('Z', '+00:00')).date()
                
                side = order.get('side', '')
                amount = safe_float(order.get('amount', 0))
                price = safe_float(order.get('price', 0))
                is_today = order_date == today
                
                print(f"   {side.upper()} | ${price:.4f} | {amount:.2f} | {order_date} | {'TODAY' if is_today else 'OLD'}")
                
                if side == 'buy':
                    buy_orders.append(order)
                elif side == 'sell':
                    if is_today:
                        sell_orders_today.append(order)
                    else:
                        sell_orders_old.append(order)
            
            # 3. Analysis
            print(f"\n📊 Analysis:")
            print(f"   Buy orders: {len(buy_orders)}")
            print(f"   Sell orders (today): {len(sell_orders_today)}")
            print(f"   Sell orders (old): {len(sell_orders_old)}")
            print(f"   Total sell amount (today): {sum(safe_float(o.get('amount', 0)) for o in sell_orders_today):.2f}")
            print(f"   Total sell amount (old): {sum(safe_float(o.get('amount', 0)) for o in sell_orders_old):.2f}")
            
            # 4. Determine if this state should allow buy orders
            has_position = position_size > 0
            has_today_buy = len(buy_orders) > 0 and any(
                datetime.fromtimestamp(safe_float(o.get('created_at', 0)) / 1000, timezone.utc).date() == today 
                for o in buy_orders
            )
            has_today_sell = len(sell_orders_today) > 0
            
            print(f"\n🎯 Decision Factors:")
            print(f"   Has position: {has_position}")
            print(f"   Has buy from today: {has_today_buy}")  
            print(f"   Has sell from today: {has_today_sell}")
            
            # Apply our emergency check logic
            should_block_buy = has_today_sell  # Our emergency check
            
            print(f"\n✅ Emergency Check Result:")
            if should_block_buy:
                print(f"   🚨 BUY BLOCKED: {len(sell_orders_today)} pending sells from today")
            else:
                print(f"   ✅ BUY ALLOWED: No pending sells from today")
            
            # Check for the problematic scenario
            if has_position and not has_today_sell and len(sell_orders_old) > 0:
                print(f"\n⚠️  POTENTIAL ISSUE SCENARIO:")
                print(f"   - Has position: {position_size} units")
                print(f"   - Old sell orders exist: {len(sell_orders_old)} orders")
                print(f"   - No sells from today: Could allow new buy cycle")
                print(f"   - This is NORMAL behavior for new trading day")
                
            elif has_position and has_today_sell:
                print(f"\n✅ NORMAL SCENARIO:")
                print(f"   - Has position with today's sells")
                print(f"   - Emergency check correctly blocks new buys")
                
            elif not has_position and not has_today_sell:
                print(f"\n✅ CLEAN START SCENARIO:")
                print(f"   - No position, no today's orders")
                print(f"   - Ready for first buy of the day")
            
        except Exception as e:
            print(f"❌ Error analyzing {market}: {e}")
            import traceback
            traceback.print_exc()

def test_recent_activity():
    """Check for recent order activity that might indicate race conditions"""
    print(f"\n🔍 CHECKING RECENT ACTIVITY (RACE CONDITION DETECTION)")
    print("=" * 60)
    
    client = CoinExClient()
    
    # Check recent user deals to see if there's rapid order activity
    for market in ["AAVEUSDT", "ADAUSDT"]:
        try:
            print(f"\n📈 Recent Activity for {market}:")
            
            # Get recent deals (last hour)
            end_time = int(time.time() * 1000)
            start_time = end_time - (60 * 60 * 1000)  # 1 hour ago
            
            deals_response = client.get_user_deals(
                market=market,
                start_time=start_time,
                end_time=end_time,
                limit=20
            )
            
            if isinstance(deals_response, dict):
                deals = deals_response.get('data', [])
            else:
                deals = deals_response if isinstance(deals_response, list) else []
            
            print(f"   Recent deals (last hour): {len(deals)}")
            
            if deals:
                print("   Recent activity:")
                for deal in deals[:5]:  # Show last 5 deals
                    deal_time = datetime.fromtimestamp(safe_float(deal.get('created_at', 0)) / 1000, timezone.utc)
                    side = deal.get('side', '')
                    amount = safe_float(deal.get('amount', 0))
                    price = safe_float(deal.get('price', 0))
                    
                    print(f"     {deal_time.strftime('%H:%M:%S')} | {side.upper()} | {amount:.2f} @ ${price:.4f}")
                    
                # Check for rapid buy-sell sequences that might cause race conditions
                buy_deals = [d for d in deals if d.get('side') == 'buy']
                sell_deals = [d for d in deals if d.get('side') == 'sell']
                
                if len(buy_deals) > 0 and len(sell_deals) > 0:
                    print(f"   ⚠️  POTENTIAL RACE CONDITION: {len(buy_deals)} buys, {len(sell_deals)} sells in last hour")
                elif len(buy_deals) > 1:
                    print(f"   🚨 MULTIPLE BUYS DETECTED: {len(buy_deals)} buy orders in last hour!")
                    
        except Exception as e:
            print(f"   ❌ Error checking recent activity: {e}")

def main():
    """Run comprehensive race condition test"""
    test_both_markets_state()
    test_recent_activity()
    
    print(f"\n" + "=" * 60)
    print("🎯 RACE CONDITION TEST SUMMARY")
    print("=" * 60)
    print("\nKey Findings:")
    print("✅ If both markets show 0 sells from today → Emergency check working correctly")
    print("⚠️  If recent activity shows multiple buys → Race condition issue") 
    print("🚨 If position exists but no today's sells → Normal new day scenario")
    print("\nNext Steps:")
    print("- Monitor during active trading hours")
    print("- Check logs when duplicate buy occurs")
    print("- Look for rapid order sequences")

if __name__ == "__main__":
    main()