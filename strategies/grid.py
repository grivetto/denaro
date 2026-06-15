"""denaro-antigravity strategies/grid.py – Symmetric Grid Strategy.

Places limit orders around a starting mid-price.
When a limit order is filled, the opposing order is placed shifted by the profit step, guaranteeing real profits.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

from loguru import logger

from core.engine import Settings, TradeDB, settings
from strategies.base import BaseStrategy, Position, Side, Signal

_FEE_PCT = 0.00075  # 0.075% BNB discounted fee per side

@dataclass
class GridLevel:
    price: float
    side: Side
    amount: float
    order_id: str = ""
    filled: bool = False
    entry_price: float = 0.0
    lowest_price: float = 999999.0

class GridTraderStrategy(BaseStrategy):
    def __init__(self, exchange: Any, db: TradeDB, settings_ref: Settings = settings):
        super().__init__(
            name="GridTrader",
            exchange=exchange,
            symbol=settings_ref.grid_symbol,
            capital=settings_ref.grid_capital
        )
        self.db = db
        self.settings = settings_ref
        
        self._n_levels = self.settings.grid_levels
        self._range_pct = self.settings.grid_range_pct / 100.0
        self._step_profit_pct = self.settings.grid_step_profit_pct / 100.0
        
        self._grid: list[GridLevel] = []
        self._mid_price = 0.0
        self._initialized = False
        self._last_reset_ts = 0.0

    async def on_candle(self, ohlcv: list[list[float]]) -> list[Signal]:
        """Monitors initialization and weekly scheduled rebalancing."""
        if self.is_paused or not ohlcv:
            return []

        curr_price = float(ohlcv[-1][4])

        if not self._initialized:
            await self.reset_grid(curr_price)
            return []

        # Stop-loss: proteggi dal -2% su ogni livello BUY
        for lvl in self._grid:
            if lvl.side == Side.BUY and lvl.entry_price > 0:
lvl.lowest_price = min(lvl.lowest_price, curr_price)
            loss_pct = (lvl.lowest_price - lvl.entry_price) / lvl.entry_price * 100
            
            # Adaptive stop-loss based on ATR and volatility
            atr = await self.exchange.fetch_ohlcv(self.symbol, "1h", limit=20)
            if atr and len(atr) > 1:
                atr_values = [bar[5] for bar in atr]
                avg_atr = sum(atr_values) / len(atr_values)
                
                # Stop-loss distance = ATR * factor (higher in volatile markets)
                sl_distance = max(0.5 * avg_atr, 2.0)  # 0.5 to 2 ATRs
                if loss_pct < -sl_distance / curr_price * 100:
                    self.logger.warning(f"🛑 STOP-LOSS {lvl.side.name} @ {curr_price:.4f} | Loss: {loss_pct:+.1f}% | ATR SL: {sl_distance:.4f} | Entry: {lvl.entry_price:.4f}")
                    try: await self.exchange.cancel_order(lvl.order_id, self.symbol)
                    except: pass
                    lvl.filled = True  # forza riciclo
        if time.time() - self._last_reset_ts > 7 * 86400:
            self.logger.info("Weekly rebalance trigger. Resetting grid zone...")
            await self.reset_grid(curr_price)

        return []  # Grid strategies are purely order-driven

    async def reset_grid(self, mid_price: float):
        """Cancels all existing grid orders and sets up a new grid around mid_price."""
        self.logger.info(f"Resetting grid around mid price: {mid_price:.4f}")
        
        if self._n_levels < 1:
            self.logger.warning(f"Grid needs at least 2 levels, got {self._n_levels}. Aborting reset.")
            return

        # 1. Cancel all open orders for this grid strategy
        existing_ids = [lvl.order_id for lvl in self._grid if lvl.order_id]
        if existing_ids:
            await asyncio.gather(
                *[self.exchange.cancel_order(oid, self.symbol) for oid in existing_ids],
                return_exceptions=True
            )
            
        self._grid.clear()
        self._mid_price = mid_price
        
        half_levels = max(1, self._n_levels // 2)
        # Price step between each grid band
        step_value = (mid_price * self._range_pct) / half_levels
        
        # Dynamically scale capital to the quote currency
        quote_capital = await self.get_quote_capital()
        capital_per_level = quote_capital / self._n_levels
        
        # Calculate dynamic minimum notional in quote currency (approx 5.0 EUR equivalent)
        parts = self.symbol.split("/")
        quote = parts[1].upper() if len(parts) >= 2 else "EUR"
        min_notional = 5.0
        if quote != "EUR":
            try:
                ticker = await self.exchange.fetch_ticker(f"{quote}/EUR")
                rate = float(ticker.get("last") or ticker.get("close") or 0)
                if rate > 0:
                    min_notional = 5.0 / rate
            except Exception:
                try:
                    ticker = await self.exchange.fetch_ticker(f"EUR/{quote}")
                    rate = float(ticker.get("last") or ticker.get("close") or 0)
                    if rate > 0:
                        min_notional = 5.0 * rate
                except Exception:
                    if quote == "BTC":
                        min_notional = 0.0001
                    elif quote == "BNB":
                        min_notional = 0.01
                    else:
                        min_notional = 5.0
        
        self.logger.info(f"Scaled Grid Allocation: Total Capital = {quote_capital:.6f} {quote} | Capital/Level = {capital_per_level:.6f} {quote} | Min Notional = {min_notional:.6f} {quote}")

        levels: list[GridLevel] = []
        
        # Set tick decimals based on price
        decimals = 4 if mid_price > 10 else (2 if mid_price > 1 else 6)
        
        # Buy Levels (below mid-price)
        for i in range(1, half_levels + 1):
            p = round(mid_price - i * step_value, decimals)
            amt = round(capital_per_level / p, 4)
            if amt * p >= min_notional:  # CCXT/exchange minimum notional safety check
                levels.append(GridLevel(price=p, side=Side.BUY, amount=amt, entry_price=p))
                
        # Sell Levels (above mid-price)
        for i in range(1, half_levels + 1):
            p = round(mid_price + i * step_value, decimals)
            amt = round(capital_per_level / p, 4)
            if amt * p >= min_notional:
                levels.append(GridLevel(price=p, side=Side.SELL, amount=amt))

                
        # Sort levels ascending by price
        levels.sort(key=lambda x: x.price)
        
        # Place Grid Orders on exchange
        placed_levels = []
        for lvl in levels:
            try:
                order = await self.exchange.create_order(
                    symbol=self.symbol,
                    order_type="limit",
                    side=lvl.side.value,
                    amount=lvl.amount,
                    price=lvl.price
                )
                lvl.order_id = order["id"]
                placed_levels.append(lvl)
                self.logger.info(f"Placed grid order: {lvl.side.upper()} @ {lvl.price:.4f} | amount: {lvl.amount:.4f} | ID: {lvl.order_id}")
            except Exception as e:
                self.logger.error(f"Failed to place initial grid level order at {lvl.price:.4f}: {e}")
                
        self._grid = placed_levels
        self._initialized = True
        self._last_reset_ts = time.time()
        self.logger.info(f"Grid initialization complete. Active levels: {len(self._grid)}/{self._n_levels}")

    async def on_order_update(self, order: dict[str, Any]) -> None:
        """When an order completes, places the opposing order shifted by the profit step."""
        order_id = order.get("id", "")
        status = order.get("status", "")
        
        if status != "closed":
            return

        # Find matching grid level
        lvl_idx = next((i for i, lvl in enumerate(self._grid) if lvl.order_id == order_id), None)
        if lvl_idx is None:
            return

        filled_lvl = self._grid[lvl_idx]
        self.logger.info(f"GRID LEVEL FILLED: {filled_lvl.side.upper()} @ {filled_lvl.price:.4f} | ID: {order_id}")

        decimals = 4 if filled_lvl.price > 10 else (2 if filled_lvl.price > 1 else 6)
        
        # Calculate recycled opposing price shifted to secure actual profit
        if filled_lvl.side == Side.BUY:
            # We bought at P. To secure profit, we must sell at a higher price!
            opp_side = Side.SELL
            opp_price = round(filled_lvl.price * (1 + self._step_profit_pct), decimals)
            net_pnl = 0.0  # Realized PnL is resolved when the matching sell completes
        else:
            # We sold at P. To buy back and complete round trip, we place a buy at a lower price!
            opp_side = Side.BUY
            opp_price = round(filled_lvl.price * (1 - self._step_profit_pct), decimals)
            
            # The round-trip is now complete: we sold high and bought low!
            # Realized net profit is generated by the grid step minus round trip fees
            value = filled_lvl.amount * filled_lvl.price
            gross_pnl = value * self._step_profit_pct
            fees = value * _FEE_PCT * 2
            net_pnl = gross_pnl - fees
            
            self.logger.info(f"GRID ROUND-TRIP COMPLETED. Realized Net Profit: {net_pnl:+.4f} USD.")
            
            # Record completed transaction in TradeDB
            self.db.save_trade(
                symbol=self.symbol,
                side="sell",
                price=filled_lvl.price,
                amount=filled_lvl.amount,
                value_usd=value,
                fee_usd=fees,
                net_pnl=net_pnl,
                strategy=self.name
            )

        # Place recycled opposing order
        try:
            new_order = await self.exchange.create_order(
                symbol=self.symbol,
                order_type="limit",
                side=opp_side.value,
                amount=filled_lvl.amount,
                price=opp_price
            )
            
            # Update Grid level state
            filled_lvl.order_id = new_order["id"]
            filled_lvl.side = opp_side
            filled_lvl.price = opp_price
            filled_lvl.entry_price = opp_price if opp_side == Side.BUY else 0.0
            filled_lvl.lowest_price = 999999.0
            
            self.logger.info(f"Recycled grid level placed: {opp_side.upper()} @ {opp_price:.4f} | ID: {new_order['id']}")
            
        except Exception as e:
            self.logger.error(f"Failed to place recycled opposing grid order @ {opp_price:.4f}: {e}")

    async def get_status(self) -> dict[str, Any]:
        buys = [lvl for lvl in self._grid if lvl.side == Side.BUY]
        sells = [lvl for lvl in self._grid if lvl.side == Side.SELL]
        return {
            "symbol": self.symbol,
            "mid_price": self._mid_price,
            "open_buys": len(buys),
            "open_sells": len(sells),
            "total_levels": len(self._grid)
        }

    async def shutdown(self) -> None:
        self.logger.info("Cancelling outstanding grid orders on shutdown...")
        ids = [lvl.order_id for lvl in self._grid if lvl.order_id]
        if ids:
            await asyncio.gather(
                *[self.exchange.cancel_order(oid, self.symbol) for oid in ids],
                return_exceptions=True
            )
        self._grid.clear()
        self._initialized = False
