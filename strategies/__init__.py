"""Strategies package for Denaro trading bot."""

from .base import BaseStrategy, Position, Side, Signal
from .grid import GridTraderStrategy
from .scalper import ScalperStrategy

__all__ = [
    "BaseStrategy",
    "Position", 
    "Side",
    "Signal",
    "GridTraderStrategy",
    "ScalperStrategy",
]
