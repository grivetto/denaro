#!/usr/bin/env python3
"""ADX Trend Filter Module."""

import asyncio
import logging
from typing import List, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class ADXConfig:
    period: int = 14  # ADX period
    adx_threshold_high: float = 25.0  # Above this, market is trending
    adx_threshold_low: float = 20.0   # Below this, market is ranging

class ADXTrendFilter:
    """Calculates ADX and determines if market is trending or ranging"""
    
    def __init__(self, config: ADXConfig = ADXConfig()):
        self.config = config
        self.price_history: List[Tuple[float, float, float]] = []  # (high, low, close)
        self.adx_value: float = 0.0
        self.plus_di: float = 0.0
        self.minus_di: float = 0.0
        
    def add_price_data(self, high: float, low: float, close: float):
        """Add new price data for ADX calculation"""
        self.price_history.append((high, low, close))
        # Keep only required period data
        if len(self.price_history) > self.config.period * 2:  # Need enough data for TR, DI, ADX
            self.price_history.pop(0)
        self._calculate_adx()
    
    def _calculate_adx(self):
        """Calculate ADX, Plus DI, Minus DI"""
        if len(self.price_history) < self.config.period * 2:
            self.adx_value = 0.0
            self.plus_di = 0.0
            self.minus_di = 0.0
            return
        
        # --- Calculate True Range (TR) ---
        true_ranges = []
        for i in range(1, len(self.price_history)):
            high, low, close = self.price_history[i]
            prev_close = self.price_history[i-1][2]
            
            tr1 = high - low
            tr2 = abs(high - prev_close)
            tr3 = abs(low - prev_close)
            true_ranges.append(max(tr1, tr2, tr3))
            
        # --- Calculate Directional Movement (DM) ---
        plus_dm = []
        minus_dm = []
        for i in range(1, len(self.price_history)):
            high, low, _ = self.price_history[i]
            prev_high, prev_low, _ = self.price_history[i-1]
            
            up_move = high - prev_high
            down_move = prev_low - low
            
            if up_move > down_move and up_move > 0:
                plus_dm.append(up_move)
                minus_dm.append(0.0)
            elif down_move > up_move and down_move > 0:
                minus_dm.append(down_move)
                plus_dm.append(0.0)
            else:
                plus_dm.append(0.0)
                minus_dm.append(0.0)
                
        # --- Exponential Moving Average (EMA) for TR, Plus DM, Minus DM ---
        def ema(data: List[float], period: int) -> float:
            if not data: return 0.0
            ema_values = [0.0] * len(data)
            ema_values[0] = data[0]
            alpha = 2 / (period + 1)
            for i in range(1, len(data)):
                ema_values[i] = (data[i] * alpha) + (ema_values[i-1] * (1 - alpha))
            return ema_values[-1]
        
        # Smoothed TR, Plus DM, Minus DM
        smoothed_tr = ema(true_ranges, self.config.period)
        smoothed_plus_dm = ema(plus_dm, self.config.period)
        smoothed_minus_dm = ema(minus_dm, self.config.period)
        
        if smoothed_tr == 0:
            self.plus_di = 0.0
            self.minus_di = 0.0
        else:
            self.plus_di = (smoothed_plus_dm / smoothed_tr) * 100
            self.minus_di = (smoothed_minus_dm / smoothed_tr) * 100
            
        # --- Calculate Directional Index (DX) ---
        dx_values = []
        for i in range(len(true_ranges)):
            if (self.plus_di + self.minus_di) == 0:
                dx_values.append(0.0)
            else:
                dx = (abs(self.plus_di - self.minus_di) / (self.plus_di + self.minus_di)) * 100
                dx_values.append(dx)
                
        # --- Calculate ADX (EMA of DX) ---
        self.adx_value = ema(dx_values, self.config.period)
    
    def is_trending(self) -> bool:
        """Returns True if market is trending, False if ranging"""
        return self.adx_value > self.config.adx_threshold_high
    
    def is_ranging(self) -> bool:
        """Returns True if market is ranging, False if trending"""
        return self.adx_value < self.config.adx_threshold_low
        
    def get_status(self) -> dict:
        """Get current ADX status for monitoring"""
        return {
            "adx_value": self.adx_value,
            "plus_di": self.plus_di,
            "minus_di": self.minus_di,
            "is_trending": self.is_trending(),
            "is_ranging": self.is_ranging(),
            "adx_period": self.config.period
        }

# Global instance for reuse
adx_filter = ADXTrendFilter()

def get_adx_trend_status(high: float, low: float, close: float) -> Tuple[bool, bool, float]:
    """
    Convenience function to get ADX trend status
    Updates ADX calculation and returns (is_trending, is_ranging, adx_value)
    """
    adx_filter.add_price_data(high, low, close)
    return adx_filter.is_trending(), adx_filter.is_ranging(), adx_filter.adx_value

if __name__ == "__main__":
    # Test the ADX filter
    calc = ADXTrendFilter(ADXConfig(period=14))
    
    # Simulate some price data (simple trend up then range)
    test_data = [
        (10, 9, 9.5), (11, 10, 10.5), (12, 11, 11.5), (13, 12, 12.5), (14, 13, 13.5), # Up trend
        (13, 12, 12.5), (12, 11, 11.5), (11, 10, 10.5), (10, 9, 9.5), (11, 10, 10.5), # Range
        (10, 9, 9.5), (11, 10, 10.5), (12, 11, 11.5), (13, 12, 12.5), (14, 13, 13.5), # Up trend
        (13, 12, 12.5), (12, 11, 11.5), (11, 10, 10.5), (10, 9, 9.5), (11, 10, 10.5)  # Range
    ]
    
    for high, low, close in test_data:
        calc.add_price_data(high, low, close)
        trending, ranging, adx = get_adx_trend_status(high, low, close)
        print(f"Price: {close}, ADX: {adx:.2f}, Trending: {trending}, Ranging: {ranging}")
        
    print("\n--- Testing with more data ---")
    # Simulate a strong trend
    trend_data = [
        (100, 90, 95), (105, 95, 100), (110, 100, 105), (115, 105, 110), (120, 110, 115),
        (125, 115, 120), (130, 120, 125), (135, 125, 130), (140, 130, 135), (145, 135, 140)
    ]
    
    calc_trend = ADXTrendFilter(ADXConfig(period=5)) # Shorter period for faster response
    for high, low, close in trend_data:
        calc_trend.add_price_data(high, low, close)
        trending, ranging, adx = get_adx_trend_status(high, low, close)
        print(f"Price: {close}, ADX: {adx:.2f}, Trending: {trending}, Ranging: {ranging}")