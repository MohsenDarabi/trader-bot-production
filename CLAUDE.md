# CRITICAL: Bot Deployment Location

**🚨 THE BOT RUNS IN DOCKER ON A VM, NOT LOCALLY! 🚨**

When investigating deployed bot issues:
- **NEVER** check local `logs/` directory - these are OLD
- **ALWAYS** use Docker commands to check the actual running bot

# Bash Commands

## Check Deployed Bot (VM/Docker)
- `docker ps` - List running containers
- `docker logs ada-bot --tail 100` - View bot logs
- `docker logs ada-bot 2>&1 | grep -i "buy\|balance"` - Search logs
- `docker exec ada-bot cat /app/logs/trading_bot.log` - View logs inside container
- `docker restart ada-bot` - Restart container

## Deployment
- `./deploy.sh` - Deploy to VM
- `./remote_restart.sh` - Restart bots on VM

# Core Files

## Trading Bot
- `src/core/trading_bot.py` - Main bot logic
  - `_initialize_from_exchange()` - MUST load balance before startup buy (lines 469-487)
  - `_check_for_completed_sell_orders()` - REST API cycle completion detection
  - `_should_place_buy_order()` - Buy decision logic
  
## Order Management  
- `src/exchange/order_manager.py` - Order placement and tracking
- `src/core/order_pairing_manager.py` - Buy-sell pairing

## Known Issues

### No Buy Orders Placed
- Check balance is loaded in `_initialize_from_exchange()` BEFORE `_perform_startup_order_cleanup()`
- Balance shows $0.00 = balance loading code not executing
- Should see log: "✅ Account balance loaded: $XXX.XX"

### Cycle Not Completing
- Check `_check_for_completed_sell_orders()` is running every 60 seconds
- Only TODAY's non-orphaned sells trigger completion
- WebSocket handlers are DISABLED - using REST API polling

# Testing

## Local Testing
- Use local logs in `logs/` directory
- Run with `python main.py ADAUSDT`

## Production Verification
- Always check Docker logs, not local logs
- Verify with `docker logs <container> --tail 100`

# Important Notes

- WebSocket is DISABLED - all updates via REST API polling
- Cycle completion detected every 60 seconds in `_sync_positions()`
- Buy orders need: valid signal + no pending buys + (cycle complete OR first of day)
- Orphaned sells have "_OS_" in client_id