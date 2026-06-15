#!/usr/bin/env python3
"""denaro-antigravity main.py – Main Trading Bot Orchestrator.

Combines strategies, SQLite DB, risk management, Telegram alerts, and FastAPI Web Dashboard into an integrated async runtime.
"""
from __future__ import annotations

import asyncio
import signal
import sys
import time
from typing import NoReturn, Dict, Any

from loguru import logger

from core.engine import ExchangeWrapper, RiskManager, TradeDB, settings
from services.dashboard import DashboardServer
from services.notifications import NotificationService
from strategies.base import BaseStrategy, Position, Side, Signal
from strategies.grid import GridTraderStrategy
from strategies.scalper import ScalperStrategy
from strategies.rsi_mean_rev import RSIReversionStrategy # Import new strategy
from strategies.dynamic_grid import DynamicGridStrategy


class TradingBot:
    def __init__(self):
        self.exchanges: Dict[str, ExchangeWrapper] = {}
        self.strategies: list[BaseStrategy] = []
        self.db: TradeDB | None = None
        self.risk_manager: RiskManager | None = None
        self.dashboard: DashboardServer | None = None
        self.notification_service: NotificationService | None = None
        self.closing = False
        self._ohlcv_cache: Dict[str, list[list[float]]] = {}  # Format: "{symbol}:{timeframe}" -> ohlcv
        self._reconcile_task: asyncio.Task | None = None

    async def _initialize_services(self):
        logger.info("Initializing Denaro Bot Engine...")
        self.db = TradeDB(settings.db_path)
        await self.db.connect()

        # Load exchanges
        for ex_name, ex_config in settings.exchanges.items():
            try:
                exchange = ExchangeWrapper(ex_name, ex_config)
                await exchange.initialize()
                self.exchanges[ex_name] = exchange
                logger.info(f"Exchange {ex_name} initialized.")
            except Exception as e:
                logger.error(f"Failed to initialize exchange {ex_name}: {e}")
                # Continue even if one exchange fails, others might work

        # Load Risk Manager
        self.risk_manager = RiskManager(self.db)

        # Load Notification Service
        self.notification_service = NotificationService()
        await self.notification_service.initialize()

        # Load Dashboard
        self.dashboard = DashboardServer(self.exchanges, self.strategies, self.db)
        asyncio.create_task(self.dashboard.start(settings.dashboard_host, settings.dashboard_port))

        # Load Strategies and their required exchanges
        required_exchanges = {{}}
        for ex_name in settings.exchanges:
            required_exchanges[ex_name] = self.exchanges.get(ex_name)

        # Dynamic strategy loading based on settings
        if settings.enable_scalper:
            for ex_name, exchange in required_exchanges.items():
                if exchange:
                    try:
                        # Load Scalper Strategy
                        # Fetching initial capital from settings
                        capital_setting = getattr(settings, f'{ex_name.lower()}_scalper_capital', settings.scalper_capital)
                        strategy = ScalperStrategy(exchange, self.db, initial_capital=capital_setting)
                        await strategy.set_initial_capital(capital_setting)
                        self.strategies.append(strategy)
                        logger.info(f"Loaded Scalper Strategy on exchange {ex_name} | Capital: {capital_setting:.2f} EUR")
                    except Exception as e:
                        logger.error(f"Failed to load Scalper Strategy on {ex_name}: {e}")

        if settings.enable_grid:
            for ex_name, exchange in required_exchanges.items():
                if exchange:
                    try:
                        # Load Grid Strategy
                        capital_setting = getattr(settings, f'{ex_name.lower()}_grid_capital', settings.grid_capital)
                        grid_symbol = getattr(settings, f'{ex_name.lower()}_grid_symbol', settings.grid_symbol)
                        grid_levels = getattr(settings, f'{ex_name.lower()}_grid_levels', settings.grid_levels)
                        grid_spacing = getattr(settings, f'{ex_name.lower()}_grid_spacing_pct', settings.grid_spacing_pct)
                        grid_take_profit = getattr(settings, f'{ex_name.lower()}_grid_take_pct', settings.grid_take_pct)
                        grid_trailing_stop = getattr(settings, f'{ex_name.lower()}_grid_trailing_stop', settings.grid_trailing_stop)

                        strategy = GridTraderStrategy(exchange, self.db, initial_capital=capital_setting, symbol=grid_symbol, levels=grid_levels, spacing_pct=grid_spacing, take_profit_pct=grid_take_profit, trailing_stop=grid_trailing_stop)
                        await strategy.set_initial_capital(capital_setting)
                        self.strategies.append(strategy)
                        logger.info(f"Loaded Grid Strategy on exchange {ex_name} | Symbol: {grid_symbol} | Capital: {capital_setting:.2f} EUR | Levels: {grid_levels}")
                    except Exception as e:
                        logger.error(f"Failed to load Grid Strategy on {ex_name}: {e}")

        # Load Dynamic Grid Strategy
        if settings.enable_dynamic_grid:
            for ex_name, exchange in required_exchanges.items():
                if exchange:
                    try:
                        # Load Dynamic Grid Strategy
                        capital_setting = getattr(settings, f'{ex_name.lower()}_dynamic_grid_capital_usdt', settings.dynamic_grid_capital_usdt)
                        symbol = getattr(settings, f'{ex_name.lower()}_dynamic_grid_symbol', settings.dynamic_grid_symbol)
                        base_levels = getattr(settings, f'{ex_name.lower()}_dynamic_grid_levels', settings.dynamic_grid_levels)
                        min_spacing = getattr(settings, f'{ex_name.lower()}_dynamic_grid_min_spacing', settings.dynamic_grid_min_spacing)
                        max_spacing = getattr(settings, f'{ex_name.lower()}_dynamic_grid_max_spacing', settings.dynamic_grid_max_spacing)
                        price_precision = getattr(settings, f'{ex_name.lower()}_dynamic_grid_price_precision', settings.dynamic_grid_price_precision)
                        amount_precision = getattr(settings, f'{ex_name.lower()}_dynamic_grid_amount_precision', settings.dynamic_grid_amount_precision)
                        take_profit_pct = getattr(settings, f'{ex_name.lower()}_dynamic_grid_take_pct', settings.dynamic_grid_take_pct)
                        trailing_stop = getattr(settings, f'{ex_name.lower()}_dynamic_grid_trailing_stop', settings.dynamic_grid_trailing_stop)

                        strategy = DynamicGridStrategy(
                            exchange,
                            self.db,
                            symbol=symbol,
                            capital=capital_setting,
                            base_levels=base_levels,
                            min_spacing=min_spacing,
                            max_spacing=max_spacing,
                            price_precision=price_precision,
                            amount_precision=amount_precision
                        )
                        await strategy.set_initial_capital(capital_setting)
                        # Ensure the strategy has the risk management attributes
                        strategy.take_profit_pct = take_profit_pct
                        strategy.trailing_stop = trailing_stop
                        self.strategies.append(strategy)
                        logger.info(f"Loaded Dynamic Grid Strategy on exchange {ex_name} | Symbol: {symbol} | Capital: {capital_setting:.2f} USDT | Levels: {base_levels}")
                    except Exception as e:
                        logger.error(f"Failed to load Dynamic Grid Strategy on {ex_name}: {e}")

        # Load RSI Mean Reversion Strategy
        if settings.enable_rsi_reversion:
            for ex_name, exchange in required_exchanges.items():
                if exchange:
                    try:
                        capital_setting = getattr(settings, f'{ex_name.lower()}_rsi_capital', settings.rsi_capital)
                        rsi_symbol = getattr(settings, f'{ex_name.lower()}_rsi_symbol', settings.rsi_symbol)
                        strategy = RSIReversionStrategy(exchange, self.db, initial_capital=capital_setting, symbol=rsi_symbol)
                        await strategy.set_initial_capital(capital_setting)
                        self.strategies.append(strategy)
                        logger.info(f"Loaded RSI Reversion Strategy on exchange {ex_name} | Symbol: {rsi_symbol} | Capital: {capital_setting:.2f} EUR")
                    except Exception as e:
                        logger.error(f"Failed to load RSI Reversion Strategy on {ex_name}: {e}")

        # Initialize Risk Manager after strategies have their initial capital set

        # Load BTC/USDT Grid Strategy
                if settings.enable_btc_grid:
                    for ex_name, exchange in required_exchanges.items():
                        if exchange:
                            try:
                                # Load BTC/USDT Grid Strategy
                                capital_setting = getattr(settings, f'{ex_name.lower()}_btc_grid_capital_usdt', settings.btc_grid_capital_usdt)
                                grid_symbol = getattr(settings, f'{ex_name.lower()}_btc_grid_symbol', settings.btc_grid_symbol)
                                grid_levels = getattr(settings, f'{ex_name.lower()}_btc_grid_levels', settings.btc_grid_levels)
                                grid_spacing = getattr(settings, f'{ex_name.lower()}_btc_grid_spacing_pct', settings.btc_grid_spacing_pct)
                                grid_take_profit = getattr(settings, f'{ex_name.lower()}_btc_grid_take_pct', settings.btc_grid_take_pct)
                                grid_trailing_stop = getattr(settings, f'{ex_name.lower()}_btc_grid_trailing_stop', settings.btc_grid_trailing_stop)
        
                                strategy = GridTraderStrategy(exchange, self.db, initial_capital=capital_setting, symbol=grid_symbol, levels=grid_levels, spacing_pct=grid_spacing, take_profit_pct=grid_take_profit, trailing_stop=grid_trailing_stop)
                                await strategy.set_initial_capital(capital_setting)
                                self.strategies.append(strategy)
                                logger.info(f"Loaded BTC/USDT Grid Strategy on exchange {ex_name} | Symbol: {grid_symbol} | Capital: {capital_setting:.2f} USDT | Levels: {grid_levels}")
                            except Exception as e:
                                logger.error(f"Failed to load BTC/USDT Grid Strategy on {ex_name}: {e}")
        
        # Load ETH/USDT Grid Strategy
                if settings.enable_eth_grid:
                    for ex_name, exchange in required_exchanges.items():
                        if exchange:
                            try:
                                # Load ETH/USDT Grid Strategy
                                capital_setting = getattr(settings, f'{ex_name.lower()}_eth_grid_capital_usdt', settings.eth_grid_capital_usdt)
                                grid_symbol = getattr(settings, f'{ex_name.lower()}_eth_grid_symbol', settings.eth_grid_symbol)
                                grid_levels = getattr(settings, f'{ex_name.lower()}_eth_grid_levels', settings.eth_grid_levels)
                                grid_spacing = getattr(settings, f'{ex_name.lower()}_eth_grid_spacing_pct', settings.eth_grid_spacing_pct)
                                grid_take_profit = getattr(settings, f'{ex_name.lower()}_eth_grid_take_pct', settings.eth_grid_take_pct)
                                grid_trailing_stop = getattr(settings, f'{ex_name.lower()}_eth_grid_trailing_stop', settings.eth_grid_trailing_stop)
        
                                strategy = GridTraderStrategy(exchange, self.db, initial_capital=capital_setting, symbol=grid_symbol, levels=grid_levels, spacing_pct=grid_spacing, take_profit_pct=grid_take_profit, trailing_stop=grid_trailing_stop)
                                await strategy.set_initial_capital(capital_setting)
                                self.strategies.append(strategy)
                                logger.info(f"Loaded ETH/USDT Grid Strategy on exchange {ex_name} | Symbol: {grid_symbol} | Capital: {capital_setting:.2f} USDT | Levels: {grid_levels}")
                            except Exception as e:
                                logger.error(f"Failed to load ETH/USDT Grid Strategy on {ex_name}: {e}")
        
        # Load BTC/USDT RSI Mean Reversion Strategy
                if settings.enable_btc_rsi:
                    for ex_name, exchange in required_exchanges.items():
                        if exchange:
                            try:
                                # Load BTC/USDT RSI Strategy
                                capital_setting = getattr(settings, f'{ex_name.lower()}_btc_rsi_capital_usdt', settings.btc_rsi_capital_usdt)
                                rsi_symbol = getattr(settings, f'{ex_name.lower()}_btc_rsi_symbol', settings.btc_rsi_symbol)
                                strategy = RSIReversionStrategy(exchange, self.db, initial_capital=capital_setting, symbol=rsi_symbol)
                                await strategy.set_initial_capital(capital_setting)
                                self.strategies.append(strategy)
                                logger.info(f"Loaded BTC/USDT RSI Reversion Strategy on exchange {ex_name} | Symbol: {rsi_symbol} | Capital: {capital_setting:.2f} USDT")
                            except Exception as e:
                                logger.error(f"Failed to load BTC/USDT RSI Reversion Strategy on {ex_name}: {e}")
        
        # Load ETH/USDT RSI Mean Reversion Strategy
                if settings.enable_eth_rsi:
                    for ex_name, exchange in required_exchanges.items():
                        if exchange:
                            try:
                                # Load ETH/USDT RSI Strategy
                                capital_setting = getattr(settings, f'{ex_name.lower()}_eth_rsi_capital_usdt', settings.eth_rsi_capital_usdt)
                                rsi_symbol = getattr(settings, f'{ex_name.lower()}_eth_rsi_symbol', settings.eth_rsi_symbol)
                                strategy = RSIReversionStrategy(exchange, self.db, initial_capital=capital_setting, symbol=rsi_symbol)
                                await strategy.set_initial_capital(capital_setting)
                                self.strategies.append(strategy)
                                logger.info(f"Loaded ETH/USDT RSI Reversion Strategy on exchange {ex_name} | Symbol: {rsi_symbol} | Capital: {capital_setting:.2f} USDT")
                            except Exception as e:
                                logger.error(f"Failed to load ETH/USDT RSI Reversion Strategy on {ex_name}: {e}")
        
        await self.risk_manager.initialize(self.strategies)

    async def _run_strategy_loop(self, strategy: BaseStrategy):
        while not self.closing:
            try:
                await asyncio.sleep(strategy.update_interval)
                if self.is_paused or strategy.is_paused: continue

                # Use shared OHLCV cache or fetch fresh data
                key = f"{strategy.symbol}:{strategy.timeframe}"
                if key in self._ohlcv_cache:
                    ohlcv = self._ohlcv_cache[key]
                else:
                    ohlcv = await strategy.exchange.fetch_ohlcv(strategy.symbol, strategy.timeframe)
                    if ohlcv:
                        self._ohlcv_cache[key] = ohlcv
                    else:
                        logger.warning(f"No OHLCV data received for {strategy.symbol} (cache missed)")
                        continue

                if not ohlcv:
                    logger.warning(f"No OHLCV data received for {strategy.symbol}")
                    continue

                # Update capital for DPS calculation
                try:
                    current_capital = await strategy.get_quote_capital()
                    await strategy.set_initial_capital(current_capital) # Update capital for dynamic risk calculation
                    await strategy._check_sl(ohlcv[-1][4]) # Check stop loss based on last close price
                except Exception as e:
                    logger.error(f"Error updating capital/checking SL for {strategy.symbol}: {e}")
                    continue

                # Handle different strategy types
                if hasattr(strategy, 'on_candle'):
                    signals = await strategy.on_candle(ohlcv)
                elif hasattr(strategy, 'run'):  # For dynamic grid which has its own run loop
                    await strategy.run()
                    continue
                
                if signals:
                    for sig in signals:
                        await self._execute_signal(sig, strategy)
            except asyncio.CancelledError:
                logger.info(f"Strategy loop for {strategy.symbol} cancelled.")
                break
            except Exception as e:
                logger.error(f"Error in strategy loop for {strategy.symbol}: {e}")
                await asyncio.sleep(5) # Wait before retrying

    async def _execute_signal(self, sig: Signal, strategy: BaseStrategy) -> None:
        # Check risk before executing trade
        if not self.risk_manager or not await self.risk_manager.is_safe(strategy.symbol, sig.side, sig.amount):
            logger.warning(f"Trade blocked by risk manager for {sig.symbol}: {sig.side} {sig.amount}")
            return

        try:
            logger.info(f"Executing signal: {sig.side.value} {sig.amount} {sig.symbol} @ {sig.entry_price} (TP: {sig.tp_price}, SL: {sig.sl_price}) from {sig.source}")
            order = await strategy.exchange.create_order(sig.symbol, 'limit', sig.side.value, sig.amount, sig.entry_price)
            
            new_pos = Position(sig.symbol, sig.side, sig.amount, sig.entry_price, sig.tp_price, sig.sl_price, sig.source, order_id=order['id'])
            strategy._positions[order['id']] = new_pos
            logger.info(f"Order placed: {order['id']} for {sig.symbol}")
        except Exception as e:
            logger.error(f"Failed to execute signal for {sig.symbol}: {e}")

    async def run(self):
        await self._initialize_services()

        # Start strategy loops
        strategy_tasks = []
        for strategy in self.strategies:
            # Ensure initial capital is set for strategies that use it (e.g., DPS)
            # If not set during init, fetch it here
            if strategy.initial_capital == 0:
                 current_capital = await strategy.get_quote_capital()
                 await strategy.set_initial_capital(current_capital)
             
            task = asyncio.create_task(self._run_strategy_loop(strategy))
            strategy_tasks.append(task)
            logger.info(f"Started strategy loop for {strategy.symbol}")

        # Start order reconciliation task
        self._reconcile_task = asyncio.create_task(self._reconcile_orders())
        logger.info("Started order reconciliation task")

        # Keep the main loop running
        while not self.closing:
            await asyncio.sleep(1)

    async def _reconcile_orders(self) -> None:
        """Periodically reconcile local order state with exchange."""
        while not self.closing:
            try:
                await asyncio.sleep(60)  # Reconcile every minute
                for strategy in self.strategies:
                    if not hasattr(strategy, '_positions'):
                        continue
                    
                    for oid, pos in list(strategy._positions.items()):
                        try:
                            if oid.startswith("MOCK-"):
                                continue  # Skip mock orders
                            
                            order = await strategy.exchange.fetch_order(oid, pos.symbol)
                            status = order.get('status', '')
                            
                            if status == 'closed' and oid in strategy._positions:
                                # Order filled - check if it was entry or exit
                                if pos.side == Side.BUY and pos.tp_order_id == oid:
                                    # This was a TP order
                                    exec_price = order.get('price', pos.tp_price) or pos.tp_price
                                    gross_pnl = (exec_price - pos.entry_price) * pos.amount
                                    fees = (pos.entry_price + exec_price) * pos.amount * 0.00075
                                    net_pnl = gross_pnl - fees
                                    
                                    logger.info(f"RECONCILE: TP filled for {pos.symbol} @ {exec_price:.4f}. Net PnL: {net_pnl:+.4f} USD.")
                                    strategy.db.save_trade(
                                        symbol=pos.symbol,
                                        side="sell",
                                        price=exec_price,
                                        amount=pos.amount,
                                        value_usd=pos.amount * exec_price,
                                        fee_usd=fees,
                                        net_pnl=net_pnl,
                                        strategy=strategy.name
                                    )
                                    del strategy._positions[oid]
                                elif pos.side == Side.SELL and pos.tp_order_id == oid:
                                    # This was an entry (sell) order
                                    # Check if corresponding buy filled
                                    pass
                            elif status == 'canceled':
                                logger.warning(f"RECONCILE: Order {oid} was canceled on exchange")
                                if oid in strategy._positions:
                                    del strategy._positions[oid]
                        except Exception as e:
                            logger.debug(f"Reconcile check for {oid} failed: {e}")
                            continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Order reconciliation error: {e}")

    def stop(self):
        self.closing = True
        logger.info("Shutting down trading bot...")
        # Close exchange connections
        for exchange in self.exchanges.values():
            exchange.close()
        # Close DB connection
        if self.db:
            self.db.close()
        logger.info("Trading bot shut down.")

async def main():
    bot = TradingBot()
    # Handle shutdown signals
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(bot.stop()))

    await bot.run()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped manually.")
    except Exception as e:
        logger.critical(f"Unhandled exception in main: {e}")
        sys.exit(1)
