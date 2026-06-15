"""denaro-antigravity strategies/rsi_mean_rev.py – RSI Mean-Reversion Strategy.

Buy when RSI < 30 (oversold), sell when RSI > 70 (overbought).
Uses EMA for trend confirmation to avoid buying in a falling knife.
"""
from __future__ import annotations

import logging
from typing import Any, List

from strategies.base import BaseStrategy, Position, Side, Signal

logger = logging.getLogger(__name__)


class RSIReversionStrategy(BaseStrategy):
    """RSI Mean-Reversion strategy with EMA trend filter."""

    def __init__(self, exchange: Any, symbol: str = "BTC/USDT", capital: float = 100.0):
        super().__init__(name="RSIReversion", exchange=exchange, symbol=symbol, capital=capital)
        self._rsi_period = 14
        self._rsi_buy = 30.0
        self._rsi_sell = 70.0
        self._ema_fast = 8
        self._ema_slow = 21
        self._position: Position | None = None
        self._price_history: list[float] = []
        self._ohlcv_history: list[list[float]] = []

    async def on_candle(self, ohlcv: list[list[float]]) -> list[Signal]:
        if self.is_paused or not ohlcv:
            return []

        self._ohlcv_history.extend(ohlcv)
        if len(self._ohlcv_history) > 100:
            self._ohlcv_history = self._ohlcv_history[-100:]

        # Need enough data
        if len(self._ohlcv_history) < self._rsi_period + 10:
            return []

        closes = [c[4] for c in self._ohlcv_history]
        rsi = self._calculate_rsi(closes)
        ema_fast = self._calculate_ema(closes, self._ema_fast)
        ema_slow = self._calculate_ema(closes, self._ema_slow)

        if rsi is None or ema_fast is None or ema_slow is None:
            return []

        curr_price = closes[-1]
        signals: list[Signal] = []

        # Trend filter: only trade if EMA fast > EMA slow (bullish bias for mean reversion)
        trend_bullish = ema_fast > ema_slow

        # Buy signal: oversold + trend support
        if rsi < self._rsi_buy and trend_bullish:
            if self._position is None or self._position.side != Side.BUY:
                signals.append(Signal(side=Side.BUY, price=curr_price, amount=self._calculate_position_size(curr_price)))
                self._position = Position(side=Side.BUY, entry_price=curr_price, amount=self._calculate_position_size(curr_price))
                logger.info(f"RSI BUY signal: RSI={rsi:.1f} < {self._rsi_buy}, EMA fast={ema_fast:.2f} > slow={ema_slow:.2f}")

        # Sell signal: overbought
        elif rsi > self._rsi_sell:
            if self._position is not None and self._position.side == Side.BUY:
                signals.append(Signal(side=Side.SELL, price=curr_price, amount=self._position.amount))
                self._position = None
                logger.info(f"RSI SELL signal: RSI={rsi:.1f} > {self._rsi_sell}")

        return signals

    def _calculate_rsi(self, prices: list[float]) -> float | None:
        if len(prices) < self._rsi_period + 1:
            return None

        deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
        gains = [max(d, 0) for d in deltas[-self._rsi_period:]]
        losses = [abs(min(d, 0)) for d in deltas[-self._rsi_period:]]

        avg_gain = sum(gains) / len(gains) if gains else 0
        avg_loss = sum(losses) / len(losses) if losses else 0

        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return rsi

    def _calculate_ema(self, prices: list[float], period: int) -> float | None:
        if len(prices) < period:
            return None
        k = 2 / (period + 1)
        ema = prices[0]
        for price in prices[-period:]:
            ema = price * k + ema * (1 - k)
        return ema

    def _calculate_position_size(self, price: float) -> float:
        # Use 10% of capital per trade
        return (self.capital * 0.10) / price

    async def on_order_update(self, order: dict[str, Any]) -> None:
        pass  # RSI strategy is signal-based

    async def get_status(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "symbol": self.symbol,
            "capital": self.capital,
            "position": self._position.side.value if self._position else "none",
        }
