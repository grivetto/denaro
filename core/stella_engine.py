"""
StellaCoreEngine — WebSocket live trading engine per Denaro
Sostituisce il polling REST con WebSocket (ccxt.pro) + RSI vettorizzato NumPy
"""
import asyncio
import numpy as np
import logging
from typing import Optional
import ccxt.pro as ccxtpro

log = logging.getLogger("stella")

class StellaCoreEngine:
    """Motore di trading ottimizzato con WebSocket e calcoli vettorizzati"""

    def __init__(self, symbol: str = "SOL/USDC", rsi_period: int = 14):
        self.symbol = symbol
        self.rsi_period = rsi_period
        self._exchange: Optional[ccxtpro.binance] = None
        self._candles: list[list[float]] = []
        self._running = False
        self._callbacks: list[callable] = []

    @property
    def exchange(self):
        if self._exchange is None:
            self._exchange = ccxtpro.binance({
                "enableRateLimit": True,
                "options": {"defaultType": "spot"},
                "newUpdates": True,  # Solo aggiornamenti reali, no duplicati
            })
        return self._exchange

    def on_ticker(self, callback):
        """Registra un callback chiamato a ogni tick con (price, rsi)"""
        self._callbacks.append(callback)

    async def bootstrap(self, api_key: str = None, api_secret: str = None):
        """Pre-carica lo storico e autentica"""
        if api_key:
            self.exchange.apiKey = api_key
            self.exchange.secret = api_secret
        
        log.info(f"[Stella] Bootstrap {self.symbol}...")
        import ccxt.async_support as ccxt_async
        rest = ccxt_async.binance()
        try:
            ohlcv = await rest.fetch_ohlcv(self.symbol, "1m", limit=100)
            self._candles = [[float(x) for x in c] for c in ohlcv]
            log.info(f"[Stella] Cache: {len(self._candles)} candele")
        finally:
            await rest.close()

    def compute_rsi(self) -> float:
        """RSI vettorizzato NumPy — O(1) senza loop Python"""
        if len(self._candles) < self.rsi_period + 1:
            return 50.0
        closes = np.array([c[4] for c in self._candles[-self.rsi_period-1:]])
        deltas = np.diff(closes)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        avg_gain = np.mean(gains)
        avg_loss = np.mean(losses)
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def latest_price(self) -> float:
        if self._candles:
            return self._candles[-1][4]
        return 0.0

    async def update_candle(self, price: float):
        """Aggiorna in-place l'ultima candela — zero allocazioni"""
        if not self._candles:
            return
        self._candles[-1][4] = price

    async def run_live_loop(self):
        """Loop principale via WebSocket (NO polling REST)"""
        self._running = True
        log.info(f"[Stella] Loop WebSocket avviato su {self.symbol}")

        while self._running:
            try:
                # WebSocket — ZERO polling, latenza < 50ms
                ticker = await self.exchange.watch_ticker(self.symbol)
                price = ticker.get("last", 0)
                if not price:
                    continue

                await self.update_candle(price)
                rsi = self.compute_rsi()

                # Notifica i callback (es. grid strategy)
                for cb in self._callbacks:
                    try:
                        cb(price, rsi)
                    except Exception:
                        pass

            except Exception as e:
                log.error(f"[Stella] WebSocket error: {e}")
                await asyncio.sleep(1)

    async def stop(self):
        self._running = False
        if self._exchange:
            await self._exchange.close()
            self._exchange = None
        log.info("[Stella] Engine fermato")
