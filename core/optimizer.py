"""denaro-antigravity core/optimizer.py – Autonomous Parameter Optimizer Agent.

Inspired by self-improving trading agents, this script fetches recent historical OHLCV data
and runs an iterative backtest simulation to find the most profitable parameters for the Scalper strategy.
"""
import asyncio
import os
import sys
import time
from pathlib import Path
import pandas as pd
import numpy as np
import ccxt.async_support as ccxt
from loguru import logger
from dotenv import load_dotenv

BASE = Path(__file__).resolve().parents[1]
load_dotenv(BASE / ".env")

# Crossover and indicator helpers (vectorized for speed)
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

class ParameterOptimizer:
    def __init__(self, symbol: str = "BTC/EUR", exchange_name: str = "binance"):
        self.symbol = symbol
        self.exchange_name = exchange_name
        self.ohlcv_df = None

    async def fetch_historical_data(self, limit: int = 1000) -> bool:
        """Fetches public OHLCV candles from the exchange."""
        logger.info(f"Fetching recent {limit} candles (1m) for {self.symbol} on {self.exchange_name}...")
        client = getattr(ccxt, self.exchange_name.lower())({"enableRateLimit": True})
        try:
            raw_candles = await client.fetch_ohlcv(self.symbol, "1m", None, limit)
            await client.close()
            
            if not raw_candles:
                logger.error("No candle data returned from exchange.")
                return False
                
            self.ohlcv_df = pd.DataFrame(raw_candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
            logger.info(f"Successfully loaded {len(self.ohlcv_df)} candles for optimization.")
            return True
        except Exception as e:
            logger.error(f"Failed to fetch historical market data: {e}")
            try:
                await client.close()
            except:
                pass
            return False

    def run_simulation(self, ema_fast: int, ema_slow: int, rsi_period: int, rsi_buy: float, rsi_sell: float) -> dict:
        """Simulates the Scalper Strategy over the fetched OHLCV dataframe."""
        if self.ohlcv_df is None or len(self.ohlcv_df) < 50:
            return {"net_pnl": 0.0, "trades": 0, "win_rate": 0.0}

        df = self.ohlcv_df.copy()
        close = df["close"]
        high = df["high"]
        low = df["low"]

        # Calculate indicators
        fast_ema = calculate_ema(close, ema_fast)
        slow_ema = calculate_ema(close, ema_slow)
        rsi = calculate_rsi(close, rsi_period)
        atr = calculate_atr(high, low, close, 14)

        # Simulation variables
        capital = 100.0  # Base simulation capital (100 USD/EUR)
        fee_pct = 0.00075
        position = None  # None or dict: {"entry": x, "tp": y, "sl": z, "amount": w}
        trades_count = 0
        wins = 0
        losses = 0
        net_pnl = 0.0

        for i in range(30, len(df)):
            curr_price = close.iloc[i]
            curr_high = high.iloc[i]
            curr_low = low.iloc[i]
            curr_atr = atr.iloc[i]

            # 1. Check stop-loss / take-profit if holding position
            if position:
                # Check stop loss (low price hits or crosses below SL)
                if curr_low <= position["sl"]:
                    # Trigger Stop Loss
                    gross = (position["sl"] - position["entry"]) * position["amount"]
                    fees = (position["entry"] + position["sl"]) * position["amount"] * fee_pct
                    trade_pnl = gross - fees
                    net_pnl += trade_pnl
                    capital += trade_pnl
                    losses += 1
                    trades_count += 1
                    position = None
                    continue

                # Check take profit (high price hits or crosses above TP)
                elif curr_high >= position["tp"]:
                    # Trigger Take Profit
                    gross = (position["tp"] - position["entry"]) * position["amount"]
                    fees = (position["entry"] + position["tp"]) * position["amount"] * fee_pct
                    trade_pnl = gross - fees
                    net_pnl += trade_pnl
                    capital += trade_pnl
                    wins += 1
                    trades_count += 1
                    position = None
                    continue

            # 2. Check entry signal if not in position
            else:
                prev_fast, curr_fast = fast_ema.iloc[i - 1], fast_ema.iloc[i]
                prev_slow, curr_slow = slow_ema.iloc[i - 1], slow_ema.iloc[i]
                curr_rsi = rsi.iloc[i]

                crossed_up = prev_fast <= prev_slow and curr_fast > curr_slow
                
                if crossed_up and curr_rsi < rsi_buy:
                    tp = curr_price + 1.5 * curr_atr
                    sl = curr_price - 1.0 * curr_atr
                    
                    # Risk 2% of simulated capital
                    risk_amount = capital * 0.02
                    risk_per_unit = curr_price - sl
                    if risk_per_unit > 0:
                        amount = risk_amount / risk_per_unit
                        # Enforce max sizing (30%)
                        max_amount = (capital * 0.30) / curr_price
                        final_amount = min(amount, max_amount)
                        
                        position = {
                            "entry": curr_price,
                            "tp": tp,
                            "sl": sl,
                            "amount": final_amount
                        }

        win_rate = (wins / trades_count * 100) if trades_count > 0 else 0.0
        return {
            "net_pnl": round(net_pnl, 4),
            "trades": trades_count,
            "win_rate": round(win_rate, 1),
            "wins": wins,
            "losses": losses
        }

    def optimize(self) -> dict:
        """Runs parameter sweep search to locate optimal configuration."""
        # Parameter search space (focused grids around current settings to conserve time)
        ema_fast_range = [7, 8, 10]
        ema_slow_range = [18, 21, 25]
        rsi_period_range = [6, 7, 9]
        rsi_buy_range = [38.0, 40.0, 42.0]
        rsi_sell_range = [60.0]  # Kept static to limit iterations

        best_pnl = -999.0
        best_cfg = {}

        logger.info("Starting coordinate parameter grid optimization...")
        
        # Current defaults for comparison
        default_res = self.run_simulation(8, 21, 7, 40.0, 60.0)
        logger.info(f"Default Config (EMA 8/21, RSI 7/40) -> PnL: {default_res['net_pnl']:+.4f} (Trades: {default_res['trades']}, Win Rate: {default_res['win_rate']}%)")

        for f in ema_fast_range:
            for s in ema_slow_range:
                if f >= s:
                    continue
                for rp in rsi_period_range:
                    for rb in rsi_buy_range:
                        for rs in rsi_sell_range:
                            res = self.run_simulation(f, s, rp, rb, rs)
                            if res["net_pnl"] > best_pnl:
                                best_pnl = res["net_pnl"]
                                best_cfg = {
                                    "ema_fast": f,
                                    "ema_slow": s,
                                    "rsi_period": rp,
                                    "rsi_buy": rb,
                                    "rsi_sell": rs,
                                    "metrics": res
                                }

        logger.info(f"Optimization complete! Best PnL: {best_pnl:+.4f}")
        logger.info(f"Best Config -> EMA: {best_cfg['ema_fast']}/{best_cfg['ema_slow']} | RSI: {best_cfg['rsi_period']}/{best_cfg['rsi_buy']} | Trades: {best_cfg['metrics']['trades']} (Win: {best_cfg['metrics']['win_rate']}%)")
        return best_cfg

    def apply_settings(self, best_cfg: dict) -> bool:
        """Writes the optimized settings directly into the local .env file."""
        env_path = BASE / ".env"
        if not env_path.exists():
            logger.error(".env file not found.")
            return False

        try:
            with open(env_path, "r") as f:
                lines = f.readlines()

            updates = {
                "SCALPER_EMA_FAST": str(best_cfg["ema_fast"]),
                "SCALPER_EMA_SLOW": str(best_cfg["ema_slow"]),
                "SCALPER_RSI_PERIOD": str(best_cfg["rsi_period"]),
                "SCALPER_RSI_BUY": f"{best_cfg['rsi_buy']:.1f}",
                "SCALPER_RSI_SELL": f"{best_cfg['rsi_sell']:.1f}"
            }

            new_lines = []
            for line in lines:
                matched = False
                for key, val in updates.items():
                    if line.strip().startswith(f"{key}="):
                        new_lines.append(f"{key}={val}\n")
                        matched = True
                        break
                if not matched:
                    new_lines.append(line)

            with open(env_path, "w") as f:
                f.writelines(new_lines)

            logger.info("Successfully updated .env file with optimized parameters.")
            return True
        except Exception as e:
            logger.error(f"Failed to write optimized settings to .env: {e}")
            return False

async def main():
    # Detect active scalper settings or fallback to default
    symbol = os.getenv("SCALPER_SYMBOL", "SOL/EUR")
    exchange = os.getenv("SCALPER_EXCHANGE", "binance")
    
    optimizer = ParameterOptimizer(symbol, exchange)
    success = await optimizer.fetch_historical_data(limit=1000)
    if not success:
        sys.exit(1)
        
    best_cfg = optimizer.optimize()
    
    # Check if this optimization is triggered with the --apply flag
    if "--apply" in sys.argv:
        optimizer.apply_settings(best_cfg)

if __name__ == "__main__":
    asyncio.run(main())
