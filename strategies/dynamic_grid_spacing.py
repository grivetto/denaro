#!/usr/bin/env python3
"""
Dynamic Grid Spacing Module
Calculates ATR (Average True Range) for adaptive grid spacing
"""

import asyncio
import logging
from typing import List, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class ATRConfig:
    period: int = 14  # ATR period
    multiplier: float = 1.0  # ATR multiplier for grid spacing
    base_spacing_pct: float = 0.04  # Base grid spacing (4%)
    min_spacing_pct: float = 0.01   # Minimum spacing (1%)
    max_spacing_pct: float = 0.10   # Maximum spacing (10%)

class DynamicGridSpacing:
    """Calculates dynamic grid spacing based on ATR volatility"""
    
    def __init__(self, config: ATRConfig = ATRConfig()):
        self.config = config
        self.price_history: List[Tuple[float, float, float]] = []  # (high, low, close)
        self.atr_value: float = 0.0
        
    def add_price_data(self, high: float, low: float, close: float):
        """Add new price data for ATR calculation"""
        self.price_history.append((high, low, close))
        # Keep only required period data
        if len(self.price_history) > self.config.period:
            self.price_history.pop(0)
        self._calculate_atr()
    
    def _calculate_atr(self):
        """Calculate Average True Range"""
        if len(self.price_history) < 2:
            self.atr_value = 0.0
            return
            
        true_ranges = []
        for i in range(1, len(self.price_history)):
            high, low, close = self.price_history[i]
            prev_close = self.price_history[i-1][2]
            
            tr1 = high - low
            tr2 = abs(high - prev_close)
            tr3 = abs(low - prev_close)
            true_ranges.append(max(tr1, tr2, tr3))
        
        if true_ranges:
            self.atr_value = sum(true_ranges) / len(true_ranges)
        else:
            self.atr_value = 0.0
    
    def get_dynamic_spacing(self, current_price: float) -> float:
        """
        Calculate dynamic grid spacing percentage based on ATR
        Returns spacing as decimal (e.g., 0.04 for 4%)
        """
        if self.atr_value == 0.0 or current_price == 0.0:
            return self.config.base_spacing_pct
            
        # Calculate ATR as percentage of price
        atr_pct = self.atr_value / current_price
        
        # Apply multiplier and clamp to min/max
        dynamic_spacing = atr_pct * self.config.multiplier
        dynamic_spacing = max(self.config.min_spacing_pct, 
                            min(self.config.max_spacing_pct, dynamic_spacing))
        
        logger.debug(f"ATR: {self.atr_value:.4f}, ATR%: {atr_pct:.4f}, "
                    f"Dynamic Spacing: {dynamic_spacing:.4f}")
        
        return dynamic_spacing
    
    def get_status(self) -> dict:
        """Get current ATR status for monitoring"""
        return {
            "atr_value": self.atr_value,
            "atr_period": self.config.period,
            "price_history_length": len(self.price_history),
            "dynamic_spacing": self.get_dynamic_spacing(100.0)  # placeholder
        }

# Global instance for reuse
atr_calculator = DynamicGridSpacing()

def calculate_dynamic_grid_spacing(current_price: float, 
                                 high: float, 
                                 low: float, 
                                 close: float) -> float:
    """
    Convenience function to calculate dynamic spacing
    Updates ATR calculation and returns spacing percentage
    """
    atr_calculator.add_price_data(high, low, close)
    return atr_calculator.get_dynamic_spacing(current_price)

if __name__ == "__main__":
    # Test the ATR calculator
    calc = DynamicGridSpacing(ATRConfig(period=14, multiplier=1.5))
    
    # Simulate some price data
    test_data = [
        (105, 95, 100),  # high, low, close
        (107, 96, 104),
        (103, 99, 101),
        (108, 100, 106),
        (104, 98, 102),
    ]
    
    for high, low, close in test_data:
        calc.add_price_data(high, low, close)
        spacing = calc.get_dynamic_spacing(close)
        print(f"Price: {close}, ATR: {calc.atr_value:.2f}, Spacing: {spacing:.4f}")