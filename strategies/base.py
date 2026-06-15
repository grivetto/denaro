#!/usr/bin/env python3
"""Patch base.py to implement Dynamic Position Sizing and Trailing Stop Guard"""
import os
import ccxt
import asyncio
import pandas as pd
from pathlib import Path
from enum import Enum

from loguru import logger

# Constants
MAX_DRAWDOWN = 0.05  # 5 % of total capital
BASE_RISK_PER_TRADE = 0.02  # 2 % base risk per trade


class Side(Enum):
    BUY = 'buy'
    SELL = 'sell'


class Position:
    def __init__(self, symbol, side, amount, entry_price, tp_price, sl_price, signal_source, order_id=None, tp_order_id=None):
        self.symbol = symbol
        self.side = side
        self.amount = amount
        self.entry_price = entry_price
        self.tp_price = tp_price
        self.sl_price = sl_price
        self.signal_source = signal_source
        self.order_id = order_id
        self.tp_order_id = tp_order_id


class Signal:
    def __init__(self, side, symbol, amount, entry_price, tp_price, sl_price, source):
        self.side = side
        self.symbol = symbol
        self.amount = amount
        self.entry_price = entry_price
        self.tp_price = tp_price
        self.sl_price = sl_price
        self.source = source


class BaseStrategy:
    def __init__(self, exchange: ccxt.Exchange, db: object, initial_capital: float = 0.0):
        self.exchange = exchange
        self.db = db
        self.symbol = exchange.symbol  # Default symbol, overridden by subclasses
        self.initial_capital = initial_capital # Store initial capital for DPS calculation
        self.capital = initial_capital # Current capital, will be updated
        self.is_paused = False
        self._positions = {}
        self.logger = logger.bind(strategy=self.__class__.__name__)

    async def get_quote_capital(self):
        """Fetches current quote currency capital (e.g., USDC) from the exchange."""
        try:
            balance = await self.exchange.fetch_balance()
            quote_currency = self.symbol.split('/')[-1]
            return balance['free'].get(quote_currency, 0.0)
        except Exception as e:
            self.logger.error(f"Error fetching quote capital: {e}")
            return 0.0

    async def _get_dynamic_risk_factor(self, current_capital):
        """Calculates a dynamic risk factor based on drawdown."""
        if self.initial_capital <= 0: return BASE_RISK_PER_TRADE # Avoid division by zero
        drawdown = max(0.0, (self.initial_capital - current_capital) / self.initial_capital)
        # Reduce risk if drawdown is significant, but not below 10% of base risk
        factor = max(0.1, 1 - drawdown / MAX_DRAWDOWN)
        return factor

    async def _size(self, entry_price, stop_loss_price, short=False):
        """Calculates order size based on dynamic risk and market limits."""
        current_capital = await self.get_quote_capital()
        risk_factor = await self._get_dynamic_risk_factor(current_capital)
        risk_amount = current_capital * risk_factor

        stop_loss_distance = abs(entry_price - stop_loss_price)
        if stop_loss_distance <= 0: return 0.0

        calculated_size = risk_amount / stop_loss_distance

        try:
            market = self.exchange.market(self.symbol)
            min_notional = market.get('limits', {}).get('minNotional', 0.0)
            min_amount = market.get('limits', {}).get('amount', {}).get('min', 0.0)
            amount_precision = market.get('precision', {}).get('amount', 8)
            price_precision = market.get('precision', {}).get('price', 8)

            # Adjust size based on min_amount and min_notional
            if calculated_size < min_amount:
                calculated_size = min_amount
            
            final_size = round(calculated_size, amount_precision)
            
            # Ensure min notional
            if final_size * entry_price < min_notional:
                 final_size = round(min_notional / entry_price, amount_precision)
            
            # Check for short/long specific balance limits
            balance = await self.exchange.fetch_balance()
            base_currency, quote_currency = self.symbol.split('/')

            if short:
                available_base = balance['free'].get(base_currency, 0.0)
                final_size = min(final_size, available_base * 0.98) # Use 98% to be safe
            else:
                available_quote = balance['free'].get(quote_currency, 0.0)
                # Convert available quote to base amount based on entry price
                max_possible_size = available_quote / entry_price
                final_size = min(final_size, max_possible_size * 0.98) # Use 98% to be safe

            # Final check against minimum amount after all adjustments
            if final_size < min_amount:
                return 0.0
            
            return round(final_size, amount_precision)

        except Exception as e:
            self.logger.error(f"Error calculating size for {self.symbol}: {e}")
            return 0.0

    async def _place_market(self, side: Side, amount, price):
        """Places a market order for closing a position."""
        try:
            order_type = 'market'
            params = {{}}
            if self.exchange.id == 'binance':
                # For Binance, market orders might need side specified for close actions
                if side == Side.BUY: params['side'] = 'SELL' # Closing a short requires a SELL market order
                else: params['side'] = 'BUY' # Closing a long requires a BUY market order
                # Note: This logic might need refinement based on Binance's specific order rules for closing positions.

            order = await self.exchange.create_order(self.symbol, order_type, side.value, amount, params=params)
            self.logger.info(f"Market order for closing: {side.value} {amount} {self.symbol} @ {price}. Order ID: {order['id']}")
            # Hedge the spot market order on futures
            try:
                import os
                BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                hedge_script = os.path.join(BASE, 'tools', 'hedger_futures.py')
                # Convert Side enum to string for the script
                hedge_side = side.value.lower()  # 'buy' or 'sell'
                subprocess.Popen([
                    '/home/sergio/denaro/venv/bin/python3',
                    hedge_script,
                    self.symbol,
                    hedge_side,
                    str(amount)
                ])
                self.logger.info(f"Hedger script launched for {self.symbol} {hedge_side} {amount}")
            except Exception as hedge_err:
                self.logger.error(f"Failed to launch hedger script: {hedge_err}")

            return order
        except Exception as e:
            self.logger.error(f"Failed to place market order: {e}")
            return None

    async def _exec_sl(self, oid, pos, side: Side):
        """Executes stop-loss, cancels existing TP order, places market order and sets trailing stop."""
        try:
            # Cancel the existing Take Profit order if it exists
            if pos.tp_order_id:
                await self.exchange.cancel_order(pos.tp_order_id, self.symbol)
                self.logger.info(f"Cancelled TP order ID: {pos.tp_order_id}")

            # Place a market order to close the position
            close_order = await self._place_market(side, pos.amount, pos.entry_price)
            if not close_order:
                return # If market order failed, do not proceed

            # Set a Trailing Stop Loss
            # Note: Trailing stop orders are typically placed as separate order types, not directly with create_order for market closes.
            # The exact implementation depends on the exchange's API for trailing stops.
            # For Binance, it's often a separate order type like 'STOP_MARKET' or 'TAKE_PROFIT_MARKET' with specific params.
            # This is a simplified representation:
            trailing_stop_loss_price = 0.0
            if side == Side.BUY: # Closing a long position, stop loss is below entry
                trailing_stop_loss_price = pos.entry_price * (1 - 0.01) # 1% trailing stop below entry
            else: # Closing a short position, stop loss is above entry
                trailing_stop_loss_price = pos.entry_price * (1 + 0.01) # 1% trailing stop above entry

            # Placeholder for actual trailing stop order creation
            # This requires specific exchange API calls, e.g., for Binance:
            # await self.exchange.create_order(symbol, 'STOP_MARKET', side.value, amount, params={'stopPrice': trailing_stop_loss_price})
            self.logger.warning(f"Trailing stop order placement for symbol {self.symbol} is a placeholder. Requires exchange-specific implementation.")

            self.logger.info(f"Stop Loss triggered for order ID {oid}. Position closed. Trailing stop logic initiated (placeholder).")
            # Hedge the stop-loss market order on futures
            try:
                import os
                BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                hedge_script = os.path.join(BASE, 'tools', 'hedger_futures.py')
                hedge_side = side.value.lower()
                subprocess.Popen([
                    '/home/sergio/denaro/venv/bin/python3',
                    hedge_script,
                    self.symbol,
                    hedge_side,
                    str(pos.amount)
                ])
                self.logger.info(f"Hedger script launched for SL {self.symbol} {hedge_side} {pos.amount}")
            except Exception as hedge_err:
                self.logger.error(f"Failed to launch hedger script for SL: {hedge_err}")

            # Remove the position from active tracking after SL
            if oid in self._positions:
                del self._positions[oid]

        except Exception as e:
            self.logger.error(f"Error executing stop loss for order ID {oid}: {e}")

    async def on_candle(self, ohlcv):
        raise NotImplementedError

    async def _check_sl(self, current_price):
        """Checks if any active positions have hit their stop loss."""
        positions_to_remove = []
        for oid, pos in self._positions.items():
            if pos.sl_price is None: continue
            if pos.side == Side.BUY and current_price <= pos.sl_price:
                await self._exec_sl(oid, pos, Side.SELL) # Closing a long position
                positions_to_remove.append(oid)
            elif pos.side == Side.SELL and current_price >= pos.sl_price:
                await self._exec_sl(oid, pos, Side.BUY) # Closing a short position
                positions_to_remove.append(oid)
        # Clean up positions that have hit SL
        for oid in positions_to_remove:
            if oid in self._positions:
                del self._positions[oid]

    async def get_initial_capital(self):
        """Placeholder to fetch initial capital. This should be set during strategy init."""
        # This method needs to be implemented or capital needs to be passed during init.
        # For now, assume it's set during __init__.
        return self.initial_capital

    async def set_initial_capital(self, capital):
        """Sets the initial capital."""
        self.initial_capital = capital
        self.capital = capital # Also set current capital initially

