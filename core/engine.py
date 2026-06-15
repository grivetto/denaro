"""denaro-antigravity core/engine.py – Core trading engine.

Manages DB operations, configurations, risk, and robust CCXT exchange wrappers.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import ccxt.async_support as ccxt
from dotenv import load_dotenv
from loguru import logger

BASE = Path(__file__).resolve().parents[1]
load_dotenv(BASE / ".env")

# ── Typed Settings ────────────────────────────────────────────────────────────
def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()

def _float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except ValueError:
        return default

def _int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except ValueError:
        return default

def _bool(key: str, default: bool = True) -> bool:
    v = os.getenv(key, "true" if default else "false").strip().lower()
    return v in ("1", "true", "yes", "on")

@dataclass
class Settings:
    binance_api_key: str = field(default_factory=lambda: _env("BINANCE_API_KEY"))
    binance_api_secret: str = field(default_factory=lambda: _env("BINANCE_API_SECRET"))
    
    cryptocom_api_key: str = field(default_factory=lambda: _env("CRYPTOCOM_API_KEY"))
    cryptocom_api_secret: str = field(default_factory=lambda: _env("CRYPTOCOM_API_SECRET"))
    
    telegram_bot_token: str = field(default_factory=lambda: _env("TELEGRAM_BOT_TOKEN"))
    telegram_chat_id: str = field(default_factory=lambda: _env("TELEGRAM_CHAT_ID"))
    
    total_capital_eur: float = field(default_factory=lambda: _float("TOTAL_CAPITAL_EUR", 49.0))
    capital_split_scalper: float = field(default_factory=lambda: _float("CAPITAL_SPLIT_SCALPER", 0.70))
    
    active_strategies: list[str] = field(
        default_factory=lambda: [s.strip() for s in _env("ACTIVE_STRATEGIES", "scalper,grid").split(",") if s.strip()]
    )
    
    scalper_exchange: str = field(default_factory=lambda: _env("SCALPER_EXCHANGE", "binance"))
    grid_exchange: str = field(default_factory=lambda: _env("GRID_EXCHANGE", "binance"))
    
    scalper_symbol: str = field(default_factory=lambda: _env("SCALPER_SYMBOL", "BTC/USDT"))
    scalper_ema_fast: int = field(default_factory=lambda: _int("SCALPER_EMA_FAST", 8))
    scalper_ema_slow: int = field(default_factory=lambda: _int("SCALPER_EMA_SLOW", 21))
    scalper_rsi_period: int = field(default_factory=lambda: _int("SCALPER_RSI_PERIOD", 7))
    scalper_rsi_buy: float = field(default_factory=lambda: _float("SCALPER_RSI_BUY", 40.0))
    scalper_rsi_sell: float = field(default_factory=lambda: _float("SCALPER_RSI_SELL", 60.0))
    
    grid_symbol: str = field(default_factory=lambda: _env("GRID_SYMBOL", "SOL/USDT"))
    grid_levels: int = field(default_factory=lambda: _int("GRID_LEVELS", 8))
    grid_range_pct: float = field(default_factory=lambda: _float("GRID_RANGE_PCT", 2.0))
    grid_step_profit_pct: float = field(default_factory=lambda: _float("GRID_STEP_PROFIT_PCT", 0.45))
    
    max_daily_loss_pct: float = field(default_factory=lambda: _float("MAX_DAILY_LOSS_PCT", 5.0))
    max_drawdown_pct: float = field(default_factory=lambda: _float("MAX_DRAWDOWN_PCT", 15.0))
    max_open_positions: int = field(default_factory=lambda: _int("MAX_OPEN_POSITIONS", 3))
    
    dashboard_port: int = field(default_factory=lambda: _int("DASHBOARD_PORT", 8000))
    dashboard_host: str = field(default_factory=lambda: _env("DASHBOARD_HOST", "127.0.0.1"))
    
    telegram_polling: bool = field(default_factory=lambda: _bool("TELEGRAM_POLLING", True))
    dry_run: bool = field(default_factory=lambda: _bool("DRY_RUN", True))
    log_level: str = field(default_factory=lambda: _env("LOG_LEVEL", "INFO"))

    @property
    def telegram_token(self) -> str:
        # Alias to prevent crash in notification scripts checking telegram_token
        return self.telegram_bot_token

    @property
    def scalper_capital(self) -> float:
        return self.total_capital_eur * self.capital_split_scalper

    @property
    def grid_capital(self) -> float:
        return self.total_capital_eur * (1.0 - self.capital_split_scalper)

settings = Settings()

# ── SQLite Database Wrapper ──────────────────────────────────────────────────
    # BTC/USDT Grid Settings
    btc_grid_symbol: str = field(default_factory=lambda: _env("BTC_GRID_SYMBOL", "BTC/USDT"))
    btc_grid_capital_usdt: float = field(default_factory=lambda: _float("BTC_GRID_CAPITAL_USDT", 5.0))
    btc_grid_levels: int = field(default_factory=lambda: _int("BTC_GRID_LEVELS", 3))
    btc_grid_spacing_pct: float = field(default_factory=lambda: _float("BTC_GRID_SPACING_PCT", 0.01))
    btc_grid_take_pct: float = field(default_factory=lambda: _float("BTC_GRID_TAKE_PCT", 0.8))
    btc_grid_trailing_stop: bool = field(default_factory=lambda: _bool("BTC_GRID_TRAILING_STOP", True))

    # ETH/USDT Grid Settings
    eth_grid_symbol: str = field(default_factory=lambda: _env("ETH_GRID_SYMBOL", "ETH/USDT"))
    eth_grid_capital_usdt: float = field(default_factory=lambda: _float("ETH_GRID_CAPITAL_USDT", 3.0))
    eth_grid_levels: int = field(default_factory=lambda: _int("ETH_GRID_LEVELS", 3))
    eth_grid_spacing_pct: float = field(default_factory=lambda: _float("ETH_GRID_SPACING_PCT", 0.015))
    eth_grid_take_pct: float = field(default_factory=lambda: _float("ETH_GRID_TAKE_PCT", 0.9))
    eth_grid_trailing_stop: bool = field(default_factory=lambda: _bool("ETH_GRID_TRAILING_STOP", True))

    # RSI Settings for BTC/ETH
    btc_rsi_symbol: str = field(default_factory=lambda: _env("BTC_RSI_SYMBOL", "BTC/USDT"))
    btc_rsi_capital_usdt: float = field(default_factory=lambda: _float("BTC_RSI_CAPITAL_USDT", 2.0))
    eth_rsi_symbol: str = field(default_factory=lambda: _env("ETH_RSI_SYMBOL", "ETH/USDT"))
    eth_rsi_capital_usdt: float = field(default_factory=lambda: _float("ETH_RSI_CAPITAL_USDT", 1.5))

    # Feature flags for new strategies
    enable_btc_grid: bool = field(default_factory=lambda: _bool("ENABLE_BTC_GRID", True))
    enable_eth_grid: bool = field(default_factory=lambda: _bool("ENABLE_ETH_GRID", True))
    enable_btc_rsi: bool = field(default_factory=lambda: _bool("ENABLE_BTC_RSI", True))
    enable_eth_rsi: bool = field(default_factory=lambda: _bool("ENABLE_ETH_RSI", True))
    enable_dynamic_grid: bool = field(default_factory=lambda: _bool("ENABLE_DYNAMIC_GRID", False))

    # Dynamic Grid Strategy Settings
    dynamic_grid_symbol: str = field(default_factory=lambda: _env("DYNAMIC_GRID_SYMBOL", "BTC/USDT"))
    dynamic_grid_capital_usdt: float = field(default_factory=lambda: _float("DYNAMIC_GRID_CAPITAL_USDT", 10.0))
    dynamic_grid_levels: int = field(default_factory=lambda: _int("DYNAMIC_GRID_LEVELS", 4))
    dynamic_grid_min_spacing_pct: float = field(default_factory=lambda: _float("DYNAMIC_GRID_MIN_SPACING_PCT", 0.0002))
    dynamic_grid_max_spacing_pct: float = field(default_factory=lambda: _float("DYNAMIC_GRID_MAX_SPACING_PCT", 0.005))
    dynamic_grid_take_pct: float = field(default_factory=lambda: _float("DYNAMIC_GRID_TAKE_PCT", 0.4))
    dynamic_grid_trailing_stop: bool = field(default_factory=lambda: _bool("DYNAMIC_GRID_TRAILING_STOP", True))
    dynamic_grid_price_precision: int = field(default_factory=lambda: _int("DYNAMIC_GRID_PRICE_PRECISION", 6))
    dynamic_grid_amount_precision: int = field(default_factory=lambda: _int("DYNAMIC_GRID_AMOUNT_PRECISION", 6))


class TradeDB:
    def __init__(self, db_name: str = "denaro"):
        self.path = BASE / ".tmp" / f"{db_name}.db"
        self.path.parent.mkdir(exist_ok=True)
        self._conn = None
        self._init()

    def _connect(self):
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.path), timeout=10, check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
        return self._conn

    def _init(self):
        c = self._connect()
        c.executescript("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT,
                side TEXT,
                price REAL,
                amount REAL,
                value_usd REAL,
                fee_usd REAL,
                net_pnl REAL,
                strategy TEXT,
                filled_at TEXT DEFAULT (datetime('now', 'localtime'))
            );
            CREATE TABLE IF NOT EXISTS state (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at REAL
            );
        """)
        c.commit()
        
        # Dynamically migrate table columns if older schema is present
        try:
            cursor = c.cursor()
            cursor.execute("PRAGMA table_info(trades)")
            columns = [row[1] for row in cursor.fetchall()]
            
            expected = {
                "value_usd": "REAL",
                "fee_usd": "REAL",
                "net_pnl": "REAL",
                "strategy": "TEXT",
                "filled_at": "TEXT DEFAULT (datetime('now', 'localtime'))"
            }
            for col, col_type in expected.items():
                if col not in columns:
                    cursor.execute(f"ALTER TABLE trades ADD COLUMN {col} {col_type}")
                    c.commit()
                    logger.info(f"Database Migration: Added missing column '{col}' to trades table.")
        except Exception as e:
            logger.error(f"Database Migration failed: {e}")

    def save_trade(self, symbol: str, side: str, price: float, amount: float, value_usd: float, fee_usd: float, net_pnl: float, strategy: str):
        c = self._connect()
        c.execute(
            "INSERT INTO trades (symbol, side, price, amount, value_usd, fee_usd, net_pnl, strategy) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (symbol, side, price, amount, value_usd, fee_usd, net_pnl, strategy)
        )
        c.commit()

    def stats(self, strategy: str | None = None, limit: int = 50) -> dict[str, Any]:
        c = self._connect()
        if strategy:
            query = "SELECT net_pnl FROM trades WHERE strategy = ? AND net_pnl IS NOT NULL ORDER BY id DESC LIMIT ?"
            params = (strategy, limit)
        else:
            query = "SELECT net_pnl FROM trades WHERE net_pnl IS NOT NULL ORDER BY id DESC LIMIT ?"
            params = (limit,)
            
        pnls = [r[0] for r in c.execute(query, params).fetchall()]
        if not pnls:
            return {"count": 0, "avg_pnl": 0.0, "win_rate": 0.0, "total_pnl": 0.0, "wins": 0, "losses": 0}
        
        wins = sum(1 for p in pnls if p > 0)
        return {
            "count": len(pnls),
            "avg_pnl": round(sum(pnls) / len(pnls), 4),
            "win_rate": round(wins / len(pnls) * 100, 1),
            "total_pnl": round(sum(pnls), 4),
            "wins": wins,
            "losses": len(pnls) - wins
        }

    def get_recent_trades(self, limit: int = 20) -> list[dict[str, Any]]:
        c = self._connect()
        rows = c.execute(
            "SELECT symbol, side, price, amount, value_usd, net_pnl, strategy, filled_at FROM trades ORDER BY id DESC LIMIT ?",
            (limit,)
        ).fetchall()
        return [
            {
                "symbol": r[0],
                "side": r[1],
                "price": r[2],
                "amount": r[3],
                "value_usd": r[4],
                "net_pnl": r[5],
                "strategy": r[6],
                "filled_at": r[7]
            }
            for r in rows
        ]

    def daily_pnl(self, strategy: str | None = None) -> float:
        c = self._connect()
        if strategy:
            row = c.execute(
                "SELECT SUM(net_pnl) FROM trades WHERE date(filled_at) = date('now', 'localtime') AND strategy = ?",
                (strategy,)
            ).fetchone()
        else:
            row = c.execute(
                "SELECT SUM(net_pnl) FROM trades WHERE date(filled_at) = date('now', 'localtime')"
            ).fetchone()
        return round(row[0] or 0.0, 4)

    def set_state(self, key: str, value: Any):
        c = self._connect()
        c.execute(
            "INSERT OR REPLACE INTO state (key, value, updated_at) VALUES (?, ?, ?)",
            (key, json.dumps(value), time.time())
        )
        c.commit()

    def get_state(self, key: str) -> Any | None:
        c = self._connect()
        row = c.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

# ── Centralized Risk Guard ────────────────────────────────────────────────────
class RiskManager:
    def __init__(self, db: TradeDB, settings_ref: Settings = settings):
        self.db = db
        self.settings = settings_ref
        self._is_halted = False
        self._daily_baseline_equity = 0.0
        self._daily_baseline_set_ts = 0.0
        self._realized_pnl_today = 0.0

    def set_daily_baseline(self, equity: float):
        self._daily_baseline_equity = max(equity, 1.0)
        self._daily_baseline_set_ts = time.time()
        self._realized_pnl_today = self.db.daily_pnl()
        self._check_circuit_breaker()
        logger.info(f"RiskManager: Baseline set to {equity:.2f} USD. PnL Today: {self._realized_pnl_today:+.4f} USD")

    def record_trade_pnl(self, pnl: float):
        self._realized_pnl_today += pnl
        self._check_circuit_breaker()

    def _check_circuit_breaker(self):
        if self._daily_baseline_equity <= 0:
            return
        drawdown_pct = (-self._realized_pnl_today / self._daily_baseline_equity) * 100
        limit = self.settings.max_daily_loss_pct
        if drawdown_pct >= limit:
            logger.critical(f"RiskManager: Drawdown limit hit! Drawdown {drawdown_pct:.2f}% >= Limit {limit}%. HALTING FLT!")
            self.halt_all()

    def halt_all(self):
        self._is_halted = True
        logger.critical("RiskManager: Emergency circuit breaker activated. All trading strategies halted.")

    def resume_all(self):
        self._is_halted = False
        self._realized_pnl_today = 0.0
        logger.info("RiskManager: Re-activated. System trading resumed.")

    def can_open_position(self, current_open_count: int) -> bool:
        if self._is_halted:
            logger.warning("RiskManager blocked execution: System is currently halted.")
            return False
        if current_open_count >= self.settings.max_open_positions:
            logger.warning(f"RiskManager blocked execution: Active positions ({current_open_count}) >= limit ({self.settings.max_open_positions}).")
            return False
        return True

    @property
    def is_halted(self) -> bool:
        return self._is_halted

    @property
    def daily_pnl(self) -> float:
        return self._realized_pnl_today

# ── Robust Async Exchange Wrapper (CCXT / DRY_RUN) ───────────────────────────
class ExchangeWrapper:
    _RETRY_DELAYS = (1, 2, 5)

    def __init__(self, name: str, api_key: str, api_secret: str, dry_run: bool = True):
        self.name = name
        self.dry_run = dry_run
        self._api_key = api_key
        self._api_secret = api_secret
        self._client: ccxt.Exchange | None = None
        
        # Dry Run Mock State
        self._mock_balances: dict[str, float] = {"USDT": 49.0, "EUR": 49.0, "BTC": 0.0, "SOL": 0.0, "BNB": 0.05}
        self._mock_orders: dict[str, dict] = {}
        
        # Public client for dry-run market data (cached)
        self._public_client: ccxt.Exchange | None = None
        
    async def connect(self):
        if self.dry_run:
            logger.info(f"Exchange [{self.name}]: Connected in DRY_RUN mode.")
            return
            
        options = {
            "apiKey": self._api_key,
            "secret": self._api_secret,
            "enableRateLimit": True,
        }
        
        if self.name.lower() == "binance":
            self._client = ccxt.binance(options)
        elif self.name.lower() in ("cryptocom", "crypto.com"):
            self._client = ccxt.cryptocom(options)
        else:
            raise ValueError(f"Unsupported exchange: {self.name}")
            
        await self._client.load_markets()
        logger.info(f"Exchange [{self.name}]: Real connection initialized.")

    async def reconnect(self, max_attempts: int = 5) -> bool:
        """Attempt to reconnect with exponential backoff."""
        for attempt in range(1, max_attempts + 1):
            try:
                delay = min(2 ** attempt, 30)  # Exponential backoff capped at 30s
                logger.warning(f"Exchange [{self.name}]: Reconnect attempt {attempt}/{max_attempts} in {delay}s...")
                await asyncio.sleep(delay)
                
                if self._client:
                    try:
                        await self._client.close()
                    except Exception:
                        pass
                    self._client = None
                
                await self.connect()
                logger.info(f"Exchange [{self.name}]: Reconnected successfully on attempt {attempt}.")
                return True
            except Exception as e:
                logger.error(f"Exchange [{self.name}]: Reconnect attempt {attempt} failed: {e}")
        
        logger.critical(f"Exchange [{self.name}]: Failed to reconnect after {max_attempts} attempts.")
        return False

    def _get_public_client(self) -> ccxt.Exchange:
        if self._public_client is None:
            self._public_client = ccxt.binance({"enableRateLimit": True})
        return self._public_client

    async def close(self):
        if self._client:
            await self._client.close()
        if self._public_client:
            await self._public_client.close()
            self._public_client = None

    def get_market_precision_and_limits(self, symbol: str) -> tuple[int, float, float]:
        """Returns (amount_precision, min_amount, min_cost) for a symbol."""
        if self.dry_run or not self._client or symbol not in self._client.markets:
            parts = symbol.split("/")
            base = parts[0].upper() if len(parts) >= 1 else "BTC"
            quote = parts[1].upper() if len(parts) >= 2 else "USDT"
            
            if base in ("BTC", "ETH"):
                precision = 4
            elif base in ("SOL", "BNB"):
                precision = 3
            else:
                precision = 2
                
            min_amount = 0.0001 if base == "BTC" else (0.001 if base == "ETH" else 0.01)
            
            if quote == "BTC":
                min_cost = 0.0001
            elif quote == "BNB":
                min_cost = 0.01
            else:
                min_cost = 5.0
                
            return precision, min_amount, min_cost
            
        market = self._client.market(symbol)
        precision = market.get('precision', {}).get('amount', 4)
        limits = market.get('limits', {})
        min_amount = limits.get('amount', {}).get('min', 0.0001)
        min_cost = limits.get('cost', {}).get('min', 5.0)
        
        if isinstance(precision, float):
            precision = int(-math.log10(precision))
            
        return precision, min_amount, min_cost

    async def _call(self, fn_name: str, *args, **kwargs) -> Any:
        if self.dry_run:
            raise RuntimeError(f"Cannot execute real API call '{fn_name}' in Dry Run mode.")
        
        fn = getattr(self._client, fn_name)
        last_exc = None
        for attempt, delay in enumerate((*self._RETRY_DELAYS, None), start=1):
            try:
                return await fn(*args, **kwargs)
            except (ccxt.NetworkError, ccxt.RequestTimeout) as exc:
                last_exc = exc
                if delay:
                    logger.warning(f"Exchange [{self.name}] {fn_name} failed: {exc}. Retrying in {delay}s...")
                    await asyncio.sleep(delay)
                else:
                    logger.error(f"Exchange [{self.name}] {fn_name} failed after retries: {exc}. Attempting reconnect...")
                    reconnected = await self.reconnect(max_attempts=3)
                    if reconnected:
                        fn = getattr(self._client, fn_name)
                        try:
                            return await fn(*args, **kwargs)
                        except Exception as retry_exc:
                            logger.error(f"Exchange [{self.name}] {fn_name} failed after reconnect: {retry_exc}")
            except ccxt.InsufficientFunds as exc:
                raise RuntimeError(f"Exchange [{self.name}] insufficient funds: {exc}") from exc
            except ccxt.InvalidOrder as exc:
                raise RuntimeError(f"Exchange [{self.name}] invalid order: {exc}") from exc
            except ccxt.RateLimitExceeded as exc:
                raise RuntimeError(f"Exchange [{self.name}] rate limit exceeded: {exc}") from exc
            except ccxt.BaseError as exc:
                raise RuntimeError(f"Exchange [{self.name}] api error: {exc}") from exc
        raise RuntimeError(f"Exchange [{self.name}] network failure: {last_exc}")

    # ── Read Operations ───────────────────────────────────────────────────────
    async def fetch_ohlcv(self, symbol: str, timeframe: str = "1m", limit: int = 200) -> list[list[float]]:
        # In dry run, we still fetch real public market candles
        if self.dry_run:
            try:
                public_client = self._get_public_client()
                data = await public_client.fetch_ohlcv(symbol, timeframe, None, limit)
                return data
            except Exception as e:
                logger.error(f"Dry run public OHLCV fetch failed: {e}")
                # return a dummy series if request fails
                t = int(time.time() * 1000)
                return [[t - i * 60000, 50000.0, 50100.0, 49900.0, 50000.0, 1.0] for i in range(limit)]
        
        return await self._call("fetch_ohlcv", symbol, timeframe, None, limit)

    async def fetch_ticker(self, symbol: str) -> dict[str, Any]:
        if self.dry_run:
            # Dry run public ticker fetch
            tmp_client = ccxt.binance({"enableRateLimit": True})
            try:
                ticker = await tmp_client.fetch_ticker(symbol)
                await tmp_client.close()
                return ticker
            except Exception:
                await tmp_client.close()
                return {"last": 50000.0, "ask": 50010.0, "bid": 49990.0}
        
        return await self._call("fetch_ticker", symbol)

    async def fetch_balance(self) -> dict[str, Any]:
        if self.dry_run:
            # Build CCXT-like balance response
            res = {"free": self._mock_balances.copy(), "total": self._mock_balances.copy()}
            return res
        return await self._call("fetch_balance")

    # ── Write Operations (Dry Run Aware) ──────────────────────────────────────
    async def create_order(self, symbol: str, order_type: str, side: str, amount: float, price: float | None = None, params: dict | None = None) -> dict[str, Any]:
        params = params or {}
        base_asset, quote_asset = symbol.split("/")
        
        if self.dry_run:
            oid = f"MOCK-{int(time.time() * 1000)}"
            exec_price = price if price else (await self.fetch_ticker(symbol))["last"]
            value = amount * exec_price
            
            # Check balance
            if side == "buy":
                if self._mock_balances.get(quote_asset, 0.0) < value:
                    raise RuntimeError(f"Insufficient {quote_asset} balance for dry run buy. Has {self._mock_balances.get(quote_asset, 0.0)}, needs {value}")
            else:
                if self._mock_balances.get(base_asset, 0.0) < amount:
                    raise RuntimeError(f"Insufficient {base_asset} balance for dry run sell. Has {self._mock_balances.get(base_asset, 0.0)}, needs {amount}")
            
            # Place order into mock registry
            self._mock_orders[oid] = {
                "id": oid,
                "symbol": symbol,
                "type": order_type,
                "side": side,
                "amount": amount,
                "price": exec_price,
                "status": "open" if order_type == "limit" else "closed",
                "filled": 0.0 if order_type == "limit" else amount,
                "remaining": amount if order_type == "limit" else 0.0,
                "timestamp": int(time.time() * 1000)
            }
            
            if order_type == "market":
                # Instantly execute market order
                self._execute_mock_trade(oid)
                
            logger.info(f"[DRY RUN] Placed mock {side.upper()} order {oid} for {amount:.6f} {symbol} @ {exec_price:.4f}")
            return self._mock_orders[oid]
            
        return await self._call("create_order", symbol, order_type, side, amount, price, params)

    async def cancel_order(self, order_id: str, symbol: str) -> dict[str, Any]:
        if self.dry_run:
            if order_id not in self._mock_orders:
                raise RuntimeError(f"Order {order_id} not found in dry run registry.")
            order = self._mock_orders[order_id]
            if order["status"] == "open":
                order["status"] = "canceled"
                logger.info(f"[DRY RUN] Canceled mock order {order_id}")
            return {"id": order_id, "status": "canceled"}
            
        return await self._call("cancel_order", order_id, symbol)

    async def fetch_order(self, order_id: str, symbol: str) -> dict[str, Any]:
        if self.dry_run:
            if order_id not in self._mock_orders:
                raise RuntimeError(f"Order {order_id} not found in dry run registry.")
            
            order = self._mock_orders[order_id]
            # Simulate matching limit orders in dry run based on current price
            if order["status"] == "open":
                ticker = await self.fetch_ticker(symbol)
                curr_price = ticker["last"]
                limit_price = order["price"]
                
                if order["side"] == "buy" and curr_price <= limit_price:
                    self._execute_mock_trade(order_id)
                elif order["side"] == "sell" and curr_price >= limit_price:
                    self._execute_mock_trade(order_id)
                    
            return self._mock_orders[order_id]
            
        return await self._call("fetch_order", order_id, symbol)

    def _execute_mock_trade(self, oid: str):
        order = self._mock_orders[oid]
        order["status"] = "closed"
        order["filled"] = order["amount"]
        order["remaining"] = 0.0
        
        base_asset, quote_asset = order["symbol"].split("/")
        value = order["amount"] * order["price"]
        fee_pct = 0.00075  # 0.075% BNB discount simulation
        
        if order["side"] == "buy":
            self._mock_balances[quote_asset] = self._mock_balances.get(quote_asset, 0.0) - value
            self._mock_balances[base_asset] = self._mock_balances.get(base_asset, 0.0) + order["amount"] * (1 - fee_pct)
        else:
            self._mock_balances[base_asset] = self._mock_balances.get(base_asset, 0.0) - order["amount"]
            self._mock_balances[quote_asset] = self._mock_balances.get(quote_asset, 0.0) + value * (1 - fee_pct)
            
        logger.info(f"[DRY RUN] Executed mock order {oid} | {order['side'].upper()} {order['amount']:.6f} {order['symbol']} @ {order['price']:.4f}")

    async def get_total_equity_usdt(self) -> float:
        try:
            bal = await self.fetch_balance()
            total_bal = bal.get("total", {})
            total_usdt = 0.0
            
            for asset, amount in total_bal.items():
                if amount <= 0.0:
                    continue
                if asset in ("USDT", "USD", "USDC", "BUSD"):
                    total_usdt += amount
                    continue
                    
                # Convert asset to USDT
                try:
                    if asset == "EUR":
                        try:
                            ticker = await self.fetch_ticker("EUR/USDT")
                            rate = float(ticker.get("last") or ticker.get("close") or 1.08)
                        except Exception:
                            rate = 1.08
                        total_usdt += amount * rate
                    else:
                        try:
                            ticker = await self.fetch_ticker(f"{asset}/USDT")
                            rate = float(ticker.get("last") or ticker.get("close") or 0.0)
                            total_usdt += amount * rate
                        except Exception:
                            if asset != "BTC":
                                try:
                                    ticker_btc = await self.fetch_ticker(f"{asset}/BTC")
                                    rate_btc = float(ticker_btc.get("last") or ticker_btc.get("close") or 0.0)
                                    ticker_usdt = await self.fetch_ticker("BTC/USDT")
                                    rate_usdt = float(ticker_usdt.get("last") or ticker_usdt.get("close") or 0.0)
                                    total_usdt += amount * rate_btc * rate_usdt
                                except Exception as e:
                                    logger.error(f"Failed to convert {asset} to USDT: {e}")
                            else:
                                logger.error(f"Failed to convert BTC to USDT.")
                except Exception as e:
                    logger.error(f"Equity conversion failed for {asset}: {e}")
            return total_usdt
        except Exception as e:
            logger.error(f"Failed to fetch balance or calculate total equity: {e}")
            return 0.0

