#!/usr/bin/env python3
"""Auto-Optimization Module for Grid Trading."""

import asyncio
import logging
import os
import json
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Any
from dataclasses import dataclass, asdict

logger = logging.getLogger(__name__)

@dataclass
class OptimizationConfig:
    analyze_period_days: int = 7      # How many days to analyze
    min_trades_for_optimize: int = 5 # Min trades needed to optimize
    target_profit_per_trade: float = 0.003  # 0.3% per trade
    max_loss_per_trade: float = -0.015  # -1.5% max loss per trade

@dataclass
class TradeRecord:
    timestamp: float
    symbol: str
    side: str
    price: float
    amount: float
    value_usd: float
    fee_usd: float
    net_pnl: float
    strategy: str

@dataclass
class OptimizationResult:
    new_spacing_pct: float
    new_take_profit_pct: float
    confidence: float  # 0.0 to 1.0
    reason: str
    trades_analyzed: int

class GridAutoOptimizer:
    """Analyzes trade history and optimizes grid parameters"""
    
    def __init__(self, config: OptimizationConfig = OptimizationConfig()):
        self.config = config
        self.trade_history: List[TradeRecord] = []
        self.trade_db_path = os.path.join(os.path.dirname(__file__), "trade_db.json")
        
    def add_trade(self, trade: TradeRecord):
        """Add a trade to history"""
        self.trade_history.append(trade)
        
    def load_trades_from_db(self, db_path: str = None):
        """Load trades from trade database"""
        if db_path:
            self.trade_db_path = db_path
            
        if not os.path.exists(self.trade_db_path):
            logger.warning(f"Trade DB not found at {self.trade_db_path}")
            return []
            
        try:
            with open(self.trade_db_path, 'r') as f:
                data = json.load(f)
                if isinstance(data, list):
                    for t in data:
                        self.trade_history.append(TradeRecord(**t))
                elif isinstance(data, dict) and 'trades' in data:
                    for t in data['trades']:
                        self.trade_history.append(TradeRecord(**t))
            logger.info(f"Loaded {len(self.trade_history)} trades from DB")
        except Exception as e:
            logger.error(f"Failed to load trades: {e}")
            
        return self.trade_history
    
    def get_trades_in_period(self, days: int = None) -> List[TradeRecord]:
        """Get trades within the analysis period"""
        if days is None:
            days = self.config.analyze_period_days
            
        cutoff = datetime.now().timestamp() - (days * 86400)
        return [t for t in self.trade_history if t.timestamp >= cutoff]
    
    def analyze_trades(self) -> Tuple[float, float, float, float, int]:
        """
        Analyze trade history and return metrics:
        (avg_profit_pct, win_rate, total_pnl, avg_pnl_per_trade, num_trades)
        """
        trades = self.get_trades_in_period()
        
        if len(trades) < self.config.min_trades_for_optimize:
            logger.info(f"Not enough trades ({len(trades)}) for optimization")
            return 0.0, 0.0, 0.0, 0.0, len(trades)
            
        profitable = [t for t in trades if t.net_pnl > 0]
        win_rate = len(profitable) / len(trades) if trades else 0.0
        
        total_pnl = sum(t.net_pnl for t in trades)
        avg_pnl = total_pnl / len(trades) if trades else 0.0
        
        # Calculate average profit percentage
        profit_pcts = []
        for t in trades:
            if t.value_usd > 0:
                profit_pcts.append(t.net_pnl / t.value_usd)
                
        avg_profit_pct = sum(profit_pcts) / len(profit_pcts) if profit_pcts else 0.0
        
        return avg_profit_pct, win_rate, total_pnl, avg_pnl, len(trades)
    
    def calculate_optimal_spacing(self, avg_profit_pct: float, win_rate: float) -> float:
        """
        Calculate optimal grid spacing based on historical performance
        """
        # If we're hitting target profit consistently, spacing is good
        target = self.config.target_profit_per_trade
        
        if abs(avg_profit_pct - target) < 0.001:
            # Perfect! Keep current spacing
            return 0.0  # No change needed
            
        if avg_profit_pct > target:
            # We're making too much profit - spacing is tight, can widen to capture more trades
            adjustment = min(0.02, (avg_profit_pct - target) * 2)
            return adjustment  # Positive = increase spacing
            
        # We're not making enough profit - could be:
        # 1. Spacing too wide (missing fills) -> decrease spacing
        # 2. Losing too much on losers -> check win rate
        
        if win_rate < 0.4:
            # Low win rate suggests spacing is too tight, getting stopped out
            return -0.01  # Decrease spacing to reduce stop-outs
            
        # Normal case: increase spacing slightly
        return -0.005  # Slight decrease to capture more profit opportunities
    
    def optimize(self) -> OptimizationResult:
        """
        Run optimization and return recommended parameter changes
        """
        avg_profit_pct, win_rate, total_pnl, avg_pnl, num_trades = self.analyze_trades()
        
        if num_trades < self.config.min_trades_for_optimize:
            return OptimizationResult(
                new_spacing_pct=0.0,
                new_take_profit_pct=0.0,
                confidence=0.0,
                reason=f"Not enough trades ({num_trades}/{self.config.min_trades_for_optimize} needed)",
                trades_analyzed=num_trades
            )
            
        spacing_adjustment = self.calculate_optimal_spacing(avg_profit_pct, win_rate)
        
        # Calculate confidence based on sample size
        confidence = min(1.0, num_trades / 20.0)  # 20 trades = 100% confidence
        
        # Build reason string
        if spacing_adjustment > 0:
            reason = f"Increase spacing by {spacing_adjustment:.2%} (avg profit {avg_profit_pct:.2%} > target {self.config.target_profit_per_trade:.2%})"
        elif spacing_adjustment < 0:
            reason = f"Decrease spacing by {abs(spacing_adjustment):.2%} (avg profit {avg_profit_pct:.2%} < target, win_rate={win_rate:.1%})"
        else:
            reason = f"Spacing optimal (avg profit {avg_profit_pct:.2%} ~= target {self.config.target_profit_per_trade:.2%})"
            
        result = OptimizationResult(
            new_spacing_pct=spacing_adjustment,
            new_take_profit_pct=0.0,  # Future: could also adjust take profit
            confidence=confidence,
            reason=reason,
            trades_analyzed=num_trades
        )
        
        logger.info(f"Optimization result: {result}")
        return result
    
    def save_optimization_result(self, result: OptimizationResult, filepath: str = None):
        """Save optimization result to file for other scripts to read"""
        if filepath is None:
            filepath = os.path.join(os.path.dirname(__file__), "optimization_result.json")
            
        data = asdict(result)
        data['timestamp'] = datetime.now().isoformat()
        
        with open(filepath, 'w') as f:
            json.dump(data, f, indent=2)
            
        logger.info(f"Saved optimization result to {filepath}")
        
    def get_status(self) -> dict:
        """Get optimizer status for monitoring"""
        avg_profit, win_rate, total_pnl, avg_pnl, num_trades = self.analyze_trades()
        return {
            "trades_in_history": len(self.trade_history),
            "trades_analyzed": num_trades,
            "avg_profit_pct": avg_profit,
            "win_rate": win_rate,
            "total_pnl": total_pnl,
            "avg_pnl": avg_pnl,
            "min_trades_needed": self.config.min_trades_for_optimize,
            "analysis_period_days": self.config.analyze_period_days
        }

# Global instance
optimizer = GridAutoOptimizer()

def run_weekly_optimization(trade_db_path: str = None) -> OptimizationResult:
    """Convenience function to run weekly optimization"""
    optimizer.load_trades_from_db(trade_db_path)
    result = optimizer.optimize()
    optimizer.save_optimization_result(result)
    return result

if __name__ == "__main__":
    # Test the optimizer
    opt = GridAutoOptimizer()
    
    # Simulate some trades
    test_trades = [
        TradeRecord(timestamp=datetime.now().timestamp() - i * 3600, symbol="SOL/USDC",
                   side="sell", price=65.0, amount=0.1, value_usd=6.5, fee_usd=0.01,
                   net_pnl=0.02, strategy="GridTrader") for i in range(10)
    ]
    
    for t in test_trades:
        opt.add_trade(t)
        
    result = opt.optimize()
    print(f"Optimization Result: {result}")
    print(f"Status: {opt.get_status()}")