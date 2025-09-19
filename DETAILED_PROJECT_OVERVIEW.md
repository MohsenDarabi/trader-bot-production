# Trader Bot Hourly — Comprehensive Project Brief

## Current Focus & Recent Changes
- **Completed**: Raised all fallback sell pricing to guarantee ≥1 % profit and clamp to active strategy sell prices; unified pairing + orphaned coverage now log and execute with full precision via `safe_float`/`safe_str_format`.
- **Completed**: Fixed two production regressions spotted in latest logs: `CoinExClient.get_user_deals()` now accepts optional markets for bulk syncs, and the buy-order timeout logic no longer clashes with strategy signal lookups.
- **Completed**: Signal display and generation logs now reflect the active trading interval (`HOURLY` vs `DAILY`) so restarts immediately show the correct cadence.
- **In Progress**: Implementing `MarketDataManager.is_new_trading_hour()` and fully wiring hourly signal gating (see §8) remains the top functional gap.
- **In Progress**: Restart safety refinements underway — orphaned sells are now period-aware and tracked, with completion hooks pending validation.
- **Next Validation**: Run `make quick-check` (fast lint/syntax gate) before committing; follow with targeted strategy backtests for both intervals once hourly scaffolding is finished.


## 1. Mission & Scope
- **Goal**: Automate range-accumulation trading on CoinEx perpetual futures, accumulating positions during drawdowns and exiting at defined profit thresholds.
- **Coverage**: Single bot codebase supports both the legacy daily cadence and the in-progress hourly cadence controlled by `TRADING_INTERVAL` / `TRADING_TIMEFRAME`.
- **Capital Management**: Defaults to 1% position sizing with leverage (`LEVERAGE` env), no stop-loss, profitability validated before exits.

## 2. Core Strategy Mechanics
- **Range Formula**: `(previous_high - previous_low) / RANGE_DIVISOR` to derive buy (low + range) and sell (high - range) targets (see `src/core/strategy.py`).
- **Signal Persistence**: Signals cached in memory and persisted via `DatabaseManager` so restarts reuse current-period signal.
- **Timeframes**:
  - Daily flow uses UTC date boundaries and `SIGNAL_GENERATION_TIME`.
  - Hourly flow reuses the same machinery but still needs `MarketDataManager.is_new_trading_hour()` and complete integration in `_check_and_generate_signals()` (see `HOURLY_IMPLEMENTATION_PLAN.md`).

## 3. Runtime Architecture
- **Primary Orchestrator**: `DailyRangeBot` in `src/core/trading_bot.py` coordinates initialization, periodic tasks, and market-level processing.
- **Exchange Abstraction**: `ExchangeFactory` chooses between `CoinExClient` (real) and SafeMode client (test). `OrderManager` handles REST order lifecycle; `OrderTracker` / `OrderPairingManager` reconcile fills and pair buys to sells.
- **Data Layer**: `MarketDataManager` provides REST data; WebSocket provider exists but is disabled in production for reliability (REST polling only).
- **Position Management**: `PositionManager` keeps exchange-aligned positions; `RecentOrderTracker` prevents duplicate order placement after restarts.
- **Circuit Control**: `TradingCircuitBreaker` enforces cooldown windows per action, escalates when coverage violations persist, and records statistics for observability.

### 3.1 Startup Sequence Highlights
1. Instantiate exchange client, market data, strategy, position manager, order manager, sizing, and trackers (`initialize()`).
2. Configure CoinEx WebSocket objects but skip connection (stability decision).
3. Synchronize exchange state: load pending orders, sync positions, invoke `_perform_startup_order_cleanup()`, and wait out funding settlement windows if needed.
4. For each configured market, validate symbol format, adjust leverage intelligently, generate/force signals, configure pairing rules, and register markets for the main loop.

### 3.2 Main Trading Loop (every ~3 seconds, see `TRADING_BOT_FLOW.md`)
- Daily reset detection & logging.
- Display current signals and balance summaries.
- Periodic account refresh (120s) and position sync (60s).
- Pairing task processing and once-per-day disk checks.
- Market pipeline per symbol:
  1. `_check_and_generate_signals()` guarded by settlement windows/timeframe.
  2. `_check_and_cover_orphaned_positions()` ensures every position has active sell coverage.
  3. `_check_exit_opportunities()` validates profitability via `ProfitabilityValidator` before placing exits.
  4. `_check_entry_opportunities()` handles cleanup, circuit breaker gating, period-based buy logic, and entry order placement.

## 4. Safety & Reliability Controls
- **Circuit Breaker**: Priority-aware cooldowns (`emergency`, `high`, `normal`, `low`); escalated cooldown if coverage violations persist.
- **Coverage Enforcement**: `_check_and_cover_orphaned_positions()` and `_calculate_position_sell_balance()` make sure every open position has matching sell orders even if manual orders exist.
- **Duplicate Prevention**: `_ensure_single_buy_order()` cancels extras, `_get_today_pending_sell_orders()` blocks buys when today's sell orders exist, `RecentOrderTracker` filters rapid duplicates.
- **Profitability Validation**: Every exit uses `ProfitabilityValidator` to guarantee net-positive after fees and required `MIN_PROFIT_PERCENT`.
- **Funding Protection**: Startup waits via `settlement_handler` utility to avoid executing during CoinEx funding fee settlement minutes (00:00, 08:00, 16:00 UTC).
- **Smart Logging**: `log_trading_event()` and `force_log_summaries()` provide structured event logs; `_should_log_state_change()` reduces noise.

## 5. Configuration Essentials (`config/settings.py`)
- **Runtime Mode**: `TRADING_ENV` toggles test (`ExchangeFactory` safe mode) vs production; `TRADING_MODE` governs min-order testing behavior.
- **Timeframe Controls**: `TRADING_INTERVAL`/`TRADING_TIMEFRAME` select daily vs hourly cadence.
- **Capital Controls**: `POSITION_SIZE_PERCENT`, `MAX/MIN_POSITION_SIZE_PERCENT`, `LEVERAGE`.
- **Fees & Profit Targets**: `MAKER_FEE`, `TAKER_FEE`, `MIN_PROFIT_PERCENT`.
- **Logging**: `LOG_LEVEL`, `ENABLE_SMART_LOGGING`, `LOG_SUMMARY_INTERVAL`.
- **Retries**: API retry backoff configuration to handle CoinEx rate limits.
- **Validation**: `validate_config()` ensures required secrets and options exist; use before deployment starts.

## 6. Deployment Environment & Procedures
- **Execution Context**: Production bots run inside Docker containers on VM infrastructure; do not rely on local `logs/` (`CLAUDE.md`).
- **Essential VM Commands**: `docker ps`, `docker logs ada-bot --tail 100`, `docker exec ...`, `docker restart ada-bot`.
- **Deployment Packages**: `trader-bot-linear-fixes.tar.gz` images, `.env.<market>` files, and `ssh-key-2025-07-27.key` for remote access (`DEPLOYMENT_INSTRUCTIONS.md`).
- **Workflow Best Practices (`DEPLOYMENT_BEST_PRACTICES.md`)**:
  - Build & test Docker image locally (`docker build --platform linux/amd64`, run smoke tests) before transfer.
  - Preserve service isolation; deploy specific market without restarting others.
  - Verify credentials per bot to avoid account mix-ups.
  - Follow script-driven deploy: `deploy.sh <bot> update`, `remote_restart.sh`, etc.
- **Monitoring**: After redeploy, tail container logs and confirm only intended service restarted.

## 7. Code Quality & Tooling
- **Pre-commit Enforcement**: `make dev-setup` installs hooks; commits blocked on syntax/import errors.
- **Developer Commands** (`README_CODE_QUALITY.md`):
  - `make quick-check` (fast critical checks)
  - `make lint`, `make check-syntax`, `make check-imports`
- **CI Philosophy**: Treat hooks as gatekeepers; bypass only during emergencies with `--no-verify` and follow-up cleanup.

## 8. Known Gaps & Active Workstreams
- Missing `MarketDataManager.is_new_trading_hour()` implementation; hourly mode still relies on daily gating (`HOURLY_IMPLEMENTATION_PLAN.md`).
- `_check_and_generate_signals()` must switch between daily/hourly generators based on `TRADING_INTERVAL`.
- Verify absence of hard-coded absolute paths during deployment.
- Hourly strategy regression testing outstanding for both test and production environments.
- Historical architectural concern: de-sync between multiple order/position tracking sources (`TRADING_BOT_ANALYSIS.md`)—current design leans on REST-first synchronization but ongoing vigilance required.

## 9. Observability & Troubleshooting
- **Local Debugging**: For sandbox runs, `logs/trading_bot.log` captures detailed output with smart summaries.
- **Production Diagnostics**:
  - Use Docker log commands from VM, not local workspace.
  - Search container logs for critical keywords: `buy`, `sell`, `coverage`, etc.
- **State Verification**: `_order_circuit_breaker.get_statistics()` and position/order summaries provide quick health snapshots.
- **Funding Settlements**: `src/utils/settlement_handler.py` logs next settlement windows—ensure restarts target safe windows.

## 10. Reference Map
- Strategy & orchestration: `src/core/trading_bot.py`, `src/core/strategy.py`
- Exchange integration: `src/exchange/` (client, order manager, order tracker)
- Market data: `src/data/market_data.py`, `src/data/websocket_market_data.py`
- Safety utilities: `src/core/recent_order_tracker.py`, `src/utils/smart_logging.py`, `src/utils/safe_conversions.py`
- Deployment docs: `CLAUDE.md`, `DEPLOYMENT_INSTRUCTIONS.md`, `DEPLOYMENT_BEST_PRACTICES.md`, `README_MULTI_INSTANCE_DEPLOY.md`
- Planning artifacts: `HOURLY_IMPLEMENTATION_PLAN.md`, `TRADING_BOT_FLOW.md`, `TRADING_BOT_ANALYSIS.md`

---

This brief consolidates the architecture, runtime behavior, and operational practices needed to work effectively on the Trader Bot Hourly codebase. It should serve as the baseline context before implementing the next round of hourly strategy enhancements or deployment tasks.
