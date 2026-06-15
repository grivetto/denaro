#!/usr/bin/env python3
import asyncio
import time
from .base import BaseStrategy, Side, Signal, Position
import numpy as np
from loguru import logger

class DynamicGridStrategy(BaseStrategy):
    """Grid that adapts spacing based on order-book depth & volatility."""
    def __init__(self, exchange, db, symbol, capital, base_levels=3,
                 min_spacing=0.0002, max_spacing=0.005, price_precision=6, amount_precision=6):
        super().__init__(exchange, db, initial_capital=capital)
        self.symbol = symbol
        self.base_levels = base_levels
        self.min_spacing = min_spacing
        self.max_spacing = max_spacing
        self.price_precision = price_precision
        self.amount_precision = amount_precision
        self.timeframe = "1m"
        self.update_interval = 30
        self._grid_levels = []
        self._last_sync = 0.0
        self._sync_interval = 300
        self.take_profit_pct = 0.004
        self.trailing_stop = True

    async def _calc_spacing(self):
        """Calculate dynamic spacing based on depth + volatility."""
        try:
            depth = await self.exchange.fetch_l2_order_book(self.symbol, limit=10)
            bid = depth['bids'][0][0] if depth['bids'] else 0
            ask = depth['asks'][0][0] if depth['asks'] else 0
            spread = (ask - bid) / ((ask + bid) / 2) if (ask + bid) / 2 != 0 else 0

            spacing = self.min_spacing + (self.max_spacing - self.min_spacing) * \
                      (1 / (1 + np.exp(-10 * (spread - 0.001))))
            return max(self.min_spacing, min(self.max_spacing, spacing))
        except Exception as e:
            self.logger.error(f"Error calculating dynamic spacing for {self.symbol}: {e}")
            return (self.min_spacing + self.max_spacing) / 2

    async def _sync_grid(self):
        """Synchronize grid orders with desired levels."""
        spacing = await self._calc_spacing()
        ticker = await self.exchange.fetch_ticker(self.symbol)
        price = ticker['last']
        
        existing_ids = [lvl['order_id'] for lvl in self._grid_levels if lvl.get('order_id')]
        if existing_ids:
            await asyncio.gather(
                *[self.exchange.cancel_order(oid, self.symbol) for oid in existing_ids],
                return_exceptions=True
            )
        
        desired_orders = []
        for i in range(1, self.base_levels + 1):
            buy_price = round(price * (1 - i * spacing), self.price_precision)
            sell_price = round(price * (1 + i * spacing), self.price_precision)
            desired_orders.append((Side.BUY, buy_price))
            desired_orders.append((Side.SELL, sell_price))
        
        quote_capital = await self.get_quote_capital()
        capital_per_level = quote_capital / max(len(desired_orders), 1)
        
        precision, min_amount, min_cost = self.exchange.get_market_precision_and_limits(self.symbol)
        
        new_levels = []
        for side, lvl_price in desired_orders:
            try:
                amount = round(capital_per_level / lvl_price, self.amount_precision)
                if amount < min_amount or amount * lvl_price < min_cost:
                    continue
                
                order = await self.exchange.create_order(
                    symbol=self.symbol,
                    order_type="limit",
                    side=side.value,
                    amount=amount,
                    price=lvl_price
                )
                
                new_levels.append({
                    'side': side,
                    'price': lvl_price,
                    'amount': amount,
                    'entry_price': lvl_price if side == Side.BUY else 0.0,
                    'lowest_price': 999999.0,
                    'order_id': order['id'],
                    'filled': False
                })
                
                self.logger.info(f"DynamicGrid: Placed {side.name} @ {lvl_price} | amount: {amount} | ID: {order['id']}")
            except Exception as e:
                self.logger.error(f"DynamicGrid: Failed to place {side.name} @ {lvl_price}: {e}")
        
        self._grid_levels = new_levels
        self._last_sync = time.time()
        self.logger.info(f"DynamicGrid sync complete: {len(self._grid_levels)} active levels for {self.symbol}")

    async def on_candle(self, ohlcv):
        """Monitor grid levels and resync when needed."""
        if not ohlcv:
            return []
        
        curr_price = float(ohlcv[-1][4])
        
        if not self._grid_levels or time.time() - self._last_sync > self._sync_interval:
            await self._sync_grid()
            return []
        
        for lvl in self._grid_levels:
            if lvl['side'] == Side.BUY and lvl['entry_price'] > 0:
                lvl['lowest_price'] = min(lvl['lowest_price'], curr_price)
                loss_pct = (lvl['lowest_price'] - lvl['entry_price']) / lvl['entry_price'] * 100
                if loss_pct < -2.0:
                    self.logger.warning(f"DynamicGrid STOP-LOSS {lvl['side'].name} @ {curr_price:.4f} | Loss: {loss_pct:+.1f}%")
                    try:
                        await self.exchange.cancel_order(lvl['order_id'], self.symbol)
                    except Exception:
                        pass
                    lvl['filled'] = True
        
        return []

    async def on_order_update(self, order):
        """Handle filled grid levels by placing opposing orders."""
        order_id = order.get('id', '')
        status = order.get('status', '')
        
        if status != 'closed':
            return
        
        lvl_idx = next((i for i, lvl in enumerate(self._grid_levels) if lvl['order_id'] == order_id), None)
        if lvl_idx is None:
            return
        
        filled_lvl = self._grid_levels[lvl_idx]
        self.logger.info(f"DynamicGrid LEVEL FILLED: {filled_lvl['side'].name} @ {filled_lvl['price']:.4f}")
        
        if filled_lvl['side'] == Side.BUY:
            opp_side = Side.SELL
            opp_price = round(filled_lvl['price'] * (1 + self.take_profit_pct), self.price_precision)
        else:
            opp_side = Side.BUY
            opp_price = round(filled_lvl['price'] * (1 - self.take_profit_pct), self.price_precision)
        
        try:
            new_order = await self.exchange.create_order(
                symbol=self.symbol,
                order_type="limit",
                side=opp_side.value,
                amount=filled_lvl['amount'],
                price=opp_price
            )
            
            filled_lvl['order_id'] = new_order['id']
            filled_lvl['side'] = opp_side
            filled_lvl['price'] = opp_price
            filled_lvl['entry_price'] = opp_price if opp_side == Side.BUY else 0.0
            filled_lvl['lowest_price'] = 999999.0
            filled_lvl['filled'] = False
            
            self.logger.info(f"DynamicGrid recycled: {opp_side.name} @ {opp_price:.4f} | ID: {new_order['id']}")
        except Exception as e:
            self.logger.error(f"DynamicGrid: Failed to place recycled order @ {opp_price:.4f}: {e}")

    async def shutdown(self):
        """Cancel all open grid orders on shutdown."""
        self.logger.info("DynamicGrid shutdown: cancelling outstanding orders...")
        ids = [lvl['order_id'] for lvl in self._grid_levels if lvl.get('order_id')]
        if ids:
            await asyncio.gather(
                *[self.exchange.cancel_order(oid, self.symbol) for oid in ids],
                return_exceptions=True
            )
        self._grid_levels.clear()
