"""denaro-antigravity strategies/scalper.py – Technical Scalping Strategy.

Uses EMA fast/slow cross + RSI + ATR to enter trades.
Implements secure LOCAL Stop Loss triggers to prevent exchange double-allocation / locking errors.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import numpy as np
import pandas as pd
from loguru import logger

from core.engine import Settings, TradeDB, settings
from strategies.base import BaseStrategy, Position, Side, Signal

_FEE_PCT = 0.00075  # 0.075% BNB discounted fee per side
_MIN_PROFIT_PCT = 2 * _FEE_PCT + 0.001  # Round trip fees + 0.1% cushion

# ── Tech Indicator Helpers ───────────────────────────────────────────────────
def calculate_ema(prices: pd.Series, period: int) -> pd.Series:
    return prices.ewm(span=period, adjust=False).mean()

def calculate_rsi(prices: pd.Series, period: int) -> pd.Series:
    delta = prices.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=period - 1, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.finfo(float).tiny)
    return 100 - (100 / (1 + rs))

def calculate_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs()
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()

# ── Scalper Strategy ──────────────────────────────────────────────────────────
class ScalperStrategy(BaseStrategy):
    def __init__(self, exchange: Any, db: TradeDB, settings_ref: Settings = settings):
        super().__init__(
            name="Scalper",
            exchange=exchange,
            symbol=settings_ref.scalper_symbol,
            capital=settings_ref.scalper_capital
        )
        self.db = db
        self.settings = settings_ref
        
        self._ema_fast_period = self.settings.scalper_ema_fast
        self._ema_slow_period = self.settings.scalper_ema_slow
        self._rsi_period = self.settings.scalper_rsi_period
        self._rsi_buy = self.settings.scalper_rsi_buy
        self._rsi_sell = self.settings.scalper_rsi_sell
        
        self._tp_atr_mult = 1.5
        self._sl_atr_mult = 1.0
        self._atr_window = 14
        self._atr_history = []  # Store ATR values for volatility regime detection

    async def on_candle(self, ohlcv: list[list[float]]) -> list[Signal]:
        if self.is_paused or not ohlcv:
            return []

        # Convert to Pandas DataFrame for vector calculations
        df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
        close = df["close"]
        high = df["high"]
        low = df["low"]

        if len(close) < max(self._ema_slow_period, 20) + 2:
            self.logger.debug("Not enough history candles yet.")
            return []

        # Calculate indicators
        ema_fast = calculate_ema(close, self._ema_fast_period)
        ema_slow = calculate_ema(close, self._ema_slow_period)
        rsi = calculate_rsi(close, self._rsi_period)
        atr = calculate_atr(high, low, close, 14)
        
        # Update ATR history for volatility adaptation
        self._atr_history.append(curr_atr)
        if len(self._atr_history) > 50:  # Keep last 50 ATR values
            self._atr_history.pop(0)
        
        # Adaptive ATR multipliers based on volatility regime
        avg_atr = sum(self._atr_history) / len(self._atr_history) if self._atr_history else curr_atr
        volatility_ratio = curr_atr / avg_atr if avg_atr > 0 else 1.0
        
        # Tighter stops in high volatility, wider in low volatility
        adaptive_sl_mult = max(0.8, min(1.5, self._sl_atr_mult / volatility_ratio))
        adaptive_tp_mult = max(1.2, min(2.5, self._tp_atr_mult * volatility_ratio))

        prev_fast, curr_fast = ema_fast.iloc[-2], ema_fast.iloc[-1]
        prev_slow, curr_slow = ema_slow.iloc[-2], ema_slow.iloc[-1]
        curr_rsi = rsi.iloc[-1]
        curr_price = close.iloc[-1]
        curr_atr = atr.iloc[-1]

        # Monitor active positions for stop-loss triggers locally
        await self._check_local_stop_loss(curr_price)

        # Trigger Entry signals
        crossed_up = prev_fast <= prev_slow and curr_fast > curr_slow
        signals: list[Signal] = []

        if crossed_up and curr_rsi < self._rsi_buy and not self.has_open_position:
            tp = curr_price + adaptive_tp_mult * curr_atr
            sl = curr_price - adaptive_sl_mult * curr_atr
            
            # Ensure price details respect standard tick size / rounding
            decimals = 4 if curr_price > 10 else (2 if curr_price > 1 else 6)
            tp = round(tp, decimals)
            sl = round(sl, decimals)

            # Check fee break-even
            if (tp - curr_price) / curr_price < _MIN_PROFIT_PCT:
                self.logger.debug("TP target too small after fees. Skipping trade.")
                return []

            amount = await self._calculate_position_size(curr_price, sl)
            if amount <= 0:
                return []

            signals.append(
                Signal(
                    side=Side.BUY,
                    symbol=self.symbol,
                    amount=amount,
                    price=curr_price,
                    tp_price=tp,
                    sl_price=sl,
                    reason=f"EMA Cross-Up | RSI={curr_rsi:.1f} ATR={curr_atr:.4f}"
                )
            )

        return signals

    async def _calculate_position_size(self, entry: float, sl: float) -> float:
        # Scale capital to quote asset dynamically
        quote_capital = await self.get_quote_capital()
        
        # Sizing precision and limits from CCXT
        precision, min_amount, min_cost = self.exchange.get_market_precision_and_limits(self.symbol)
        
        # Risk 2% of allocated capital per trade
        risk_amount = quote_capital * 0.02
        risk_per_unit = entry - sl
        if risk_per_unit <= 0:
            return 0.0
            
        size = risk_amount / risk_per_unit
        
        # Minimum cost safety check in quote currency
        # If the size is too small for exchange rules, we fall back to a minimal viable size
        min_size_by_cost = min_cost / entry
        
        if size < min_size_by_cost:
            # For small accounts, we trade the minimum notional size plus a tiny cushion
            size = min_size_by_cost * 1.05
            self.logger.debug(f"Position size adjusted to minimum viable notional size: {size:.6f}")
            
        # Max position sizing: never allocate more than 40% of total capital to a single trade
        max_size = (quote_capital * 0.40) / entry
        final_size = min(size, max_size)
        
        # If the final size is still below minimum amount, enforce minimum amount
        if final_size < min_amount:
            final_size = min_amount
            
        # If final size exceeds max allowed size (or our current free balance), we scale it down
        try:
            bal = await self.exchange.fetch_balance()
            parts = self.symbol.split("/")
            quote = parts[1].upper() if len(parts) >= 2 else "EUR"
            free_bal = float(bal["free"].get(quote, 0.0))
            
            if final_size * entry > free_bal * 0.95:
                # We scale down to 95% of our free balance to allow a fee buffer
                final_size = (free_bal * 0.95) / entry
                self.logger.info(f"Scaled size down to free balance: {final_size:.6f} {parts[0]}")
        except Exception as e:
            self.logger.warning(f"Could not check actual balance during sizing: {e}")
            
        # Settle decimals robustly using exchange precision
        rounded = round(final_size, precision)
        if rounded < min_amount:
            # If rounding down went below min_amount, round up instead
            rounded = round(final_size + 10**(-precision), precision)
            
        # Final notional check
        if rounded * entry < min_cost:
            self.logger.warning(f"Calculated size {rounded:.6f} has notional {rounded * entry:.6f} < min cost {min_cost:.6f}. Cannot trade.")
            return 0.0
            
        return rounded



    async def _check_local_stop_loss(self, current_price: float):
        """Monitors positions and triggers stop loss locally to prevent fund lock errors."""
        for oid, pos in list(self._positions.items()):
            # Only trigger SL if position is filled and exits are active
            if pos.sl_price and pos.tp_order_id and current_price <= pos.sl_price:
                self.logger.warning(f"LOCAL STOP LOSS TRIGGERED for {pos.symbol} @ {current_price:.4f} <= SL {pos.sl_price:.4f} (Entry: {pos.entry_price:.4f})")
                
                # 1. Cancel the outstanding TP limit order on exchange
                try:
                    await self.exchange.cancel_order(pos.tp_order_id, pos.symbol)
                except Exception as e:
                    self.logger.error(f"Failed to cancel TP limit order {pos.tp_order_id} during SL trigger: {e}")

                # 2. Place an immediate market order to exit the trade
                try:
                    exit_order = await self.exchange.create_order(
                        symbol=pos.symbol,
                        order_type="market",
                        side="sell",
                        amount=pos.amount
                    )
                    
                    # Calculate realized PnL
                    actual_exit_price = exit_order["price"] if not self.exchange.dry_run else current_price
                    gross_pnl = (actual_exit_price - pos.entry_price) * pos.amount
                    fees = (pos.entry_price + actual_exit_price) * pos.amount * _FEE_PCT
                    net_pnl = gross_pnl - fees
                    
                    self.logger.critical(f"LOCAL STOP LOSS EXECUTED. Realized Net PnL: {net_pnl:+.4f} USD.")
                    
                    # Save to DB
                    self.db.save_trade(
                        symbol=pos.symbol,
                        side="sell",
                        price=actual_exit_price,
                        amount=pos.amount,
                        value_usd=pos.amount * actual_exit_price,
                        fee_usd=fees,
                        net_pnl=net_pnl,
                        strategy=self.name
                    )
                    
                    # Clean up positions registry
                    del self._positions[oid]
                    
                except Exception as e:
                    self.logger.critical(f"FATAL: Local Stop Loss execution failed! position is orphaned! Error: {e}")

    async def on_order_update(self, order: dict[str, Any]) -> None:
        oid = order.get("id", "")
        status = order.get("status", "")
        filled = float(order.get("filled", 0.0) or 0.0)
        remaining = float(order.get("remaining", 0.0) or 0.0)
        
        if status not in ("closed", "open"):
            return

        # Handle partial fills for open orders
        if status == "open" and filled > 0 and oid in self._positions:
            pos = self._positions[oid]
            if filled < pos.amount:
                self.logger.info(f"Partial fill detected for {oid}: {filled}/{pos.amount}")
                pos.partially_filled = filled

        if status != "closed":
            return

        # Case 1: Entry Order Filled -> Place Take Profit Limit Order on Exchange
        if oid in self._positions:
            pos = self._positions[oid]
            actual_amount = filled if filled > 0 else pos.amount
            if actual_amount != pos.amount:
                pos.amount = actual_amount
                self.logger.info(f"Adjusting position size to filled amount: {actual_amount}")
            
            if not pos.tp_order_id:
                try:
                    tp_order = await self.exchange.create_order(
                        symbol=pos.symbol,
                        order_type="limit",
                        side="sell",
                        amount=pos.amount,
                        price=pos.tp_price
                    )
                    pos.tp_order_id = tp_order["id"]
                    self.logger.info(f"Entry filled @ {pos.entry_price:.4f}. Active TP order placed on exchange: {tp_order['id']} @ {pos.tp_price:.4f}")
                except Exception as e:
                    self.logger.critical(f"Failed to place TP limit order after entry fill: {e}. Position is currently naked!")
            return

        # Case 2: TP Order Filled -> Process profit trade completion
        for entry_oid, pos in list(self._positions.items()):
            if oid == pos.tp_order_id:
                exec_price = order.get("price", pos.tp_price) or pos.tp_price
                actual_amount = filled if filled > 0 else pos.amount
                gross_pnl = (exec_price - pos.entry_price) * actual_amount
                fees = (pos.entry_price + exec_price) * actual_amount * _FEE_PCT
                net_pnl = gross_pnl - fees
                
                self.logger.info(f"TAKE PROFIT FILLED for {pos.symbol} @ {exec_price:.4f}. Net PnL: {net_pnl:+.4f} USD.")
                
                # Save trade
                self.db.save_trade(
                    symbol=pos.symbol,
                    side="sell",
                    price=exec_price,
                    amount=actual_amount,
                    value_usd=actual_amount * exec_price,
                    fee_usd=fees,
                    net_pnl=net_pnl,
                    strategy=self.name
                )
                
                # Clean registry
                del self._positions[entry_oid]
                return

    async def shutdown(self) -> None:
        self.logger.info("Graceful shutdown initiated. Cancelling active orders...")
        for oid, pos in list(self._positions.items()):
            # Cancel entry limit order if still open
            if not pos.tp_order_id:
                try:
                    await self.exchange.cancel_order(oid, pos.symbol)
                except Exception:
                    pass
            # Cancel TP limit order and liquidate position immediately
            else:
                try:
                    await self.exchange.cancel_order(pos.tp_order_id, pos.symbol)
                except Exception:
                    pass
                try:
                    await self.exchange.create_order(
                        symbol=pos.symbol,
                        order_type="market",
                        side="sell",
                        amount=pos.amount
                    )
                except Exception:
                    pass
        self._positions.clear()
